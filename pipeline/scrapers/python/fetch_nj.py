#!/usr/bin/env python3
"""
NJ Mutual Fund - download monthly portfolio files for YYYY-MM.

Source page:
  https://downloads.njmutualfund.com/njmf_download.php?nme=127

Data source:
  Monthly Portfolio Disclosure accordion entries with links like:
  viewfile.php?file=NJ-MF-Monthly-Portfolio-NJBAF-February-2026-....xlsx
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
from urllib.parse import parse_qs, unquote, urljoin, urlparse

BASE = "https://downloads.njmutualfund.com"
PAGE_URL = f"{BASE}/njmf_download.php?nme=127"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
LINK_RE = re.compile(
    r"""<a[^>]+href=['"]([^'"]+)['"][^>]*>(.*?)</a>""",
    re.I | re.S,
)
FILE_PARAM_RE = re.compile(
    r"viewfile\.php\?file=([^\"'&\s>]+)",
    re.I,
)
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
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:[-_ ]+\d{1,2},?)?[-_ ]*(\d{4}|\d{2})",
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
    s = (name or "").strip() or "nj_monthly_portfolio.xls"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "nj_monthly_portfolio.xls"


def filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query or "")
    file_param = (qs.get("file") or [""])[0]
    if file_param:
        return unquote(file_param)
    return unquote(parsed.path.rsplit("/", 1)[-1])


def fetch_html(*, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _to_year(v: str) -> int:
    y = int(v)
    return y + 2000 if y < 100 else y


def parse_ym(text: str) -> tuple[int, int] | None:
    m = TITLE_YM_RE.search(text)
    if not m:
        return None
    mon = MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    return _to_year(m.group(2)), mon


def extract_rows(html_text: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for href_raw, label_html in LINK_RE.findall(html_text):
        href = href_raw.strip()
        if "viewfile.php?file=" not in href.lower():
            continue
        url = href if href.startswith("http") else urljoin(BASE + "/", href)
        if url in seen:
            continue
        label = " ".join(re.sub(r"<[^>]+>", " ", label_html).split())
        ym = parse_ym(label)
        if ym is None:
            # fallback from file name token in query string
            m = FILE_PARAM_RE.search(url)
            token = unquote(m.group(1)) if m else unquote(url)
            ym = parse_ym(token)
        if ym is None:
            continue
        seen.add(url)
        rows.append({"year": ym[0], "month": ym[1], "title": label, "url": url})
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
    parser = argparse.ArgumentParser(description="Fetch NJ monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "nj-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"GET {PAGE_URL}", flush=True)
    try:
        html_text = fetch_html(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_nj.py ... --insecure-ssl"
            ) from e
        raise

    rows = extract_rows(html_text)
    print(f"  Indexed {len(rows)} monthly row(s)", flush=True)

    by_month: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        ym = (row["year"], row["month"])
        if ym in targets:
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
            print("  No monthly portfolio file found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            url = row["url"]
            raw_name = filename_from_url(url)
            fn = safe_filename(raw_name or f"nj-monthly-portfolio-{mk}.xls")
            rec = {
                "month": mk,
                "title": row.get("title", ""),
                "source_page": PAGE_URL,
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
