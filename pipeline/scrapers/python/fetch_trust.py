#!/usr/bin/env python3
"""
TRUST Mutual Fund — download monthly portfolio disclosures for given YYYY-MM.

Public page:
  https://www.trustmf.com/disclosures

Runtime config:
  https://www.trustmf.com/config.json
which points API base to:
  https://www.trustmf.com/api/api/

API used by the SPA:
  POST /api/api/Trust/GetData
  {
    "systemQueryFileName": "disclosuresweb.xml",
    "tagName": "GetDisclosureByType",
    "replaceField": "_slug_",
    "replaceValue": "portfolio-monthly-disclosure",
    ...
  }
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse

BASE = "https://www.trustmf.com"
PAGE_URL = f"{BASE}/disclosures"
API_URL = f"{BASE}/api/api/Trust/GetData"
DISCLOSURE_SLUG_MONTHLY = "portfolio-monthly-disclosure"
DISCLOSURE_SLUG_FORTNIGHTLY = "portfolio-fortnightly-disclosure"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

POST_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": BASE,
    "Referer": f"{BASE}/",
}

TITLE_DATE_RE = re.compile(r"as on (\d{2})\.(\d{2})\.(\d{4})", re.I)


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
    s = (name or "").strip() or "trust_monthly_portfolio.xlsx"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:200] or "trust_monthly_portfolio.xlsx"


def parse_month_from_title(title: str) -> tuple[int, int] | None:
    m = TITLE_DATE_RE.search(title or "")
    if not m:
        return None
    # dd.mm.yyyy -> yyyy, mm
    return int(m.group(3)), int(m.group(2))


def make_opener(*, ctx: ssl.SSLContext) -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ctx),
    )


def bootstrap_session(opener: urllib.request.OpenerDirector) -> None:
    req = urllib.request.Request(f"{BASE}/", headers=HEADERS, method="GET")
    with opener.open(req, timeout=60):
        pass


def fetch_disclosure_rows(opener: urllib.request.OpenerDirector, *, fortnightly: bool = False) -> list[dict]:
    candidates = (
        [
            "portfolio-fortnightly-disclosure",
            "fortnightly-portfolio-disclosure",
            "portfolio-fortnightly",
            "debt-fortnightly-portfolio-disclosure",
            "midmonth-portfolio-disclosure",
            DISCLOSURE_SLUG_FORTNIGHTLY,
        ]
        if fortnightly
        else [DISCLOSURE_SLUG_MONTHLY]
    )
    best: list[dict] = []
    for slug in candidates:
        payload = {
            "systemQueryFileName": "disclosuresweb.xml",
            "tagName": "GetDisclosureByType",
            "searchField": "",
            "searchValue": "",
            "sortField": "uploaddate",
            "sortDirection": "DESC",
            "replaceField": "_slug_",
            "replaceValue": slug,
        }
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=POST_HEADERS,
            method="POST",
        )
        with opener.open(req, timeout=120) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        obj = json.loads(body) if body else {}
        rows = [r for r in (obj.get("resultSetArray") or []) if isinstance(r, dict)]
        print(f"  slug={slug} -> {len(rows)} row(s)", flush=True)
        if len(rows) > len(best):
            best = rows
        if rows:
            return rows
    return best


def pick_download_url(row: dict) -> str:
    u = str(row.get("fileurl") or "").strip()
    if u:
        return u
    # Fallback: some rows keep direct URL in slug.
    s = str(row.get("slug") or "").strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return ""


def path_to_download_url(url: str) -> str:
    """
    Encode path segments (for spaces etc.) while preserving scheme/host/query.
    """
    p = urlparse(url)
    safe_path = "/".join(quote(seg, safe="") for seg in p.path.split("/"))
    return urlunparse((p.scheme, p.netloc, safe_path, p.params, p.query, p.fragment))


def download(opener: urllib.request.OpenerDirector, url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with opener.open(req, timeout=180) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch TRUST MF monthly portfolio disclosures")
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
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    opener = make_opener(ctx=ctx)
    amc_dir = args.root / "amcs" / "trust-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    print(f"GET {BASE}/ then POST {API_URL} …", flush=True)
    try:
        bootstrap_session(opener)
        rows = fetch_disclosure_rows(opener, fortnightly=args.fortnightly)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_trust.py ... --insecure-ssl"
            ) from e
        raise
    kind = "fortnightly" if args.fortnightly else "monthly"
    print(f"  Indexed {len(rows)} {kind} disclosure row(s)", flush=True)

    by_month: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        ym = parse_month_from_title(str(row.get("title") or ""))
        if ym is None or ym not in targets:
            continue
        by_month.setdefault(ym, []).append(row)

    for ym, mk in targets.items():
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()
        manifest: list[dict] = []
        selected = by_month.get(ym, [])
        print(f"\n{mk}: {len(selected)} file(s)", flush=True)
        if not selected:
            print("  No monthly disclosure row found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = str(row.get("title") or "").strip()
            url = path_to_download_url(pick_download_url(row))
            if not url:
                manifest.append(
                    {
                        "month": mk,
                        "title": title,
                        "download_url": "",
                        "saved_as": "",
                        "sha256": "",
                        "error": "missing fileurl/slug URL",
                    }
                )
                print(f"  ERR {title}: missing file URL", flush=True)
                continue
            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            fn = safe_filename(raw_name or f"trust_monthly_{mk}.xlsx")
            rec = {
                "month": mk,
                "title": title,
                "download_url": url,
                "saved_as": fn,
            }
            try:
                body = download(opener, url)
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
