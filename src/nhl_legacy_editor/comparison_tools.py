from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .player_tools import get_player_snapshot
from .rating_models import calculate_weighted_overall, plan_rating_upgrade


@dataclass(slots=True)
class ComparisonBlend:
    target_name: str
    archetype: str
    target_overall: int
    source_players: list[str]
    blended_ratings: dict[str, int]
    upgraded_ratings: dict[str, int]
    estimated_overall_before_cap: int
    estimated_overall_after_cap: int


def build_comparison_blend(
    db_path: Path,
    target_name: str,
    source_names: list[str],
    archetype: str,
    target_overall: int,
) -> ComparisonBlend:
    source_rows = []
    for full_name in source_names:
        first, last = full_name.split(" ", 1)
        snapshot = get_player_snapshot(db_path, first, last)
        if snapshot is None or snapshot.ratings_row is None:
            raise RuntimeError(f"Ratings row not found for comparison player: {full_name}")
        source_rows.append(snapshot.ratings_row)

    numeric_fields = sorted(
        {
            key
            for row in source_rows
            for key, value in row.items()
            if key != "zIBw" and isinstance(value, int)
        }
    )
    blended = {
        field: round(sum(int(row.get(field, 0)) for row in source_rows) / len(source_rows))
        for field in numeric_fields
    }

    semantic_seed = {
        "offensive_awareness": blended.get(numeric_fields[0], 75) if numeric_fields else 75,
        "passing": blended.get(numeric_fields[1], 75) if len(numeric_fields) > 1 else 75,
        "puck_control": blended.get(numeric_fields[2], 75) if len(numeric_fields) > 2 else 75,
        "deking": blended.get(numeric_fields[3], 75) if len(numeric_fields) > 3 else 75,
        "hand_eye": blended.get(numeric_fields[4], 75) if len(numeric_fields) > 4 else 75,
        "speed": blended.get(numeric_fields[5], 75) if len(numeric_fields) > 5 else 75,
        "acceleration": blended.get(numeric_fields[6], 75) if len(numeric_fields) > 6 else 75,
        "defensive_awareness": blended.get(numeric_fields[7], 75) if len(numeric_fields) > 7 else 75,
    }
    before = calculate_weighted_overall(semantic_seed, archetype)
    plan = plan_rating_upgrade(semantic_seed, archetype, target_overall)
    after = calculate_weighted_overall(plan.suggested_ratings, archetype)
    return ComparisonBlend(
        target_name=target_name,
        archetype=archetype,
        target_overall=target_overall,
        source_players=source_names,
        blended_ratings=semantic_seed,
        upgraded_ratings=plan.suggested_ratings,
        estimated_overall_before_cap=before,
        estimated_overall_after_cap=after,
    )
