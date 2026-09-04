#!/usr/bin/env python3
"""
LIC Mutual Fund — download **monthly portfolio** `.xlsx` for given YYYY-MM.

Source page (human):
  https://www.licmf.com/downloads/monthly-portfolio

**Per-scheme** (Monthly Portfolio tab):

  • POST /downloads/portfolio-filter-options — category → schemes → years → months
  • POST /downloads/portfolio-files — HTML with `/assets/downloads/portfolio/monthly/.../*.xlsx`

**Consolidated** (Consolidated Portfolio tab — equity/debt/etc. combined workbooks):

  • POST /downloads/consolidated-portfolio-filters — `id=639` (monthly), `filter=year|month`
  • POST /downloads/consolidated-portfolio-files — `id`, `month`, `year` → multiple `.xlsx` links
    under `/assets/pdf/statuary_disclosure_new/...`

`fund_name` for per-scheme POST is the **scheme display name** from the `<option>` text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _ssl_context(insecure: bool):
    if insecure:
        return ssl._create_unverified_context()
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

BASE = "https://www.licmf.com"
PAGE_URL = f"{BASE}/downloads/monthly-portfolio"
FILTER_URL = f"{BASE}/downloads/portfolio-filter-options"
FILES_URL = f"{BASE}/downloads/portfolio-files"
CONSOLIDATED_FILTERS_URL = f"{BASE}/downloads/consolidated-portfolio-filters"
CONSOLIDATED_FILES_URL = f"{BASE}/downloads/consolidated-portfolio-files"
# From <select class="consolidated_type"> on consolidated tab
CONSOLIDATED_MONTHLY_PORTFOLIO_ID = "639"
CONSOLIDATED_FORTNIGHTLY_PORTFOLIO_ID = "638"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": PAGE_URL,
    "Origin": BASE,
    "X-Requested-With": "XMLHttpRequest",
}


def post_form(url: str, fields: dict[str, str], *, ctx: ssl.SSLContext) -> str:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def get_page(url: str, *, ctx: ssl.SSLContext) -> str:
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": "text/html,*/*"}, method="GET")
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_fund_categories(html: str) -> list[str]:
    m = re.search(
        r'<select[^>]*class="fund_category"[^>]*>([\s\S]*?)</select>',
        html,
        re.I,
    )
    if not m:
        return [
            "Equity",
            "Hybrid",
            "Debt",
            "ETFs & Index Funds",
            "Solution Oriented Funds",
        ]
    block = m.group(1)
    out: list[str] = []
    for m2 in re.finditer(r'<option\s+value="([^"]*)"', block):
        v = m2.group(1).strip()
        if v:
            out.append(v)
    return out or [
        "Equity",
        "Hybrid",
        "Debt",
        "ETFs & Index Funds",
        "Solution Oriented Funds",
    ]


def parse_options_single_quoted(html: str) -> list[tuple[str, str]]:
    """<option value='CODE'>Label</option>"""
    rows: list[tuple[str, str]] = []
    for m in re.finditer(r"<option\s+value='([^']*)'>([^<]*)</option>", html, re.I):
        code, label = m.group(1).strip(), m.group(2).strip()
        if not code:
            continue
        rows.append((code, label))
    return rows


def parse_year_values(html: str) -> set[str]:
    return {v for v, _ in parse_options_single_quoted(html) if v.isdigit()}


def parse_month_values(html: str) -> set[int]:
    ms: set[int] = set()
    for v, _ in parse_options_single_quoted(html):
        if v.isdigit():
            ms.add(int(v))
    return ms


XLSX_HREF_RE = re.compile(
    r'href="(/assets/downloads/portfolio/monthly/\d+/\d+/[^"]+\.xlsx)"',
    re.I,
)
# Consolidated tab links live under /assets/pdf/... or similar
CONSOLIDATED_XLSX_HREF_RE = re.compile(r'href="(/assets/[^"]+\.xlsx)"', re.I)
CAPTION_RE = re.compile(
    r'class="caption"[^>]*>([^<]+)</',
    re.I,
)


def safe_filename(path: str) -> str:
    base = path.rsplit("/", 1)[-1].split("?")[0]
    return re.sub(r"[^\w.\-()]", "_", base).strip()[:200] or "download.xlsx"


def path_to_download_url(path: str) -> str:
    """Percent-encode each path segment (LIC consolidated hrefs often contain spaces)."""
    return BASE + "/".join(
        urllib.parse.quote(seg, safe="") if seg else "" for seg in path.split("/")
    )


def month_args_to_parts(months: list[str]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for raw in months:
        y, m = raw.strip().split("-", 1)
        out.append((int(y), int(m)))
    return out


def _ssl_fail_hint(insecure: bool, e: Exception) -> None:
    if not insecure and "CERTIFICATE_VERIFY_FAILED" in str(e).upper():
        raise SystemExit(
            f"{e}\n\n"
            "Retry with:  python3 scripts/fetch_lic.py ... --insecure-ssl\n"
            "Or fix macOS Python certs (Install Certificates.command) / use pip install certifi."
        ) from e


def discover_consolidated_month(
    *,
    ctx: ssl.SSLContext,
    portfolio_id: str,
    y: int,
    mon: int,
    sleep: float,
    seen_url: set[str],
) -> list[tuple[str, str, str, str, str]]:
    """
    Return rows (month_key, scheme_code, label, full_url, path) for one YYYY-MM.
    scheme_code is empty; label is caption text or derived from filename.
    """
    time.sleep(sleep)
    try:
        y_html = post_form(
            CONSOLIDATED_FILTERS_URL,
            {"id": portfolio_id, "filter": "year"},
            ctx=ctx,
        )
    except urllib.error.HTTPError as e:
        print(f"  consolidated years HTTP {e.code}", flush=True)
        return []
    if str(y) not in parse_year_values(y_html):
        return []

    time.sleep(sleep)
    try:
        m_html = post_form(
            CONSOLIDATED_FILTERS_URL,
            {
                "id": portfolio_id,
                "filter": "month",
                "year": str(y),
            },
            ctx=ctx,
        )
    except urllib.error.HTTPError as e:
        print(f"  consolidated {y} months HTTP {e.code}", flush=True)
        return []
    if mon not in parse_month_values(m_html):
        return []

    time.sleep(sleep)
    try:
        f_html = post_form(
            CONSOLIDATED_FILES_URL,
            {"id": portfolio_id, "month": str(mon), "year": str(y)},
            ctx=ctx,
        )
    except urllib.error.HTTPError as e:
        print(f"  consolidated {y}-{mon:02d} files HTTP {e.code}", flush=True)
        return []

    paths = [m.group(1) for m in CONSOLIDATED_XLSX_HREF_RE.finditer(f_html)]
    captions = [c.strip() for c in CAPTION_RE.findall(f_html)]
    mk = f"{y}-{mon:02d}"
    out: list[tuple[str, str, str, str, str]] = []
    for i, path in enumerate(paths):
        full = path_to_download_url(path)
        if full in seen_url:
            continue
        seen_url.add(full)
        label = captions[i] if i < len(captions) else path.rsplit("/", 1)[-1].replace(".xlsx", "")
        out.append((mk, "", label, full, path))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch LIC MF monthly portfolio xlsx files")
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
        help="Max files total across all months (0 = no cap)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.35,
        help="Seconds between POSTs (be polite)",
    )
    parser.add_argument(
        "--insecure-ssl",
        action="store_true",
        help="Disable TLS certificate verification (use if Python lacks CA bundle)",
    )
    parser.add_argument(
        "--scope",
        choices=("per-scheme", "consolidated", "both"),
        default="per-scheme",
        help="per-scheme: each scheme xlsx; consolidated: combined monthly workbooks; both",
    )
    parser.add_argument(
        "--consolidated-id",
        default=None,
        metavar="ID",
        help=(
            f"Consolidated portfolio type id (default {CONSOLIDATED_MONTHLY_PORTFOLIO_ID} = monthly; "
            f"{CONSOLIDATED_FORTNIGHTLY_PORTFOLIO_ID} = fortnightly). Numeric month=1..12 POSTed."
        ),
    )
    parser.add_argument(
        "--fortnightly",
        action="store_true",
        help=(
            f"Consolidated fortnightly debt portfolios "
            f"(id={CONSOLIDATED_FORTNIGHTLY_PORTFOLIO_ID}, scope=consolidated)"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.fortnightly:
        if args.scope == "per-scheme":
            args.scope = "consolidated"
        if args.consolidated_id is None:
            args.consolidated_id = CONSOLIDATED_FORTNIGHTLY_PORTFOLIO_ID
    if args.consolidated_id is None:
        args.consolidated_id = CONSOLIDATED_MONTHLY_PORTFOLIO_ID

    ctx = _ssl_context(args.insecure_ssl)
    want_parts = month_args_to_parts(args.months)
    want_set = set(want_parts)
    amc_dir = args.root / "amcs" / "lic-mutual-fund"

    # (yyyy-mm, scheme_code, fund_name, download_url, path_suffix, scope)
    pending: list[tuple[str, str, str, str, str, str]] = []
    seen_url: set[str] = set()

    want_consolidated = args.scope in ("consolidated", "both")
    want_per_scheme = args.scope in ("per-scheme", "both")

    if want_consolidated:
        print(
            f"Consolidated (id={args.consolidated_id}) for {len(want_parts)} month(s) …",
            flush=True,
        )
        try:
            for y, mon in want_parts:
                rows = discover_consolidated_month(
                    ctx=ctx,
                    portfolio_id=str(args.consolidated_id),
                    y=y,
                    mon=mon,
                    sleep=args.sleep,
                    seen_url=seen_url,
                )
                for mk, _code, label, full, path in rows:
                    pending.append((mk, "CONSOLIDATED", label, full, path, "consolidated"))
                if rows:
                    print(f"  {y}-{mon:02d}: {len(rows)} consolidated file(s)", flush=True)
        except urllib.error.URLError as e:
            _ssl_fail_hint(args.insecure_ssl, e)
            raise

    categories: list[str] = []
    if want_per_scheme:
        print(f"GET {PAGE_URL} …", flush=True)
        try:
            page_html = get_page(PAGE_URL, ctx=ctx)
        except urllib.error.URLError as e:
            _ssl_fail_hint(args.insecure_ssl, e)
            raise
        categories = parse_fund_categories(page_html)
        print(f"Categories: {categories}", flush=True)

    pending_scheme: list[tuple[str, str, str, str, str, str]] = []

    for cat in categories:
        time.sleep(args.sleep)
        try:
            sch_html = post_form(
                FILTER_URL,
                {"fund_category": cat, "filter": "category"},
                ctx=ctx,
            )
        except urllib.error.HTTPError as e:
            print(f"  category {cat!r}: HTTP {e.code}", flush=True)
            continue
        schemes = parse_options_single_quoted(sch_html)
        schemes = [(c, n) for c, n in schemes if c.lower() not in ("", "scheme name")]
        print(f"  {cat}: {len(schemes)} scheme(s)", flush=True)

        for scheme_code, fund_name in schemes:
            time.sleep(args.sleep)
            try:
                y_html = post_form(
                    FILTER_URL,
                    {
                        "scheme_code": scheme_code,
                        "filter": "fund_name",
                        "type": "monthly_portfolio",
                    },
                    ctx=ctx,
                )
            except urllib.error.HTTPError as e:
                print(f"    {scheme_code}: years HTTP {e.code}", flush=True)
                continue
            years = parse_year_values(y_html)

            for y, mon in want_parts:
                if str(y) not in years:
                    continue
                time.sleep(args.sleep)
                try:
                    m_html = post_form(
                        FILTER_URL,
                        {
                            "year": str(y),
                            "filter": "year",
                            "type": "monthly_portfolio",
                            "scheme_code": scheme_code,
                        },
                        ctx=ctx,
                    )
                except urllib.error.HTTPError as e:
                    print(f"    {scheme_code} {y}: months HTTP {e.code}", flush=True)
                    continue
                months_avail = parse_month_values(m_html)
                if mon not in months_avail:
                    continue

                time.sleep(args.sleep)
                try:
                    f_html = post_form(
                        FILES_URL,
                        {
                            "scheme_code": scheme_code,
                            "fund_name": fund_name,
                            "type": "monthly_portfolio",
                            "month": str(mon),
                            "year": str(y),
                        },
                        ctx=ctx,
                    )
                except urllib.error.HTTPError as e:
                    print(f"    {scheme_code} {y}-{mon:02d}: files HTTP {e.code}", flush=True)
                    continue
                mhref = XLSX_HREF_RE.search(f_html)
                if not mhref:
                    continue
                path = mhref.group(1)
                full = path_to_download_url(path)
                if full in seen_url:
                    continue
                seen_url.add(full)
                mk = f"{y}-{mon:02d}"
                pending_scheme.append((mk, scheme_code, fund_name, full, path, "per_scheme"))

    if want_per_scheme and args.limit and len(pending_scheme) > args.limit:
        pending_scheme = pending_scheme[: args.limit]
        print(f"Per-scheme limited to {args.limit} file(s)", flush=True)

    pending.extend(pending_scheme)

    print(f"\nDiscovered {len(pending)} file URL(s) total", flush=True)

    by_month: dict[str, list[tuple[str, str, str, str, str]]] = {k: [] for k in args.months}
    for mk, code, fname, full, path, scope in pending:
        if (int(mk[:4]), int(mk[5:7])) not in want_set:
            continue
        by_month.setdefault(mk, []).append((full, path, code, fname, scope))

    for mk in args.months:
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        batch = by_month.get(mk, [])
        batch.sort(key=lambda t: (0 if t[4] == "consolidated" else 1, (t[3] or "").lower()))
        print(f"\n{mk}: {len(batch)} file(s)", flush=True)
        manifest: list[dict] = []
        for full, path, code, fund_name, scope in batch:
            fn = safe_filename(path)
            rec = {
                "month": mk,
                "scope": scope,
                "scheme_code": code,
                "scheme_name": fund_name,
                "download_url": full,
                "saved_as": fn,
            }
            if args.dry_run:
                print(f"  dry-run {fn}", flush=True)
                manifest.append({**rec, "sha256": "", "dry_run": True})
                continue
            try:
                req = urllib.request.Request(full, headers={**HEADERS, "Referer": PAGE_URL}, method="GET")
                with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                    body = resp.read()
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fn).write_bytes(body)
                manifest.append({**rec, "sha256": h})
                print(f"  OK {fn} ({len(body)} bytes)", flush=True)
            except Exception as e:
                manifest.append({**rec, "sha256": "", "error": str(e)})
                print(f"  ERR {fn}: {e}", flush=True)
            time.sleep(args.sleep)

        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
