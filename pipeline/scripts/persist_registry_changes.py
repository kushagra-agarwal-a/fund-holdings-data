#!/usr/bin/env python3
"""Commit + push durable registry/parser pipeline fixes so they never stay local.

Intended for the automated month-end loop after match/enrich/catalog. Only
stages allowlisted paths (shortcode map, aliases, locks, parsers, sync scripts).

Usage:
  .venv/bin/python3 scripts/persist_registry_changes.py
  .venv/bin/python3 scripts/persist_registry_changes.py --dry-run
  .venv/bin/python3 scripts/persist_registry_changes.py --message='chore: refresh shortcode map'
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Never leave these on a laptop after a successful month-end run.
ALLOWLIST = [
    "registry/disclosure_shortcode_map.json",
    "registry/amfi_holdings_aliases.json",
    "registry/holdings_mapping_locks.json",
    "parsers/amc_parsers/family.py",
    "parsers/amc_parsers/parse_progress.py",
    "parsers/run_amc_parser.py",
    "scripts/check_parse_completeness.py",
    "scripts/assert_holdings_mapping_locks.py",
    "scripts/persist_registry_changes.py",
    "scripts/enrich_holdings_identifiers.py",
    "scripts/build_holdings_browser_catalog.py",
    "scripts/sync-holdings-to-github.mjs",
    "scripts/upload-holdings-to-b2.mjs",
    "scrapers/node/fetch-period.js",
    "scrapers/node/lib/http.js",
    "holdings-browser/public/catalog.json",
    "holdings-browser/api/amfi-lookup.json",
    "package.json",
    "docs/PIPELINE.md",
    "docs/GITHUB_HOLDINGS.md",
    ".gitignore",
]


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--message",
        default="chore: persist holdings registry and parse durability fixes",
    )
    ap.add_argument(
        "--no-push",
        action="store_true",
        help="Commit only; skip git push",
    )
    args = ap.parse_args()

    assert_cp = run(
        [sys.executable, str(ROOT / "scripts" / "assert_holdings_mapping_locks.py")],
        check=False,
    )
    if assert_cp.returncode != 0:
        sys.stderr.write(assert_cp.stdout or "")
        sys.stderr.write(assert_cp.stderr or "")
        return assert_cp.returncode

    existing = [p for p in ALLOWLIST if (ROOT / p).exists()]
    status = run(["git", "status", "--porcelain", "--", *existing], check=False)
    dirty = [ln for ln in status.stdout.splitlines() if ln.strip()]
    if not dirty:
        print("persist: nothing to commit (allowlist clean)")
        return 0

    print("persist: staging")
    for ln in dirty:
        print(" ", ln)

    if args.dry_run:
        print("persist: dry-run, not committing")
        return 0

    run(["git", "add", "--", *existing])
    staged = run(["git", "diff", "--cached", "--name-only", "--", *existing])
    if not staged.stdout.strip():
        print("persist: nothing staged after add")
        return 0

    run(["git", "commit", "-m", args.message])
    if args.no_push:
        print("persist: committed (push skipped)")
        return 0

    push = run(["git", "push", "origin", "HEAD"], check=False)
    sys.stdout.write(push.stdout or "")
    sys.stderr.write(push.stderr or "")
    if push.returncode != 0:
        return push.returncode
    print("persist: committed and pushed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
