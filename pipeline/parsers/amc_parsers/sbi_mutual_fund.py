"""SBI Mutual Fund — CAMS single-sheet per scheme (skip mega all-schemes here)."""
from __future__ import annotations

import re
from pathlib import Path

from .cams_sheet import parse_cams_file

AMC_ID = "sbi-mutual-fund"
SKIP_FILE_RE = re.compile(r"(?i)all[-_\s]?schemes|debt-schemes-fortnightly-portfolio---as-on")


def parse_file(path: Path):
    if SKIP_FILE_RE.search(path.name):
        return []
    return parse_cams_file(path, amc_id=AMC_ID)
