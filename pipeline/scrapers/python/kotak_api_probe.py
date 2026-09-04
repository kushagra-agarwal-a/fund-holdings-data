#!/usr/bin/env python3
"""
Quick probe for Kotak `getsubheaderList` — same pattern as a browser XHR (requests + mobile headers).

  pip install -r scripts/requirements-kotak-requests.txt
  export KOTAK_UZLC='…'   # from DevTools → Network → Request headers
  python3 scripts/kotak_api_probe.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def _load_fetch_kotak():
    p = Path(__file__).resolve().parent / "fetch_kotak.py"
    spec = importlib.util.spec_from_file_location("_fk", p)
    if spec is None or spec.loader is None:
        raise RuntimeError("fetch_kotak.py not found")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> None:
    try:
        import requests
    except ImportError:
        raise SystemExit("Install: pip install -r scripts/requirements-kotak-requests.txt")

    fk = _load_fetch_kotak()

    ap = argparse.ArgumentParser(description="Probe Kotak getsubheaderList JSON")
    ap.add_argument("--uzlc", default=os.environ.get("KOTAK_UZLC", ""), help="or env KOTAK_UZLC")
    ap.add_argument("--authorization", default=os.environ.get("KOTAK_API_AUTH", "decrypted"))
    ap.add_argument("--parent-id", type=int, default=fk.DEFAULT_API_PARENT_ID)
    ap.add_argument("--option", type=int, default=fk.DEFAULT_API_OPTION)
    ap.add_argument("--page-size", type=int, default=10)
    ap.add_argument("--page-number", type=int, default=1)
    args = ap.parse_args()

    url = f"{fk.API_BASE}/getsubheaderList/{args.parent_id}"
    params = {
        "option": args.option,
        "pagination": 1,
        "pageSize": args.page_size,
        "pageNumber": args.page_number,
    }
    headers = fk.kotak_mobile_api_headers(args.uzlc.strip() or None, args.authorization)

    resp = requests.get(url, params=params, headers=headers, timeout=120)
    print(resp.status_code, flush=True)
    try:
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except Exception:
        print((resp.text or "")[:4000])


if __name__ == "__main__":
    main()
