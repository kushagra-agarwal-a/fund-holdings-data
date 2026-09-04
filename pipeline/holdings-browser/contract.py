"""Public holdings API contract. Every holding emits the same keys."""
from __future__ import annotations

import math
import re
from typing import Any

MARKET_VALUE_UNIT = "INR_LAKH"
PCT_NAV_UNIT = "percent"

HOLDING_TYPES = (
    "equity",
    "debt",
    "money_market",
    "cash",
    "derivative",
    "commodity",
    "fund_unit",
    "other",
)

SCHEME_KEYS = (
    "amfi_code",
    "name",
    "amc_name",
    "parent_name",
    "parent_amfi",
    "nav",
    "nav_date",
    "isin",
    "category",
)

META_KEYS = (
    "as_of",
    "disclosure_type",
    "period",
    "disclosure_shortcode",
    "holding_count",
    "source_file",
    "market_value_unit",
    "pct_nav_unit",
)

HOLDING_KEYS = (
    "holding_type",
    "instrument",
    "isin",
    "section",
    "industry",
    "rating",
    "coupon",
    "maturity_date",
    "quantity",
    "market_value",
    "pct_nav",
    "ytm",
    "ytc",
    "instrument_yield",
    "listed_status",
    "underlying",
    "position_side",
)

_EMPTY = {
    "",
    "-",
    "--",
    "—",
    "na",
    "n/a",
    "n.a.",
    "nil",
    "null",
    "none",
    ".",
    "nan",
    "^",
    "#",
    "@",
    "$",
    "% to nav",
    "% to n.a.v",
}

_CASH_RE = re.compile(
    r"treps?|tri[\s-]?party|reverse\s+repos?|\bcblo\b|clearing\s+corporation|\bccil\b|"
    r"amc\s+repo\s+clearing|net\s+current\s+assets?|\bnca\b|net\s+receivables?|net\s+payables?|"
    r"receivable\s*/\s*\(?\s*payable|payables?\s*/\s*\(?\s*receivable|cash\s+margin|margin\s+money|"
    r"cash\s*/\s*bank|cash\s+and\s+other|call,\s*cash|^\s*cash\s*$|^\s*cash\s*/|\brepos?\b|"
    r"^\s*trp[_-]|^\s*rep\d+",
    re.I,
)
_NOT_CASH_RE = re.compile(
    r"interest\s+rate\s+swaps?|\birs\b|(?<![A-Za-z])ois(?![A-Za-z])|t[\s-]?bill|treasury\s+bill|"
    r"commercial\s+paper|certificate\s+of\s+deposit|\bdebenture\b|\bncd\b",
    re.I,
)
_DERIV_RE = re.compile(
    r"\bfutures?\b|\boptions?\b|\bderivatives?\b|covered\s+call|interest\s+rate\s+swaps?|\birs\b|"
    r"(?<![A-Za-z])ois(?![A-Za-z])|\bswaps?\b",
    re.I,
)
_COMMODITY_RE = re.compile(
    r"^\s*\(?\s*(?:[a-z]\)\s*)?gold(?:\s+\d{3}\s+purity)?\s*\)?\s*$|"
    r"^\s*\(?\s*(?:[a-z]\)\s*)?silver\s*\)?\s*$|"
    r"physical\s+gold|physical\s+silver|gold\s+bar|silver\s+bar|gold\s+\d{3}\s+purity",
    re.I,
)
_NOT_COMMODITY_RE = re.compile(r"sovereign\s+gold|gold\s+bond", re.I)
_FUND_RE = re.compile(
    r"mutual\s+fund|units?\s+of\s+(?:an?\s+)?(?:alternative|aif)|exchange\s+traded\s+fund|"
    r"\betf\b|fund\s+of\s+funds|overseas\s+etfs?|international\s+selection\s+fund",
    re.I,
)
_MONEY_MARKET_RE = re.compile(
    r"treasury\s+bills?|t[\s-]?bills?|commercial\s+paper|certificate\s+of\s+deposits?",
    re.I,
)
_DEBT_RE = re.compile(
    r"government\s+securit|g[\s-]?sec|\bsdl\b|state\s+development|non[\s-]?convertible|"
    r"\bbonds?\b|\bdebenture|\bncd\b|\bfrn\b|securitised|corporate\s+debt|debt\s+instrument|"
    r"zero\s+coupon|floating\s+rate|perpetual|tier\s+[-i1]|\bgoi\b",
    re.I,
)
_EQUITY_RE = re.compile(
    r"equity|listed\s*/?\s*awaiting|shares?\b|stock\s+exchange|preference|warrant|"
    r"rights?\s+entitlement|real\s+estate\s+investment|infrastructure\s+investment\s+trust|"
    r"\breits?\b|\binvits?\b|overseas\s+securit",
    re.I,
)
_RATING_RE = re.compile(
    r"^(?:(?:crisil|icra|care|india\s*ratings?|fitch|brickwork|acute|infomerics)[\s/\-]*)?"
    r"(sovereign|unrated|not\s*applicable|a1\+?|a2\+?|a3\+?|a4\+?|aaa|aa[+\-]?|a[+\-]?|"
    r"bbb[+\-]?|bb[+\-]?|b[+\-]?|ccc|d)(?:\s*/\s*[a-z0-9+\-]+)?$",
    re.I,
)
_COUPON_NAME_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        return n if math.isfinite(n) else None
    s = str(value).strip()
    if not s or s.lower() in _EMPTY:
        return None
    s = re.sub(r"[%$,₹]", "", s)
    s = re.sub(r"\bRs\.?\b", "", s, flags=re.I).replace(",", "").strip()
    if not s or s.lower() in _EMPTY:
        return None
    try:
        n = float(s)
        return n if math.isfinite(n) else None
    except ValueError:
        m = _NUM_RE.search(s)
        if not m:
            return None
        n = float(m.group(0))
        return n if math.isfinite(n) else None


