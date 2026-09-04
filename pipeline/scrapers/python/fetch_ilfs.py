#!/usr/bin/env python3
"""
IL&FS Mutual Fund (IDF) — download monthly portfolio files for given YYYY-MM.

Source page:
  http://www.ilfsinfrafund.com/other.php
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

PAGE_URL = "http://www.ilfsinfrafund.com/other.php"
BASE_URL = "https://www.ilfsinfrafund.com/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

LINK_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
MONTH_YEAR_RE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|november|december)(?:[\s\-_]+)?(\d{4})",
    re.I,
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = unquote(path.rsplit("/", 1)[-1].split("?")[0])
    if not base or base in (".", ".."):
        base = "download.xlsx"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.xlsx"


def fetch_html() -> str:
    req = Request(PAGE_URL, headers=HEADERS)
    with urlopen(req, timeout=180) as resp:
        return resp.read().decode("utf-8", "ignore")


def text_to_month_key(text: str) -> str | None:
    m = MONTH_YEAR_RE.search(text)
    if not m:
        return None
    mon, year = m.group(1), m.group(2)
    try:
        dt = datetime.strptime(f"{mon} {year}", "%B %Y")
        return f"{dt.year:04d}-{dt.month:02d}"
    except ValueError:
        return None


def extract_rows(html: str) -> list[dict]:
    rows: list[dict] = []
    for href in LINK_RE.findall(html):
        full = urljoin(BASE_URL, href)
        blob = unquote(full).lower()
        if not re.search(r"\.(xlsx|xls|zip|pdf)(\?|$)", blob):
            continue
        if "portfolio" not in blob and "monthly" not in blob:
            continue
        if "fortnight" in blob or "halfyear" in blob:
            continue
        mk = text_to_month_key(unquote(full))
        if not mk:
            continue
        rows.append({"month_key": mk, "download_url": full, "title": safe_filename(full)})
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        key = (r["month_key"], r["download_url"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def score_row(url: str) -> int:
    b = unquote(url).lower()
    score = 0
    if "portfolio_transactionreports" in b:
        score += 100
    if "transaction report" in b:
        score += 50
    if "monthly" in b:
        score += 20
    if "portfolio" in b:
        score += 20
    return score


def pick_best_per_month(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    return [sorted(rows, key=lambda r: (score_row(r["download_url"]), r["download_url"]), reverse=True)[0]]


def download(url: str) -> bytes:
    req = Request(encode_url_for_http(url), headers={"User-Agent": HEADERS["User-Agent"], "Accept": "*/*", "Referer": PAGE_URL})
    with urlopen(req, timeout=180) as resp:
        return resp.read()


def encode_url_for_http(url: str) -> str:
    p = urlparse(url.strip())
    path = quote(p.path, safe="/%")
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, p.fragment))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch IL&FS monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--all-candidates", action="store_true", help="Download all matches per month")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent, help="mf-monthly-holdings root")
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "ilfs-mutual-fund-idf"
    print(f"GET {PAGE_URL} ...")
    html = fetch_html()
    rows = extract_rows(html)
    print(f"  ... parsed {len(rows)} monthly portfolio candidate link(s)")

    by_month: dict[str, list[dict]] = {k: [] for k in args.months}
    for r in rows:
        mk = r.get("month_key")
        if mk in by_month:
            by_month[mk].append(r)

    for mk in args.months:
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = by_month.get(mk) or []
        if not args.all_candidates:
            batch = pick_best_per_month(batch)
        print(f"\n{mk}: {len(batch)} file(s)")
        manifest: list[dict] = []
        if not batch:
            print("  No matching rows for this month.")
        for i, row in enumerate(batch, 1):
            url = row["download_url"]
            fname = safe_filename(url)
            rec = {"month": mk, "download_url": url, "saved_as": fname, "title": row.get("title")}
            if args.dry_run:
                print(f"  [{i}] {fname}")
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(url)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fname).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  [{i}] OK {fname} ({len(body)} bytes)")
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  [{i}] ERR {fname}: {e}")
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {out_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
