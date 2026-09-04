#!/usr/bin/env python3
"""
AMC Disclosure File Link Extractor
=====================================
Per-AMC config-driven system. Each AMC has a "profile" that teaches
the parser how to navigate that AMC's specific HTML structure.

Two extraction strategies are supported:
  "html"  — CSS selector based: find a section by ID/text, collect <a> tags
  "json"  — Parse a JS variable (e.g. `const verticals = [...]`) embedded in
             the page's <script> tags, navigate the JSON to find the target section.

To add a new AMC: add an entry to AMC_PROFILES below.

Usage:
    python extract_disclosure_links.py                  # all AMCs
    python extract_disclosure_links.py 360_ONE_Mutual_Fund  # single AMC by key
    python extract_disclosure_links.py --latest-only    # only most recent file per AMC

Output:
    {AMC_KEY}_disclosure_links.csv   — per-AMC CSV with all monthly portfolio links

Requirements:
    pip install -r requirements-extractor.txt
    # or: pip install httpx beautifulsoup4
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

# ── Per-AMC Profiles ──────────────────────────────────────────────────────────

AMC_PROFILES = {

    # ── 360 ONE Mutual Fund ───────────────────────────────────────────────────
    # AMFI page is an iframe shell → real content at archive.iiflmf.com
    # Monthly Portfolio is under accordion div id="collapse0"
    # Year headings are <h4>2026</h4> etc, files are <a href="...xlsx"> in <li>
    "360_ONE_Mutual_Fund": {
        "strategy": "html",
        "fetch_url": "https://archive.iiflmf.com/downloads/disclosures/",
        "iframe": False,
        "section_id": "collapse0",
        "link_selector": "a[href]",
        "base_url": "https://archive.iiflmf.com",
        "ext_filter": ["xls", "xlsx", "pdf"],
        "notes": "AMFI page wraps iframe. Monthly=collapse0, Fortnightly=collapse1.",
    },

    # ── Abakkus Mutual Fund ───────────────────────────────────────────────────
    # Self-contained page. All data is in a `const verticals = [...]` JSON blob
    # embedded in a <script> tag. We parse the JSON directly.
    # Target vertical title: "Monthly Portfolio Disclosures"
    # Each item has downloadMedia.url (relative path like /uploads/...)
    "Abakkus_Mutual_Fund": {
        "strategy": "json",
        "fetch_url": "https://www.abakkusmf.com/statutory-disclosures.html",
        "iframe": False,
        "js_var": "verticals",  # name of the JS variable to extract
        "section_title": "Monthly Portfolio Disclosures",  # vertical title to find
        "base_url": "https://www.abakkusmf.com",
        "ext_filter": ["xls", "xlsx", "pdf"],
        "notes": (
            "Data in <script>const verticals=[...]</script>. "
            "Tab 16 = Monthly Portfolio Disclosures. "
            "Items have downloadMedia.url (relative /uploads/...)."
        ),
    },

    # ── Template for next AMC ─────────────────────────────────────────────────
    # "Next_AMC": {
    #     "strategy"   : "html",          # or "json"
    #     "fetch_url"  : "https://...",
    #     "iframe"     : False,
    #     # if html:
    #     "section_id" : None,
    #     "link_selector": "a[href]",
    #     "base_url"   : "https://...",
    #     "ext_filter" : ["xls", "xlsx", "pdf"],
    #     # if json:
    #     # "js_var"        : "verticals",
    #     # "section_title" : "Monthly Portfolio Disclosures",
    #     "notes"      : "",
    # },
}


# ── HTTP ──────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch(url: str) -> str:
    with httpx.Client(headers=HEADERS, timeout=25, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


def resolve_iframe(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe")
    if iframe and iframe.get("src"):
        src = iframe["src"]
        return src if src.startswith("http") else urljoin(base_url, src)
    return None


# ── Strategy A: HTML scraping ─────────────────────────────────────────────────


def extract_html(amc_key: str, p: dict, latest_only: bool) -> list[dict]:
    url = p["fetch_url"]
    html = fetch(url)

    if p.get("iframe"):
        src = resolve_iframe(html, url)
        if src:
            print(f"  → iframe → {src}")
            url, html = src, fetch(src)

    soup = BeautifulSoup(html, "html.parser")
    base = p.get("base_url", url.rstrip("/"))
    ext_filter = [e.lower() for e in p.get("ext_filter", [])]

    # Locate section
    section_id = p.get("section_id")
    container = soup.find(id=section_id) if section_id else soup
    if not container:
        # fallback: find heading matching text
        for tag in soup.find_all(["h2", "h3", "h4", "a", "button", "div"]):
            if "monthly portfolio" in tag.get_text(strip=True).lower():
                container = tag.find_next_sibling() or tag.parent
                break
    if not container:
        container = soup
        print(f"  ⚠ Section '{section_id}' not found — scanning full page")

    # Walk elements tracking year headings
    results = []
    current_year = None
    for el in container.find_all(["h3", "h4", "h5", "li", "a"]):
        if el.name in ("h3", "h4", "h5"):
            text = el.get_text(strip=True)
            if re.match(r"^20\d{2}$", text):
                current_year = int(text)
            continue
        if el.name == "a" and el.get("href"):
            href = el["href"]
            ext = href.rsplit(".", 1)[-1].lower().split("?")[0]
            if ext_filter and ext not in ext_filter:
                continue
            if not href.startswith("http"):
                href = urljoin(base, href)
            label = (el.get("title") or el.get_text(strip=True) or "").strip()
            results.append(
                {
                    "amc": amc_key,
                    "type": "monthly_portfolio",
                    "year": current_year,
                    "label": label,
                    "url": href,
                    "ext": ext,
                }
            )

    if latest_only and results:
        results = [results[0]]
    return results


# ── Strategy B: JSON-in-script parsing ───────────────────────────────────────


def extract_json(amc_key: str, p: dict, latest_only: bool) -> list[dict]:
    url = p["fetch_url"]
    html = fetch(url)

    # Find `const <js_var> = [...]` or `var <js_var> = [...]` in script tags
    js_var = p["js_var"]
    # Match the variable assignment and capture the JSON array/object
    pattern = re.compile(
        rf"(?:const|var|let)\s+{re.escape(js_var)}\s*=\s*(\[.*?\]);",
        re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        raise ValueError(f"JS variable '{js_var}' not found in page HTML.")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        Path(f"debug_{amc_key}.txt").write_text(match.group(1)[:3000], encoding="utf-8")
        raise ValueError(f"JSON parse failed: {e}") from e

    # Find the target section by title
    section_title = p["section_title"].lower()
    target_vertical = None
    for vertical in data:
        if vertical.get("title", "").lower().strip() == section_title:
            target_vertical = vertical
            break

    if not target_vertical:
        available = [v.get("title", "") for v in data]
        raise ValueError(
            f"Section '{p['section_title']}' not found.\n" f"Available: {available}"
        )

    # Walk sections → subSections → items
    base = p.get("base_url", "").rstrip("/")
    ext_filter = [e.lower() for e in p.get("ext_filter", [])]
    results = []

    for section in target_vertical.get("sections", []):
        # Year is in section title e.g. "2025-2026"
        section_label = section.get("title", "")
        year = None
        yr_match = re.search(r"(20\d{2})", section_label)
        if yr_match:
            year = int(yr_match.group(1))

        for sub in section.get("subSections", []):
            for item in sub.get("items", []):
                title = item.get("title", "").strip()

                # Get URL from downloadMedia.url or downloadUrl
                media = item.get("downloadMedia") or {}
                rel_url = media.get("url") or item.get("downloadUrl") or ""
                if not rel_url:
                    continue

                href = rel_url if rel_url.startswith("http") else base + rel_url
                ext = href.rsplit(".", 1)[-1].lower().split("?")[0]

                if ext_filter and ext not in ext_filter:
                    continue

                # Try to get year from item title if section didn't have one
                if not year:
                    m = re.search(r"20\d{2}", title)
                    if m:
                        year = int(m.group())

                results.append(
                    {
                        "amc": amc_key,
                        "type": "monthly_portfolio",
                        "year": year,
                        "label": title,
                        "url": href,
                        "ext": ext,
                    }
                )

    if latest_only and results:
        results = [results[0]]
    return results


# ── Output ────────────────────────────────────────────────────────────────────


def save_csv(amc_key: str, links: list[dict], out_dir: Path | None = None) -> Path:
    out_dir = out_dir or Path.cwd()
    path = out_dir / f"{amc_key}_disclosure_links.csv"
    lines = ["amc,type,year,label,url,ext"]
    for r in links:
        label = r["label"].replace('"', "'")
        lines.append(
            f'{r["amc"]},{r["type"]},{r["year"] or ""},"{label}",{r["url"]},{r["ext"]}'
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → Saved: {path}  ({len(links)} rows)")
    return path


# ── Main ──────────────────────────────────────────────────────────────────────


def run() -> None:
    # Strip Jupyter kernel args
    raw_args = sys.argv[1:]
    clean_args = []
    skip_next = False
    for arg in raw_args:
        if skip_next:
            skip_next = False
            continue
        if arg == "-f":
            skip_next = True
            continue
        if arg.endswith(".json") and "kernel" in arg:
            continue
        clean_args.append(arg)

    args = clean_args
    latest_only = "--latest-only" in args
    args = [a for a in args if not a.startswith("--")]

    keys = [k for k in args if k in AMC_PROFILES] if args else list(AMC_PROFILES.keys())
    missing = [k for k in args if k not in AMC_PROFILES] if args else []
    if missing:
        print(f"⚠ Unknown: {missing}  |  Available: {list(AMC_PROFILES.keys())}")

    if not keys:
        print("No AMCs to process.")
        return

    print("=" * 60)
    print(f"AMC Disclosure Extractor  |  latest_only={latest_only}")
    print(f"Processing: {keys}")
    print("=" * 60)

    for key in keys:
        p = AMC_PROFILES[key]
        print(f"\n[{key}]  strategy={p['strategy']}  url={p['fetch_url']}")
        try:
            if p["strategy"] == "html":
                links = extract_html(key, p, latest_only)
            elif p["strategy"] == "json":
                links = extract_json(key, p, latest_only)
            else:
                print(f"  ✗ Unknown strategy: {p['strategy']}")
                continue

            print(f"  ✓ {len(links)} link(s) found")
            for lnk in links[:4]:
                print(
                    f"    {lnk['year']}  {lnk['label'][:38]:<40}  ...{lnk['url'][-50:]}"
                )
            if len(links) > 4:
                print(f"    ... and {len(links) - 4} more")

            save_csv(key, links)

        except Exception as e:
            print(f"  ✗ Error: {e}")

    print("\nDone.")


if __name__ == "__main__":
    run()
