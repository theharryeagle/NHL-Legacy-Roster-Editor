from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import unicodedata
from typing import Iterable

from .capwages import fetch_capwages_team_contracts
from .nhl_remote import TEAM_ABBREVIATIONS, fetch_league_rosters
from .team_tools import TeamRecord, league_name_for_team, normalize_org_abbrev, organization_for_abbrev, resolve_playable_nhl_team, resolve_team_abbrev

EXPANSION_TEAM_ABBREVS = {"SEA", "VGK"}
EXPANSION_DESTINATION_TEAMS = "teams"
EXPANSION_DESTINATION_FREE_AGENCY = "free_agency"
EXPANSION_DESTINATION_SEATTLE_FREE_AGENCY = "seattle_free_agency"
EXPANSION_DESTINATION_VEGAS_FREE_AGENCY = "vegas_free_agency"
FREE_AGENCY_TARGET = "FREE_AGENCY"
FIRST_NAME_EQUIVALENTS = {
    "alex": "alexander",
    "chris": "christopher",
    "jon": "jonathan",
    "matt": "matthew",
    "mike": "michael",
    "mitch": "mitchell",
    "nate": "nathan",
    "nick": "nicholas",
    "tony": "anthony",
    "vinnie": "vincent",
    "zach": "zachary",
}


def expansion_free_agent_teams(destination: str) -> set[str]:
    if destination == EXPANSION_DESTINATION_FREE_AGENCY:
        return {"SEA", "VGK"}
    if destination == EXPANSION_DESTINATION_SEATTLE_FREE_AGENCY:
        return {"SEA"}
    if destination == EXPANSION_DESTINATION_VEGAS_FREE_AGENCY:
        return {"VGK"}
    return set()


def can_auto_apply_move_on_save(row: dict[str, object]) -> bool:
    """Any resolved destination may run as a Save to Game side effect."""
    target = str(row.get("to_team") or "").strip()
    return bool(target)


@dataclass(slots=True)
class MoveProposal:
    player_name: str
    player_id: int
    from_team: str | None
    to_team: str
    source: str
    reason: str


@dataclass(slots=True)
class CreateCandidate:
    player_name: str
    team: str
    position: str | None
    drafted_by: str | None
    draft_year: int | None
    status: str | None
    source: str


