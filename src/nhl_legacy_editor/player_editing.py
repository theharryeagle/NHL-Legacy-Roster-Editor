from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .attribute_map import attribute_specs_by_field, build_attribute_editor_rows, display_to_raw
from .player_tools import (
    GOALIE_RATINGS_TABLE_INDEX,
    PLAYER_BIO_TABLE_INDEX,
    PLAYER_FLAGS_TABLE_INDEX,
    PLAYER_INSTANCE_TABLE_INDEX,
    PLAYER_RATINGS_TABLE_INDEX,
    PlayerSnapshotCache,
)
from .review_tools import diff_player_snapshots
from .player_tools import get_player_snapshot
from .tdb_access import TdbAccess
from .move_tools import find_primary_player_instance
from .team_tools import INSTANCE_KEY_FIELD


CONTRACT_CAP_HIT_FIELD = "dhKk"
CONTRACT_UNIT_DOLLARS = 5_000
MINIMUM_CONTRACT_DOLLARS = 675_000
CONTRACT_LENGTH_FIELD = "GDhI"
CONTRACT_STATUS_FIELD = "QwoG"
CONTRACT_TWO_WAY_FIELD = "DVoL"
CONTRACT_MULTI_YEAR_FIELD = "gHmt"
CONTRACT_ENTRY_LEVEL_FIELD = "yvUt"
CONTRACT_EXTENSION_CAP_HIT_FIELD = "IzRv"
CONTRACT_EXTENSION_LENGTH_FIELD = "IrlK"
CONTRACT_EXTENSION_TWO_WAY_FIELD = "xdoJ"


@dataclass(slots=True)
class PlayerRowPointers:
    first_name: str
    last_name: str
    player_id: int
    bio_record_index: int
    ratings_record_index: int | None
    goalie_ratings_record_index: int | None


def _read_full_table(access: TdbAccess, db_path: Path, table_index: int | str) -> list[dict[str, object]]:
    resolved_index = access.resolve_table_index(db_path, table_index)
    _table, _fields, rows = access.sample_records(
        db_path,
        table_index,
        limit=access.list_tables(db_path)[resolved_index].record_count,
    )
    return rows


def _read_record_fields(
    access: TdbAccess,
    db_path: Path,
    table_ref: int | str,
    record_index: int,
    field_names: set[str],
) -> dict[str, object]:
    table_index = access.resolve_table_index(db_path, table_ref)
    with access.open_database(db_path) as db_index:
        table = access.get_table_properties(db_index, table_index)
        fields = {
            field.name: field
            for field in (
                access.get_field_properties(db_index, table.name, field_index)
                for field_index in range(table.field_count)
            )
            if field.name in field_names
        }
        missing = field_names.difference(fields)
        if missing:
            raise RuntimeError(f"Fields not found in {table.name}: {', '.join(sorted(missing))}")
        return {
            field_name: access.get_field_value(db_index, table.name, fields[field_name], record_index)
            for field_name in field_names
        }


def _verify_record_fields(
    access: TdbAccess,
    db_path: Path,
    table_ref: int | str,
    record_index: int,
    expected: dict[str, object],
) -> dict[str, object]:
    actual = _read_record_fields(access, db_path, table_ref, record_index, set(expected))
    mismatches = {
        field: (expected_value, actual.get(field))
        for field, expected_value in expected.items()
        if actual.get(field) != expected_value
    }
    if mismatches:
        details = ", ".join(
            f"{field}: expected {expected_value!r}, read {actual_value!r}"
            for field, (expected_value, actual_value) in sorted(mismatches.items())
        )
        raise RuntimeError(f"Roster contract verification failed ({details})")
    return actual


