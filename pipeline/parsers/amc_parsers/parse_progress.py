"""Resume + completeness helpers for AMC disclosure parsing.

A mid-run kill used to leave portfolios without a full schemes.json, or with
schemes.json covering only a prefix of disclosure files (e.g. Mirae 82/97).
These helpers:

- detect which disclosure files already have enrichable portfolios
- reload existing SchemePortfolio rows so a resumed run can rewrite schemes.json
- compare disclosure dirs vs parsed dirs before enrich/sync
"""
from __future__ import annotations

import json
from pathlib import Path

from .common import SchemePortfolio, holding_from_dict, safe_name
from .family import SKIP_FILE_RE

DISCLOSURE_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb", ".zip"}


def iter_disclosure_files(disc_dir: Path) -> list[Path]:
    if not disc_dir.is_dir():
        return []
    files = []
    for p in sorted(disc_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in DISCLOSURE_SUFFIXES:
            continue
        if SKIP_FILE_RE.search(p.name):
            continue
        files.append(p)
    return files


def _source_basename(name: str | None) -> str:
    return Path(str(name or "").strip()).name


def covered_source_files(amc_parsed_dir: Path) -> dict[str, list[dict]]:
    """Map source_file basename → schemes.json rows that have portfolio.json."""
    out: dict[str, list[dict]] = {}
    idx = amc_parsed_dir / "schemes.json"
    if not idx.is_file():
        return out
    try:
        items = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(items, list):
        return out
    for s in items:
        if not isinstance(s, dict):
            continue
        src = _source_basename(s.get("source_file"))
        if not src:
            continue
        folder = s.get("folder") or safe_name(s.get("shortcode") or s.get("scheme") or "")
        pj = amc_parsed_dir / folder / "portfolio.json"
        if not pj.is_file():
            continue
        out.setdefault(src, []).append(s)
    return out


def source_file_complete(
    amc_parsed_dir: Path,
    disclosure: Path,
    *,
    covered: dict[str, list[dict]] | None = None,
) -> bool:
    """True when schemes.json already lists this file and all rows have portfolios.

    Re-parses when the disclosure file is newer than every matching portfolio.json
    (re-download / corrected pack).
    """
    covered = covered if covered is not None else covered_source_files(amc_parsed_dir)
    rows = covered.get(disclosure.name) or []
    if not rows:
        return False
    try:
        disc_mtime = disclosure.stat().st_mtime
    except OSError:
        disc_mtime = None
    for s in rows:
        folder = s.get("folder") or safe_name(s.get("shortcode") or s.get("scheme") or "")
        pj = amc_parsed_dir / folder / "portfolio.json"
        if not pj.is_file():
            return False
        if disc_mtime is not None:
            try:
                if pj.stat().st_mtime < disc_mtime:
                    return False
            except OSError:
                return False
    return True


def filter_paths_for_resume(
    paths: list[Path],
    amc_parsed_dir: Path,
) -> tuple[list[Path], list[Path]]:
    """Split paths into (to_parse, already_complete)."""
    covered = covered_source_files(amc_parsed_dir)
    todo: list[Path] = []
    done: list[Path] = []
    for path in paths:
        if source_file_complete(amc_parsed_dir, path, covered=covered):
            done.append(path)
        else:
            todo.append(path)
    return todo, done


def load_existing_portfolios(
    amc_parsed_dir: Path,
    *,
    amc_id: str,
    disclosure_type: str,
    period: str,
    only_sources: set[str] | None = None,
) -> list[SchemePortfolio]:
    """Rebuild SchemePortfolio objects from on-disk portfolios for schemes.json merge."""
    covered = covered_source_files(amc_parsed_dir)
    out: list[SchemePortfolio] = []
    for src, rows in covered.items():
        if only_sources is not None and src not in only_sources:
            continue
        for s in rows:
            folder = s.get("folder") or safe_name(s.get("shortcode") or s.get("scheme") or "")
            pj = amc_parsed_dir / folder / "portfolio.json"
            try:
                payload = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            meta = payload.get("meta") or {}
            holdings_raw = payload.get("holdings") or []
            holdings = [holding_from_dict(h) for h in holdings_raw if isinstance(h, dict)]
            out.append(
                SchemePortfolio(
                    amc_id=str(meta.get("amc_id") or amc_id),
                    disclosure_type=str(meta.get("disclosure_type") or disclosure_type),
                    period=str(meta.get("period") or period),
                    scheme_name=str(meta.get("scheme_name") or s.get("scheme") or folder),
                    shortcode=meta.get("shortcode") if meta.get("shortcode") is not None else s.get("shortcode"),
                    as_of=meta.get("as_of") if meta.get("as_of") is not None else s.get("as_of"),
                    source_file=str(meta.get("source_file") or s.get("source_file") or src),
                    sheet_name=str(meta.get("sheet_name") or s.get("sheet_name") or ""),
                    holdings=holdings,
                    notes=list(meta.get("notes") or []),
                )
            )
    return out


def check_amc_completeness(
    *,
    amc_id: str,
    disclosure_type: str,
    period: str,
    disc_root: Path,
    parsed_root: Path,
) -> dict:
    """Compare disclosure files vs enrichable schemes for one AMC period."""
    disc_dir = disc_root / disclosure_type / period / amc_id
    parsed_dir = parsed_root / disclosure_type / period / amc_id
    expected = iter_disclosure_files(disc_dir)
    covered = covered_source_files(parsed_dir) if parsed_dir.is_dir() else {}
    missing: list[str] = []
    stale: list[str] = []
    for path in expected:
        if path.name not in covered:
            missing.append(path.name)
            continue
        if not source_file_complete(parsed_dir, path, covered=covered):
            stale.append(path.name)
    return {
        "amc_id": amc_id,
        "disclosure_type": disclosure_type,
        "period": period,
        "disclosure_files": len(expected),
        "covered_files": len(expected) - len(missing) - len(stale),
        "schemes_indexed": sum(len(v) for v in covered.values()),
        "missing_files": missing,
        "stale_files": stale,
        "complete": not missing and not stale,
        "has_schemes_json": (parsed_dir / "schemes.json").is_file(),
    }


def check_period_completeness(
    *,
    disclosure_type: str,
    period: str,
    disc_root: Path,
    parsed_root: Path,
    amc_ids: list[str] | None = None,
) -> dict:
    disc_period = disc_root / disclosure_type / period
    if not disc_period.is_dir():
        return {
            "disclosure_type": disclosure_type,
            "period": period,
            "amcs": 0,
            "incomplete": [],
            "complete": True,
            "rows": [],
        }
    ids = amc_ids or sorted(p.name for p in disc_period.iterdir() if p.is_dir())
    rows = [
        check_amc_completeness(
            amc_id=amc_id,
            disclosure_type=disclosure_type,
            period=period,
            disc_root=disc_root,
            parsed_root=parsed_root,
        )
        for amc_id in ids
    ]
    # Ignore AMCs with zero disclosure files (empty dirs)
    rows = [r for r in rows if r["disclosure_files"] > 0]
    incomplete = [r for r in rows if not r["complete"]]
    return {
        "disclosure_type": disclosure_type,
        "period": period,
        "amcs": len(rows),
        "incomplete_amcs": len(incomplete),
        "incomplete": [
            {
                "amc_id": r["amc_id"],
                "disclosure_files": r["disclosure_files"],
                "covered_files": r["covered_files"],
                "missing": len(r["missing_files"]),
                "stale": len(r["stale_files"]),
                "missing_samples": r["missing_files"][:5],
                "stale_samples": r["stale_files"][:5],
            }
            for r in incomplete
        ],
        "complete": not incomplete,
        "rows": rows,
    }
