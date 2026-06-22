from __future__ import annotations

from dataclasses import dataclass


DEFAULT_REAL_CAP_MILLIONS = 104.0


@dataclass(slots=True)
class ContractScaleResult:
    player_name: str
    real_cap_millions: float
    game_cap_millions: float
    real_aav_millions: float
    cap_hit_percent: float
    scaled_aav_millions: float
    scaled_aav_dollars: int


def scale_contract_by_cap_percentage(
    player_name: str,
    real_aav_millions: float,
    game_cap_millions: float,
    real_cap_millions: float = DEFAULT_REAL_CAP_MILLIONS,
) -> ContractScaleResult:
    if real_cap_millions <= 0 or game_cap_millions <= 0 or real_aav_millions < 0:
        raise ValueError("Cap and AAV values must be positive, and AAV cannot be negative.")

    cap_hit_percent = real_aav_millions / real_cap_millions
    scaled_aav_millions = cap_hit_percent * game_cap_millions
    return ContractScaleResult(
        player_name=player_name,
        real_cap_millions=real_cap_millions,
        game_cap_millions=game_cap_millions,
        real_aav_millions=real_aav_millions,
        cap_hit_percent=cap_hit_percent,
        scaled_aav_millions=scaled_aav_millions,
        scaled_aav_dollars=round(scaled_aav_millions * 1_000_000),
    )


def scale_contract_by_percent(
    player_name: str,
    cap_hit_percent: float,
    game_cap_millions: float,
    real_cap_millions: float = DEFAULT_REAL_CAP_MILLIONS,
) -> ContractScaleResult:
    if cap_hit_percent < 0:
        raise ValueError("Cap hit percent cannot be negative.")
    real_aav_millions = cap_hit_percent * real_cap_millions
    return scale_contract_by_cap_percentage(
        player_name=player_name,
        real_aav_millions=real_aav_millions,
        game_cap_millions=game_cap_millions,
        real_cap_millions=real_cap_millions,
    )
