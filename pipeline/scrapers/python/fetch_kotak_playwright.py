#!/usr/bin/env python3
"""
Kotak Mahindra Mutual Fund — **Playwright** automation for monthly portfolio `.xlsx` / `.xls`.

Use this when plain `fetch_kotak.py` hits Radware/captcha or expired `uzlc`. A real Chromium
window runs with your session; you can solve captchas manually if prompted.

Flow:
  1. Open forms-and-downloads in Chromium (headed by default).
  2. Collect JSON from every `getsubheaderList` XHR the page fires.
  3. Optionally extend via in-page `fetch()` pagination (same cookies / client as the SPA).
  4. Parse with the same JSON walker as `fetch_kotak.py`, then download via Playwright
     `APIRequestContext` (reuses storage state).

Setup once:

  pip install -r scripts/requirements-kotak-playwright.txt
  playwright install chromium

Run:

  python3 scripts/fetch_kotak_playwright.py --months 2026-01 2026-02 --captcha-pause

`--captcha-pause` waits for you to press Enter after the real app loads (after solving captcha).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path


def _load_fetch_kotak():
    path = Path(__file__).resolve().parent / "fetch_kotak.py"
    spec = importlib.util.spec_from_file_location("_kotak_fetch_mod", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load fetch_kotak.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit(
            "Playwright is not installed.\n"
            "  pip install -r scripts/requirements-kotak-playwright.txt\n"
            "  playwright install chromium\n"
            f"Import error: {e}"
        ) from e

    fk = _load_fetch_kotak()

    parser = argparse.ArgumentParser(description="Kotak MF monthly portfolio via Playwright")
    parser.add_argument("--months", nargs="+", default=["2026-01", "2026-02"], help="YYYY-MM")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="mf-monthly-holdings root",
    )
    parser.add_argument(
        "--url",
        default=fk.PAGE_URL,
        help="Start URL (default: forms-and-downloads)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Headless Chromium (more likely to be blocked by Radware)",
    )
    parser.add_argument(
        "--channel",
        default="",
        help="Playwright browser channel, e.g. chrome (use system Chrome)",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=18.0,
        help="Wait after load for XHRs / SPA to populate",
    )
    parser.add_argument(
        "--captcha-pause",
        action="store_true",
        help="After navigation, wait for Enter so you can solve captcha / expand menus",
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        help="Load Playwright storage state JSON from a previous successful run",
    )
    parser.add_argument(
        "--save-storage-state",
        type=Path,
        help="Save storage state after navigation (reuse with --storage-state)",
    )
    parser.add_argument("--api-parent-id", type=int, default=fk.DEFAULT_API_PARENT_ID)
    parser.add_argument("--api-option", type=int, default=fk.DEFAULT_API_OPTION)
    parser.add_argument("--api-page-size", type=int, default=50)
    parser.add_argument("--api-max-pages", type=int, default=120)
    parser.add_argument(
        "--no-inpage-fetch",
        action="store_true",
        help="Do not run in-page fetch() pagination (only rely on captured XHR)",
    )
    parser.add_argument(
        "--api-strict-hint",
        action="store_true",
        help="Stricter monthly-portfolio text filter (same as fetch_kotak.py)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print payload counts and output directory",
    )
    parser.add_argument(
        "--no-dom-scrape",
        action="store_true",
        help="Skip scanning the page for <a href=*.xlsx>",
    )
    args = parser.parse_args()

    want = set(args.months)
    amc_dir = (args.root / "amcs" / "kotak-mahindra-mutual-fund").resolve()
    captured: list[object] = []
    _CAP_MAX = 400

    def on_response(response) -> None:
        try:
            u = response.url
            if "/api/kotakapi/forms/" not in u:
                return
            if response.status != 200:
                return
            ct = response.headers.get("content-type", "").lower()
            if "json" not in ct:
                return
            if len(captured) >= _CAP_MAX:
                return
            captured.append(response.json())
        except Exception:
            pass

    launch_kwargs: dict = {"headless": args.headless}
    if args.channel:
        launch_kwargs["channel"] = args.channel

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        ctx_kwargs: dict = {
            "user_agent": fk.HEADERS["User-Agent"],
            "locale": "en-IN",
            "timezone_id": "Asia/Kolkata",
        }
        if args.storage_state and args.storage_state.is_file():
            ctx_kwargs["storage_state"] = str(args.storage_state)
            print(f"Loaded storage state {args.storage_state}", flush=True)

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.set_default_timeout(180_000)
        page.on("response", on_response)

        print(f"Navigating to {args.url} …", flush=True)
        print(f"Files will be written under: {amc_dir}/<YYYY-MM>/", flush=True)
        page.goto(args.url, wait_until="domcontentloaded")

        if args.captcha_pause:
            input(
                "\n>>> Solve captcha in the browser if needed, expand “Monthly portfolio” if required,\n"
                ">>> then press Enter here to continue <<<\n\n"
            )

        # Wait until we're not obviously on Radware captcha HTML title
        deadline = time.time() + 360.0
        while time.time() < deadline:
            try:
                t = page.title().lower()
            except Exception:
                t = ""
            if t and "radware captcha" not in t and "captcha page" not in t:
                break
            time.sleep(0.8)
        else:
            browser.close()
            raise SystemExit("Timed out waiting to leave Radware captcha page.")

        print(f"Settling {args.settle_seconds}s for network …", flush=True)
        time.sleep(max(0.0, args.settle_seconds))

        # Best-effort: click disclosure / portfolio labels to trigger XHR
        click_text_patterns = [
            r"(?i)monthly.*portfolio",
            r"(?i)portfolio.*disclosure",
            r"(?i)monthly.*disclosure",
            r"(?i)forms.*download",
        ]
        for pat in click_text_patterns:
            try:
                loc = page.get_by_text(re.compile(pat)).first
                if loc.is_visible(timeout=1500):
                    loc.click(timeout=3000)
                    time.sleep(2.5)
            except Exception:
                continue

        time.sleep(max(0.0, min(30.0, args.settle_seconds)))

        # Lazy-loaded lists / anchors
        try:
            for _ in range(6):
                page.evaluate("() => window.scrollBy(0, Math.max(400, innerHeight * 0.85))")
                time.sleep(0.7)
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2.0)
        except Exception:
            pass

        if not args.no_inpage_fetch:
            print(
                f"In-page API BFS from parent={args.api_parent_id} option={args.api_option} …",
                flush=True,
            )
            js = """
            async ({ parentId, option, pageSize, maxPages }) => {
                const out = [];
                const headers = {
                    Accept: 'application/json, text/plain, */*',
                    'Content-Type': 'application/json',
                };
                for (let p = 1; p <= maxPages; p++) {
                    const u = '/api/kotakapi/forms/user/v1/getsubheaderList/' + parentId
                        + '?option=' + option + '&pagination=1&pageSize=' + pageSize
                        + '&pageNumber=' + p;
                    const r = await fetch(u, { credentials: 'include', headers });
                    if (!r.ok) { out.push({ __error: r.status, page: p, parentId }); break; }
                    const j = await r.json();
                    out.push(j);
                    let items = j?.data?.list ?? j?.data?.items ?? j?.list ?? j?.items ?? j?.data;
                    if (!Array.isArray(items)) items = [];
                    if (items.length < pageSize) break;
                }
                return out;
            }
            """
            try:
                queue: list[int] = [args.api_parent_id]
                visited_parents: set[int] = set()
                max_parents = 150
                while queue and len(visited_parents) < max_parents:
                    pid = queue.pop(0)
                    if pid in visited_parents:
                        continue
                    visited_parents.add(pid)
                    extra = page.evaluate(
                        js,
                        {
                            "parentId": pid,
                            "option": args.api_option,
                            "pageSize": max(1, args.api_page_size),
                            "maxPages": max(1, args.api_max_pages),
                        },
                    )
                    if not isinstance(extra, list):
                        continue
                    stop_branch = False
                    for payload in extra:
                        if isinstance(payload, dict) and "__error" in payload:
                            print(
                                f"  … parent {pid}: HTTP {payload.get('__error')} "
                                f"(page {payload.get('page')})",
                                flush=True,
                            )
                            stop_branch = True
                            break
                        captured.append(payload)
                        for item in fk.extract_api_list(payload):
                            if isinstance(item, dict):
                                for cid in fk.child_subheader_ids(item):
                                    if cid not in visited_parents and cid not in queue:
                                        queue.append(cid)
                    if stop_branch:
                        continue
            except Exception as e:
                print(f"  … in-page fetch failed (XHR capture still used): {e}", flush=True)

        if args.save_storage_state:
            args.save_storage_state.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(args.save_storage_state))
            print(f"Wrote storage state {args.save_storage_state}", flush=True)

        require_hint = args.api_strict_hint
        all_rows: list[tuple[str, str, str]] = []
        if args.verbose:
            print(f"Captured {len(captured)} JSON response(s) from /api/kotakapi/forms/", flush=True)
        for payload in captured:
            all_rows.extend(
                fk.collect_spreadsheet_rows_from_json(payload, require_monthly_hint=require_hint)
            )

        if not args.no_dom_scrape:
            dom_added: list[tuple[str, str, str]] = []
            try:
                raw_pairs = page.evaluate(
                    """() => {
                      const o = [];
                      const seen = new Set();
                      for (const a of document.querySelectorAll('a[href]')) {
                        const h = (a.getAttribute('href') || '').trim();
                        if (!/\\.(xlsx|xls)(\\?|#|$)/i.test(h)) continue;
                        const t = (a.textContent || '').trim().slice(0, 900);
                        const k = h + '\\t' + t;
                        if (seen.has(k)) continue;
                        seen.add(k);
                        o.push([h, t]);
                      }
                      return o;
                    }"""
                )
                if isinstance(raw_pairs, list):
                    pairs = [
                        (str(a[0]), str(a[1]))
                        for a in raw_pairs
                        if isinstance(a, list) and len(a) >= 2
                    ]
                    dom_added = fk.collect_spreadsheet_rows_from_dom_anchors(
                        pairs,
                        page_url=page.url,
                        require_monthly_hint=False,
                    )
                    all_rows.extend(dom_added)
                print(
                    f"After DOM <a> scrape: +{len(dom_added)} row(s) (require_monthly_hint=false)",
                    flush=True,
                )
            except Exception as e:
                print(f"DOM scrape failed: {e}", flush=True)

        all_rows = fk.dedupe_rows(all_rows)
        print(f"Total {len(all_rows)} spreadsheet row(s) after merge/dedupe", flush=True)

        if not all_rows and captured:
            sample_path = amc_dir / "last-api-sample.json"
            try:
                amc_dir.mkdir(parents=True, exist_ok=True)
                blob = json.dumps(captured[0], indent=2, ensure_ascii=False)
                sample_path.write_text(blob[:800_000], encoding="utf-8")
                print(
                    f"\n*** No spreadsheet URLs parsed. Wrote first API JSON to:\n    {sample_path}\n"
                    "Share (redacted) structure if you need parser tweaks. "
                    "Try --months that match dates inside that JSON, or expand menus before Enter.\n",
                    flush=True,
                )
            except OSError as err:
                print(f"Could not write last-api-sample.json: {err}", flush=True)

        # Months present vs requested (debug)
        if args.verbose and all_rows:
            months_found = sorted({r[0] for r in all_rows})
            print(f"Months seen in data: {months_found}", flush=True)
            print(f"You asked for: {sorted(want)}", flush=True)

        by_month: dict[str, list[tuple[str, str]]] = {k: [] for k in args.months}
        for mk, url, label in all_rows:
            if mk not in want:
                continue
            by_month[mk].append((url, label))

        for month_key in args.months:
            out_dir = amc_dir / month_key
            out_dir.mkdir(parents=True, exist_ok=True)
            batch = by_month.get(month_key) or []
            print(f"\n{month_key}: {len(batch)} file(s)", flush=True)
            manifest: list[dict] = []

            if not batch:
                print(
                    "  No rows for this month — try --captcha-pause, expand UI, or lower filters.",
                    flush=True,
                )

            for i, (file_url, label) in enumerate(batch, 1):
                fname = fk.safe_filename(file_url)
                rec = {
                    "month": month_key,
                    "download_url": file_url,
                    "saved_as": fname,
                    "label": label,
                    "source": "playwright",
                }
                if args.dry_run:
                    print(f"  [{i}] {fname}", flush=True)
                    manifest.append({**rec, "sha256": "", "dry_run": True})
                    continue
                try:
                    resp = context.request.get(file_url, timeout=120_000)
                    if not resp.ok:
                        manifest.append(
                            {**rec, "sha256": "", "error": f"HTTP {resp.status}: {resp.status_text}"}
                        )
                        print(f"  [{i}] ERR {fname}: HTTP {resp.status}", flush=True)
                        continue
                    body = resp.body()
                    h = hashlib.sha256(body).hexdigest()
                    (out_dir / fname).write_bytes(body)
                    manifest.append({**rec, "sha256": h})
                    print(f"  [{i}] OK {fname} ({len(body)} bytes)", flush=True)
                except Exception as e:
                    manifest.append({**rec, "sha256": "", "error": str(e)})
                    print(f"  [{i}] ERR {fname}: {e}", flush=True)

            man_path = out_dir / "manifest.json"
            man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print(f"Wrote {man_path}", flush=True)

        browser.close()


if __name__ == "__main__":
    main()
