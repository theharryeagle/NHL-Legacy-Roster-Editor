from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contract_models import DEFAULT_GAME_CAP_MILLIONS, MINIMUM_GAME_AAV_MILLIONS
from .player_editing import contract_cap_hit_millions_from_raw
from .roster_sync import build_player_name_indexes, find_player_name_matches
from .team_tools import TeamRecord, league_name_for_team, organization_for_abbrev


FRANCHISE_FORWARD_COUNT = 12
FRANCHISE_DEFENSE_COUNT = 6
FRANCHISE_GOALIE_COUNT = 2
FRANCHISE_CORE_SIZE = 20
FRANCHISE_ROSTER_SIZE = 23
ELIGIBLE_FRANCHISE_LEAGUES = frozenset({"NHL", "AHL", "Organization"})
POSITION_BY_CODE = {0: "C", 1: "LW", 2: "RW", 3: "D", 4: "G"}


@dataclass(frozen=True, slots=True)
class FranchiseCandidate:
    player_id: int
    player_name: str
    first_name: str
    last_name: str
    organization: str
    current_team_abbrev: str | None
    current_team_code: int | None
    position: str
    overall: int
    cap_hit_millions: float

    @property
    def position_group(self) -> str:
        if self.position == "G":
            return "G"
        if self.position == "D":
            return "D"
        return "F"


def _cap_units(cap_hit_millions: float) -> int:
    # Contract fields are stored in $5,000 units. Treat invalid zero-dollar
    # contracts as league minimum so a generated roster is safely under cap.
    safe_hit = max(MINIMUM_GAME_AAV_MILLIONS, float(cap_hit_millions))
    return round(safe_hit * 200)


def select_franchise_roster(
    candidates: Iterable[FranchiseCandidate],
    *,
    cap_millions: float = DEFAULT_GAME_CAP_MILLIONS,
    forward_count: int = FRANCHISE_FORWARD_COUNT,
    defense_count: int = FRANCHISE_DEFENSE_COUNT,
    goalie_count: int = FRANCHISE_GOALIE_COUNT,
    preferred_player_ids: Iterable[int] = (),
) -> tuple[FranchiseCandidate, ...]:
    """Select the best playable core, then add cap-safe skater backups.

    The salary cap never pushes a higher-overall player out of the 18-skater,
    two-goalie core. Franchise Mode's CPU can resolve an over-cap core. Up to
    three additional skaters are carried only when their full cap hit fits.
    ``preferred_player_ids`` remains accepted for settings/backward
    compatibility, but external lineup sources do not influence talent rank.
    """
    candidates = tuple(candidates)
    required = {"F": forward_count, "D": defense_count, "G": goalie_count}
    grouped = {
        group: tuple(player for player in candidates if player.position_group == group)
        for group in required
    }
    shortages = [
        f"{group}: need {count}, found {len(grouped[group])}"
        for group, count in required.items()
        if len(grouped[group]) < count
    ]
    if shortages:
        raise ValueError("Insufficient franchise roster candidates (" + "; ".join(shortages) + ")")

    del preferred_player_ids
    selected = [
        player
        for group, count in required.items()
        for player in sorted(grouped[group], key=lambda item: (-item.overall, item.player_id))[:count]
    ]
    selected_ids = {player.player_id for player in selected}
    cap_units = round(float(cap_millions) * 200)
    used_units = sum(_cap_units(player.cap_hit_millions) for player in selected)
    backups = sorted(
        (
            player for player in candidates
            if player.position_group != "G" and player.player_id not in selected_ids
        ),
        key=lambda item: (-item.overall, item.player_id),
    )
    for player in backups:
        if len(selected) >= FRANCHISE_ROSTER_SIZE:
            break
        player_units = _cap_units(player.cap_hit_millions)
        if used_units + player_units <= cap_units:
            selected.append(player)
            used_units += player_units
    return tuple(sorted(selected, key=lambda player: (player.position_group, -player.overall, player.player_id)))


