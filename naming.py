"""
Output folder and file names with DBeaver-style variables.

Supported variables (in both OUTPUT_DIR and OUTPUT_FILE_PATTERN):
    ${scriptFilename}  .sql file name without extension
    ${timestamp}       export time, formatted with OUTPUT_TIMESTAMP_PATTERN
    ${yyyy} ${MM} ${dd} ${monthName}   date parts (for monthly folders)

OUTPUT_TIMESTAMP_PATTERN uses Java/DBeaver patterns, for example:
    dd-MM-yyyy        -> 21-09-2026
    yyyyMMddHHmm      -> 202609210915
    dd MMMM yyyy      -> 21 September 2026
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

# Java token -> function that renders it. Order matters: longest tokens first.
_TOKENS = {
    "yyyy": lambda d: f"{d.year:04d}",
    "MMMM": lambda d: MONTHS[d.month - 1],
    "MMM":  lambda d: MONTHS[d.month - 1][:3],
    "SSS":  lambda d: f"{d.microsecond // 1000:03d}",
    "yy":   lambda d: f"{d.year % 100:02d}",
    "MM":   lambda d: f"{d.month:02d}",
    "dd":   lambda d: f"{d.day:02d}",
    "HH":   lambda d: f"{d.hour:02d}",
    "hh":   lambda d: f"{(d.hour % 12) or 12:02d}",
    "mm":   lambda d: f"{d.minute:02d}",
    "ss":   lambda d: f"{d.second:02d}",
    "a":    lambda d: "AM" if d.hour < 12 else "PM",
}
_TOKEN_RE = re.compile("|".join(_TOKENS))
_VAR_RE = re.compile(r"\$\{(\w+)\}")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*]')


def format_java_date(pattern: str, when: datetime) -> str:
    return _TOKEN_RE.sub(lambda m: _TOKENS[m.group(0)](when), pattern)


def _expand(template: str, values: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise SystemExit(f"[naming] Unknown variable ${{{key}}}. Available: "
                             + ", ".join(f"${{{k}}}" for k in values))
        return values[key]
    return _VAR_RE.sub(repl, template)


def build_output_path(dir_template: str, file_template: str, timestamp_pattern: str,
                      script_name: str, base_dir: Path, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    values = {
        "scriptFilename": script_name,
        "timestamp": format_java_date(timestamp_pattern, when),
        "yyyy": f"{when.year:04d}",
        "MM": f"{when.month:02d}",
        "dd": f"{when.day:02d}",
        "monthName": MONTHS[when.month - 1],
    }
    folder = Path(_expand(dir_template, values)).expanduser()
    if not folder.is_absolute():
        folder = base_dir / folder

    filename = _BAD_CHARS.sub("_", _expand(file_template, values)).strip()
    if not filename.lower().endswith(".csv"):
        filename += ".csv"
    return folder / filename


def autofix_name(path: Path) -> Path:
    """If the file already exists, append _1, _2, ... like DBeaver's 'Autofix name'."""
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1
