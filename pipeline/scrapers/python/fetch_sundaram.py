#!/usr/bin/env python3
"""
Sundaram Mutual Fund - download monthly portfolio files for YYYY-MM.

Source page:
  https://www.sundarammutual.com/monthly-fortnightly-adhoc-portfolios

Data source:
  ASP.NET AJAX endpoint discovered from the page HTML:
  /ajax/Modules_Disclosure_Monthly_Fortnightly_Adhoc_Portfolios,App_Web_*.ashx
  with _method=GetCategory and Catid=Monthly.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

BASE = "https://www.sundarammutual.com"
PAGE_URL = f"{BASE}/monthly-fortnightly-adhoc-portfolios"
ASHX_RE = re.compile(
    r"(Modules_Disclosure_Monthly_Fortnightly_Adhoc_Portfolios,App_Web_[A-Za-z0-9]+\.ashx)",
    re.I,
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
MONTHS = {
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
TITLE_YM_RE = re.compile(
    r"(?:monthly|fortnightly)\s+portfolio(?:\s+of\s+debt\s+schemes)?(?:\s+disclosure)?.*?[-–]\s*"
    r"(?:\d{1,2}\s+)?"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{4})",
    re.I,
)
ITEM_RE = re.compile(
    r"<a\s+href='([^']+)'\s+target='_blank'[^>]*>.*?</i>\s*([^<]+)\s*</a>",
    re.I | re.S,
)


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def month_key_to_ym(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def safe_filename(name: str) -> str:
    s = (name or "").strip() or "sundaram_monthly_portfolio.xlsx"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "sundaram_monthly_portfolio.xlsx"


def fetch_text(url: str, *, ctx: ssl.SSLContext, data: bytes | None = None) -> str:
    req = urllib.request.Request(url, headers=HEADERS, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def discover_ajax_url(*, ctx: ssl.SSLContext) -> str:
    page = fetch_text(PAGE_URL, ctx=ctx)
    m = ASHX_RE.search(page)
    if not m:
        raise RuntimeError("could not discover Sundaram App_Web_*.ashx from page HTML")
    return f"{BASE}/ajax/{m.group(1)}?_method=GetCategory&_session=no"


def fetch_category_html(*, ctx: ssl.SSLContext, ajax_url: str, catid: str) -> str:
    payload = urllib.parse.urlencode({"Catid": catid}).encode("utf-8")
    text = fetch_text(ajax_url, ctx=ctx, data=payload)
    # endpoint returns a quoted JS string containing HTML
    return ast.literal_eval(text)


def parse_month_from_title(title: str, *, fortnightly: bool) -> tuple[int, int] | None:
    m = TITLE_YM_RE.search(title or "")
    if not m:
        return None
    blob = (title or "").lower()
    if fortnightly:
        if "fortnightly" not in blob:
            return None
    elif "fortnightly" in blob and "monthly" not in blob:
        return None
    month = MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return int(m.group(2)), month


def extract_rows(html_text: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for href, title in ITEM_RE.findall(html_text):
        url = urljoin(BASE, href.strip())
        title = " ".join(title.split())
        key = f"{url}|{title}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"title": title, "url": url})
    return rows


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Sundaram monthly portfolio files"
    )
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Fetch Catid=Fortnightly debt portfolios instead of Monthly",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "sundaram-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}
    catid = "Fortnightly" if args.fortnightly else "Monthly"

    print(f"Discover AJAX from {PAGE_URL}", flush=True)
    try:
        ajax_url = discover_ajax_url(ctx=ctx)
        print(f"POST {ajax_url} (Catid={catid})", flush=True)
        html_text = fetch_category_html(ctx=ctx, ajax_url=ajax_url, catid=catid)
        rows = extract_rows(html_text)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_sundaram.py ... --insecure-ssl"
            ) from e
        raise
    print(f"  Indexed {len(rows)} row(s)", flush=True)

    by_month: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        ym = parse_month_from_title(row["title"], fortnightly=args.fortnightly)
        if ym is None or ym not in targets:
            continue
        by_month.setdefault(ym, []).append(row)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()

        selected = by_month.get(ym, [])
        manifest: list[dict] = []
        print(f"\n{mk}: {len(selected)} file(s)", flush=True)
        if not selected:
            print("  No portfolio disclosure file found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = row["title"]
            url = row["url"]
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"sundaram_monthly_portfolio_{mk}.xlsx")
            rec = {
                "month": mk,
                "title": title,
                "source_page": PAGE_URL,
                "ajax_url": ajax_url,
                "download_url": url,
                "saved_as": fn,
            }
            if args.dry_run:
                print(f"  dry-run {fn}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(url, ctx=ctx)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fn).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  OK {fn} ({len(body)} bytes)", flush=True)
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  ERR {fn}: {e}", flush=True)

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
