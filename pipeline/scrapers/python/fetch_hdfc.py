#!/usr/bin/env python3
"""
HDFC Mutual Fund — download monthly portfolio files for given YYYY-MM.

Primary source (more complete than page-embedded JSON):
  POST https://cms.hdfcfund.com/en/hdfc/api/v2/disclosures/monthfortportfolio
  multipart/form-data fields:
    - year: YYYY
    - type: monthly
    - month: M (1..12)

Response shape:
  {
    "data": {
      "title": "Monthly Portfolio of Scheme(s)",
      "files": [
        {"title": "Monthly HDFC ... - 31 January 2026.xlsx", "file": {"url": "https://files.hdfcfund.com/..."}}
      ]
    }
  }
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import string
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

API_URL = "https://cms.hdfcfund.com/en/hdfc/api/v2/disclosures/monthfortportfolio"
PAGE_REF = "https://www.hdfcfund.com/"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.xlsx"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.xlsx"


def month_key_to_parts(month_key: str) -> tuple[str, str]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, mm = parts[0], parts[1].zfill(2)
    if len(y) != 4 or not y.isdigit():
        raise ValueError(f"Bad year in {month_key!r}")
    if not (mm.isdigit() and 1 <= int(mm) <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, mm


def build_multipart(year: str, month: str, typ: str = "monthly") -> tuple[bytes, str]:
    # mimic browser multipart boundary style
    rand = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
    boundary = f"----WebKitFormBoundary{rand}"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="year"\r\n\r\n'
        f"{year}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="type"\r\n\r\n'
        f"{typ}\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="month"\r\n\r\n'
        f"{int(month)}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    return body, boundary


def fetch_month_rows(year: str, month: str) -> list[dict]:
    data, boundary = build_multipart(year, month, "monthly")
    req = Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": "https://www.hdfcfund.com",
            "Referer": PAGE_REF,
        },
    )
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    payload = json.loads(raw)
    files = ((payload.get("data") or {}).get("files")) or []
    if not isinstance(files, list):
        return []
    return [x for x in files if isinstance(x, dict)]


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Referer": PAGE_REF,
        },
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch HDFC monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    amc_dir = args.root / "amcs" / "hdfc-mutual-fund"

    for mk in args.months:
        y, mm = month_key_to_parts(mk)
        print(f"POST monthfortportfolio year={y} month={int(mm)} …")
        rows = fetch_month_rows(y, mm)

        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"{mk}: {len(rows)} file(s)")
        manifest: list[dict] = []

        if not rows:
            print("  No files in API response for this month.")

        for i, row in enumerate(rows, 1):
            title = str(row.get("title") or "").strip()
            file_obj = row.get("file") or {}
            file_url = str((file_obj.get("url") or "")).strip()
            if not file_url:
                continue
            fname = safe_filename(file_url)
            rec = {
                "month": mk,
                "download_url": file_url,
                "saved_as": fname,
                "title": title,
                "file_id": row.get("file_id"),
                "extension": row.get("extension"),
            }
            if args.dry_run:
                print(f"  [{i}] {fname}")
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                body = download(file_url)
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
