#!/usr/bin/env python3
"""
Verify parsed portfolio holdings against Upvaly scheme API.

  GET https://finapi.upvaly.com/api/mf/scheme-code/{amfi_code}

Comparisons use normalized instrument names + %NAV / weightage
(API holdings do not include ISIN).

Examples:
  .venv/bin/python3 scripts/verify_holdings_upvaly.py --fixtures-latest
  .venv/bin/python3 scripts/verify_holdings_upvaly.py --fixtures-latest --limit-schemes=30
  .venv/bin/python3 scripts/verify_holdings_upvaly.py --amc=ppfas-mutual-fund
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "parsers"))
sys.path.insert(0, str(ROOT / "scripts"))

from rapidfuzz import fuzz  # noqa: E402

API_TMPL = "https://finapi.upvaly.com/api/mf/scheme-code/{code}"
_FIXTURES_REG = ROOT / "registry" / "parser_fixtures.json"
_FIXTURES_OLD = ROOT / "data" / "sources" / "parser_fixtures.json"
FIXTURES = _FIXTURES_REG if _FIXTURES_REG.exists() else _FIXTURES_OLD
_SC_REG = ROOT / "registry" / "disclosure_shortcode_map.json"
_SC_OLD = ROOT / "data" / "sources" / "disclosure_shortcode_map.json"
SHORTCODE_MAP = _SC_REG if _SC_REG.exists() else _SC_OLD
MATCHING_DIR = {
    "monthly": ROOT / "data" / "parsed" / "monthly" / "latest" / "_matching",
    "fortnightly": ROOT / "data" / "parsed" / "fortnightly" / "latest" / "_matching",
}
PARSED = {
    "monthly": ROOT / "data" / "parsed" / "monthly" / "latest",
    "fortnightly": ROOT / "data" / "parsed" / "fortnightly" / "latest",
}

JUNK_NAME_RE = re.compile(
    r"(?i)^(net\s+receivables|cash\s+offset|others?|nil|sub\s*total|grand\s*total|"
    r"trp[_\s]|treps|margin|payable)"
)


@dataclass
class SchemeRef:
    amc_id: str
    cadence: str
    folder: str
    scheme_name: str
    shortcode: str | None
    holdings_path: Path
    amfi_code: str | None = None
    amfi_codes: list[str] = field(default_factory=list)
    map_via: str | None = None


def norm_name(s: str) -> str:
    t = (s or "").lower()
    t = t.replace("&", " and ")
    t = re.sub(r"\b(limited|ltd\.?|plc|inc\.?|corp\.?|corporation|company|co\.?)\b", " ", t)
    t = re.sub(r"\b(ordinary\s+shares?|class\s+[a-z])\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_weight(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if not s or s.upper() == "NIL":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_percent_weights(raw: list[float | None]) -> list[float | None]:
    """If values look like fractions (max <= 1.5), scale to percent."""
    nums = [x for x in raw if x is not None]
    if not nums:
        return raw
    mx = max(abs(x) for x in nums)
    if 0 < mx <= 1.5:
        return [None if x is None else x * 100.0 for x in raw]
    return raw


def load_parsed_holdings(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("holdings") if isinstance(data, dict) else data
    out = []
    for h in rows or []:
        name = (h.get("instrument") or "").strip()
        if not name or JUNK_NAME_RE.search(name):
            continue
        # skip pure futures annex noise optionally — keep them
        out.append(
            {
                "name": name,
                "norm": norm_name(name),
                "isin": (h.get("isin") or "").strip().upper(),
                "weight": parse_weight(h.get("pct_nav")),
                "section": h.get("section") or "",
            }
        )
    weights = to_percent_weights([r["weight"] for r in out])
    for r, w in zip(out, weights):
        r["weight"] = w
    return out


def load_api_holdings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    holdings = (payload.get("data") or {}).get("holdings") or []
    out = []
    for h in holdings:
        name = (h.get("name") or "").strip()
        if not name or JUNK_NAME_RE.search(name):
            continue
        out.append(
            {
                "name": name,
                "norm": norm_name(name),
                "weight": parse_weight(h.get("weightage")),
                "sector": h.get("sector") or "",
            }
        )
    return out


def build_amfi_lookup() -> dict[str, dict[str, Any]]:
    """Map amc|shortcode and amc|norm(scheme) → matching row."""
    idx: dict[str, dict[str, Any]] = {}
    # shortcode durable map
    if SHORTCODE_MAP.exists():
        sm = json.loads(SHORTCODE_MAP.read_text(encoding="utf-8"))
        for e in sm.get("entries") or []:
            amc = e.get("amc_id") or ""
            sc = (e.get("shortcode") or "").strip()
            if amc and sc:
                idx[f"{amc}|sc|{sc.upper()}"] = e
            for a in e.get("aliases") or []:
                if a:
                    idx[f"{amc}|sc|{str(a).upper()}"] = e
            base = e.get("amfi_base_name") or e.get("base_name")
            if base:
                idx[f"{amc}|name|{norm_name(base)}"] = e
    # matching matrices (prefer these when present)
    for cadence, d in MATCHING_DIR.items():
        if not d.is_dir():
            continue
        for path in d.glob("*.json"):
            if path.name.startswith("_"):
                continue
            try:
                m = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            amc = m.get("amc_id") or path.stem
            for row in m.get("matched_disclosures") or m.get("matched") or []:
                sc = (row.get("shortcode") or "").strip()
                if sc:
                    idx[f"{amc}|sc|{sc.upper()}"] = row
                label = row.get("disclosure_base") or row.get("disclosure_label") or row.get("amfi_base_name")
                if label:
                    idx[f"{amc}|name|{norm_name(label)}"] = row
    return idx


def resolve_amfi(ref: SchemeRef, idx: dict[str, dict[str, Any]]) -> SchemeRef:
    amc = ref.amc_id
    cands = []
    if ref.shortcode:
        cands.append(f"{amc}|sc|{ref.shortcode.upper()}")
    cands.append(f"{amc}|name|{norm_name(ref.scheme_name)}")
    # folder often equals shortcode
    if ref.folder:
        cands.append(f"{amc}|sc|{ref.folder.upper()}")
        cands.append(f"{amc}|name|{norm_name(ref.folder)}")
    for key in cands:
        row = idx.get(key)
        if not row:
            continue
        code = str(row.get("canonical_amfi_code") or "").strip()
        codes = [str(c) for c in (row.get("amfi_codes") or []) if c]
        if code and code not in codes:
            codes = [code] + codes
        if code or codes:
            ref.amfi_code = code or (codes[0] if codes else None)
            ref.amfi_codes = codes
            ref.map_via = key
            return ref
    return ref


def discover_fixture_schemes(amc_filter: str | None = None) -> list[SchemeRef]:
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    refs: list[SchemeRef] = []
    for amc_id, fx in fixtures.items():
        if amc_filter and amc_id != amc_filter:
            continue
        for cadence in ("monthly", "fortnightly"):
            root = PARSED[cadence] / amc_id
            schemes_path = root / "schemes.json"
            if not schemes_path.exists():
                continue
            try:
                schemes = json.loads(schemes_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for s in schemes:
                folder = s.get("folder") or ""
                hp = root / folder / "portfolio.json"
                if not hp.exists():
                    continue
                refs.append(
                    SchemeRef(
                        amc_id=amc_id,
                        cadence=cadence,
                        folder=folder,
                        scheme_name=s.get("scheme") or folder,
                        shortcode=s.get("shortcode"),
                        holdings_path=hp,
                    )
                )
    return refs


def fetch_api(code: str, timeout: float = 30.0) -> dict[str, Any]:
    url = API_TMPL.format(code=code)
    req = urllib.request.Request(url, headers={"User-Agent": "fund-disclosures-verify/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def best_name_match(query: str, choices: list[str]) -> tuple[str | None, float]:
    if not query or not choices:
        return None, 0.0
    if query in choices:
        return query, 100.0
    # exact token sort
    best, score = None, 0.0
    for c in choices:
        sc = float(fuzz.token_sort_ratio(query, c))
        if sc > score:
            best, score = c, sc
    return best, score


def compare_holdings(
    parsed: list[dict[str, Any]],
    api: list[dict[str, Any]],
    *,
    name_cutoff: float = 88.0,
    weight_tol: float = 0.35,
) -> dict[str, Any]:
    api_by_norm = {h["norm"]: h for h in api if h["norm"]}
    api_norms = list(api_by_norm.keys())

    matched = []
    missing_in_api = []
    weight_mismatch = []

    used_api: set[str] = set()
    for p in parsed:
        if not p["norm"]:
            continue
        hit, score = best_name_match(p["norm"], api_norms)
        if not hit or score < name_cutoff:
            missing_in_api.append(
                {"name": p["name"], "weight": p["weight"], "best": hit, "score": round(score, 1)}
            )
            continue
        used_api.add(hit)
        a = api_by_norm[hit]
        pw, aw = p["weight"], a["weight"]
        ok = True
        delta = None
        if pw is not None and aw is not None:
            delta = abs(pw - aw)
            # relative for larger weights
            rel = delta / max(abs(aw), 0.2)
            ok = delta <= weight_tol or rel <= 0.15
        row = {
            "parsed": p["name"],
            "api": a["name"],
            "score": round(score, 1),
            "parsed_weight": pw,
            "api_weight": aw,
            "delta": None if delta is None else round(delta, 3),
            "weight_ok": ok,
        }
        matched.append(row)
        if not ok:
            weight_mismatch.append(row)

    missing_in_parsed = []
    for n, a in api_by_norm.items():
        if n in used_api:
            continue
        # ignore tiny API leftovers
        if a["weight"] is not None and abs(a["weight"]) < 0.05:
            continue
        missing_in_parsed.append({"name": a["name"], "weight": a["weight"]})

    matched_ok = sum(1 for m in matched if m["weight_ok"])
    return {
        "parsed_count": len(parsed),
        "api_count": len(api),
        "matched": len(matched),
        "matched_weight_ok": matched_ok,
        "missing_in_api": len(missing_in_api),
        "missing_in_parsed": len(missing_in_parsed),
        "weight_mismatch": len(weight_mismatch),
        "match_rate": round(len(matched) / len(parsed), 4) if parsed else 0.0,
        "weight_ok_rate": round(matched_ok / len(matched), 4) if matched else 0.0,
        "samples": {
            "weight_mismatch": weight_mismatch[:8],
            "missing_in_api_top": sorted(
                missing_in_api, key=lambda x: -(x["weight"] or 0)
            )[:8],
            "missing_in_parsed_top": sorted(
                missing_in_parsed, key=lambda x: -(x["weight"] or 0)
            )[:8],
            "matched_top": sorted(matched, key=lambda x: -(x["api_weight"] or 0))[:5],
        },
    }


def verify_one(ref: SchemeRef, cache: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "amc_id": ref.amc_id,
        "cadence": ref.cadence,
        "scheme": ref.scheme_name,
        "shortcode": ref.shortcode,
        "folder": ref.folder,
        "amfi_code": ref.amfi_code,
        "map_via": ref.map_via,
        "status": "ok",
    }
    if not ref.amfi_code:
        out["status"] = "no_amfi_map"
        return out

    codes = ref.amfi_codes or [ref.amfi_code]
    payload = None
    used = None
    err = None
    for code in codes[:4]:
        if code in cache:
            payload = cache[code]
            used = code
            break
        try:
            payload = fetch_api(code)
            cache[code] = payload
            used = code
            if payload.get("status") == "success" and (payload.get("data") or {}).get("holdings") is not None:
                break
        except Exception as e:
            err = str(e)
            continue

    if not payload or payload.get("status") != "success":
        out["status"] = "api_error"
        out["error"] = err or payload
        return out

    data = payload.get("data") or {}
    api_holdings = load_api_holdings(payload)
    if not api_holdings:
        out["status"] = "api_no_holdings"
        out["amfi_code_used"] = used
        out["api_scheme"] = data.get("schemeName")
        return out

    parsed = load_parsed_holdings(ref.holdings_path)
    if not parsed:
        out["status"] = "parsed_empty"
        out["amfi_code_used"] = used
        return out

    cmp = compare_holdings(parsed, api_holdings)
    out.update(
        {
            "amfi_code_used": used,
            "api_scheme": data.get("schemeName"),
            "api_holding_reported": (data.get("portfolio") or {}).get("concentration", {}).get(
                "numberOfHoldings"
            ),
            **cmp,
            "status": "compared",
        }
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixtures-latest", action="store_true", help="Verify fixture schemes under parsed/*/latest")
    ap.add_argument("--amc", help="Limit to one AMC id")
    ap.add_argument("--limit-schemes", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--out",
        default=str(ROOT / "data" / "parsed" / "holdings_verify_upvaly.json"),
    )
    args = ap.parse_args()
    if not args.fixtures_latest and not args.amc:
        raise SystemExit("Use --fixtures-latest and/or --amc")

    refs = discover_fixture_schemes(args.amc)
    idx = build_amfi_lookup()
    for r in refs:
        resolve_amfi(r, idx)

    if args.limit_schemes:
        refs = refs[: args.limit_schemes]

    print(f"schemes to verify: {len(refs)}", flush=True)
    cache: dict[str, Any] = {}
    results: list[dict[str, Any]] = []

    # sequential cache-friendly but threaded fetches for unique codes
    # First resolve unique primary codes
    def run(ref: SchemeRef) -> dict[str, Any]:
        return verify_one(ref, cache)

    # Thread-safe enough if we prefetch unique codes first
    unique_codes: list[str] = []
    seen = set()
    for r in refs:
        for c in (r.amfi_codes or ([r.amfi_code] if r.amfi_code else [])):
            if c and c not in seen:
                seen.add(c)
                unique_codes.append(c)

    print(f"unique amfi codes to touch (up to 4/scheme): {len(unique_codes)}", flush=True)

    def pref(code: str):
        try:
            cache[code] = fetch_api(code)
            return code, "ok"
        except Exception as e:
            return code, str(e)

    # Prefetch only first code per scheme to reduce load
    primary = []
    seen_p = set()
    for r in refs:
        if r.amfi_code and r.amfi_code not in seen_p:
            seen_p.add(r.amfi_code)
            primary.append(r.amfi_code)

    print(f"prefetching {len(primary)} primary codes…", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(pref, c) for c in primary]
        done = 0
        for fut in as_completed(futs):
            done += 1
            if done % 25 == 0 or done == len(futs):
                print(f"  fetched {done}/{len(futs)}", flush=True)

    for i, ref in enumerate(refs, 1):
        results.append(verify_one(ref, cache))
        if i % 50 == 0 or i == len(refs):
            print(f"  compared {i}/{len(refs)}", flush=True)

    # summary
    by_status: dict[str, int] = defaultdict(int)
    compared = []
    for r in results:
        by_status[r["status"]] += 1
        if r["status"] == "compared":
            compared.append(r)

    summary = {
        "schemes_total": len(results),
        "by_status": dict(by_status),
        "compared": len(compared),
        "avg_match_rate": round(
            sum(r["match_rate"] for r in compared) / len(compared), 4
        )
        if compared
        else None,
        "avg_weight_ok_rate": round(
            sum(r["weight_ok_rate"] for r in compared) / len(compared), 4
        )
        if compared
        else None,
        "median_match_rate": None,
        "poor_match": [],
    }
    if compared:
        mrs = sorted(r["match_rate"] for r in compared)
        summary["median_match_rate"] = mrs[len(mrs) // 2]
        poor = sorted(compared, key=lambda r: r["match_rate"])[:15]
        summary["poor_match"] = [
            {
                "amc_id": r["amc_id"],
                "scheme": r["scheme"],
                "amfi_code": r.get("amfi_code_used"),
                "match_rate": r["match_rate"],
                "weight_ok_rate": r["weight_ok_rate"],
                "parsed_count": r["parsed_count"],
                "api_count": r["api_count"],
                "missing_in_api": r["missing_in_api"],
            }
            for r in poor
        ]

    out = {"summary": summary, "results": results}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
