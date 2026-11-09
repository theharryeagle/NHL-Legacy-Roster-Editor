"""Inspect and rebuild copies of NHL 24 PC custom roster slots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from shutil import copytree, rmtree
from struct import pack, pack_into, unpack_from

from lz4.frame import BLOCKSIZE_MAX64KB, LZ4FrameDecompressor, compress

from .roster_formats import _crc32_bzip2


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
    return _decode_roster(data)[0]


def _decode_roster(data: bytes) -> tuple[Nhl24Roster, bytes, int, int]:
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
    roster = _parse_database(decoded)
    return roster, decoded[len(roster.database):], roster_record, payload_end


def _parse_database(decoded: bytes) -> Nhl24Roster:
    if len(decoded) < _DB_DIRECTORY_OFFSET or not decoded.startswith(_EA_DB_MAGIC):
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


def _patch_database(original: Nhl24Roster, replacement: bytes) -> bytes:
    updated = _parse_database(replacement)
    if len(replacement) != len(original.database) or updated.tables != original.tables:
        raise ValueError("Experimental export requires the original database size and table layout.")
    first_table = min(start for start, _ in original.tables.values())
    if replacement[:first_table] != original.database[:first_table]:
        raise ValueError("Experimental export requires the original database header and directory.")

    patched = bytearray(replacement)
    for name, (start, end) in original.tables.items():
        previous = original.database[start:end]
        current = replacement[start:end]
        if current == previous:
            continue
        if len(previous) < 40:
            raise ValueError(f"Table {name} is too short to rebuild.")
        fields_end = 36 + 16 * unpack_from("<I", previous, 24)[0]
        if fields_end > len(previous) - 4 or current[:fields_end] != previous[:fields_end]:
            raise ValueError(f"Table {name} changed its header or field definitions.")
        stored_crc = unpack_from("<I", previous, len(previous) - 4)[0]
        if (_crc32_bzip2(previous[36:-4]) ^ 0xFFFFFFFF) != stored_crc:
            raise ValueError(f"Table {name} has an unrecognized records checksum.")
        pack_into("<I", patched, end - 4, _crc32_bzip2(current[36:-4]) ^ 0xFFFFFFFF)
    return bytes(patched)


def _rebuild_index(index: bytes, records: list[tuple[int, int]], delta: int) -> bytes:
    if len(index) != 4 * 80:
        raise ValueError("Unrecognized NHL 24 INDEX size.")
    result = bytearray(index)
    expected_names = (None, b"__SaveLoadFileInfo", b"__SaveLoadMetaData", b"__SaveloadVer")
    for entry, (record, name) in enumerate(zip((1, 0, 3, 2), expected_names)):
        offset = entry * 80
        if name is not None and index[offset:offset + 32].split(b"\0", 1)[0] != name:
            raise ValueError("Unrecognized NHL 24 INDEX record name.")
        start, end = records[record]
        if unpack_from("<QQQQ", index, offset + 32) != (1, end - start, start, end):
            raise ValueError("NHL 24 INDEX does not match DATA record boundaries.")
        if record >= 2:
            start += delta
        if record >= 1:
            end += delta
        pack_into("<QQQ", result, offset + 40, end - start, start, end)
    return bytes(result)


def export_nhl24_slot(source: Path, destination: Path, *, experimental_database: bytes | None = None) -> Path:
    """Write a new slot copy. An edited database requires in-game validation."""
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("Choose a new destination outside the source roster folder.")
    name = (source / "NAME").read_bytes()
    if not re.fullmatch(rb"RosterCustom[0-9]", name) or destination.name != name.decode("ascii"):
        raise ValueError("The destination folder must use the slot name stored in NAME (RosterCustom0–9).")
    index = (source / "INDEX").read_bytes()
    with (source / "DATA").open("rb") as file:
        data = file.read(_MAX_ROSTER_SIZE + 1)
    original, tail, start, end = _decode_roster(data)
    if index[:32].split(b"\0", 1)[0] != name:
        raise ValueError("The NHL 24 INDEX slot name does not match NAME.")

    if experimental_database is not None:
        database = _patch_database(original, experimental_database)
        if database != original.database:
            if end + 16 > len(data):
                raise ValueError("NHL 24 save version record is missing.")
            version_end = end + 16 + unpack_from("<Q", data, end)[0]
            if version_end + 16 > len(data) or version_end + 16 + unpack_from("<Q", data, version_end)[0] != len(data):
                raise ValueError("NHL 24 metadata record has invalid boundaries.")
            decoded = database + tail
            frame = compress(decoded, block_size=BLOCKSIZE_MAX64KB, block_linked=False,
                             content_checksum=True, store_size=False)
            payload = b"\x01" + frame + pack("<I", len(decoded))
            new_record = pack("<Q", len(payload)) + data[start + 8:start + 16] + payload
            index = _rebuild_index(index, [(0, start), (start, end), (end, version_end), (version_end, len(data))],
                                   len(new_record) - (end - start))
            data = data[:start] + new_record + data[end:]
            if _decode_roster(data)[0].database != database:
                raise ValueError("Rebuilt NHL 24 DATA failed verification.")

    destination.mkdir()
    try:
        copytree(source, destination, dirs_exist_ok=True)
        (destination / "DATA").write_bytes(data)
        (destination / "INDEX").write_bytes(index)
    except BaseException:
        rmtree(destination)
        raise
    return destination
