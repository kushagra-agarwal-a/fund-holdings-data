#!/usr/bin/env python3
"""Local server for holdings side-by-side compare (our parse vs Upvaly).

  .venv/bin/python3 scripts/holdings_compare_server.py
  open http://127.0.0.1:8765/
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
_WEB_NEW = ROOT / "qc" / "web" / "holdings-compare"
_WEB_OLD = ROOT / "web" / "holdings-compare"
WEB = _WEB_NEW if _WEB_NEW.exists() else _WEB_OLD
PARSED = {
    "monthly": ROOT / "data" / "parsed" / "monthly" / "latest",
    "fortnightly": ROOT / "data" / "parsed" / "fortnightly" / "latest",
}
_SC_REG = ROOT / "registry" / "disclosure_shortcode_map.json"
_SC_OLD = ROOT / "data" / "sources" / "disclosure_shortcode_map.json"
SHORTCODE_MAP = _SC_REG if _SC_REG.exists() else _SC_OLD
MATCHING = {
    "monthly": ROOT / "data" / "parsed" / "monthly" / "latest" / "_matching",
    "fortnightly": ROOT / "data" / "parsed" / "fortnightly" / "latest" / "_matching",
}
UPVALY = "https://finapi.upvaly.com/api/mf/scheme-code/{code}"


def json_response(handler: SimpleHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def list_schemes() -> list[dict]:
    out: list[dict] = []
    for cadence, root in PARSED.items():
        if not root.is_dir():
            continue
        for amc_dir in sorted(root.iterdir()):
            if not amc_dir.is_dir() or amc_dir.name.startswith("_"):
                continue
            schemes_path = amc_dir / "schemes.json"
            if schemes_path.exists():
                try:
                    schemes = json.loads(schemes_path.read_text(encoding="utf-8"))
                except Exception:
                    schemes = []
                for s in schemes:
                    folder = s.get("folder") or ""
                    hp = amc_dir / folder / "portfolio.json"
                    if not hp.exists():
                        continue
                    out.append(
                        {
                            "id": f"{cadence}|{amc_dir.name}|{folder}",
                            "cadence": cadence,
                            "amc_id": amc_dir.name,
                            "folder": folder,
                            "scheme": s.get("scheme") or folder,
                            "shortcode": s.get("shortcode"),
                            "as_of": s.get("as_of"),
                            "rows": s.get("rows"),
                        }
                    )
            else:
                for folder in sorted(amc_dir.iterdir()):
                    hp = folder / "portfolio.json"
                    if hp.exists():
                        out.append(
                            {
                                "id": f"{cadence}|{amc_dir.name}|{folder.name}",
                                "cadence": cadence,
                                "amc_id": amc_dir.name,
                                "folder": folder.name,
                                "scheme": folder.name,
                                "shortcode": None,
                                "as_of": None,
                                "rows": None,
                            }
                        )
    return out


def amfi_hints() -> dict[str, list[str]]:
    """shortcode/folder → amfi codes."""
    hints: dict[str, list[str]] = {}

    def add(key: str, codes: list[str]):
        key = key.strip().upper()
        if not key or not codes:
            return
        prev = hints.get(key) or []
        for c in codes:
            if c and c not in prev:
                prev.append(str(c))
        hints[key] = prev

    if SHORTCODE_MAP.exists():
        sm = json.loads(SHORTCODE_MAP.read_text(encoding="utf-8"))
        for e in sm.get("entries") or []:
            codes = [str(e.get("canonical_amfi_code") or "")]
            codes += [str(c) for c in (e.get("amfi_codes") or [])]
            codes = [c for c in codes if c]
            sc = e.get("shortcode") or ""
            add(sc, codes)
            for a in e.get("aliases") or []:
                add(str(a), codes)

    for cadence, d in MATCHING.items():
        if not d.is_dir():
            continue
        for path in d.glob("*.json"):
            if path.name.startswith("_"):
                continue
            try:
                m = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for row in m.get("matched_disclosures") or m.get("matched") or []:
                codes = [str(row.get("canonical_amfi_code") or "")]
                codes += [str(c) for c in (row.get("amfi_codes") or [])]
                codes = [c for c in codes if c]
                add(row.get("shortcode") or "", codes)
    return hints


def load_local(cadence: str, amc_id: str, folder: str) -> dict:
    path = PARSED[cadence] / amc_id / folder / "portfolio.json"
    if not path.exists():
        raise FileNotFoundError(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("meta") if isinstance(data, dict) else {}
    holdings = data.get("holdings") if isinstance(data, dict) else data
    return {
        "source": "local",
        "path": str(path.relative_to(ROOT)),
        "meta": meta or {},
        "holdings": holdings or [],
    }


def fetch_upvaly(code: str) -> dict:
    url = UPVALY.format(code=code.strip())
    req = urllib.request.Request(url, headers={"User-Agent": "fund-disclosures-compare/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("message") or "Upvaly error")
    data = payload.get("data") or {}
    return {
        "source": "upvaly",
        "schemeCode": data.get("schemeCode"),
        "schemeName": data.get("schemeName"),
        "fundHouse": data.get("fundHouse"),
        "aum": data.get("aum"),
        "latestNav": data.get("latestNav"),
        "latestNavDate": data.get("latestNavDate"),
        "numberOfHoldings": (data.get("portfolio") or {})
        .get("concentration", {})
        .get("numberOfHoldings"),
        "holdings": data.get("holdings") or [],
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, hints=None, **kwargs):
        self._hints = hints or {}
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path == "/api/schemes":
                schemes = list_schemes()
                q = (qs.get("q") or [""])[0].strip().lower()
                if q:
                    schemes = [
                        s
                        for s in schemes
                        if q in (s["scheme"] or "").lower()
                        or q in (s["amc_id"] or "").lower()
                        or q in (s.get("shortcode") or "").lower()
                        or q in (s["folder"] or "").lower()
                    ]
                json_response(self, {"count": len(schemes), "schemes": schemes[:5000]})
                return

            if path == "/api/amfi-hint":
                key = (qs.get("key") or [""])[0]
                codes = self._hints.get(key.strip().upper()) or []
                json_response(self, {"key": key, "codes": codes})
                return

            if path == "/api/local":
                cadence = (qs.get("cadence") or [""])[0]
                amc = (qs.get("amc") or [""])[0]
                folder = (qs.get("folder") or [""])[0]
                if cadence not in PARSED or not amc or not folder:
                    json_response(self, {"error": "cadence, amc, folder required"}, 400)
                    return
                if re.search(r"[\\/]", amc) or re.search(r"[\\/]", folder):
                    json_response(self, {"error": "invalid path"}, 400)
                    return
                json_response(self, load_local(cadence, amc, folder))
                return

            if path.startswith("/api/upvaly/"):
                code = path.split("/api/upvaly/", 1)[1].strip()
                if not re.fullmatch(r"[0-9]{4,8}", code):
                    json_response(self, {"error": "invalid scheme code"}, 400)
                    return
                try:
                    json_response(self, fetch_upvaly(code))
                except urllib.error.HTTPError as e:
                    json_response(self, {"error": f"HTTP {e.code}", "detail": str(e)}, e.code)
                except Exception as e:
                    json_response(self, {"error": str(e)}, 502)
                return

            if path in {"/", ""}:
                self.path = "/index.html"
            return SimpleHTTPRequestHandler.do_GET(self)
        except FileNotFoundError as e:
            json_response(self, {"error": f"not found: {e}"}, 404)
        except Exception as e:
            json_response(self, {"error": str(e)}, 500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    WEB.mkdir(parents=True, exist_ok=True)
    print("Loading AMFI shortcode hints…")
    hints = amfi_hints()
    print(f"  {len(hints)} keys")
    handler = partial(Handler, hints=hints)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Holdings compare: http://{args.host}:{args.port}/")
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
