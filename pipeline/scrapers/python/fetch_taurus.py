#!/usr/bin/env python3
"""
Taurus Mutual Fund — download **monthly** scheme portfolio `.xlsx` for given YYYY-MM.

Public page (Drupal Views + exposed filters):
  https://www.taurusmutualfund.com/monthly-portfolio

The default HTML includes `<select name="field_monthly_portfolio_target_id">` (year → taxonomy id)
and `<select name="field_month_target_id">` (month name → id). Submitting the filter is a GET:

  /monthly-portfolio?field_monthly_portfolio_target_id=<year_id>&field_month_target_id=<month_id>

The filtered page lists one `.xlsx` per scheme under `/sites/default/files/downloads/…`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

BASE = "https://www.taurusmutualfund.com"
LISTING_URL = f"{BASE}/monthly-portfolio"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
}

MONTH_NAMES_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

SELECT_BLOCK_RE = re.compile(
    r'<select[^>]*name="([^"]+)"[^>]*>([\s\S]*?)</select>',
    re.I,
)
OPTION_RE = re.compile(
    r'<option\s+value="([^"]*)"(?:[^>]*selected[^>]*)?>([^<]*)</option>',
    re.I,
)
XLSX_HREF_RE = re.compile(
    r'href="\s*(/sites/default/files/downloads/[^"]+\.xlsx)\s*"',
    re.I,
)


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def get_html(url: str, *, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_select(html: str, name: str) -> str:
    for sel_name, block in SELECT_BLOCK_RE.findall(html):
        if sel_name == name:
            return block
    return ""


def parse_year_tid_by_calendar_year(html: str) -> dict[int, str]:
    """Calendar year (e.g. 2026) → Views filter id."""
    block = parse_select(html, "field_monthly_portfolio_target_id")
    out: dict[int, str] = {}
    for val, label in OPTION_RE.findall(block):
        lab = label.strip()
        if not val or val.lower() == "all" or not lab.isdigit() or len(lab) != 4:
            continue
        out[int(lab)] = val.strip()
    return out


def parse_month_tid_by_month_number(html: str) -> dict[int, str]:
    """1..12 → Views filter id."""
    block = parse_select(html, "field_month_target_id")
    out: dict[int, str] = {}
    for val, label in OPTION_RE.findall(block):
        lab = label.strip()
        if not val or val.lower() == "all":
            continue
        if lab in MONTH_NAMES_EN:
            out[MONTH_NAMES_EN.index(lab) + 1] = val.strip()
    return out


def filter_url(year_tid: str, month_tid: str) -> str:
    q = urlencode(
        {
            "field_monthly_portfolio_target_id": year_tid,
            "field_month_target_id": month_tid,
        }
    )
    return f"{LISTING_URL}?{q}"


def extract_xlsx_paths(html: str) -> list[str]:
    paths = [p.strip() for p in XLSX_HREF_RE.findall(html)]
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def safe_filename(path: str) -> str:
    base = unquote(path.rsplit("/", 1)[-1].split("?")[0])
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._")[:200]
    return base or "download.xlsx"


def month_key_to_parts(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Taurus MF monthly portfolio xlsx files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max files per month (0 = no cap)",
    )
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "taurus-mutual-fund"

    print(f"GET {LISTING_URL} (parse year/month filter ids) …", flush=True)
    try:
        base_html = get_html(LISTING_URL, ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_taurus.py ... --insecure-ssl"
            ) from e
        raise

    years = parse_year_tid_by_calendar_year(base_html)
    months = parse_month_tid_by_month_number(base_html)
    if not years or not months:
        raise SystemExit("Could not parse Drupal Views year/month options from listing page.")

    for mk in args.months:
        y, mon = month_key_to_parts(mk)
        ytid = years.get(y)
        mtid = months.get(mon)
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest: list[dict] = []
        print(f"\n{mk}:", flush=True)
        if not ytid:
            print(f"  Year {y} not in site dropdown (available: {sorted(years)!r}).", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue
        if not mtid:
            print("  Month mapping missing (site HTML changed?).", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        furl = filter_url(ytid, mtid)
        print(f"  GET {furl}", flush=True)
        try:
            html = get_html(furl, ctx=ctx)
        except urllib.error.URLError as e:
            print(f"  ERR filter page: {e}", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        rel_paths = extract_xlsx_paths(html)
        if args.limit and len(rel_paths) > args.limit:
            rel_paths = rel_paths[: args.limit]
        print(f"  {len(rel_paths)} xlsx file(s)", flush=True)
        if not rel_paths:
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for rp in rel_paths:
            full = BASE + quote(unquote(rp), safe="/:-._?=&%")
            fn = safe_filename(rp)
            rec = {
                "month": mk,
                "download_url": full,
                "saved_as": fn,
            }
            try:
                req = urllib.request.Request(
                    full,
                    headers={**HEADERS, "Accept": "*/*", "Referer": furl},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
                    body = resp.read()
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fn).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  OK {fn} ({len(body)} bytes)", flush=True)
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  ERR {fn}: {e}", flush=True)

        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
