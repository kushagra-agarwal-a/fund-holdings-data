#!/usr/bin/env python3
"""Pick 3 funds per AMC from June 30 monthly (July 15 FN fill); compare top-5 % vs Upvaly.

Top-5 ignores cash/NCA/TREPS/derivative offsets and futures — those restatements
distort Upvaly matching. Compare security weights only.
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "parsers"))
sys.path.insert(0, str(ROOT / "qc"))

from amc_parsers.common import holding_from_dict, is_futures_holding  # noqa: E402
from upvaly_random100_compare import (  # noqa: E402
    fetch_api,
    load_code_index,
    parse_weight,
    pick_plan_code,
)

SEED = 20260813
PER_AMC = 3
OUT = ROOT / "data" / "parsed" / "upvaly_top5_june30_jul15_report.json"
JUNE = ROOT / "data" / "parsed" / "monthly" / "2026-06"
JUL15 = ROOT / "data" / "parsed" / "fortnightly" / "2026-07-15"


# Cash / NCA / TREPS / derivative offsets and futures distort top-5 % vs Upvaly.
CASH_RE = re.compile(
    r"(?i)net\s+current|nca\b|net\s+receivables?|net\s+payables?|"
    r"receivable\s*/\s*\(?\s*payable|payables?\s*/\s*\(?\s*receivable|"
    r"treps?|tri[\s\-]?party|reverse\s+repo|\bcblo\b|"
    r"cash\s+offset|offset\s+for\s+derivative|cash\s+margin|margin\s+money|"
    r"clearing\s+corporation|\bccil\b|cash\s+and\s+other|"
    r"^\s*cash\s*$|^\s*cash\s*/"
)
FUTURES_RE = re.compile(
    r"(?i)\bfutures\b|future\s+on\b|\bfutcom\b|"
    r"stock\s+future|index\s+future|commodity\s+future"
)


def is_cash_or_futures_name(name: str, extra: str = "") -> bool:
    blob = f"{name} {extra}".strip()
    if not blob:
        return False
    return bool(CASH_RE.search(blob) or FUTURES_RE.search(blob))


def skip_holding(h: dict, *, name_keys: tuple[str, ...] = ("instrument", "name")) -> bool:
    name = ""
    for k in name_keys:
        name = str(h.get(k) or "").strip()
        if name:
            break
    extra = " ".join(str(h.get(k) or "") for k in ("section", "industry"))
    if is_cash_or_futures_name(name, extra):
        return True
    try:
        return is_futures_holding(holding_from_dict(h))
    except Exception:
        return False


def as_percent_weights(ws: list[float]) -> list[float]:
    ws = [w for w in ws if w is not None]
    if not ws:
        return []
    if max(abs(x) for x in ws) <= 1.0:
        return [x * 100.0 for x in ws]
    return ws


def top5_nd(weights: list[float | None], nd: int) -> list[float]:
    ws = as_percent_weights([w for w in weights if w is not None])
    ws.sort(reverse=True)
    return [round(w, nd) for w in ws[:5]]


def our_weights(portfolio_path: str) -> list[float | None]:
    data = json.loads(Path(portfolio_path).read_text())
    hs = data.get("holdings") if isinstance(data, dict) else data
    return [
        parse_weight(h.get("pct_nav"))
        for h in (hs or [])
        if not skip_holding(h)
    ]


def api_weights(data: dict) -> list[float | None]:
    return [
        parse_weight(h.get("weightage"))
        for h in (data.get("holdings") or [])
        if not is_cash_or_futures_name(str(h.get("name") or ""))
    ]


def overlay_matching(code_by_key: dict[tuple[str, str], str], match_root: Path) -> None:
    if not match_root.exists():
        return
    for mj in match_root.glob("*.json"):
        amc = mj.stem
        m = json.loads(mj.read_text())
        for it in m.get("matched_disclosures") or m.get("matched") or []:
            sc = str(it.get("shortcode") or "").strip()
            if not sc:
                continue
            preferred = "122640" if sc.upper() == "PPFCF" else None
            code = pick_plan_code(it.get("amfi_codes") or [], preferred=preferred) or (
                str(it.get("canonical_amfi_code") or "").strip() or None
            )
            if code:
                code_by_key[(amc, sc.upper())] = code


def discover(root: Path, *, period: str, cadence: str, code_by_key: dict) -> list[dict]:
    out = []
    if not root.exists():
        return out
    for amc_dir in sorted(root.iterdir()):
        if not amc_dir.is_dir() or amc_dir.name.startswith("_"):
            continue
        schemes_path = amc_dir / "schemes.json"
        if not schemes_path.exists():
            continue
        for s in json.loads(schemes_path.read_text()):
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
            pdata = json.loads(pj.read_text())
            meta = pdata.get("meta") if isinstance(pdata, dict) else {}
            out.append(
                {
                    "amc_id": amc_dir.name,
                    "scheme": s.get("scheme") or folder,
                    "shortcode": sc,
                    "folder": folder,
                    "amfi_code": str(code),
                    "portfolio_path": str(pj),
                    "period": period,
                    "cadence": cadence,
                    "as_of": s.get("as_of") or (meta.get("as_of") if meta else None),
                    "source_file": s.get("source_file") or (meta.get("source_file") if meta else None),
                }
            )
    return out


def pick_sample(schemes: list[dict]) -> list[dict]:
    by_amc: dict[str, list[dict]] = defaultdict(list)
    for s in schemes:
        by_amc[s["amc_id"]].append(s)
    rng = random.Random(SEED)
    sample = []
    for amc, rows in sorted(by_amc.items()):
        # Prefer June monthly over July-15 FN when both exist for the AMC
        june = [r for r in rows if r["period"] == "2026-06"]
        jul = [r for r in rows if r["period"] == "2026-07-15"]
        pool = list(june) if june else list(jul)
        rng.shuffle(pool)

        def score(s: dict) -> tuple[int, int]:
            n = sum(1 for w in our_weights(s["portfolio_path"]) if w is not None)
            return (1 if n >= 5 else 0, n)

        pool.sort(key=score, reverse=True)
        chosen = pool[:PER_AMC]
        if len(chosen) < PER_AMC and june and jul:
            have = {c["amfi_code"] for c in chosen}
            extra = [r for r in jul if r["amfi_code"] not in have]
            extra.sort(key=score, reverse=True)
            chosen.extend(extra[: PER_AMC - len(chosen)])
        sample.extend(chosen)
    return sample


def main() -> None:
    code_by_key = load_code_index()
    overlay_matching(code_by_key, JUNE / "_matching")
    overlay_matching(code_by_key, JUL15 / "_matching")

    june = discover(JUNE, period="2026-06", cadence="monthly", code_by_key=code_by_key)
    jul15 = discover(JUL15, period="2026-07-15", cadence="fortnightly", code_by_key=code_by_key)
    schemes = june + jul15
    sample = pick_sample(schemes)
    print(
        f"june_mapped={len(june)} jul15_mapped={len(jul15)} "
        f"amcs_june={len({s['amc_id'] for s in june})} "
        f"sampled={len(sample)} seed={SEED}",
        flush=True,
    )
    print(
        "sample mix",
        dict(Counter((s["period"], s["cadence"]) for s in sample)),
        flush=True,
    )

    results = []
    for i, s in enumerate(sample, 1):
        ws = our_weights(s["portfolio_path"])
        ours2 = top5_nd(ws, 2)
        ours1 = top5_nd(ws, 1)
        row = {
            "amc_id": s["amc_id"],
            "scheme": s.get("scheme"),
            "shortcode": s.get("shortcode"),
            "amfi_code": s["amfi_code"],
            "period": s["period"],
            "cadence": s["cadence"],
            "as_of": s.get("as_of"),
            "source_file": s.get("source_file"),
            "our_top5": ours2,
            "our_top5_1dp": ours1,
        }
        try:
            data = fetch_api(s["amfi_code"])
            aw = api_weights(data)
            theirs2 = top5_nd(aw, 2)
            theirs1 = top5_nd(aw, 1)
            row["api_ok"] = True
            row["api_scheme"] = data.get("schemeName")
            row["api_top5"] = theirs2
            row["api_top5_1dp"] = theirs1
            n2 = min(len(ours2), len(theirs2), 5)
            n1 = min(len(ours1), len(theirs1), 5)
            row["match_2dp"] = n2 == 5 and ours2 == theirs2
            row["match_1dp"] = n1 == 5 and ours1 == theirs1
            if n2 < 5:
                row["status"] = "short_top5"
            elif row["match_2dp"]:
                row["status"] = "match_2dp"
            elif row["match_1dp"]:
                row["status"] = "match_1dp_only"
            else:
                row["status"] = "mismatch"
        except Exception as e:
            row["api_ok"] = False
            row["status"] = "api_error"
            row["error"] = str(e)[:200]
            row["match_2dp"] = False
            row["match_1dp"] = False
        results.append(row)
        if i % 10 == 0 or i == len(sample):
            print(
                f"  {i}/{len(sample)}  2dp={sum(1 for r in results if r.get('match_2dp'))} "
                f"1dp={sum(1 for r in results if r.get('match_1dp'))}",
                flush=True,
            )
        time.sleep(0.35)

    by_amc_out = []
    for amc in sorted({r["amc_id"] for r in results}):
        rows = [r for r in results if r["amc_id"] == amc]
        by_amc_out.append(
            {
                "amc_id": amc,
                "sampled": len(rows),
                "match_2dp": sum(1 for r in rows if r.get("match_2dp")),
                "match_1dp": sum(1 for r in rows if r.get("match_1dp")),
                "periods": sorted({r["period"] for r in rows}),
                "funds": [
                    {
                        "amfi_code": r["amfi_code"],
                        "shortcode": r.get("shortcode"),
                        "period": r["period"],
                        "as_of": r.get("as_of"),
                        "status": r["status"],
                        "match_2dp": r.get("match_2dp"),
                        "match_1dp": r.get("match_1dp"),
                        "our_top5": r.get("our_top5"),
                        "api_top5": r.get("api_top5"),
                        "api_scheme": r.get("api_scheme") or r.get("scheme"),
                    }
                    for r in rows
                ],
            }
        )

    summary = {
        "seed": SEED,
        "per_amc": PER_AMC,
        "source": "monthly/2026-06 primary; fortnightly/2026-07-15 fill",
        "amcs": len(by_amc_out),
        "sampled": len(results),
        "api_ok": sum(1 for r in results if r.get("api_ok")),
        "match_2dp": sum(1 for r in results if r.get("match_2dp")),
        "match_1dp": sum(1 for r in results if r.get("match_1dp")),
        "mismatch": sum(1 for r in results if r["status"] == "mismatch"),
        "short_top5": sum(1 for r in results if r["status"] == "short_top5"),
        "api_error": sum(1 for r in results if r["status"] == "api_error"),
        "amcs_all_3_match_1dp": sum(
            1 for a in by_amc_out if a["match_1dp"] == a["sampled"] and a["sampled"] == PER_AMC
        ),
        "amcs_zero_match_1dp": sum(1 for a in by_amc_out if a["match_1dp"] == 0),
        "period_mix": dict(Counter(r["period"] for r in results)),
        "status_counts": dict(Counter(r["status"] for r in results)),
        "ppfas": [r for r in results if r["amc_id"] == "ppfas-mutual-fund"],
    }
    OUT.write_text(json.dumps({"summary": summary, "by_amc": by_amc_out, "results": results}, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "ppfas"}, indent=2))
    print("\n=== PPFAS ===")
    for r in summary["ppfas"]:
        print(
            f"  {r['amfi_code']} {r['period']} as_of={r.get('as_of')} {r['status']} "
            f"ours={r.get('our_top5')} api={r.get('api_top5')}"
        )
    print("\n=== 1 d.p. per AMC ===")
    for a in by_amc_out:
        print(f"  {a['match_1dp']}/{a['sampled']}  {a['amc_id']}  periods={a['periods']}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
