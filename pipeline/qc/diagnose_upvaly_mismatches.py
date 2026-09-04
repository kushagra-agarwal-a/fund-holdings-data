#!/usr/bin/env python3
"""Classify June-30 vs Upvaly top-5 mismatches (names + weights)."""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qc"))
from upvaly_random100_compare import fetch_api, norm_name, parse_weight  # noqa: E402
from upvaly_top5_per_amc import as_percent_weights  # noqa: E402

REPORT = ROOT / "data/parsed/upvaly_top5_june30_jul15_report.json"
OUT = ROOT / "tmp/upvaly_mismatch_diagnosis.json"

CASH_RE = re.compile(
    r"(?i)receivable|payable|net current|nca\b|treps|tri[\s\-]?party|reverse repo|"
    r"cash margin|cash(\s|&)|margin money|offset for derivative|"
    r"net assets|cblo|clearing corporation"
)
FUT_RE = re.compile(r"(?i)\bfutures?\b|\bfutcom\b|index future|stock future")
HEADER_RE = re.compile(
    r"(?i)^(equity|debt|money market|derivatives|others|gold|silver|"
    r"listed|unlisted|a\)|b\)|c\)|i\)|ii\))"
)


def pick_name(h: dict, preferred: str | None = None) -> str:
    keys = [
        preferred,
        "instrument",
        "name",
        "stockName",
        "companyName",
        "securityName",
        "isin",
    ]
    for k in keys:
        if not k:
            continue
        v = h.get(k)
        if v:
            return str(v).strip()
    return ""


def named_holdings(rows: list[dict], name_key: str, w_key: str) -> list[tuple[str, float]]:
    out = []
    for h in rows or []:
        w = parse_weight(h.get(w_key))
        if w is None:
            continue
        name = pick_name(h, name_key)
        out.append((name, w))
    ws = as_percent_weights([w for _, w in out])
    named = [(n, w) for (n, _), w in zip(out, ws)]
    named.sort(key=lambda x: -x[1])
    return named


def our_named(period: str, amc: str, shortcode: str) -> list[tuple[str, float]]:
    root = (
        ROOT / "data/parsed/monthly/2026-06"
        if period == "2026-06"
        else ROOT / "data/parsed/fortnightly/2026-07-15"
    )
    pj = root / amc / str(shortcode) / "portfolio.json"
    if not pj.exists():
        # folder may differ from shortcode
        amc_dir = root / amc
        if amc_dir.exists():
            for p in amc_dir.glob("*/portfolio.json"):
                if p.parent.name.upper() == str(shortcode).upper():
                    pj = p
                    break
    if not pj.exists():
        return []
    data = json.loads(pj.read_text())
    hs = data.get("holdings") if isinstance(data, dict) else data
    return named_holdings(hs or [], "name", "pct_nav")


def best_name_hit(name: str, others: list[str], cutoff: int = 80) -> tuple[str | None, int]:
    nn = norm_name(name)
    if not nn:
        return None, 0
    best, score = None, 0
    for o in others:
        s = fuzz.token_set_ratio(nn, norm_name(o))
        if s > score:
            best, score = o, s
    return (best, score) if score >= cutoff else (None, score)


def can_rollup(our: list[tuple[str, float]], api_w: float, used: set[int]) -> bool:
    """True if 2+ unused our rows sum to api_w within 0.15pp."""
    cands = [(i, w) for i, (_, w) in enumerate(our[:25]) if i not in used and 0.05 < w < api_w]
    for i, w1 in cands:
        need = api_w - w1
        for j, w2 in cands:
            if j <= i:
                continue
            if abs(w1 + w2 - api_w) <= 0.15:
                return True
            for k, w3 in cands:
                if k <= j:
                    continue
                if abs(w1 + w2 + w3 - api_w) <= 0.15:
                    return True
        if abs(need) <= 0.15:
            continue
    return False


def stale_as_of(as_of: str | None, period: str) -> bool:
    if not as_of:
        return False
    t = as_of.upper().replace(" ", "")
    if period == "2026-06":
        return bool(re.search(r"MAY|APR|MAR|JUL|31[\-/]?JUL|JULY", t)) and "JUN" not in t and "JUNE" not in t
    if period == "2026-07-15":
        return "JUN" in t or "JUNE" in t or "MAY" in t or bool(re.search(r"31|JULY\s*31", t))
    return False


