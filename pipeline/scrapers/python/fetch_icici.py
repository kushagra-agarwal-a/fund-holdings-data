#!/usr/bin/env python3
"""
ICICI Prudential Mutual Fund - download monthly portfolio files for YYYY-MM.

Source API:
  POST https://apps.digital.icicipruamc.com/nms/v1/downloads/files

Observed request payload:
  {
    "categoryId":"26a073d7-08d2-4a95-95fa-f83a4ee51e40",
    "schemeCategory":"",
    "userType":"Investor",
    "fileType":"All",
    "page":"1",
    "size":"20",
    "filter":[],
    "categoryName":"OTHERS"
  }
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

BASE = "https://www.icicipruamc.com"
API_URL = "https://apps.digital.icicipruamc.com/nms/v1/downloads/files"
CATEGORY_ID = "26a073d7-08d2-4a95-95fa-f83a4ee51e40"
HEADERS_BASE = {
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Origin": BASE,
    "Referer": f"{BASE}/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "env": "api",
}
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
TITLE_YM_RE = re.compile(
    r"monthly\s+portfolio\s+disclosure\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(20\d{2})",
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


def month_key_to_ym(month_key: str) -> tuple[int, int]:
    parts = month_key.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {month_key!r}")
    y, m = int(parts[0]), int(parts[1].zfill(2))
    if not (1 <= m <= 12):
        raise ValueError(f"Bad month in {month_key!r}")
    return y, m


def safe_filename(name: str) -> str:
    s = (name or "").strip() or "icici_monthly_portfolio.zip"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "icici_monthly_portfolio.zip"


def post_page(*, ctx: ssl.SSLContext, page: int, size: int = 50) -> dict:
    payload = {
        "categoryId": CATEGORY_ID,
        "schemeCategory": "",
        "userType": "Investor",
        "fileType": "All",
        "page": str(page),
        "size": str(size),
        "filter": [],
        "categoryName": "OTHERS",
    }
    headers = {**HEADERS_BASE, "requestAPIId": str(uuid.uuid4())}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def parse_ym(row: dict) -> tuple[int, int] | None:
    title_obj = row.get("title") or {}
    title = str(title_obj.get("text") or title_obj.get("code") or "")
    m = TITLE_YM_RE.search(title)
    if m:
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            return int(m.group(2)), mon
    # fallback to applicableMonth/fileDate epoch millis
    for k in ("applicableMonth", "fileDate"):
        val = row.get(k)
        if isinstance(val, (int, float)) and val > 0:
            dt = datetime.fromtimestamp(float(val) / 1000.0, tz=timezone.utc)
            return dt.year, dt.month
    return None


def fetch_rows(*, ctx: ssl.SSLContext) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        obj = post_page(ctx=ctx, page=page)
        data = ((obj.get("success") or {}).get("data") or {})
        chunk = data.get("files") or []
        if not isinstance(chunk, list):
            break
        rows.extend(chunk)
        if not data.get("isNext"):
            break
        page += 1
        if page > 100:
            break
    return rows


def download(url: str, *, ctx: ssl.SSLContext) -> tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={**HEADERS_BASE, "Accept": "*/*", "Referer": f"{BASE}/"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        body = resp.read()
        ctype = str(resp.headers.get("Content-Type") or "")
        return body, ctype


def normalize_url(raw: str) -> str:
    abs_url = raw if raw.startswith("http") else urljoin(BASE + "/", raw.lstrip("/"))
    # /downloads/Files/* redirects to dead archive.icicipruamc.com (no public DNS).
    # Same objects are live under /blob/downloads/Files/*.
    abs_url = abs_url.replace(
        "://www.icicipruamc.com/downloads/",
        "://www.icicipruamc.com/blob/downloads/",
    )
    parsed = urlparse(abs_url)
    safe_path = quote(unquote(parsed.path), safe="/%._-()")
    safe_query = quote(unquote(parsed.query), safe="=&%._-()")
    return urlunparse((parsed.scheme, parsed.netloc, safe_path, parsed.params, safe_query, parsed.fragment))


def is_probably_html(body: bytes, ctype: str) -> bool:
    if "text/html" in (ctype or "").lower():
        return True
    head = body[:256].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch ICICI monthly portfolio files")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS verification if your Python lacks CA certs",
    )
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "icici-prudential-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"POST {API_URL}", flush=True)
    try:
        rows = fetch_rows(ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_icici.py ... --insecure-ssl"
            ) from e
        raise
    print(f"  Indexed {len(rows)} row(s)", flush=True)

    by_month: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        ym = parse_ym(row)
        if ym is None or ym not in targets:
            continue
        by_month.setdefault(ym, []).append(row)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()

        selected = by_month.get(ym, [])
        # de-dup by URL
        uniq: list[dict] = []
        seen_urls: set[str] = set()
        for r in selected:
            raw = str(r.get("url") or "").strip()
            if not raw:
                continue
            abs_url = normalize_url(raw)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)
            r = dict(r)
            r["_abs_url"] = abs_url
            uniq.append(r)

        manifest: list[dict] = []
        print(f"\n{mk}: {len(uniq)} file(s)", flush=True)
        if not uniq:
            print("  No monthly portfolio file found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in uniq:
            title_obj = row.get("title") or {}
            title = str(title_obj.get("text") or title_obj.get("code") or "").strip()
            url = row["_abs_url"]
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"Monthly-Portfolio-Disclosure-{mk}.zip")
            rec = {
                "month": mk,
                "title": title,
                "source_api": API_URL,
                "download_url": url,
                "saved_as": fn,
            }
            try:
                body, ctype = download(url, ctx=ctx)
                if is_probably_html(body, ctype):
                    raise RuntimeError(
                        "download blocked/redirected (HTML response instead of file). "
                        "Try running from local browser network with valid cookies."
                    )
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
