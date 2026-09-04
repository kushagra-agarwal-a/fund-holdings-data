#!/usr/bin/env python3
"""
Tata Mutual Fund - download monthly or fortnightly portfolio files for YYYY-MM.

Monthly (HTML embed):
  https://www.tatamutualfund.com/schemes-related/portfolio

Fortnightly (CMS API):
  GET https://prod-dist-api.tatamfdev.com/cms-data/api/CMSDATA_portfolio_fortnightly
  Header: check-enc: false
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
import warnings
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse

PAGE_URL = "https://www.tatamutualfund.com/schemes-related/portfolio"
FORTNIGHTLY_API_URL = (
    "https://prod-dist-api.tatamfdev.com/cms-data/api/CMSDATA_portfolio_fortnightly"
)
SITE_REFERER = "https://www.tatamutualfund.com/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

API_HEADERS = {
    **HEADERS,
    "Accept": "application/json",
    "Referer": SITE_REFERER,
    "check-enc": "false",
}

TITLE_RE = re.compile(
    r"portfolio\s+as\s+on\s+\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r",?\s+([12]\d{3})",
    re.I,
)

FORTNIGHTLY_TITLE_RE = re.compile(
    r"fortnightly\s+portfolio\s+for\s+the\s+period\s+ending\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+([12]\d{3})",
    re.I,
)
MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def month_key_to_ym(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def parse_month_from_title(title: str) -> tuple[int, int] | None:
    m = TITLE_RE.search(title or "")
    if not m:
        return None
    month = MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return int(m.group(2)), month


def parse_fortnightly_from_title(title: str) -> tuple[int, int, int] | None:
    """Return (year, month, day) from fortnightly CMS title."""
    m = FORTNIGHTLY_TITLE_RE.search(re.sub(r"\s+", " ", (title or "")).strip())
    if not m:
        return None
    day = int(m.group(1))
    month = MONTHS.get(m.group(2).lower())
    if not month:
        return None
    return int(m.group(3)), month, day


def title_to_as_of(title: str) -> str | None:
    parsed = parse_fortnightly_from_title(title)
    if not parsed:
        return None
    y, m, d = parsed
    return f"{y}-{m:02d}-{d:02d}"


def safe_filename(name: str) -> str:
    s = (name or "").strip() or "tata_monthly_portfolio.xlsx"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "tata_monthly_portfolio.xlsx"


def path_to_download_url(url: str) -> str:
    p = urlparse(url)
    safe_path = "/".join(quote(seg, safe="%") for seg in p.path.split("/"))
    host = p.netloc
    if host.lower() == "betacms.tatamutualfund.com":
        host = "www.tatamutualfund.com"
    return urlunparse((p.scheme, host, safe_path, p.params, p.query, p.fragment))


def fetch_text(url: str, *, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def extract_initial_data_array(html_text: str) -> list[dict]:
    # The page contains escaped script payload; decode escapes first.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        decoded = html_text.encode("utf-8").decode("unicode_escape", errors="ignore")
    needle = '"initialData":['
    idx = decoded.find(needle)
    if idx < 0:
        return []

    start = decoded.find("[", idx)
    if start < 0:
        return []

    depth = 0
    end = -1
    for i, ch in enumerate(decoded[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return []

    arr_txt = decoded[start : end + 1]
    arr = json.loads(arr_txt)
    if not isinstance(arr, list):
        return []
    return [r for r in arr if isinstance(r, dict)]


def fetch_json(url: str, *, headers: dict[str, str], ctx: ssl.SSLContext) -> object:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def fetch_fortnightly_rows(*, ctx: ssl.SSLContext) -> list[dict]:
    data = fetch_json(FORTNIGHTLY_API_URL, headers=API_HEADERS, ctx=ctx)
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def fetch_rows(*, ctx: ssl.SSLContext) -> list[dict]:
    html_text = fetch_text(PAGE_URL, ctx=ctx)
    rows = extract_initial_data_array(html_text)
    # Guard: keep only rows that look like Monthly tab entries
    return [r for r in rows if "portfolio as on" in str(r.get("field_document_title") or "").lower()]


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Tata monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    parser.add_argument("--fortnightly", action="store_true", help="Fetch fortnightly debt portfolios when supported")
    parser.add_argument(
        "--as-of",
        default="",
        help="Calendar as-of YYYY-MM-DD (filters fortnightly CMS titles)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "tata-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}
    as_of = args.as_of.strip()
    if args.fortnightly and not as_of and args.months:
        as_of = f"{args.months[0]}-15"

    try:
        if args.fortnightly:
            print(f"GET {FORTNIGHTLY_API_URL}", flush=True)
            rows = fetch_fortnightly_rows(ctx=ctx)
            print(f"  Indexed {len(rows)} row(s) from fortnightly CMS API", flush=True)
        else:
            print(f"GET {PAGE_URL}", flush=True)
            rows = fetch_rows(ctx=ctx)
            print(f"  Indexed {len(rows)} row(s) from embedded Monthly tab data", flush=True)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_tata.py ... --insecure-ssl"
            ) from e
        raise

    by_month: dict[tuple[int, int], list[dict]] = {}
    seen: set[str] = set()
    for row in rows:
        title = str(row.get("field_document_title") or "").strip()
        if args.fortnightly:
            parsed = parse_fortnightly_from_title(title)
            if parsed is None:
                continue
            y, m, d = parsed
            ym = (y, m)
            row_as_of = f"{y}-{m:02d}-{d:02d}"
            if as_of and row_as_of != as_of:
                continue
        else:
            ym = parse_month_from_title(title)
            if ym is None:
                continue
        if ym not in targets:
            continue
        raw_url = str(row.get("field_media_document") or row.get("field_icon_link") or "").strip()
        if not raw_url:
            continue
        key = f"{ym}:{raw_url}"
        if key in seen:
            continue
        seen.add(key)
        by_month.setdefault(ym, []).append(row)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        if not args.dry_run:
            for p in out_dir.iterdir():
                if p.is_file():
                    p.unlink()

        selected = by_month.get(ym, [])
        manifest: list[dict] = []
        suffix = f" as_of={as_of}" if args.fortnightly and as_of else ""
        label = "fortnightly" if args.fortnightly else "monthly"
        print(f"\n{mk} [{label}{suffix}]: {len(selected)} file(s)", flush=True)
        if not selected:
            print(f"  No {label} portfolio row found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = str(row.get("field_document_title") or "").strip()
            raw_url = str(row.get("field_media_document") or row.get("field_icon_link") or "").strip()
            url = path_to_download_url(raw_url)
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"tata_{label}_portfolio_{mk}.xlsx")
            rec = {
                "month": mk,
                "as_of": title_to_as_of(title) if args.fortnightly else None,
                "title": title,
                "download_url": url,
                "saved_as": fn,
            }
            if args.dry_run:
                manifest.append({**rec, "sha256": "", "dry_run": True})
                print(f"  DRY {fn}", flush=True)
                continue
            try:
                body = download(url, ctx=ctx)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fn).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  OK {fn} ({len(body)} bytes)", flush=True)
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  ERR {fn}: {e}", flush=True)

        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
