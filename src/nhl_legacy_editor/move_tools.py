from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .player_tools import PLAYER_BIO_TABLE_INDEX, PLAYER_RELATION_TABLE_INDEX, build_player_snapshot_cache, get_player_snapshot
from .review_tools import diff_player_snapshots
from .roster_views import load_player_index
from .tdb_access import TdbAccess
from .team_tools import INSTANCE_KEY_FIELD, INSTANCE_TEAM_FIELD, PLAYER_INSTANCE_TABLE_INDEX, franchise_contract_team_code, franchise_rights_code, get_team_maps, league_name_for_team, resolve_team_abbrev


ROSTER_SLOT_FIELD = "XWot"
ROSTER_SLOTS_PER_TEAM = 40
CAPTAINCY_FIELD = "lcCm"
CONTRACT_PRESERVED_FIELDS = ("dhKk", "GDhI", "QwoG", "DVoL", "gHmt", "yvUt", "IzRv", "IrlK", "xdoJ")


@dataclass(slots=True)
class InstanceCandidate:
    relation_record_index: int
    instance_record_index: int
    instance_id: int
    team_code: int
    team_abbrev: str | None
    score: int


def repair_ahl_contract_ownership(db_path: Path) -> list[dict[str, object]]:
    """Attach AHL contracts/rights to the parent NHL organization.

    Physical placement remains in ``ulGe`` on the AHL affiliate. Only the
    three organization fields in ``cPbu`` are normalized, and every salary,
    term, clause, and contract-status field is verified unchanged afterward.
    """
    teams = get_team_maps(db_path)[0]
    all_teams = list(teams.values())
    cache = build_player_snapshot_cache(db_path)
    player_index = load_player_index(db_path)
    bio_record_by_player_id = {
        int(row.get("zIBw") or -1): index for index, row in enumerate(cache.bio_rows)
    }
    repairs: list[dict[str, object]] = []
    for player in player_index:
        if player.is_hidden or player.league_name != "AHL" or player.current_team_code is None:
            continue
        team = teams.get(int(player.current_team_code))
        bio = cache.bio_by_player_id.get(int(player.player_id))
        if team is None or bio is None:
            continue
        parent_code = franchise_contract_team_code(team, all_teams)
        rights_code = franchise_rights_code(team, all_teams)
        before = {field: bio.get(field) for field in CONTRACT_PRESERVED_FIELDS}
        ownership_before = {field: bio.get(field) for field in ("BSXd", "WBbd", "uWgv")}
        ownership_after = {"BSXd": parent_code, "WBbd": rights_code, "uWgv": rights_code}
        if all(ownership_before[field] == value for field, value in ownership_after.items()):
            continue
        repairs.append({
            "player": player.full_name,
            "player_id": int(player.player_id),
            "team": team.abbrev,
            "bio_record": bio_record_by_player_id[int(player.player_id)],
            "ownership_before": ownership_before,
            "ownership_after": ownership_after,
            "contract_before": before,
        })

    if not repairs:
        return []
    access = TdbAccess()
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
        for repair in repairs:
            record = int(repair["bio_record"])
            for field_name, value in dict(repair["ownership_after"]).items():
                if field_name in fields:
                    access.set_field_value(db_index, table.name, fields[field_name], record, int(value))
            contract_after = {
                field_name: access.get_field_value(db_index, table.name, fields[field_name], record)
                for field_name in CONTRACT_PRESERVED_FIELDS
                if field_name in fields
            }
            changed = {
                field_name: (dict(repair["contract_before"]).get(field_name), value)
                for field_name, value in contract_after.items()
                if dict(repair["contract_before"]).get(field_name) != value
            }
            if changed:
                raise RuntimeError(f"AHL ownership repair changed contract values for {repair['player']}: {changed}")
            repair["contract_preserved"] = True
        access.save_database(db_index)
    return repairs


