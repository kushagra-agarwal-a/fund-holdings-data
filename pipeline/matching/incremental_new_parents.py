#!/usr/bin/env python3
"""Diff AMFI parent universe vs disclosure→AMFI map; propose matches for new parents.

Examples:
  .venv/bin/python3 matching/incremental_new_parents.py
  .venv/bin/python3 matching/incremental_new_parents.py --apply-auto --min-score=95
  .venv/bin/python3 matching/incremental_new_parents.py --out=data/parsed/new_parents_proposals.json
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]


def norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def active_parents(path: Path) -> list[dict]:
    data = load_json(path)
    if isinstance(data, dict) and "schemes" in data:
        rows = data["schemes"]
    elif isinstance(data, list):
        rows = data
    else:
        raise SystemExit(f"Unrecognized active parents file: {path}")
    out = []
    for r in rows:
        sid = str(r.get("scheme_id") or r.get("canonical_amfi_code") or "")
        name = r.get("scheme_name") or r.get("base_name") or r.get("name") or ""
        if sid and name:
            out.append({"scheme_id": sid, "scheme_name": name, "raw": r})
    return out


def mapped_parent_ids(disc_map: Path) -> set[str]:
    data = load_json(disc_map)
    rows = data.get("mappings") if isinstance(data, dict) else data
    ids = set()
    for r in rows or []:
        if not r.get("mapped"):
            continue
        sid = r.get("fund_amfi_scheme_id")
        if sid is not None and str(sid) not in ("", "None"):
            ids.add(str(sid))
    return ids


def disclosure_candidates(disc_map: Path) -> list[dict]:
    data = load_json(disc_map)
    rows = data.get("mappings") if isinstance(data, dict) else data
    return list(rows or [])


def best_disclosure(parent_name: str, candidates: list[dict], cutoff: float):
    nq = norm(parent_name)
    best = None
    best_score = 0.0
    for c in candidates:
        name = c.get("disclosure_fund_name") or ""
        if not name:
            continue
        sc = float(
            max(
                fuzz.token_set_ratio(nq, norm(name)),
                fuzz.partial_ratio(nq, norm(name)),
            )
        )
        # Prefer unmapped or same-ish names
        if c.get("mapped") and str(c.get("fund_amfi_scheme_id") or ""):
            sc -= 2.0  # slight penalty vs already-mapped rows (alias risk)
        if sc > best_score:
            best_score = sc
            best = c
    if best is None or best_score < cutoff:
        return None, best_score
    return best, best_score


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--active-parents",
        default="data/sources/amfi_schemes_active_aug2026.json",
        help="AMFI populate-scheme / active parent universe JSON",
    )
    ap.add_argument(
        "--disclosure-map",
        default="data/sources/disclosure_to_amfi_global_mapping.json",
    )
    ap.add_argument("--cutoff", type=float, default=90.0)
    ap.add_argument(
        "--min-score",
        type=float,
        default=95.0,
        help="Auto-apply threshold when --apply-auto is set",
    )
    ap.add_argument(
        "--out",
        default="data/parsed/new_parents_proposals.json",
    )
    ap.add_argument(
        "--apply-auto",
        action="store_true",
        help="Merge high-confidence proposals into disclosure map (updates file)",
    )
    args = ap.parse_args()

    parents = active_parents(Path(args.active_parents))
    mapped = mapped_parent_ids(Path(args.disclosure_map))
    cands = disclosure_candidates(Path(args.disclosure_map))

    missing = [p for p in parents if p["scheme_id"] not in mapped]
    proposals = []
    for p in missing:
        hit, score = best_disclosure(p["scheme_name"], cands, args.cutoff)
        proposals.append(
            {
                "scheme_id": p["scheme_id"],
                "scheme_name": p["scheme_name"],
                "best_score": round(score, 2),
                "suggested_disclosure": (hit or {}).get("disclosure_fund_name"),
                "suggested_shortname": (hit or {}).get("disclosure_fund_shortname"),
                "suggested_already_mapped_to": (hit or {}).get("fund_amfi_scheme_id"),
                "action": (
                    "auto_map"
                    if hit and score >= args.min_score and not (hit or {}).get("mapped")
                    else "review"
                    if hit and score >= args.cutoff
                    else "no_disclosure_candidate"
                ),
            }
        )

    proposals.sort(key=lambda x: (-(x["best_score"] or 0), x["scheme_name"]))
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_parents": len(parents),
        "already_mapped": len(mapped),
        "missing_count": len(missing),
        "cutoff": args.cutoff,
        "min_score_auto": args.min_score,
        "proposals": proposals,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} ({len(proposals)} missing parents)")

    auto = [p for p in proposals if p["action"] == "auto_map"]
    review = [p for p in proposals if p["action"] == "review"]
    none = [p for p in proposals if p["action"] == "no_disclosure_candidate"]
    print(f"  auto_map={len(auto)} review={len(review)} no_candidate={len(none)}")

    if not args.apply_auto or not auto:
        return

    disc_path = Path(args.disclosure_map)
    data = load_json(disc_path)
    rows = data.get("mappings") if isinstance(data, dict) else data
    by_name = {(r.get("disclosure_fund_name") or ""): r for r in rows}
    applied = 0
    for p in auto:
        name = p["suggested_disclosure"]
        row = by_name.get(name or "")
        if not row or row.get("mapped"):
            continue
        row["fund_amfi_scheme_id"] = p["scheme_id"]
        row["parent_fund_name"] = p["scheme_name"]
        row["mapped"] = True
        row["match_how"] = "incremental_new_parents_auto"
        row["fix_note"] = f"Auto-mapped at score {p['best_score']}"
        applied += 1
    if isinstance(data, dict):
        data.setdefault("meta", {})["incremental_new_parents_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        data["meta"]["incremental_new_parents_applied"] = applied
    disc_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Applied {applied} auto maps → {disc_path}")


if __name__ == "__main__":
    main()
