#!/usr/bin/env python3
"""
JM Financial Mutual Fund — download monthly portfolio spreadsheets (per scheme) for given YYYY-MM.

Flow (from public React bundle):
  POST https://jmmfapi.jmfinancialmf.com/api/GetDownloadNew
  Body JSON: {\"IICategoryID\":\"2\",\"IISubCategoryID\":\"0\",\"IVsearch\":\"\"}
    (category 2 = Portfolio Disclosure, same as /downloads/portfolio-disclosure)

Response: {\"data\":\"<base64 AES ciphertext>\"}

Decryption matches site CryptoJS:
  AES-256-CBC, PKCS7
  key = CryptoJS.enc.Utf8.parse(REACT_APP_AES_KEY)   # 32-byte UTF-8 string
  iv  = CryptoJS.enc.Utf8.parse(REACT_APP_AES_IV)    # 16-byte UTF-8 string

Default key/iv are embedded in the production bundle (may change on redeploy).

Decrypted payload is a **JSON array** of document rows. We keep:
  SubCategoryName == \"Monthly Portfolio of Schemes\"
and map month from the **Title** date (e.g. \"January 31, 2026\" -> 2026-01).

Files download from:
  https://www.jmfinancialmf.com/<FileName>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse
from urllib.request import Request, urlopen

API_URL = "https://jmmfapi.jmfinancialmf.com/api/GetDownloadNew"
PAGE_REF = "https://www.jmfinancialmf.com/downloads/portfolio-disclosure"
FILE_BASE = "https://www.jmfinancialmf.com/"

# From production main.*.js (REACT_APP_*); UTF-8 string bytes used as key/iv material.
KEY_STR = "6fa979f20126cb08aa645a8f495f6d85"
IV_STR = "I8zyA4lVhMCaJ5Kg"

HEADERS_POST = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.jmfinancialmf.com",
    "Referer": PAGE_REF,
}

# Site titles mix full names ("June 30, 2026") and 3-letter abbrevs ("Jun 30, 2026").
MONTH_NAME_TO_NUM = {
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

TITLE_DATE_RE = re.compile(
    r"\b("
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    r")"
    r"\s+(\d{1,2}),?\s+(\d{4})\b",
    re.I,
)

MONTHLY_SUB = "Monthly Portfolio of Schemes"
FORTNIGHTLY_SUB = "Fortnightly Portfolio of Schemes"


def check_openssl() -> None:
    try:
        subprocess.run(["openssl", "version"], capture_output=True, check=True, timeout=5)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise SystemExit(
            "JM Financial fetch requires `openssl` on PATH for AES decrypt. Error: %s" % e
        ) from e


def _key_hex() -> str:
    return KEY_STR.encode("utf-8").hex()


def _iv_hex() -> str:
    return IV_STR.encode("utf-8").hex()


def decrypt_jm_data_b64(b64: str) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".b64", delete=False) as f:
        f.write(b64.strip())
        b64_path = f.name
    try:
        proc = subprocess.run(
            [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-d",
                "-K",
                _key_hex(),
                "-iv",
                _iv_hex(),
                "-base64",
                "-A",
                "-in",
                b64_path,
            ],
            capture_output=True,
            check=True,
            timeout=120,
        )
    finally:
        Path(b64_path).unlink(missing_ok=True)
    raw = proc.stdout.decode("utf-8", "ignore")
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def fetch_document_rows() -> list[dict]:
    body = json.dumps(
        {"IICategoryID": "2", "IISubCategoryID": "0", "IVsearch": ""},
        separators=(",", ":"),
    ).encode("utf-8")
    req = Request(API_URL, data=body, method="POST", headers=HEADERS_POST)
    with urlopen(req, timeout=120) as resp:
        outer = json.loads(resp.read().decode("utf-8", "ignore"))
    b64 = (outer.get("data") or "").strip()
    if not b64:
        return []
    return decrypt_jm_data_b64(b64)


def title_to_month_key(title: str) -> str | None:
    m = TITLE_DATE_RE.search(title or "")
    if not m:
        return None
    mon, y = m.group(1), m.group(3)
    mm = MONTH_NAME_TO_NUM.get(mon.lower())
    if not mm:
        return None
    return f"{y}-{mm:02d}"


def title_to_as_of(title: str) -> str | None:
    """'Fortnightly Portfolio- JM Liquid Fund - July 15, 2026' -> '2026-07-15'."""
    m = TITLE_DATE_RE.search(title or "")
    if not m:
        return None
    mon, day, y = m.group(1), int(m.group(2)), m.group(3)
    mm = MONTH_NAME_TO_NUM.get(mon.lower())
    if not mm:
        return None
    return f"{y}-{mm:02d}-{day:02d}"


def parse_as_of(as_of: str) -> tuple[int, int, int] | None:
    parts = as_of.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def safe_filename(path_part: str) -> str:
    base = unquote(path_part.rsplit("/", 1)[-1].split("?")[0])
    if not base:
        base = "download.xlsx"
    return re.sub(r"[^\w.\-() ]", "_", base).strip()[:200] or "download.xlsx"


def file_url(file_name: str) -> str:
    fn = (file_name or "").lstrip("/")
    return FILE_BASE + fn


def encode_url_for_http(url: str) -> str:
    """Percent-encode path (spaces, commas, etc.) for urllib."""
    p = urlparse(url.strip())
    path = quote(p.path, safe="/%")
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, p.fragment))


def download(url: str) -> bytes:
    req = Request(
        encode_url_for_http(url),
        headers={
            "User-Agent": HEADERS_POST["User-Agent"],
            "Accept": "*/*",
            "Referer": PAGE_REF,
        },
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch JM Financial monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max files per month (0 = all)")
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Fetch Fortnightly Portfolio of Schemes (mid-month / month-end by --as-of)",
    )
    parser.add_argument(
        "--as-of",
        default="",
        help="Calendar as-of YYYY-MM-DD (filters fortnightly titles by date)",
    )
    args = parser.parse_args()

    check_openssl()
    amc_dir = args.root / "amcs" / "jm-financial-mutual-fund"
    label = "fortnightly" if args.fortnightly else "monthly"
    want_sub = FORTNIGHTLY_SUB if args.fortnightly else MONTHLY_SUB
    as_of = args.as_of.strip()
    if args.fortnightly and not as_of and args.months:
        as_of = f"{args.months[0]}-15"

    print(f"POST {API_URL} …")
    rows = fetch_document_rows()
    print(f"  … decrypted {len(rows)} document row(s)")

    selected: list[dict] = []
    for r in rows:
        if (r.get("SubCategoryName") or "").strip() != want_sub:
            continue
        title = (r.get("Title") or "").strip()
        fn = (r.get("FileName") or "").strip()
        if not fn:
            continue
        if args.fortnightly:
            row_as_of = title_to_as_of(title)
            if as_of and row_as_of != as_of:
                continue
            mk = title_to_month_key(title)
        else:
            mk = title_to_month_key(title)
        if not mk:
            continue
        selected.append(
            {
                "month_key": mk,
                "as_of": title_to_as_of(title) if args.fortnightly else None,
                "title": title,
                "download_url": file_url(fn),
                "file_name": fn,
            }
        )

    by_month: dict[str, list[dict]] = {k: [] for k in args.months}
    for r in selected:
        mk = r["month_key"]
        if mk in by_month:
            by_month[mk].append(r)

    for mk_raw in args.months:
        mk = datetime.strptime(mk_raw.strip(), "%Y-%m").strftime("%Y-%m")
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = by_month.get(mk) or []
        suffix = f" as_of={as_of}" if args.fortnightly and as_of else ""
        print(f"\n{mk} [{label}{suffix}]: {len(batch)} file(s)")
        if args.limit and len(batch) > args.limit:
            batch = batch[: args.limit]
            print(f"  (limited to {args.limit})")

        manifest: list[dict] = []
        if not batch:
            print(f"  No matching {label} portfolio rows.")

        for i, item in enumerate(batch, 1):
            url = item["download_url"]
            fname = safe_filename(item["file_name"])
            rec = {
                "month": mk,
                "as_of": item.get("as_of"),
                "download_url": url,
                "saved_as": fname,
                "title": item.get("title"),
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
