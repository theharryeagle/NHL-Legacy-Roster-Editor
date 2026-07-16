from __future__ import annotations

from dataclasses import dataclass


# Player type still controls how an upgrade plan distributes points. NHL
# Legacy's displayed overall, however, is based on position rather than type.
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


# Reverse-engineered NHL 14/Legacy formulas. The forward formula's integer
# weights total 65. It reproduces the large influence of awareness that is
# visible in-game and is substantially closer than the old archetype curves.
SKATER_OVERALL_WEIGHTS: dict[str, float] = {
    "speed": 3.0,
    "body_checking": 2.0,
    "endurance": 1.0,
    "puck_control": 2.0,
    "passing": 5.0,
    "slap_shot_power": 2.0,
    "slap_shot_accuracy": 2.0,
    "wrist_shot_power": 3.0,
    "wrist_shot_accuracy": 4.0,
    "agility": 3.0,
    "strength": 3.0,
    "acceleration": 3.0,
    "balance": 3.0,
    "faceoffs": 1.0,
    "durability": 1.0,
    "deking": 1.0,
    "aggressiveness": 1.0,
    "poise": 2.0,
    "hand_eye": 1.0,
    "shot_blocking": 1.0,
    "offensive_awareness": 7.0,
    "defensive_awareness": 9.0,
    "discipline": 1.0,
    "fighting_skill": 1.0,
    "stick_checking": 3.0,
}

FORWARD_OVERALL_SCALE = 1.9165
FORWARD_OVERALL_INTERCEPT = -78.725

DEFENSE_OVERALL_COEFFICIENTS: dict[str, float] = {
    "speed": 0.023699,
    "body_checking": 0.084108,
    "endurance": 0.010734,
    "puck_control": -0.029,
    "passing": 0.058153,
    "slap_shot_power": 0.071821,
    "slap_shot_accuracy": 0.073114,
    "wrist_shot_power": 0.014244,
    "wrist_shot_accuracy": -0.00035,
    "agility": 0.089079,
    "strength": 0.243921,
    "acceleration": 0.048991,
    "balance": -0.01611,
    "faceoffs": 0.023246,
    "durability": -0.00716,
    "deking": 0.037535,
    "aggressiveness": -0.00211,
    "poise": 0.086034,
    "hand_eye": -0.02652,
    "shot_blocking": 0.157127,
    "offensive_awareness": 0.102197,
    "defensive_awareness": 0.557359,
    "discipline": 0.008544,
    "fighting_skill": -0.0169,
    "stick_checking": 0.270786,
}

DEFENSE_OVERALL_INTERCEPT = -74.3907

