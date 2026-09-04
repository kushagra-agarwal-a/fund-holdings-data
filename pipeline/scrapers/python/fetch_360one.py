#!/usr/bin/env python3
"""360 ONE — fortnightly portfolio from downloads hub embedded JSON."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.request
from pathlib import Path

PAGE = "https://www.360.one/asset/mutual-funds/downloads/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--fortnightly", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    with urllib.request.urlopen(urllib.request.Request(PAGE, headers=HEADERS), timeout=60, context=ctx) as resp:
        html = resp.read().decode("utf-8", "ignore")
    text = html.replace("\\/", "/").replace('\\"', '"')

    rows = []
    for m in re.finditer(
        r'\{"fileName":"(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+(\d{1,2}),\s*(20\d{2})","fileUrl":"(https://[^"]+\.xlsx?)"\}',
        text,
        re.I,
    ):
        start = max(0, m.start() - 400)
        ctxb = text[start : m.end() + 50]
        if "Fortnightly" not in ctxb and "fortnightly" not in ctxb:
            continue
        mon = MONTHS[m.group(1).lower()]
        rows.append({"year": int(m.group(3)), "month": mon, "url": m.group(4), "title": f"{m.group(1)} {m.group(2)}, {m.group(3)}"})

    for m in re.finditer(
        r'(https://[^"\s]+Fortnightly_Disclosure_(\d{2})_(\d{2})_(\d{4})[^"\s]*\.xlsx?)',
        text,
        re.I,
    ):
        rows.append({
            "year": int(m.group(4)),
            "month": int(m.group(3)),
            "url": m.group(1),
            "title": m.group(1).rsplit("/", 1)[-1],
        })

    seen = set()
    uniq = []
    for r in rows:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        uniq.append(r)

    targets = {(int(mk.split("-")[0]), int(mk.split("-")[1])): mk for mk in args.months}
    amc = args.root / "amcs" / "360-one-mutual-fund"
    for (y, mon), mk in targets.items():
        out = amc / mk
        out.mkdir(parents=True, exist_ok=True)
        for p in out.iterdir():
            if p.is_file():
                p.unlink()
        selected = [r for r in uniq if r["year"] == y and r["month"] == mon]
        print(f"{mk}: {len(selected)} file(s)")
        manifest = []
        for r in selected:
            fn = re.sub(r"[^\w.\-]+", "_", r["url"].rsplit("/", 1)[-1])[:180]
            rec = {"month": mk, "title": r.get("title"), "download_url": r["url"], "saved_as": fn}
            if args.dry_run:
                manifest.append(rec)
                print("  DRY", fn)
                continue
            with urllib.request.urlopen(
                urllib.request.Request(r["url"], headers={**HEADERS, "Referer": PAGE}),
                timeout=120,
                context=ctx,
            ) as resp:
                body = resp.read()
            (out / fn).write_bytes(body)
            manifest.append({**rec, "sha256": hashlib.sha256(body).hexdigest()})
            print(f"  OK {fn} ({len(body)} bytes)")
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
