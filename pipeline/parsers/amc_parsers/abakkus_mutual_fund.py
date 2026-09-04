"""Abakkus Mutual Fund — multi-sheet .xls (OOXML) pack; tab = shortcode."""
from __future__ import annotations

from pathlib import Path

from .common import (
    SchemePortfolio,
    disclosure_type_from_path,
    extract_as_of,
    extract_title_scheme,
    load_sheets,
    parse_holdings_table,
    period_from_path,
)

AMC_ID = "abakkus-mutual-fund"


def parse_file(path: Path) -> list[SchemePortfolio]:
    sheets = load_sheets(path)
    out: list[SchemePortfolio] = []
    dtype = disclosure_type_from_path(path)
    period = period_from_path(path)
    for sheet_name, rows in sheets:
        if not rows:
            continue
        # Skip index-like tabs
        if sheet_name.strip().lower() in {"index", "contents", "cover"}:
            continue
        scheme = extract_title_scheme(rows, sheet_name)
        holdings, _meta = parse_holdings_table(rows, prefer_leading_code=True)
        out.append(
            SchemePortfolio(
                amc_id=AMC_ID,
                disclosure_type=dtype,
                period=period,
                scheme_name=scheme,
                shortcode=sheet_name.strip() or None,
                as_of=extract_as_of(rows, filename=path.name),
                source_file=path.name,
                sheet_name=sheet_name,
                holdings=holdings,
            )
        )
    return out
