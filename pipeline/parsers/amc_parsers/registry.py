"""Registry of AMC-wise portfolio parsers (config-driven for all AMCs)."""
from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Callable

from . import family as family_mod
from . import uti_mutual_fund

ROOT = Path(__file__).resolve().parents[2]
_FAM_REG = ROOT / "registry" / "amc_parser_families.json"
_FAM_OLD = ROOT / "data" / "sources" / "amc_parser_families.json"
FAMILIES_PATH = _FAM_REG if _FAM_REG.exists() else _FAM_OLD

ParseFn = Callable


def _load_family_config() -> dict:
    if not FAMILIES_PATH.exists():
        return {}
    return json.loads(FAMILIES_PATH.read_text(encoding="utf-8"))


def _make_family_parser(amc_id: str, cfg: dict) -> ParseFn:
    if (cfg.get("family") or "") == "uti_mega" or amc_id == uti_mutual_fund.AMC_ID:
        return uti_mutual_fund.parse_file
    return partial(
        family_mod.parse_file,
        amc_id=amc_id,
        family=cfg.get("family") or "sebi_title",
        prefer_leading_code=bool(cfg.get("prefer_leading_code")),
        multi_sheet=bool(cfg.get("multi_sheet", True)),
        skip_mega=bool(cfg.get("skip_mega", True)),
    )


def build_parsers() -> dict[str, ParseFn]:
    parsers: dict[str, ParseFn] = {}
    for amc_id, cfg in _load_family_config().items():
        parsers[amc_id] = _make_family_parser(amc_id, cfg)
    # Hard overrides
    parsers[uti_mutual_fund.AMC_ID] = uti_mutual_fund.parse_file
    return parsers


PARSERS: dict[str, ParseFn] = build_parsers()


def get_parser(amc_id: str):
    parsers = build_parsers()
    try:
        return parsers[amc_id]
    except KeyError as e:
        known = ", ".join(sorted(parsers)[:25])
        raise SystemExit(
            f"No AMC parser for {amc_id!r}. Known ({len(parsers)}): {known}…"
        ) from e


def list_amcs() -> list[str]:
    return sorted(build_parsers())
