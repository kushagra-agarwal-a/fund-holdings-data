#!/usr/bin/env python3
"""Standalone Union Aug 2026 portfolio fetch for GitHub Actions egress."""
from __future__ import annotations

import calendar
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from curl_cffi import requests as creq

PAGE = "https://www.unionmf.com/about-us/downloads/monthly-portfolio"
API = (
    "https://www.unionmf.com/api/downloads/documents"
    "?$filter=FolderId%20eq%20b6cafa81-47fb-4935-bc54-b752b9e7d797&$orderby=Yearfilter%20desc"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": PAGE,
}

URL_RE = re.compile(
    r"https?://www\.unionmf\.com/docs/default-source/funddetail-downloads/"
    r"fund-portfolio/(august-2026)/[^\"'\s<>]+\.(?:xlsx|xls|xlsb)(?:\?[^\"'\s<>]*)?",
    re.I,
)

# Known July filenames (from prior sync) → derive August paths.
JULY_FILES = [
    "monthly-portfolio-report-union-active-momentum-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-aggressive-hybrid-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-arbitrage-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-balanced-advantage-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-business-cycle-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-childrens-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-consumption-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-diversified-equity-all-cap-active-fof-31-07-2026.xlsx",
    "monthly-portfolio-report-union-elss-tax-saver-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-equity-savings-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-flexi-cap-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-focused-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-gold-etf-31-07-2026.xlsx",
    "monthly-portfolio-report-union-gold-etf-fund-of-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-income-plus-arbitrage-active-fof-31-07-2026.xlsx",
    "monthly-portfolio-report-union-innovation-opportunities-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-large-midcap-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-largecap-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-midcap-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-multi-asset-allocation-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-multicap-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-retirement-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-small-cap-fund-31-07-2026.xlsx",
    "monthly-portfolio-report-union-value-fund-31-07-2026.xlsx",
]


def get(url: str) -> bytes:
    r = creq.get(url, headers=HEADERS, impersonate="chrome131", timeout=120)
    r.raise_for_status()
    return r.content


def derive_august_urls() -> list[str]:
    out = []
    for name in JULY_FILES:
        aug = name.replace("31-07-2026", "31-08-2026").replace("july-2026", "august-2026")
        out.append(
            "https://www.unionmf.com/docs/default-source/funddetail-downloads/"
            f"fund-portfolio/august-2026/{aug}"
        )
    return out


def main() -> int:
    out_dir = Path("union-aug-2026")
    out_dir.mkdir(parents=True, exist_ok=True)
    urls: list[str] = []

    print("GET page…", flush=True)
    try:
        html = get(PAGE).decode("utf-8", "ignore")
        urls = sorted({m.group(0).split("?")[0] for m in URL_RE.finditer(html)})
        print(f"  page links: {len(urls)}", flush=True)
    except Exception as e:
        print(f"  page failed: {e}", flush=True)

    if not urls:
        print("GET API…", flush=True)
        try:
            data = json.loads(get(API).decode("utf-8", "ignore"))
            for row in data.get("value") or []:
                u = str(row.get("Url") or "")
                if "august-2026" in u.lower() and re.search(r"\.(xlsx|xls|xlsb)$", u, re.I):
                    urls.append(u if u.startswith("http") else "https://www.unionmf.com" + u)
            urls = sorted(set(urls))
            print(f"  api links: {len(urls)}", flush=True)
        except Exception as e:
            print(f"  api failed: {e}", flush=True)

    if not urls:
        print("Using derived August URLs from July filename set…", flush=True)
        urls = derive_august_urls()

    manifest = []
    ok = 0
    for i, url in enumerate(urls, 1):
        fname = unquote(url.rstrip("/").rsplit("/", 1)[-1].split("?")[0])
        dest = out_dir / fname
        try:
            body = get(url)
            if len(body) < 1000 or not (body[:2] == b"PK" or body[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
                print(f"  [{i}] SKIP {fname} ({len(body)} bytes, not spreadsheet)", flush=True)
                continue
            dest.write_bytes(body)
            ok += 1
            print(f"  [{i}] OK {fname} ({len(body)})", flush=True)
            manifest.append(
                {
                    "url": url,
                    "file": fname,
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        except Exception as e:
            print(f"  [{i}] FAIL {fname}: {e}", flush=True)

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"downloaded": ok, "attempted": len(urls), "out": str(out_dir)}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
