

from typing import Any, Dict, List

import click

COLORS = {
    "name": "green",
    "version": "blue",
    "old_version": "red",
    "build": "yellow",
    "channel": "bright_white",
    "pypi": "cyan",
    "comment": "white",
    "platform": "magenta"
}


def pkg_col_lengths(pkg_infos: List[Dict[str, Any]], cols: List[str]):
    return {col: max(map(lambda x: len(x.get(col, None) or ""), pkg_infos)) for col in cols}


def format_pkg_line(pkg_info: dict, col_lengths: Dict[str, int], bold: bool = False):
    cols = list(col_lengths.keys())
    col_strs = []
    for col in cols:
        if col_lengths[col] == 0:
            continue
        if col == "name" and pkg_info.get("channel", None) == "pypi":
            fg = COLORS["pypi"]
        else:
            fg = COLORS.get(col, None)
        col_strs.append(click.style((pkg_info.get(col, None) or "").ljust(col_lengths[col]), fg=fg, bold=bold))

    if col_lengths.get("old_version", 0) > 0 and col_lengths.get("version", 0) > 0:
        i = cols.index("old_version")
        oldv = col_strs.pop(i)
        col_strs[i] = f"{oldv} ➜ {col_strs[i]}"
    return "  ".join(col_strs)