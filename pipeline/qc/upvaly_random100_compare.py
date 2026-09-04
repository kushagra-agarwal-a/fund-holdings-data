#!/usr/bin/env python3
"""Random 100-fund holdings compare: our parsed disclosures vs Upvaly API + allocation % totals."""
from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
sys_path_parsers = ROOT / "parsers"
import sys

sys.path.insert(0, str(sys_path_parsers))
from amc_parsers.common import (  # noqa: E402
    Holding,
    allocation_policy_for_amc,
    allocation_totals,
    holding_from_dict,
    meets_allocation_qc,
)

API = "https://finapi.upvaly.com/api/mf/scheme-code/{code}"
OUT = ROOT / "data" / "parsed" / "upvaly_random100_report.json"
SEED = 20260812
SAMPLE_N = 100
FORCE_CODES = {"122640"}  # user-linked PPFAS Flexi Cap Regular Growth


def norm_name(s: str) -> str:
    t = (s or "").lower().replace("&", " and ")
    t = re.sub(r"\b(limited|ltd\.?|plc|inc\.?|corp\.?|corporation|company|co\.?)\b", " ", t)
    t = re.sub(r"\b(ordinary\s+shares?|class\s+[a-z])\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_weight(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def fetch_api(code: str, *, retries: int = 6) -> dict:
    url = API.format(code=code)
    req = urllib.request.Request(url, headers={"User-Agent": "fund-disclosures-qc/1.0"})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("status") != "success":
                raise RuntimeError(payload.get("message") or "api error")
            return payload["data"]
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 502, 503, 504) and attempt < retries - 1:
                time.sleep(1.5 * (2**attempt) + random.random())
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise
    raise last_err or RuntimeError("fetch failed")


def pick_plan_code(codes: list[Any], preferred: str | None = None) -> str | None:
    cleaned = [str(c).strip() for c in codes if str(c).strip()]
    if preferred and preferred in cleaned:
        return preferred
    # Prefer a Regular Growth-looking code when 122640-style sibling exists: keep first else.
    return cleaned[0] if cleaned else None


def load_code_index() -> dict[tuple[str, str], str]:
    """(amc_id, SHORTCODE) → AMFI plan code for Upvaly."""
    code_by_key: dict[tuple[str, str], str] = {}

    # 1) Per-AMC matching files (best: full plan list)
    match_root = ROOT / "data/parsed/monthly/latest/_matching"
    if match_root.exists():
        for mj in match_root.glob("*.json"):
            amc = mj.stem
            m = json.loads(mj.read_text())
            for it in m.get("matched_disclosures") or m.get("matched") or []:
                sc = str(it.get("shortcode") or "").strip()
                if not sc:
                    continue
                codes = it.get("amfi_codes") or []
                code = pick_plan_code(codes) or (
                    str(it.get("canonical_amfi_code") or "").strip() or None
                )
                if code:
                    code_by_key[(amc, sc.upper())] = code

    # 2) Shortcode registry
    p = ROOT / "registry" / "disclosure_shortcode_map.json"
    if not p.exists():
        p = ROOT / "data" / "sources" / "disclosure_shortcode_map.json"
    sc_map = json.loads(p.read_text())
    for e in sc_map.get("entries") or []:
        if not isinstance(e, dict):
            continue
        amc = e.get("amc_id") or ""
        sc = e.get("shortcode") or ""
        if not amc or not sc:
            continue
        key = (amc, str(sc).upper())
        if key in code_by_key:
            continue
        plans = e.get("amfi_codes") or e.get("amfi_plan_codes") or []
        code = pick_plan_code(plans) or str(
            e.get("canonical_amfi_code") or e.get("amfi_code") or ""
        ).strip()
        if code:
            code_by_key[key] = code

    # 3) Global disclosure map (shortcode-only fallback)
    disc_path = ROOT / "data/sources/disclosure_to_amfi_global_mapping.json"
    if disc_path.exists():
        disc = json.loads(disc_path.read_text())
        for r in disc.get("mappings") or []:
            if not r.get("mapped"):
                continue
            sc = r.get("disclosure_fund_shortname")
            plans = r.get("fund_amfi_plan_codes") or []
            if sc and plans:
                key = ("", str(sc).upper())
                if key not in code_by_key:
                    code_by_key[key] = str(plans[0])
    return code_by_key


