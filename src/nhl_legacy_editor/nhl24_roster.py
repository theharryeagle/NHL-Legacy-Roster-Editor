"""Read-only support for the NHL 24 PC custom roster save format."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from struct import unpack_from

from lz4.frame import LZ4FrameDecompressor


_SAVE_RECORD_HEADER_SIZE = 16
_LZ4_ROSTER_MARKER = b"\x01\x04\x22\x4d\x18"
_EA_DB_MAGIC = b"DB\x00\x08\x00\x00\x00\x00"
_DB_DIRECTORY_OFFSET = 24
_MAX_ROSTER_SIZE = 64 * 1024 * 1024


@dataclass(frozen=True)
class Nhl24Roster:
    database: bytes
    tables: dict[str, tuple[int, int]]

    def summary(self) -> dict[str, object]:
        return {
            "format": "NHL 24 PC custom roster",
            "database_bytes": len(self.database),
            "table_count": len(self.tables),
            "table_ids": list(self.tables),
        }


def read_nhl24_roster(path: Path) -> Nhl24Roster:
    """Read a custom roster slot folder or its DATA file."""
    if path.is_dir():
        path = path / "DATA"

    with path.open("rb") as source:
        data = source.read(_MAX_ROSTER_SIZE + 1)
    if len(data) > _MAX_ROSTER_SIZE:
        raise ValueError("NHL 24 roster DATA exceeds the supported size.")
    if len(data) < _SAVE_RECORD_HEADER_SIZE:
        raise ValueError("NHL 24 roster DATA header is truncated.")

    # DATA starts with a save-info record, then a roster record. Each has a
    # little-endian 64-bit payload size and an eight-byte timestamp.
    roster_record = _SAVE_RECORD_HEADER_SIZE + unpack_from("<Q", data)[0]
    payload_start = roster_record + _SAVE_RECORD_HEADER_SIZE
    if payload_start > len(data):
        raise ValueError("NHL 24 roster record header is missing.")
    payload_end = payload_start + unpack_from("<Q", data, roster_record)[0]
    payload = memoryview(data)[payload_start:payload_end]
    if payload_end > len(data) or len(payload) < 9 or payload[:5] != _LZ4_ROSTER_MARKER:
        raise ValueError("NHL 24 roster record does not contain the expected LZ4 payload.")

    # The record ends with the size of the uncompressed payload. The EA DB
    # occupies only the leading portion of that payload.
    decoded_size = unpack_from("<I", payload, len(payload) - 4)[0]
    if not _DB_DIRECTORY_OFFSET <= decoded_size <= _MAX_ROSTER_SIZE:
        raise ValueError("NHL 24 roster has an invalid uncompressed size.")
    decoder = LZ4FrameDecompressor()
    try:
        decoded = decoder.decompress(payload[1:-4], max_length=decoded_size + 1)
    except RuntimeError as exc:
        raise ValueError(f"Could not decompress the NHL 24 roster: {exc}") from exc
    if len(decoded) != decoded_size or not decoder.eof or decoder.unused_data:
        raise ValueError("NHL 24 roster LZ4 frame does not match its declared size.")
    if not decoded.startswith(_EA_DB_MAGIC):
        raise ValueError("NHL 24 roster is missing the expected EA database header.")

    db_size, _, table_count = unpack_from("<III", decoded, 8)
    table_data_start = _DB_DIRECTORY_OFFSET + table_count * 8 + 4
    if not table_count or not table_data_start < db_size <= len(decoded):
        raise ValueError("NHL 24 roster has an invalid EA database directory.")

    entries = []
    for index in range(table_count):
        name, offset = unpack_from("<4sI", decoded, _DB_DIRECTORY_OFFSET + index * 8)
        if not name.isalnum():
            raise ValueError("NHL 24 roster has an invalid table ID.")
        entries.append((table_data_start + offset, name.decode("ascii")))
    entries.sort()
    table_ends = [offset for offset, _ in entries[1:]] + [db_size]
    tables = {}
    for (start, name), end in zip(entries, table_ends):
        if not start < end <= db_size or name in tables:
            raise ValueError("NHL 24 roster has overlapping or duplicate tables.")
        tables[name] = (start, end)
    return Nhl24Roster(decoded[:db_size], tables)


def compare_nhl24_rosters(before: Nhl24Roster, after: Nhl24Roster) -> dict[str, object]:
    """Find changed table spans after decompression, excluding the save wrapper."""
    changed = []
    for name in before.tables.keys() & after.tables.keys():
        old_start, old_end = before.tables[name]
        new_start, new_end = after.tables[name]
        if before.database[old_start:old_end] != after.database[new_start:new_end]:
            changed.append(name)
    return {
        "database_changed": before.database != after.database,
        "changed_tables": sorted(changed),
        "added_tables": sorted(after.tables.keys() - before.tables.keys()),
        "removed_tables": sorted(before.tables.keys() - after.tables.keys()),
    }
