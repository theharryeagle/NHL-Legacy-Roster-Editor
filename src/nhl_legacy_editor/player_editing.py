from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .attribute_map import attribute_specs_by_field, build_attribute_editor_rows, display_to_raw
from .player_tools import (
    PLAYER_BIO_TABLE_INDEX,
    PLAYER_FLAGS_TABLE_INDEX,
    PLAYER_INSTANCE_TABLE_INDEX,
    PLAYER_RATINGS_TABLE_INDEX,
)
from .review_tools import diff_player_snapshots
from .player_tools import get_player_snapshot
from .tdb_access import TdbAccess
from .move_tools import find_primary_player_instance
from .team_tools import INSTANCE_KEY_FIELD


@dataclass(slots=True)
class PlayerRowPointers:
    first_name: str
    last_name: str
    player_id: int
    bio_record_index: int
    ratings_record_index: int | None


def _read_full_table(access: TdbAccess, db_path: Path, table_index: int) -> list[dict[str, object]]:
    _table, _fields, rows = access.sample_records(
        db_path,
        table_index,
        limit=access.list_tables(db_path)[table_index].record_count,
    )
    return rows


def find_player_rows(db_path: Path, first_name: str, last_name: str) -> PlayerRowPointers:
    access = TdbAccess()
    bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    bio_record_index = next(
        (
            index
            for index, row in enumerate(bio_rows)
            if str(row.get("PedH") or "").strip() == first_name
            and str(row.get("RMbQ") or "").strip() == last_name
        ),
        None,
    )
    if bio_record_index is None:
        raise RuntimeError(f"Player not found: {first_name} {last_name}")
    bio = bio_rows[bio_record_index]
    player_id = int(bio.get("zIBw") or -1)

    ratings_rows = _read_full_table(access, db_path, PLAYER_RATINGS_TABLE_INDEX)
    ratings_record_index = next(
        (index for index, row in enumerate(ratings_rows) if int(row.get("zIBw") or -1) == player_id),
        None,
    )
    return PlayerRowPointers(
        first_name=first_name,
        last_name=last_name,
        player_id=player_id,
        bio_record_index=bio_record_index,
        ratings_record_index=ratings_record_index,
    )


def list_editable_rating_fields(snapshot) -> list[str]:
    if snapshot is None or snapshot.ratings_row is None:
        return []
    return sorted(
        key
        for key, value in snapshot.ratings_row.items()
        if key != "zIBw" and isinstance(value, int)
    )


def build_player_attribute_rows(snapshot) -> list[dict[str, object]]:
    return build_attribute_editor_rows(None if snapshot is None else snapshot.ratings_row)


def parse_attribute_form_updates(form_data) -> dict[str, int]:
    specs = attribute_specs_by_field()
    updates: dict[str, int] = {}
    for field, spec in specs.items():
        if field not in form_data:
            continue
        updates[field] = display_to_raw(spec, form_data[field])
    return updates


def update_player_ratings(
    db_path: Path,
    first_name: str,
    last_name: str,
    updates: dict[str, int],
) -> dict[str, object]:
    pointers = find_player_rows(db_path, first_name, last_name)
    if pointers.ratings_record_index is None:
        raise RuntimeError(f"Ratings row not found: {first_name} {last_name}")

    access = TdbAccess()
    ratings_rows = _read_full_table(access, db_path, PLAYER_RATINGS_TABLE_INDEX)
    before_row = ratings_rows[pointers.ratings_record_index]
    access.update_record_fields(
        db_path,
        PLAYER_RATINGS_TABLE_INDEX,
        pointers.ratings_record_index,
        updates,
    )
    changes = [
        {
            "section": "ratings",
            "field": field,
            "before": before_row.get(field),
            "after": value,
        }
        for field, value in updates.items()
        if before_row.get(field) != value
    ]
    return {
        "player": f"{first_name} {last_name}",
        "updated_fields": updates,
        "changes": changes,
    }


def update_player_bio(
    db_path: Path,
    first_name: str,
    last_name: str,
    updates: dict[str, object],
) -> dict[str, object]:
    pointers = find_player_rows(db_path, first_name, last_name)
    access = TdbAccess()
    bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    before_row = bio_rows[pointers.bio_record_index]
    access.update_record_fields(
        db_path,
        PLAYER_BIO_TABLE_INDEX,
        pointers.bio_record_index,
        updates,
    )
    changes = [
        {
            "section": "bio",
            "field": field,
            "before": before_row.get(field),
            "after": value,
        }
        for field, value in updates.items()
        if before_row.get(field) != value
    ]
    return {
        "player": f"{first_name} {last_name}",
        "updated_fields": updates,
        "changes": changes,
    }


def update_player_flags(
    db_path: Path,
    first_name: str,
    last_name: str,
    updates: dict[str, object],
) -> dict[str, object]:
    pointers = find_player_rows(db_path, first_name, last_name)
    flags_rows = _read_full_table(TdbAccess(), db_path, PLAYER_FLAGS_TABLE_INDEX)
    flags_record_index = next(
        (
            index
            for index, row in enumerate(flags_rows)
            if int(row.get("zIBw") or -1) == pointers.player_id
        ),
        None,
    )
    if flags_record_index is None:
        raise RuntimeError(f"Flags row not found: {first_name} {last_name}")

    access = TdbAccess()
    before_row = flags_rows[flags_record_index]
    access.update_record_fields(
        db_path,
        PLAYER_FLAGS_TABLE_INDEX,
        flags_record_index,
        updates,
    )
    changes = [
        {
            "section": "flags",
            "field": field,
            "before": before_row.get(field),
            "after": value,
        }
        for field, value in updates.items()
        if before_row.get(field) != value
    ]
    return {
        "player": f"{first_name} {last_name}",
        "updated_fields": updates,
        "changes": changes,
    }


def update_player_instance_fields(
    db_path: Path,
    first_name: str,
    last_name: str,
    updates: dict[str, object],
) -> dict[str, object]:
    snapshot = get_player_snapshot(db_path, first_name, last_name)
    if snapshot is None:
        raise RuntimeError(f"Player not found: {first_name} {last_name}")
    linked_instance_ids = {
        int(row.get(INSTANCE_KEY_FIELD) or -1)
        for row in snapshot.instance_rows
        if row.get(INSTANCE_KEY_FIELD) is not None
    }
    if not linked_instance_ids:
        instance = find_primary_player_instance(db_path, first_name, last_name)
        linked_instance_ids = {instance.instance_id}

    instance_rows = _read_full_table(TdbAccess(), db_path, PLAYER_INSTANCE_TABLE_INDEX)
    record_indexes = [
        index
        for index, row in enumerate(instance_rows)
        if int(row.get(INSTANCE_KEY_FIELD) or -1) in linked_instance_ids
    ]
    if not record_indexes:
        raise RuntimeError(f"Instance row not found: {first_name} {last_name}")

    access = TdbAccess()
    before_rows = {index: dict(instance_rows[index]) for index in record_indexes}
    for record_index in record_indexes:
        access.update_record_fields(
            db_path,
            PLAYER_INSTANCE_TABLE_INDEX,
            record_index,
            updates,
        )
    changes = [
        {
            "section": "instance_rows",
            "field": field,
            "before": before_rows[record_index].get(field),
            "after": value,
        }
        for record_index in record_indexes
        for field, value in updates.items()
        if before_rows[record_index].get(field) != value
    ]
    return {
        "player": f"{first_name} {last_name}",
        "instance_ids": sorted(linked_instance_ids),
        "updated_count": len(record_indexes),
        "updated_fields": updates,
        "changes": changes,
    }