def repair_roster_identity_collisions(db_path: Path) -> dict[str, list[dict[str, object]]]:
    """Remove duplicate instance records and make every XWot slot unique.

    Duplicate ``ulGe.TWSX`` rows make one relation resolve to two physical
    players. Free-agent slots can also collide with custom-team blocks because
    they are not tied to team 255's mathematical block. Non-free-agent rows
    are never renumbered; ambiguous non-free-agent collisions fail closed.
    """
    access = TdbAccess()
    team_by_code = get_team_maps(db_path)[0]

    def score(row: dict[str, object]) -> int:
        team_code = int(row.get(INSTANCE_TEAM_FIELD, -1))
        if team_code == 255:
            return 0
        team = team_by_code.get(team_code)
        if team is None:
            return 10
        league = league_name_for_team(team)
        return {
            "NHL": 300 if 0 <= team.code <= 29 else 260,
            "AHL": 220,
            "Organization": 200,
            "Prospects": 180,
        }.get(league, 100)

    removed: list[dict[str, object]] = []
    instance_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_TABLE_INDEX)
    by_instance_id: dict[int, list[tuple[int, dict[str, object]]]] = {}
    for index, row in enumerate(instance_rows):
        by_instance_id.setdefault(int(row.get(INSTANCE_KEY_FIELD) or -1), []).append((index, row))
    removal_indexes: list[int] = []
    for instance_id, rows in by_instance_id.items():
        if len(rows) <= 1:
            continue
        # Organization/system rows intentionally mirror a base player's
        # instance ID. Only a stale free-agent copy is an invalid duplicate;
        # never collapse normal system-team projections.
        if not any(int(row.get(INSTANCE_TEAM_FIELD, -1)) == 255 for _index, row in rows):
            continue
        keep_index, keep_row = max(rows, key=lambda item: (score(item[1]), -item[0]))
        for index, row in rows:
            if index == keep_index:
                continue
            removal_indexes.append(index)
            removed.append({
                "instance_id": instance_id,
                "record_index": index,
                "team_code": int(row.get(INSTANCE_TEAM_FIELD, -1)),
                "kept_team_code": int(keep_row.get(INSTANCE_TEAM_FIELD, -1)),
            })
    if removal_indexes:
        with access.open_database(db_path) as db_index:
            for index in sorted(removal_indexes, reverse=True):
                access.remove_record(db_index, PLAYER_INSTANCE_TABLE_INDEX, index)
            access.save_database(db_index)

    instance_rows = _read_full_table(access, db_path, PLAYER_INSTANCE_TABLE_INDEX)
    by_slot: dict[int, list[tuple[int, dict[str, object]]]] = {}
    for index, row in enumerate(instance_rows):
        by_slot.setdefault(int(row.get(ROSTER_SLOT_FIELD) or -1), []).append((index, row))
    used_slots = set(by_slot)
    available = iter(value for value in range((1 << 14) - 1, -1, -1) if value not in used_slots)
    reassigned: list[dict[str, object]] = []
    planned: dict[int, int] = {}
    for old_slot, rows in by_slot.items():
        if len(rows) <= 1:
            continue
        fixed = [item for item in rows if int(item[1].get(INSTANCE_TEAM_FIELD, -1)) != 255]
        if len(fixed) > 1:
            raise RuntimeError(f"Unsafe non-free-agent XWot collision at slot {old_slot}.")
        keep_index = fixed[0][0] if fixed else rows[0][0]
        for index, row in rows:
            if index == keep_index:
                continue
            if int(row.get(INSTANCE_TEAM_FIELD, -1)) != 255:
                raise RuntimeError(f"Unsafe XWot collision at slot {old_slot}.")
            new_slot = next(available)
            planned[index] = new_slot
            reassigned.append({
                "instance_id": int(row.get(INSTANCE_KEY_FIELD) or -1),
                "record_index": index,
                "before": old_slot,
                "after": new_slot,
            })
    if planned:
        table_index = access.resolve_table_index(db_path, PLAYER_INSTANCE_TABLE_INDEX)
        with access.open_database(db_path) as db_index:
            table = access.get_table_properties(db_index, table_index)
            fields = {
                access.get_field_properties(db_index, table.name, index).name:
                access.get_field_properties(db_index, table.name, index)
                for index in range(table.field_count)
            }
            for index, new_slot in planned.items():
                access.set_field_value(db_index, table.name, fields[ROSTER_SLOT_FIELD], index, new_slot)
            access.save_database(db_index)
    return {"removed_duplicate_instances": removed, "reassigned_free_agent_slots": reassigned}


