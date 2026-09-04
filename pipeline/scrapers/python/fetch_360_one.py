#!/usr/bin/env python3
"""
360 ONE Mutual Fund — download consolidated monthly portfolio packs for YYYY-MM.

Live source (AMC-direct):
  https://www.360.one/asset/mutual-funds/downloads/

The downloads hub embeds JSON with Disclosures → Monthly Portfolio → yearly
packs (e.g. fileName "July", fileUrl …/IN_MF_MONTHLY_PORTFOLIO_July2026_….xls).

Note: archive.iiflmf.com (old IIFL disclosures page) is often down (HTTP 522)
and is no longer used as the primary source.

Each pack is a multi-sheet workbook (includes Flexi Cap, Focused, Liquid, etc.).
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

PAGE_URL = "https://www.360.one/asset/mutual-funds/downloads/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MONTH_NAME_TO_NUM = {
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
MONTH_NUM_TO_NAME = {v: k.capitalize() for k, v in MONTH_NAME_TO_NUM.items()}

# Structured embed: year bucket + month fileName + S3 url
YEAR_BLOCK_RE = re.compile(
    r'"year"\s*:\s*"Monthly Portfolio (20\d{2})"(.*?)(?="year"\s*:\s*"Monthly Portfolio |\Z)',
    re.I | re.S,
)
DOC_RE = re.compile(
    r'\{"fileName"\s*:\s*"(January|February|March|April|May|June|July|August|September|October|November|December)"'
    r'\s*,\s*"fileUrl"\s*:\s*"(https://[^"]+\.xls[x]?)"\}',
    re.I,
)
# Filename fallback: IN_MF_MONTHLY_PORTFOLIO_July2026_….xls / June2026_Final_….xls
URL_YM_RE = re.compile(
    r"(?:IN_MF_MONTHLY_PORTFOLIO|MONTHLY_PORTFOLIO)[_-]?"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"[_-]?(\d{4}|\d{2})",
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


def month_key_to_ym(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def safe_filename(name: str) -> str:
    s = (name or "").strip() or "360one_monthly_portfolio.xls"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "360one_monthly_portfolio.xls"


def fetch_html(*, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _normalize_page(html: str) -> str:
    return html.replace("\\/", "/").replace('\\"', '"')


def _ym_from_url(url: str) -> tuple[int, int] | None:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    m = URL_YM_RE.search(name)
    if not m:
        return None
    mon_tok = m.group(1).lower()
    yy = m.group(2)
    mon = None
    for key, num in MONTH_NAME_TO_NUM.items():
        if mon_tok.startswith(key[:3]):
            mon = num
            break
    if mon is None:
        return None
    year = int(yy)
    if year < 100:
        year += 2000
    return year, mon


def extract_rows(page_html: str) -> list[dict]:
    """Return consolidated monthly portfolio rows from the downloads hub embed."""
    text = _normalize_page(page_html)
    rows: list[dict] = []
    seen: set[str] = set()

    for ym_block in YEAR_BLOCK_RE.finditer(text):
        year = int(ym_block.group(1))
        chunk = ym_block.group(2)
        for dm in DOC_RE.finditer(chunk):
            mon_name = dm.group(1)
            url = dm.group(2).strip()
            mon = MONTH_NAME_TO_NUM.get(mon_name.lower())
            if not mon:
                continue
            # Prefer true consolidated packs; skip stray mid-month scheme files
            # that sometimes share month-named labels.
            path = unquote(urlparse(url).path).lower()
            if "monthly_portfolio" not in path and "monthly-portfolio" not in path:
                # Still accept if URL ym matches and looks like IN_MF pack
                if "in_mf_monthly" not in path and "360_one_mf_monthly" not in path:
                    continue
            key = f"{year}-{mon:02d}|{url}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "year": year,
                    "month": mon,
                    "title": f"{mon_name} {year}",
                    "url": url,
                }
            )

    # Fallback: scan all MONTHLY_PORTFOLIO S3 URLs if structured parse missed some
    for url in set(
        re.findall(
            r'https://[^"\s]+(?:IN_MF_MONTHLY_PORTFOLIO|360_ONE_MF_MONTHLY_PORTFOLIO|'
            r'360_ONE_MONTHLY_PORTFOLIO|IN_MF_Monthly_Portfolio)[^"\s]*\.xls[x]?',
            text,
            re.I,
        )
    ):
        ym = _ym_from_url(url)
        if ym is None:
            continue
        key = f"{ym[0]}-{ym[1]:02d}|{url}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "year": ym[0],
                "month": ym[1],
                "title": f"{MONTH_NUM_TO_NAME.get(ym[1], ym[1])} {ym[0]}",
                "url": url,
            }
        )

    return rows


def pick_for_month(rows: list[dict], year: int, month: int) -> list[dict]:
    """Prefer a single consolidated pack for the month (newest URL token wins)."""
    matched = [r for r in rows if r["year"] == year and r["month"] == month]
    if not matched:
        return []
    # Prefer Final / final in name, then lexicographically latest URL
    matched.sort(
        key=lambda r: (
            1 if re.search(r"final", r["url"], re.I) else 0,
            r["url"],
        ),
        reverse=True,
    )
    return [matched[0]]


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
        description="Fetch 360 ONE consolidated monthly portfolio packs"
    )
    parser.add_argument("--months", nargs="+", default=["2026-07"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Output root (writes amcs/360-one-mutual-fund/<YYYY-MM>/)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "360-one-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"GET {PAGE_URL}", flush=True)
    try:
        page_html = fetch_html(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scrapers/python/fetch_360_one.py "
                f"... --insecure-ssl"
            ) from e
        raise

    rows = extract_rows(page_html)
    print(f"  Indexed {len(rows)} monthly pack(s) from downloads hub", flush=True)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        if not args.dry_run:
            for p in out_dir.iterdir():
                if p.is_file():
                    p.unlink()

        selected = pick_for_month(rows, ym[0], ym[1])
        manifest: list[dict] = []
        print(f"\n{mk}: {len(selected)} file(s)", flush=True)
        if not selected:
            print("  No consolidated monthly portfolio for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = row["title"]
            url = row["url"]
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"360one-monthly-portfolio-{mk}.xls")
            rec = {
                "month": mk,
                "title": title,
                "source_page": PAGE_URL,
                "download_url": url,
                "saved_as": fn,
            }
            if args.dry_run:
                manifest.append({**rec, "sha256": "", "dry_run": True})
                print(f"  would save {fn}", flush=True)
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

        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
