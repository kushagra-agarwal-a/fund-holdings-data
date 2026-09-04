#!/usr/bin/env python3
"""
Run all AMC monthly disclosure fetchers for one or more YYYY-MM values.

Example:
  python3 scripts/fetch_all_amcs.py --months 2026-02
  python3 scripts/fetch_all_amcs.py --months 2026-01 2026-02 --amcs axis-mutual-fund kotak-mahindra-mutual-fund
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AmcFetcher:
    slug: str
    script: str
    extra_args: tuple[str, ...] = ()


AMC_FETCHERS: list[AmcFetcher] = [
    AmcFetcher("360-one-mutual-fund", "fetch_360_one.py"),
    AmcFetcher("abakkus-mutual-fund", "fetch_abakkus.py"),
    AmcFetcher("aditya-birla-sun-life-mutual-fund", "fetch_absl.py"),
    AmcFetcher("angel-one-mutual-fund", "fetch_angel_one.py"),
    AmcFetcher("axis-mutual-fund", "fetch_axis.py"),
    AmcFetcher("bajaj-finserv-mutual-fund", "fetch_bajaj.py"),
    AmcFetcher("bandhan-mutual-fund", "fetch_bandhan.py"),
    AmcFetcher("bank-of-india-mutual-fund", "fetch_boi.py"),
    AmcFetcher("baroda-bnp-paribas-mutual-fund", "fetch_baroda_bnp.py"),
    AmcFetcher("canara-robeco-mutual-fund", "fetch_canara_robeco.py"),
    AmcFetcher("capitalmind-mutual-fund", "fetch_capitalmind.py"),
    AmcFetcher("choice-mutual-fund", "fetch_choice.py"),
    AmcFetcher("dsp-mutual-fund", "fetch_dsp.py"),
    AmcFetcher("edelweiss-mutual-fund", "fetch_edelweiss.py"),
    AmcFetcher("franklin-templeton-mutual-fund", "fetch_franklin.py"),
    AmcFetcher("groww-mutual-fund", "fetch_groww.py"),
    AmcFetcher("hdfc-mutual-fund", "fetch_hdfc.py"),
    AmcFetcher("helios-mutual-fund", "fetch_helios.py"),
    AmcFetcher("hsbc-mutual-fund", "fetch_hsbc.py"),
    AmcFetcher("icici-prudential-mutual-fund", "fetch_icici.py"),
    AmcFetcher("ilfs-mutual-fund-idf", "fetch_ilfs.py"),
    AmcFetcher("invesco-mutual-fund", "fetch_invesco.py"),
    AmcFetcher("iti-mutual-fund", "fetch_iti.py"),
    AmcFetcher("jio-blackrock-mutual-fund", "fetch_jio_blackrock.py"),
    AmcFetcher("jm-financial-mutual-fund", "fetch_jm_financial.py"),
    AmcFetcher("kotak-mahindra-mutual-fund", "fetch_kotak.py"),
    AmcFetcher("lic-mutual-fund", "fetch_lic.py", ("--insecure-ssl",)),
    AmcFetcher("mahindra-manulife-mutual-fund", "fetch_mahindra_manulife.py"),
    AmcFetcher("mirae-asset-mutual-fund", "fetch_mirae.py"),
    AmcFetcher("motilal-oswal-mutual-fund", "fetch_motilal.py"),
    AmcFetcher("navi-mutual-fund", "fetch_navi.py"),
    AmcFetcher("nj-mutual-fund", "fetch_nj.py"),
    AmcFetcher("nippon-india-mutual-fund", "fetch_nippon.py"),
    AmcFetcher("old-bridge-mutual-fund", "fetch_oldbridge.py"),
    AmcFetcher("parag-parikh-mutual-fund", "fetch_ppfas.py"),
    AmcFetcher("pgim-india-mutual-fund", "fetch_pgim.py"),
    AmcFetcher("quantum-mutual-fund", "fetch_quantum.py"),
    AmcFetcher("samco-mutual-fund", "fetch_samco.py"),
    AmcFetcher("sbi-mutual-fund", "fetch_sbi.py"),
    AmcFetcher("shriram-mutual-fund", "fetch_shriram.py"),
    AmcFetcher("sundaram-mutual-fund", "fetch_sundaram.py"),
    AmcFetcher("tata-mutual-fund", "fetch_tata.py"),
    AmcFetcher("taurus-mutual-fund", "fetch_taurus.py"),
    AmcFetcher("trust-mutual-fund", "fetch_trust.py"),
    AmcFetcher("union-mutual-fund", "fetch_union.py"),
    AmcFetcher("uti-mutual-fund", "fetch_uti.py"),
]


def is_valid_month_key(value: str) -> bool:
    if len(value) != 7:
        return False
    year, sep, month = value.partition("-")
    return sep == "-" and year.isdigit() and month.isdigit() and 1 <= int(month) <= 12


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    scripts_dir = repo_root / "scripts"

    parser = argparse.ArgumentParser(description="Run all AMC fetchers for one or more months")
    parser.add_argument("--months", nargs="+", required=True, help="One or more YYYY-MM values")
    parser.add_argument(
        "--amcs",
        nargs="*",
        help="Optional subset of AMC slugs to run (default: all supported AMCs)",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately on first failed AMC fetcher",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to use for child scripts (default: current interpreter)",
    )
    args = parser.parse_args()

    bad = [m for m in args.months if not is_valid_month_key(m)]
    if bad:
        raise SystemExit(f"Invalid --months values: {', '.join(bad)} (expected YYYY-MM)")

    selected = AMC_FETCHERS
    if args.amcs:
        requested = set(args.amcs)
        known = {x.slug for x in AMC_FETCHERS}
        unknown = sorted(requested - known)
        if unknown:
            raise SystemExit(f"Unknown AMC slug(s): {', '.join(unknown)}")
        selected = [x for x in AMC_FETCHERS if x.slug in requested]

    print(f"Running {len(selected)} AMC fetcher(s) for months: {', '.join(args.months)}", flush=True)
    print(f"Repo root: {repo_root}", flush=True)

    failures: list[tuple[str, int]] = []
    started = time.time()

    for i, amc in enumerate(selected, 1):
        script_path = scripts_dir / amc.script
        if not script_path.is_file():
            print(f"\n[{i}/{len(selected)}] {amc.slug}: SKIP (missing {amc.script})", flush=True)
            failures.append((amc.slug, 127))
            if args.stop_on_error:
                break
            continue

        cmd = [
            args.python,
            str(script_path),
            "--months",
            *args.months,
            "--root",
            str(repo_root),
            *amc.extra_args,
        ]
        print(f"\n[{i}/{len(selected)}] {amc.slug}", flush=True)
        print("  $ " + " ".join(cmd), flush=True)
        proc = subprocess.run(cmd, cwd=str(repo_root))
        if proc.returncode != 0:
            failures.append((amc.slug, proc.returncode))
            print(f"  -> FAILED (exit {proc.returncode})", flush=True)
            if args.stop_on_error:
                break
        else:
            print("  -> OK", flush=True)

    elapsed = time.time() - started
    ok = len(selected) - len(failures)
    print("\n=== Fetch summary ===", flush=True)
    print(f"Total selected: {len(selected)}", flush=True)
    print(f"Successful: {ok}", flush=True)
    print(f"Failed: {len(failures)}", flush=True)
    print(f"Elapsed: {elapsed:.1f}s", flush=True)
    if failures:
        print("Failed AMCs:", flush=True)
        for slug, code in failures:
            print(f"  - {slug} (exit {code})", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