def _round6(n: float | None) -> float | None:
    if n is None or not math.isfinite(n):
        return None
    return round(n * 1_000_000) / 1_000_000


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in _EMPTY:
        return None
    return s


def _looks_rating(value: Any) -> bool:
    s = _text(value)
    return bool(s and _RATING_RE.match(s))


def classify_holding_type(h: dict[str, Any] | None) -> str:
    h = h or {}
    name = str(h.get("instrument") or "")
    section = str(h.get("section") or "")
    industry = f"{h.get('industry') or ''} {h.get('industry_rating') or ''}"
    isin = str(h.get("isin") or "").strip().upper()
    blob = f"{name} {section} {industry}"

    if _DERIV_RE.search(name) or _DERIV_RE.search(section) or _DERIV_RE.search(industry):
        return "derivative"
    if _CASH_RE.search(name) or (_CASH_RE.search(section) and not isin and not _NOT_CASH_RE.search(name)):
        if not _NOT_CASH_RE.search(name):
            return "cash"
    if isin.startswith("INF") or _FUND_RE.search(section) or _FUND_RE.search(name) or re.fullmatch(r"LU[A-Z0-9]{10}", isin):
        return "fund_unit"
    if (_COMMODITY_RE.search(name) or re.search(r"^(?:[a-z]\)\s*)?(gold|silver)\b", section, re.I)) and not _NOT_COMMODITY_RE.search(name):
        return "commodity"
    if _MONEY_MARKET_RE.search(section) or _MONEY_MARKET_RE.search(name):
        return "money_market"
    if (
        _COUPON_NAME_RE.search(name)
        or _DEBT_RE.search(name)
        or re.match(r"^(IN0|IN3)", isin)
        or (_DEBT_RE.search(section) and not _EQUITY_RE.search(section))
    ):
        return "debt"
    if re.search(r"physical\s+commodit|commodities\s+exchange", section, re.I) or re.search(
        r"commodity", name, re.I
    ) or re.search(r"gold.*bar|silver.*bar|1\s*kg", name, re.I):
        return "commodity"
    if (
        _EQUITY_RE.search(section)
        or _EQUITY_RE.search(name)
        or isin.startswith("INE")
        or re.match(r"^(US|GB|KY|TW|KR|JP|HK|MU|IE|CA|AU|CH|DE|FR|NL|BM|SG)", isin)
        or re.search(r"unlisted|privately\s+placed", section, re.I)
        or re.search(r"\b(?:ltd|limited|plc|inc|corp|holdings)\b", name, re.I)
    ):
        return "equity"
    if not isin and _EQUITY_RE.search(blob):
        return "equity"
    return "other"


def _industry_and_rating(h: dict[str, Any]) -> tuple[str | None, str | None]:
    industry = _text(h.get("industry"))
    combined = _text(h.get("industry_rating"))
    rating = _text(h.get("rating"))
    if rating and industry:
        extra = combined if _looks_rating(combined) and combined.lower() != industry.lower() else rating
        return industry, extra
    if rating:
        return industry or (None if _looks_rating(combined) else combined), rating
    if combined and _looks_rating(combined) and not industry:
        return None, combined
    if combined and not industry:
        return (None if _looks_rating(combined) else combined), (combined if _looks_rating(combined) else None)
    if combined and industry and combined.lower() != industry.lower():
        return industry, (combined if _looks_rating(combined) else None)
    return industry, None


