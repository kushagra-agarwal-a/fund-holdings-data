#!/usr/bin/env python3
"""
Capitalmind Mutual Fund — download **monthly portfolio** `.xlsx` files for given YYYY-MM.

Source: static HTML at statutory disclosures — the **Monthly Portfolio** pill tab
(`#v-pills-tabContent4`) lists one row per scheme per month (`<h6>Month Year</h6>` + download link).

There is typically **one file per scheme** per calendar month (e.g. Flexi Cap + Liquid = 2 files).

Uses stdlib only (`urllib`). Request headers match a normal browser to avoid edge/WAF blocks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import http.cookiejar
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

BASE = "https://capitalmindmf.com"
PAGE_URL = f"{BASE}/statutory-disclosures.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE}/",
}

MONTH_LONG_TO_MM = {
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

# <h6>…</h6> then <a href="…xlsx">
H6_XLSX_RE = re.compile(
    r'<h6[^>]*>([^<]+)</h6>\s*<a\s+[^>]*href="([^"]+\.xlsx)"',
    re.I | re.S,
)

LABEL_MONTH_RE = re.compile(
    r"^([A-Za-z]+)\s+(\d{4})\s*$",
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


def month_label_to_key(label: str) -> str | None:
    m = LABEL_MONTH_RE.match(label.strip())
    if not m:
        return None
    mon_word, year = m.group(1).lower(), m.group(2)
    mm = MONTH_LONG_TO_MM.get(mon_word)
    if not mm:
        return None
    return f"{year}-{mm}"


def fetch_page(opener: urllib.request.OpenerDirector) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with opener.open(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "ignore")


def extract_monthly_tab(html: str) -> str:
    """HTML fragment for tab pane `v-pills-tabContent4` (Monthly Portfolio)."""
    needle0 = 'id="v-pills-tabContent4"'
    needle1 = 'id="v-pills-tabContent5"'
    i0 = html.find(needle0)
    i1 = html.find(needle1)
    if i0 == -1 or i1 == -1 or i1 <= i0:
        raise RuntimeError(
            f"Could not isolate Monthly Portfolio tab ({needle0!r} … {needle1!r}). "
            "Page structure may have changed."
        )
    return html[i0:i1]


def list_monthly_links(html: str) -> list[tuple[str, str, str]]:
    """
    Returns list of (month_label, absolute_url, month_key) for Monthly Portfolio tab only.
    """
    block = extract_monthly_tab(html)
    out: list[tuple[str, str, str]] = []
    for label, href in H6_XLSX_RE.findall(block):
        label = label.strip()
        mk = month_label_to_key(label)
        if not mk:
            continue
        href = href.strip()
        full = href if href.startswith("http") else urljoin(BASE, href)
        out.append((label, full, mk))
    return out


def download(opener: urllib.request.OpenerDirector, url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with opener.open(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Capitalmind MF monthly portfolio xlsx file(s) per YYYY-MM",
    )
    parser.add_argument(
        "--months",
        nargs="+",
        default=["2026-01", "2026-02"],
        help="Calendar months as YYYY-MM",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "capitalmind-mutual-fund"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    print(f"GET {PAGE_URL} …", flush=True)
    html = fetch_page(opener)
    rows = list_monthly_links(html)
    print(f"  … {len(rows)} monthly portfolio row(s) in tab", flush=True)

    by_month: dict[str, list[tuple[str, str]]] = {}
    for label, url, mk in rows:
        by_month.setdefault(mk, []).append((label, url))

    for month_key in args.months:
        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)

        selected = list(by_month.get(month_key) or [])
        print(f"\n{month_key}: {len(selected)} file(s)", flush=True)
        manifest: list[dict] = []

        if not selected:
            print(
                "  No rows matched (month not on page yet, or label format changed).",
                flush=True,
            )

        for i, (label, file_url) in enumerate(selected, 1):
            fname = safe_filename(file_url)
            rec = {
                "month": month_key,
                "download_url": file_url,
                "saved_as": fname,
                "label": label,
            }
            if args.dry_run:
                print(f"  [{i}] {fname}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(opener, file_url)
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
