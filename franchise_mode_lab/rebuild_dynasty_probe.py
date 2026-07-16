from __future__ import annotations

import argparse
from pathlib import Path
import zlib


def _first_zlib_region(data: bytes, offset: int = 0x30) -> tuple[int, int, bytes]:
    obj = zlib.decompressobj()
    payload = obj.decompress(data[offset:])
    payload += obj.flush()
    consumed = len(data[offset:]) - len(obj.unused_data)
    if consumed <= 0 or not payload.startswith(b"DB\x00"):
        raise RuntimeError("Could not locate the primary Dynasty DB zlib stream.")
    return offset, consumed, obj.unused_data


def rebuild_dynasty(input_dynasty: Path, replacement_db: Path, output_dynasty: Path) -> None:
    original = input_dynasty.read_bytes()
    offset, original_compressed_size, tail = _first_zlib_region(original)
    replacement_payload = replacement_db.read_bytes()
    replacement_compressed = zlib.compress(replacement_payload)
    if len(replacement_compressed) > original_compressed_size:
        raise RuntimeError(
            f"Replacement compressed DB is too large: {len(replacement_compressed)} > {original_compressed_size}"
        )

    # Keep the trailing content at its original offset. This gives the game the
    # same wrapper layout while the zlib stream itself still ends naturally.
    padding = b"\x00" * (original_compressed_size - len(replacement_compressed))
    rebuilt = bytearray(original[:offset] + replacement_compressed + padding + tail)
    if offset == 0x30 and rebuilt.startswith(b"Dynasty"):
        # Dynasty stores the decompressed DB size as 16-bit words, unlike the
        # roster wrapper which stores bytes.
        rebuilt[0x24:0x28] = (len(replacement_payload) // 2).to_bytes(4, "big")
        rebuilt[0x2C:0x30] = len(replacement_compressed).to_bytes(4, "little")
    output_dynasty.parent.mkdir(parents=True, exist_ok=True)
    output_dynasty.write_bytes(rebuilt)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild a lab-only Dynasty file with a replacement primary DB.")
    parser.add_argument("--input-dynasty", type=Path, default=Path("inputs/DYNASTY 20260620125233"))
    parser.add_argument("--replacement-db", type=Path, default=Path("working/dynasty_32team_probe.db"))
    parser.add_argument("--output-dynasty", type=Path, default=Path("working/DYNASTY_32TEAM_PROBE"))
    args = parser.parse_args()
    rebuild_dynasty(args.input_dynasty, args.replacement_db, args.output_dynasty)
    print(f"wrote {args.output_dynasty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
