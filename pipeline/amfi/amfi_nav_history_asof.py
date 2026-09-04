#!/usr/bin/env python3
"""Download AMFI NAV History for a single day and filter funds.json to that universe.

Schemes that only appear in later NAVAll dumps (e.g. August launches) drop out of
matching, so disclosure ↔ AMFI gaps shrink to funds that actually operated as-of
the disclosure cut-off.

Default as-of date: 31-Jul-2026
  https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=31-Jul-2026&todt=31-Jul-2026

Writes:
  data/amfi/NAVHistory_YYYY-MM-DD.txt
  data/amfi/active_codes_YYYY-MM-DD.json
  data/amfi/schemes_asof_YYYY-MM-DD.json
  data/amfi/funds_asof_YYYY-MM-DD.json
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path

ISIN_RE = re.compile(r"^INF[A-Z0-9]{9}$")
NUMERIC_NAV_RE = re.compile(r"^\d+(?:\.\d+)?$")


PORTAL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"


def parse_dd_mon_yyyy(s: str) -> datetime:
    return datetime.strptime(s, "%d-%b-%Y")


def asof_token(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def portal_date(dt: datetime) -> str:
    return dt.strftime("%d-%b-%Y")


def download(frm: datetime, to: datetime, dest: Path) -> None:
    url = f"{PORTAL}?frmdt={portal_date(frm)}&todt={portal_date(to)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _cell(parts: list[str], i: int) -> str | None:
    if i >= len(parts):
        return None
    v = parts[i].strip()
    if not v or v == "-":
        return None
    return v


def _looks_like_isin(v: str | None) -> bool:
    return bool(v and ISIN_RE.match(v.upper()))


def _looks_like_nav(v: str | None) -> bool:
    return bool(v and NUMERIC_NAV_RE.match(v.replace(",", "")))


def detect_history_layout(header: str) -> str:
    """AMFI added Plan + Option columns to NAV history (~2024).

    New: Scheme Code;NAV Name;Plan;Option;ISIN…;ISIN…;Net Asset Value;Date
    Old: Scheme Code;Scheme Name;ISIN…;ISIN…;Net Asset Value;Repurchase;Sale;Date
    """
    h = header.lower()
    if "nav name" in h and "plan" in h:
        return "plan_option"
    return "legacy"


def parse_history_row(
    parts: list[str],
    *,
    layout: str,
    amc: str | None,
    cat: str | None,
) -> dict | None:
    code = parts[0].strip()
    if not code.isdigit():
        return None

    if layout == "plan_option" or (
        len(parts) >= 8 and _looks_like_isin(_cell(parts, 4))
    ):
        return {
            "amfi_code": code,
            "name": parts[1].strip(),
            "plan": _cell(parts, 2),
            "option": _cell(parts, 3),
            "isin_growth_or_payout": _cell(parts, 4),
            "isin_div_reinvestment": _cell(parts, 5),
            "nav": _cell(parts, 6),
            "nav_date": _cell(parts, 7),
            "amc_name": amc,
            "category": cat,
        }

    nav = _cell(parts, 4)
    nav_date = _cell(parts, 7) if len(parts) >= 8 else _cell(parts, 5)
    return {
        "amfi_code": code,
        "name": parts[1].strip(),
        "plan": None,
        "option": None,
        "isin_growth_or_payout": _cell(parts, 2),
        "isin_div_reinvestment": _cell(parts, 3),
        "nav": nav,
        "nav_date": nav_date,
        "amc_name": amc,
        "category": cat,
    }


def parse_history(text: str) -> list[dict]:
    idx = text.find("Scheme Code;")
    if idx >= 0:
        text = text[idx:]

    lines = text.splitlines()
    layout = "legacy"
    for line in lines:
        if line.strip().startswith("Scheme Code;"):
            layout = detect_history_layout(line)
            break

    rows: list[dict] = []
    amc = None
    cat = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Scheme Code;"):
            layout = detect_history_layout(line)
            continue
        if ";" not in line:
            if (
                line.startswith("Open Ended")
                or line.startswith("Close Ended")
                or line.startswith("Interval")
            ):
                cat = line
            elif "Mutual Fund" in line:
                amc = line
            continue
        parts = line.split(";")
        if len(parts) < 5:
            continue
        row = parse_history_row(parts, layout=layout, amc=amc, cat=cat)
        if row:
            rows.append(row)
    return rows


def validate_scheme_nav_fields(schemes: list[dict]) -> list[str]:
    """Return human-readable errors when NAV/ISIN columns look swapped."""
    errors: list[str] = []
    for row in schemes:
        code = row.get("amfi_code")
        nav = row.get("nav")
        isin = row.get("isin_growth_or_payout")
        if _looks_like_isin(str(nav or "")):
            errors.append(f"{code}: nav looks like ISIN ({nav})")
        if isin and not _looks_like_isin(str(isin)) and not str(isin).startswith("-"):
            if "plan" in str(isin).lower() or "growth" in str(isin).lower() or "idcw" in str(isin).lower():
                errors.append(f"{code}: isin looks like plan/option ({isin})")
        if nav and not _looks_like_nav(str(nav)) and not _looks_like_isin(str(nav)):
            errors.append(f"{code}: nav is not numeric ({nav})")
    return errors


def filter_funds(funds: list[dict], active_codes: set[str]) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    dropped: list[dict] = []
    for f in funds:
        codes = set(f.get("amfi_codes") or [])
        live = codes & active_codes
        if not live:
            dropped.append(f)
            continue
        row = dict(f)
        row["amfi_codes_active"] = sorted(live)
        row["amfi_codes_inactive"] = sorted(codes - active_codes)
        if row.get("canonical_amfi_code") not in live:
            row["canonical_amfi_code"] = row["amfi_codes_active"][0]
        kept.append(row)
    return kept, dropped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asof", default="31-Jul-2026", help="dd-Mon-YYYY (default 31-Jul-2026)")
    ap.add_argument("--input", default="", help="Use existing NAV history file instead of download")
    ap.add_argument("--funds", default="data/amfi/funds.json")
    ap.add_argument("--out-dir", default="data/amfi")
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()

    asof = parse_dd_mon_yyyy(args.asof)
    token = asof_token(asof)
    out_dir = Path(args.out_dir)
    hist_path = Path(args.input) if args.input else out_dir / f"NAVHistory_{token}.txt"

    if not args.skip_download and not args.input:
        print(f"Downloading NAV history for {portal_date(asof)} …")
        download(asof, asof, hist_path)

    text = hist_path.read_text(encoding="utf-8", errors="replace")
    schemes = parse_history(text)
    field_errors = validate_scheme_nav_fields(schemes)
    if field_errors:
        sample = "\n".join(f"  - {e}" for e in field_errors[:8])
        raise SystemExit(
            f"NAV/ISIN column sanity check failed ({len(field_errors)} rows). "
            f"AMFI layout may have changed again.\n{sample}"
        )
    active_codes = {r["amfi_code"] for r in schemes}

    funds = json.loads(Path(args.funds).read_text(encoding="utf-8"))
    kept, dropped = filter_funds(funds, active_codes)

    (out_dir / f"active_codes_{token}.json").write_text(
        json.dumps(sorted(active_codes), indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / f"schemes_asof_{token}.json").write_text(
        json.dumps(schemes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    funds_asof = out_dir / f"funds_asof_{token}.json"
    funds_asof.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = {
        "asof": portal_date(asof),
        "source": str(hist_path),
        "active_plan_codes": len(active_codes),
        "base_funds_before": len(funds),
        "base_funds_after": len(kept),
        "base_funds_dropped": len(dropped),
        "funds_asof_path": str(funds_asof),
    }
    (out_dir / f"asof_summary_{token}.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"As-of {portal_date(asof)}: {len(active_codes)} plan codes → "
        f"{len(kept)} base funds (dropped {len(dropped)} of {len(funds)})\n"
        f"Wrote {funds_asof}"
    )


if __name__ == "__main__":
    main()