def build_franchise_candidates(
    player_index,
    snapshot_cache,
    player_overall_by_id: dict[int, int],
    organization: str,
) -> list[FranchiseCandidate]:
    candidates: list[FranchiseCandidate] = []
    for player in player_index:
        if player.is_hidden or player.league_name not in ELIGIBLE_FRANCHISE_LEAGUES:
            continue
        if player.organization_abbrev != organization:
            continue
        overall = player_overall_by_id.get(int(player.player_id))
        if overall is None:
            continue
        snapshot = snapshot_cache.get_player_snapshot(
            player.first_name,
            player.last_name,
            player.player_id,
        )
        if snapshot is None:
            continue
        raw_position = snapshot.bio.get("aljv")
        position = POSITION_BY_CODE.get(int(raw_position) if raw_position is not None else -1)
        if position is None:
            continue
        candidates.append(
            FranchiseCandidate(
                player_id=int(player.player_id),
                player_name=player.full_name,
                first_name=player.first_name,
                last_name=player.last_name,
                organization=organization,
                current_team_abbrev=player.current_team_abbrev,
                current_team_code=player.current_team_code,
                position=position,
                overall=int(overall),
                cap_hit_millions=contract_cap_hit_millions_from_raw(snapshot.bio.get("dhKk")),
            )
        )
    return candidates


