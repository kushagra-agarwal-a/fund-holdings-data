#!/usr/bin/env python3
"""
Mahindra Manulife Mutual Fund — download monthly portfolio files for YYYY-MM.

Source:
  GET https://investorapi.mahindramanulife.com/api/v1/web/preLogin/downloads
  Response body is AES-CBC encrypted JSON ({"payload": "..."}).
  Client constants from SPA: key=mahindra2024mahindra2024mahindra, iv=hasnainsheikh202.

Titles look like: "Monthly Portfolio Disclosure - June, 2026"
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

API_URL = "https://investorapi.mahindramanulife.com/api/v1/web/preLogin/downloads"
PAGE = "https://www.mahindramanulife.com/downloads"
AES_KEY = b"mahindra2024mahindra2024mahindra"  # 32 bytes
AES_IV = b"hasnainsheikh202"  # 16 bytes

TITLE_YM_RE = re.compile(
    r"monthly\s+portfolio\s+disclosure\s*-\s*"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s*,?\s*(\d{4})",
    re.I,
)
TITLE_FN_YM_RE = re.compile(
    r"fortnight\s+ended\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}\s*,?\s*(\d{4})",
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


def decrypt_payload(obj: dict | str):
    raw_b64 = obj["payload"] if isinstance(obj, dict) else obj
    raw = base64.b64decode(raw_b64)
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV), backend=default_backend())
    pt = cipher.decryptor().update(raw) + cipher.decryptor().finalize()
    unpadder = PKCS7(128).unpadder()
    plain = unpadder.update(pt) + unpadder.finalize()
    return json.loads(plain.decode("utf-8"))


def parse_ym(title: str) -> tuple[int, int] | None:
    text = title or ""
    m = TITLE_YM_RE.search(text) or TITLE_FN_YM_RE.search(text)
    if not m:
        return None
    mon = MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    return int(m.group(2)), mon


def walk_files(cats, path=None):
    path = path or []
    for c in cats or []:
        name = c.get("categoryName") or ""
        p = path + [name]
        for f in c.get("files") or []:
            yield p, f
        yield from walk_files(c.get("subcategories") or [], p)


def safe_filename(url: str, title: str = "") -> str:
    base = unquote(urlparse(url).path.rsplit("/", 1)[-1].split("?")[0])
    if not base or "." not in base:
        ext = ".xlsx"
        if title.lower().endswith(".xls"):
            ext = ".xls"
        base = re.sub(r"[^\w.\-() ]+", "_", title).strip()[:180] + ext
    return re.sub(r"[^\w.\-() ]+", "_", base).strip()[:220] or "mahindra.xlsx"


def fetch_structure() -> list:
    req = Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.mahindramanulife.com",
            "Referer": PAGE,
        },
    )
    with urlopen(req, timeout=60) as resp:
        obj = json.loads(resp.read().decode("utf-8", "ignore"))
    data = decrypt_payload(obj)
    if not isinstance(data, dict) or data.get("status") != 1:
        raise RuntimeError(f"unexpected API status: {data!r}"[:200])
    return data.get("data") or []


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": PAGE},
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Mahindra Manulife monthly portfolios")
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fortnightly", action="store_true", help="Fetch fortnightly debt portfolios")
    args = ap.parse_args()

    targets = {}
    for mk in args.months:
        y, m = mk.split("-")
        targets[(int(y), int(m))] = mk

    print(f"GET+decrypt {API_URL}", flush=True)
    cats = fetch_structure()
    files = list(walk_files(cats))
    print(f"  indexed {len(files)} download rows", flush=True)

    by_month: dict[str, list] = {mk: [] for mk in args.months}
    for path, f in files:
        title = f.get("title") or ""
        ym = parse_ym(title)
        if ym is None or ym not in targets:
            continue
        url = (f.get("fileUrl") or f.get("redirectUrl") or "").strip()
        if not url:
            continue
        # keep portfolio disclosure path only (monthly or fortnightly)
        path_join = " / ".join(path)
        low = path_join.lower()
        if args.fortnightly:
            if "fortnight" not in low:
                continue
        else:
            if "fortnight" in low:
                continue
            if not any("Monthly Portfolio Disclosure" == x for x in path) and "Monthly Portfolio Disclosure" not in path:
                if "Portfolio Disclosure" not in path_join:
                    continue
        by_month[targets[ym]].append({"title": title, "url": url, "path": path, "downloadId": f.get("downloadId")})

    amc_dir = args.root / "amcs" / "mahindra-manulife-mutual-fund"
    for mk in args.months:
        selected = by_month.get(mk) or []
        # de-dupe urls
        seen = set()
        uniq = []
        for row in selected:
            if row["url"] in seen:
                continue
            seen.add(row["url"])
            uniq.append(row)
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = []
        print(f"\n{mk}: {len(uniq)} file(s)", flush=True)
        for row in uniq:
            fname = safe_filename(row["url"], row["title"])
            rec = {
                "month": mk,
                "download_url": row["url"],
                "saved_as": fname,
                "title": row["title"],
                "downloadId": row.get("downloadId"),
            }
            if args.dry_run:
                print(f"  dry-run {fname}", flush=True)
                manifest.append({**rec, "dry_run": True})
                continue
            try:
                body = download(row["url"])
                (out_dir / fname).write_bytes(body)
                print(f"  OK {fname} ({len(body)} bytes)", flush=True)
                manifest.append({**rec, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
            except Exception as e:
                print(f"  ERR {fname}: {e}", flush=True)
                manifest.append({**rec, "error": str(e)})
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"  wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
