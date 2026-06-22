from __future__ import annotations

from dataclasses import asdict, dataclass

from .capwages import fetch_capwages_team_contracts
from .team_tools import normalize_org_abbrev, organization_for_abbrev


@dataclass(slots=True)
class MoveProposal:
    player_name: str
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


def canonical_abbrev(value: str | None) -> str | None:
    return normalize_org_abbrev(value)


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
) -> dict[str, list[dict[str, object]]]:
    unique_teams: dict[str, str] = {}
    for abbrev, slug in team_slugs.items():
        canonical = canonical_abbrev(abbrev)
        if canonical and canonical not in unique_teams:
            unique_teams[canonical] = slug

    players_by_name: dict[str, list[object]] = {}
    for player in player_index:
        players_by_name.setdefault(normalize_name(player.full_name), []).append(player)
    move_proposals: list[MoveProposal] = []
    create_candidates: list[CreateCandidate] = []

    for team_abbrev, team_slug in sorted(unique_teams.items()):
        try:
            contracts = fetch_capwages_team_contracts(team_slug)
        except Exception:
            continue
        for row in contracts.get("signed", []):
            roster_matches = players_by_name.get(normalize_name(row.name), [])
            if not roster_matches:
                if not _has_real_draft_info(row):
                    continue
                create_candidates.append(
                    CreateCandidate(
                        player_name=normalize_name(row.name).title(),
                        team=team_abbrev,
                        position=row.position,
                        drafted_by=row.drafted_by,
                        draft_year=row.draft_year,
                        status=row.status,
                        source="CapWages",
                    )
                )
                continue
            lookup = next(
                (
                    player
                    for player in roster_matches
                    if (
                        canonical_abbrev(player.organization_abbrev)
                        or organization_for_abbrev(player.current_team_abbrev, organization_links)
                        or canonical_abbrev(player.current_team_abbrev)
                    )
                    == team_abbrev
                ),
                None,
            )
            if lookup is None:
                lookup = sorted(
                    roster_matches,
                    key=lambda player: (
                        0 if player.league_name in {"NHL", "AHL", "Organization", "Prospects"} else 1,
                        player.full_name,
                    ),
                )[0]
            current_org = (
                canonical_abbrev(lookup.organization_abbrev)
                or organization_for_abbrev(lookup.current_team_abbrev, organization_links)
                or canonical_abbrev(lookup.current_team_abbrev)
            )
            if lookup.league_name in {"CHL / Juniors", "Prospects"} and row.draft_year is not None:
                continue
            if current_org != team_abbrev:
                move_proposals.append(
                    MoveProposal(
                        player_name=lookup.full_name,
                        from_team=lookup.current_team_abbrev,
                        to_team=team_abbrev,
                        source="CapWages",
                        reason=f"CapWages lists {lookup.full_name} on {team_abbrev}.",
                    )
                )
    dedup_moves: dict[tuple[str, str], MoveProposal] = {}
    for proposal in move_proposals:
        dedup_moves[(proposal.player_name, proposal.to_team)] = proposal

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
