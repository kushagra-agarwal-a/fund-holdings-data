#!/usr/bin/env python3
"""
Motilal Oswal Mutual Fund — download month-end / scheme portfolio files for YYYY-MM.

Live AEM search API (from downloads SPA):
  GET /content/aem-cloud-dept-backend-motilal-oswal/api/search-documents.json
    ?year=&category=month%20end%20portfolio&month=&type=mf

Results include paths like:
  /content/dam/motilal-mf/downloads/mf/month-end-portfolio/2026/jul/scheme portfolio details june 2026.xlsx

Filter to monthly scheme portfolio details for the requested month (skip fortnightly).
With `--fortnightly`, keep fortnightly rows instead and skip pure monthly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote, urljoin
from urllib.request import Request, urlopen

BASE = "https://www.motilaloswalmf.com"
API = (
    f"{BASE}/content/aem-cloud-dept-backend-motilal-oswal/api/search-documents.json"
    "?year=&category=month%20end%20portfolio&month=&type=mf"
)
PAGE = f"{BASE}/downloads/scheme-portfolio-details"

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
    r"scheme\s+portfolio\s+details\s+"
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(20\d{2})",
    re.I,
)


def parse_month(mk: str) -> tuple[int, int]:
    y, m = mk.split("-")
    return int(y), int(m)


def safe_filename(path: str, title: str = "") -> str:
    base = unquote(path.rstrip("/").split("/")[-1].split("?")[0]).strip()
    if not base:
        base = (title or "motilal.xlsx").strip()
    if not re.search(r"\.(xlsx?|xlsb)$", base, re.I):
        base = base + ".xlsx"
    return re.sub(r"[^\w.\-() ]+", "_", base)[:220]


def load_results() -> list[dict]:
    req = Request(
        API,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": PAGE,
        },
    )
    with urlopen(req, timeout=120) as resp:
        obj = json.loads(resp.read().decode("utf-8", "ignore"))
    rows = obj.get("results") or []
    if not isinstance(rows, list):
        raise RuntimeError("unexpected search-documents shape")
    return rows


def parse_ym(row: dict, *, fortnightly: bool = False) -> tuple[int, int] | None:
    title = str(row.get("title") or "")
    path = str(row.get("path") or "")
    blob = f"{title} {unquote(path)}"
    has_fn = bool(re.search(r"fortnight", blob, re.I))
    if fortnightly:
        if not has_fn:
            return None
    elif has_fn:
        return None
    m = TITLE_YM_RE.search(blob)
    if not m:
        # filename style: scheme portfolio details june 2026.xlsx
        m = re.search(
            r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
            r"[\s_\-]+(20\d{2})",
            blob,
            re.I,
        )
        if not m:
            return None
        if not fortnightly and "scheme portfolio" not in blob.lower():
            return None
    mon = MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    return int(m.group(2)), mon


def abs_url(path: str) -> str:
    if path.startswith("http"):
        # encode spaces in path segments
        return path
    # quote each segment but keep slashes
    parts = path.split("/")
    enc = "/".join(quote(p, safe="") if p else "" for p in parts)
    return urljoin(BASE + "/", enc.lstrip("/"))


def download(url: str) -> bytes:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": PAGE},
    )
    with urlopen(req, timeout=180) as resp:
        return resp.read()


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Motilal Oswal monthly portfolios")
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--fortnightly",
        action="store_true",
        help="Keep fortnightly rows instead of skipping them",
    )
    args = ap.parse_args()

    targets = {parse_month(mk): mk for mk in args.months}
    print(f"GET {API}", flush=True)
    rows = load_results()
    print(f"  indexed {len(rows)} search hit(s)", flush=True)

    by_month: dict[str, list[dict]] = {mk: [] for mk in args.months}
    for row in rows:
        ym = parse_ym(row, fortnightly=args.fortnightly)
        if ym is None or ym not in targets:
            continue
        path = str(row.get("path") or "").strip()
        if not path:
            continue
        by_month[targets[ym]].append(row)

    amc_dir = args.root / "amcs" / "motilal-oswal-mutual-fund"
    for mk in args.months:
        selected = by_month.get(mk) or []
        seen = set()
        uniq = []
        for row in selected:
            path = row["path"]
            if path in seen:
                continue
            seen.add(path)
            uniq.append(row)

        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = []
        print(f"\n{mk}: {len(uniq)} file(s)", flush=True)
        for row in uniq:
            path = row["path"]
            url = abs_url(path)
            fname = safe_filename(path, row.get("title") or "")
            rec = {
                "month": mk,
                "download_url": url,
                "saved_as": fname,
                "title": row.get("title"),
                "publishDate": row.get("publishDate"),
                "path": path,
            }
            if args.dry_run:
                print(f"  dry-run {fname}", flush=True)
                manifest.append({**rec, "dry_run": True})
                continue
            try:
                body = download(url)
                (out_dir / fname).write_bytes(body)
                print(f"  OK {fname} ({len(body)} bytes)", flush=True)
                manifest.append(
                    {**rec, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                )
            except Exception as e:
                print(f"  ERR {fname}: {e}", flush=True)
                manifest.append({**rec, "error": str(e)})
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"  wrote {out_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
