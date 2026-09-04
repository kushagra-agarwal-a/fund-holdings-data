"""UTI Mutual Fund — zip with a mega 'Sebi Exposure' sheet that concatenates schemes."""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .common import (
    SchemePortfolio,
    disclosure_type_from_path,
    extract_as_of,
    load_sheets,
    parse_holdings_table,
    period_from_path,
    safe_name,
)

AMC_ID = "uti-mutual-fund"
SCHEME_START_RE = re.compile(r"(?i)SCHEME\s*CODE\s*\d+\s*STARTS|SCHEME\s*:\s*(.+)")
SCHEME_LABEL_RE = re.compile(r"(?i)^\s*SCHEME\s*:\s*(.+?)\s*$")
SKIP_INNER = re.compile(r"(?i)risk-?o-?meter|fut\s*disclo|divmast|derivative")
# Cut annexes out of each scheme block before holdings parse.
ANNEX_CUT_RE = re.compile(
    r"(?i)default\s+beyond\s+maturity|non[\s\-]?traded\s+securit|"
    r"exposure\s+to\s+credit\s+default|total\s+amt\.?\s*due|"
    r"value\s+as\s+per\s+nca|name\s+of\s+the\s+security\b|"
    r"details\s+of\s+default|a1\)\s*exposure"
)


def _trim_annex(rows: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    seen_holdings_header = False
    for row in rows:
        joined = " | ".join(c for c in row if c)
        if re.search(r"(?i)name\s+of\s+the\s+instrument|% to nav", joined):
            seen_holdings_header = True
        if seen_holdings_header and ANNEX_CUT_RE.search(joined):
            break
        out.append(row)
    return out or rows


def _prefer_workbook(names: list[str]) -> list[str]:
    scored = []
    for n in names:
        low = n.lower()
        if SKIP_INNER.search(Path(n).name):
            continue
        if not re.search(r"\.(xlsx|xls|xlsm)$", n, re.I):
            continue
        score = 0
        if "sebi" in low or "exposure" in low or "portfolio" in low:
            score += 10
        scored.append((score, n))
    scored.sort(reverse=True)
    return [n for _, n in scored] or [
        n for n in names if re.search(r"\.(xlsx|xls|xlsm)$", n, re.I)
    ]


def _split_scheme_blocks(rows: list[list[str]]) -> list[tuple[str, list[list[str]]]]:
    """Split UTI mega sheet into (scheme_name, rows_including_local_header) blocks."""
    blocks: list[tuple[str, list[list[str]]]] = []
    current_name: str | None = None
    current_rows: list[list[str]] = []

    def flush():
        nonlocal current_name, current_rows
        if current_name and current_rows:
            blocks.append((current_name, current_rows))
        current_name, current_rows = None, []

    for row in rows:
        joined = " | ".join(c for c in row if c)
        # New scheme marker
        label = None
        for cell in row:
            m = SCHEME_LABEL_RE.match(cell or "")
            if m:
                label = m.group(1).strip()
                break
        # CODE STARTS is a delimiter before the real "SCHEME: …" title — don't open a stub block.
        if re.search(r"(?i)SCHEME\s*CODE\s*\d+\s*STARTS", joined) and not label:
            flush()
            current_name = None
            current_rows = []
            continue
        if label:
            flush()
            current_name = label
            current_rows = [row]
            continue
        if current_name is None:
            continue
        current_rows.append(row)
    flush()
    # If no markers, treat whole sheet as one
    if not blocks and rows:
        blocks.append(("UTI portfolio", rows))
    return blocks


def parse_file(path: Path, *, workbook_limit: int | None = None) -> list[SchemePortfolio]:
    dtype = disclosure_type_from_path(path)
    period = period_from_path(path)
    out: list[SchemePortfolio] = []

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        workbooks: list[Path] = []
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as zf:
                preferred = _prefer_workbook([i.filename for i in zf.infolist() if not i.is_dir()])
                if workbook_limit:
                    preferred = preferred[:workbook_limit]
                for name in preferred:
                    target = td_path / safe_name(Path(name).name)
                    with zf.open(name) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    workbooks.append(target)
        else:
            workbooks = [path]

        for wb in workbooks:
            try:
                sheets = load_sheets(wb)
            except Exception:
                continue
            for sheet_name, rows in sheets:
                blocks = _split_scheme_blocks(rows)
                if workbook_limit:
                    blocks = blocks[:workbook_limit]
                for scheme_name, block_rows in blocks:
                    block_rows = _trim_annex(block_rows)
                    holdings, _ = parse_holdings_table(block_rows, prefer_leading_code=False)
                    if not holdings:
                        continue
                    out.append(
                        SchemePortfolio(
                            amc_id=AMC_ID,
                            disclosure_type=dtype,
                            period=period,
                            scheme_name=scheme_name,
                            shortcode=None,
                            as_of=extract_as_of(block_rows, filename=path.name)
                            or extract_as_of(rows, filename=wb.name),
                            source_file=path.name,
                            sheet_name=sheet_name,
                            holdings=holdings,
                            notes=[f"from:{wb.name}"],
                        )
                    )
    return out
