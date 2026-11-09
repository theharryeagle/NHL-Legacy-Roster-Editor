from pathlib import Path
from struct import pack

from lz4.frame import compress
import pytest

from nhl_legacy_editor.nhl24_roster import compare_nhl24_rosters, read_nhl24_roster


def _sample_data(last_table: bytes = b"old!", timestamp: int = 0) -> bytes:
    db = (
        b"DB\x00\x08\x00\x00\x00\x00"
        + pack("<4I", 52, 0, 2, 0)
        + pack("<4sI4sI", b"Bio1", 0, b"Rate", 4)
        + b"\x00" * 4
        + b"bio!"
        + last_table
    )
    decoded = db + b"trailing metadata"
    payload = b"\x01" + compress(decoded) + pack("<I", len(decoded))
    metadata = b'{"IsBackup":false}'
    return pack("<QQ", len(metadata), timestamp) + metadata + pack("<QQ", len(payload), timestamp) + payload


def test_reads_slot_and_compares_database_tables(tmp_path: Path) -> None:
    data = tmp_path / "DATA"
    data.write_bytes(_sample_data())
    before = read_nhl24_roster(tmp_path)
    assert list(before.tables) == ["Bio1", "Rate"]
    assert len(before.database) == 52

    data.write_bytes(_sample_data(timestamp=2))
    assert compare_nhl24_rosters(before, read_nhl24_roster(data))["database_changed"] is False

    data.write_bytes(_sample_data(last_table=b"new!"))
    assert compare_nhl24_rosters(before, read_nhl24_roster(data)) == {
        "database_changed": True,
        "changed_tables": ["Rate"],
        "added_tables": [],
        "removed_tables": [],
    }


@pytest.mark.parametrize(
    "data",
    [b"RosterFile" + b"\x00" * 60, _sample_data()[:-1], _sample_data()[:-4] + pack("<I", 1024)],
)
def test_rejects_incompatible_or_invalid_save(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "DATA"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        read_nhl24_roster(path)
