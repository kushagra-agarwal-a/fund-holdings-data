#!/usr/bin/env python3
"""Holdings browser: AMC → parent fund → AMFI plan → holdings.

  .venv/bin/python holdings-browser/server.py
  open http://127.0.0.1:8777
"""
from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from contract import shape_holdings_payload
from filings import (
    NO_DATA_FOUND,
    dated_local_candidates,
    filing_links,
    is_fortnightly,
    next_filing_date,
    no_data_payload,
    normalize_as_of,
    previous_filing_date,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "public" if (HERE / "public" / "index.html").exists() else HERE
CATALOG = json.loads((STATIC / "catalog.json").read_text(encoding="utf-8"))
PORT = int(os.environ.get("PORT", "8777"))
ALLOWED_KEYS = {
    (s.get("holdings") or {}).get("b2_key")
    for s in (CATALOG.get("schemes") or CATALOG.get("parents") or [])
    if (s.get("holdings") or {}).get("b2_key")
}
ALLOWED_PATHS = {
    (s.get("holdings") or {}).get("local_path")
    for s in (CATALOG.get("schemes") or CATALOG.get("parents") or [])
    if (s.get("holdings") or {}).get("local_path")
}


def _scheme_from_row(row: dict | None) -> dict:
    row = row or {}
    hold = row.get("holdings") or {}
    return {
        "amfi_code": row.get("amfi_code"),
        "name": row.get("name"),
        "amc_name": row.get("amc_name"),
        "parent_name": row.get("parent_name"),
        "parent_amfi": row.get("parent_amfi"),
        "nav": row.get("nav"),
        "nav_date": row.get("nav_date"),
        "isin": row.get("isin"),
        "category": row.get("category"),
        "shortcode": hold.get("shortcode"),
        "as_of": hold.get("as_of"),
        "source_file": hold.get("source_file"),
        "disclosure_type": hold.get("disclosure_type"),
        "local_path": hold.get("local_path"),
        "b2_key": hold.get("b2_key"),
        "has_holdings": row.get("has_holdings"),
    }


def _origin(handler: SimpleHTTPRequestHandler) -> str:
    host = handler.headers.get("Host") or f"127.0.0.1:{PORT}"
    return f"http://{host}"


def _resolve_local(row: dict, as_of: str | None) -> Path | None:
    hold = row.get("holdings") or {}
    rel = hold.get("local_path")
    if not rel:
        return None
    latest_as_of = normalize_as_of(hold.get("as_of")) or hold.get("as_of")
    candidates = [rel]
    if as_of and as_of != latest_as_of:
        candidates = dated_local_candidates(rel, as_of) + candidates
    elif not as_of or as_of == latest_as_of:
        path = (ROOT / rel).resolve()
        if ROOT.resolve() in path.parents and path.exists():
            return path
        return None
    for cand in candidates:
        path = (ROOT / cand).resolve()
        if ROOT.resolve() not in path.parents or not path.exists():
            continue
        try:
            meta = (json.loads(path.read_text(encoding="utf-8")).get("meta") or {})
        except Exception:
            continue
        file_as_of = normalize_as_of(meta.get("as_of")) or meta.get("as_of")
        if not as_of or not file_as_of or file_as_of == as_of:
            return path
    return None


def _local_available(row: dict, as_of: str) -> bool:
    hold = row.get("holdings") or {}
    latest_as_of = normalize_as_of(hold.get("as_of")) or hold.get("as_of")
    if as_of and as_of == latest_as_of and hold.get("local_path"):
        return True
    return _resolve_local(row, as_of) is not None


def _links(handler: SimpleHTTPRequestHandler, row: dict, as_of: str, meta: dict | None) -> dict:
    scheme = _scheme_from_row(row)
    fortnightly = is_fortnightly(scheme, meta or {})
    prev = previous_filing_date(as_of, fortnightly)
    nxt = next_filing_date(as_of, fortnightly)
    return filing_links(
        origin=_origin(handler),
        code=str(scheme.get("amfi_code") or ""),
        as_of=as_of,
        previous_as_of=prev,
        next_as_of=nxt,
        previous_available=_local_available(row, prev),
        next_available=_local_available(row, nxt),
    )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/catalog":
            return self._json(CATALOG)
        if parsed.path.startswith("/api/amfi/"):
            return self._amfi(parsed.path.rsplit("/", 1)[-1], parse_qs(parsed.query))
        if parsed.path == "/api/holdings":
            return self._holdings(parse_qs(parsed.query))
        if parsed.path in {"/", "/index.html"}:
            return super().do_GET()
        return super().do_GET()

    def _json(self, payload, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _scheme_row(self, code: str):
        for s in CATALOG.get("schemes") or []:
            if str(s.get("amfi_code") or "") == code:
                return s
        return None

    def _amfi(self, code: str, q: dict[str, list[str]] | None = None) -> None:
        q = q or {}
        code = (code or "").strip()
        if not code.isdigit():
            return self._json({"error": "Enter a valid AMFI scheme code"}, 400)
        row = self._scheme_row(code)
        if not row:
            return self._json({"error": "Unknown AMFI code", "amfi_code": code}, 404)
        raw_date = (q.get("as_of") or q.get("date") or [""])[0].strip()
        as_of = normalize_as_of(raw_date)
        if raw_date and not as_of:
            return self._json(
                {"error": "Enter a valid date (YYYY-MM-DD)", "amfi_code": code},
                400,
            )
        hold = row.get("holdings") or {}
        if not row.get("has_holdings") or not hold.get("local_path"):
            return self._json(
                no_data_payload(_scheme_from_row(row), as_of or hold.get("as_of")),
                404,
            )
        return self._holdings_payload(row, as_of)

    def _holdings_payload(self, row: dict, as_of: str | None) -> None:
        hold = row.get("holdings") or {}
        want = as_of or normalize_as_of(hold.get("as_of")) or hold.get("as_of")
        path = _resolve_local(row, as_of)
        if not path:
            links = _links(self, row, want, {"disclosure_type": hold.get("disclosure_type")}) if want else None
            return self._json(no_data_payload(_scheme_from_row(row), want, links), 404)
        data = json.loads(path.read_text(encoding="utf-8"))
        payload = shape_holdings_payload(_scheme_from_row(row), data)
        filing_as_of = payload.get("meta", {}).get("as_of") or want
        if as_of and filing_as_of and filing_as_of != as_of:
            links = _links(self, row, as_of, payload.get("meta") or {})
            return self._json(no_data_payload(_scheme_from_row(row), as_of, links), 404)
        payload["links"] = _links(self, row, filing_as_of, payload.get("meta") or {})
        return self._json(payload)

    def _holdings(self, q: dict[str, list[str]]) -> None:
        amfi = (q.get("amfi") or q.get("code") or [""])[0].strip()
        if amfi:
            return self._amfi(amfi, q)
        key = (q.get("key") or [""])[0].strip()
        rel = (q.get("path") or [""])[0].strip()
        row = None
        if key:
            if key not in ALLOWED_KEYS:
                return self._json({"error": NO_DATA_FOUND}, 404)
            row = next(
                (
                    s
                    for s in (CATALOG.get("schemes") or CATALOG.get("parents") or [])
                    if (s.get("holdings") or {}).get("b2_key") == key
                ),
                None,
            )
            rel = ((row or {}).get("holdings") or {}).get("local_path") or ""
        elif rel:
            row = next(
                (
                    s
                    for s in (CATALOG.get("schemes") or [])
                    if (s.get("holdings") or {}).get("local_path") == rel
                ),
                None,
            )
        if not rel or rel not in ALLOWED_PATHS:
            return self._json({"error": NO_DATA_FOUND}, 404)
        as_of = normalize_as_of((q.get("as_of") or q.get("date") or [""])[0])
        if row:
            return self._holdings_payload(row, as_of)
        path = (ROOT / rel).resolve()
        if ROOT.resolve() not in path.parents:
            return self._json({"error": "Invalid path"}, 400)
        if not path.exists():
            return self._json({"error": NO_DATA_FOUND}, 404)
        data = json.loads(path.read_text(encoding="utf-8"))
        return self._json(shape_holdings_payload({}, data))


def main() -> int:
    if not (STATIC / "catalog.json").exists():
        raise SystemExit("Run scripts/build_holdings_browser_catalog.py first")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Holdings browser → http://127.0.0.1:{PORT}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
