#!/usr/bin/env python3
"""Rebuild full-catalog coverage against AMFI as-of funds (2334).

Statuses (only):
  mapped         — disclosure match and/or sibling/key_share/compact
  unmapped_open  — still needs work (never 'not_available' without human say-so)

Writes:
  data/parsed/catalog_coverage_2334.json
  data/parsed/catalog_coverage_2334.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from match_disclosure_amfi import (
    compact_name,
    is_debt_like_amfi_fund,
    load_amc_registry,
    map_amcs_to_amfi,
)


def load_kind(kind: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    d = Path(f"data/parsed/{kind}/latest/_matching")
    if not d.exists():
        return out
    for p in d.glob("*.json"):
        if p.name.startswith("_"):
            continue
        j = json.loads(p.read_text(encoding="utf-8"))
        out[j["amc_id"]] = j
    return out


def rebuild(*, amfi_funds: Path, registry: Path, shortcode_map: Path | None = None) -> dict:
    funds = json.loads(amfi_funds.read_text(encoding="utf-8"))
    amcs = load_amc_registry(registry)
    amc_map = map_amcs_to_amfi(amcs, sorted({f["amc_name"] for f in funds}))
    amfi_to_ids: dict[str, list[str]] = defaultdict(list)
    for aid, m in amc_map.items():
        if m.get("amfi_amc_name"):
            amfi_to_ids[m["amfi_amc_name"]].append(aid)

    from match_disclosure_amfi import load_shortcode_map, normalize_shortcode

    sc_by_amfi_code: dict[str, list[str]] = defaultdict(list)
    sc_path = shortcode_map or Path("data/sources/disclosure_shortcode_map.json")
    for key, row in load_shortcode_map(sc_path).items():
        code = str(row.get("canonical_amfi_code") or "").strip()
        sc = normalize_shortcode(row.get("shortcode"))
        if code and sc and sc not in sc_by_amfi_code[code]:
            sc_by_amfi_code[code].append(sc)

    mo, fn = load_kind("monthly"), load_kind("fortnightly")

    mapped_by_amfi: dict[str, set[str]] = defaultdict(set)
    seen_by_amfi: dict[str, set[str]] = defaultdict(set)
    via_by_amfi: dict[str, dict[str, str]] = defaultdict(dict)
    disc_sc_by_amfi: dict[str, dict[str, str]] = defaultdict(dict)

    for aid in set(mo) | set(fn):
        amfi_name = None
        for src in (mo.get(aid), fn.get(aid)):
            if not src:
                continue
            amfi_name = (src.get("amc_map") or {}).get("amfi_amc_name") or amfi_name
            for row in src.get("matched_amfi") or []:
                ab = row.get("base_name")
                if not ab:
                    continue
                mapped_by_amfi[amfi_name].add(ab)
                mapped_by_amfi[amfi_name].add(compact_name(ab))
                seen_by_amfi[amfi_name].add(ab)
                seen_by_amfi[amfi_name].add(compact_name(ab))
                via_by_amfi[amfi_name][ab] = row.get("via") or "direct"
            # Back-compat if matched_amfi missing
            for row in src.get("matched") or []:
                ab = row.get("amfi_base_name")
                if not ab:
                    continue
                mapped_by_amfi[amfi_name].add(ab)
                mapped_by_amfi[amfi_name].add(compact_name(ab))
                seen_by_amfi[amfi_name].add(ab)
                seen_by_amfi[amfi_name].add(compact_name(ab))
                via_by_amfi[amfi_name].setdefault(ab, row.get("via") or "direct")
                sc = row.get("shortcode")
                if sc:
                    disc_sc_by_amfi[amfi_name].setdefault(ab, sc)
            for row in src.get("matched_disclosures") or []:
                ab = row.get("amfi_base_name")
                sc = row.get("shortcode")
                if ab and sc:
                    disc_sc_by_amfi[amfi_name].setdefault(ab, sc)
            for row in src.get("unmatched_amfi") or []:
                ab = row.get("base_name")
                if ab:
                    seen_by_amfi[amfi_name].add(ab)
                    seen_by_amfi[amfi_name].add(compact_name(ab))

    rows = []
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()

    for f in funds:
        amfi = f["amc_name"]
        name = f["base_name"]
        c = compact_name(name)
        ids = amfi_to_ids.get(amfi) or []
        has_mo = any(aid in mo for aid in ids)
        has_fn = any(aid in fn for aid in ids)
        mapped_set = mapped_by_amfi.get(amfi) or set()
        seen_set = seen_by_amfi.get(amfi) or set()
        amfi_code = str(f.get("canonical_amfi_code") or "")
        shortcodes = sc_by_amfi_code.get(amfi_code) or []
        if not shortcodes:
            dsc = disc_sc_by_amfi.get(amfi, {}).get(name)
            if dsc:
                shortcodes = [dsc]

        if name in mapped_set or c in mapped_set:
            status = "mapped"
            reason = via_by_amfi.get(amfi, {}).get(name) or "matched_to_disclosure"
        elif not has_mo and not has_fn:
            status = "unmapped_open"
            reason = "no_files_fetched"
        elif not has_mo and has_fn and not is_debt_like_amfi_fund(name):
            status = "unmapped_open"
            reason = "fn_only_no_monthly_yet"
        elif name not in seen_set and c not in seen_set:
            status = "unmapped_open"
            reason = "absent_from_match_json"
        else:
            status = "unmapped_open"
            reason = "no_disclosure_match"

        status_counts[status] += 1
        reason_counts[reason] += 1
        rows.append(
            {
                "amc_name": amfi,
                "base_name": name,
                "canonical_amfi_code": f.get("canonical_amfi_code"),
                "disclosure_shortcodes": "|".join(shortcodes),
                "status": status,
                "reason": reason,
                "has_monthly_match": has_mo,
                "has_fortnightly_match": has_fn,
            }
        )

    summary = {
        "universe": len(funds),
        "mapped": status_counts["mapped"],
        "unmapped_open": status_counts["unmapped_open"],
        "map_rate": round(100 * status_counts["mapped"] / max(1, len(funds)), 2),
        "shortcode_mapped_funds": sum(1 for r in rows if r["status"] == "mapped" and r["disclosure_shortcodes"]),
    }
    return {
        "summary": summary,
        "status_counts": dict(status_counts),
        "reason_counts": dict(reason_counts),
        "funds": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--amfi-funds", default="data/amfi/funds_asof_2026-07-31.json")
    ap.add_argument(
        "--registry",
        default="registry/amcs.json"
        if Path("registry/amcs.json").exists()
        else "data/sources/amcs.json",
    )
    ap.add_argument(
        "--shortcode-map",
        default="registry/disclosure_shortcode_map.json"
        if Path("registry/disclosure_shortcode_map.json").exists()
        else "data/sources/disclosure_shortcode_map.json",
    )
    ap.add_argument("--out-json", default="data/parsed/catalog_coverage_2334.json")
    ap.add_argument("--out-csv", default="data/parsed/catalog_coverage_2334.csv")
    args = ap.parse_args()

    out = rebuild(
        amfi_funds=Path(args.amfi_funds),
        registry=Path(args.registry),
        shortcode_map=Path(args.shortcode_map),
    )
    Path(args.out_json).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with Path(args.out_csv).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out["funds"][0].keys()))
        w.writeheader()
        w.writerows(out["funds"])
    print(json.dumps(out["summary"], indent=2))
    print("reasons:")
    for k, v in sorted(out["reason_counts"].items(), key=lambda x: -x[1]):
        print(f"  {v:4d}  {k}")
    print(f"Wrote {args.out_json}")
    print(f"Wrote {args.out_csv}")


if __name__ == "__main__":
    main()
