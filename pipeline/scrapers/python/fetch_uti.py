#!/usr/bin/env python3
"""
UTI Mutual Fund — download consolidated monthly portfolio files for YYYY-MM.

Public page:
  https://www.utimf.com/downloads/consolidate-all-portfolio-disclosure

API used by the page:
  GET https://www.utimf.com/api/get-consolidate-portfolio-disclosure?year=<YYYY>&month=<month-name>

Rows include:
  - name
  - month
  - year
  - url/doc (download URL, typically CloudFront)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse

BASE = "https://www.utimf.com"
PAGE_URL = f"{BASE}/downloads/consolidate-all-portfolio-disclosure"
API_URL = f"{BASE}/api/get-consolidate-portfolio-disclosure"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
MONTH_NUM_TO_NAME = {v: k for k, v in MONTHS.items()}


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
    s = (name or "").strip() or "uti_scheme_disclosure.xlsx"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "uti_scheme_disclosure.xlsx"


def path_to_download_url(url: str) -> str:
    p = urlparse(url)
    # Preserve existing percent-encoding (e.g. "%20") while encoding raw spaces.
    safe_path = "/".join(quote(seg, safe="%") for seg in p.path.split("/"))
    return urlunparse((p.scheme, p.netloc, safe_path, p.params, p.query, p.fragment))


def fetch_month_rows(year: int, month_name: str, *, ctx: ssl.SSLContext) -> list[dict]:
    url = f"{API_URL}?year={year}&month={quote(month_name)}"
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        obj = json.loads(resp.read().decode("utf-8", errors="ignore"))
    rows = obj.get("rows") or []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch UTI consolidated monthly portfolio files")
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
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "uti-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    for ym, mk in targets.items():
        year, month_num = ym
        month_name = MONTH_NUM_TO_NAME[month_num]
        query_url = f"{API_URL}?year={year}&month={month_name}"
        print(f"GET {query_url}", flush=True)
        try:
            selected = fetch_month_rows(year, month_name, ctx=ctx)
        except urllib.error.URLError as e:
            if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
                raise SystemExit(
                    f"{e}\n\nRetry with:  python3 scripts/fetch_uti.py ... --insecure-ssl"
                ) from e
            raise

        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()
        manifest: list[dict] = []
        print(f"\n{mk}: {len(selected)} file(s)", flush=True)
        if not selected:
            print("  No consolidated portfolio row found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = str(row.get("name") or "").strip()
            raw_url = str(row.get("url") or row.get("doc") or "").strip()
            category = str(row.get("category") or "").strip()
            api_month = str(row.get("month") or "").strip()
            api_year = str(row.get("year") or "").strip()
            url = path_to_download_url(raw_url)
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"uti_consolidated_{mk}.zip")
            rec = {
                "month": mk,
                "title": title,
                "category": category,
                "api_month": api_month,
                "api_year": api_year,
                "query_url": query_url,
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

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
