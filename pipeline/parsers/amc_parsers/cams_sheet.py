"""CAMS-style single-sheet packs used by SBI / Choice (and several other AMCs).

Layout markers:
  SCHEME NAME : <name>
  PORTFOLIO STATEMENT AS ON : <date>
  Optional leading security code column before instrument name.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import (
    SchemePortfolio,
    disclosure_type_from_path,
    extract_as_of,
    extract_scheme_name_cams,
    extract_title_scheme,
    load_sheets,
    parse_holdings_table,
    period_from_path,
)


def _shortcode_from_path(path: Path, sheet_name: str) -> str | None:
    sc = (sheet_name or "").strip()
    if sc and sc.lower() not in {"sheet", "index", "contents", "cover", "notes"}:
        return sc
    m = re.match(r"^([A-Z0-9]+)", path.stem.upper())
    return m.group(1) if m else None


def parse_cams_file(path: Path, *, amc_id: str) -> list[SchemePortfolio]:
    sheets = load_sheets(path)
    out: list[SchemePortfolio] = []
    dtype = disclosure_type_from_path(path)
    period = period_from_path(path)
    for sheet_name, rows in sheets:
        if not rows:
            continue
        low = sheet_name.strip().lower()
        if low in {"index", "contents", "cover", "notes"}:
            continue
        scheme = (
            extract_scheme_name_cams(rows)
            or extract_title_scheme(rows, sheet_name)
            or sheet_name.strip()
            or path.stem
        )
        if re.search(r"(?i)^name\s+of\s+the\s", scheme):
            scheme = extract_title_scheme(rows, sheet_name) or path.stem
        holdings, _meta = parse_holdings_table(rows, prefer_leading_code=True)
        out.append(
            SchemePortfolio(
                amc_id=amc_id,
                disclosure_type=dtype,
                period=period,
                scheme_name=scheme,
                shortcode=_shortcode_from_path(path, sheet_name),
                as_of=extract_as_of(rows, filename=path.name),
                source_file=path.name,
                sheet_name=sheet_name,
                holdings=holdings,
            )
        )
    return out
