from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import re

from .capwages import fetch_capwages_team_contracts
from .contract_models import scale_contract_by_cap_percentage
from .roster_sync import build_player_name_indexes, canonical_abbrev, find_player_name_matches
from .team_tools import organization_for_abbrev


@dataclass(slots=True)
class ContractProposal:
    player_name: str
    player_id: int
    team: str
    current_team: str | None
    real_aav_millions: float
    game_aav_millions: float
    clause: str | None
    expiry: str | None
    source: str
    term_years: int | None


def _money_to_millions(value: str | None) -> float | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9.]", "", value)
    if not digits:
        return None
    return float(digits) / 1_000_000.0


def build_contract_update_queue(
    player_index,
    *,
    team_slugs: dict[str, str],
    real_cap: float,
    game_cap: float,
    force_refresh: bool = False,
) -> list[dict[str, object]]:
    unique_teams: dict[str, str] = {}
    for abbrev, slug in team_slugs.items():
        canonical = canonical_abbrev(abbrev)
        if canonical and canonical not in unique_teams:
            unique_teams[canonical] = slug
    players_by_name, players_by_equivalent_name = build_player_name_indexes(player_index)

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

    proposals_by_player_id: dict[int, tuple[int, ContractProposal]] = {}
    for team_abbrev in sorted(fetched_contracts):
        contracts = fetched_contracts[team_abbrev]
        for row in contracts.get("signed", []):
            real_aav = _money_to_millions(row.aav or row.cap_hit)
            if real_aav is None:
                continue
            matches = [
                player
                for player in find_player_name_matches(
                    row.name,
                    players_by_name,
                    players_by_equivalent_name,
                )
                if not bool(getattr(player, "is_hidden", False))
            ]
            if not matches:
                continue
            organization_matches = [
                player
                for player in matches
                if canonical_abbrev(
                    organization_for_abbrev(getattr(player, "current_team_abbrev", None))
                    or getattr(player, "organization_abbrev", None)
                )
                == team_abbrev
            ]
            lookup = organization_matches[0] if organization_matches else matches[0]
            scaled = scale_contract_by_cap_percentage(
                lookup.full_name,
                real_aav,
                game_cap,
                real_cap,
            )
            proposal = ContractProposal(
                player_name=lookup.full_name,
                player_id=int(lookup.player_id),
                team=team_abbrev,
                current_team=lookup.current_team_abbrev,
                real_aav_millions=real_aav,
                game_aav_millions=scaled.scaled_aav_millions,
                clause=row.clause,
                expiry=row.expiry,
                source="CapWages",
                term_years=row.term_years,
            )
            current_team = canonical_abbrev(
                organization_for_abbrev(getattr(lookup, "current_team_abbrev", None))
                or getattr(lookup, "current_team_abbrev", None)
            )
            match_score = (2 if organization_matches else 0) + (1 if current_team == team_abbrev else 0)
            previous = proposals_by_player_id.get(proposal.player_id)
            if previous is None or match_score > previous[0]:
                proposals_by_player_id[proposal.player_id] = (match_score, proposal)
            elif match_score == previous[0]:
                previous_term = previous[1].term_years or 0
                if (proposal.term_years or 0) > previous_term:
                    proposals_by_player_id[proposal.player_id] = (match_score, proposal)
    proposals = [proposal for _score, proposal in proposals_by_player_id.values()]
    return [
        asdict(item)
        for item in sorted(proposals, key=lambda item: (-item.game_aav_millions, item.player_name))
    ]
