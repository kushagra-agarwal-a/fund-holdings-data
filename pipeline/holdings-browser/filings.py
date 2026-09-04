"""Filing dates, previous/next links, and dated local/B2 paths."""
from __future__ import annotations

import calendar
import re
from typing import Any

NO_DATA_FOUND = "No Data Found"
LINK_KEYS = ("as_of", "href", "message")

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_FORTNIGHTLY_RE = re.compile(
    r"\b(debt|liquid|overnight|money\s*market|gilt|credit\s*risk|floater|"
    r"ultra\s*short|ultrashort|low\s*duration|short\s*duration|medium\s*duration|"
    r"corporate\s*bond|banking\s*(?:and|&)\s*psu|dynamic\s*bond)\b",
    re.I,
)


def format_date(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def normalize_as_of(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo < 1 or mo > 12:
            return ""
        dim = last_day_of_month(y, mo)
        if d < 1 or d > dim:
            return ""
        return format_date(y, mo, d)
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if mo < 1 or mo > 12:
            return ""
        return format_date(y, mo, last_day_of_month(y, mo))
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo < 1 or mo > 12:
            return ""
        dim = last_day_of_month(y, mo)
        if d < 1 or d > dim:
            return ""
        return format_date(y, mo, d)
    m = re.match(r"^(\d{1,2})[./\s-]+([A-Za-z]+)[./\s,-]+(\d{4})$", s)
    if m:
        mo = _MONTHS.get(m.group(2).lower())
        if not mo:
            return ""
        d, y = int(m.group(1)), int(m.group(3))
        dim = last_day_of_month(y, mo)
        if d < 1 or d > dim:
            return ""
        return format_date(y, mo, d)
    return ""


def is_fortnightly(scheme: dict[str, Any] | None, meta: dict[str, Any] | None = None) -> bool:
    scheme = scheme or {}
    meta = meta or {}
    dtype = str(meta.get("disclosure_type") or scheme.get("disclosure_type") or "").lower()
    if dtype == "fortnightly":
        return True
    if dtype == "monthly":
        return False
    blob = f"{scheme.get('category') or ''} {scheme.get('name') or ''}"
    return bool(_FORTNIGHTLY_RE.search(blob))


def previous_filing_date(as_of: str, fortnightly: bool) -> str:
    y, m, d = (int(x) for x in as_of.split("-"))
    if fortnightly:
        if d > 15:
            return format_date(y, m, 15)
        if m == 1:
            return format_date(y - 1, 12, last_day_of_month(y - 1, 12))
        return format_date(y, m - 1, last_day_of_month(y, m - 1))
    if m == 1:
        return format_date(y - 1, 12, last_day_of_month(y - 1, 12))
    return format_date(y, m - 1, last_day_of_month(y, m - 1))


def next_filing_date(as_of: str, fortnightly: bool) -> str:
    y, m, d = (int(x) for x in as_of.split("-"))
    eom = last_day_of_month(y, m)
    if fortnightly:
        if d < 15:
            return format_date(y, m, 15)
        if d < eom:
            return format_date(y, m, eom)
        if m == 12:
            return format_date(y + 1, 1, 15)
        return format_date(y, m + 1, 15)
    if d < eom:
        return format_date(y, m, eom)
    if m == 12:
        return format_date(y + 1, 1, last_day_of_month(y + 1, 1))
    return format_date(y, m + 1, last_day_of_month(y, m + 1))


def dated_b2_key(latest_key: str, as_of: str) -> str:
    if not latest_key or not as_of:
        return ""
    if "/holdings/latest/" in latest_key:
        return latest_key.replace("/holdings/latest/", f"/holdings/{as_of}/")
    return re.sub(r"/holdings/\d{4}-\d{2}-\d{2}/", f"/holdings/{as_of}/", latest_key)


def amfi_href(origin: str, code: str, as_of: str) -> str:
    return f"{origin.rstrip('/')}/api/amfi/{code}?as_of={as_of}"


def _link(as_of: str, href: str, available: bool) -> dict[str, Any]:
    return {"as_of": as_of, "href": href, "message": None if available else NO_DATA_FOUND}


def filing_links(
    *,
    origin: str,
    code: str,
    as_of: str,
    previous_as_of: str,
    next_as_of: str,
    previous_available: bool,
    next_available: bool,
) -> dict[str, Any]:
    return {
        "self": _link(as_of, amfi_href(origin, code, as_of), True),
        "previous": _link(previous_as_of, amfi_href(origin, code, previous_as_of), previous_available),
        "next": _link(next_as_of, amfi_href(origin, code, next_as_of), next_available),
    }


def no_data_payload(
    scheme: dict[str, Any] | None,
    as_of: str | None,
    links: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scheme = scheme or {}
    return {
        "error": NO_DATA_FOUND,
        "amfi_code": scheme.get("amfi_code"),
        "as_of": as_of,
        "scheme": {
            "amfi_code": scheme.get("amfi_code"),
            "name": scheme.get("name"),
            "amc_name": scheme.get("amc_name"),
            "parent_name": scheme.get("parent_name"),
        },
        "links": links,
    }


def dated_local_candidates(rel: str, as_of: str) -> list[str]:
    """Swap the parsed period folder for another as-of date."""
    parts = rel.replace("\\", "/").split("/")
    if len(parts) < 6 or parts[0] != "data" or parts[1] != "parsed":
        return []
    y, m, d = (int(x) for x in as_of.split("-"))
    eom = last_day_of_month(y, m)
    periods: list[tuple[str, str]] = []
    if d == 15:
        periods.append(("fortnightly", f"{y:04d}-{m:02d}-15"))
    if d == eom:
        periods.append(("monthly", f"{y:04d}-{m:02d}"))
        periods.append(("fortnightly", f"{y:04d}-{m:02d}"))
    periods.append((parts[2], as_of))
    out: list[str] = []
    seen: set[str] = set()
    rest = "/".join(parts[4:])
    for dtype, period in periods:
        cand = f"data/parsed/{dtype}/{period}/{rest}"
        if cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out