def find_player_rows(
    db_path: Path,
    first_name: str,
    last_name: str,
    player_id: int | None = None,
) -> PlayerRowPointers:
    access = TdbAccess()
    bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    bio_record_index = next(
        (
            index
            for index, row in enumerate(bio_rows)
            if (
                int(row.get("zIBw") or -1) == player_id
                if player_id is not None
                else (
                    str(row.get("PedH") or "").strip() == first_name
                    and str(row.get("RMbQ") or "").strip() == last_name
                )
            )
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
    goalie_ratings_rows = _read_full_table(access, db_path, GOALIE_RATINGS_TABLE_INDEX)
    goalie_ratings_record_index = next(
        (index for index, row in enumerate(goalie_ratings_rows) if int(row.get("zIBw") or -1) == player_id),
        None,
    )
    return PlayerRowPointers(
        first_name=first_name,
        last_name=last_name,
        player_id=player_id,
        bio_record_index=bio_record_index,
        ratings_record_index=ratings_record_index,
        goalie_ratings_record_index=goalie_ratings_record_index,
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
    player_id: int | None = None,
) -> dict[str, object]:
    pointers = find_player_rows(db_path, first_name, last_name, player_id)
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
        "player_id": pointers.player_id,
        "updated_fields": updates,
        "changes": changes,
    }


def update_many_player_ratings(
    db_path: Path,
    player_updates: list[tuple[str, str, dict[str, int]] | tuple[str, str, dict[str, int], int]],
    *,
    snapshot_cache: PlayerSnapshotCache | None = None,
) -> list[dict[str, object]]:
    if not player_updates:
        return []

    access = TdbAccess()
    use_cache = snapshot_cache is not None and Path(snapshot_cache.db_path) == Path(db_path)
    bio_rows = snapshot_cache.bio_rows if use_cache else _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    ratings_rows = snapshot_cache.ratings_rows if use_cache else _read_full_table(access, db_path, PLAYER_RATINGS_TABLE_INDEX)
    bio_by_name = {
        (str(row.get("PedH") or "").strip(), str(row.get("RMbQ") or "").strip()): row
        for row in bio_rows
    }
    bio_by_player_id = {
        int(row.get("zIBw") or -1): row
        for row in bio_rows
    }
    rating_index_by_player_id = {
        int(row.get("zIBw") or -1): index
        for index, row in enumerate(ratings_rows)
    }

    prepared: list[tuple[str, str, int, dict[str, int], dict[str, object]]] = []
    for update in player_updates:
        first_name, last_name, updates = update[:3]
        requested_player_id = int(update[3]) if len(update) == 4 else None
        bio = (
            bio_by_player_id.get(requested_player_id)
            if requested_player_id is not None
            else bio_by_name.get((first_name, last_name))
        )
        if bio is None:
            raise RuntimeError(f"Player not found: {first_name} {last_name}")
        player_id = int(bio.get("zIBw") or -1)
        record_index = rating_index_by_player_id.get(player_id)
        if record_index is None:
            raise RuntimeError(f"Ratings row not found: {first_name} {last_name}")
        prepared.append((first_name, last_name, record_index, updates, dict(ratings_rows[record_index])))

    results: list[dict[str, object]] = []
    ratings_table_index = access.resolve_table_index(db_path, PLAYER_RATINGS_TABLE_INDEX)
    with access.open_database(db_path) as db_index:
        table = access.get_table_properties(db_index, ratings_table_index)
        fields = {
            field.name: field
            for field in (
                access.get_field_properties(db_index, table.name, field_index)
                for field_index in range(table.field_count)
            )
        }
        for first_name, last_name, record_index, updates, before_row in prepared:
            for field_name, value in updates.items():
                field = fields.get(field_name)
                if field is None:
                    raise RuntimeError(f"Field not found: {table.name}.{field_name}")
                access.set_field_value(db_index, table.name, field, record_index, value)
            results.append(
                {
                    "player": f"{first_name} {last_name}",
                    "player_id": int(ratings_rows[record_index].get("zIBw") or -1),
                    "updated_fields": updates,
                    "changes": [
                        {
                            "section": "ratings",
                            "field": field,
                            "before": before_row.get(field),
                            "after": value,
                        }
                        for field, value in updates.items()
                        if before_row.get(field) != value
                    ],
                }
            )
        access.save_database(db_index)
    return results


def update_many_player_goalie_ratings(
    db_path: Path,
    player_updates: list[tuple[str, str, dict[str, int]] | tuple[str, str, dict[str, int], int]],
    *,
    snapshot_cache: PlayerSnapshotCache | None = None,
) -> list[dict[str, object]]:
    if not player_updates:
        return []

    access = TdbAccess()
    use_cache = snapshot_cache is not None and Path(snapshot_cache.db_path) == Path(db_path)
    bio_rows = snapshot_cache.bio_rows if use_cache else _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    ratings_rows = snapshot_cache.goalie_ratings_rows if use_cache else _read_full_table(access, db_path, GOALIE_RATINGS_TABLE_INDEX)
    bio_by_name = {
        (str(row.get("PedH") or "").strip(), str(row.get("RMbQ") or "").strip()): row
        for row in bio_rows
    }
    bio_by_player_id = {
        int(row.get("zIBw") or -1): row
        for row in bio_rows
    }
    rating_index_by_player_id = {
        int(row.get("zIBw") or -1): index
        for index, row in enumerate(ratings_rows)
    }

    prepared: list[tuple[str, str, int, dict[str, int], dict[str, object]]] = []
    for update in player_updates:
        first_name, last_name, updates = update[:3]
        requested_player_id = int(update[3]) if len(update) == 4 else None
        bio = (
            bio_by_player_id.get(requested_player_id)
            if requested_player_id is not None
            else bio_by_name.get((first_name, last_name))
        )
        if bio is None:
            raise RuntimeError(f"Player not found: {first_name} {last_name}")
        player_id = int(bio.get("zIBw") or -1)
        record_index = rating_index_by_player_id.get(player_id)
        if record_index is None:
            raise RuntimeError(f"Goalie ratings row not found: {first_name} {last_name}")
        prepared.append((first_name, last_name, record_index, updates, dict(ratings_rows[record_index])))

    results: list[dict[str, object]] = []
    ratings_table_index = access.resolve_table_index(db_path, GOALIE_RATINGS_TABLE_INDEX)
    with access.open_database(db_path) as db_index:
        table = access.get_table_properties(db_index, ratings_table_index)
        fields = {
            field.name: field
            for field in (
                access.get_field_properties(db_index, table.name, field_index)
                for field_index in range(table.field_count)
            )
        }
        for first_name, last_name, record_index, updates, before_row in prepared:
            for field_name, value in updates.items():
                field = fields.get(field_name)
                if field is None:
                    raise RuntimeError(f"Field not found: {table.name}.{field_name}")
                access.set_field_value(db_index, table.name, field, record_index, value)
            results.append(
                {
                    "player": f"{first_name} {last_name}",
                    "player_id": int(ratings_rows[record_index].get("zIBw") or -1),
                    "updated_fields": updates,
                    "changes": [
                        {
                            "section": "goalie_ratings",
                            "field": field,
                            "before": before_row.get(field),
                            "after": value,
                        }
                        for field, value in updates.items()
                        if before_row.get(field) != value
                    ],
                }
            )
        access.save_database(db_index)
    return results


def update_player_goalie_ratings(
    db_path: Path,
    first_name: str,
    last_name: str,
    updates: dict[str, int],
    player_id: int | None = None,
) -> dict[str, object]:
    pointers = find_player_rows(db_path, first_name, last_name, player_id)
    if pointers.goalie_ratings_record_index is None:
        raise RuntimeError(f"Goalie ratings row not found: {first_name} {last_name}")

    access = TdbAccess()
    ratings_rows = _read_full_table(access, db_path, GOALIE_RATINGS_TABLE_INDEX)
    before_row = ratings_rows[pointers.goalie_ratings_record_index]
    access.update_record_fields(
        db_path,
        GOALIE_RATINGS_TABLE_INDEX,
        pointers.goalie_ratings_record_index,
        updates,
    )
    changes = [
        {
            "section": "goalie_ratings",
            "field": field,
            "before": before_row.get(field),
            "after": value,
        }
        for field, value in updates.items()
        if before_row.get(field) != value
    ]
    return {
        "player": f"{first_name} {last_name}",
        "player_id": pointers.player_id,
        "updated_fields": updates,
        "changes": changes,
    }


def update_player_bio(
    db_path: Path,
    first_name: str,
    last_name: str,
    updates: dict[str, object],
    player_id: int | None = None,
    *,
    snapshot_cache: PlayerSnapshotCache | None = None,
) -> dict[str, object]:
    access = TdbAccess()
    use_cache = snapshot_cache is not None and Path(snapshot_cache.db_path) == Path(db_path)
    if use_cache:
        bio_rows = snapshot_cache.bio_rows
        bio = snapshot_cache.bio_by_player_id.get(player_id) if player_id is not None else snapshot_cache.bio_by_name.get((first_name, last_name))
        if bio is None:
            raise RuntimeError(f"Player not found: {first_name} {last_name}")
        resolved_player_id = int(bio.get("zIBw") or -1)
        bio_record_index = next(
            index for index, row in enumerate(bio_rows)
            if int(row.get("zIBw") or -1) == resolved_player_id
        )
    else:
        pointers = find_player_rows(db_path, first_name, last_name, player_id)
        bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
        resolved_player_id = pointers.player_id
        bio_record_index = pointers.bio_record_index
    before_row = bio_rows[bio_record_index]
    access.update_record_fields(
        db_path,
        PLAYER_BIO_TABLE_INDEX,
        bio_record_index,
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
        "player_id": resolved_player_id,
        "updated_fields": updates,
        "changes": changes,
    }


def contract_cap_hit_millions_from_raw(value: object) -> float:
    return (int(value or 0) * CONTRACT_UNIT_DOLLARS) / 1_000_000.0


def contract_cap_hit_raw_from_millions(value: float) -> int:
    dollars = max(MINIMUM_CONTRACT_DOLLARS, round(float(value) * 1_000_000))
    return round(dollars / CONTRACT_UNIT_DOLLARS)


def contract_value_raw_from_millions(value: float, *, enforce_minimum: bool = False) -> int:
    dollars = round(max(0.0, float(value)) * 1_000_000)
    if enforce_minimum and dollars:
        dollars = max(MINIMUM_CONTRACT_DOLLARS, dollars)
    return round(dollars / CONTRACT_UNIT_DOLLARS)


def update_player_contract_details(
    db_path: Path,
    first_name: str,
    last_name: str,
    *,
    cap_hit_millions: float,
    length: int,
    signed_or_restricted: bool,
    two_way: bool,
    entry_level_required: bool,
    extension_cap_hit_millions: float,
    extension_length: int,
    extension_two_way: bool,
    player_id: int | None = None,
) -> dict[str, object]:
    pointers = find_player_rows(db_path, first_name, last_name, player_id)
    access = TdbAccess()
    bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    before_row = bio_rows[pointers.bio_record_index]
    normalized_length = max(0, min(15, int(length)))
    updates = {
        CONTRACT_CAP_HIT_FIELD: contract_value_raw_from_millions(cap_hit_millions),
        CONTRACT_LENGTH_FIELD: normalized_length,
        CONTRACT_STATUS_FIELD: 1 if signed_or_restricted else 0,
        CONTRACT_TWO_WAY_FIELD: 1 if two_way else 0,
        CONTRACT_MULTI_YEAR_FIELD: 1 if normalized_length > 1 else 0,
        CONTRACT_ENTRY_LEVEL_FIELD: 1 if entry_level_required else 0,
        CONTRACT_EXTENSION_CAP_HIT_FIELD: contract_value_raw_from_millions(extension_cap_hit_millions),
        CONTRACT_EXTENSION_LENGTH_FIELD: max(0, min(15, int(extension_length))),
        CONTRACT_EXTENSION_TWO_WAY_FIELD: 1 if extension_two_way else 0,
    }
    access.update_record_fields(db_path, PLAYER_BIO_TABLE_INDEX, pointers.bio_record_index, updates)
    verified_updates = _verify_record_fields(
        access,
        db_path,
        PLAYER_BIO_TABLE_INDEX,
        pointers.bio_record_index,
        updates,
    )
    return {
        "player": f"{first_name} {last_name}",
        "player_id": pointers.player_id,
        "updated_fields": verified_updates,
        "game_aav_millions": contract_cap_hit_millions_from_raw(verified_updates[CONTRACT_CAP_HIT_FIELD]),
        "extension_aav_millions": contract_cap_hit_millions_from_raw(verified_updates[CONTRACT_EXTENSION_CAP_HIT_FIELD]),
        "verified": True,
        "changes": [
            {
                "section": "bio",
                "field": field,
                "before": before_row.get(field),
                "after": value,
            }
            for field, value in verified_updates.items()
            if before_row.get(field) != value
        ],
    }


def update_player_contract_cap_hit(
    db_path: Path,
    first_name: str,
    last_name: str,
    game_aav_millions: float,
    player_id: int | None = None,
) -> dict[str, object]:
    if player_id is not None:
        pointers = find_player_rows(db_path, first_name, last_name, player_id)
        access = TdbAccess()
        bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
        before_raw = int(bio_rows[pointers.bio_record_index].get(CONTRACT_CAP_HIT_FIELD) or 0)
        after_raw = contract_cap_hit_raw_from_millions(game_aav_millions)
        access.update_record_fields(
            db_path,
            PLAYER_BIO_TABLE_INDEX,
            pointers.bio_record_index,
            {CONTRACT_CAP_HIT_FIELD: after_raw},
        )
        verified_updates = _verify_record_fields(
            access,
            db_path,
            PLAYER_BIO_TABLE_INDEX,
            pointers.bio_record_index,
            {CONTRACT_CAP_HIT_FIELD: after_raw},
        )
        return {
            "player": f"{first_name} {last_name}",
            "player_id": player_id,
            "updated_fields": verified_updates,
            "game_aav_millions": contract_cap_hit_millions_from_raw(verified_updates[CONTRACT_CAP_HIT_FIELD]),
            "verified": True,
            "changes": [] if before_raw == after_raw else [
                {
                    "section": "bio",
                    "field": CONTRACT_CAP_HIT_FIELD,
                    "before": before_raw,
                    "after": after_raw,
                }
            ],
        }
    return update_many_player_contract_cap_hits(
        db_path,
        [(first_name, last_name, game_aav_millions)],
    )[0]


def update_many_player_contract_cap_hits(
    db_path: Path,
    player_updates: list[tuple[str, str, float] | tuple[str, str, float, int] | tuple[str, str, float, int, int]],
    *,
    error_messages: list[str] | None = None,
    snapshot_cache: PlayerSnapshotCache | None = None,
) -> list[dict[str, object]]:
    if not player_updates:
        return []

    access = TdbAccess()
    use_cache = snapshot_cache is not None and Path(snapshot_cache.db_path) == Path(db_path)
    bio_rows = snapshot_cache.bio_rows if use_cache else _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    bio_index_by_name = {
        (str(row.get("PedH") or "").strip(), str(row.get("RMbQ") or "").strip()): index
        for index, row in enumerate(bio_rows)
    }
    bio_index_by_player_id = {
        int(row.get("zIBw") or -1): index
        for index, row in enumerate(bio_rows)
    }
    prepared_by_record: dict[int, tuple[str, str, int, int, int, int, int | None, int, int]] = {}
    for update in player_updates:
        first_name, last_name, game_aav_millions = update[:3]
        requested_player_id = int(update[3]) if len(update) >= 4 else None
        requested_term = max(0, min(15, int(update[4]))) if len(update) >= 5 else None
        record_index = (
            bio_index_by_player_id.get(requested_player_id)
            if requested_player_id is not None
            else bio_index_by_name.get((first_name, last_name))
        )
        if record_index is None:
            if error_messages is not None:
                error_messages.append(f"{first_name} {last_name}: player not found")
                continue
            raise RuntimeError(f"Player not found: {first_name} {last_name}")
        resolved_player_id = int(bio_rows[record_index].get("zIBw") or -1)
        before_raw = int(bio_rows[record_index].get(CONTRACT_CAP_HIT_FIELD) or 0)
        before_term = int(bio_rows[record_index].get(CONTRACT_LENGTH_FIELD) or 0)
        before_multi_year = int(bio_rows[record_index].get(CONTRACT_MULTI_YEAR_FIELD) or 0)
        after_raw = contract_cap_hit_raw_from_millions(game_aav_millions)
        prepared_by_record[record_index] = (
            first_name,
            last_name,
            resolved_player_id,
            record_index,
            before_raw,
            after_raw,
            requested_term,
            before_term,
            before_multi_year,
        )

    prepared = list(prepared_by_record.values())
    if not prepared:
        return []

    results: list[dict[str, object]] = []
    table_index = access.resolve_table_index(db_path, PLAYER_BIO_TABLE_INDEX)
    with access.open_database(db_path) as db_index:
        table = access.get_table_properties(db_index, table_index)
        fields = {
            field.name: field
            for field in (
                access.get_field_properties(db_index, table.name, field_index)
                for field_index in range(table.field_count)
            )
        }
        cap_field = fields.get(CONTRACT_CAP_HIT_FIELD)
        term_field = fields.get(CONTRACT_LENGTH_FIELD)
        multi_year_field = fields.get(CONTRACT_MULTI_YEAR_FIELD)
        if cap_field is None:
            raise RuntimeError(f"Field not found: {table.name}.{CONTRACT_CAP_HIT_FIELD}")
        if any(item[6] is not None for item in prepared):
            if term_field is None:
                raise RuntimeError(f"Field not found: {table.name}.{CONTRACT_LENGTH_FIELD}")
            if multi_year_field is None:
                raise RuntimeError(f"Field not found: {table.name}.{CONTRACT_MULTI_YEAR_FIELD}")
        for first_name, last_name, player_id, record_index, before_raw, after_raw, requested_term, before_term, before_multi_year in prepared:
            access.set_field_value(db_index, table.name, cap_field, record_index, after_raw)
            if requested_term is not None:
                access.set_field_value(db_index, table.name, term_field, record_index, requested_term)
                access.set_field_value(db_index, table.name, multi_year_field, record_index, 1 if requested_term > 1 else 0)
        access.save_database(db_index)

    for first_name, last_name, player_id, record_index, before_raw, after_raw, requested_term, before_term, before_multi_year in prepared:
        expected = {CONTRACT_CAP_HIT_FIELD: after_raw}
        if requested_term is not None:
            expected[CONTRACT_LENGTH_FIELD] = requested_term
            expected[CONTRACT_MULTI_YEAR_FIELD] = 1 if requested_term > 1 else 0
        verified_updates = _verify_record_fields(
            access,
            db_path,
            PLAYER_BIO_TABLE_INDEX,
            record_index,
            expected,
        )
        changes = []
        if before_raw != verified_updates[CONTRACT_CAP_HIT_FIELD]:
            changes.append(
                {
                    "section": "bio",
                    "field": CONTRACT_CAP_HIT_FIELD,
                    "before": before_raw,
                    "after": verified_updates[CONTRACT_CAP_HIT_FIELD],
                }
            )
        if requested_term is not None and before_term != verified_updates[CONTRACT_LENGTH_FIELD]:
            changes.append(
                {
                    "section": "bio",
                    "field": CONTRACT_LENGTH_FIELD,
                    "before": before_term,
                    "after": verified_updates[CONTRACT_LENGTH_FIELD],
                }
            )
        if requested_term is not None and before_multi_year != verified_updates[CONTRACT_MULTI_YEAR_FIELD]:
            changes.append(
                {
                    "section": "bio",
                    "field": CONTRACT_MULTI_YEAR_FIELD,
                    "before": before_multi_year,
                    "after": verified_updates[CONTRACT_MULTI_YEAR_FIELD],
                }
            )
        bio_rows[record_index].update(verified_updates)
        results.append(
            {
                "player": f"{first_name} {last_name}",
                "player_id": player_id,
                "updated_fields": verified_updates,
                "game_aav_millions": contract_cap_hit_millions_from_raw(verified_updates[CONTRACT_CAP_HIT_FIELD]),
                "verified": True,
                "changes": changes,
            }
        )
    return results


def update_player_flags(
    db_path: Path,
    first_name: str,
    last_name: str,
    updates: dict[str, object],
    player_id: int | None = None,
) -> dict[str, object]:
    pointers = find_player_rows(db_path, first_name, last_name, player_id)
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
        "player_id": pointers.player_id,
        "updated_fields": updates,
        "changes": changes,
    }


