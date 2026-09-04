#!/usr/bin/env python3
"""
Baroda BNP Paribas Mutual Fund — download **monthly portfolio** files for given YYYY-MM.

Primary path (default): download the consolidated workbook:
  \"Monthly Portfolio - all funds as on <DD> <Month> <YYYY>\"
which is typically an `.xls` named like:
  BOBBNPMF_Monthly_Portfolio_<DD-MM-YYYY>_<id>.xls

The public page loads additional rows via AJAX:
  POST https://www.barodabnpparibasmf.in/ajax-load-more-documents
(application/x-www-form-urlencoded; requires the hidden `csrf_test_name` + `category` from the page HTML.)

Optional:
  `--all-schemes` downloads every per-scheme monthly portfolio `.xlsx` for that month (much larger).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlparse
import http.cookiejar
import urllib.parse
import urllib.request

BASE = "https://www.barodabnpparibasmf.in/"
PAGE_URL = BASE + "downloads/monthly-portfolio-scheme"
AJAX_URL = BASE + "ajax-load-more-documents"

MONTH_NAME_TO_NUM = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}

AS_ON_RE = re.compile(
    r"\bas\s+on\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b",
    re.I,
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def title_to_month_key(title: str) -> str | None:
    m = AS_ON_RE.search((title or "").strip())
    if not m:
        return None
    _d, mon_word, year = m.group(1), m.group(2), m.group(3)
    mm = MONTH_NAME_TO_NUM.get(mon_word.lower())
    if not mm:
        return None
    return f"{year}-{mm}"


def parse_li_items(html_chunk: str) -> list[tuple[str, str]]:
    """
    Returns list of (title, url) for document rows embedded in HTML fragments.
    """
    items: list[tuple[str, str]] = []
    for m in re.finditer(
        r'<p class="file-name">(.*?)</p>.*?href="([^"]+\.(?:xlsx|xls|pdf)(?:\?[^"]*)?)"',
        html_chunk,
        re.I | re.S,
    ):
        title = unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        url = m.group(2).strip()
        if title and url:
            items.append((title, url))
    return items


def fetch_year_listing(send_year: str) -> dict[str, str]:
    """
    Fetch all rows for a given year dropdown value, deduping by download URL.
    Returns url -> title map.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    html = (
        opener.open(urllib.request.Request(PAGE_URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=60)
        .read()
        .decode("utf-8", "ignore")
    )

    csrf_m = re.search(r'name="csrf_test_name"\s+value="([^"]+)"', html)
    cat_m = re.search(r'id="category"[^>]*value="([^"]+)"', html)
    total_m = re.search(r'id="total_cnt"[^>]*value="([^"]+)"', html)
    page_m = re.search(r'id="page"[^>]*value="([^"]+)"', html)
    sub_m = re.search(r'id="sub_container"[^>]*>([\s\S]*?)</ul>', html)
    if not (csrf_m and cat_m and total_m and page_m and sub_m):
        raise RuntimeError("Failed to parse required hidden fields from monthly portfolio page HTML")

    csrf = csrf_m.group(1)
    category = cat_m.group(1)
    total_cnt = total_m.group(1)

    items: dict[str, str] = {}
    for title, url in parse_li_items(sub_m.group(1)):
        items[url] = title

    page = int(page_m.group(1))
    while True:
        post = {
            "csrf_test_name": csrf,
            "cnt": total_cnt,
            "pagination": str(page),
            "send_category": category,
            "send_year": send_year,
            "remaining_cnt": "0",
        }
        data = urllib.parse.urlencode(post).encode("utf-8")
        req = urllib.request.Request(
            AJAX_URL,
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": PAGE_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        raw = opener.open(req, timeout=120).read().decode("utf-8", "ignore")
        payload = json.loads(raw)
        chunk = payload.get("data") or ""
        for title, url in parse_li_items(chunk):
            items.setdefault(url, title)

        page = int(payload.get("pagination") or page)
        if payload.get("status") == "N":
            break
        if page > 500:
            raise RuntimeError("Safety stop: pagination exceeded 500 pages")

    return items


def pick_rows_for_month(
    items: dict[str, str],
    month_key: str,
    *,
    all_schemes: bool,
) -> list[tuple[str, str]]:
    """
    Return list of (title, url) for the requested month.
    """
    selected: list[tuple[str, str]] = []
    for url, title in items.items():
        mk = title_to_month_key(title)
        if mk != month_key:
            continue
        tl = title.lower()
        if (not all_schemes) and ("all funds" not in tl):
            continue
        if all_schemes and ("all funds" in tl):
            # Per-scheme mode: skip consolidated row if present.
            continue
        selected.append((title, url))

    # Stable ordering: by title
    selected.sort(key=lambda x: x[0])
    return selected


def download(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": PAGE_URL,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Baroda BNP Paribas MF monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="Months as YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument(
        "--all-schemes",
        action="store_true",
        help="Download every per-scheme monthly portfolio file for each month (not just consolidated).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "baroda-bnp-paribas-mutual-fund"

    # Group months by calendar year for efficient listing reuse.
    months_by_year: dict[str, list[str]] = {}
    for mk in args.months:
        y = mk.split("-", 1)[0]
        months_by_year.setdefault(y, []).append(mk)

    cache: dict[str, dict[str, str]] = {}
    for y in sorted(months_by_year.keys()):
        print(f"Listing monthly portfolio page (year={y})…", flush=True)
        cache[y] = fetch_year_listing(y)
        print(f"  … indexed {len(cache[y])} unique download URL(s)", flush=True)

    for month_key in args.months:
        y = month_key.split("-", 1)[0]
        items = cache[y]
        rows = pick_rows_for_month(items, month_key, all_schemes=args.all_schemes)

        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{month_key}: {len(rows)} file(s) to download", flush=True)
        manifest: list[dict] = []
        if not rows:
            print("  No matching rows (check if that month is published yet).", flush=True)

        for i, (title, file_url) in enumerate(rows, 1):
            fname = safe_filename(file_url)
            rec = {
                "month": month_key,
                "download_url": file_url,
                "saved_as": fname,
                "title": title,
                "mode": "all_schemes" if args.all_schemes else "consolidated",
            }

            if args.dry_run:
                print(f"  [{i}] {fname}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue

            try:
                body = download(file_url)
                h = hashlib.sha256(body).hexdigest()
                dest = out_dir / fname
                dest.write_bytes(body)
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
