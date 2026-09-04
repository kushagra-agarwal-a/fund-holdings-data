#!/usr/bin/env python3
"""
Canara Robeco Mutual Fund — download monthly or fortnightly-debt portfolio files.

No BeautifulSoup. Listing pages are HTML; Excel URLs are extracted with regex:

  https://www.canararobeco.com/wp-content/uploads/....xls(x)

Monthly (scheme dashboard, ~10 files/page):
  .../scheme-dashboard/scheme-monthly-portfolio/
    ?filteryear=YYYY&filtermonth=MM&pagination=N

Fortnightly debt (same query shape; --fortnightly):
  .../fortnightly-portfolio-disclosure-debt/
    ?filteryear=YYYY&filtermonth=MM&pagination=N

TLS: plain urllib often gets 403; prefer curl_cffi chrome impersonation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from asof_filter import file_matches_storage_key, parse_as_of  # noqa: E402

BASE_URL = "https://www.canararobeco.com"
PAGE_URL_MONTHLY = (
    f"{BASE_URL}/documents/statutory-disclosures/scheme-dashboard/"
    "scheme-monthly-portfolio/"
)
PAGE_URL_FORTNIGHTLY = (
    f"{BASE_URL}/documents/statutory-disclosures/"
    "fortnightly-portfolio-disclosure-debt/"
)

UPLOAD_XLSX_RE = re.compile(
    r"https?://(?:www\.)?canararobeco\.com/wp-content/uploads/"
    r"[^\"'\s<>]+\.xlsx?",
    re.I,
)
# Relative hrefs in case the host is omitted
UPLOAD_XLSX_REL_RE = re.compile(
    r"(?:https?:)?//(?:www\.)?canararobeco\.com/wp-content/uploads/"
    r"[^\"'\s<>]+\.xlsx?"
    r"|/wp-content/uploads/[^\"'\s<>]+\.xlsx?",
    re.I,
)
# HTML often uses &#038; instead of & before query params — don't require [?&].
PAGINATION_RE = re.compile(r"pagination=(\d+)", re.I)
MONTHS_BY_YEAR_RE = re.compile(
    r"const\s+monthsByYear\s*=\s*(\{)",
    re.I,
)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}


def encode_url_for_http(url: str) -> str:
    """IRI → ASCII URI (Canara paths often contain en-dashes)."""
    p = urlparse(url.strip())
    path = quote(p.path, safe="/%")
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, p.fragment))


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = unquote(path.rsplit("/", 1)[-1].split("?")[0])
    base = base.replace("\u2013", "-").replace("\u2014", "-")
    if not base or base in (".", ".."):
        base = "download.xlsx"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.xlsx"


def _http_get_text(url: str, *, referer: str) -> str:
    try:
        from curl_cffi import requests as creq  # type: ignore

        r = creq.get(
            url,
            headers={**HEADERS, "Referer": referer},
            impersonate="chrome124",
            timeout=60,
        )
        r.raise_for_status()
        return r.text
    except Exception:
        pass
    import urllib.request

    req = urllib.request.Request(
        url, headers={**HEADERS, "Referer": referer}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "ignore")


def _http_get_bytes(url: str, *, referer: str) -> bytes:
    safe = encode_url_for_http(url)
    try:
        from curl_cffi import requests as creq  # type: ignore

        r = creq.get(
            safe,
            headers={**HEADERS, "Referer": referer, "Accept": "*/*"},
            impersonate="chrome124",
            timeout=120,
        )
        r.raise_for_status()
        return r.content
    except Exception:
        pass
    import urllib.request

    req = urllib.request.Request(
        safe,
        headers={**HEADERS, "Referer": referer, "Accept": "*/*"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _extract_balanced_object(html: str, start: int) -> str | None:
    depth = 0
    i = start
    n = len(html)
    while i < n:
        c = html[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
        i += 1
    return None


def fetch_months_by_year(page_url: str) -> dict[str, list[str]]:
    """Optional discovery of available year/months from embedded JS."""
    html = _http_get_text(page_url, referer=page_url)
    m = MONTHS_BY_YEAR_RE.search(html)
    if not m:
        print("  ⚠ monthsByYear not found — will fetch requested months directly", flush=True)
        return {}
    raw = _extract_balanced_object(html, m.start(1))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ⚠ monthsByYear JSON parse failed ({e})", flush=True)
        return {}
    result: dict[str, list[str]] = {}
    for year, months_dict in data.items():
        if not isinstance(months_dict, dict):
            continue
        keys = [str(k).zfill(2) for k in months_dict.keys()]
        result[str(year)] = sorted(keys, reverse=True)
    total = sum(len(v) for v in result.values())
    print(f"  ✓ monthsByYear: {len(result)} years, {total} year-month combos", flush=True)
    return result


def extract_xlsx_urls(html: str) -> list[str]:
    """Pull Canara wp-content upload links ending in .xls / .xlsx."""
    found: list[str] = []
    seen: set[str] = set()
    for m in UPLOAD_XLSX_REL_RE.finditer(html):
        raw = m.group(0).strip()
        if raw.startswith("//"):
            url = "https:" + raw
        elif raw.startswith("/"):
            url = urljoin(BASE_URL, raw)
        else:
            url = raw
        # normalize host
        if "canararobeco.com/wp-content/uploads/" not in url.lower():
            continue
        if url in seen:
            continue
        seen.add(url)
        found.append(url)
    # also absolute matches (dedupe)
    for m in UPLOAD_XLSX_RE.finditer(html):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def max_pagination(html: str) -> int:
    nums = [int(x) for x in PAGINATION_RE.findall(html)]
    return max(nums) if nums else 1


def fetch_xlsx_links_for_month(
    year: str,
    month: str,
    *,
    page_url: str,
) -> list[str]:
    """Paginate listing pages; return unique xlsx/xls URLs."""
    mm = month.zfill(2)
    referer = f"{page_url}?{urlencode({'filteryear': year, 'filtermonth': mm})}"
    first_qs = urlencode(
        {"filteryear": year, "filtermonth": mm, "pagination": 1}
    )
    first_url = f"{page_url}?{first_qs}"
    html = _http_get_text(first_url, referer=referer)
    max_page = max(max_pagination(html), 1)
    urls = extract_xlsx_urls(html)

    # Walk declared pages, then keep going until a page yields no new xlsx
    # (guards against incomplete pagination markup).
    page = 2
    empty_streak = 0
    while page <= max(max_page, page) and page <= 30:
        qs = urlencode(
            {"filteryear": year, "filtermonth": mm, "pagination": page}
        )
        page_html = _http_get_text(f"{page_url}?{qs}", referer=referer)
        max_page = max(max_page, max_pagination(page_html))
        batch = extract_xlsx_urls(page_html)
        new = [u for u in batch if u not in set(urls)]
        if not new:
            empty_streak += 1
            if page > max_page or empty_streak >= 2:
                break
        else:
            empty_streak = 0
            urls.extend(new)
        page += 1

    # stable unique
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def month_key_to_parts(month_key: str) -> tuple[str, str]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, mm = parts[0], parts[1].zfill(2)
    if len(y) != 4 or not y.isdigit():
        raise ValueError(f"Bad year in {month_key!r}")
    if not (mm.isdigit() and 1 <= int(mm) <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, mm


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fetch Canara Robeco portfolio Excel files (regex listing, no bs4)",
    )
    ap.add_argument(
        "--months",
        nargs="+",
        default=["2026-01", "2026-02"],
        help="Calendar months as YYYY-MM",
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Staging root (python_ref passes data/staging/python)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max files per month (0 = all)",
    )
    ap.add_argument(
        "--skip-unavailable",
        action="store_true",
        help="If monthsByYear is present and omits the month, skip instead of fetching.",
    )
    ap.add_argument(
        "--fortnightly",
        action="store_true",
        help="Use debt fortnightly listing page instead of scheme-monthly.",
    )
    ap.add_argument(
        "--as-of",
        default="",
        help="Calendar as-of YYYY-MM-DD (filters fortnightly filenames to mid vs month-end)",
    )
    args = ap.parse_args()

    page_url = PAGE_URL_FORTNIGHTLY if args.fortnightly else PAGE_URL_MONTHLY
    kind = "fortnightly-debt" if args.fortnightly else "monthly"
    amc_dir = args.root / "amcs" / "canara-robeco-mutual-fund"

    print(f"Fetching {kind} base page for monthsByYear …", flush=True)
    months_by_year = fetch_months_by_year(page_url)

    for month_key in args.months:
        year, month = month_key_to_parts(month_key)
        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)

        if months_by_year:
            avail = months_by_year.get(year) or []
            if month not in avail and args.skip_unavailable:
                print(
                    f"\n{month_key}: SKIP — not in monthsByYear ({avail})",
                    flush=True,
                )
                (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
                continue

        print(f"\n{month_key}: GET filtered {kind} pages (regex xlsx) …", flush=True)
        urls = fetch_xlsx_links_for_month(year, month, page_url=page_url)
        as_of = args.as_of.strip()
        if args.fortnightly and not as_of:
            as_of = f"{year}-{month}-15"
        cadence = "fortnightly" if args.fortnightly else "monthly"
        if as_of and parse_as_of(as_of):
            before = len(urls)
            urls = [
                u
                for u in urls
                if file_matches_storage_key(
                    safe_filename(u), u, as_of, cadence
                )
            ]
            if before != len(urls):
                print(
                    f"  … as-of {as_of}: kept {len(urls)}/{before} link(s)",
                    flush=True,
                )
        if args.limit > 0:
            urls = urls[: args.limit]

        print(f"  … {len(urls)} file link(s)", flush=True)
        manifest: list[dict] = []

        for i, file_url in enumerate(urls, 1):
            fname = safe_filename(file_url)
            label = unquote(urlparse(file_url).path.rsplit("/", 1)[-1])
            rec = {
                "month": month_key,
                "download_url": file_url,
                "saved_as": fname,
                "label": label,
                "source": kind,
            }
            if args.dry_run:
                print(f"  [{i}] {fname}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = _http_get_bytes(file_url, referer=page_url)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fname).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  [{i}] OK {fname} ({len(body)} bytes)", flush=True)
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  [{i}] ERR {fname}: {e}", flush=True)

        man_path = out_dir / "manifest.json"
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {man_path}", flush=True)


if __name__ == "__main__":
    main()
