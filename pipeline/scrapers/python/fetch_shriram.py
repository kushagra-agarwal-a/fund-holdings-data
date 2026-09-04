#!/usr/bin/env python3
"""
Shriram Mutual Fund — download **combined monthly portfolio** `.xls` for given YYYY-MM.

The statutory disclosures page (Next.js RSC) embeds CDN links like:

  https://cdn.shriramamc.in/uploads/Statutory-disclosure/
    Monthly--Fortnightly--Weekly-Portfolio-of-Scheme(s)/Monthly-Portfolio-for-the-Financial-Year/
    2025-2026/Monthly-Portfolio-Shriram-Mutual-Fund-February-2026.xls

Filenames use `Monthly-Portfolio-Shriram-Mutual-Fund-<Month>-<YYYY>.xls` where `<Month>` may be
full (`February`) or abbreviated (`Jan`). Only this pattern is kept (other `.xls` on the page
are different disclosures).

If both old and new path prefixes exist for the same month, the URL under
`Monthly--Fortnightly--Weekly-Portfolio-of-Scheme(s)` is preferred.
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
from urllib.parse import unquote, urlparse

PAGE_URL = "https://www.shriramamc.in/investor-statutory-disclosures"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
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

MONTH_ABBR = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# Do not use `[^...\\s...]` with a broken escape — excluding literal `s` breaks `https://...shriram...`.
CDN_XLS_RE = re.compile(
    r'https://cdn\.shriramamc\.in[^\s"<>]+\.xls',
    re.I,
)

# Some files use a trailing hyphen before `.xls`, e.g. `...-Nov-2025-.xls`.
FNAME_RE = re.compile(
    r"Monthly-Portfolio-Shriram-Mutual-Fund-([A-Za-z]+)-(\d{4})-?\.xls",
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


def get_html(*, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def filename_to_year_month(basename: str) -> tuple[int, int] | None:
    m = FNAME_RE.match(basename)
    if not m:
        return None
    token, y_s = m.group(1).strip(), int(m.group(2))
    for i, name in enumerate(MONTH_NAMES_EN, 1):
        if token.lower() == name.lower():
            return y_s, i
    mi = MONTH_ABBR.get(token.lower())
    if mi:
        return y_s, mi
    return None


def url_preference_score(url: str) -> int:
    if "Monthly--Fortnightly--Weekly-Portfolio" in url:
        return 2
    if "Monthly-Fortnightly" in url and "Monthly--" not in url:
        return 1
    return 0


def parse_monthly_portfolio_index(html: str) -> dict[tuple[int, int], tuple[str, str]]:
    """(year, month) -> (url, basename). Best-scoring URL wins."""
    best: dict[tuple[int, int], tuple[int, str, str]] = {}
    for url in set(CDN_XLS_RE.findall(html)):
        path = urlparse(url).path
        base = unquote(path.rsplit("/", 1)[-1])
        ym = filename_to_year_month(base)
        if ym is None:
            continue
        score = url_preference_score(url)
        prev = best.get(ym)
        if prev is None or score > prev[0]:
            best[ym] = (score, url, base)
    return {k: (v[1], v[2]) for k, v in best.items()}


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def month_key_to_ym(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Shriram MF combined monthly portfolio xls",
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
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "shriram-mutual-fund"

    print(f"GET {PAGE_URL} …", flush=True)
    try:
        html = get_html(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_shriram.py ... --insecure-ssl"
            ) from e
        raise

    index = parse_monthly_portfolio_index(html)
    print(f"  Indexed {len(index)} month(s) (Shriram combined monthly portfolio)", flush=True)

    for mk in args.months:
        y, mon = month_key_to_ym(mk)
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        row = index.get((y, mon))
        manifest: list[dict] = []
        print(f"\n{mk}:", flush=True)
        if not row:
            print(
                "  No Monthly-Portfolio-Shriram-Mutual-Fund-* row for this month "
                "(not published yet or filename pattern changed).",
                flush=True,
            )
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue
        url, base = row
        fn = re.sub(r"[^\w.\-]+", "_", base).strip("._")[:200] or "shriram_monthly.xls"
        rec = {
            "month": mk,
            "kind": "combined_monthly_portfolio",
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

        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