def classify(row: dict, ours: list[tuple[str, float]], apis: list[tuple[str, float]]) -> dict:
    our5 = ours[:5]
    api5 = apis[:5]
    our_names = [n for n, _ in our5]
    api_names = [n for n, _ in api5]
    hits = []
    for n, w in our5:
        hit, sc = best_name_hit(n, api_names)
        hits.append({"our": n, "our_w": w, "api": hit, "score": sc})
    n_name_hits = sum(1 for h in hits if h["api"])
    api_cash = [(n, w) for n, w in api5 if CASH_RE.search(n)]
    our_cash = [(n, w) for n, w in our5 if CASH_RE.search(n)]
    our_cash_all = [(n, w) for n, w in ours[:20] if CASH_RE.search(n)]
    api_fut = [(n, w) for n, w in api5 if FUT_RE.search(n)]
    our_fut = [(n, w) for n, w in our5 if FUT_RE.search(n)]
    our_header = [(n, w) for n, w in our5 if HEADER_RE.search(n or "")]

    # weight-only overlap (multiset of 2dp)
    our_ws = [round(w, 2) for _, w in our5]
    api_ws = [round(w, 2) for _, w in api5]
    shared_w = [w for w in our_ws if w in api_ws]

    used = set()
    rollup_hits = 0
    for aw in api_ws:
        if aw in our_ws:
            # consume one
            try:
                used.add(next(i for i, w in enumerate(our_ws) if w == aw and i not in used))
            except StopIteration:
                pass
            continue
        if can_rollup(ours, aw, used):
            rollup_hits += 1

    reason = "composition_different"
    if row.get("status") == "short_top5":
        reason = "short_top5"
    elif our_header:
        reason = "our_header_rows_in_top5"
    elif stale_as_of(row.get("as_of"), row.get("period") or ""):
        reason = "stale_as_of_file"
    elif api_cash and not our_cash and (
        not our_cash_all or abs(api_cash[0][1] - (our_cash_all[0][1] if our_cash_all else 0)) > 0.5
    ):
        # Upvaly cash/TREPS/NCA sits in their top-5; we either omit it or rank it lower
        reason = "upvaly_cash_nca_in_top5"
    elif our_cash and not api_cash:
        reason = "our_cash_nca_in_top5"
    elif api_fut or our_fut:
        reason = "futures_in_top5"
    elif rollup_hits >= 1 and n_name_hits <= 3:
        reason = "issuer_or_line_rollup"
    elif n_name_hits >= 4:
        reason = "same_names_weight_drift"
    elif n_name_hits >= 2 and len(shared_w) >= 2:
        reason = "partial_overlap_weight_mix"
    elif n_name_hits <= 1:
        reason = "composition_different"

    return {
        "reason": reason,
        "n_name_hits": n_name_hits,
        "shared_weights": shared_w,
        "rollup_hits": rollup_hits,
        "api_cash": [{"name": n, "w": round(w, 2)} for n, w in api_cash],
        "our_cash_top5": [{"name": n, "w": round(w, 2)} for n, w in our_cash],
        "our_cash_book": [{"name": n, "w": round(w, 2)} for n, w in our_cash_all[:3]],
        "stale_as_of": stale_as_of(row.get("as_of"), row.get("period") or ""),
        "our_top5_named": [{"name": n, "w": round(w, 2)} for n, w in our5],
        "api_top5_named": [{"name": n, "w": round(w, 2)} for n, w in api5],
        "name_align": hits,
    }


def main() -> None:
    rep = json.loads(REPORT.read_text())
    rows = [r for r in rep["results"] if r.get("status") != "match_2dp"]
    print(f"non-exact={len(rows)} (mismatch+short+1dp_only)", flush=True)
    out_rows = []
    for i, r in enumerate(rows, 1):
        ours = our_named(r["period"], r["amc_id"], r["shortcode"])
        try:
            data = fetch_api(r["amfi_code"])
            apis = named_holdings(data.get("holdings") or [], "name", "weightage")
            if not apis:
                # some payloads use stockName / companyName
                hs = data.get("holdings") or []
                nk = "name"
                if hs:
                    for k in ("stockName", "companyName", "instrument", "securityName"):
                        if hs[0].get(k):
                            nk = k
                            break
                apis = named_holdings(hs, nk, "weightage")
            diag = classify(r, ours, apis)
        except Exception as e:
            diag = {"reason": "api_error", "error": str(e)[:200]}
        rec = {
            "amc_id": r["amc_id"],
            "amfi_code": r["amfi_code"],
            "shortcode": r.get("shortcode"),
            "scheme": r.get("scheme"),
            "api_scheme": r.get("api_scheme"),
            "period": r["period"],
            "as_of": r.get("as_of"),
            "status": r["status"],
            "our_top5": r.get("our_top5"),
            "api_top5": r.get("api_top5"),
            **diag,
        }
        out_rows.append(rec)
        print(
            f"  {i}/{len(rows)} {r['amc_id'][:22]:22} {r['amfi_code']} "
            f"{rec.get('reason')} names={rec.get('n_name_hits')}",
            flush=True,
        )
        time.sleep(0.25)

    counts = Counter(r.get("reason") for r in out_rows)
    by_amc = defaultdict(list)
    for r in out_rows:
        by_amc[r["amc_id"]].append(r["reason"])
    summary = {
        "n": len(out_rows),
        "reason_counts": dict(counts),
        "by_amc_primary": {
            a: Counter(rs).most_common(1)[0][0] for a, rs in sorted(by_amc.items())
        },
    }
    OUT.write_text(json.dumps({"summary": summary, "funds": out_rows}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
