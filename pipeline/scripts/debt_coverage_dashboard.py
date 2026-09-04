#!/usr/bin/env python3
"""North-star debt coverage dashboard vs AMFI NAVAll.txt.

1. Fetch NAVAll.txt (live or cached)
2. Keep plans with NAV date in a target month (default Aug 2026)
3. Collapse to unique base funds → canonical AMFI code + AMFI category
4. Label Debt / Non_debt from AMFI category
5. Compare debt funds against parsed holdings by **meta.as_of** (not folder name)
6. Optionally compare CDN filings.json counts

Outputs under data/parsed/ (JSON + CSV + summary).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "amfi"))

from amfi_navall import AmfiScheme, collapse_funds, parse_navall  # noqa: E402

NAVALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
DEFAULT_OUT = ROOT / "data" / "parsed"

MONTH_RE = re.compile(
    r"^(\d{1,2})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{4})$",
    re.I,
)
MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def fetch_navall(url: str = NAVALL_URL, cache: Path | None = None) -> str:
    if cache and cache.exists():
        return cache.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": "fund-disclosures/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    return text


def parse_nav_date(s: str | None) -> tuple[int, int, int] | None:
    if not s:
        return None
    m = MONTH_RE.match(s.strip())
    if not m:
        return None
    d, mon, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
    return y, mon, d


def debt_label(category: str | None) -> str:
    c = (category or "").lower()
    if (
        "debt scheme" in c
        or "debt oriented" in c
        or c.startswith("interval fund")
        or "money market" in c
    ):
        return "Debt"
    return "Non_debt"


def short_category(category: str | None) -> str | None:
    if not category:
        return None
    m = re.search(r"\(([^)]+)\)", category)
    return m.group(1).strip() if m else category


def filter_schemes_for_month(schemes: list[AmfiScheme], year: int, month: int) -> list[AmfiScheme]:
    out: list[AmfiScheme] = []
    for s in schemes:
        d = parse_nav_date(s.nav_date)
        if d and d[0] == year and d[1] == month:
            out.append(s)
    return out


def build_amfi_universe(schemes: list[AmfiScheme]) -> list[dict]:
    funds = collapse_funds(schemes)
    rows: list[dict] = []
    for f in funds:
        # category from canonical plan row
        canon_code = f["canonical_amfi_code"]
        cat = None
        for s in schemes:
            if s.amfi_code == canon_code:
                cat = s.category
                break
        label = debt_label(cat)
        rows.append(
            {
                "amc_name": f["amc_name"],
                "base_name": f["base_name"],
                "canonical_amfi_code": canon_code,
                "canonical_name": f["canonical_name"],
                "category": cat,
                "category_short": short_category(cat),
                "debt_label": label,
                "plan_count": f["plan_count"],
                "all_amfi_codes": f["amfi_codes"],
            }
        )
    return rows


def merge_holdings(*sets: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for holdings in sets:
        for code, row in holdings.items():
            prev = out.get(code)
            if not prev or (row.get("as_of") or "") >= (prev.get("as_of") or ""):
                out[code] = row
    return out


def load_holdings_by_as_of(
    cadences: list[str] | None = None,
    as_of_dates: list[str] | None = None,
) -> dict[str, dict[str, dict]]:
    """Group parsed portfolios by meta.as_of → amfi_code → row."""
    cadences = cadences or ["monthly", "fortnightly"]
    by_asof: dict[str, dict[str, dict]] = defaultdict(dict)
    parsed_root = ROOT / "data" / "parsed"
    for cadence in cadences:
        base = parsed_root / cadence
        if not base.exists():
            continue
        for pj in base.rglob("portfolio.json"):
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, list):
                continue
            meta = data.get("meta") or {}
            as_of = str(meta.get("as_of") or "").strip()[:10]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
                continue
            if as_of_dates and as_of not in as_of_dates:
                continue
            code = str(meta.get("amfi_code") or meta.get("scheme_id") or "").strip()
            if not code.isdigit():
                continue
            try:
                local_path = str(pj.resolve().relative_to(ROOT.resolve()))
            except ValueError:
                local_path = str(pj)
            row = {
                "amfi_code": code,
                "amc_id": meta.get("amc_id"),
                "amc_name": meta.get("amc_name"),
                "scheme_name": meta.get("scheme_name") or meta.get("amfi_name"),
                "as_of": as_of,
                "holding_count": meta.get("holding_count"),
                "disclosure_type": meta.get("disclosure_type"),
                "cadence": cadence,
                "period": meta.get("period"),
                "local_path": local_path,
            }
            prev = by_asof[as_of].get(code)
            if not prev or (row.get("holding_count") or 0) >= (prev.get("holding_count") or 0):
                by_asof[as_of][code] = row
    return dict(by_asof)


def load_cdn_filings(path: Path | None = None) -> dict[str, dict]:
    """Load catalog/filings.json from local clone or GitHub."""
    candidates = [
        path,
        ROOT / ".tmp/fund-holdings-data/catalog/filings.json",
    ]
    for p in candidates:
        if p and p.exists():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                return {f["as_of"]: f for f in doc.get("filings") or [] if f.get("as_of")}
            except (OSError, json.JSONDecodeError):
                pass
    try:
        url = "https://raw.githubusercontent.com/kushagra-agarwal-a/fund-holdings-data/main/catalog/filings.json"
        req = urllib.request.Request(url, headers={"User-Agent": "fund-disclosures/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        return {f["as_of"]: f for f in doc.get("filings") or [] if f.get("as_of")}
    except OSError:
        return {}


def default_as_of_slices() -> list[str]:
    """Headline calendar dates to report (matches CDN asof folders)."""
    discovered: set[str] = set()
    for cadence in ("monthly", "fortnightly"):
        base = ROOT / "data" / "parsed" / cadence
        if not base.exists():
            continue
        for pj in base.rglob("portfolio.json"):
            try:
                raw = json.loads(pj.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    continue
                meta = raw.get("meta") or {}
            except (OSError, json.JSONDecodeError):
                continue
            as_of = str(meta.get("as_of") or "").strip()[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
                discovered.add(as_of)
    if discovered:
        # Drop sparse accidental as_of tags (e.g. one stray parse)
        counts: Counter[str] = Counter()
        for cadence in ("monthly", "fortnightly"):
            base = ROOT / "data" / "parsed" / cadence
            if not base.exists():
                continue
            for pj in base.rglob("portfolio.json"):
                try:
                    raw = json.loads(pj.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        continue
                    meta = raw.get("meta") or {}
                except (OSError, json.JSONDecodeError):
                    continue
                as_of = str(meta.get("as_of") or "").strip()[:10]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
                    counts[as_of] += 1
        return sorted(
            (d for d in discovered if counts.get(d, 0) >= 20),
            reverse=True,
        )
    return ["2026-08-15", "2026-07-31", "2026-07-15", "2026-06-30"]


def load_parsed_holdings(period_dir: Path) -> dict[str, dict]:
    """amfi_code -> best row (newest as_of wins on duplicate)."""
    by_code: dict[str, dict] = {}
    if not period_dir.exists():
        return by_code
    for pj in period_dir.rglob("portfolio.json"):
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta = data.get("meta") or {}
        code = str(meta.get("amfi_code") or meta.get("scheme_id") or "").strip()
        if not code or not code.isdigit():
            continue
        try:
            local_path = str(pj.resolve().relative_to(ROOT.resolve()))
        except ValueError:
            local_path = str(pj)
        row = {
            "amfi_code": code,
            "amc_id": meta.get("amc_id"),
            "amc_name": meta.get("amc_name"),
            "scheme_name": meta.get("scheme_name") or meta.get("amfi_name"),
            "as_of": meta.get("as_of"),
            "holding_count": meta.get("holding_count"),
            "disclosure_type": meta.get("disclosure_type"),
            "period": meta.get("period"),
            "local_path": local_path,
        }
        prev = by_code.get(code)
        if not prev or (row.get("as_of") or "") >= (prev.get("as_of") or ""):
            by_code[code] = row
    return by_code


def index_by_all_codes(holdings: dict[str, dict], universe: list[dict]) -> dict[str, str]:
    """Map any plan AMFI code → canonical_amfi_code for universe funds."""
    canon_by_any: dict[str, str] = {}
    for f in universe:
        canon = f["canonical_amfi_code"]
        for c in f["all_amfi_codes"]:
            canon_by_any[c] = canon
        canon_by_any[canon] = canon
    return canon_by_any


PLAN_SUFFIX_RE = re.compile(
    r"\s*-\s*(?:"
    r"Cash|Div\.?|Dividend|Institution(?:al)?|Growth|IDCW|Payout|Direct|Regular"
    r")\s*$",
    re.I,
)
# AMFI pools / liability accounts — not fortnightly portfolio targets.
DEBT_COVERAGE_SKIP_RE = re.compile(
    r"(?i)\b(unclaimed|investor education pool|education pool)\b",
)
# Recently launched — no fortnightly history yet (still in AMFI NAV universe).
RECENT_LAUNCH_DEBT_CODES = frozenset(
    {
        "154538",  # Franklin India Short Term Fund
    }
)


def fund_family_key(amc_name: str, base_name: str) -> str:
    name = PLAN_SUFFIX_RE.sub("", base_name or "").strip()
    name = re.sub(r"\s+", " ", name).casefold()
    return f"{(amc_name or '').casefold()}|{name}"


def build_holdings_family_index(holdings: dict[str, dict]) -> dict[str, dict]:
    """amc|normalized-base-name → best holdings row."""
    idx: dict[str, dict] = {}
    for row in holdings.values():
        key = fund_family_key(row.get("amc_name") or "", row.get("scheme_name") or "")
        prev = idx.get(key)
        if not prev or (row.get("holding_count") or 0) >= (prev.get("holding_count") or 0):
            idx[key] = row
    return idx


def match_fund_to_holdings(
    fund: dict,
    holdings: dict[str, dict],
    canon_by_any: dict[str, str],
    family_index: dict[str, dict] | None = None,
) -> dict | None:
    for code in fund["all_amfi_codes"]:
        if code in holdings:
            return holdings[code]
    canon = fund["canonical_amfi_code"]
    if canon in holdings:
        return holdings[canon]
    if family_index is not None:
        hit = family_index.get(fund_family_key(fund["amc_name"], fund["base_name"]))
        if hit:
            return hit
    return None


def build_coverage_rows(
    universe: list[dict],
    fetch_sets: dict[str, dict[str, dict]],
    canon_by_any: dict[str, str],
) -> list[dict]:
    rows: list[dict] = []
    family_indexes = {
        label: build_holdings_family_index(holdings) for label, holdings in fetch_sets.items()
    }
    for f in sorted(universe, key=lambda x: (x["debt_label"], x["amc_name"], x["base_name"])):
        if f["debt_label"] == "Debt" and DEBT_COVERAGE_SKIP_RE.search(f["base_name"] or ""):
            continue
        if f["debt_label"] == "Debt" and f["canonical_amfi_code"] in RECENT_LAUNCH_DEBT_CODES:
            continue
        row = {**f}
        for label, holdings in fetch_sets.items():
            hit = match_fund_to_holdings(
                f,
                holdings,
                canon_by_any,
                family_indexes.get(label),
            )
            row[f"in_{label}"] = hit is not None
            if hit:
                row[f"{label}_as_of"] = hit.get("as_of")
                row[f"{label}_holdings"] = hit.get("holding_count")
                row[f"{label}_amfi_matched"] = hit.get("amfi_code")
            else:
                row[f"{label}_as_of"] = None
                row[f"{label}_holdings"] = None
                row[f"{label}_amfi_matched"] = None
        rows.append(row)
    return rows


def summarize(rows: list[dict], fetch_labels: list[str], headline_labels: list[str] | None = None) -> dict:
    debt_rows = [r for r in rows if r["debt_label"] == "Debt"]
    non_debt_rows = [r for r in rows if r["debt_label"] == "Non_debt"]
    headline_labels = headline_labels or fetch_labels

    def cov(subset: list[dict], label: str) -> dict:
        key = f"in_{label}"
        present = sum(1 for r in subset if r.get(key))
        return {"present": present, "total": len(subset), "pct": round(100 * present / len(subset), 1) if subset else 0}

    by_amc: dict[str, dict] = defaultdict(lambda: {"debt_total": 0, "debt_missing": defaultdict(int)})
    for r in debt_rows:
        amc = r["amc_name"]
        by_amc[amc]["debt_total"] += 1
        for fl in headline_labels:
            if not r.get(f"in_{fl}"):
                by_amc[amc]["debt_missing"][fl] += 1

    missing_debt = []
    for r in debt_rows:
        gaps = [fl for fl in headline_labels if not r.get(f"in_{fl}")]
        if gaps:
            missing_debt.append(
                {
                    "amc_name": r["amc_name"],
                    "base_name": r["base_name"],
                    "canonical_amfi_code": r["canonical_amfi_code"],
                    "category_short": r["category_short"],
                    "missing_in": gaps,
                }
            )

    missing_by_asof: dict[str, list[dict]] = {}
    for fl in headline_labels:
        key = f"in_{fl}"
        missing_by_asof[fl] = [
            {
                "amc_name": r["amc_name"],
                "base_name": r["base_name"],
                "canonical_amfi_code": r["canonical_amfi_code"],
                "category_short": r["category_short"],
            }
            for r in debt_rows
            if not r.get(key)
        ]

    return {
        "universe_total": len(rows),
        "debt_total": len(debt_rows),
        "non_debt_total": len(non_debt_rows),
        "coverage": {fl: cov(debt_rows, fl) for fl in fetch_labels},
        "non_debt_coverage": {fl: cov(non_debt_rows, fl) for fl in fetch_labels},
        "headline_debt_coverage": {fl: cov(debt_rows, fl) for fl in headline_labels},
        "debt_missing_by_asof": {
            fl: {"count": len(items), "funds": items[:50]} for fl, items in missing_by_asof.items()
        },
        "debt_missing_count": max(len(v) for v in missing_by_asof.values()) if missing_by_asof else 0,
        "debt_missing": missing_by_asof.get(headline_labels[0], []) if headline_labels else [],
        "debt_missing_by_amc": {
            amc: {
                "debt_total": v["debt_total"],
                **{f"missing_{fl}": v["debt_missing"].get(fl, 0) for fl in headline_labels},
            }
            for amc, v in sorted(by_amc.items(), key=lambda x: -x[1]["debt_total"])
        },
    }


def write_csv(path: Path, rows: list[dict], fetch_labels: list[str]) -> None:
    base_cols = [
        "amc_name",
        "base_name",
        "canonical_amfi_code",
        "category_short",
        "debt_label",
        "plan_count",
    ]
    fetch_cols: list[str] = []
    for fl in fetch_labels:
        fetch_cols.extend([f"in_{fl}", f"{fl}_as_of", f"{fl}_holdings", f"{fl}_amfi_matched"])
    cols = base_cols + fetch_cols
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def default_fortnightly_periods() -> list[str]:
    """Month rollup folders + explicit as-of slices present on disk."""
    root = ROOT / "data/parsed/fortnightly"
    if not root.exists():
        return ["2026-07", "2026-07-31", "2026-08", "2026-08-15"]
    periods = sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )
    return periods or ["2026-07", "2026-07-31", "2026-08", "2026-08-15"]


def fortnightly_union_groups(periods: list[str]) -> dict[str, list[str]]:
    """Group fortnightly folders into calendar-month unions (e.g. 2026-07 + 2026-07-15 + 2026-07-31)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for p in periods:
        if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", p):
            groups[p[:7]].append(p)
        else:
            groups[p].append(p)
    return dict(sorted(groups.items()))


