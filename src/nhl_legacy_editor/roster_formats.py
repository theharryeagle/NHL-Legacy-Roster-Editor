from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import zlib


STFS_TYPES = {
    b"CON ": "CON",
    b"PIRS": "PIRS",
    b"LIVE": "LIVE",
}

_CRC32_BZIP2_TABLE: tuple[int, ...] | None = None


def _crc32_bzip2(data: bytes) -> int:
    """CRC-32/BZIP2, used by the console RosterFile wrapper at offset 0x28."""
    global _CRC32_BZIP2_TABLE
    if _CRC32_BZIP2_TABLE is None:
        poly = 0x04C11DB7
        table: list[int] = []
        for value in range(256):
            crc = value << 24
            for _ in range(8):
                if crc & 0x80000000:
                    crc = ((crc << 1) ^ poly) & 0xFFFFFFFF
                else:
                    crc = (crc << 1) & 0xFFFFFFFF
            table.append(crc)
        _CRC32_BZIP2_TABLE = tuple(table)

    crc = 0xFFFFFFFF
    for byte in data:
        crc = ((crc << 8) ^ _CRC32_BZIP2_TABLE[((crc >> 24) ^ byte) & 0xFF]) & 0xFFFFFFFF
    return crc ^ 0xFFFFFFFF


def update_rosterfile_checksums(data: bytes | bytearray) -> bytes:
    """Refresh the RosterFile integrity fields after payload/container edits.

    Offsets are based on the NHL 14/Legacy console roster format:
    * 0x28: CRC-32/BZIP2 over bytes 0x2C through EOF.
    * 0x10: standard CRC32 over bytes 0x1C through EOF, after 0x28 is updated.
    """
    rebuilt = bytearray(data)
    if len(rebuilt) < 0x30 or not rebuilt.startswith(b"RosterFile"):
        return bytes(rebuilt)

    bzip_crc = _crc32_bzip2(bytes(rebuilt[0x2C:]))
    rebuilt[0x28:0x2C] = bzip_crc.to_bytes(4, "big")
    header_crc = zlib.crc32(bytes(rebuilt[0x1C:])) & 0xFFFFFFFF
    rebuilt[0x10:0x14] = header_crc.to_bytes(4, "big")
    return bytes(rebuilt)


@dataclass(slots=True)
class FileInspection:
    path: Path
    size_bytes: int
    header_magic: str
    file_kind: str
    package_type: str | None = None
    roster_markers: list[str] | None = None
    compression_offset: int | None = None
    decompressed_size: int | None = None
    payload_magic: str | None = None


def inspect_file(path: Path) -> FileInspection:
    data = path.read_bytes()
    magic = data[:4]
    header_magic = data[:16].decode("ascii", errors="replace").rstrip("\x00")

    if magic in STFS_TYPES:
        markers = sorted(
            {
                match.decode("ascii", errors="ignore")
                for match in re.findall(rb"ROSTER[ -_0-9A-Z]{0,64}", data)
            }
        )
        return FileInspection(
            path=path,
            size_bytes=len(data),
            header_magic=magic.decode("ascii", errors="replace"),
            file_kind="stfs",
            package_type=STFS_TYPES[magic],
            roster_markers=markers,
        )

    if data.startswith(b"RosterFile"):
        offset, payload = extract_roster_payload_bytes(path)
        payload_magic = payload[:16].decode("ascii", errors="replace").rstrip("\x00")
        return FileInspection(
            path=path,
            size_bytes=len(data),
            header_magic=header_magic,
            file_kind="rosterfile",
            compression_offset=offset,
            decompressed_size=len(payload),
            payload_magic=payload_magic,
        )

    return FileInspection(
        path=path,
        size_bytes=len(data),
        header_magic=header_magic,
        file_kind="unknown",
    )


def extract_roster_payload_bytes(path: Path) -> tuple[int, bytes]:
    data = path.read_bytes()
    if not data.startswith(b"RosterFile"):
        raise ValueError("File does not start with the expected RosterFile header.")

    # In the observed Xenia roster save, the compressed payload starts at 0x30.
    # We also scan nearby to avoid hard-coding a single offset too aggressively.
    for offset in range(0x20, min(len(data), 0x200)):
        if data[offset : offset + 2] != b"\x78\x9c":
            continue
        try:
            payload = zlib.decompress(data[offset:])
        except zlib.error:
            continue
        return offset, payload

    raise ValueError("Could not locate a zlib-compressed roster payload.")


def extract_roster_payload(path: Path, output_path: Path) -> Path:
    _, payload = extract_roster_payload_bytes(path)
    output_path.write_bytes(payload)
    return output_path


def replace_roster_payload(path: Path, payload: bytes, output_path: Path | None = None) -> Path:
    data = path.read_bytes()
    offset, old_payload = extract_roster_payload_bytes(path)
    expected_payload_size = int.from_bytes(data[0x24:0x28], "big") if len(data) >= 0x30 else len(old_payload)
    if expected_payload_size <= 0:
        expected_payload_size = len(old_payload)
    if len(payload) > expected_payload_size:
        raise ValueError(
            f"Edited DB payload ({len(payload)} bytes) exceeds roster header size ({expected_payload_size} bytes)."
        )
    if len(payload) < expected_payload_size:
        padding_source = old_payload[len(payload):expected_payload_size]
        payload = payload + padding_source + (b"\xdb" * (expected_payload_size - len(payload) - len(padding_source)))
    recompressed = zlib.compress(payload)

    # Observed Xenia roster files keep a compressed zlib stream followed by zero padding.
    # We preserve the original file length when possible so the container stays shape-stable.
    tail_capacity = len(data) - offset
    if len(recompressed) > tail_capacity:
        raise ValueError(
            f"Compressed payload ({len(recompressed)} bytes) exceeds available container tail ({tail_capacity} bytes)."
        )

    rebuilt = bytearray(data[:offset])
    if len(rebuilt) >= 0x30:
        rebuilt[0x24:0x28] = expected_payload_size.to_bytes(4, "big")
        rebuilt[0x2C:0x30] = len(recompressed).to_bytes(4, "little")
    rebuilt.extend(recompressed)
    rebuilt.extend(b"\x00" * (tail_capacity - len(recompressed)))

    target = output_path or path
    target.write_bytes(update_rosterfile_checksums(rebuilt))
    _, rebuilt_payload = extract_roster_payload_bytes(target)
    if len(rebuilt_payload) != expected_payload_size:
        raise ValueError(
            f"Rebuilt roster payload size mismatch: expected {expected_payload_size}, got {len(rebuilt_payload)}."
        )
    return target
