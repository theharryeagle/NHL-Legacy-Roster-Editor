from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .player_tools import PLAYER_BIO_TABLE_INDEX, PLAYER_INSTANCE_AUX_TABLE_INDEX, PLAYER_RELATION_TABLE_INDEX, get_player_snapshot
from .review_tools import diff_player_snapshots
from .tdb_access import TdbAccess
from .team_tools import INSTANCE_KEY_FIELD, INSTANCE_TEAM_FIELD, PLAYER_INSTANCE_TABLE_INDEX, get_team_maps, league_name_for_team, resolve_team_abbrev


@dataclass(slots=True)
class InstanceCandidate:
    relation_record_index: int
    instance_id: int
    team_code: int
    team_abbrev: str | None
    score: int


def _read_full_table(access: TdbAccess, db_path: Path, table_index: int | str) -> list[dict[str, object]]:
    resolved_index = access.resolve_table_index(db_path, table_index)
    _table, _fields, rows = access.sample_records(
        db_path,
        table_index,
        limit=access.list_tables(db_path)[resolved_index].record_count,
    )
    return rows


def find_primary_player_instance(
    db_path: Path,
    first_name: str,
    last_name: str,
    player_id: int | None = None,
) -> InstanceCandidate:
    snapshot = get_player_snapshot(db_path, first_name, last_name, player_id)
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
            league = league_name_for_team(team)
            if league == "NHL" and 0 <= team.code <= 29:
                score += 300
            elif league == "NHL":
                score += 260
            elif league == "AHL":
                score += 220
            elif league in {"Organization", "Prospects"}:
                score += 200
            elif league in {"International", "World Cup", "Exhibition"}:
                score += 30
            else:
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


