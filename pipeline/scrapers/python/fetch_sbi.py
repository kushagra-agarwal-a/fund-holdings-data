#!/usr/bin/env python3
"""
SBI Mutual Fund — download **monthly portfolio** `.xlsx` for given YYYY-MM.

The public portfolios page loads rows via Sitefinity-style AJAX:

  POST https://www.sbimf.com/ajaxcall/CMS/GetMonthsByYear
  Body (JSON): {"folder": "Scheme Portfolios", "year": <int>}
  → list of English month names with data (e.g. ["January","February",...])

  POST https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets
  Body (JSON):
    {"FundId": 0, "PSYear": "<YYYY>", "PSMonth": "<English month>",
     "PSFrequency": "Monthly"}
  → HTML `<tr>` rows with `href="https://www.sbimf.com/docs/.../*.xlsx?sfvrsn=..."`

`FundId: 0` matches the website default (all schemes for that month).

Scope:
  • `all` — every workbook including **All Schemes Monthly Portfolio** (consolidated)
  • `consolidated` — only the combined “all schemes” file
  • `per-scheme` — all scheme files, excluding the consolidated row
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_module
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE = "https://www.sbimf.com"
PAGE_REF = f"{BASE}/portfolios"
MONTHS_API = f"{BASE}/ajaxcall/CMS/GetMonthsByYear"
SHEETS_API = f"{BASE}/ajaxcall/CMS/GetSchemePortfolioSheets"
FOLDER = "Scheme Portfolios"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Origin": BASE,
    "Referer": PAGE_REF,
}

MONTH_NAMES_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# <a href="https://...xlsx?...">Title</a> (first link in each row)
LINK_RE = re.compile(
    r'<a\s+href="(https://www\.sbimf\.com/docs[^"]+\.xlsx[^"]*)"[^>]*>([^<]*)</a>',
    re.I,
)


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def month_key_to_en_month(month_key: str) -> tuple[str, str]:
    """Return (YYYY, EnglishMonthName)."""
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = parts[0], parts[1].zfill(2)
    if len(y) != 4 or not y.isdigit():
        raise ValueError(f"Bad year in {month_key!r}")
    mi = int(m)
    if not (1 <= mi <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, MONTH_NAMES_EN[mi - 1]


def post_json(url: str, payload: dict, *, ctx: ssl.SSLContext) -> str:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            **HEADERS,
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json, text/html, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_months_for_year(year: int, *, ctx: ssl.SSLContext) -> list[str]:
    raw = post_json(MONTHS_API, {"folder": FOLDER, "year": year}, ctx=ctx)
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


def fetch_sheets_html(year: str, month_en: str, *, ctx: ssl.SSLContext) -> str:
    payload = {
        "FundId": 0,
        "PSYear": year,
        "PSMonth": month_en,
        "PSFrequency": "Monthly",
    }
    return post_json(SHEETS_API, payload, ctx=ctx)


def parse_links(html: str) -> list[tuple[str, str]]:
    """Deduped (url, title) preserving first-seen order."""
    seen: dict[str, str] = {}
    for m in LINK_RE.finditer(html):
        url = m.group(1).strip()
        title = html_module.unescape(m.group(2).strip())
        if url not in seen:
            seen[url] = title or _title_from_url(url)
    return list(seen.items())


def _title_from_url(url: str) -> str:
    path = urlparse(url).path
    base = unquote(path.rsplit("/", 1)[-1])
    return base.replace(".xlsx", "").replace("-", " ")[:120]


def is_consolidated(url: str, title: str) -> bool:
    u = url.lower()
    t = title.lower()
    return "all-schemes-monthly-portfolio" in u or "all schemes monthly" in t


def filter_by_scope(
    rows: list[tuple[str, str]],
    scope: str,
) -> list[tuple[str, str]]:
    if scope == "all":
        return rows
    out: list[tuple[str, str]] = []
    for url, title in rows:
        c = is_consolidated(url, title)
        if scope == "consolidated" and c:
            out.append((url, title))
        elif scope == "per-scheme" and not c:
            out.append((url, title))
    return out


def safe_filename(url: str, fallback_title: str) -> str:
    path = urlparse(url).path
    base = unquote(path.rsplit("/", 1)[-1].split("?")[0])
    if not base or not base.lower().endswith(".xlsx"):
        stem = re.sub(r"[^\w.\-]+", "_", fallback_title)[:80]
        base = f"{stem}.xlsx" if stem else "download.xlsx"
    base = re.sub(r"[^\w.\-()]+", "_", base).strip("._")[:200]
    return base or "download.xlsx"


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SBI MF monthly portfolio xlsx files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--scope",
        choices=("all", "consolidated", "per-scheme"),
        default="all",
        help="all: consolidated + each scheme; consolidated: combined workbook only; per-scheme: exclude combined",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max files per month (0 = no cap)",
    )
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "sbi-mutual-fund"

    by_month: dict[str, list[tuple[str, str]]] = {k: [] for k in args.months}

    for mk in args.months:
        year_s, month_en = month_key_to_en_month(mk)
        year_i = int(year_s)
        print(f"\n{mk} ({month_en} {year_s}) …", flush=True)
        try:
            avail = fetch_months_for_year(year_i, ctx=ctx)
        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as e:
            if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
                raise SystemExit(
                    f"{e}\n\nRetry with:  python3 scripts/fetch_sbi.py ... --insecure-ssl"
                ) from e
            print(f"  ERR months API: {e}", flush=True)
            continue
        if month_en not in avail:
            print(f"  No portfolio data for this month (available: {avail})", flush=True)
            continue
        try:
            html = fetch_sheets_html(year_s, month_en, ctx=ctx)
        except urllib.error.URLError as e:
            print(f"  ERR sheets API: {e}", flush=True)
            continue
        rows = parse_links(html)
        rows = filter_by_scope(rows, args.scope)
        if args.limit and len(rows) > args.limit:
            rows = rows[: args.limit]
            print(f"  Limited to {args.limit} file(s)", flush=True)
        print(f"  {len(rows)} file(s) after --scope {args.scope!r}", flush=True)
        by_month[mk] = rows

    for mk in args.months:
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = by_month.get(mk) or []
        print(f"\n{mk}: downloading {len(batch)} file(s)", flush=True)
        manifest: list[dict] = []
        for url, title in batch:
            fn = safe_filename(url, title)
            rec = {
                "month": mk,
                "title": title,
                "scope": "consolidated" if is_consolidated(url, title) else "per_scheme",
                "download_url": url,
                "saved_as": fn,
            }
            try:
                body = download(url, ctx=ctx)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fn).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  OK {fn} ({len(body)} bytes)", flush=True)
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  ERR {fn}: {e}", flush=True)

        man_path = out_dir / "manifest.json"
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {man_path}", flush=True)


if __name__ == "__main__":
    main()
