from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TextHit:
    offset: int
    text: str
    context: str


def _ascii_window(data: bytes, start: int, end: int) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data[start:end])


def find_text_hits(path: Path, needle: str, context_bytes: int = 64) -> list[TextHit]:
    data = path.read_bytes()
    needle_bytes = needle.encode("utf-8")
    hits: list[TextHit] = []
    start = 0
    while True:
        idx = data.find(needle_bytes, start)
        if idx == -1:
            break
        left = max(0, idx - context_bytes)
        right = min(len(data), idx + len(needle_bytes) + context_bytes)
        hits.append(
            TextHit(
                offset=idx,
                text=needle,
                context=_ascii_window(data, left, right),
            )
        )
        start = idx + 1
    return hits


def extract_ascii_strings(
    path: Path,
    min_length: int = 4,
    limit: int | None = None,
) -> list[tuple[int, str]]:
    data = path.read_bytes()
    strings: list[tuple[int, str]] = []
    start: int | None = None

    for idx, byte in enumerate(data):
        if 32 <= byte < 127:
            if start is None:
                start = idx
            continue
        if start is not None and idx - start >= min_length:
            strings.append((start, data[start:idx].decode("ascii", errors="ignore")))
            if limit is not None and len(strings) >= limit:
                return strings
        start = None

    if start is not None and len(data) - start >= min_length:
        strings.append((start, data[start:].decode("ascii", errors="ignore")))

    return strings if limit is None else strings[:limit]