def move_player_to_team(
    db_path: Path,
    first_name: str,
    last_name: str,
    target_abbrev: str,
    player_id: int | None = None,
) -> dict[str, object]:
    target_abbrev = target_abbrev.upper()
    team_by_code, team_by_abbrev = get_team_maps(db_path)
    target_team = resolve_team_abbrev(target_abbrev, list(team_by_code.values()))
    if target_team is None:
        raise RuntimeError(f"Unknown team abbreviation: {target_abbrev}")

    before_snapshot = get_player_snapshot(db_path, first_name, last_name, player_id)
    if before_snapshot is None:
        raise RuntimeError(f"Player not found: {first_name} {last_name}")

    instance = find_primary_player_instance(db_path, first_name, last_name, player_id)
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

    after_snapshot = get_player_snapshot(db_path, first_name, last_name, player_id)
    return {
        "player": f"{first_name} {last_name}",
        "player_id": int(before_snapshot.bio.get("zIBw") or -1),
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


def move_players_to_teams(
    db_path: Path,
    moves: list[tuple[str, str, str] | tuple[str, str, str, int]],
    *,
    snapshot_cache=None,
    cached_team_by_code=None,
) -> list[dict[str, object]]:
    if not moves:
        return []

    access = TdbAccess()
    if cached_team_by_code is None:
        team_by_code, team_by_abbrev = get_team_maps(db_path)
    else:
        team_by_code = cached_team_by_code
        team_by_abbrev = {team.abbrev: team for team in team_by_code.values()}
    if snapshot_cache is None:
        tables = access.list_tables(db_path)
        bio_index = access.resolve_table_index(db_path, PLAYER_BIO_TABLE_INDEX)
        relation_index = access.resolve_table_index(db_path, PLAYER_RELATION_TABLE_INDEX)
        instance_index = access.resolve_table_index(db_path, PLAYER_INSTANCE_TABLE_INDEX)
        _bio_table, _bio_fields, bio_rows = access.sample_records(db_path, PLAYER_BIO_TABLE_INDEX, limit=tables[bio_index].record_count)
        _rel_table, _rel_fields, relation_rows = access.sample_records(db_path, PLAYER_RELATION_TABLE_INDEX, limit=tables[relation_index].record_count)
        _inst_table, _inst_fields, instance_rows = access.sample_records(
            db_path,
            PLAYER_INSTANCE_TABLE_INDEX,
            limit=tables[instance_index].record_count,
        )
    else:
        bio_rows = snapshot_cache.bio_rows
        relation_rows = snapshot_cache.relation_rows
        instance_rows = snapshot_cache.instance_rows
    bio_by_name = {
        (str(row.get("PedH") or "").strip(), str(row.get("RMbQ") or "").strip()): row
        for row in bio_rows
    }
    bio_by_player_id = {
        int(row.get("zIBw") or -1): row
        for row in bio_rows
    }
    relation_by_player_key: dict[object, list[dict[str, object]]] = {}
    for row in relation_rows:
        relation_by_player_key.setdefault(row.get("qFky"), []).append(row)
    instance_by_id = {
        int(row.get(INSTANCE_KEY_FIELD) or -1): row
        for row in instance_rows
    }
    record_index_by_instance_id = {
        int(row.get(INSTANCE_KEY_FIELD) or -1): index
        for index, row in enumerate(instance_rows)
    }

    prepared: list[tuple[str, str, int, str | None, int, int, int]] = []
    for move in moves:
        first_name, last_name, target_abbrev = move[:3]
        requested_player_id = int(move[3]) if len(move) == 4 else None
        target_team = resolve_team_abbrev(target_abbrev, list(team_by_code.values()))
        if target_team is None:
            raise RuntimeError(f"Unknown team abbreviation: {target_abbrev}")
        bio = bio_by_player_id.get(requested_player_id) if requested_player_id is not None else bio_by_name.get((first_name, last_name))
        if bio is None:
            raise RuntimeError(f"Player not found: {first_name} {last_name}")
        player_id = bio.get("zIBw")
        long_id = bio.get("DaPp")
        relation_candidates = list(relation_by_player_key.get(player_id, []))
        if long_id != player_id:
            relation_candidates.extend(
                row
                for row in relation_by_player_key.get(long_id, [])
                if row not in relation_candidates
            )
        candidates: list[InstanceCandidate] = []
        for rel_index, rel in enumerate(relation_candidates):
            instance_id = int(rel.get("qEfv") or -1)
            instance_row = instance_by_id.get(instance_id)
            if instance_row is None:
                continue
            team_code = int(instance_row.get(INSTANCE_TEAM_FIELD, -1))
            team = team_by_code.get(team_code)
            score = 0
            if team is not None:
                league = league_name_for_team(team)
                if league == "NHL" and 0 <= team.code <= 29:
                    score += 300
                elif league == "NHL":
                    score += 260
                elif league == "AHL":
                    score += 220
                elif league in {"Organization", "Prospects"}:
                    score += 200
                elif league in {"International", "World Cup", "Exhibition"}:
                    score += 30
                else:
                    score += 100
            if int(instance_row.get("XWot", 0)) < 2000:
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
        instance = candidates[0]
        record_index = record_index_by_instance_id.get(instance.instance_id)
        if record_index is None:
            raise RuntimeError(f"Instance record not found for {first_name} {last_name}")
        prepared.append((first_name, last_name, int(player_id), instance.team_abbrev, instance.instance_id, record_index, target_team.code))

    results: list[dict[str, object]] = []
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
        team_field = fields.get(INSTANCE_TEAM_FIELD)
        if team_field is None:
            raise RuntimeError(f"Field not found: {table.name}.{INSTANCE_TEAM_FIELD}")
        for first_name, last_name, player_id, from_team, instance_id, record_index, target_code in prepared:
            target_team = team_by_code[target_code]
            access.set_field_value(db_index, table.name, team_field, record_index, target_code)
            results.append(
                {
                    "player": f"{first_name} {last_name}",
                    "player_id": player_id,
                    "instance_id": instance_id,
                    "from_team": from_team,
                    "to_team": target_team.abbrev,
                    "target_team_code": target_code,
                    "changes": [
                        {
                            "section": "instance_rows",
                            "field": INSTANCE_TEAM_FIELD,
                            "before": from_team,
                            "after": target_team.abbrev,
                        }
                    ],
                    "team_lookup": {
                        "from": None if from_team is None or from_team not in team_by_abbrev else as_team_json(team_by_abbrev[from_team]),
                        "to": as_team_json(target_team),
                    },
                }
            )
        access.save_database(db_index)
    return results


def move_players_to_free_agency(
    db_path: Path,
    players: list[tuple[str, str] | tuple[str, str, int]],
    *,
    snapshot_cache=None,
    cached_team_by_code=None,
) -> list[dict[str, object]]:
    """Release multiple players with one native database save."""
    if not players:
        return []

    access = TdbAccess()
    if cached_team_by_code is None:
        team_by_code, team_by_abbrev = get_team_maps(db_path)
    else:
        team_by_code = cached_team_by_code
        team_by_abbrev = {team.abbrev: team for team in team_by_code.values()}
    if snapshot_cache is None:
        bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
        relation_rows = _read_full_table(access, db_path, PLAYER_RELATION_TABLE_INDEX)
        instance_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_TABLE_INDEX)
        aux_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_AUX_TABLE_INDEX)
    else:
        bio_rows = snapshot_cache.bio_rows
        relation_rows = snapshot_cache.relation_rows
        instance_rows = snapshot_cache.instance_rows
        aux_rows = snapshot_cache.instance_aux_rows

    bio_by_name = {
        (str(row.get("PedH") or "").strip(), str(row.get("RMbQ") or "").strip()): row
        for row in bio_rows
    }
    bio_by_player_id = {int(row.get("zIBw") or -1): row for row in bio_rows}
    bio_record_by_player_id = {
        int(row.get("zIBw") or -1): index
        for index, row in enumerate(bio_rows)
    }
    relation_by_player_key: dict[object, list[dict[str, object]]] = {}
    for row in relation_rows:
        relation_by_player_key.setdefault(row.get("qFky"), []).append(row)
    instance_by_id = {
        int(row.get(INSTANCE_KEY_FIELD) or -1): row
        for row in instance_rows
    }
    instance_record_by_id = {
        int(row.get(INSTANCE_KEY_FIELD) or -1): index
        for index, row in enumerate(instance_rows)
    }
    aux_records_by_instance_id: dict[int, list[int]] = {}
    for index, row in enumerate(aux_rows):
        aux_records_by_instance_id.setdefault(int(row.get("qEfv") or -1), []).append(index)

    prepared: list[tuple[str, str, int, str | None, int, int, int, list[int]]] = []
    seen_player_ids: set[int] = set()
    for player in players:
        first_name, last_name = player[:2]
        requested_player_id = int(player[2]) if len(player) == 3 else None
        bio = bio_by_player_id.get(requested_player_id) if requested_player_id is not None else bio_by_name.get((first_name, last_name))
        if bio is None:
            raise RuntimeError(f"Player not found: {first_name} {last_name}")
        player_id = int(bio.get("zIBw") or -1)
        if player_id in seen_player_ids:
            continue
        seen_player_ids.add(player_id)
        relation_candidates = list(relation_by_player_key.get(player_id, []))
        long_id = bio.get("DaPp")
        if long_id != player_id:
            relation_candidates.extend(
                row
                for row in relation_by_player_key.get(long_id, [])
                if row not in relation_candidates
            )
        candidates: list[InstanceCandidate] = []
        for rel_index, relation in enumerate(relation_candidates):
            instance_id = int(relation.get("qEfv") or -1)
            instance_row = instance_by_id.get(instance_id)
            if instance_row is None:
                continue
            team_code = int(instance_row.get(INSTANCE_TEAM_FIELD, -1))
            team = team_by_code.get(team_code)
            score = 0
            if team is not None:
                league = league_name_for_team(team)
                if league == "NHL" and 0 <= team.code <= 29:
                    score += 300
                elif league == "NHL":
                    score += 260
                elif league == "AHL":
                    score += 220
                elif league in {"Organization", "Prospects"}:
                    score += 200
                elif league in {"International", "World Cup", "Exhibition"}:
                    score += 30
                else:
                    score += 100
            if int(instance_row.get("XWot", 0)) < 2000:
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
        instance = candidates[0]
        prepared.append(
            (
                first_name,
                last_name,
                player_id,
                instance.team_abbrev,
                instance.instance_id,
                instance_record_by_id[instance.instance_id],
                bio_record_by_player_id[player_id],
                aux_records_by_instance_id.get(instance.instance_id, []),
            )
        )

    instance_table_index = access.resolve_table_index(db_path, PLAYER_INSTANCE_TABLE_INDEX)
    aux_table_index = access.resolve_table_index(db_path, PLAYER_INSTANCE_AUX_TABLE_INDEX)
    bio_table_index = access.resolve_table_index(db_path, PLAYER_BIO_TABLE_INDEX)
    release_bio_fields = {"dhKk", "GDhI", "NYKk", "DVoL", "IzRv", "IrlK", "xdoJ", "LcvS", "WBbd", "uWgv"}
    results: list[dict[str, object]] = []
    with access.open_database(db_path) as db_index:
        def table_fields(table_index: int):
            table = access.get_table_properties(db_index, table_index)
            fields = {
                field.name: field
                for field in (
                    access.get_field_properties(db_index, table.name, field_index)
                    for field_index in range(table.field_count)
                )
            }
            return table, fields

        instance_table, instance_fields = table_fields(instance_table_index)
        aux_table, aux_fields = table_fields(aux_table_index)
        bio_table, bio_fields = table_fields(bio_table_index)
        for first_name, last_name, player_id, from_team, instance_id, instance_record, bio_record, aux_records in prepared:
            access.set_field_value(db_index, instance_table.name, instance_fields[INSTANCE_TEAM_FIELD], instance_record, 255)
            if "jZSh" in instance_fields:
                access.set_field_value(db_index, instance_table.name, instance_fields["jZSh"], instance_record, 0)
            for aux_record in aux_records:
                for field_name, field in aux_fields.items():
                    if field_name == "qEfv":
                        continue
                    access.set_field_value(
                        db_index,
                        aux_table.name,
                        field,
                        aux_record,
                        255 if field_name == INSTANCE_TEAM_FIELD else 0,
                    )
            bio_before = bio_rows[bio_record]
            bio_changes = []
            for field_name in release_bio_fields:
                field = bio_fields.get(field_name)
                if field is None:
                    continue
                before = bio_before.get(field_name)
                access.set_field_value(db_index, bio_table.name, field, bio_record, 0)
                if before not in (None, 0):
                    bio_changes.append({"section": "bio", "field": field_name, "before": before, "after": 0})
            results.append(
                {
                    "player": f"{first_name} {last_name}",
                    "player_id": player_id,
                    "instance_id": instance_id,
                    "from_team": from_team,
                    "to_team": "code:255",
                    "target_team_code": 255,
                    "changes": [
                        {
                            "section": "instance_rows",
                            "field": INSTANCE_TEAM_FIELD,
                            "before": from_team,
                            "after": "code:255",
                        },
                        *bio_changes,
                    ],
                    "team_lookup": {
                        "from": None if from_team is None or from_team not in team_by_abbrev else as_team_json(team_by_abbrev[from_team]),
                        "to": None,
                    },
                }
            )
        access.save_database(db_index)
    return results