def update_player_instance_fields(
    db_path: Path,
    first_name: str,
    last_name: str,
    updates: dict[str, object],
    player_id: int | None = None,
    *,
    snapshot_cache: PlayerSnapshotCache | None = None,
) -> dict[str, object]:
    use_cache = snapshot_cache is not None and Path(snapshot_cache.db_path) == Path(db_path)
    snapshot = (
        snapshot_cache.get_player_snapshot(first_name, last_name, player_id)
        if use_cache
        else get_player_snapshot(db_path, first_name, last_name, player_id)
    )
    if snapshot is None:
        raise RuntimeError(f"Player not found: {first_name} {last_name}")
    linked_instance_ids = {
        int(row.get(INSTANCE_KEY_FIELD) or -1)
        for row in snapshot.instance_rows
        if row.get(INSTANCE_KEY_FIELD) is not None
    }
    if not linked_instance_ids:
        instance = find_primary_player_instance(db_path, first_name, last_name, player_id=player_id)
        linked_instance_ids = {instance.instance_id}

    instance_rows = snapshot_cache.instance_rows if use_cache else _read_full_table(TdbAccess(), db_path, PLAYER_INSTANCE_TABLE_INDEX)
    record_indexes = [
        index
        for index, row in enumerate(instance_rows)
        if int(row.get(INSTANCE_KEY_FIELD) or -1) in linked_instance_ids
    ]
    if not record_indexes:
        raise RuntimeError(f"Instance row not found: {first_name} {last_name}")

    access = TdbAccess()
    before_rows = {index: dict(instance_rows[index]) for index in record_indexes}
    instance_table_index = access.resolve_table_index(db_path, PLAYER_INSTANCE_TABLE_INDEX)
    with access.open_database(db_path) as db_index:
        table = access.get_table_properties(db_index, instance_table_index)
        fields = {
            field.name: field
            for field in (
                access.get_field_properties(db_index, table.name, field_index)
                for field_index in range(table.field_count)
            )
        }
        for record_index in record_indexes:
            for field_name, value in updates.items():
                field = fields.get(field_name)
                if field is None:
                    raise RuntimeError(f"Field not found: {table.name}.{field_name}")
                access.set_field_value(db_index, table.name, field, record_index, value)
        access.save_database(db_index)
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
        "player_id": player_id,
        "instance_ids": sorted(linked_instance_ids),
        "updated_count": len(record_indexes),
        "updated_fields": updates,
        "changes": changes,
    }
