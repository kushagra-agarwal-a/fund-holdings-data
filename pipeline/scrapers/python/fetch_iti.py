#!/usr/bin/env python3
"""
ITI Mutual Fund — download consolidated monthly portfolio file(s) for given YYYY-MM.

The public statutory page loads data via encrypted POST:
  POST https://itiamc.com/jeeth/api/v1/catalog/getPartnerDocumentByType
  Body: {\"eData\": \"<AES-128-CBC base64>\"}

Plaintext payload shape (before encryption): {type, guid, timeStamp}.
Response may contain {\"eData\": \"...\"} to decrypt.

AES parameters (from site bundle, production):
  key (Latin1): aar6tzij8o1snaar
  iv  (Latin1): 0123456789ABCDEF

This script encrypts/decrypts using **openssl** (must be on PATH), same as CryptoJS CBC/PKCS7.

We select **Portfolio Disclosures → Monthly →** rows whose **fileName** matches:
  \"Monthly Portfolio - <MonthName> <Year>\"
and map to the requested calendar month.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

BASE = "https://itiamc.com"
PAGE_REF = "https://www.itiamc.com/statuory-disclosure"
API_URL = f"{BASE}/jeeth/api/v1/catalog/getPartnerDocumentByType"

KEY = b"aar6tzij8o1snaar"
IV = b"0123456789ABCDEF"

HEADERS_JSON = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.itiamc.com",
    "Referer": PAGE_REF,
}

MONTH_NAME_TO_NUM = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

PORTFOLIO_TITLE_RE = re.compile(
    r"^\s*(?:Monthly|Fortnightly)\s+Portfolio\s*-\s*([A-Za-z]+)\s+(\d{4})\s*$",
    re.I,
)
PORTFOLIO_TITLE_DAY_RE = re.compile(
    r"(?:Fortnightly|Monthly)\s+Portfolio.*?(\d{1,2}).{0,12}?"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r".{0,6}?(20\d{2})",
    re.I,
)


def check_openssl() -> None:
    try:
        subprocess.run(["openssl", "version"], capture_output=True, check=True, timeout=5)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise SystemExit(
            "ITI fetch requires `openssl` on PATH for AES encrypt/decrypt. "
            f"Error: {e}"
        ) from e


def gen_guid() -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(random.choice(chars) for _ in range(32))


def encrypt_json_payload(obj: dict) -> str:
    plain = json.dumps(obj, separators=(",", ":"))
    proc = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-K",
            KEY.hex(),
            "-iv",
            IV.hex(),
            "-base64",
            "-A",
        ],
        input=plain.encode("utf-8"),
        capture_output=True,
        check=True,
        timeout=60,
    )
    return proc.stdout.decode().strip()


def decrypt_e_data(e_data: str) -> dict:
    proc = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-d",
            "-K",
            KEY.hex(),
            "-iv",
            IV.hex(),
            "-base64",
            "-A",
        ],
        input=e_data.encode("ascii"),
        capture_output=True,
        check=True,
        timeout=60,
    )
    return json.loads(proc.stdout.decode("utf-8"))


def post_encrypted(payload: dict) -> dict:
    body = {"guid": gen_guid(), "timeStamp": int(time.time() * 1000), **payload}
    enc = encrypt_json_payload(body)
    data = json.dumps({"eData": enc}).encode("utf-8")
    req = Request(API_URL, data=data, method="POST", headers=HEADERS_JSON)
    with urlopen(req, timeout=120) as resp:
        raw = json.loads(resp.read().decode("utf-8", "ignore"))
    if raw.get("eData"):
        return decrypt_e_data(raw["eData"])
    return raw


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit("/", 1)[-1]
    base = unquote(base.split("?")[0])
    if not base or base in (".", ".."):
        base = "download.bin"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.bin"


DDMMYYYY_RE = re.compile(r"(?<!\d)(\d{1,2})[./\-](\d{1,2})[./\-](20\d{2})(?!\d)")


def file_name_to_month_key(file_name: str) -> str | None:
    text = (file_name or "").strip()
    m = PORTFOLIO_TITLE_RE.match(text)
    if m:
        mon_s, y = m.group(1), m.group(2)
        mm = MONTH_NAME_TO_NUM.get(mon_s.lower())
        if mm:
            return f"{y}-{mm:02d}"
    # Fortnightly: "Fortnightly Debt Portfolio Disclosures_31.07.2026"
    d = DDMMYYYY_RE.search(text)
    if d:
        _dd, mm, y = int(d.group(1)), int(d.group(2)), d.group(3)
        if 1 <= mm <= 12:
            return f"{y}-{mm:02d}"
    m2 = PORTFOLIO_TITLE_DAY_RE.search(text)
    if m2:
        mon_s, y = m2.group(2), m2.group(3)
        mm = MONTH_NAME_TO_NUM.get(mon_s.lower()[:3]) or MONTH_NAME_TO_NUM.get(mon_s.lower())
        if mm:
            return f"{y}-{mm:02d}"
    return None


def extract_portfolio_rows(data: dict, *, fortnightly: bool = False) -> list[dict]:
    want_topic = "Fortnightly" if fortnightly else "Monthly"
    out: list[dict] = []
    tl = ((data.get("data") or {}).get("typeList")) or []
    if not isinstance(tl, list):
        return out
    for block in tl:
        if not isinstance(block, dict) or block.get("subType") != "Portfolio Disclosures":
            continue
        for st in (block.get("subTypesList") or []):
            topic = str(st.get("topic") or "")
            if not isinstance(st, dict):
                continue
            if topic != want_topic and not (fortnightly and "fortnight" in topic.lower()):
                continue
            for item in st.get("topicsList") or []:
                if not isinstance(item, dict):
                    continue
                fn = (item.get("fileName") or "").strip()
                mk = file_name_to_month_key(fn)
                if mk and (item.get("url") or "").strip():
                    out.append(
                        {
                            "month_key": mk,
                            "fileName": fn,
                            "download_url": item["url"].strip(),
                        }
                    )
    return out


def fetch_catalog(*, fortnightly: bool = False) -> list[dict]:
    raw = post_encrypted({"type": "Disclosure"})
    if raw.get("status") != 0:
        raise RuntimeError(f"API error: {raw}")
    return extract_portfolio_rows(raw, fortnightly=fortnightly)


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": HEADERS_JSON["User-Agent"],
            "Accept": "*/*",
            "Referer": PAGE_REF,
        },
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch ITI consolidated monthly portfolio file")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fortnightly", action="store_true", help="Fetch fortnightly debt portfolios when supported")
    args = parser.parse_args()

    check_openssl()
    amc_dir = args.root / "amcs" / "iti-mutual-fund"

    print(f"POST {API_URL} …")
    rows = fetch_catalog(fortnightly=args.fortnightly)
    kind = "fortnightly" if args.fortnightly else "monthly"
    print(f"  … {len(rows)} consolidated {kind} portfolio row(s) in catalog")

    by_month: dict[str, list[dict]] = {k: [] for k in args.months}
    for r in rows:
        mk = r.get("month_key")
        if mk in by_month:
            by_month[mk].append(r)

    for mk_raw in args.months:
        mk = datetime.strptime(mk_raw.strip(), "%Y-%m").strftime("%Y-%m")
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = by_month.get(mk) or []
        print(f"\n{mk}: {len(batch)} file(s)")
        manifest: list[dict] = []

        if not batch:
            print("  No matching consolidated Monthly Portfolio row for this month.")
        # expect at most one consolidated file
        for i, row in enumerate(batch, 1):
            url = row["download_url"]
            fname = safe_filename(url)
            rec = {
                "month": mk,
                "download_url": url,
                "saved_as": fname,
                "title": row.get("fileName"),
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