def _position_group(bio: dict[str, object]) -> str:
    position = int(bio.get("aljv") or 0)
    if position == 4:
        return "G"
    if position == 3:
        return "D"
    return "F"


def plan_roster_slot_updates(
    instance_rows: list[dict[str, object]],
    target_team_by_record_index: dict[int, int],
) -> dict[int, int]:
    """Allocate the target team's native 40-player XWot slot block.

    NHL Legacy stores two team references on an instance: BSXd is the team
    code and XWot is a globally unique roster slot.  Team N owns XWot values
    N*40 through N*40+39.  Changing BSXd without moving XWot leaves the game
    resolving the player through two different teams and can produce Player
    Undefined entries when Be a GM builds its depth charts.
    """
    moving_records = set(target_team_by_record_index)
    affected_teams = {
        team_code
        for record_index, target_code in target_team_by_record_index.items()
        for team_code in (int(instance_rows[record_index].get(INSTANCE_TEAM_FIELD, -1)), target_code)
        if team_code >= 0 and team_code != 255
    }
    planned: dict[int, int] = {}
    for team_code in sorted(affected_teams):
        retained_slots: set[int] = set()
        incoming: list[int] = []
        final_count = 0
        for record_index, row in enumerate(instance_rows):
            instance_id = int(row.get(INSTANCE_KEY_FIELD) or -1)
            current_code = int(row.get(INSTANCE_TEAM_FIELD, -1))
            final_code = target_team_by_record_index.get(record_index, current_code)
            if final_code != team_code:
                continue
            final_count += 1
            if record_index in moving_records and current_code != team_code:
                incoming.append(record_index)
                continue
            roster_slot = int(row.get(ROSTER_SLOT_FIELD, -1)) - team_code * ROSTER_SLOTS_PER_TEAM
            if not 0 <= roster_slot < ROSTER_SLOTS_PER_TEAM:
                raise RuntimeError(
                    f"Unsafe roster slot on team {team_code}: instance {instance_id} has "
                    f"{ROSTER_SLOT_FIELD}={row.get(ROSTER_SLOT_FIELD)!r}."
                )
            if roster_slot in retained_slots:
                raise RuntimeError(f"Duplicate roster slot {roster_slot} on team {team_code}.")
            retained_slots.add(roster_slot)
        if final_count > ROSTER_SLOTS_PER_TEAM:
            raise RuntimeError(
                f"Team {team_code} would contain {final_count} players; NHL Legacy allows "
                f"at most {ROSTER_SLOTS_PER_TEAM}."
            )
        available = [slot for slot in range(ROSTER_SLOTS_PER_TEAM) if slot not in retained_slots]
        if len(available) < len(incoming):
            raise RuntimeError(f"Not enough roster slots on team {team_code}.")
        for record_index, roster_slot in zip(sorted(incoming), available):
            planned[record_index] = team_code * ROSTER_SLOTS_PER_TEAM + roster_slot
    return planned


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
    team_by_code, _team_by_abbrev = get_team_maps(db_path)

    candidates: list[InstanceCandidate] = []
    for rel_index, rel in enumerate(snapshot.relation_rows):
        instance_id = int(rel["qEfv"])
        for instance_record_index, instance_row in enumerate(instance_rows):
            if int(instance_row.get(INSTANCE_KEY_FIELD) or -1) != instance_id:
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
                    instance_record_index=instance_record_index,
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
    move = (
        (first_name, last_name, target_abbrev, player_id)
        if player_id is not None
        else (first_name, last_name, target_abbrev)
    )
    results = move_players_to_teams(db_path, [move])
    if not results:
        raise RuntimeError(f"{first_name} {last_name} is already on {target_abbrev.upper()}.")
    return results[0]


