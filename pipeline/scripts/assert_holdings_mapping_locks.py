#!/usr/bin/env python3
"""Fail if pinned shortcode/alias locks drift from registry maps.

Run after match / shortcode edits and before enrich/sync so wrong parents
cannot silently ship again (HDINCF, SILVRFOF, INDEX, etc.).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ROOT / "registry" / "holdings_mapping_locks.json"
SHORT = ROOT / "registry" / "disclosure_shortcode_map.json"
ALIASES = ROOT / "registry" / "amfi_holdings_aliases.json"


def main() -> int:
    locks = json.loads(LOCKS.read_text(encoding="utf-8"))
    short_rows = json.loads(SHORT.read_text(encoding="utf-8")).get("entries") or []
    by_key = {
        f"{(r.get('amc_id') or '').strip()}::{(r.get('shortcode') or '').strip()}": r
        for r in short_rows
    }
    alias_map = {}
    if ALIASES.is_file():
        alias_map = json.loads(ALIASES.read_text(encoding="utf-8")).get("aliases") or {}

    errors: list[str] = []
    for row in locks.get("shortcodes") or []:
        amc = row["amc_id"]
        sc = row["shortcode"]
        want = str(row["canonical_amfi_code"])
        hit = by_key.get(f"{amc}::{sc}")
        if not hit:
            errors.append(f"missing shortcode map entry {amc}::{sc} (want AMFI {want})")
            continue
        got = str(hit.get("canonical_amfi_code") or "")
        if got != want:
            errors.append(
                f"{amc}::{sc} maps to {got}, lock wants {want} ({row.get('label')})"
            )
        conf = (hit.get("confidence") or "").strip().lower()
        if conf not in {"manual", "confirmed"}:
            errors.append(
                f"{amc}::{sc} confidence={conf!r}; lock requires manual/confirmed "
                "so rematch cannot overwrite"
            )

    for orphan, parent in (locks.get("aliases") or {}).items():
        got = str(alias_map.get(str(orphan)) or "")
        if got != str(parent):
            errors.append(
                f"alias {orphan} -> {got or '(missing)'}, lock wants {parent}"
            )

    if errors:
        print(
            json.dumps({"ok": False, "errors": errors, "locks": str(LOCKS)}, indent=2),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "shortcode_locks": len(locks.get("shortcodes") or []),
                "alias_locks": len(locks.get("aliases") or {}),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