def move_player_to_team_code(
    db_path: Path,
    first_name: str,
    last_name: str,
    target_team_code: int,
    player_id: int | None = None,
) -> dict[str, object]:
    team_by_code, team_by_abbrev = get_team_maps(db_path)
    target_team = team_by_code.get(target_team_code)
    if target_team is not None:
        return move_player_to_team(db_path, first_name, last_name, target_team.abbrev, player_id)

    before_snapshot = get_player_snapshot(db_path, first_name, last_name, player_id)
    if before_snapshot is None:
        raise RuntimeError(f"Player not found: {first_name} {last_name}")

    instance = find_primary_player_instance(db_path, first_name, last_name, player_id)
    access = TdbAccess()
    instance_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_TABLE_INDEX)
    record_index = next(
        idx for idx, row in enumerate(instance_rows) if int(row[INSTANCE_KEY_FIELD]) == instance.instance_id
    )
    instance_updates = {INSTANCE_TEAM_FIELD: target_team_code}
    if target_team_code == 255:
        instance_updates["jZSh"] = 0
    access.update_record_fields(
        db_path,
        PLAYER_INSTANCE_TABLE_INDEX,
        record_index,
        instance_updates,
    )
    # NHL Legacy only exposes an unassigned player in its FA pool when the
    # auxiliary roster assignment is cleared along with the main team field.
    if target_team_code == 255:
        aux_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_AUX_TABLE_INDEX)
        aux_record_index = next(
            (idx for idx, row in enumerate(aux_rows) if int(row.get("qEfv") or -1) == instance.instance_id),
            None,
        )
        if aux_record_index is not None:
            aux_fields = {
                field.name
                for field in access.list_fields_by_index(db_path, PLAYER_INSTANCE_AUX_TABLE_INDEX)
            }
            cleared = {field: 0 for field in aux_fields if field != "qEfv"}
            if "BSXd" in cleared:
                cleared["BSXd"] = 255
            access.update_record_fields(db_path, PLAYER_INSTANCE_AUX_TABLE_INDEX, aux_record_index, cleared)
        bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
        bio_record_index = next(
            (
                idx
                for idx, row in enumerate(bio_rows)
                if int(row.get("zIBw") or -1) == int(before_snapshot.bio.get("zIBw") or -1)
            ),
            None,
        )
        if bio_record_index is not None:
            # A contracted unassigned player is omitted from the franchise FA
            # pool. Releasing a player must also make the contract signable.
            access.update_record_fields(
                db_path,
                PLAYER_BIO_TABLE_INDEX,
                bio_record_index,
                {
                    "dhKk": 0,
                    "GDhI": 0,
                    "NYKk": 0,
                    "DVoL": 0,
                    "IzRv": 0,
                    "IrlK": 0,
                    "xdoJ": 0,
                    "LcvS": 0,
                    "WBbd": 0,
                    "uWgv": 0,
                },
            )
    after_snapshot = get_player_snapshot(db_path, first_name, last_name, player_id)
    return {
        "player": f"{first_name} {last_name}",
        "player_id": int(before_snapshot.bio.get("zIBw") or -1),
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