DEFENSE_ARCHETYPES = {
    "offensive_defenseman",
    "two_way_defenseman",
    "defensive_defenseman",
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


def _is_defenseman(archetype: str, position: str | None) -> bool:
    if position:
        return str(position).strip().upper() == "D"
    return archetype in DEFENSE_ARCHETYPES


def calculate_base_overall(ratings: dict[str, int], archetype: str = "") -> float:
    """Return the 65-point EA weighted average used by the forward formula."""
    total_weight = sum(SKATER_OVERALL_WEIGHTS.values())
    weighted_sum = sum(
        float(ratings.get(stat_name, 36)) * weight
        for stat_name, weight in SKATER_OVERALL_WEIGHTS.items()
    )
    return weighted_sum / total_weight


def calibrate_overall(base_overall: float, archetype: str = "") -> int:
    """Convert a 65-point weighted average to NHL Legacy's overall scale."""
    value = (float(base_overall) * FORWARD_OVERALL_SCALE) + FORWARD_OVERALL_INTERCEPT
    return max(36, min(99, int(value + 0.5)))


def calculate_unrounded_overall(
    ratings: dict[str, int],
    archetype: str = "two_way_forward",
    *,
    position: str | None = None,
) -> float:
    if _is_defenseman(archetype, position):
        return DEFENSE_OVERALL_INTERCEPT + sum(
            float(ratings.get(stat_name, 36)) * coefficient
            for stat_name, coefficient in DEFENSE_OVERALL_COEFFICIENTS.items()
        )
    return (calculate_base_overall(ratings) * FORWARD_OVERALL_SCALE) + FORWARD_OVERALL_INTERCEPT


def calculate_weighted_overall(
    ratings: dict[str, int],
    archetype: str,
    *,
    position: str | None = None,
) -> int:
    value = calculate_unrounded_overall(ratings, archetype, position=position)
    return max(36, min(99, int(value + 0.5)))


def _overall_impacts(archetype: str, position: str | None) -> dict[str, float]:
    if _is_defenseman(archetype, position):
        return {
            name: max(0.0, coefficient)
            for name, coefficient in DEFENSE_OVERALL_COEFFICIENTS.items()
        }
    factor = FORWARD_OVERALL_SCALE / sum(SKATER_OVERALL_WEIGHTS.values())
    return {name: weight * factor for name, weight in SKATER_OVERALL_WEIGHTS.items()}


def weighted_delta_to_target(
    ratings: dict[str, int],
    archetype: str,
    target_overall: int,
    *,
    position: str | None = None,
) -> float:
    current = calculate_unrounded_overall(ratings, archetype, position=position)
    return max(0.0, float(target_overall) - current)


def _upgrade_order(
    ratings: dict[str, int],
    archetype: str,
    position: str | None,
) -> list[tuple[str, float]]:
    impacts = _overall_impacts(archetype, position)
    role = ARCHETYPE_WEIGHTS.get(archetype, {})
    max_role = max(role.values(), default=1.0)
    ranked: list[tuple[str, float]] = []
    for name, impact in impacts.items():
        if impact <= 0 or name not in ratings:
            continue
        role_bonus = 1.0 + (0.45 * (role.get(name, 0.0) / max_role))
        ranked.append((name, impact * role_bonus))
    ranked.sort(key=lambda item: (-item[1], ratings.get(item[0], 36), item[0]))
    return ranked


def plan_rating_upgrade(
    ratings: dict[str, int],
    archetype: str,
    target_overall: int,
    max_stat: int = 99,
    *,
    position: str | None = None,
) -> RatingPlan:
    if archetype not in ARCHETYPE_WEIGHTS:
        raise ValueError(f"Unknown archetype: {archetype}")

    suggested = {name: int(value) for name, value in ratings.items()}
    current_overall = calculate_weighted_overall(suggested, archetype, position=position)
    start_unrounded = calculate_unrounded_overall(suggested, archetype, position=position)
    ranked_stats = _upgrade_order(suggested, archetype, position)
    points_used = 0

    while calculate_weighted_overall(suggested, archetype, position=position) < target_overall:
        changed = False
        for stat_name, _priority in ranked_stats:
            if suggested.get(stat_name, 36) >= max_stat:
                continue
            suggested[stat_name] = suggested.get(stat_name, 36) + 1
            points_used += 1
            changed = True
            if calculate_weighted_overall(suggested, archetype, position=position) >= target_overall:
                break
        if not changed:
            break

    end_unrounded = calculate_unrounded_overall(suggested, archetype, position=position)
    remaining = max(0.0, float(target_overall) - end_unrounded)
    return RatingPlan(
        archetype=archetype,
        current_overall=current_overall,
        target_overall=target_overall,
        points_used=points_used,
        weighted_delta_used=max(0.0, end_unrounded - start_unrounded),
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
    position: str | None = None,
) -> RatingPlan:
    """Scale a comparison blend until it fits the requested EA overall cap."""
    if archetype not in ARCHETYPE_WEIGHTS:
        raise ValueError(f"Unknown archetype: {archetype}")

    current_overall = calculate_weighted_overall(ratings, archetype, position=position)
    if current_overall <= target_overall:
        return plan_rating_upgrade(
            ratings,
            archetype,
            target_overall,
            max_stat=max_stat,
            position=position,
        )

    suggested = {
        key: max(min_stat, min(max_stat, int(value)))
        for key, value in ratings.items()
    }
    start_unrounded = calculate_unrounded_overall(suggested, archetype, position=position)
    impacts = _overall_impacts(archetype, position)
    eligible = [name for name, impact in impacts.items() if impact > 0 and name in suggested]
    points_used = 0

    # Lower all gameplay-relevant ratings in rounds so a comparison blend keeps
    # its shape instead of dumping the entire reduction into one attribute.
    while calculate_weighted_overall(suggested, archetype, position=position) > target_overall:
        changed = False
        eligible.sort(key=lambda name: (-suggested.get(name, min_stat), name))
        for stat_name in eligible:
            if suggested.get(stat_name, min_stat) <= min_stat:
                continue
            suggested[stat_name] -= 1
            points_used -= 1
            changed = True
            if calculate_weighted_overall(suggested, archetype, position=position) <= target_overall:
                break
        if not changed:
            break

    end_unrounded = calculate_unrounded_overall(suggested, archetype, position=position)
    return RatingPlan(
        archetype=archetype,
        current_overall=current_overall,
        target_overall=target_overall,
        points_used=points_used,
        weighted_delta_used=end_unrounded - start_unrounded,
        weighted_delta_remaining=max(0.0, end_unrounded - float(target_overall)),
        suggested_ratings=suggested,
    )