def discover_schemes() -> list[dict]:
    """Parsed monthly/latest schemes that have an AMFI plan code + portfolio.json."""
    code_by_key = load_code_index()
    out = []
    seen_codes: set[str] = set()
    root = ROOT / "data/parsed/monthly/latest"
    if not root.exists():
        return out
    for amc_dir in sorted(root.iterdir()):
        if not amc_dir.is_dir() or amc_dir.name.startswith("_"):
            continue
        schemes_path = amc_dir / "schemes.json"
        if not schemes_path.exists():
            continue
        schemes = json.loads(schemes_path.read_text())
        for s in schemes:
            folder = s.get("folder") or s.get("shortcode")
            sc = s.get("shortcode")
            if not folder:
                continue
            pj = amc_dir / str(folder) / "portfolio.json"
            if not pj.exists():
                continue
            code = None
            if sc:
                code = code_by_key.get((amc_dir.name, str(sc).upper())) or code_by_key.get(
                    ("", str(sc).upper())
                )
            if not code:
                continue
            # Prefer Regular Growth (122640) for PPFAS Flexi Cap
            if str(sc).upper() == "PPFCF":
                mj = root / "_matching" / f"{amc_dir.name}.json"
                if mj.exists():
                    m = json.loads(mj.read_text())
                    for it in m.get("matched_disclosures") or []:
                        if str(it.get("shortcode") or "").upper() == "PPFCF":
                            code = pick_plan_code(it.get("amfi_codes") or [], preferred="122640") or code
                            break
            if code in seen_codes:
                continue
            seen_codes.add(code)
            out.append(
                {
                    "amc_id": amc_dir.name,
                    "scheme": s.get("scheme") or folder,
                    "shortcode": sc,
                    "folder": folder,
                    "amfi_code": str(code),
                    "portfolio_path": str(pj),
                }
            )
    return out


def parsed_holdings(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    rows = data.get("holdings") if isinstance(data, dict) else data
    out = []
    for h in rows or []:
        name = (h.get("instrument") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "norm": norm_name(name),
                "weight": parse_weight(h.get("pct_nav")),
                "isin": (h.get("isin") or "").strip().upper(),
                "raw": h,
            }
        )
    return out


def api_holdings(data: dict) -> list[dict]:
    out = []
    for h in data.get("holdings") or []:
        name = (h.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "norm": norm_name(name),
                "weight": parse_weight(h.get("weightage")),
            }
        )
    return out


def compare(parsed: list[dict], api: list[dict], name_cutoff: float = 86.0) -> dict:
    api_by = {h["norm"]: h for h in api if h["norm"]}
    matched = 0
    weight_ok = 0
    weight_checked = 0
    missing = []
    for p in parsed:
        if not p["norm"]:
            continue
        hit = api_by.get(p["norm"])
        if not hit:
            # fuzzy
            best = None
            best_sc = 0
            for a in api:
                sc = fuzz.token_set_ratio(p["norm"], a["norm"])
                if sc > best_sc:
                    best_sc = sc
                    best = a
            if best and best_sc >= name_cutoff:
                hit = best
            else:
                missing.append(p["name"][:80])
                continue
        matched += 1
        if p["weight"] is not None and hit["weight"] is not None:
            weight_checked += 1
            if abs(p["weight"] - hit["weight"]) <= 0.35:  # tolerance %
                weight_ok += 1
    return {
        "parsed_count": len(parsed),
        "api_count": len(api),
        "matched": matched,
        "match_rate": round(matched / len(parsed), 4) if parsed else None,
        "weight_ok_rate": round(weight_ok / weight_checked, 4) if weight_checked else None,
        "missing_sample": missing[:8],
    }


def to_holding(h: Any) -> Holding:
    if isinstance(h, Holding):
        return h
    if not isinstance(h, dict):
        return Holding(instrument=str(h), pct_nav="")
    return holding_from_dict(h)


def alloc_report(holdings_raw: list[Any], label: str, *, amc_id: str | None = None) -> dict:
    rows = [to_holding(h) for h in holdings_raw]
    policy = allocation_policy_for_amc(amc_id) if amc_id else {}
    totals = allocation_totals(rows, **policy) if policy else allocation_totals(rows)
    ok = meets_allocation_qc(totals, tol=0.10)
    near = {
        k: (abs(v - 100.0) <= 1.0 if v is not None else False)
        for k, v in totals.items()
        if isinstance(v, (int, float))
    }
    return {"label": label, "totals": totals, "meets_qc": ok, "near_100_pm1": near}


