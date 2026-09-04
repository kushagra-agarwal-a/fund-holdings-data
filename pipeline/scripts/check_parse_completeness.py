#!/usr/bin/env python3
"""Fail if disclosure files are missing from parsed schemes.json.

Catches the Mirae-style failure mode: mid-run kill left 82/97 files parsed,
enrich/sync still succeeded, and ETF/index parents quietly vanished from CDN.

Examples:
  .venv/bin/python3 scripts/check_parse_completeness.py --type=monthly --period=2026-07
  .venv/bin/python3 scripts/check_parse_completeness.py --type=monthly --period=2026-07 --amc=mirae-asset-mutual-fund
  .venv/bin/python3 scripts/check_parse_completeness.py --type=monthly --period=2026-07 --allow-incomplete
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "parsers"))

from amc_parsers.parse_progress import check_period_completeness  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--type", choices=["monthly", "fortnightly"], required=True)
    ap.add_argument("--period", required=True, help="YYYY-MM or latest")
    ap.add_argument("--amc", action="append", default=[], help="Limit to AMC id (repeatable)")
    ap.add_argument(
        "--disc-root",
        type=Path,
        default=ROOT / "data" / "disclosures",
    )
    ap.add_argument(
        "--parsed-root",
        type=Path,
        default=ROOT / "data" / "parsed",
    )
    ap.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Print report but exit 0 even when gaps exist",
    )
    ap.add_argument(
        "--min-missing",
        type=int,
        default=1,
        help="Treat an AMC incomplete when missing+stale files >= this (default 1)",
    )
    args = ap.parse_args()

    report = check_period_completeness(
        disclosure_type=args.type,
        period=args.period,
        disc_root=args.disc_root,
        parsed_root=args.parsed_root,
        amc_ids=args.amc or None,
    )

    if args.min_missing > 1:
        incomplete = []
        for row in report.get("rows") or []:
            gaps = len(row.get("missing_files") or []) + len(row.get("stale_files") or [])
            if gaps >= args.min_missing:
                incomplete.append(row)
        report["incomplete_amcs"] = len(incomplete)
        report["incomplete"] = [
            {
                "amc_id": r["amc_id"],
                "disclosure_files": r["disclosure_files"],
                "covered_files": r["covered_files"],
                "missing": len(r["missing_files"]),
                "stale": len(r["stale_files"]),
                "missing_samples": r["missing_files"][:5],
                "stale_samples": r["stale_files"][:5],
            }
            for r in incomplete
        ]
        report["complete"] = not incomplete

    # Compact stdout for humans / CI; full rows available via --verbose later if needed
    out = {
        "disclosure_type": report["disclosure_type"],
        "period": report["period"],
        "amcs": report["amcs"],
        "incomplete_amcs": report["incomplete_amcs"],
        "complete": report["complete"],
        "incomplete": report["incomplete"],
    }
    print(json.dumps(out, indent=2))

    if report["complete"] or args.allow_incomplete:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
