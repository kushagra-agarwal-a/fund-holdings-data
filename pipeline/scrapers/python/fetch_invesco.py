#!/usr/bin/env python3
"""
Invesco Mutual Fund — download complete monthly holdings (per scheme) for given YYYY-MM.

Sitefinity JSON APIs (from literature page JS):
  GET https://invescomutualfund.com/api/ClassificationCompleteMonthlyHoldings?page=Holding
  GET https://invescomutualfund.com/api/CompleteMonthlyHoldings?year=YYYY&classification=<slug>

Each row has JanUrl … DecUrl and scheme Name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

BASE = "https://invescomutualfund.com"
PAGE_REF = f"{BASE}/literature-and-form?tab=Complete"
CLASSIFICATION_URL = f"{BASE}/api/ClassificationCompleteMonthlyHoldings?page=Holding"
WEEKLY_HOLDINGS_URL = f"{BASE}/api/WeeklyHoldings"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": PAGE_REF,
}

MONTH_NUM_TO_FIELD = {
    1: "JanUrl",
    2: "FebUrl",
    3: "MarUrl",
    4: "AprUrl",
    5: "MayUrl",
    6: "JunUrl",
    7: "JulUrl",
    8: "AugUrl",
    9: "SepUrl",
    10: "OctUrl",
    11: "NovUrl",
    12: "DecUrl",
}


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.xlsx"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.xlsx"


def fetch_json(url: str) -> object:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    return json.loads(raw)


def load_classifications() -> list[dict]:
    data = fetch_json(CLASSIFICATION_URL)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def fetch_holdings_for_year_classification(year: int, classification_slug: str) -> list[dict]:
    qs = urlencode({"year": str(year), "classification": classification_slug})
    url = f"{BASE}/api/CompleteMonthlyHoldings?{qs}"
    data = fetch_json(url)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def month_key_from_arg(s: str) -> tuple[str, int, int]:
    """Return (YYYY-MM, year int, month 1-12)."""
    dt = datetime.strptime(s.strip(), "%Y-%m")
    return f"{dt.year:04d}-{dt.month:02d}", dt.year, dt.month


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "*/*",
            "Referer": PAGE_REF,
        },
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def collect_urls_for_month(year: int, month_num: int) -> list[dict]:
    field = MONTH_NUM_TO_FIELD.get(month_num)
    if not field:
        return []
    rows_out: list[dict] = []
    seen_urls: set[str] = set()
    for cat in load_classifications():
        slug = (cat.get("FunClassificationValue") or "").strip()
        if not slug:
            continue
        for row in fetch_holdings_for_year_classification(year, slug):
            url = (row.get(field) or "").strip()
            if not url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            name = (row.get("Name") or "").strip() or safe_filename(url)
            rows_out.append({"download_url": url, "scheme_name": name, "classification": slug})
    return rows_out


def collect_fortnightly_urls(year: int, month_num: int) -> list[dict]:
    """GET /api/WeeklyHoldings?month=&year=&classification=fixed-income → DocumentUrl."""
    qs = urlencode(
        {
            "month": str(month_num),
            "year": str(year),
            "classification": "fixed-income",
        }
    )
    url = f"{WEEKLY_HOLDINGS_URL}?{qs}"
    data = fetch_json(url)
    rows_out: list[dict] = []
    seen_urls: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        for key in ("DocumentUrl", "documentUrl"):
            doc = (node.get(key) or "").strip() if isinstance(node.get(key), str) else ""
            if doc and doc not in seen_urls:
                seen_urls.add(doc)
                name = (
                    (node.get("DocumentName") or node.get("Name") or node.get("SchemeName") or "")
                    .strip()
                    or safe_filename(doc)
                )
                rows_out.append(
                    {
                        "download_url": doc,
                        "scheme_name": name,
                        "classification": "fixed-income",
                    }
                )
        for v in node.values():
            if isinstance(v, (list, dict)):
                walk(v)

    walk(data)
    return rows_out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Invesco complete monthly holdings")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max files per month (0 = all)")
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Use WeeklyHoldings API (fixed-income DocumentUrl) instead of monthly holdings",
    )
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "invesco-mutual-fund"

    if args.fortnightly:
        print(f"GET {WEEKLY_HOLDINGS_URL} (classification=fixed-income) …")
    else:
        print(f"GET {CLASSIFICATION_URL} …")
        cats = load_classifications()
        print(f"  … {len(cats)} classification(s)")

    for mk_raw in args.months:
        mk, year, month_num = month_key_from_arg(mk_raw)
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\nCollecting URLs for {mk} (year={year}) …")
        if args.fortnightly:
            batch = collect_fortnightly_urls(year, month_num)
        else:
            batch = collect_urls_for_month(year, month_num)
        print(f"  … {len(batch)} file(s)")
        if args.limit and len(batch) > args.limit:
            batch = batch[: args.limit]
            print(f"  (limited to {args.limit})")

        manifest: list[dict] = []
        for i, item in enumerate(batch, 1):
            url = item["download_url"]
            fname = safe_filename(url)
            rec = {
                "month": mk,
                "scheme_name": item.get("scheme_name"),
                "classification": item.get("classification"),
                "download_url": url,
                "saved_as": fname,
            }
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
