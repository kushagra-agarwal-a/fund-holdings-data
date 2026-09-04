#!/usr/bin/env python3
"""
Samco Mutual Fund - download monthly portfolio files for YYYY-MM.

Source page:
  https://www.samcomf.com/StatutoryDisclosure

Data source:
  Static HTML with accordion sections. Monthly disclosures are under the
  "Monthly" accordion and have titles like:
  IN_MF_MONTHLY_PORTFOLIO_February_2026_Samco_Special_Opportunities_Fund
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
from urllib.parse import unquote, urljoin, urlparse

BASE = "https://www.samcomf.com"
PAGE_URL = f"{BASE}/StatutoryDisclosure"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
MONTHLY_BLOCK_RE = re.compile(
    r'<a\s+class="toggle\s+bgm"\s+href=#>\s*Monthly\s*<span[^>]*></span></a>\s*'
    r'<div\s+class="main_div"[^>]*>\s*(.*?)\s*</div>\s*</li>',
    re.I | re.S,
)
ROW_RE = re.compile(
    r"<tr>\s*"
    r"<td[^>]*data-th=\"Document Title\"[^>]*>\s*(.*?)\s*</td>\s*"
    r"<td[^>]*data-th=\"Action\"[^>]*>\s*(.*?)\s*</td>\s*"
    r"</tr>",
    re.I | re.S,
)
HREF_RE = re.compile(r"""href=['"]([^'"]+)['"]""", re.I)
TITLE_YM_RE = re.compile(
    r"IN_MF_MONTHLY_PORTFOLIO_"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"[_ ]+(\d{4})",
    re.I,
)
TITLE_DDMMYYYY_RE = re.compile(
    r"Portfolio_Monthly_Fortnightly_OvernightFund_\s*(\d{8})\b",
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
    s = (name or "").strip() or "samco_monthly_portfolio.xls"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "samco_monthly_portfolio.xls"


def fetch_html(*, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def normalize_url(raw: str) -> str:
    s = raw.replace("\\/", "/").strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("//"):
        return "https:" + s
    return urljoin(BASE + "/", s)


def parse_ym_from_title(title: str) -> tuple[int, int] | None:
    clean = " ".join(title.replace("&nbsp;", " ").split())
    m = TITLE_YM_RE.search(clean)
    if m:
        month = MONTHS.get(m.group(1).lower())
        if month:
            return int(m.group(2)), month

    # Some rows in the Monthly accordion use DDMMYYYY token in title.
    d = TITLE_DDMMYYYY_RE.search(clean)
    if not d:
        return None
    token = d.group(1)
    dd = int(token[:2])
    mm = int(token[2:4])
    yyyy = int(token[4:8])
    if not (1 <= dd <= 31 and 1 <= mm <= 12):
        return None
    return yyyy, mm


def extract_rows(html_text: str) -> list[dict]:
    block_m = MONTHLY_BLOCK_RE.search(html_text)
    if not block_m:
        return []
    block = block_m.group(1)

    rows: list[dict] = []
    seen_name: set[str] = set()
    for raw_title, action_html in ROW_RE.findall(block):
        title = " ".join(re.sub(r"<[^>]+>", " ", raw_title).split())
        ym = parse_ym_from_title(title)
        if ym is None:
            continue
        hrefs = HREF_RE.findall(action_html)
        if not hrefs:
            continue
        # Prefer first download href from action column.
        url = normalize_url(hrefs[0])
        name = unquote(urlparse(url).path.rsplit("/", 1)[-1]).lower()
        if not name or name in seen_name:
            continue
        seen_name.add(name)
        rows.append({"year": ym[0], "month": ym[1], "url": url, "title": title})
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
    parser = argparse.ArgumentParser(description="Fetch Samco monthly portfolio files")
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
    amc_dir = args.root / "amcs" / "samco-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"GET {PAGE_URL}", flush=True)
    try:
        html_text = fetch_html(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_samco.py ... --insecure-ssl"
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
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"samco-monthly-portfolio-{mk}.xls")
            rec = {
                "month": mk,
                "title": row["title"],
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
