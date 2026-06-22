from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .capwages import fetch_capwages_team_contracts
from .contract_models import scale_contract_by_cap_percentage
from .roster_sync import canonical_abbrev, normalize_name


@dataclass(slots=True)
class ContractProposal:
    player_name: str
    team: str
    current_team: str | None
    real_aav_millions: float
    game_aav_millions: float
    clause: str | None
    expiry: str | None
    source: str


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
) -> list[dict[str, object]]:
    unique_teams: dict[str, str] = {}
    for abbrev, slug in team_slugs.items():
        canonical = canonical_abbrev(abbrev)
        if canonical and canonical not in unique_teams:
            unique_teams[canonical] = slug
    players_by_name = {normalize_name(player.full_name): player for player in player_index}

    proposals: list[ContractProposal] = []
    for team_abbrev, team_slug in sorted(unique_teams.items()):
        try:
            contracts = fetch_capwages_team_contracts(team_slug)
        except Exception:
            continue
        for row in contracts.get("signed", []):
            real_aav = _money_to_millions(row.aav or row.cap_hit)
            if real_aav is None:
                continue
            lookup = players_by_name.get(normalize_name(row.name))
            if lookup is None:
                continue
            scaled = scale_contract_by_cap_percentage(
                lookup.full_name,
                real_aav,
                game_cap,
                real_cap,
            )
            proposals.append(
                ContractProposal(
                    player_name=lookup.full_name,
                    team=team_abbrev,
                    current_team=lookup.current_team_abbrev,
                    real_aav_millions=real_aav,
                    game_aav_millions=scaled.scaled_aav_millions,
                    clause=row.clause,
                    expiry=row.expiry,
                    source="CapWages",
                )
            )
    return [
        asdict(item)
        for item in sorted(proposals, key=lambda item: (-item.game_aav_millions, item.player_name))
    ]
