"""Angel One Mutual Fund — one scheme per xlsx; sheet title ≈ scheme name."""
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

AMC_ID = "angel-one-mutual-fund"


def parse_file(path: Path) -> list[SchemePortfolio]:
    sheets = load_sheets(path)
    out: list[SchemePortfolio] = []
    dtype = disclosure_type_from_path(path)
    period = period_from_path(path)
    for sheet_name, rows in sheets:
        if not rows:
            continue
        scheme = extract_title_scheme(rows, sheet_name)
        holdings, _meta = parse_holdings_table(rows, prefer_leading_code=False)
        out.append(
            SchemePortfolio(
                amc_id=AMC_ID,
                disclosure_type=dtype,
                period=period,
                scheme_name=scheme,
                shortcode=None,
                as_of=extract_as_of(rows, filename=path.name),
                source_file=path.name,
                sheet_name=sheet_name,
                holdings=holdings,
            )
        )
    return out
