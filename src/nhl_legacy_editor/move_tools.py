from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .player_tools import get_player_snapshot
from .review_tools import diff_player_snapshots
from .tdb_access import TdbAccess
from .team_tools import INSTANCE_KEY_FIELD, INSTANCE_TEAM_FIELD, PLAYER_INSTANCE_TABLE_INDEX, get_team_maps


@dataclass(slots=True)
class InstanceCandidate:
    relation_record_index: int
    instance_id: int
    team_code: int
    team_abbrev: str | None
    score: int


def _read_full_table(access: TdbAccess, db_path: Path, table_index: int) -> list[dict[str, object]]:
    _table, _fields, rows = access.sample_records(
        db_path,
        table_index,
        limit=access.list_tables(db_path)[table_index].record_count,
    )
    return rows


def find_primary_player_instance(db_path: Path, first_name: str, last_name: str) -> InstanceCandidate:
    snapshot = get_player_snapshot(db_path, first_name, last_name)
    if snapshot is None:
        raise RuntimeError(f"Player not found: {first_name} {last_name}")

    access = TdbAccess()
    instance_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_TABLE_INDEX)
    instance_index = {int(row[INSTANCE_KEY_FIELD]): idx for idx, row in enumerate(instance_rows)}
    instance_map = {int(row[INSTANCE_KEY_FIELD]): row for row in instance_rows}
    team_by_code, _team_by_abbrev = get_team_maps(db_path)

    candidates: list[InstanceCandidate] = []
    for rel_index, rel in enumerate(snapshot.relation_rows):
        instance_id = int(rel["qEfv"])
        instance_row = instance_map.get(instance_id)
        if not instance_row:
            continue
        team_code = int(instance_row.get(INSTANCE_TEAM_FIELD, -1))
        team = team_by_code.get(team_code)
        score = 0
        if team is not None:
            score += 100
        xwot = int(instance_row.get("XWot", 0))
        if xwot < 2000:
            score += 20
        if int(instance_row.get("Imzy", 0)) == 0:
            score += 5
        candidates.append(
            InstanceCandidate(
                relation_record_index=rel_index,
                instance_id=instance_id,
                team_code=team_code,
                team_abbrev=None if team is None else team.abbrev,
                score=score,
            )
        )

    if not candidates:
        raise RuntimeError(f"No instance candidates found for {first_name} {last_name}")
    candidates.sort(key=lambda item: (-item.score, item.instance_id))
    return candidates[0]


def move_player_to_team(db_path: Path, first_name: str, last_name: str, target_abbrev: str) -> dict[str, object]:
    target_abbrev = target_abbrev.upper()
    team_by_code, team_by_abbrev = get_team_maps(db_path)
    target_team = team_by_abbrev.get(target_abbrev)
    if target_team is None:
        raise RuntimeError(f"Unknown team abbreviation: {target_abbrev}")

    before_snapshot = get_player_snapshot(db_path, first_name, last_name)
    if before_snapshot is None:
        raise RuntimeError(f"Player not found: {first_name} {last_name}")

    instance = find_primary_player_instance(db_path, first_name, last_name)
    access = TdbAccess()
    instance_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_TABLE_INDEX)
    record_index = next(
        idx for idx, row in enumerate(instance_rows) if int(row[INSTANCE_KEY_FIELD]) == instance.instance_id
    )
    access.update_record_fields(
        db_path,
        PLAYER_INSTANCE_TABLE_INDEX,
        record_index,
        {INSTANCE_TEAM_FIELD: target_team.code},
    )

    after_snapshot = get_player_snapshot(db_path, first_name, last_name)
    return {
        "player": f"{first_name} {last_name}",
        "instance_id": instance.instance_id,
        "from_team": instance.team_abbrev,
        "to_team": target_team.abbrev,
        "target_team_code": target_team.code,
        "changes": [] if after_snapshot is None else [
            {
                "section": change.section,
                "field": change.field,
                "before": change.before,
                "after": change.after,
            }
            for change in diff_player_snapshots(before_snapshot, after_snapshot)
        ],
        "team_lookup": {
            "from": None if instance.team_abbrev is None else as_team_json(team_by_abbrev[instance.team_abbrev]),
            "to": as_team_json(target_team),
        },
    }


def move_player_to_team_code(db_path: Path, first_name: str, last_name: str, target_team_code: int) -> dict[str, object]:
    team_by_code, team_by_abbrev = get_team_maps(db_path)
    target_team = team_by_code.get(target_team_code)
    if target_team is not None:
        return move_player_to_team(db_path, first_name, last_name, target_team.abbrev)

    before_snapshot = get_player_snapshot(db_path, first_name, last_name)
    if before_snapshot is None:
        raise RuntimeError(f"Player not found: {first_name} {last_name}")

    instance = find_primary_player_instance(db_path, first_name, last_name)
    access = TdbAccess()
    instance_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_TABLE_INDEX)
    record_index = next(
        idx for idx, row in enumerate(instance_rows) if int(row[INSTANCE_KEY_FIELD]) == instance.instance_id
    )
    access.update_record_fields(
        db_path,
        PLAYER_INSTANCE_TABLE_INDEX,
        record_index,
        {INSTANCE_TEAM_FIELD: target_team_code},
    )
    after_snapshot = get_player_snapshot(db_path, first_name, last_name)
    return {
        "player": f"{first_name} {last_name}",
        "instance_id": instance.instance_id,
        "from_team": instance.team_abbrev,
        "to_team": f"code:{target_team_code}",
        "target_team_code": target_team_code,
        "changes": [] if after_snapshot is None else [
            {
                "section": change.section,
                "field": change.field,
                "before": change.before,
                "after": change.after,
            }
            for change in diff_player_snapshots(before_snapshot, after_snapshot)
        ],
        "team_lookup": {
            "from": None if instance.team_abbrev is None else as_team_json(team_by_abbrev[instance.team_abbrev]),
            "to": None,
        },
    }


def get_player_current_team(db_path: Path, first_name: str, last_name: str) -> dict[str, object]:
    instance = find_primary_player_instance(db_path, first_name, last_name)
    team_by_code, _team_by_abbrev = get_team_maps(db_path)
    team = team_by_code.get(instance.team_code)
    return {
        "instance_id": instance.instance_id,
        "team_code": instance.team_code,
        "team": None if team is None else as_team_json(team),
    }


def as_team_json(team) -> dict[str, object]:
    return {
        "code": team.code,
        "abbrev": team.abbrev,
        "name": team.name,
        "city": team.city,
    }
