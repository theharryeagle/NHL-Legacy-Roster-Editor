from __future__ import annotations

from pathlib import Path

from .ea_big import list_big_archive


def list_archive_matches(file_path: Path, filters: list[str] | None = None) -> list[dict[str, object]]:
    return [
        {
            "offset_hex": entry.offset_hex,
            "size_bytes": entry.size_bytes,
            "path": entry.path,
        }
        for entry in list_big_archive(file_path, filters=filters)
    ]
