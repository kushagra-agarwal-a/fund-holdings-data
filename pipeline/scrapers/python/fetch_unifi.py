#!/usr/bin/env python3
"""Unifi MF — fortnightly liquid portfolio from statutorydocuments page."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

BASE = "https://unifimf.com"
PAGE = f"{BASE}/statutorydocuments/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
FILE_RE = re.compile(
    r"(?:https?:)?//unifimf\.com(/wp-content/uploads/fund-sheets/FN-Unifi-[^\"'\s]+\.xls[x]?)",
    re.I,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--fortnightly", action="store_true")
    ap.add_argument("--insecure-ssl", action="store_true", default=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(PAGE, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        html = resp.read().decode("utf-8", "ignore")
    paths = list(dict.fromkeys(m.group(1) for m in FILE_RE.finditer(html)))
    targets = {}
    for mk in args.months:
        y, m = mk.split("-")
        targets[mk] = (int(y), int(m))

    amc = args.root / "amcs" / "unifi-mutual-fund"
    for mk, (y, mon) in targets.items():
        out = amc / mk
        out.mkdir(parents=True, exist_ok=True)
        for p in out.iterdir():
            if p.is_file():
                p.unlink()
        selected = []
        for path in paths:
            name = path.rsplit("/", 1)[-1]
            cm = re.search(r"(15|28|29|30|31)(0[1-9]|1[0-2])(20\d{2})", name)
            if not cm:
                cm = re.search(r"(15|28|29|30|31)(0[1-9]|1[0-2])(\d{2})", name)
            if not cm:
                continue
            dd, mm, yy = cm.group(1), int(cm.group(2)), cm.group(3)
            year = int(yy) if len(yy) == 4 else 2000 + int(yy)
            if year == y and mm == mon:
                selected.append(path)
        print(f"{mk}: {len(selected)} file(s)")
        manifest = []
        for path in selected:
            url = urljoin(BASE, path)
            fn = path.rsplit("/", 1)[-1]
            rec = {"month": mk, "download_url": url, "saved_as": fn}
            if args.dry_run:
                manifest.append(rec)
                print("  DRY", fn)
                continue
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={**HEADERS, "Referer": PAGE}),
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
