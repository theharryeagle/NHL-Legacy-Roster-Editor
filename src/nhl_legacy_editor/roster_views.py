from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .player_tools import PLAYER_BIO_TABLE_INDEX, PLAYER_RELATION_TABLE_INDEX
from .tdb_access import TdbAccess
from .team_tools import TeamRecord, canonical_organization_abbrev, get_team_maps, league_name_for_team, load_teams


@dataclass(slots=True)
class PlayerListEntry:
    first_name: str
    last_name: str
    full_name: str
    player_id: int
    current_team_code: int | None
    current_team_abbrev: str | None
    current_team_name: str | None
    league_group: str
    league_name: str
    organization_abbrev: str | None


def classify_team(team: TeamRecord | None) -> str:
    if team is None:
        return "free_agents"
    league_name = league_name_for_team(team)
    name = team.name.lower()
    if league_name == "NHL":
        return "nhl"
    if league_name in {"Prospects", "Organization"} or "prospect" in name or "system" in name:
        return "prospects"
    return "other_leagues"


def _primary_team_score(team: TeamRecord | None, team_code: int | None) -> int:
    if team_code == 255:
        return 10
    if team is None:
        return 0
    league = league_name_for_team(team)
    if league == "NHL":
        return 1000
    if league == "AHL":
        return 900
    if league == "Organization":
        return 850
    if league == "Prospects":
        return 800
    if league == "CHL / Juniors":
        return 700
    if league == "Europe":
        return 650
    if league == "Other League":
        return 500
    if league in {"International", "World Cup", "EASHL", "Exhibition"}:
        return 100
    return 250


def build_player_index_from_tables(
    bio_rows: list[dict[str, object]],
    relation_rows: list[dict[str, object]],
    instance_rows: list[dict[str, object]],
    team_by_code: dict[int, TeamRecord],
    organization_links: dict[str, str] | None = None,
) -> list[PlayerListEntry]:
    relation_by_player_id: dict[int, list[int]] = {}
    for row in relation_rows:
        player_id = int(row.get("qFky") or -1)
        instance_id = int(row.get("qEfv") or -1)
        relation_by_player_id.setdefault(player_id, []).append(instance_id)
    instance_team_by_id = {
        int(row.get("TWSX") or -1): int(row.get("BSXd") or -1)
        for row in instance_rows
    }

    entries: list[PlayerListEntry] = []
    for row in bio_rows:
        first_name = str(row.get("PedH") or "").strip()
        last_name = str(row.get("RMbQ") or "").strip()
        if not first_name and not last_name:
            continue
        player_id = int(row.get("zIBw") or -1)
        team_code = None
        best_score = -1
        for instance_id in relation_by_player_id.get(player_id, []):
            candidate_code = instance_team_by_id.get(instance_id)
            team = None if candidate_code is None else team_by_code.get(candidate_code)
            score = _primary_team_score(team, candidate_code)
            if score > best_score:
                team_code = candidate_code
                best_score = score
        team = None if team_code is None else team_by_code.get(team_code)
        current_team_code = team_code
        current_team_abbrev = None if team is None else team.abbrev
        current_team_name = None if team is None else team.name
        league_group = classify_team(team if current_team_code != 255 else None)
        league_name = "Free Agents" if current_team_code == 255 else league_name_for_team(team)
        entries.append(
            PlayerListEntry(
                first_name=first_name,
                last_name=last_name,
                full_name=f"{first_name} {last_name}".strip(),
                player_id=player_id,
                current_team_code=current_team_code,
                current_team_abbrev=current_team_abbrev,
                current_team_name=current_team_name,
                league_group=league_group,
                league_name=league_name,
                organization_abbrev=canonical_organization_abbrev(
                    team if current_team_code != 255 else None,
                    organization_links,
                ),
            )
        )
    entries.sort(key=lambda item: (item.current_team_abbrev or "ZZZ", item.last_name, item.first_name))
    return entries


def load_player_index(db_path: Path) -> list[PlayerListEntry]:
    access = TdbAccess()
    tables = access.list_tables(db_path)
    _table, _fields, bio_rows = access.sample_records(
        db_path,
        PLAYER_BIO_TABLE_INDEX,
        limit=tables[PLAYER_BIO_TABLE_INDEX].record_count,
    )
    _rel_table, _rel_fields, relation_rows = access.sample_records(
        db_path,
        PLAYER_RELATION_TABLE_INDEX,
        limit=tables[PLAYER_RELATION_TABLE_INDEX].record_count,
    )
    _inst_table, _inst_fields, instance_rows = access.sample_records(
        db_path,
        3,
        limit=tables[3].record_count,
    )
    team_by_code, _team_by_abbrev = get_team_maps(db_path)
    return build_player_index_from_tables(bio_rows, relation_rows, instance_rows, team_by_code)


def filter_player_index(
    entries: list[PlayerListEntry],
    *,
    team_filter: str = "ALL",
    league_filter: str = "all",
    search: str = "",
) -> list[PlayerListEntry]:
    search_lower = search.lower().strip()
    team_upper = team_filter.upper().strip()
    filtered: list[PlayerListEntry] = []
    for entry in entries:
        if team_upper == "FREE":
            if entry.league_group != "free_agents":
                continue
        elif team_upper not in {"", "ALL"} and (entry.current_team_abbrev or "").upper() != team_upper:
            continue
        if league_filter not in {"", "all"} and entry.league_group != league_filter:
            continue
        if search_lower and search_lower not in entry.full_name.lower():
            continue
        filtered.append(entry)
    return filtered


def build_team_collections(db_path: Path) -> dict[str, list[TeamRecord]]:
    teams = load_teams(db_path)
    grouped = {"nhl": [], "prospects": [], "other_leagues": []}
    for team in teams:
        grouped[classify_team(team)].append(team)
    for rows in grouped.values():
        rows.sort(key=lambda item: (item.city, item.abbrev))
    return grouped
