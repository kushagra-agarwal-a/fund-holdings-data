#!/usr/bin/env python3
"""Fetch AMFI scheme-data (NAV date + ISIN) for every populate-scheme id.

Resumable JSONL checkpoint:
  data/sources/amfi_scheme_data.jsonl

Usage:
  .venv/bin/python scripts/fetch_amfi_scheme_data.py
  .venv/bin/python scripts/fetch_amfi_scheme_data.py --workers 16
  .venv/bin/python scripts/fetch_amfi_scheme_data.py --aggregate-only
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMES_PATH = ROOT / "data" / "sources" / "amfi_schemes.json"
OUT_JSONL = ROOT / "data" / "sources" / "amfi_scheme_data.jsonl"
OUT_ACTIVE = ROOT / "data" / "sources" / "amfi_schemes_active_aug2026.json"
OUT_SUMMARY = ROOT / "data" / "sources" / "amfi_scheme_data_summary.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
API = "https://www.amfiindia.com/api/scheme-data?strMFId={mf_id}&strSDId={scheme_id}&strOption=NAV"

_write_lock = threading.Lock()


def _load_done(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = str(row.get("scheme_id") or "").strip()
            if sid:
                done.add(sid)
    return done


def _fetch_one(mf_id: int, scheme_id: str, scheme_name: str, retries: int = 4) -> dict:
    url = API.format(mf_id=mf_id, scheme_id=scheme_id)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "*/*",
                    "Referer": "https://www.amfiindia.com/otherdata/scheme-details",
                    "User-Agent": UA,
                },
            )
            with urllib.request.urlopen(req, timeout=45) as res:
                raw = res.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("message"):
                rows = []
                message = str(data.get("message"))
            elif isinstance(data, list):
                rows = data
                message = None
            else:
                rows = []
                message = f"unexpected_payload:{type(data).__name__}"
            # Keep only fields we care about (+ name/nav for debugging)
            slim = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                slim.append(
                    {
                        "scheme_nav_name": r.get("Scheme_NAV_Name") or "",
                        "isin_growth_or_div_payout": r.get("ISIN_Div_Payout_ISIN_Growth") or "",
                        "isin_div_reinvestment": r.get("ISIN_Div_Reinvestment") or "",
                        "nav": r.get("Net_Asset_Value") or "",
                        "nav_date": r.get("Date") or "",
                    }
                )
            return {
                "mf_id": mf_id,
                "scheme_id": str(scheme_id),
                "scheme_name": scheme_name,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "message": message,
                "subscheme_count": len(slim),
                "subschemes": slim,
                "error": None,
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(min(8.0, 0.6 * attempt * attempt))
    return {
        "mf_id": mf_id,
        "scheme_id": str(scheme_id),
        "scheme_name": scheme_name,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "message": None,
        "subscheme_count": 0,
        "subschemes": [],
        "error": str(last_err) if last_err else "unknown",
    }


def _append_jsonl(path: Path, row: dict) -> None:
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _write_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _parse_nav_date(s: str):
    if not s:
        return None
    # 2026-08-10T00:00:00.000Z
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def aggregate(jsonl_path: Path) -> dict:
    total = 0
    with_data = 0
    no_data = 0
    errors = 0
    active_aug = []
    active_10_aug = []
    nav_date_counter: dict[str, int] = {}

    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            total += 1
            if row.get("error"):
                errors += 1
            subs = row.get("subschemes") or []
            if not subs:
                no_data += 1
                continue
            with_data += 1
            dates = []
            isins = set()
            for sub in subs:
                dt = _parse_nav_date(sub.get("nav_date") or "")
                if dt:
                    dates.append(dt)
                    key = dt.isoformat()
                    nav_date_counter[key] = nav_date_counter.get(key, 0) + 1
                for k in ("isin_growth_or_div_payout", "isin_div_reinvestment"):
                    v = (sub.get(k) or "").strip()
                    if v and v not in {"-", "NA", "N/A"}:
                        isins.add(v)
            if not dates:
                continue
            latest = max(dates)
            has_aug = any(d.year == 2026 and d.month == 8 for d in dates)
            has_10 = any(d.year == 2026 and d.month == 8 and d.day == 10 for d in dates)
            payload = {
                "mf_id": row.get("mf_id"),
                "scheme_id": row.get("scheme_id"),
                "scheme_name": row.get("scheme_name"),
                "latest_nav_date": latest.isoformat(),
                "subscheme_count": len(subs),
                "isins": sorted(isins),
                "subschemes": subs,
            }
            if has_aug:
                active_aug.append(payload)
            if has_10:
                active_10_aug.append(payload)

    summary = {
        "fetched_schemes": total,
        "with_subscheme_data": with_data,
        "no_data": no_data,
        "errors": errors,
        "active_aug2026_count": len(active_aug),
        "active_10_aug2026_count": len(active_10_aug),
        "nav_date_counts_top": sorted(nav_date_counter.items(), key=lambda x: (-x[1], x[0]))[:20],
    }
    OUT_ACTIVE.write_text(
        json.dumps(
            {
                "filter": "any subscheme NAV date in August 2026",
                "count": len(active_aug),
                "also_10_aug_2026_count": len(active_10_aug),
                "schemes": sorted(active_aug, key=lambda s: (s.get("scheme_name") or "").casefold()),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="optional cap for testing")
    ap.add_argument("--aggregate-only", action="store_true")
    ap.add_argument("--mf-id", type=int, default=0, help="optional single MF filter")
    args = ap.parse_args()

    if args.aggregate_only:
        summary = aggregate(OUT_JSONL)
        print(json.dumps(summary, indent=2))
        print(f"wrote {OUT_ACTIVE}")
        return 0

    schemes = json.loads(SCHEMES_PATH.read_text(encoding="utf-8"))["schemes"]
    if args.mf_id:
        schemes = [s for s in schemes if int(s["mf_id"]) == args.mf_id]
    if args.limit and args.limit > 0:
        schemes = schemes[: args.limit]

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    done = _load_done(OUT_JSONL)
    todo = [s for s in schemes if str(s["scheme_id"]) not in done]
    print(
        f"total={len(schemes)} already={len(done)} todo={len(todo)} workers={args.workers}",
        flush=True,
    )
    if not todo:
        summary = aggregate(OUT_JSONL)
        print(json.dumps(summary, indent=2))
        return 0

    ok = err = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {
            ex.submit(_fetch_one, int(s["mf_id"]), str(s["scheme_id"]), s.get("scheme_name") or ""): s
            for s in todo
        }
        for i, fut in enumerate(as_completed(futs), 1):
            row = fut.result()
            _append_jsonl(OUT_JSONL, row)
            if row.get("error"):
                err += 1
            else:
                ok += 1
            if i % 100 == 0 or i == len(futs):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed else 0
                print(
                    f"[{i}/{len(futs)}] ok={ok} err={err} "
                    f"{rate:.1f}/s eta={((len(futs)-i)/rate/60) if rate else float('inf'):.1f}m",
                    flush=True,
                )

    summary = aggregate(OUT_JSONL)
    print(json.dumps(summary, indent=2))
    print(f"wrote {OUT_JSONL}")
    print(f"wrote {OUT_ACTIVE}")
    print(f"wrote {OUT_SUMMARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
