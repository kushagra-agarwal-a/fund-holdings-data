#!/usr/bin/env python3
"""
Helios Mutual Fund — download monthly portfolio files for given YYYY-MM.

Source page (WordPress static HTML with direct links):
  https://www.heliosmf.in/portfolio-disclosure/

Monthly portfolio links are direct `.xls/.xlsx` URLs under `wp-content/uploads/...`.
We parse month from filename patterns like:
  - ...-28th-February-2026.xlsx
  - ...-as-on-31st-January-2026.xlsx
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

PAGE_URL = "https://www.heliosmf.in/portfolio-disclosure/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

LINK_RE = re.compile(
    r'href="(https://www\.heliosmf\.in/wp-content/uploads/[^"]+\.(?:xlsx|xls)(?:\?[^"]*)?)"',
    re.I,
)

# captures "31st-January-2026" OR "31st January 2026" etc.
DATE_TOKEN_RE = re.compile(
    r'(\d{1,2})(?:st|nd|rd|th)[\s\-_]+([A-Za-z]+)[\s\-_]+(\d{4})',
    re.I,
)


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    base = path.rsplit('/', 1)[-1]
    base = unquote(base.split('?')[0])
    if not base or base in ('.', '..'):
        base = 'download.xlsx'
    return re.sub(r'[^\w.\-() ]', '_', base).strip()[:200] or 'download.xlsx'


def fetch_html() -> str:
    req = Request(PAGE_URL, headers=HEADERS)
    with urlopen(req, timeout=120) as resp:
        return resp.read().decode('utf-8', 'ignore')


def url_to_month_key(url: str) -> str | None:
    name = unquote(urlparse(url).path.rsplit('/', 1)[-1])
    m = DATE_TOKEN_RE.search(name)
    if not m:
        return None
    d, mon, y = m.group(1), m.group(2), m.group(3)
    for fmt in ('%d %B %Y', '%d %b %Y'):
        try:
            dt = datetime.strptime(f'{d} {mon} {y}', fmt)
            return f'{dt.year:04d}-{dt.month:02d}'
        except ValueError:
            continue
    return None


def extract_rows(html: str) -> list[dict]:
    urls = sorted(set(LINK_RE.findall(html)))
    rows = []
    for u in urls:
        name = unquote(urlparse(u).path.rsplit('/', 1)[-1])
        blob = name.lower()
        mk = url_to_month_key(u)
        if not mk:
            continue
        # Older files explicitly include 'Monthly Portfolio'; latest files may be like
        # 'Helios-<Scheme>-28th-February-2026.xlsx' without that token.
        if 'fortnightly' in blob:
            continue
        if 'portfolio' not in blob and 'monthly' not in blob and 'monthtly' not in blob:
            if not blob.startswith('helios-'):
                continue
        rows.append({'month_key': mk, 'download_url': u, 'name': name})
    return rows


def download(url: str) -> bytes:
    req = Request(url, headers={'User-Agent': HEADERS['User-Agent'], 'Accept': '*/*', 'Referer': PAGE_URL})
    with urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> None:
    parser = argparse.ArgumentParser(description='Fetch Helios monthly portfolio files')
    parser.add_argument('--months', nargs='+', default=['2026-01', '2026-02'], help='YYYY-MM')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent, help='mf-monthly-holdings root')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    amc_dir = args.root / 'amcs' / 'helios-mutual-fund'

    print(f'GET {PAGE_URL} …')
    html = fetch_html()
    rows = extract_rows(html)
    print(f'  … parsed {len(rows)} monthly portfolio link(s)')

    by_month: dict[str, list[dict]] = {k: [] for k in args.months}
    for r in rows:
        mk = r.get('month_key')
        if mk in by_month:
            by_month[mk].append(r)

    for mk in args.months:
        out_dir = amc_dir / mk
        out_dir.mkdir(parents=True, exist_ok=True)

        batch = by_month.get(mk) or []
        print(f'\n{mk}: {len(batch)} file(s)')
        manifest: list[dict] = []

        if not batch:
            print('  No matching monthly rows for this month.')

        for i, row in enumerate(batch, 1):
            url = row['download_url']
            fname = safe_filename(url)
            rec = {
                'month': mk,
                'download_url': url,
                'saved_as': fname,
                'title': row.get('name'),
            }
            if args.dry_run:
                print(f'  [{i}] {fname}')
                manifest.append({**rec, 'sha256': '', 'dry_run': True})
                continue
            try:
                body = download(url)
                h = hashlib.sha256(body).hexdigest()
                (out_dir / fname).write_bytes(body)
                manifest.append({**rec, 'sha256': h})
                print(f'  [{i}] OK {fname} ({len(body)} bytes)')
            except Exception as e:
                manifest.append({**rec, 'sha256': '', 'error': str(e)})
                print(f'  [{i}] ERR {fname}: {e}')

        (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print(f'Wrote {out_dir / "manifest.json"}')


if __name__ == '__main__':
    main()
