from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .player_tools import PlayerSnapshot, get_player_snapshot


@dataclass(slots=True)
class FieldChange:
    section: str
    field: str
    before: object
    after: object


def _diff_mapping(section: str, before: dict[str, object] | None, after: dict[str, object] | None) -> list[FieldChange]:
    changes: list[FieldChange] = []
    before_map = before or {}
    after_map = after or {}
    for key in sorted(set(before_map) | set(after_map)):
        if before_map.get(key) != after_map.get(key):
            changes.append(
                FieldChange(
                    section=section,
                    field=key,
                    before=before_map.get(key),
                    after=after_map.get(key),
                )
            )
    return changes


def _normalize_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: tuple(sorted(row.items())))


def diff_player_snapshots(before: PlayerSnapshot, after: PlayerSnapshot) -> list[FieldChange]:
    changes: list[FieldChange] = []
    changes.extend(_diff_mapping("bio", before.bio, after.bio))
    changes.extend(_diff_mapping("flags", before.flags_row, after.flags_row))
    changes.extend(_diff_mapping("ratings", before.ratings_row, after.ratings_row))

    if _normalize_rows(before.relation_rows) != _normalize_rows(after.relation_rows):
        changes.append(
            FieldChange(
                section="relation_rows",
                field="rows",
                before=_normalize_rows(before.relation_rows),
                after=_normalize_rows(after.relation_rows),
            )
        )
    if _normalize_rows(before.instance_rows) != _normalize_rows(after.instance_rows):
        changes.append(
            FieldChange(
                section="instance_rows",
                field="rows",
                before=_normalize_rows(before.instance_rows),
                after=_normalize_rows(after.instance_rows),
            )
        )
    if _normalize_rows(before.instance_aux_rows) != _normalize_rows(after.instance_aux_rows):
        changes.append(
            FieldChange(
                section="instance_aux_rows",
                field="rows",
                before=_normalize_rows(before.instance_aux_rows),
                after=_normalize_rows(after.instance_aux_rows),
            )
        )
    if _normalize_rows(before.small_link_rows) != _normalize_rows(after.small_link_rows):
        changes.append(
            FieldChange(
                section="small_link_rows",
                field="rows",
                before=_normalize_rows(before.small_link_rows),
                after=_normalize_rows(after.small_link_rows),
            )
        )
    if _normalize_rows(before.wide_link_rows) != _normalize_rows(after.wide_link_rows):
        changes.append(
            FieldChange(
                section="wide_link_rows",
                field="rows",
                before=_normalize_rows(before.wide_link_rows),
                after=_normalize_rows(after.wide_link_rows),
            )
        )
    return changes


def diff_player_between_dbs(
    before_db: Path,
    after_db: Path,
    first_name: str,
    last_name: str,
) -> list[FieldChange] | None:
    before = get_player_snapshot(before_db, first_name, last_name)
    after = get_player_snapshot(after_db, first_name, last_name)
    if before is None or after is None:
        return None
    return diff_player_snapshots(before, after)
