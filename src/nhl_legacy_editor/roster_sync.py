from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

from .capwages import fetch_capwages_team_contracts
from .team_tools import normalize_org_abbrev, organization_for_abbrev

EXPANSION_TEAM_ABBREVS = {"SEA", "VGK"}
EXPANSION_DESTINATION_TEAMS = "teams"
EXPANSION_DESTINATION_FREE_AGENCY = "free_agency"
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
    cleaned = " ".join(value.lower().split())
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
            if is_expansion_team and expansion_destination == EXPANSION_DESTINATION_FREE_AGENCY
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