def normalize_name(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    folded = folded.translate(
        str.maketrans({"ı": "y", "ø": "o", "ł": "l", "đ": "d", "ß": "ss", "æ": "ae", "œ": "oe"})
    )
    cleaned = " ".join(folded.split())
    if "," in cleaned:
        last, first = [part.strip() for part in cleaned.split(",", 1)]
        return " ".join((first, last)).strip()
    return cleaned.replace(",", "")


def equivalent_name_key(value: str) -> str:
    """Return a conservative key for common formal/short first names."""
    normalized = normalize_name(value)
    parts = normalized.split()
    if not parts:
        return normalized
    parts[0] = FIRST_NAME_EQUIVALENTS.get(parts[0], parts[0])
    return " ".join(parts)


def build_player_name_indexes(player_index) -> tuple[dict[str, list[object]], dict[str, list[object]]]:
    exact: dict[str, list[object]] = {}
    equivalent: dict[str, list[object]] = {}
    for player in player_index:
        exact.setdefault(normalize_name(player.full_name), []).append(player)
        equivalent.setdefault(equivalent_name_key(player.full_name), []).append(player)
    return exact, equivalent


def find_player_name_matches(
    value: str,
    exact_index: dict[str, list[object]],
    equivalent_index: dict[str, list[object]],
) -> list[object]:
    """Prefer exact names and fail closed when a nickname match is ambiguous."""
    exact = exact_index.get(normalize_name(value), [])
    if exact:
        return list(exact)
    candidates = equivalent_index.get(equivalent_name_key(value), [])
    unique_player_ids = {
        int(getattr(player, "player_id", -1))
        for player in candidates
    }
    return list(candidates) if len(unique_player_ids) == 1 else []


def canonical_abbrev(value: str | None) -> str | None:
    return normalize_org_abbrev(value)


def player_organization_memberships(
    player,
    organization_links: dict[str, str] | None = None,
) -> set[str]:
    """Return every parent organization supported by the roster entry."""
    memberships: set[str] = set()
    for value in (
        getattr(player, "current_team_abbrev", None),
        getattr(player, "organization_abbrev", None),
    ):
        resolved = organization_for_abbrev(value, organization_links)
        if resolved:
            memberships.add(resolved)
    return memberships


def player_is_in_organization(
    player,
    organization: str | None,
    organization_links: dict[str, str] | None = None,
) -> bool:
    target = organization_for_abbrev(organization, organization_links) or canonical_abbrev(organization)
    return bool(target and target in player_organization_memberships(player, organization_links))


def move_is_already_satisfied(
    row: dict[str, object],
    player,
    organization_links: dict[str, str] | None = None,
) -> bool:
    """Return whether a queued destination already matches the live roster."""
    target = str(row.get("to_team") or "").strip()
    if not target:
        return False
    if str(row.get("source") or "") == "Franchise Roster Builder":
        current = str(getattr(player, "current_team_abbrev", None) or "").strip().upper()
        return current == target.upper()
    if target == FREE_AGENCY_TARGET:
        if bool(getattr(player, "is_hidden", False)):
            return False
        current = str(getattr(player, "current_team_abbrev", None) or "").strip().upper()
        league = str(getattr(player, "league_name", None) or "").strip().lower()
        return current in {"", "FA", "FREE_AGENT", "FREE_AGENCY"} and league == "free agents"
    return player_is_in_organization(player, target, organization_links)


def filter_redundant_organization_moves(
    queue: dict[str, list[dict[str, object]]],
    player_index,
    organization_links: dict[str, str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Remove moves for players already anywhere inside the target organization."""
    players_by_id = {
        int(player.player_id): player
        for player in player_index
    }
    players_by_name, players_by_equivalent_name = build_player_name_indexes(player_index)

    filtered_moves: list[dict[str, object]] = []
    for row in queue.get("moves", []):
        player_id = row.get("player_id")
        try:
            matched_player = players_by_id.get(int(player_id))
        except (TypeError, ValueError):
            matched_player = None
        matches = [matched_player] if matched_player is not None else find_player_name_matches(
            str(row.get("player_name") or ""),
            players_by_name,
            players_by_equivalent_name,
        )
        if any(move_is_already_satisfied(row, player, organization_links) for player in matches):
            continue
        filtered_moves.append(dict(row))

    filtered = dict(queue)
    filtered["moves"] = filtered_moves
    filtered.setdefault("create_candidates", list(queue.get("create_candidates", [])))
    return filtered


def _has_real_draft_info(row) -> bool:
    values = [row.drafted_by, row.draft_year]
    for value in values:
        if value is None:
            return False
        if str(value).strip() in {"", "?", "None", "Unknown"}:
            return False
    return True


def build_capwages_roster_update(
    player_index,
    *,
    team_slugs: dict[str, str],
    organization_links: dict[str, str] | None = None,
    expansion_destination: str = EXPANSION_DESTINATION_TEAMS,
    force_refresh: bool = False,
) -> dict[str, list[dict[str, object]]]:
    unique_teams: dict[str, str] = {}
    for abbrev, slug in team_slugs.items():
        canonical = canonical_abbrev(abbrev)
        if canonical and canonical not in unique_teams:
            unique_teams[canonical] = slug

    players_by_name, players_by_equivalent_name = build_player_name_indexes(player_index)
    move_proposals: list[MoveProposal] = []
    create_candidates: list[CreateCandidate] = []

    fetched_contracts: dict[str, dict[str, list[object]]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_team = {}
        for team_abbrev, team_slug in sorted(unique_teams.items()):
            future = (
                executor.submit(fetch_capwages_team_contracts, team_slug, force_refresh=True)
                if force_refresh
                else executor.submit(fetch_capwages_team_contracts, team_slug)
            )
            future_to_team[future] = team_abbrev
        for future in as_completed(future_to_team):
            team_abbrev = future_to_team[future]
            try:
                fetched_contracts[team_abbrev] = future.result()
            except Exception:
                continue

    for team_abbrev in sorted(fetched_contracts):
        contracts = fetched_contracts[team_abbrev]
        is_expansion_team = team_abbrev in EXPANSION_TEAM_ABBREVS
        target_team_abbrev = (
            FREE_AGENCY_TARGET
            if is_expansion_team and team_abbrev in expansion_free_agent_teams(expansion_destination)
            else team_abbrev
        )
        for row in contracts.get("signed", []):
            roster_matches = find_player_name_matches(
                row.name,
                players_by_name,
                players_by_equivalent_name,
            )
            # NHLViewNG treats players without a linked team instance as Hidden.
            # They are intentionally unavailable in-game and must not be moved
            # merely because a cap site lists their contract on an NHL club.
            if roster_matches and all(bool(getattr(player, "is_hidden", False)) for player in roster_matches):
                continue
            if not roster_matches:
                if not _has_real_draft_info(row):
                    continue
                create_candidates.append(
                    CreateCandidate(
                        player_name=normalize_name(row.name).title(),
                        team=target_team_abbrev,
                        position=row.position,
                        drafted_by=row.drafted_by,
                        draft_year=row.draft_year,
                        status=row.status,
                        source="CapWages",
                    )
                )
                continue
            eligible_matches = [
                player
                for player in roster_matches
                if not bool(getattr(player, "is_hidden", False))
            ]
            if not eligible_matches:
                continue
            organization_matches = [
                player
                for player in eligible_matches
                if player_is_in_organization(player, team_abbrev, organization_links)
            ]
            if target_team_abbrev != FREE_AGENCY_TARGET and organization_matches:
                # CapWages reports NHL contract ownership, not the player's
                # active NHL/AHL/prospect assignment. Preserve their current
                # placement when any instance already belongs to this parent.
                continue
            lookup = organization_matches[0] if organization_matches else None
            if lookup is None:
                lookup = sorted(
                    eligible_matches,
                    key=lambda player: (
                        0 if player.league_name in {"NHL", "AHL", "Organization", "Prospects"} else 1,
                        player.full_name,
                    ),
                )[0]
            if lookup.league_name in {"CHL / Juniors", "Prospects"} and row.draft_year is not None:
                continue
            should_release_to_free_agency = target_team_abbrev == FREE_AGENCY_TARGET and bool(lookup.current_team_abbrev)
            should_move_to_team = (
                target_team_abbrev != FREE_AGENCY_TARGET
                and not player_is_in_organization(lookup, team_abbrev, organization_links)
            )
            if should_release_to_free_agency or should_move_to_team:
                move_proposals.append(
                    MoveProposal(
                        player_name=lookup.full_name,
                        player_id=int(lookup.player_id),
                        from_team=lookup.current_team_abbrev,
                        to_team=target_team_abbrev,
                        source="CapWages",
                        reason=(
                            f"Expansion mode releases {lookup.full_name} from {team_abbrev} to free agency."
                            if target_team_abbrev == FREE_AGENCY_TARGET
                            else f"CapWages lists {lookup.full_name} on {team_abbrev}."
                        ),
                    )
                )
    dedup_moves: dict[tuple[int, str], MoveProposal] = {}
    for proposal in move_proposals:
        dedup_moves[(proposal.player_id, proposal.to_team)] = proposal

    dedup_creates: dict[tuple[str, str], CreateCandidate] = {}
    for candidate in create_candidates:
        dedup_creates[(candidate.player_name, candidate.team)] = candidate

    return {
        "moves": [asdict(item) for item in sorted(dedup_moves.values(), key=lambda item: (item.to_team, item.player_name))],
        "create_candidates": [
            asdict(item)
            for item in sorted(dedup_creates.values(), key=lambda item: (item.team, item.player_name))
        ],
    }


def build_official_nhl_roster_update(
    player_index,
    *,
    teams: Iterable[TeamRecord] = (),
    organization_links: dict[str, str] | None = None,
    expansion_destination: str = EXPANSION_DESTINATION_TEAMS,
    free_agent_organizations: set[str] | None = None,
    protected_player_ids: set[int] | None = None,
    force_refresh: bool = False,
    official_rows=None,
) -> dict[str, list[dict[str, object]]]:
    """Build exact NHL-roster proposals from the league's current rosters.

    Unlike contract/depth-chart sources, the NHL endpoint represents active
    NHL placement. A player found on an affiliate therefore still receives a
    promotion proposal. Missing players are reported conservatively and are
    never auto-created from a name-only match.
    """
    players_by_name, players_by_equivalent_name = build_player_name_indexes(player_index)
    player_index = tuple(player_index)
    teams = tuple(teams)
    protected_player_ids = {int(value) for value in (protected_player_ids or set())}
    if free_agent_organizations is None:
        free_agent_organizations = expansion_free_agent_teams(expansion_destination)
    else:
        free_agent_organizations = {value.upper() for value in free_agent_organizations}
    proposals: dict[int, MoveProposal] = {}
    unmatched: list[dict[str, object]] = []
    official_target_by_player_id: dict[int, str] = {}
    protected_ambiguous_ids: set[int] = set()
    if official_rows is None:
        official_rows = fetch_league_rosters(TEAM_ABBREVIATIONS, use_cache=not force_refresh)
    for row in official_rows:
        matches = find_player_name_matches(
            row.full_name,
            players_by_name,
            players_by_equivalent_name,
        )
        eligible = [player for player in matches if not bool(getattr(player, "is_hidden", False))]
        if len({int(getattr(player, "player_id", -1)) for player in eligible}) != 1:
            protected_ambiguous_ids.update(int(getattr(player, "player_id", -1)) for player in eligible)
            unmatched.append(
                {
                    "player_name": row.full_name,
                    "team": row.team_abbrev,
                    "reason": "missing or ambiguous roster identity",
                    "source": "NHL.com Active Roster",
                }
            )
            continue
        player = eligible[0]
        official_target_by_player_id[int(player.player_id)] = row.team_abbrev
        target = (
            FREE_AGENCY_TARGET
            if row.team_abbrev in free_agent_organizations
            else (
                resolve_playable_nhl_team(row.team_abbrev, list(teams), organization_links).abbrev
                if teams and resolve_playable_nhl_team(row.team_abbrev, list(teams), organization_links) is not None
                else row.team_abbrev
            )
        )
        current = str(getattr(player, "current_team_abbrev", None) or "").upper()
        already_exact = (
            target == FREE_AGENCY_TARGET
            and str(getattr(player, "league_name", "")).lower() == "free agents"
        ) or current == target.upper()
        if already_exact:
            continue
        proposals[int(player.player_id)] = MoveProposal(
            player_name=player.full_name,
            player_id=int(player.player_id),
            from_team=getattr(player, "current_team_abbrev", None),
            to_team=target,
            source="NHL.com Active Roster",
            reason=(
                f"Official NHL active roster lists {row.full_name} on {row.team_abbrev}."
                if target != FREE_AGENCY_TARGET
                else f"Expansion policy releases official {row.team_abbrev} roster players to free agency."
            ),
        )

    affiliate_by_organization: dict[str, TeamRecord] = {}
    for team in sorted(
        teams,
        key=lambda item: (
            0 if league_name_for_team(item) == "AHL" else 1,
            item.code,
        ),
    ):
        league = league_name_for_team(team)
        if league not in {"AHL", "Organization"}:
            continue
        organization = organization_for_abbrev(team.abbrev, organization_links)
        if organization:
            affiliate_by_organization.setdefault(organization, team)

    # Apply the configured expansion release to the whole organization, not
    # only to names exposed by the NHL endpoint.
    for player in player_index:
        player_id = int(getattr(player, "player_id", -1))
        organization = organization_for_abbrev(
            getattr(player, "organization_abbrev", None),
            organization_links,
        )
        if (
            organization in free_agent_organizations
            and not bool(getattr(player, "is_hidden", False))
        ):
            if str(getattr(player, "league_name", "")).lower() != "free agents":
                proposals[player_id] = MoveProposal(
                    player_name=player.full_name,
                    player_id=player_id,
                    from_team=getattr(player, "current_team_abbrev", None),
                    to_team=FREE_AGENCY_TARGET,
                    source="NHL.com Active Roster",
                    reason=f"Expansion policy releases the {organization} organization to free agency.",
                )

    def physical_abbrev(value: str | None) -> str | None:
        if not value or value == FREE_AGENCY_TARGET:
            return value
        resolved = resolve_team_abbrev(value, list(teams), organization_links)
        return value.upper() if resolved is None else resolved.abbrev.upper()

    # Promotions must remain within the game's native 40-slot team blocks.
    # Demote only the minimum number of stale NHL occupants needed to make the
    # complete proposal batch safe; broader depth-chart decisions stay manual.
    projected_counts: dict[str, int] = {}
    for player in player_index:
        current = str(getattr(player, "current_team_abbrev", None) or "").upper()
        if current:
            projected_counts[current] = projected_counts.get(current, 0) + 1
    for proposal in proposals.values():
        source = str(proposal.from_team or "").upper()
        destination = physical_abbrev(proposal.to_team)
        if source:
            projected_counts[source] = projected_counts.get(source, 0) - 1
        if destination and destination != FREE_AGENCY_TARGET:
            projected_counts[destination] = projected_counts.get(destination, 0) + 1

    for official_abbrev in sorted(set(official_target_by_player_id.values())):
        target_abbrev = physical_abbrev(official_abbrev)
        if not target_abbrev:
            continue
        overflow = projected_counts.get(target_abbrev, 0) - 40
        if overflow <= 0:
            continue
        organization = organization_for_abbrev(official_abbrev, organization_links)
        affiliate = affiliate_by_organization.get(organization or "")
        if affiliate is None:
            unmatched.append(
                {
                    "player_name": "Team capacity",
                    "team": official_abbrev,
                    "reason": f"projected {projected_counts[target_abbrev]} players and no safe affiliate mapping",
                    "source": "NHL.com Active Roster",
                }
            )
            continue
        candidates = sorted(
            (
                player
                for player in player_index
                if str(getattr(player, "current_team_abbrev", None) or "").upper() == target_abbrev
                and int(getattr(player, "player_id", -1)) not in proposals
                and int(getattr(player, "player_id", -1)) not in protected_ambiguous_ids
                and int(getattr(player, "player_id", -1)) not in protected_player_ids
                and official_target_by_player_id.get(int(getattr(player, "player_id", -1))) != official_abbrev
            ),
            key=lambda player: (int(getattr(player, "player_id", -1)), player.full_name),
            reverse=True,
        )
        for player in candidates[:overflow]:
            player_id = int(player.player_id)
            proposals[player_id] = MoveProposal(
                player_name=player.full_name,
                player_id=player_id,
                from_team=target_abbrev,
                to_team=affiliate.abbrev,
                source="NHL.com Active Roster",
                reason=(
                    f"Capacity-safe demotion: not on the official {official_abbrev} active roster; "
                    f"retain in the organization on {affiliate.abbrev}."
                ),
            )
            projected_counts[target_abbrev] -= 1
            projected_counts[affiliate.abbrev.upper()] = projected_counts.get(affiliate.abbrev.upper(), 0) + 1
    return {
        "moves": [
            asdict(item)
            for item in sorted(proposals.values(), key=lambda item: (item.to_team, item.player_name))
        ],
        "create_candidates": [],
        "unmatched_official_players": sorted(
            unmatched,
            key=lambda item: (str(item["team"]), str(item["player_name"])),
        ),
    }


def build_puckpedia_assignment_update(
    player_index,
    charts,
    *,
    teams: Iterable[TeamRecord],
    organization_links: dict[str, str] | None = None,
    protected_nhl_names: set[str] | None = None,
    free_agent_organizations: set[str] | None = None,
) -> dict[str, list[dict[str, object]]]:
    """Translate PuckPedia depth sections into conservative assignments.

    NHL.com-active names are protected from a PuckPedia demotion. Minor
    leaguers go to the AHL affiliate; reserve/prospect/loaned players go to
    the organization's system team when available. Matching is exact and
    ambiguous identities are review items rather than guesses.
    """
    teams = tuple(teams)
    protected_nhl_names = {normalize_name(value) for value in (protected_nhl_names or set())}
    free_agent_organizations = {value.upper() for value in (free_agent_organizations or set())}
    exact, equivalent = build_player_name_indexes(player_index)
    player_index = tuple(player_index)
    ahl_by_org: dict[str, TeamRecord] = {}
    system_by_org: dict[str, TeamRecord] = {}
    for team in sorted(teams, key=lambda item: item.code):
        organization = organization_for_abbrev(team.abbrev, organization_links)
        if not organization:
            continue
        if league_name_for_team(team) == "AHL":
            ahl_by_org.setdefault(organization, team)
        elif league_name_for_team(team) == "Organization":
            system_by_org.setdefault(organization, team)

    proposals: dict[int, MoveProposal] = {}
    unmatched: list[dict[str, object]] = []
    for chart in charts:
        chart_abbrev = str(getattr(chart, "team_abbrev", "") or "").upper()
        organization = organization_for_abbrev(chart_abbrev, organization_links)
        if not organization:
            continue
        nhl_team = resolve_playable_nhl_team(chart_abbrev, list(teams), organization_links)
        for row in getattr(chart, "players", ()):
            name = str(getattr(row, "full_name", "") or "")
            status = str(getattr(row, "status", "") or "")
            if status != "nhl" and normalize_name(name) in protected_nhl_names:
                continue
            matches = [player for player in find_player_name_matches(name, exact, equivalent) if not player.is_hidden]
            if len({int(player.player_id) for player in matches}) != 1:
                unmatched.append({
                    "player_name": name,
                    "team": chart_abbrev,
                    "section": getattr(row, "section", ""),
                    "reason": "missing or ambiguous roster identity",
                    "source": "PuckPedia Depth Charts",
                })
                continue
            player = matches[0]
            if organization in free_agent_organizations:
                target = FREE_AGENCY_TARGET
            elif status == "nhl":
                target = None if nhl_team is None else nhl_team.abbrev
            elif status == "minors":
                target = None if organization not in ahl_by_org else ahl_by_org[organization].abbrev
            else:
                depth_team = system_by_org.get(organization) or ahl_by_org.get(organization)
                target = None if depth_team is None else depth_team.abbrev
            if target is None:
                unmatched.append({
                    "player_name": name,
                    "team": chart_abbrev,
                    "section": getattr(row, "section", ""),
                    "reason": "no playable destination for depth-chart section",
                    "source": "PuckPedia Depth Charts",
                })
                continue
            current = str(player.current_team_abbrev or "").upper()
            if current == target.upper() or (
                target == FREE_AGENCY_TARGET and str(player.league_name).lower() == "free agents"
            ):
                continue
            proposals[int(player.player_id)] = MoveProposal(
                player_name=player.full_name,
                player_id=int(player.player_id),
                from_team=player.current_team_abbrev,
                to_team=target,
                source="PuckPedia Depth Charts",
                reason=f"PuckPedia lists {name} in {getattr(row, 'section', status)} for {chart_abbrev}.",
            )
    return {
        "moves": [asdict(item) for item in sorted(proposals.values(), key=lambda item: (item.to_team, item.player_name))],
        "create_candidates": [],
        "unmatched_puckpedia_players": sorted(unmatched, key=lambda item: (str(item["team"]), str(item["player_name"]))),
    }
