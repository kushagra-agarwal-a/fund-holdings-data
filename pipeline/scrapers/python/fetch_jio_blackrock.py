#!/usr/bin/env python3
"""
Jio BlackRock Mutual Fund - download monthly portfolio files for YYYY-MM.

Source page:
  https://www.jioblackrockamc.com/statutory-disclosure/disclosures/monthly-portfolio-disclosure

Data source:
  The page uses a Next.js server action (`getDisclosureL3Data`) to fetch
  month-filtered rows on demand.
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
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

BASE = "https://www.jioblackrockamc.com"
PRIMARY_PAGE_URL = f"{BASE}/statutory-disclosure/disclosures/monthly-portfolio-disclosure"
FORTNIGHTLY_PAGE_URL = f"{BASE}/statutory-disclosure/disclosures/fortnightly-portfolio-disclosure"
PAGE_URL = PRIMARY_PAGE_URL
SOURCE_L2_ID = "monthly-portfolio-disclosure"
FORTNIGHTLY_L2_ID = "fortnightly-portfolio-disclosure"
NEXT_ACTION_ID = "6096790a6821baed03f386cd22554768d5f0bad49d"
MONTH_NUM_TO_SHORT = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}
TITLE_DM_RE = re.compile(
    r"(?i)(?:monthly|fortnightly)[- ]portfolio[- ](\d{2})[- ](\d{2})[- ](\d{4})"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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


def safe_filename(name: str) -> str:
    s = (name or "").strip() or "jio_blackrock_monthly_portfolio.pdf"
    s = re.sub(r"[^\w.\-() ]+", "_", s).strip("._ ")
    return s[:220] or "jio_blackrock_monthly_portfolio.pdf"


def path_to_download_url(url: str) -> str:
    p = urlparse(url)
    safe_path = "/".join(quote(seg, safe="%") for seg in p.path.split("/"))
    return urlunparse((p.scheme, p.netloc, safe_path, p.params, p.query, p.fragment))


def fetch_html(url: str, *, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fiscal_year_for_month(year: int, month: int) -> str:
    # Indian FY: Apr-Mar. Jan/Feb/Mar belong to previous FY start year.
    start = year if month >= 4 else year - 1
    return f"FI{start}-{start + 1}"


def parse_as_of(as_of: str) -> tuple[int, int, int] | None:
    parts = as_of.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return y, m, d


def row_matches_ym(row: dict, year: int, month: int, *, day: int | None = None) -> bool:
    date_s = str(row.get("date") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_s):
        y, m, d = int(date_s[0:4]), int(date_s[5:7]), int(date_s[8:10])
        if y == year and m == month and (day is None or d == day):
            return True
    title = str(row.get("title") or "").strip()
    m = TITLE_DM_RE.search(title)
    if not m:
        return False
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y != year or mo != month:
        return False
    return day is None or d == day


def _extract_json_lines_from_rsc(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        _, payload = line.split(":", 1)
        payload = payload.strip()
        if not payload.startswith("{"):
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def discover_next_action_id(html: str, *, ctx: ssl.SSLContext) -> str | None:
    """
    Resolve current Next.js server action id for getDisclosureL3Data from page chunk.
    This id changes across deploys, so hardcoding causes intermittent 404s.
    """
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html, re.I)
    candidates: list[str] = []
    for src in srcs:
        s = src.lower()
        if "statutory-disclosure" in s and "/page-" in s and s.endswith(".js"):
            candidates.append(urljoin(BASE, src))
    for chunk_url in candidates:
        req = urllib.request.Request(chunk_url, headers=HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        m = re.search(
            r'"([a-f0-9]{40,64})"[\s\S]{0,1200}"getDisclosureL3Data"',
            text,
            re.S,
        )
        if m:
            return m.group(1)
    return None


def fetch_rows_via_server_action(
    *,
    ctx: ssl.SSLContext,
    next_action_id: str,
    l2_id: str,
    fiscal_year: str,
    month_name: str,
    page_url: str,
) -> list[dict]:
    body = json.dumps([l2_id, {"year": fiscal_year, "month": month_name}]).encode("utf-8")
    req = urllib.request.Request(
        page_url,
        data=body,
        method="POST",
        headers={
            **HEADERS,
            "Accept": "text/x-component, */*",
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": BASE,
            "Referer": page_url,
            "Next-Action": next_action_id,
        },
    )
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        text = resp.read().decode("utf-8", errors="ignore")
    objs = _extract_json_lines_from_rsc(text)
    for obj in objs:
        data = obj.get("data")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    return []


def download(url: str, *, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(
        url,
        headers={**HEADERS, "Accept": "*/*", "Referer": PAGE_URL},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Jio BlackRock monthly portfolio disclosure files"
    )
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
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help="Use fortnightly-portfolio-disclosure page + L2 id",
    )
    parser.add_argument(
        "--as-of",
        dest="as_of",
        default="",
        help="Calendar as-of YYYY-MM-DD (fortnightly: keep only matching title/date day)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ctx = _ssl_context(args.insecure_ssl)
    amc_dir = args.root / "amcs" / "jio-blackrock-mutual-fund"
    targets = {month_key_to_ym(mk): mk for mk in args.months}

    source_tag = FORTNIGHTLY_L2_ID if args.fortnightly else SOURCE_L2_ID
    source_page = FORTNIGHTLY_PAGE_URL if args.fortnightly else PRIMARY_PAGE_URL
    print(f"GET {source_page}", flush=True)
    # Warm session and cookies once before action POST calls.
    try:
        page_html = fetch_html(source_page, ctx=ctx)
    except urllib.error.URLError as e:
        if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
            raise SystemExit(
                f"{e}\n\nRetry with:  python3 scripts/fetch_jio_blackrock.py ... --insecure-ssl"
            ) from e
        raise
    action_id = discover_next_action_id(page_html, ctx=ctx) or NEXT_ACTION_ID
    if action_id != NEXT_ACTION_ID:
        print(f"Using discovered Next-Action id: {action_id}", flush=True)
    else:
        print("Using fallback Next-Action id (could fail if site changed)", flush=True)

    as_of_day: int | None = None
    if args.as_of:
        parsed = parse_as_of(args.as_of)
        if not parsed:
            raise SystemExit(f"Invalid --as-of (expected YYYY-MM-DD): {args.as_of!r}")
        as_of_day = parsed[2]

    for ym, mk in targets.items():
        fy = fiscal_year_for_month(ym[0], ym[1])
        month_name = MONTH_NUM_TO_SHORT[ym[1]]
        try:
            rows_fy_month = fetch_rows_via_server_action(
                ctx=ctx,
                next_action_id=action_id,
                l2_id=source_tag,
                fiscal_year=fy,
                month_name=month_name,
                page_url=source_page,
            )
            # Reconciliation: month-only query returns the missing Jan row that
            # fiscal-year+month misses on this backend. Keep this generic.
            rows_month_only = fetch_rows_via_server_action(
                ctx=ctx,
                next_action_id=action_id,
                l2_id=source_tag,
                fiscal_year="",
                month_name=month_name,
                page_url=source_page,
            )
            # Year-only is a secondary source for robustness.
            rows_fy_only = fetch_rows_via_server_action(
                ctx=ctx,
                next_action_id=action_id,
                l2_id=source_tag,
                fiscal_year=fy,
                month_name="",
                page_url=source_page,
            )
        except urllib.error.URLError as e:
            if not args.insecure_ssl and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
                raise SystemExit(
                    f"{e}\n\nRetry with:  python3 scripts/fetch_jio_blackrock.py ... --insecure-ssl"
                ) from e
            raise

        selected: list[dict] = []
        seen_urls: set[str] = set()
        merged_rows = [*rows_fy_month, *rows_month_only, *rows_fy_only]
        for row in merged_rows:
            if not row_matches_ym(row, ym[0], ym[1], day=as_of_day if args.fortnightly else None):
                continue
            file_obj = row.get("file") or {}
            raw_url = str(file_obj.get("url") or "").strip()
            if not raw_url or raw_url in seen_urls:
                continue
            seen_urls.add(raw_url)
            selected.append(row)

        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file():
                p.unlink()

        manifest: list[dict] = []
        print(
            f"\n{mk}: {len(selected)} file(s) via action reconciliation "
            f"(fy={fy}, month={month_name})",
            flush=True,
        )
        if not selected:
            print("  No portfolio disclosure file found for this month.", flush=True)
            (out_dir / "manifest.json").write_text("[]\n", encoding="utf-8")
            continue

        for row in selected:
            title = str(row.get("title") or "").strip()
            uid = str(row.get("uid") or "").strip()
            date = str(row.get("date") or "").strip()
            file_obj = row.get("file") or {}
            raw_url = str(file_obj.get("url") or "").strip()
            ext = str(file_obj.get("ext") or "").strip()
            url = path_to_download_url(raw_url)

            raw_name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            if not raw_name and ext:
                raw_name = f"portfolio_insights_{mk}{ext}"
            fn = safe_filename(raw_name or f"monthly_portfolio_disclosure_{mk}.pdf")
            rec = {
                "month": mk,
                "title": title,
                "uid": uid,
                "date": date,
                "source_page": source_page,
                "source_l2_id": source_tag,
                "download_url": url,
                "saved_as": fn,
            }
            if args.dry_run:
                print(f"  dry-run {fn}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
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