def move_players_to_teams(
    db_path: Path,
    moves: list[
        tuple[str, str, str]
        | tuple[str, str, str, int]
        | tuple[str, str, str, int, str]
    ],
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
    bio_record_by_player_id = {
        int(row.get("zIBw") or -1): index
        for index, row in enumerate(bio_rows)
    }
    relation_by_player_key: dict[object, list[dict[str, object]]] = {}
    for row in relation_rows:
        relation_by_player_key.setdefault(row.get("qFky"), []).append(row)
    instances_by_id: dict[int, list[tuple[int, dict[str, object]]]] = {}
    for index, row in enumerate(instance_rows):
        instances_by_id.setdefault(int(row.get(INSTANCE_KEY_FIELD) or -1), []).append((index, row))

    prepared: list[dict[str, object]] = []
    for move in moves:
        first_name, last_name, target_abbrev = move[:3]
        requested_player_id = int(move[3]) if len(move) >= 4 else None
        expected_source = str(move[4]).upper() if len(move) >= 5 and move[4] else None
        target_team = resolve_team_abbrev(target_abbrev, list(team_by_code.values()))
        if target_team is None:
            raise RuntimeError(f"Unknown team abbreviation: {target_abbrev}")
        bio = bio_by_player_id.get(requested_player_id) if requested_player_id is not None else bio_by_name.get((first_name, last_name))
        if bio is None:
            raise RuntimeError(f"Player not found: {first_name} {last_name}")
        player_id = bio.get("zIBw")
        relation_candidates = list(relation_by_player_key.get(player_id, []))
        candidates: list[InstanceCandidate] = []
        for rel_index, rel in enumerate(relation_candidates):
            instance_id = int(rel.get("qEfv") or -1)
            for instance_record_index, instance_row in instances_by_id.get(instance_id, []):
                team_code = int(instance_row.get(INSTANCE_TEAM_FIELD, -1))
                team = team_by_code.get(team_code)
                team_abbrev = None if team is None else team.abbrev
                if expected_source is not None and (team_abbrev or "").upper() != expected_source:
                    continue
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
                        instance_record_index=instance_record_index,
                        instance_id=instance_id,
                        team_code=team_code,
                        team_abbrev=team_abbrev,
                        score=score,
                    )
                )
        if not candidates:
            raise RuntimeError(f"No instance candidates found for {first_name} {last_name}")
        candidates.sort(key=lambda item: (-item.score, item.instance_id))
        instance = candidates[0]
        record_index = instance.instance_record_index
        if instance.team_code == target_team.code:
            continue
        prepared.append({
            "first_name": first_name,
            "last_name": last_name,
            "player_id": int(player_id),
            "from_team": instance.team_abbrev,
            "source_code": instance.team_code,
            "instance_id": instance.instance_id,
            "record_index": record_index,
            "bio_record": bio_record_by_player_id[int(player_id)],
            "target_code": target_team.code,
            "position_group": _position_group(bio),
            "contract_before": {field: bio.get(field) for field in CONTRACT_PRESERVED_FIELDS},
        })

    instance_owners: dict[int, int] = {}
    for move in prepared:
        record_index = int(move["record_index"])
        player_id = int(move["player_id"])
        previous_owner = instance_owners.setdefault(record_index, player_id)
        if previous_owner != player_id:
            raise RuntimeError(
                f"Unsafe move collision: instance record {record_index} resolved to both "
                f"player {previous_owner} and player {player_id}."
            )

    if not prepared:
        return []

    target_by_record = {
        int(move["record_index"]): int(move["target_code"])
        for move in prepared
    }
    planned_roster_slots = plan_roster_slot_updates(instance_rows, target_by_record)
    instance_field_specs = access.list_fields_by_index(db_path, PLAYER_INSTANCE_TABLE_INDEX)
    lineup_field_names = {
        field.name for field in instance_field_specs if field.size == 1
    }
    if any(field.name == CAPTAINCY_FIELD for field in instance_field_specs):
        lineup_field_names.add(CAPTAINCY_FIELD)

    # A moved instance must not carry its old team's line-slot markers into
    # the destination.  When an outgoing player of the same position group
    # exists, transfer that complete lineup template to the incoming player;
    # otherwise the incoming player is safely scratched and the game can
    # auto-fill the line in Franchise mode.
    templates: dict[tuple[int, str], list[dict[str, int]]] = {}
    for move in prepared:
        source_row = instance_rows[int(move["record_index"])]
        key = (int(move["source_code"]), str(move["position_group"]))
        template = {field: int(source_row.get(field) or 0) for field in lineup_field_names}
        template["__slot"] = int(source_row.get(ROSTER_SLOT_FIELD) or 0)
        templates.setdefault(key, []).append(template)
    for values in templates.values():
        values.sort(key=lambda row: (-int(row.get("jZSh", 0)), int(row["__slot"])))

    lineup_updates_by_record: dict[int, dict[str, int]] = {}
    incoming_by_target: dict[tuple[int, str], list[dict[str, object]]] = {}
    for move in prepared:
        key = (int(move["target_code"]), str(move["position_group"]))
        incoming_by_target.setdefault(key, []).append(move)
    for key, incoming in incoming_by_target.items():
        incoming.sort(key=lambda move: int(move["player_id"]))
        available_templates = templates.get(key, [])
        for index, move in enumerate(incoming):
            update = {field: 0 for field in lineup_field_names}
            if index < len(available_templates):
                update.update({
                    field: value
                    for field, value in available_templates[index].items()
                    if field != "__slot"
                })
            lineup_updates_by_record[int(move["record_index"])] = update

    results: list[dict[str, object]] = []
    instance_table_index = access.resolve_table_index(db_path, PLAYER_INSTANCE_TABLE_INDEX)
    bio_table_index = access.resolve_table_index(db_path, PLAYER_BIO_TABLE_INDEX)
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
        roster_slot_field = fields.get(ROSTER_SLOT_FIELD)
        if roster_slot_field is None:
            raise RuntimeError(f"Field not found: {table.name}.{ROSTER_SLOT_FIELD}")
        bio_table = access.get_table_properties(db_index, bio_table_index)
        bio_fields = {
            field.name: field
            for field in (
                access.get_field_properties(db_index, bio_table.name, field_index)
                for field_index in range(bio_table.field_count)
            )
        }
        bio_team_field = bio_fields.get(INSTANCE_TEAM_FIELD)
        if bio_team_field is None:
            raise RuntimeError(f"Field not found: {bio_table.name}.{INSTANCE_TEAM_FIELD}")
        used_roster_slots = {int(row.get(ROSTER_SLOT_FIELD) or 0) for row in instance_rows}
        temporary_slots = [value for value in range((1 << 14) - 1, -1, -1) if value not in used_roster_slots]
        if len(temporary_slots) < len(prepared):
            raise RuntimeError("Not enough temporary roster slots to apply the move atomically.")
        for move, temporary_slot in zip(prepared, temporary_slots):
            access.set_field_value(
                db_index,
                table.name,
                roster_slot_field,
                int(move["record_index"]),
                temporary_slot,
            )

        for move in prepared:
            first_name = str(move["first_name"])
            last_name = str(move["last_name"])
            player_id = int(move["player_id"])
            from_team = move["from_team"]
            instance_id = int(move["instance_id"])
            record_index = int(move["record_index"])
            bio_record = int(move["bio_record"])
            target_code = int(move["target_code"])
            target_team = team_by_code[target_code]
            access.set_field_value(db_index, table.name, team_field, record_index, target_code)
            new_roster_slot = planned_roster_slots[record_index]
            access.set_field_value(db_index, table.name, roster_slot_field, record_index, new_roster_slot)
            for field_name, value in lineup_updates_by_record[record_index].items():
                access.set_field_value(db_index, table.name, fields[field_name], record_index, value)
            target_league = league_name_for_team(target_team)
            contract_team_code = franchise_contract_team_code(target_team, list(team_by_code.values()))
            access.set_field_value(db_index, bio_table.name, bio_team_field, bio_record, contract_team_code)
            rights_code = None
            if target_league in {"NHL", "AHL", "Organization"}:
                rights_code = franchise_rights_code(target_team, list(team_by_code.values()))
                for field_name in ("WBbd", "uWgv"):
                    field = bio_fields.get(field_name)
                    if field is not None:
                        access.set_field_value(
                            db_index,
                            bio_table.name,
                            field,
                            bio_record,
                            rights_code,
                        )
            contract_before = dict(move["contract_before"])
            contract_after = {
                field_name: access.get_field_value(
                    db_index,
                    bio_table.name,
                    bio_fields[field_name],
                    bio_record,
                )
                for field_name in CONTRACT_PRESERVED_FIELDS
                if field_name in bio_fields
            }
            changed_contract = {
                field_name: (contract_before.get(field_name), value)
                for field_name, value in contract_after.items()
                if contract_before.get(field_name) != value
            }
            if changed_contract:
                raise RuntimeError(
                    f"Roster move changed contract fields for {first_name} {last_name}: {changed_contract}"
                )
            results.append(
                {
                    "player": f"{first_name} {last_name}",
                    "player_id": player_id,
                    "instance_id": instance_id,
                    "instance_record_index": record_index,
                    "target_roster_slot": new_roster_slot,
                    "lineup_updates": dict(lineup_updates_by_record[record_index]),
                    "contract_preserved": True,
                    "from_team": from_team,
                    "to_team": target_team.abbrev,
                    "target_team_code": target_code,
                    "changes": [
                        {
                            "section": "instance_rows",
                            "field": INSTANCE_TEAM_FIELD,
                            "before": from_team,
                            "after": target_team.abbrev,
                        },
                        {
                            "section": "instance_rows",
                            "field": ROSTER_SLOT_FIELD,
                            "before": instance_rows[record_index].get(ROSTER_SLOT_FIELD),
                            "after": new_roster_slot,
                        },
                        {
                            "section": "instance_rows",
                            "field": "lineup assignments",
                            "before": "source team",
                            "after": "transferred template" if any(lineup_updates_by_record[record_index].values()) else "scratched",
                        },
                        *(
                            [
                                {
                                    "section": "bio",
                                    "field": "organization rights",
                                    "before": "previous organization",
                                    "after": rights_code,
                                }
                            ]
                            if rights_code is not None
                            else []
                        ),
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
    players: list[
        tuple[str, str]
        | tuple[str, str, int]
        | tuple[str, str, int, str]
    ],
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
    else:
        bio_rows = snapshot_cache.bio_rows
        relation_rows = snapshot_cache.relation_rows
        instance_rows = snapshot_cache.instance_rows

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
    instances_by_id: dict[int, list[tuple[int, dict[str, object]]]] = {}
    for index, row in enumerate(instance_rows):
        instances_by_id.setdefault(int(row.get(INSTANCE_KEY_FIELD) or -1), []).append((index, row))
    prepared: list[tuple[str, str, int, str | None, int, int, int]] = []
    seen_player_ids: set[int] = set()
    for player in players:
        first_name, last_name = player[:2]
        requested_player_id = int(player[2]) if len(player) >= 3 else None
        expected_source = str(player[3]).upper() if len(player) >= 4 and player[3] else None
        bio = bio_by_player_id.get(requested_player_id) if requested_player_id is not None else bio_by_name.get((first_name, last_name))
        if bio is None:
            raise RuntimeError(f"Player not found: {first_name} {last_name}")
        player_id = int(bio.get("zIBw") or -1)
        if player_id in seen_player_ids:
            continue
        seen_player_ids.add(player_id)
        relation_candidates = list(relation_by_player_key.get(player_id, []))
        candidates: list[InstanceCandidate] = []
        for rel_index, relation in enumerate(relation_candidates):
            instance_id = int(relation.get("qEfv") or -1)
            for instance_record_index, instance_row in instances_by_id.get(instance_id, []):
                team_code = int(instance_row.get(INSTANCE_TEAM_FIELD, -1))
                team = team_by_code.get(team_code)
                team_abbrev = None if team is None else team.abbrev
                if expected_source is not None and (team_abbrev or "").upper() != expected_source:
                    continue
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
                        instance_record_index=instance_record_index,
                        instance_id=instance_id,
                        team_code=team_code,
                        team_abbrev=team_abbrev,
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
                instance.instance_record_index,
                bio_record_by_player_id[player_id],
            )
        )

    instance_owners: dict[int, int] = {}
    for _first, _last, player_id, _from, _instance_id, instance_record, *_rest in prepared:
        previous_owner = instance_owners.setdefault(instance_record, player_id)
        if previous_owner != player_id:
            raise RuntimeError(
                f"Unsafe release collision: instance record {instance_record} resolved to both "
                f"player {previous_owner} and player {player_id}."
            )

    instance_table_index = access.resolve_table_index(db_path, PLAYER_INSTANCE_TABLE_INDEX)
    bio_table_index = access.resolve_table_index(db_path, PLAYER_BIO_TABLE_INDEX)
    prepared_records = {item[5] for item in prepared}
    used_roster_slots = {
        int(row.get(ROSTER_SLOT_FIELD) or -1)
        for index, row in enumerate(instance_rows)
        if index not in prepared_records
    }
    available_free_agent_slots = iter(
        value for value in range((1 << 14) - 1, -1, -1)
        if value not in used_roster_slots
    )
    free_agent_slot_by_record = {
        item[5]: next(available_free_agent_slots) for item in prepared
    }
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
        bio_table, bio_fields = table_fields(bio_table_index)
        for first_name, last_name, player_id, from_team, instance_id, instance_record, bio_record in prepared:
            access.set_field_value(db_index, instance_table.name, instance_fields[INSTANCE_TEAM_FIELD], instance_record, 255)
            free_agent_slot = free_agent_slot_by_record[instance_record]
            access.set_field_value(db_index, instance_table.name, instance_fields[ROSTER_SLOT_FIELD], instance_record, free_agent_slot)
            for field_name, field in instance_fields.items():
                if field.size == 1 or field_name == CAPTAINCY_FIELD:
                    access.set_field_value(db_index, instance_table.name, field, instance_record, 0)
            # Franchise mode only exposes a released player in its signable
            # free-agent pool when both the active roster slot and the compact
            # contract/rights ownership fields are cleared.  The single-player
            # release path already did this; bulk releases must be identical.
            for field_name in (
                "BSXd",
                "dhKk",
                "GDhI",
                "NYKk",
                "DVoL",
                "IzRv",
                "IrlK",
                "xdoJ",
                "LcvS",
                "WBbd",
                "uWgv",
            ):
                field = bio_fields.get(field_name)
                if field is not None:
                    access.set_field_value(db_index, bio_table.name, field, bio_record, 0)
            results.append(
                {
                    "player": f"{first_name} {last_name}",
                    "player_id": player_id,
                    "instance_id": instance_id,
                    "instance_record_index": instance_record,
                    "target_roster_slot": free_agent_slot,
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
    record_index = instance.instance_record_index
    instance_updates = {INSTANCE_TEAM_FIELD: target_team_code}
    if target_team_code == 255:
        for field in access.list_fields_by_index(db_path, PLAYER_INSTANCE_TABLE_INDEX):
            if field.size == 1 or field.name == CAPTAINCY_FIELD:
                instance_updates[field.name] = 0
    access.update_record_fields(
        db_path,
        PLAYER_INSTANCE_TABLE_INDEX,
        record_index,
        instance_updates,
    )
    if target_team_code == 255:
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
                    "BSXd": 0,
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
        "instance_record_index": record_index,
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
