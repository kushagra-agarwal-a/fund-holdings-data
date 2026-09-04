#!/usr/bin/env python3
"""Regression: AMFI NAV history Plan/Option columns must not swap NAV and ISIN."""

from amfi_nav_history_asof import parse_history, validate_scheme_nav_fields

NEW_FORMAT_SAMPLE = """\
Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date

Aditya Birla Sun Life Mutual Fund
100033;Aditya Birla Sun Life Large & Mid Cap Fund - Regular Growth;Regular Plan;GROWTH;INF209K01165;;946.61;31-Jul-2026
"""

LEGACY_FORMAT_SAMPLE = """\
Scheme Code;Scheme Name;ISIN Div Payout/ Growth ISIN;ISIN Div Reinvestment;Net Asset Value;Repurchase Price;Sale Price;Date

Example AMC Mutual Fund
100033;Example Fund - Regular Growth;INF209K01165;;946.61;;;31-Jul-2026
"""


def test_new_format_columns():
    rows = parse_history(NEW_FORMAT_SAMPLE)
    assert len(rows) == 1
    row = rows[0]
    assert row["nav"] == "946.61"
    assert row["isin_growth_or_payout"] == "INF209K01165"
    assert row["plan"] == "Regular Plan"
    assert not validate_scheme_nav_fields(rows)


def test_legacy_format_columns():
    rows = parse_history(LEGACY_FORMAT_SAMPLE)
    assert len(rows) == 1
    row = rows[0]
    assert row["nav"] == "946.61"
    assert row["isin_growth_or_payout"] == "INF209K01165"
    assert not validate_scheme_nav_fields(rows)


if __name__ == "__main__":
    test_new_format_columns()
    test_legacy_format_columns()
    print("ok")
