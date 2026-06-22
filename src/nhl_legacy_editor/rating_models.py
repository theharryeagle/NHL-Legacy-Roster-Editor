from __future__ import annotations

from dataclasses import dataclass


ARCHETYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "sniper": {
        "offensive_awareness": 1.6,
        "wrist_shot_accuracy": 1.5,
        "wrist_shot_power": 1.3,
        "slap_shot_accuracy": 1.2,
        "slap_shot_power": 1.1,
        "puck_control": 1.0,
        "deking": 0.9,
        "speed": 0.8,
        "defensive_awareness": 0.5,
    },
    "playmaker": {
        "offensive_awareness": 1.6,
        "passing": 1.5,
        "puck_control": 1.3,
        "deking": 1.2,
        "hand_eye": 1.0,
        "speed": 0.9,
        "acceleration": 0.9,
        "defensive_awareness": 0.6,
    },
    "power_forward": {
        "offensive_awareness": 1.3,
        "body_checking": 1.2,
        "strength": 1.2,
        "puck_control": 1.0,
        "wrist_shot_power": 1.0,
        "hand_eye": 0.9,
        "aggressiveness": 0.8,
        "defensive_awareness": 0.8,
    },
    "grinder": {
        "defensive_awareness": 1.3,
        "body_checking": 1.2,
        "stick_checking": 1.1,
        "strength": 1.0,
        "aggressiveness": 1.0,
        "endurance": 1.0,
        "speed": 0.8,
        "discipline": 0.8,
        "offensive_awareness": 0.7,
    },
    "enforcer": {
        "fighting_skill": 1.5,
        "aggressiveness": 1.4,
        "strength": 1.3,
        "body_checking": 1.3,
        "balance": 0.9,
        "durability": 0.9,
        "defensive_awareness": 0.6,
        "speed": 0.5,
    },
    "two_way_forward": {
        "offensive_awareness": 1.2,
        "defensive_awareness": 1.4,
        "stick_checking": 1.2,
        "faceoffs": 1.0,
        "passing": 1.0,
        "discipline": 0.8,
        "speed": 0.8,
    },
    "offensive_defenseman": {
        "offensive_awareness": 1.3,
        "passing": 1.2,
        "puck_control": 1.1,
        "slap_shot_power": 1.0,
        "slap_shot_accuracy": 1.0,
        "speed": 0.9,
        "defensive_awareness": 0.8,
        "stick_checking": 0.7,
    },
    "two_way_defenseman": {
        "defensive_awareness": 1.3,
        "offensive_awareness": 1.1,
        "stick_checking": 1.1,
        "passing": 1.0,
        "puck_control": 0.9,
        "body_checking": 0.9,
        "shot_blocking": 0.9,
        "slap_shot_power": 0.8,
        "speed": 0.8,
    },
    "defensive_defenseman": {
        "defensive_awareness": 1.5,
        "stick_checking": 1.3,
        "shot_blocking": 1.1,
        "body_checking": 1.1,
        "strength": 1.0,
        "discipline": 0.8,
        "offensive_awareness": 0.6,
        "speed": 0.6,
    },
}


@dataclass(slots=True)
class RatingPlan:
    archetype: str
    current_overall: int
    target_overall: int
    points_used: int
    weighted_delta_used: float
    weighted_delta_remaining: float
    suggested_ratings: dict[str, int]


def calculate_weighted_overall(ratings: dict[str, int], archetype: str) -> int:
    weights = ARCHETYPE_WEIGHTS[archetype]
    total_weight = sum(weights.values())
    weighted_sum = 0.0
    for stat_name, weight in weights.items():
        weighted_sum += ratings.get(stat_name, 0) * weight
    return round(weighted_sum / total_weight)


def plan_rating_upgrade(
    ratings: dict[str, int],
    archetype: str,
    target_overall: int,
    max_stat: int = 99,
) -> RatingPlan:
    if archetype not in ARCHETYPE_WEIGHTS:
        raise ValueError(f"Unknown archetype: {archetype}")

    weights = ARCHETYPE_WEIGHTS[archetype]
    suggested = dict(ratings)
    current_overall = calculate_weighted_overall(suggested, archetype)
    total_weight = sum(weights.values())
    required_weighted_delta = max(0.0, (target_overall - current_overall) * total_weight)

    points_used = 0
    weighted_delta_used = 0.0
    ranked_stats = sorted(weights.items(), key=lambda item: (-item[1], item[0]))

    while weighted_delta_used + 1e-9 < required_weighted_delta:
        changed = False
        for stat_name, weight in ranked_stats:
            current_value = suggested.get(stat_name, 0)
            if current_value >= max_stat:
                continue
            suggested[stat_name] = current_value + 1
            points_used += 1
            weighted_delta_used += weight
            changed = True
            if weighted_delta_used + 1e-9 >= required_weighted_delta:
                break
        if not changed:
            break

    remaining = max(0.0, required_weighted_delta - weighted_delta_used)
    return RatingPlan(
        archetype=archetype,
        current_overall=current_overall,
        target_overall=target_overall,
        points_used=points_used,
        weighted_delta_used=weighted_delta_used,
        weighted_delta_remaining=remaining,
        suggested_ratings=suggested,
    )


def fit_ratings_to_overall(
    ratings: dict[str, int],
    archetype: str,
    target_overall: int,
    *,
    min_stat: int = 36,
    max_stat: int = 99,
) -> RatingPlan:
    """Raise or shrink a comparison blend until it fits the requested cap."""
    if archetype not in ARCHETYPE_WEIGHTS:
        raise ValueError(f"Unknown archetype: {archetype}")

    current_overall = calculate_weighted_overall(ratings, archetype)
    if current_overall <= target_overall:
        return plan_rating_upgrade(ratings, archetype, target_overall, max_stat=max_stat)

    weights = ARCHETYPE_WEIGHTS[archetype]
    suggested = {key: max(min_stat, min(max_stat, int(value))) for key, value in ratings.items()}
    ranked_stats = sorted(weights.items(), key=lambda item: (item[1], item[0]))
    points_used = 0
    weighted_delta_used = 0.0
    while calculate_weighted_overall(suggested, archetype) > target_overall:
        changed = False
        for stat_name, weight in ranked_stats:
            current_value = suggested.get(stat_name, min_stat)
            if current_value <= min_stat:
                continue
            suggested[stat_name] = current_value - 1
            points_used -= 1
            weighted_delta_used -= weight
            changed = True
            if calculate_weighted_overall(suggested, archetype) <= target_overall:
                break
        if not changed:
            break

    return RatingPlan(
        archetype=archetype,
        current_overall=current_overall,
        target_overall=target_overall,
        points_used=points_used,
        weighted_delta_used=weighted_delta_used,
        weighted_delta_remaining=0.0,
        suggested_ratings=suggested,
    )
