#!/usr/bin/env python3
"""
DSP Mutual Fund — download **month-end portfolio** disclosure archives for given YYYY-MM.

The public page embeds a **Month End Portfolio Disclosures** section with one `.zip` per
month-end (consolidated package). Anchor text looks like:
  Portfolio Details as on February 28, 2026

We parse the calendar month from that phrase and match `--months`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
import http.cookiejar
import urllib.request

# Canonical page (matches site); `/about-us/mandatory-disclosure/...` also redirects here.
PAGE_URL = "https://www.dspim.com/mandatory-disclosures/portfolio-disclosures"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SECTION_END_MARKER = re.compile(
    r"<big>Month End Portfolio Disclosures</big>([\s\S]*?)<big>Half-Yearly Portfolio Disclosures</big>",
    re.I,
)

# <a href="https://...zip"> ... Portfolio Details as on Month D, YYYY</a>
LINK_RE = re.compile(
    r'<a\s+href="(https://www\.dspim\.com/media/pages/mandatory-disclosures/portfolio-disclosures/[^"]+\.zip)"[^>]*>'
    r"[\s\S]*?"
    r"Portfolio Details as on\s+([^<]+)</a>",
    re.I | re.S,
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.zip"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.zip"


def anchor_to_month_key(anchor: str) -> str | None:
    """e.g. 'February 28, 2026' -> '2026-02'."""
    text = " ".join(anchor.split())
    for fmt in ("%B %d, %Y", "%B %Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return f"{dt.year}-{dt.month:02d}"
        except ValueError:
            continue
    return None


def fetch_html(opener: urllib.request.OpenerDirector) -> str:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS, method="GET")
    with opener.open(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "ignore")


def parse_month_end_links(html: str) -> list[tuple[str, str, str]]:
    """Returns (month_key, url, anchor_label)."""
    m = SECTION_END_MARKER.search(html)
    if not m:
        raise RuntimeError(
            "Could not find Month End / Half-Yearly section markers — page structure may have changed."
        )
    block = m.group(1)
    out: list[tuple[str, str, str]] = []
    for url, label in LINK_RE.findall(block):
        mk = anchor_to_month_key(label.strip())
        if mk:
            out.append((mk, url.strip(), label.strip()))
    return out


def download(opener: urllib.request.OpenerDirector, url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with opener.open(req, timeout=180) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch DSP MF month-end portfolio zip for each YYYY-MM",
    )
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    want = set(args.months)
    amc_dir = args.root / "amcs" / "dsp-mutual-fund"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    print(f"GET {PAGE_URL} …", flush=True)
    html = fetch_html(opener)
    rows = parse_month_end_links(html)
    print(f"  … {len(rows)} month-end zip link(s) in section", flush=True)

    by_month: dict[str, tuple[str, str]] = {}
    for mk, url, label in rows:
        if mk not in want:
            continue
        # If duplicate month keys, keep first (should not happen)
        if mk not in by_month:
            by_month[mk] = (url, label)

    for month_key in args.months:
        out_dir = amc_dir / month_key
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest: list[dict] = []

        pair = by_month.get(month_key)
        print(f"\n{month_key}:", end=" ", flush=True)
        if not pair:
            print("no month-end row on page for this month.", flush=True)
            man_path = out_dir / "manifest.json"
            man_path.write_text("[]\n", encoding="utf-8")
            print(f"Wrote {man_path}", flush=True)
            continue

        file_url, label = pair
        fname = safe_filename(file_url)
        rec = {
            "month": month_key,
            "download_url": file_url,
            "saved_as": fname,
            "anchor_label": label,
            "kind": "month_end_portfolio_zip",
        }

        if args.dry_run:
            print(f"would save {fname}", flush=True)
            manifest.append({**rec, "sha256": "", "dry_run": True})
        else:
            try:
                body = download(opener, file_url)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fname).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"OK {fname} ({len(body)} bytes)", flush=True)
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"ERR: {e}", flush=True)

        man_path = out_dir / "manifest.json"
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Wrote {man_path}", flush=True)


if __name__ == "__main__":
    main()
