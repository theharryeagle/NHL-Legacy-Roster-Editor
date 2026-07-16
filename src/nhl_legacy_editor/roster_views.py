from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .player_tools import PLAYER_BIO_TABLE_INDEX, PLAYER_RELATION_TABLE_INDEX
from .tdb_access import TdbAccess
from .team_tools import TeamRecord, canonical_organization_abbrev, get_team_maps, league_name_for_team, load_teams


ACTIVE_ORGANIZATION_LEAGUES = frozenset({"NHL", "AHL", "Organization"})


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
    is_hidden: bool = False


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
    if league == "NHL" and 0 <= team.code <= 29:
        return 1100
    if league == "NHL":
        return 1050
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


def resolve_player_organization(
    current_team: TeamRecord | None,
    rights_team: TeamRecord | None,
    *,
    current_team_code: int | None,
    current_league_name: str,
    organization_links: dict[str, str] | None = None,
) -> str | None:
    """Resolve current organization without losing unsigned prospect rights.

    The roster rights fields retain the team that drafted or controls a
    player, and can remain stale after an NHL trade. An active NHL, AHL, or
    system-team assignment is stronger evidence of present organization.
    Junior, European, prospect-pool, hidden, and free-agent entries still use
    their rights field because their current club is not an NHL parent.
    """
    current_organization = canonical_organization_abbrev(current_team, organization_links)
    rights_organization = canonical_organization_abbrev(rights_team, organization_links)
    if (
        current_team_code != 255
        and current_league_name in ACTIVE_ORGANIZATION_LEAGUES
        and current_organization is not None
    ):
        return current_organization
    return rights_organization or current_organization


def build_player_index_from_tables(
    bio_rows: list[dict[str, object]],
    relation_rows: list[dict[str, object]],
    instance_rows: list[dict[str, object]],
    team_by_code: dict[int, TeamRecord],
    organization_links: dict[str, str] | None = None,
) -> list[PlayerListEntry]:
    relation_by_player_id: dict[int, list[int]] = {}
    for row in relation_rows:
        player_id = int(row["qFky"]) if row.get("qFky") is not None else -1
        instance_id = int(row["qEfv"]) if row.get("qEfv") is not None else -1
        relation_by_player_id.setdefault(player_id, []).append(instance_id)
    instance_team_by_id = {
        (int(row["TWSX"]) if row.get("TWSX") is not None else -1):
        (int(row["BSXd"]) if row.get("BSXd") is not None else -1)
        for row in instance_rows
    }

    entries: list[PlayerListEntry] = []
    seen_player_ids: set[int] = set()
    for row in bio_rows:
        first_name = str(row.get("PedH") or "").strip()
        last_name = str(row.get("RMbQ") or "").strip()
        if not first_name and not last_name:
            continue
        player_id = int(row.get("zIBw") or -1)
        if player_id in seen_player_ids:
            continue
        seen_player_ids.add(player_id)
        team_code = None
        best_score = -1
        relation_keys = {player_id}
        long_id = int(row.get("DaPp") or -1)
        if long_id >= 0:
            relation_keys.add(long_id)
        instance_ids: list[int] = []
        seen_instance_ids: set[int] = set()
        for relation_key in relation_keys:
            for instance_id in relation_by_player_id.get(relation_key, []):
                if instance_id in seen_instance_ids:
                    continue
                seen_instance_ids.add(instance_id)
                instance_ids.append(instance_id)
        for instance_id in instance_ids:
            candidate_code = instance_team_by_id.get(instance_id)
            team = None if candidate_code is None else team_by_code.get(candidate_code)
            score = _primary_team_score(team, candidate_code)
            if score > best_score:
                team_code = candidate_code
                best_score = score
        is_hidden = team_code is None
        team = None if team_code is None else team_by_code.get(team_code)
        current_team_code = team_code
        current_team_abbrev = None if team is None else team.abbrev
        current_team_name = None if team is None else team.name
        if is_hidden:
            league_group = "hidden"
            league_name = "Hidden"
        else:
            league_group = classify_team(team if current_team_code != 255 else None)
            league_name = "Free Agents" if current_team_code == 255 else league_name_for_team(team)
        rights_code = int(row.get("WBbd") or 0) - 1
        rights_team = team_by_code.get(rights_code) if rights_code >= 0 else None
        organization_abbrev = resolve_player_organization(
            team if current_team_code != 255 else None,
            rights_team,
            current_team_code=current_team_code,
            current_league_name=league_name,
            organization_links=organization_links,
        )
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
                organization_abbrev=organization_abbrev,
                is_hidden=is_hidden,
            )
        )
    entries.sort(key=lambda item: (item.current_team_abbrev or "ZZZ", item.last_name, item.first_name))
    return entries


def load_player_index(db_path: Path) -> list[PlayerListEntry]:
    access = TdbAccess()
    tables = access.list_tables(db_path)
    bio_index = access.resolve_table_index(db_path, PLAYER_BIO_TABLE_INDEX)
    relation_index = access.resolve_table_index(db_path, PLAYER_RELATION_TABLE_INDEX)
    instance_index = access.resolve_table_index(db_path, "ulGe")
    _table, _fields, bio_rows = access.sample_records(
        db_path,
        PLAYER_BIO_TABLE_INDEX,
        limit=tables[bio_index].record_count,
    )
    _rel_table, _rel_fields, relation_rows = access.sample_records(
        db_path,
        PLAYER_RELATION_TABLE_INDEX,
        limit=tables[relation_index].record_count,
    )
    _inst_table, _inst_fields, instance_rows = access.sample_records(
        db_path,
        "ulGe",
        limit=tables[instance_index].record_count,
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