def _pick(keys: tuple[str, ...], extras: dict[str, Any]) -> dict[str, Any]:
    return {k: extras.get(k) for k in keys}


def shape_holdings_payload(scheme: dict[str, Any] | None, portfolio: Any) -> dict[str, Any]:
    scheme = scheme or {}
    if isinstance(portfolio, list):
        meta_in: dict[str, Any] = {}
        raw_holdings = portfolio
    else:
        portfolio = portfolio or {}
        meta_in = portfolio.get("meta") or {}
        raw_holdings = portfolio.get("holdings") or []

    raw_mvs = [parse_number(h.get("market_value")) for h in raw_holdings]
    max_abs = max((abs(n) for n in raw_mvs if n is not None), default=0.0)
    scale = 1e-5 if max_abs >= 1e8 else 1.0
    market_values = [_round6(None if n is None else n * scale) for n in raw_mvs]

    parsed_pcts = [parse_number(h.get("pct_nav")) for h in raw_holdings]
    present = [n for n in parsed_pcts if n is not None]
    total = sum(present)
    max_pct = max((abs(n) for n in present), default=0.0)
    fractional = bool(present) and max_pct <= 1.5 and 0.85 <= abs(total) <= 1.15
    total_mv = sum(v or 0.0 for v in market_values)

    pcts: list[float | None] = []
    for pct, mv in zip(parsed_pcts, market_values):
        n = pct * 100 if fractional and pct is not None else pct
        if (n is None or abs(n) < 1e-12) and mv is not None and abs(mv) > 0 and total_mv:
            n = (mv / total_mv) * 100
        pcts.append(_round6(n))

    holdings = []
    for h, mv, pct in zip(raw_holdings, market_values, pcts):
        industry, rating = _industry_and_rating(h)
        holdings.append(
            _pick(
                HOLDING_KEYS,
                {
                    "holding_type": classify_holding_type(h),
                    "instrument": _text(h.get("instrument")) or "",
                    "isin": _text(h.get("isin")),
                    "section": _text(h.get("section")),
                    "industry": industry,
                    "rating": rating,
                    "coupon": _round6(parse_number(h.get("coupon"))),
                    "maturity_date": _text(h.get("maturity_date")),
                    "quantity": _round6(parse_number(h.get("quantity"))),
                    "market_value": mv,
                    "pct_nav": pct,
                    "ytm": _round6(parse_number(h.get("ytm"))),
                    "ytc": _round6(parse_number(h.get("ytc"))),
                    "instrument_yield": _round6(parse_number(h.get("instrument_yield"))),
                    "listed_status": _text(h.get("listed_status")),
                    "underlying": _text(h.get("underlying")),
                    "position_side": _text(h.get("position_side")),
                },
            )
        )

    scheme_out = _pick(
        SCHEME_KEYS,
        {
            "amfi_code": _text(scheme.get("amfi_code")),
            "name": _text(scheme.get("name")),
            "amc_name": _text(scheme.get("amc_name")),
            "parent_name": _text(scheme.get("parent_name")),
            "parent_amfi": _text(scheme.get("parent_amfi")),
            "nav": _round6(parse_number(scheme.get("nav"))),
            "nav_date": _text(scheme.get("nav_date")),
            "isin": _text(scheme.get("isin")),
            "category": _text(scheme.get("category")),
        },
    )
    meta = _pick(
        META_KEYS,
        {
            "as_of": _text(meta_in.get("as_of")) or _text(scheme.get("as_of")),
            "disclosure_type": _text(meta_in.get("disclosure_type")),
            "period": _text(meta_in.get("period")),
            "disclosure_shortcode": _text(scheme.get("shortcode")) or _text(meta_in.get("shortcode")),
            "holding_count": len(holdings),
            "source_file": _text(meta_in.get("source_file")) or _text(scheme.get("source_file")),
            "market_value_unit": MARKET_VALUE_UNIT,
            "pct_nav_unit": PCT_NAV_UNIT,
        },
    )
    return {
        "amfi_code": scheme_out["amfi_code"],
        "scheme": scheme_out,
        "meta": meta,
        "holdings": holdings,
    }