def build_franchise_roster_update(
    player_index,
    snapshot_cache,
    player_overall_by_id: dict[int, int],
    teams: Iterable[TeamRecord],
    *,
    cap_millions: float = DEFAULT_GAME_CAP_MILLIONS,
    official_roster_rows=(),
    puckpedia_charts=(),
    puckpedia_errors=(),
) -> dict[str, list[dict[str, object]]]:
    teams = tuple(teams)
    base_teams = [team for team in teams if 0 <= team.code <= 29]
    ahl_by_org = {
        organization_for_abbrev(team.abbrev): team
        for team in teams
        if league_name_for_team(team) == "AHL" and organization_for_abbrev(team.abbrev)
    }
    moves: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    players_by_name, players_by_equivalent_name = build_player_name_indexes(player_index)
    puckpedia_preferred_by_org: dict[str, set[int]] = {}
    puckpedia_unmatched: list[dict[str, object]] = []
    puckpedia_seasons: dict[str, dict[str, object]] = {}
    official_preferred_by_org: dict[str, set[int]] = {}
    official_unmatched: list[dict[str, object]] = []
    for row in official_roster_rows:
        team_abbrev = str(getattr(row, "team_abbrev", "") or "").upper()
        organization = organization_for_abbrev(team_abbrev)
        if organization is None:
            continue
        matches = [
            player
            for player in find_player_name_matches(
                str(getattr(row, "full_name", "")),
                players_by_name,
                players_by_equivalent_name,
            )
            if not bool(getattr(player, "is_hidden", False))
        ]
        unique_ids = {int(getattr(player, "player_id", -1)) for player in matches}
        if len(unique_ids) != 1:
            official_unmatched.append(
                {
                    "player_name": getattr(row, "full_name", ""),
                    "team": team_abbrev,
                    "reason": "missing or ambiguous roster identity",
                    "source": "NHL.com Active Roster",
                }
            )
            continue
        official_preferred_by_org.setdefault(organization, set()).add(next(iter(unique_ids)))
    for chart in puckpedia_charts:
        chart_team = str(getattr(chart, "team_abbrev", "") or "").upper()
        organization = organization_for_abbrev(chart_team)
        puckpedia_seasons[chart_team] = {
            "lines": getattr(chart, "lines_season", None),
            "contracts": getattr(chart, "contracts_season", None),
            "last_updated": getattr(chart, "last_updated", None),
        }
        if organization is None:
            continue
        # PuckPedia deliberately exposes the lineup and contract seasons
        # separately. During the offseason those often differ; stale lineups
        # remain useful for review but must not force Franchise Builder picks.
        lineup_is_current = (
            getattr(chart, "lines_season", None)
            and getattr(chart, "lines_season", None) == getattr(chart, "contracts_season", None)
        )
        for row in getattr(chart, "players", ()):
            if getattr(row, "status", None) != "nhl" or not lineup_is_current:
                continue
            matches = [
                player
                for player in find_player_name_matches(
                    str(getattr(row, "full_name", "")),
                    players_by_name,
                    players_by_equivalent_name,
                )
                if not bool(getattr(player, "is_hidden", False))
            ]
            unique_ids = {int(getattr(player, "player_id", -1)) for player in matches}
            if len(unique_ids) != 1:
                puckpedia_unmatched.append(
                    {
                        "player_name": getattr(row, "full_name", ""),
                        "team": chart_team,
                        "section": getattr(row, "section", ""),
                        "reason": "missing or ambiguous roster identity",
                        "source": "PuckPedia Depth Charts",
                    }
                )
                continue
            puckpedia_preferred_by_org.setdefault(organization, set()).add(next(iter(unique_ids)))

    for target_team in sorted(base_teams, key=lambda team: team.code):
        organization = organization_for_abbrev(target_team.abbrev)
        if organization is None:
            continue
        candidates = build_franchise_candidates(
            player_index,
            snapshot_cache,
            player_overall_by_id,
            organization,
        )
        try:
            selected = select_franchise_roster(
                candidates,
                cap_millions=cap_millions,
            )
        except ValueError as exc:
            errors.append({"team": target_team.abbrev, "organization": organization, "error": str(exc)})
            continue
        selected_ids = {player.player_id for player in selected}
        roster_cap = sum(max(MINIMUM_GAME_AAV_MILLIONS, player.cap_hit_millions) for player in selected)
        summaries.append(
            {
                "team": target_team.abbrev,
                "organization": organization,
                "players": len(selected),
                "core_players": FRANCHISE_CORE_SIZE,
                "backup_skaters": max(0, len(selected) - FRANCHISE_CORE_SIZE),
                "cap_hit_millions": round(roster_cap, 3),
                "over_cap": roster_cap > cap_millions,
                "average_overall": round(sum(player.overall for player in selected) / len(selected), 2),
                "puckpedia_nhl_players_selected": sum(
                    player.player_id in puckpedia_preferred_by_org.get(organization, set())
                    for player in selected
                ),
                "official_nhl_players_selected": sum(
                    player.player_id in official_preferred_by_org.get(organization, set())
                    for player in selected
                ),
            }
        )
        for player in selected:
            if player.current_team_code == target_team.code:
                continue
            moves.append(
                {
                    "player_name": player.player_name,
                    "player_id": player.player_id,
                    "from_team": player.current_team_abbrev,
                    "to_team": target_team.abbrev,
                    "source": "Franchise Roster Builder",
                    "reason": (
                        f"Promote to best-available franchise roster: {player.position}, "
                        f"{player.overall} OVR, ${max(MINIMUM_GAME_AAV_MILLIONS, player.cap_hit_millions):.3f}M cap hit"
                    ),
                }
            )

        affiliate = ahl_by_org.get(organization)
        if affiliate is None:
            continue
        for player in candidates:
            if player.current_team_code != target_team.code or player.player_id in selected_ids:
                continue
            moves.append(
                {
                    "player_name": player.player_name,
                    "player_id": player.player_id,
                    "from_team": player.current_team_abbrev,
                    "to_team": affiliate.abbrev,
                    "source": "Franchise Roster Builder",
                    "reason": (
                        f"Move outside the best-available NHL roster to {affiliate.abbrev}: "
                        f"{player.position}, {player.overall} OVR, "
                        f"${max(MINIMUM_GAME_AAV_MILLIONS, player.cap_hit_millions):.3f}M cap hit"
                    ),
                }
            )

    moves.sort(key=lambda row: (str(row["to_team"]), str(row["player_name"]), int(row["player_id"])))
    return {
        "moves": moves,
        "create_candidates": [],
        "franchise_summaries": summaries,
        "franchise_errors": errors,
        "puckpedia_errors": list(puckpedia_errors),
        "puckpedia_unmatched": puckpedia_unmatched,
        "puckpedia_seasons": puckpedia_seasons,
        "official_unmatched": official_unmatched,
    }