def count_amcs(period_dir: Path) -> int:
    if not period_dir.exists():
        return 0
    return sum(1 for p in period_dir.iterdir() if p.is_dir() and not p.name.startswith("_"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--month", type=int, default=8, help="1-12")
    ap.add_argument("--navall-url", default=NAVALL_URL)
    ap.add_argument("--navall-cache", default=str(ROOT / "data/amfi/NAVAll.txt"))
    ap.add_argument("--refresh", action="store_true", help="Re-download NAVAll.txt")
    ap.add_argument(
        "--as-of",
        action="append",
        default=None,
        help="Calendar as-of date(s) to compare (default: auto-discover from parsed meta.as_of)",
    )
    ap.add_argument(
        "--cdn-filings",
        default=str(ROOT / ".tmp/fund-holdings-data/catalog/filings.json"),
        help="Local path to CDN filings.json (falls back to GitHub raw)",
    )
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    cache = Path(args.navall_cache)
    if args.refresh and cache.exists():
        cache.unlink()
    text = fetch_navall(args.navall_url, cache=cache)
    if text.startswith("Source URL:"):
        idx = text.find("Scheme Code;")
        if idx >= 0:
            text = text[idx:]

    all_schemes = parse_navall(text)
    month_schemes = filter_schemes_for_month(all_schemes, args.year, args.month)
    universe = build_amfi_universe(month_schemes)
    canon_by_any = index_by_all_codes({}, universe)

    as_of_dates = args.as_of or default_as_of_slices()
    holdings_by_asof = load_holdings_by_as_of(as_of_dates=as_of_dates)
    cdn_filings = load_cdn_filings(Path(args.cdn_filings) if args.cdn_filings else None)

    fetch_sets: dict[str, dict[str, dict]] = {}
    fetch_labels: list[str] = []
    fetch_meta: dict[str, dict] = {}

    for as_of in as_of_dates:
        label = f"asof_{as_of}"
        fetch_labels.append(label)
        fetch_sets[label] = holdings_by_asof.get(as_of, {})
        amcs = len({r.get("amc_id") for r in fetch_sets[label].values() if r.get("amc_id")})
        cadences = Counter(r.get("cadence") for r in fetch_sets[label].values())
        cdn = cdn_filings.get(as_of, {})
        fetch_meta[label] = {
            "as_of": as_of,
            "cadence": cdn.get("cadence") or "+".join(sorted(cadences.keys())) or None,
            "amc_count": amcs,
            "cdn_portfolio_count": cdn.get("portfolio_count"),
            "local_portfolio_count": len(fetch_sets[label]),
        }

    rows = build_coverage_rows(universe, fetch_sets, canon_by_any)
    summary = summarize(rows, fetch_labels, headline_labels=fetch_labels)

    month_slug = f"{args.year}-{args.month:02d}"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "navall_source": args.navall_url,
        "navall_cache": str(cache),
        "filter": {"year": args.year, "month": args.month},
        "amfi_plan_rows_in_month": len(month_schemes),
        "amfi_unique_funds": len(universe),
        "as_of_slices": fetch_meta,
        "cdn_filings": cdn_filings,
        "summary": summary,
        "funds": rows,
    }

    json_path = out_dir / f"debt_coverage_dashboard_{month_slug}.json"
    csv_path = out_dir / f"debt_coverage_dashboard_{month_slug}.csv"
    debt_missing_path = out_dir / f"debt_coverage_missing_{month_slug}.csv"

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(csv_path, rows, fetch_labels)

    with debt_missing_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["as_of", "amc_name", "base_name", "canonical_amfi_code", "category_short"],
        )
        w.writeheader()
        for fl in fetch_labels:
            as_of = fetch_meta[fl]["as_of"]
            for m in summary["debt_missing_by_asof"].get(fl, {}).get("funds", []):
                w.writerow({**m, "as_of": as_of})

    print(f"AMFI NAVAll → {len(month_schemes)} plans with NAV in {month_slug} → {len(universe)} unique funds")
    print(f"  Debt: {summary['debt_total']}  |  Non_debt: {summary['non_debt_total']}")
    print()
    print("Debt coverage vs AMFI (by meta.as_of — matches CDN asof folders):")
    print(f"  {'as_of':<12} {'debt cov':>14} {'local':>7} {'CDN':>7} {'AMCs':>5}  cadence")
    print(f"  {'-'*12} {'-'*14} {'-'*7} {'-'*7} {'-'*5}  {'-'*12}")
    for fl in fetch_labels:
        c = summary["headline_debt_coverage"][fl]
        meta = fetch_meta[fl]
        local_n = meta.get("local_portfolio_count", 0)
        cdn_n = meta.get("cdn_portfolio_count")
        cdn_s = str(cdn_n) if cdn_n is not None else "—"
        cad = meta.get("cadence") or "?"
        print(
            f"  {meta['as_of']:<12} {c['present']:>4}/{c['total']} ({c['pct']:>5}%)"
            f" {local_n:>7} {cdn_s:>7} {meta.get('amc_count', 0):>5}  {cad}"
        )
    print()
    print("Missing debt funds by as_of:")
    for fl in fetch_labels:
        block = summary["debt_missing_by_asof"].get(fl, {})
        meta = fetch_meta[fl]
        print(f"  {meta['as_of']}: {block.get('count', 0)} missing")
        for m in (block.get("funds") or [])[:5]:
            print(f"    {m['canonical_amfi_code']} | {m['base_name'][:50]}")
        if block.get("count", 0) > 5:
            print(f"    … +{block['count'] - 5} more")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {debt_missing_path}")


if __name__ == "__main__":
    main()
