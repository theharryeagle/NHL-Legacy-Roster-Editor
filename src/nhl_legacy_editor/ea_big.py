from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


DEFAULT_QUICKBMS_EXE = Path(__file__).resolve().parents[2] / "tools" / "quickbms" / "app" / "quickbms.exe"
DEFAULT_FIGHTNIGHT_BMS = Path(__file__).resolve().parents[2] / "tools" / "quickbms" / "fightnight.bms"


@dataclass(slots=True)
class BigArchiveEntry:
    offset_hex: str
    size_bytes: int
    path: str


def list_big_archive(
    archive_path: Path,
    *,
    quickbms_exe: Path | None = None,
    script_path: Path | None = None,
    filters: list[str] | None = None,
) -> list[BigArchiveEntry]:
    quickbms = quickbms_exe or DEFAULT_QUICKBMS_EXE
    script = script_path or DEFAULT_FIGHTNIGHT_BMS
    if not quickbms.exists():
        raise FileNotFoundError(f"QuickBMS not found: {quickbms}")
    if not script.exists():
        raise FileNotFoundError(f"QuickBMS script not found: {script}")
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    result = subprocess.run(
        [str(quickbms), "-l", str(script), str(archive_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    wanted = [item.casefold() for item in (filters or [])]
    line_pattern = re.compile(r"^\s*([0-9A-Fa-f]{8})\s+(\d+)\s+(.+?)\s*$")
    entries: list[BigArchiveEntry] = []
    for raw_line in result.stdout.splitlines():
        match = line_pattern.match(raw_line)
        if match is None:
            continue
        offset_hex = match.group(1)
        size_bytes = int(match.group(2))
        path = match.group(3)
        if wanted and not any(item in path.casefold() for item in wanted):
            continue
        entries.append(BigArchiveEntry(offset_hex=offset_hex, size_bytes=size_bytes, path=path))
    return entries
