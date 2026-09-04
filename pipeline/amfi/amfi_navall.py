#!/usr/bin/env python3
"""
Parse AMFI NAVAll.txt into a standardized scheme catalog.

Outputs (under data/amfi/ by default):
  schemes.jsonl / schemes.json  — one row per AMFI plan (scheme code)
  funds.json                    — plans collapsed to base fund within AMC
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PLAN_DIRECT_RE = re.compile(r"\bdirect(?:\s+plan)?\b", re.I)
PLAN_REGULAR_RE = re.compile(r"\bregular(?:\s+plan)?\b", re.I)
# Plan-level IDCW/dividend options — do NOT match fund names like "Dividend Yield Fund"
IDCW_RE = re.compile(
    r"(?ix)\b(?:idcw)\b|"
    r"\b(?:daily|weekly|fortnightly|monthly|quarterly|half[\s\-]?yearly|annual(?:ly)?)\s+"
    r"(?:idcw|dividend)\b|"
    r"\b(?:idcw|dividend)\s*(?:option|plan)?\s*(?:payout|reinvestment|reinvest)\b|"
    r"\bdividend\s+(?:option|plan)\b|"
    r"\bincome\s+distribution(?:\s+cum\s+(?:capital\s+)?withdrawal)?\b"
)
GROWTH_RE = re.compile(r"\bgrowth(?:\s+option)?\b", re.I)
BONUS_RE = re.compile(r"\bbonus(?:\s+option)?\b", re.I)

# Tokens stripped to recover base fund name (order matters a bit)
STRIP_PLAN_RE = re.compile(
    r"""(?ix)
    [\s\-_/]*                        # separator
    (?:
        \(\s*[^)]*(?:direct|regular|growth|idcw|bonus|plan|option)[^)]*\s*\)
      | \bdirect(?:\s+plan)?\b
      | \bregular(?:\s+plan)?\b
      | \bgrowth(?:\s+option)?\b
      | \bbonus(?:\s+option)?\b
      | \bidcw\b(?:\s*[/\-]?\s*(?:payout|reinvestment|reinvest|option|plan))?
      | \b(?:daily|weekly|fortnightly|monthly|quarterly|half[\s\-]?yearly|annual(?:ly)?)\s*(?:idcw|dividend)?
      | \b(?:idcw|dividend)\s*(?:option|plan)?\s*[/\-]?\s*(?:payout|reinvestment|reinvest)
      | \bdividend\s+(?:option|plan)\b
      | \bincome\s+distribution(?:\s+cum\s+(?:capital\s+)?withdrawal)?
      | \boption\b
      | \bplan\b
    )
    """,
)

DASH_SPLIT_RE = re.compile(r"\s*[-–—]\s*")
WS_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class AmfiScheme:
    amfi_code: str
    name: str
    name_norm: str
    base_name: str
    base_name_norm: str
    amc_name: str
    amc_name_norm: str
    is_direct: bool | None
    is_regular: bool | None
    is_growth: bool | None
    is_idcw: bool | None
    is_bonus: bool | None
    isin_growth_or_payout: str | None
    isin_div_reinvestment: str | None
    nav: str | None
    nav_date: str | None
    category: str | None


def norm_text(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = s.replace(" limted", " limited")
    s = NON_ALNUM_RE.sub(" ", s)
    return WS_RE.sub(" ", s).strip()


def clean_isin(v: str) -> str | None:
    v = (v or "").strip()
    if not v or v == "-":
        return None
    return v


def classify_plan(name: str) -> dict[str, bool | None]:
    is_direct = bool(PLAN_DIRECT_RE.search(name))
    is_regular = bool(PLAN_REGULAR_RE.search(name))
    is_growth = bool(GROWTH_RE.search(name))
    is_idcw = bool(IDCW_RE.search(name))
    is_bonus = bool(BONUS_RE.search(name))

    # Mutual exclusivity soft defaults
    if is_direct and not is_regular:
        plan_direct, plan_regular = True, False
    elif is_regular and not is_direct:
        plan_direct, plan_regular = False, True
    elif is_direct and is_regular:
        # rare junk; keep both True and flag via None? Prefer explicit both
        plan_direct, plan_regular = True, True
    else:
        plan_direct = plan_regular = None

    if is_growth and not is_idcw and not is_bonus:
        growth, idcw = True, False
    elif is_idcw and not is_growth:
        growth, idcw = False, True
    elif is_growth and is_idcw:
        growth, idcw = True, True
    else:
        growth = idcw = None

    return {
        "is_direct": plan_direct,
        "is_regular": plan_regular,
        "is_growth": growth,
        "is_idcw": idcw,
        "is_bonus": is_bonus or None,
    }


def base_fund_name(name: str) -> str:
    """Strip plan/option tokens; keep the investable scheme identity."""
    s = WS_RE.sub(" ", (name or "").strip())
    # Remove Formerly Known suffixes (often truncated)
    s = re.sub(r"(?i)\s*\(?\s*formerly\s+known(?:\s+as)?\b.*$", "", s).strip()
    # Walk cutting common trailing plan clauses after dashes
    parts = DASH_SPLIT_RE.split(s)
    kept: list[str] = []
    for i, part in enumerate(parts):
        pl = part.lower().strip()
        if i == 0:
            kept.append(part.strip())
            continue
        # drop pure plan parts
        if PLAN_DIRECT_RE.search(pl) or PLAN_REGULAR_RE.search(pl) or GROWTH_RE.search(pl) or IDCW_RE.search(pl) or BONUS_RE.search(pl):
            continue
        if re.fullmatch(r"(?:daily|weekly|fortnightly|monthly|quarterly|annual|half yearly|half-yearly|option|plan)", pl):
            continue
        # open-ended descriptions after dash
        if re.match(r"an?\s+open[-\s]?ended\b", pl):
            continue
        kept.append(part.strip())
    s = " - ".join(p for p in kept if p)
    # Second pass: remove remaining inline tokens
    prev = None
    while prev != s:
        prev = s
        s = STRIP_PLAN_RE.sub(" ", s)
        s = WS_RE.sub(" ", s).strip(" -_/()")
    s = re.sub(r"\s{2,}", " ", s).strip(" -")
    return s or name.strip()


def parse_navall(text: str) -> list[AmfiScheme]:
    lines = text.splitlines()
    category: str | None = None
    amc_name: str | None = None
    out: list[AmfiScheme] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Scheme Code;") or line.startswith("Source URL") or line == "Title: NAVAll.txt":
            continue
        if ";" not in line:
            # Category e.g. Open Ended Schemes(...) or AMC header
            if line.startswith("Open Ended Schemes") or line.startswith("Close Ended Schemes") or line.startswith(
                "Interval Fund"
            ):
                category = line
                continue
            # AMC name line
            if line.endswith("Mutual Fund") or "Mutual Fund" in line:
                amc_name = line
                continue
            # ignore other free text
            continue

        parts = line.split(";")
        if len(parts) < 6:
            continue
        # NAVAll layout changed ~2024: optional Plan + Option columns before NAV + Date.
        if len(parts) >= 8:
            code, isin1, isin2, name, nav, date = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
                parts[6],
                parts[7],
            )
        else:
            code, isin1, isin2, name, nav, date = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        code = code.strip()
        if not code.isdigit():
            continue
        name = name.strip()
        if not name or not amc_name:
            continue
        flags = classify_plan(name)
        base = base_fund_name(name)
        out.append(
            AmfiScheme(
                amfi_code=code,
                name=name,
                name_norm=norm_text(name),
                base_name=base,
                base_name_norm=norm_text(base),
                amc_name=amc_name,
                amc_name_norm=norm_text(amc_name),
                is_direct=flags["is_direct"],
                is_regular=flags["is_regular"],
                is_growth=flags["is_growth"],
                is_idcw=flags["is_idcw"],
                is_bonus=flags["is_bonus"],
                isin_growth_or_payout=clean_isin(isin1),
                isin_div_reinvestment=clean_isin(isin2),
                nav=nav.strip() or None,
                nav_date=date.strip() or None,
                category=category,
            )
        )
    return out


def is_listed_etf_payout_series(name: str) -> str | None:
    """Return 'growth' / 'idcw' when name is a separately listed ETF payout series.

    Liquid-rate ETFs (and similar) publish distinct Growth vs IDCW products with
    separate AMFI codes / exchange symbols. Those must NOT collapse together.

    Direct/Regular *plan* rows of an ETF FoF stay collapsed as usual.
    """
    s = name or ""
    if not re.search(r"(?i)\betf\b|liquid\s*bees", s):
        return None
    # Direct/Regular plan framing → ordinary plan, not a distinct listed series
    if re.search(r"(?i)\b(?:direct|regular)\s+plan\b", s):
        return None
    if re.search(r"(?i)\b(?:fof|fund of funds?)\b", s) and re.search(
        r"(?i)\b(?:direct|regular)\b", s
    ):
        return None
    has_g = bool(GROWTH_RE.search(s))
    has_i = bool(IDCW_RE.search(s))
    if has_g and not has_i:
        return "growth"
    if has_i and not has_g:
        return "idcw"
    return None


def collapse_funds(schemes: list[AmfiScheme]) -> list[dict[str, Any]]:
    """Collapse plan rows to base funds; keep listed ETF Growth/IDCW series separate."""
    # Remap base identity for listed ETF payout series so Growth ≠ IDCW.
    enriched: list[AmfiScheme] = []
    for s in schemes:
        series = is_listed_etf_payout_series(s.name)
        if series:
            suffix = " - Growth" if series == "growth" else " - IDCW"
            core = s.base_name or base_fund_name(s.name)
            # Avoid double-append if base already kept the suffix somehow
            if not re.search(r"(?i)\b(?:growth|idcw)\s*$", core):
                new_base = f"{core}{suffix}"
            else:
                new_base = core
            enriched.append(
                AmfiScheme(
                    **{
                        **asdict(s),
                        "base_name": new_base,
                        "base_name_norm": norm_text(new_base),
                    }
                )
            )
        else:
            enriched.append(s)

    groups: dict[tuple[str, str], list[AmfiScheme]] = defaultdict(list)
    for s in enriched:
        groups[(s.amc_name_norm, s.base_name_norm)].append(s)

    funds: list[dict[str, Any]] = []
    for (amc_norm, base_norm), rows in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        rows_sorted = sorted(rows, key=lambda r: int(r.amfi_code))
        # Prefer Direct Growth as canonical plan, else Regular Growth, else first
        canon = None
        for prefer in (
            lambda r: r.is_direct is True and r.is_growth is True,
            lambda r: r.is_regular is True and r.is_growth is True,
            lambda r: r.is_growth is True,
            lambda r: r.is_direct is True,
        ):
            for r in rows_sorted:
                if prefer(r):
                    canon = r
                    break
            if canon:
                break
        if canon is None:
            canon = rows_sorted[0]

        funds.append(
            {
                "amc_name": canon.amc_name,
                "amc_name_norm": amc_norm,
                "base_name": canon.base_name,
                "base_name_norm": base_norm,
                "canonical_amfi_code": canon.amfi_code,
                "canonical_name": canon.name,
                "plan_count": len(rows_sorted),
                "amfi_codes": [r.amfi_code for r in rows_sorted],
                "isins": sorted(
                    {
                        x
                        for r in rows_sorted
                        for x in (r.isin_growth_or_payout, r.isin_div_reinvestment)
                        if x
                    }
                ),
                "has_direct": any(r.is_direct is True for r in rows_sorted),
                "has_regular": any(r.is_regular is True for r in rows_sorted),
                "has_growth": any(r.is_growth is True for r in rows_sorted),
                "has_idcw": any(r.is_idcw is True for r in rows_sorted),
            }
        )
    return funds


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="data/amfi/NAVAll.txt")
    ap.add_argument("--out-dir", default="data/amfi")
    args = ap.parse_args()

    text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    # strip possible markdown wrapper from saved uploads
    if text.startswith("Source URL:"):
        # keep from first real header / first dataish line
        idx = text.find("Scheme Code;")
        if idx >= 0:
            text = text[idx:]

    schemes = parse_navall(text)
    funds = collapse_funds(schemes)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scheme_dicts = [asdict(s) for s in schemes]
    (out_dir / "schemes.json").write_text(json.dumps(scheme_dicts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out_dir / "schemes.jsonl").open("w", encoding="utf-8") as f:
        for row in scheme_dicts:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / "funds.json").write_text(json.dumps(funds, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    amcs = sorted({s.amc_name for s in schemes})
    summary = {
        "source": str(args.input),
        "scheme_plans": len(schemes),
        "base_funds": len(funds),
        "amcs": len(amcs),
        "amc_names": amcs,
    }
    (out_dir / "catalog_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"Parsed {len(schemes)} plans → {len(funds)} base funds across {len(amcs)} AMCs\n"
        f"Wrote {out_dir}/schemes.json, funds.json, catalog_summary.json"
    )


if __name__ == "__main__":
    main()