def main() -> None:
    rng = random.Random(SEED)
    schemes = discover_schemes()
    print(f"discoverable schemes with amfi+portfolio: {len(schemes)}")
    if len(schemes) < SAMPLE_N:
        print("WARNING: fewer than 100 available; using all")
    by_code = {s["amfi_code"]: s for s in schemes}

    # force include 122640 if present, else still try API-only note
    sample = []
    if "122640" in by_code:
        sample.append(by_code["122640"])
    elif FORCE_CODES:
        # try find via any portfolio mapped later — still fetch API for report
        sample.append(
            {
                "amc_id": "ppfas-mutual-fund",
                "scheme": "Parag Parikh Flexi Cap (forced 122640)",
                "shortcode": None,
                "folder": None,
                "amfi_code": "122640",
                "portfolio_path": None,
            }
        )

    pool = [s for s in schemes if s["amfi_code"] not in FORCE_CODES]
    rng.shuffle(pool)
    need = SAMPLE_N - len(sample)
    sample.extend(pool[: max(0, need)])

    print(f"sampling {len(sample)} schemes (seed={SEED})")

    # prefetch APIs
    codes = sorted({s["amfi_code"] for s in sample})
    cache: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def pref(code: str):
        try:
            return code, fetch_api(code), None
        except Exception as e:
            return code, None, str(e)

    print(f"fetching {len(codes)} Upvaly scheme codes (sequential + retry)…")
    for i, c in enumerate(codes, 1):
        code, data, err = pref(c)
        if data is not None:
            cache[code] = data
        else:
            errors[code] = err or "error"
        if i % 10 == 0 or i == len(codes):
            print(f"  {i}/{len(codes)} ok={len(cache)} err={len(errors)}", flush=True)
        time.sleep(0.35)

    results = []
    for s in sample:
        code = s["amfi_code"]
        row = {
            "amc_id": s["amc_id"],
            "scheme": s["scheme"],
            "shortcode": s.get("shortcode"),
            "amfi_code": code,
            "api_ok": code in cache,
        }
        if code not in cache:
            row["status"] = "api_error"
            row["error"] = errors.get(code)
            results.append(row)
            continue
        data = cache[code]
        row["api_scheme"] = data.get("schemeName")
        api_h = api_holdings(data)
        # allocation on API weights
        row["api_allocation"] = alloc_report(
            [{"name": h["name"], "weight": h["weight"]} for h in api_h],
            "upvaly",
            amc_id=s.get("amc_id"),
        )
        # asset allocation from API portfolio block
        aa = (data.get("portfolio") or {}).get("assetAllocation") or {}
        try:
            aa_sum = sum(float(str(aa.get(k) or 0).replace(",", "")) for k in (
                "equityAllocation", "debtAllocation", "cashAllocation", "otherAllocation"
            ))
        except ValueError:
            aa_sum = None
        row["api_asset_allocation_sum"] = aa_sum

        if s.get("portfolio_path") and Path(s["portfolio_path"]).exists():
            pdata = json.loads(Path(s["portfolio_path"]).read_text())
            ph_raw = pdata.get("holdings") if isinstance(pdata, dict) else pdata
            parsed = parsed_holdings(Path(s["portfolio_path"]))
            row["parsed_allocation"] = alloc_report(
                list(ph_raw or []), "parsed", amc_id=s.get("amc_id")
            )
            row["compare"] = compare(parsed, api_h)
            row["status"] = "compared"
        else:
            row["status"] = "api_only_no_parsed"
            row["compare"] = None
        results.append(row)

    compared = [r for r in results if r["status"] == "compared"]
    api_ok = [r for r in results if r.get("api_ok")]

    def pct_true(vals):
        vals = list(vals)
        return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None

    summary = {
        "seed": SEED,
        "requested": SAMPLE_N,
        "sampled": len(sample),
        "api_ok": len(api_ok),
        "compared": len(compared),
        "api_errors": len(errors),
        "avg_match_rate": round(sum(r["compare"]["match_rate"] for r in compared if r["compare"]["match_rate"] is not None) / len(compared), 4)
        if compared
        else None,
        "avg_weight_ok_rate": round(
            sum(r["compare"]["weight_ok_rate"] for r in compared if r["compare"].get("weight_ok_rate") is not None)
            / max(1, sum(1 for r in compared if r["compare"].get("weight_ok_rate") is not None)),
            4,
        )
        if compared
        else None,
        "parsed_meets_allocation_qc_rate": pct_true(
            r["parsed_allocation"]["meets_qc"] for r in compared if r.get("parsed_allocation")
        ),
        "parsed_exclude_futures_near_100_pm1_rate": pct_true(
            (r["parsed_allocation"]["near_100_pm1"] or {}).get("exclude_futures")
            for r in compared
            if r.get("parsed_allocation")
        ),
        "api_holdings_all_near_100_pm1_rate": pct_true(
            (r["api_allocation"]["near_100_pm1"] or {}).get("all") for r in api_ok if r.get("api_allocation")
        ),
        "api_asset_allocation_near_100_pm1_rate": pct_true(
            r.get("api_asset_allocation_sum") is not None and abs(r["api_asset_allocation_sum"] - 100) <= 1.0
            for r in api_ok
        ),
        "status_counts": dict(Counter(r["status"] for r in results)),
        "forced_122640": next((r for r in results if r["amfi_code"] == "122640"), None),
        "worst_match": sorted(
            compared,
            key=lambda r: r["compare"]["match_rate"] or 0,
        )[:10],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"summary": summary, "results": results}, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("worst_match", "forced_122640")}, indent=2))
    print("\n=== forced 122640 ===")
    f = summary["forced_122640"]
    if f:
        print(json.dumps({k: f.get(k) for k in ("status", "api_scheme", "compare", "parsed_allocation", "api_allocation", "api_asset_allocation_sum")}, indent=2)[:2000])
    print("\n=== worst match (10) ===")
    for r in summary["worst_match"]:
        print(
            f"  {r['compare']['match_rate']:.2%} w={r['compare'].get('weight_ok_rate')}  {r['amfi_code']}  {r['amc_id']}  {(r['scheme'] or '')[:50]}"
        )
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
