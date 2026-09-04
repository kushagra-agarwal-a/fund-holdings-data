"""Choice Mutual Fund — CAMS single-sheet per scheme."""
from __future__ import annotations

from pathlib import Path

from .cams_sheet import parse_cams_file

AMC_ID = "choice-mutual-fund"


def parse_file(path: Path):
    return parse_cams_file(path, amc_id=AMC_ID)
