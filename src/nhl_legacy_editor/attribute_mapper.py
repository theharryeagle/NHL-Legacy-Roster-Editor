from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .moneypuck_scraper import (
    MoneyPuckPercentileModel,
    faceoff_won_taken,
    games_played,
    number,
    rating_from_score,
)
from .rating_models import calculate_weighted_overall

GOALIE_OVERALL_COEFFICIENTS = {
    "Glove Side Low": 0.185152,
    "Glove Side High": 0.165627,
    "Stick Side High": 0.162782,
    "Stick Side Low": 0.157201,
    "Five Hole": 0.118122,
    "Speed": 0.056902,
    "Agility": 0.07652,
    "Poke Check": 0.012405,
    "Endurance": 0.081754,
    "Breakaway": 0.1286,
    "Rebound Control": 0.162675,
    "Shot Recovery": 0.161285,
    "Poise": 0.004859,
    "Passing": 0.047306,
    "Angles": 0.157808,
    "Puck Play Frequency": 0.003561,
    "Aggressiveness": 0.046093,
    "Durability": 0.053731,
    "Vision": 0.050163,
}

GOALIE_OVERALL_INTERCEPT = -73.8756

DISPLAY_TO_SEMANTIC = {
    "Speed": "speed",
    "Body Checking": "body_checking",
    "Endurance": "endurance",
    "Puck Control": "puck_control",
    "Passing": "passing",
    "Slap Shot Power": "slap_shot_power",
    "Slap Shot Accuracy": "slap_shot_accuracy",
    "Wrist Shot Power": "wrist_shot_power",
    "Wrist Shot Accuracy": "wrist_shot_accuracy",
    "Agility": "agility",
    "Strength": "strength",
    "Acceleration": "acceleration",
    "Balance": "balance",
    "Face-offs": "faceoffs",
    "Durability": "durability",
    "Deking": "deking",
    "Aggressiveness": "aggressiveness",
    "Poise": "poise",
    "Hand-Eye": "hand_eye",
    "Shot Blocking": "shot_blocking",
    "Off. Awareness": "offensive_awareness",
    "Def. Awareness": "defensive_awareness",
    "Discipline": "discipline",
    "Fighting Skill": "fighting_skill",
    "Stick Checking": "stick_checking",
}


def calculate_goalie_overall(display_values: dict[str, int], style: str = "Hybrid Goalie") -> int:
    if not display_values:
        return 0
    value = GOALIE_OVERALL_INTERCEPT + sum(
        float(display_values.get(label, 36)) * coefficient
        for label, coefficient in GOALIE_OVERALL_COEFFICIENTS.items()
    )
    return max(36, min(99, int(value + 0.5)))


def blend_season_recommendations(
    current: RecommendationSet,
    previous: RecommendationSet | None,
    *,
    current_weight: float,
    previous_source: str,
) -> RecommendationSet:
    if previous is None or previous.skipped_reason:
        return current
    if current.skipped_reason:
        result = RecommendationSet(
            suggestions=dict(previous.suggestions),
            notes={
                label: f"{note} | previous-season fallback"
                for label, note in previous.notes.items()
            },
            sources={
                label: f"{source} ({previous_source} fallback)"
                for label, source in previous.sources.items()
            },
            overall_baseline=previous.overall_baseline,
            overall_note=previous.overall_note,
        )
        return result
    result = RecommendationSet(
        suggestions=dict(current.suggestions),
        notes=dict(current.notes),
        sources=dict(current.sources),
        overall_baseline=current.overall_baseline,
        overall_note=current.overall_note,
    )
    prior_weight = 1.0 - current_weight
    for label, prior_value in previous.suggestions.items():
        if label not in result.suggestions:
            continue
        result.suggestions[label] = round(
            (result.suggestions[label] * current_weight) + (prior_value * prior_weight)
        )
        result.notes[label] = f"{result.notes.get(label, '')} | stabilized with {previous_source}".strip(" |")
        result.sources[label] = f"{result.sources.get(label, 'MoneyPuck')} + {previous_source}"
    if current.overall_baseline is not None and previous.overall_baseline is not None:
        result.overall_baseline = round(
            (current.overall_baseline * current_weight) + (previous.overall_baseline * prior_weight)
        )
        result.overall_note = f"{current.overall_note or ''} | stabilized with {previous_source}".strip(" |")
    return result


@dataclass(slots=True)
class RecommendationSet:
    suggestions: dict[str, int] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    skipped_reason: str | None = None
    overall_baseline: int | None = None
    overall_note: str | None = None

    def add(self, label: str, value: int, note: str, source: str = "MoneyPuck") -> None:
        self.suggestions[label] = int(max(0, min(99, value)))
        self.notes[label] = note
        self.sources[label] = source


def clone_recommendation_set(value: RecommendationSet) -> RecommendationSet:
    return RecommendationSet(
        suggestions=dict(value.suggestions),
        notes=dict(value.notes),
        sources=dict(value.sources),
        skipped_reason=value.skipped_reason,
        overall_baseline=value.overall_baseline,
        overall_note=value.overall_note,
    )


def nhl_role_overall_from_score(score: float) -> int:
    """Map a MoneyPuck quality percentile to the NHL Legacy role scale."""
    points = (
        (0.00, 72),
        (0.10, 74),
        (0.25, 77),
        (0.50, 81),
        (0.70, 85),
        (0.85, 89),
        (0.95, 93),
        (1.00, 96),
    )
    score = max(0.0, min(1.0, float(score)))
    for (left_score, left_rating), (right_score, right_rating) in zip(points, points[1:]):
        if left_score <= score <= right_score:
            ratio = (score - left_score) / (right_score - left_score)
            return int((left_rating + ((right_rating - left_rating) * ratio)) + 0.5)
    return points[-1][1]


FORWARD_ICE_TIME_ROLE_POINTS = (
    (0.0, 72),
    (6.0, 72),
    (8.0, 74),
    (10.0, 76),
    (12.0, 78),
    (14.0, 80),
    (16.0, 83),
    (18.0, 86),
    (20.0, 89),
    (22.0, 92),
    (25.0, 96),
)

DEFENSE_ICE_TIME_ROLE_POINTS = (
    (0.0, 72),
    (8.0, 72),
    (10.0, 73),
    (12.0, 75),
    (14.0, 78),
    (16.0, 81),
    (18.0, 84),
    (20.0, 87),
    (22.0, 90),
    (24.0, 93),
    (25.0, 96),
)

SKATER_ROLE_CEILING_OFFSETS = {
    # NHL Legacy's component ratings generally sit above its compressed
    # displayed OVR, so each role ceiling includes two calibration points.
    "Off. Awareness": 4,
    "Def. Awareness": 5,
    "Passing": 6,
    "Puck Control": 6,
    "Stick Checking": 5,
    "Poise": 6,
    "Deking": 7,
    "Wrist Shot Accuracy": 8,
    "Slap Shot Accuracy": 8,
    "Shot Blocking": 7,
    "Hand-Eye": 8,
    "Strength": 9,
    "Endurance": 8,
    "Durability": 8,
}


def _interpolate_rating(value: float, points: Sequence[tuple[float, int]]) -> int:
    value = max(points[0][0], min(points[-1][0], float(value)))
    for (left_value, left_rating), (right_value, right_rating) in zip(points, points[1:]):
        if left_value <= value <= right_value:
            ratio = (value - left_value) / max(0.001, right_value - left_value)
            return int((left_rating + ((right_rating - left_rating) * ratio)) + 0.5)
    return points[-1][1]


def ice_time_role_overall(minutes_per_game: float, position: str = "") -> int:
    """Map absolute TOI/game to the NHL Legacy role ladder.

    A player reaches full workload credit at 25 minutes. Defensemen use a
    separate curve because pair deployment naturally runs above forward-line
    deployment.
    """
    points = DEFENSE_ICE_TIME_ROLE_POINTS if str(position).upper() == "D" else FORWARD_ICE_TIME_ROLE_POINTS
    return _interpolate_rating(minutes_per_game, points)


def workload_adjusted_skater_overall(
    quality_score: float,
    minutes_per_game: float | None,
    position: str = "",
) -> int:
    """Blend performance quality with role, with TOI acting as an upside cap."""
    performance_rating = nhl_role_overall_from_score(quality_score)
    if minutes_per_game is None:
        return performance_rating
    role_rating = ice_time_role_overall(minutes_per_game, position)
    blended = round((performance_rating * 0.55) + (role_rating * 0.45))
    role_floor = max(72, role_rating - 6)
    role_ceiling = min(96, role_rating + 2)
    return max(role_floor, min(role_ceiling, blended))


def apply_workload_attribute_ceilings(
    recommendation: RecommendationSet,
    *,
    target_overall: int,
    minutes_per_game: float | None,
) -> None:
    if minutes_per_game is None:
        return
    for label, offset in SKATER_ROLE_CEILING_OFFSETS.items():
        if label not in recommendation.suggestions:
            continue
        ceiling = min(99, int(target_overall) + offset)
        prior = recommendation.suggestions[label]
        if prior <= ceiling:
            continue
        recommendation.suggestions[label] = ceiling
        note = recommendation.notes.get(label, "advanced metrics")
        recommendation.notes[label] = (
            f"{note} | {minutes_per_game:.1f} TOI/GP role ceiling {ceiling}"
        )


def stabilize_recommendations(
    suggestions: dict[str, int],
    current: dict[str, int] | None,
    *,
    position: str = "",
    player_kind: str = "skater",
    target_overall: int | None = None,
    role_ceiling_overall: int | None = None,
    notes: dict[str, str] | None = None,
) -> dict[str, int]:
    """Blend noisy metrics and keep NHL-calibre attribute sets coherent."""
    if not current:
        return dict(suggestions)
    result: dict[str, int] = {}
    position = position.upper()
    for label, proposed in suggestions.items():
        if label not in current:
            result[label] = int(proposed)
            continue
        old = int(current[label])
        proposed = int(proposed)
        if player_kind == "goalie":
            blended = round((old * 0.65) + (proposed * 0.35))
            result[label] = max(old - 5, min(old + 5, blended))
            continue
        if label == "Face-offs" and position == "D":
            result[label] = 36
            continue
        if label == "Face-offs":
            result[label] = max(60, min(99, proposed))
            continue
        if label in {"Body Checking", "Aggressiveness"}:
            blended = round((old * 0.85) + (proposed * 0.15))
            result[label] = max(old - 3, min(old + 3, blended))
        elif label in {
            "Off. Awareness",
            "Def. Awareness",
            "Passing",
            "Wrist Shot Accuracy",
            "Puck Control",
            "Stick Checking",
        }:
            blended = round((old * 0.55) + (proposed * 0.45))
            result[label] = max(old - 5, min(old + 6, blended))
        elif label in {"Shot Blocking", "Strength", "Discipline"}:
            blended = round((old * 0.68) + (proposed * 0.32))
            result[label] = max(old - 4, min(old + 5, blended))
        else:
            blended = round((old * 0.60) + (proposed * 0.40))
            result[label] = max(old - 6, min(old + 6, blended))

    if player_kind != "skater" or target_overall is None:
        return result

    target = max(72, min(96, int(target_overall)))
    if position == "D":
        minimums = {
            "Def. Awareness": target + 1,
            "Stick Checking": target,
            "Shot Blocking": target - 2,
            "Strength": target - 3,
            "Off. Awareness": target - 5,
            "Passing": target - 4,
        }
        raise_order = [
            "Def. Awareness",
            "Stick Checking",
            "Shot Blocking",
            "Strength",
            "Off. Awareness",
            "Passing",
            "Slap Shot Accuracy",
            "Slap Shot Power",
            "Agility",
            "Poise",
            "Body Checking",
        ]
        archetype = "two_way_defenseman"
    else:
        minimums = {
            "Off. Awareness": target + 1,
            "Def. Awareness": target - 2,
            "Passing": target - 1,
            "Wrist Shot Accuracy": target - 2,
            "Puck Control": target - 2,
            "Stick Checking": target - 3,
        }
        raise_order = [
            "Def. Awareness",
            "Off. Awareness",
            "Passing",
            "Wrist Shot Accuracy",
            "Puck Control",
            "Stick Checking",
            "Strength",
            "Wrist Shot Power",
            "Agility",
            "Acceleration",
            "Speed",
            "Balance",
            "Poise",
        ]
        archetype = "two_way_forward"

    adjusted_labels: set[str] = set()
    role_ceilings = {}
    if role_ceiling_overall is not None:
        ceiling_target = max(72, min(96, int(role_ceiling_overall)))
        role_ceilings = {
            label: min(99, ceiling_target + offset)
            for label, offset in SKATER_ROLE_CEILING_OFFSETS.items()
            if label in result
        }
    for label, ceiling in role_ceilings.items():
        if result[label] > ceiling:
            result[label] = ceiling
            adjusted_labels.add(label)
    for label, floor in minimums.items():
        if label not in result:
            continue
        prior = result.get(label, int(current.get(label, 36)))
        guarded = min(99, max(prior, floor))
        if guarded != prior:
            result[label] = guarded
            adjusted_labels.add(label)

    def estimated_overall() -> int:
        combined = {label: int(value) for label, value in current.items()}
        combined.update(result)
        semantic = {
            semantic_name: int(combined.get(label, 36))
            for label, semantic_name in DISPLAY_TO_SEMANTIC.items()
        }
        return calculate_weighted_overall(semantic, archetype, position=position)

    # Awareness and core role skills are lifted in rounds, not as one giant
    # awareness spike, until the proposed set reaches its metrics baseline.
    while estimated_overall() < target:
        changed = False
        for label in raise_order:
            if label not in result:
                continue
            value = result.get(label, int(current.get(label, 36)))
            if value >= role_ceilings.get(label, 99):
                continue
            result[label] = value + 1
            adjusted_labels.add(label)
            changed = True
            if estimated_overall() >= target:
                break
        if not changed:
            break

    lower_floor_offsets = {
        "Off. Awareness": -2,
        "Def. Awareness": -2,
        "Passing": -3,
        "Puck Control": -3,
        "Stick Checking": -4,
        "Poise": -5,
        "Deking": -5,
        "Wrist Shot Accuracy": -4,
        "Slap Shot Accuracy": -6,
        "Shot Blocking": -7,
        "Hand-Eye": -6,
        "Strength": -5,
        "Endurance": -5,
        "Durability": -8,
    }
    lower_order = [
        "Off. Awareness",
        "Def. Awareness",
        "Passing",
        "Puck Control",
        "Stick Checking",
        "Wrist Shot Accuracy",
        "Poise",
        "Deking",
        "Slap Shot Accuracy",
        "Shot Blocking",
        "Hand-Eye",
        "Strength",
        "Endurance",
        "Durability",
    ]
    while estimated_overall() > target:
        changed = False
        for label in lower_order:
            if label not in result:
                continue
            floor = max(36, minimums.get(label, target + lower_floor_offsets[label]))
            if result[label] <= floor:
                continue
            result[label] -= 1
            adjusted_labels.add(label)
            changed = True
            if estimated_overall() <= target:
                break
        if not changed:
            break

    if notes is not None:
        for label in adjusted_labels:
            existing = notes.get(label, "advanced metrics")
            notes[label] = f"{existing} | stabilized to {target} EA OVR role baseline"
    return result


class LegacyAttributeMapper:
    def __init__(self, model: MoneyPuckPercentileModel) -> None:
        self.model = model
        self._recommendation_cache: dict[tuple[object, ...], RecommendationSet] = {}

    def _p(
        self,
        row: dict[str, Any],
        keys: Sequence[str],
        *,
        inverse: bool = False,
        default: float = 0.5,
    ) -> float:
        return self.model.percentile(row, keys, inverse=inverse, default=default)

    def _weighted_score(
        self,
        row: dict[str, Any],
        parts: Sequence[tuple[Sequence[str], float, bool]],
        *,
        extras: Sequence[tuple[float | None, float]] = (),
    ) -> float:
        total_weight = 0.0
        score = 0.0
        for keys, weight, inverse in parts:
            score += self._p(row, keys, inverse=inverse) * weight
            total_weight += weight
        for value, weight in extras:
            if value is None or weight <= 0:
                continue
            score += max(0.0, min(1.0, float(value))) * weight
            total_weight += weight
        return score / total_weight if total_weight else 0.5

    @staticmethod
    def _position(row: dict[str, Any]) -> str:
        return str(row.get("position") or "").upper()

    @staticmethod
    def _size_score(player_bio: dict[str, Any] | None) -> float | None:
        if not player_bio:
            return None
        height = number(player_bio, "heightInInches", "height_in_inches")
        weight = number(player_bio, "weightInPounds", "weight_in_pounds")
        if height is None and weight is None:
            return None
        height_score = None if height is None else (float(height) - 68.0) / (78.0 - 68.0)
        weight_score = None if weight is None else (float(weight) - 170.0) / (240.0 - 170.0)
        parts = [value for value in (height_score, weight_score) if value is not None]
        if not parts:
            return None
        if height_score is not None and weight_score is not None:
            return max(0.0, min(1.0, (height_score * 0.35) + (weight_score * 0.65)))
        return max(0.0, min(1.0, sum(parts) / len(parts)))

    def add_weighted(
        self,
        rec: RecommendationSet,
        row: dict[str, Any],
        label: str,
        parts: Sequence[tuple[Sequence[str], float, bool]],
        floor: int,
        ceiling: int,
        note: str,
        *,
        extras: Sequence[tuple[float | None, float]] = (),
    ) -> None:
        rec.add(label, rating_from_score(self._weighted_score(row, parts, extras=extras), floor, ceiling), note)

    def money_puck_recommendations(
        self,
        row: dict[str, Any],
        min_games: int = 20,
        *,
        player_bio: dict[str, Any] | None = None,
    ) -> RecommendationSet:
        size_key = (
            number(player_bio or {}, "heightInInches", "height_in_inches"),
            number(player_bio or {}, "weightInPounds", "weight_in_pounds"),
        )
        cache_key = ("skater", id(row), min_games, *size_key)
        cached = self._recommendation_cache.get(cache_key)
        if cached is not None:
            return clone_recommendation_set(cached)
        result = self._money_puck_recommendations_uncached(
            row,
            min_games=min_games,
            player_bio=player_bio,
        )
        self._recommendation_cache[cache_key] = clone_recommendation_set(result)
        return result

    def _money_puck_recommendations_uncached(
        self,
        row: dict[str, Any],
        min_games: int = 20,
        *,
        player_bio: dict[str, Any] | None = None,
    ) -> RecommendationSet:
        rec = RecommendationSet()
        gp = games_played(row)
        if gp < min_games:
            rec.skipped_reason = f"Skipped: {gp} GP, minimum is {min_games}."
            return rec

        add = self.add_weighted
        size_score = self._size_score(player_bio)
        position = self._position(row)
        is_defense = position == "D"
        won, taken = faceoff_won_taken(row)
        faceoff_defense_score = None
        if position == "D":
            rec.add("Face-offs", 36, "defenseman fixed value")
        elif won is not None and taken is not None and taken > 0:
            pct_score = max(0.0, min(1.0, (((won / taken) * 100.0) - 45.0) / 15.0))
            volume_score = max(0.0, min(1.0, taken / 800.0))
            faceoff_defense_score = (pct_score * 0.62) + (volume_score * 0.38)

        add(rec, row, "Wrist Shot Accuracy", [(("_goals_per60",), 0.20, False), (("_xgoals_per60",), 0.24, False), (("_shooting_pct",), 0.18, False), (("_shots_per60",), 0.14, False), (("I_F_highDangerGoals",), 0.14, False), (("_goals_above_expected",), 0.10, False)], 64, 98, "finishing: goals/xG rates, shooting pct, shot volume, high-danger goals")
        add(rec, row, "Slap Shot Accuracy", [(("_shot_attempts_per60",), 0.24, False), (("_xgoals_per60",), 0.22, False), (("_shots_per60",), 0.20, False), (("I_F_rebounds",), 0.14, False), (("_goals_per60",), 0.12, False), (("I_F_mediumDangerShots",), 0.08, False)], 54, 96, "shot generation, xG rate, rebound creation, medium-range volume")
        add(rec, row, "Passing", [(("_primary_assists_per60",), 0.34, False), (("_assists_per60",), 0.24, False), (("onIce_xGoalsPercentage",), 0.18, False), (("_points_per60",), 0.14, False), (("gameScore",), 0.10, False)], 66, 99, "primary assists, assist rate, on-ice xG share, points, game score")
        add(rec, row, "Puck Control", [(("onIce_corsiPercentage",), 0.22, False), (("onIce_xGoalsPercentage",), 0.22, False), (("_giveaways_per60",), 0.18, True), (("_takeaways_per60",), 0.14, False), (("_points_per60",), 0.14, False), (("_toi_per_gp",), 0.10, False)], 68, 98, "possession share, xG share, puck security, takeaways, skill workload")
        add(rec, row, "Hand-Eye", [(("I_F_highDangerGoals",), 0.20, False), (("I_F_rebounds",), 0.18, False), (("_shooting_pct",), 0.18, False), (("_xgoals_per60",), 0.18, False), (("_shots_per60",), 0.14, False), (("_goals_above_expected",), 0.12, False)], 56, 98, "tips/net-front finishing: high-danger goals, rebounds, shooting pct, xG")
        add(rec, row, "Off. Awareness", [(("gameScore",), 0.24, False), (("_points_per60",), 0.22, False), (("_xgoals_per60",), 0.18, False), (("_primary_assists_per60",), 0.16, False), (("onIce_xGoalsPercentage",), 0.14, False), (("_toi_per_gp",), 0.06, False)], 68, 99, "overall offensive impact: game score, points, xG, primary assists, xG share")
        add(rec, row, "Poise", [(("gameScore",), 0.26, False), (("onIce_xGoalsPercentage",), 0.18, False), (("_giveaways_per60",), 0.18, True), (("_penalties_per60",), 0.16, True), (("_points_per60",), 0.12, False), (("_toi_per_gp",), 0.10, False)], 56, 98, "impact under workload: game score, xG share, low giveaways/penalties, points")

        add(rec, row, "Def. Awareness", [(("onIce_xGoalsPercentage",), 0.20, False), (("_onice_xga_per60",), 0.15, True), (("_onice_hd_xga_per60",), 0.10, True), (("_takeaways_per60",), 0.12, False), (("_takeaways",), 0.08, False), (("_blocks_per60",), 0.10, False), (("_blocks",), 0.10, False), (("onIce_corsiPercentage",), 0.08, False), (("_toi_per_gp",), 0.07, False)], 68, 99, "defensive impact: xG share, xGA suppression, takeaways, blocks, possession, workload", extras=((faceoff_defense_score, 0.10),))
        add(rec, row, "Stick Checking", [(("_takeaways_per60",), 0.24, False), (("_takeaways",), 0.12, False), (("_blocks_per60",), 0.15, False), (("_blocks",), 0.10, False), (("_onice_xga_per60",), 0.13, True), (("onIce_xGoalsPercentage",), 0.12, False), (("_giveaways_per60",), 0.08, True), (("_toi_per_gp",), 0.06, False)], 66, 99, "defensive stick detail: takeaways, blocks, xGA suppression, xG share, puck security", extras=((faceoff_defense_score, 0.06),))
        add(rec, row, "Shot Blocking", [(("_blocks_per60",), 0.48, False), (("_blocks",), 0.34, False), (("_onice_hd_xga_per60",), 0.10, True), (("_toi_per_gp",), 0.08, False)], 50, 96, "blocked shots by rate and total, with high-danger defensive context")

        add(rec, row, "Body Checking", [(("_hits_per60",), 0.42, False), (("_hits",), 0.24, False), (("_takeaways_per60",), 0.12, False), (("_pim_per60",), 0.08, False)], 48, 98, "physical pressure: hit rate, hit total, pressure takeaways, edge", extras=((size_score, 0.14),))
        add(rec, row, "Aggressiveness", [(("_hits_per60",), 0.36, False), (("_pim_per60",), 0.20, False), (("_penalties_per60",), 0.16, False), (("_takeaways_per60",), 0.14, False)], 44, 98, "physical engagement: hits, penalty minutes, penalties, takeaways", extras=((size_score, 0.10),))
        add(rec, row, "Discipline", [(("_penalties_per60",), 0.48, True), (("_pim_per60",), 0.32, True), (("penaltiesDrawn", "I_F_penaltiesDrawn"), 0.12, False), (("_giveaways_per60",), 0.08, True)], 45, 98, "lower penalties/PIM are better; penalties drawn and puck security add context")
        add(rec, row, "Durability", [(("games_played", "gamesPlayed"), 0.58, False), (("_toi_per_gp",), 0.28, False), (("shifts", "I_F_shifts"), 0.14, False)], 55, 98, "games played, ice time per game, shifts")
        add(rec, row, "Endurance", [(("_toi_per_gp",), 0.46, False), (("games_played", "gamesPlayed"), 0.26, False), (("shifts", "I_F_shifts"), 0.16, False), (("_onice_cf_per60",), 0.12, False)], 66, 98, "workload: TOI/game, games, shifts, pace")
        add(rec, row, "Strength", [(("_toi_per_gp",), 0.14, False), (("_giveaways_per60",), 0.14, True), (("_xgoals_per60",), 0.10, False), (("_blocks",), 0.08, False), (("_hits",), 0.08, False), (("_hits_per60",), 0.06, False)], 58, 99, "size-weighted strength: player size, puck protection, workload, net-front xG, physical play", extras=((size_score, 0.50),))
        add(rec, row, "Deking", [(("_points_per60",), 0.24, False), (("onIce_corsiPercentage",), 0.20, False), (("_giveaways_per60",), 0.18, True), (("_primary_assists_per60",), 0.16, False), (("_shots_per60",), 0.12, False), (("gameScore",), 0.10, False)], 56, 98, "puck skill proxy: production rate, possession, low giveaways, creation")

        if won is not None and taken is not None and taken > 0:
            pct_percent = max(0.0, min(100.0, (won / taken) * 100.0))
            points = [(42.0, 48), (45.0, 58), (48.0, 68), (50.0, 76), (52.0, 84), (54.0, 90), (56.0, 94), (58.0, 96), (60.0, 98), (62.0, 99)]
            if pct_percent <= points[0][0]:
                faceoff_rating = points[0][1]
            elif pct_percent >= points[-1][0]:
                faceoff_rating = points[-1][1]
            else:
                faceoff_rating = 76
                for (x1, y1), (x2, y2) in zip(points, points[1:]):
                    if x1 <= pct_percent <= x2:
                        faceoff_rating = round(y1 + (((pct_percent - x1) / (x2 - x1)) * (y2 - y1)))
                        break
            if taken < 50:
                volume_cap = 76
            elif taken < 100:
                volume_cap = 84
            elif taken < 250:
                volume_cap = 90
            elif taken < 500:
                volume_cap = 94
            else:
                volume_cap = 99
            faceoff_rating = max(60, min(volume_cap, int(faceoff_rating)))
            rec.add("Face-offs", faceoff_rating, f"{int(won)}/{int(taken)} faceoffs ({pct_percent:.1f}%); volume cap {volume_cap}")
        else:
            rec.add("Face-offs", 60, "forward minimum: no recorded faceoffs")

        shots = number(row, "_shots", default=0) or 0
        goals = number(row, "_goals", default=0) or 0
        assists = number(row, "_assists", default=0) or 0
        if shots + assists + goals > 0:
            pass_score = assists / max(1.0, assists + goals + (shots * 0.10))
            rec.add("Shoot-Pass Bias", rating_from_score(pass_score, 0, 15), "assist share versus goals and shot volume; 0=shoot-heavy, 15=pass-heavy")

        offence = self._weighted_score(row, [(("_xgoals_per60",), 0.30, False), (("_primary_assists_per60",), 0.24, False), (("gameScore",), 0.24, False), (("_points_per60",), 0.22, False)])
        defence = self._weighted_score(row, [(("_onice_xga_per60",), 0.28, True), (("_blocks_per60",), 0.22, False), (("_takeaways_per60",), 0.22, False), (("onIce_xGoalsPercentage",), 0.18, False), (("_onice_hd_xga_per60",), 0.10, True)])
        bias_score = offence / max(0.01, offence + defence)
        if is_defense:
            bias_score = (bias_score * 0.90) + 0.05
        rec.add("Defence-Offence Bias", rating_from_score(bias_score, 1, 15), "offensive creation versus defensive impact")

        if is_defense:
            quality_score = self._weighted_score(
                row,
                [
                    (("gameScore",), 0.25, False),
                    (("onIce_xGoalsPercentage",), 0.20, False),
                    (("_onice_xga_per60",), 0.20, True),
                    (("_blocks_per60",), 0.10, False),
                    (("_blocks",), 0.07, False),
                    (("_points_per60",), 0.10, False),
                    (("_takeaways_per60",), 0.08, False),
                ],
            )
        else:
            quality_score = self._weighted_score(
                row,
                [
                    (("gameScore",), 0.26, False),
                    (("_points_per60",), 0.22, False),
                    (("onIce_xGoalsPercentage",), 0.17, False),
                    (("_xgoals_per60",), 0.13, False),
                    (("_primary_assists_per60",), 0.10, False),
                    (("_onice_xga_per60",), 0.07, True),
                    (("_takeaways_per60",), 0.05, False),
                ],
            )
        toi_seconds = number(row, "_toi_per_gp")
        toi_minutes = None if toi_seconds is None else max(0.0, float(toi_seconds) / 60.0)
        performance_baseline = nhl_role_overall_from_score(quality_score)
        role_baseline = None if toi_minutes is None else ice_time_role_overall(toi_minutes, position)
        raw_baseline = workload_adjusted_skater_overall(quality_score, toi_minutes, position)
        confidence = min(1.0, (gp / 55.0) ** 0.5)
        baseline = int((78 + ((raw_baseline - 78) * confidence)) + 0.5)
        if role_baseline is not None:
            baseline = min(baseline, min(96, role_baseline + 2))
        if gp < 30:
            baseline = min(baseline, 89)
        elif gp < 40:
            baseline = min(baseline, 92)
        rec.overall_baseline = max(72, min(96, baseline))
        workload_note = "TOI unavailable" if toi_minutes is None else (
            f"{toi_minutes:.1f} TOI/GP, workload role {role_baseline}"
        )
        rec.overall_note = (
            f"NHL role baseline {rec.overall_baseline}: {gp} GP, {workload_note}, "
            f"performance {performance_baseline} ({quality_score * 100:.0f}th percentile)"
        )
        apply_workload_attribute_ceilings(
            rec,
            target_overall=rec.overall_baseline,
            minutes_per_game=toi_minutes,
        )
        return rec

    def goalie_recommendations(self, row: dict[str, Any], min_games: int = 20) -> RecommendationSet:
        cache_key = ("goalie", id(row), min_games)
        cached = self._recommendation_cache.get(cache_key)
        if cached is not None:
            return clone_recommendation_set(cached)
        result = self._goalie_recommendations_uncached(row, min_games=min_games)
        self._recommendation_cache[cache_key] = clone_recommendation_set(result)
        return result

    def _goalie_recommendations_uncached(self, row: dict[str, Any], min_games: int = 20) -> RecommendationSet:
        rec = RecommendationSet()
        gp = games_played(row)
        if gp < min_games:
            rec.skipped_reason = f"Skipped: {gp} GP, minimum is {min_games}."
            return rec

        add = self.add_weighted
        core = [(("_save_pct",), 0.28, False), (("_goals_saved_above_expected",), 0.24, False), (("_gsae_per60",), 0.16, False), (("_high_danger_save_pct",), 0.18, False), (("_toi_per_gp",), 0.14, False)]
        low = [(("_low_danger_save_pct",), 0.34, False), (("_save_pct",), 0.22, False), (("_goals_saved_above_expected",), 0.18, False), (("_rebound_rate",), 0.14, True), (("_freeze_rate",), 0.12, False)]
        high = [(("_high_danger_save_pct",), 0.38, False), (("_gsae_per60",), 0.22, False), (("_save_pct",), 0.18, False), (("_rebound_rate",), 0.12, True), (("_shots_against_per60",), 0.10, False)]

        add(rec, row, "Glove Side Low", low, 72, 98, "low-danger save pct, total save pct, GSAE, rebound/freeze control")
        add(rec, row, "Glove Side High", high, 74, 99, "high-danger save pct, GSAE rate, total save pct, rebound control")
        add(rec, row, "Stick Side High", high, 74, 99, "high-danger save pct, GSAE rate, total save pct, rebound control")
        add(rec, row, "Stick Side Low", low, 72, 98, "low-danger save pct, total save pct, GSAE, rebound/freeze control")
        add(rec, row, "Five Hole", [(("_medium_danger_save_pct",), 0.32, False), (("_high_danger_save_pct",), 0.26, False), (("_save_pct",), 0.20, False), (("_rebound_rate",), 0.12, True), (("_gsae_per60",), 0.10, False)], 74, 99, "medium/high-danger save pct, total save pct, rebound control")
        add(rec, row, "Agility", [(("_high_danger_save_pct",), 0.28, False), (("_gsae_per60",), 0.24, False), (("_rebound_rate",), 0.20, True), (("_shots_against_per60",), 0.16, False), (("_save_pct",), 0.12, False)], 74, 99, "reaction profile: high-danger saves, GSAE rate, rebound control, shot workload")
        add(rec, row, "Speed", [(("_shots_against_per60",), 0.30, False), (("_high_danger_save_pct",), 0.24, False), (("_rebound_rate",), 0.18, True), (("_gsae_per60",), 0.16, False), (("_toi_per_gp",), 0.12, False)], 70, 96, "movement workload proxy: shots faced, high-danger saves, rebound recovery")
        add(rec, row, "Poke Check", [(("_high_danger_save_pct",), 0.30, False), (("_rebound_rate",), 0.24, True), (("_gsae_per60",), 0.20, False), (("_shots_against_per60",), 0.14, False), (("_save_pct",), 0.12, False)], 66, 94, "breakaway/rush proxy: high-danger saves, rebound control, GSAE")
        add(rec, row, "Consistency", [(("_save_pct",), 0.30, False), (("_goals_saved_above_expected",), 0.24, False), (("_gsae_per60",), 0.18, False), (("games_played", "gamesPlayed"), 0.16, False), (("_goals_against_per60",), 0.12, True)], 72, 99, "save pct, GSAE, GSAE rate, games, goals-against rate")
        add(rec, row, "Breakaway", high, 74, 99, "high-danger save pct and GSAE profile")
        add(rec, row, "Endurance", [(("games_played", "gamesPlayed"), 0.48, False), (("_toi_per_gp",), 0.32, False), (("_shots_against_per60",), 0.20, False)], 72, 98, "games, TOI/game, shots faced")
        add(rec, row, "Shot Recovery", [(("_rebound_rate",), 0.44, True), (("_freeze_rate",), 0.20, False), (("_gsae_per60",), 0.18, False), (("_save_pct",), 0.18, False)], 72, 99, "low rebound rate, freeze rate, GSAE rate, save pct")
        add(rec, row, "Rebound Control", [(("_rebound_rate",), 0.52, True), (("_freeze_rate",), 0.18, False), (("_save_pct",), 0.16, False), (("_gsae_per60",), 0.14, False)], 72, 99, "rebound rate weighted heavily, with freeze/save/GSAE context")
        add(rec, row, "Poise", core, 74, 99, "overall goalie impact under workload: save pct, GSAE, high-danger saves, TOI")
        add(rec, row, "Passing", [(("_freeze_rate",), 0.25, True), (("_rebound_rate",), 0.25, True), (("_save_pct",), 0.20, False), (("_toi_per_gp",), 0.15, False), (("_gsae_per60",), 0.15, False)], 45, 88, "limited puck-play proxy; no direct passing feed available")
        add(rec, row, "Angles", [(("_save_pct",), 0.28, False), (("_goals_against_per60",), 0.22, True), (("_high_danger_save_pct",), 0.20, False), (("_medium_danger_save_pct",), 0.18, False), (("_gsae_per60",), 0.12, False)], 74, 99, "positioning proxy: save pct, goals-against rate, danger-zone save pct")
        add(rec, row, "Puck Play Frequency", [(("_freeze_rate",), 0.28, True), (("_rebound_rate",), 0.24, True), (("_shots_against_per60",), 0.20, False), (("_toi_per_gp",), 0.16, False), (("_save_pct",), 0.12, False)], 40, 86, "limited puck-touch proxy from freezes/rebounds/workload")
        add(rec, row, "Aggressiveness", [(("_shots_against_per60",), 0.30, False), (("_high_danger_save_pct",), 0.22, False), (("_rebound_rate",), 0.18, True), (("_freeze_rate",), 0.16, False), (("_gsae_per60",), 0.14, False)], 45, 92, "shot workload, high-danger saves, rebound/freeze profile")
        add(rec, row, "Durability", [(("games_played", "gamesPlayed"), 0.60, False), (("_toi_per_gp",), 0.28, False), (("_shots_against_per60",), 0.12, False)], 70, 98, "games, TOI/game, shots faced")
        add(rec, row, "Vision", core, 72, 99, "save pct, GSAE, high-danger saves, workload")
        baseline_score = self._weighted_score(
            row,
            [
                (("_save_pct",), 0.20, False),
                (("_goals_against_per60",), 0.12, True),
                (("_goals_saved_above_expected",), 0.24, False),
                (("_save_pct_above_expected",), 0.22, False),
                (("_gsae_per60",), 0.12, False),
                (("games_played", "gamesPlayed"), 0.10, False),
            ],
        )
        raw_baseline = 70 + (baseline_score * 28)
        workload_confidence = min(1.0, (gp / 50.0) ** 0.5)
        baseline = round(80 + ((raw_baseline - 80) * workload_confidence))
        if gp < 25:
            baseline = min(baseline, 89)
        elif gp < 35:
            baseline = min(baseline, 92)
        elif gp < 45:
            baseline = min(baseline, 95)
        baseline = max(70, min(98, baseline))
        rec.overall_baseline = baseline
        save_pct = number(row, "_save_pct", default=0.0) or 0.0
        expected_save_pct = number(row, "_expected_save_pct", default=0.0) or 0.0
        gaa = number(row, "_goals_against_per60", default=0.0) or 0.0
        gsae = number(row, "_goals_saved_above_expected", default=0.0) or 0.0
        rec.overall_note = (
            f"goalie baseline {baseline}: {gp} GP, {save_pct:.3f} SV%, {gaa:.2f} GAA, "
            f"{expected_save_pct:.3f} xSV%, {gsae:+.1f} goals saved above expected"
        )
        baseline_attributes = {
            "Glove Side Low", "Glove Side High", "Stick Side High", "Stick Side Low",
            "Five Hole", "Agility", "Consistency", "Breakaway", "Shot Recovery",
            "Rebound Control", "Poise", "Angles", "Vision",
        }
        for label in baseline_attributes:
            if label not in rec.suggestions:
                continue
            rec.suggestions[label] = max(baseline - 4, min(baseline + 4, rec.suggestions[label]))
            rec.notes[label] = f"{rec.notes[label]} | {rec.overall_note}"
        return rec

    @staticmethod
    def blend_with_edge(moneypuck: RecommendationSet, edge_suggestions: dict[str, int] | None = None) -> RecommendationSet:
        return LegacyAttributeMapper._blend(
            moneypuck,
            edge_suggestions,
            {
                "Speed": (0.12, 0.88),
                "Acceleration": (0.15, 0.85),
                "Agility": (0.28, 0.72),
                "Endurance": (0.62, 0.38),
                "Wrist Shot Accuracy": (0.70, 0.30),
                "Wrist Shot Power": (0.25, 0.75),
                "Slap Shot Accuracy": (0.70, 0.30),
                "Slap Shot Power": (0.25, 0.75),
                "Passing": (0.88, 0.12),
                "Puck Control": (0.78, 0.22),
                "Deking": (0.50, 0.50),
                "Hand-Eye": (0.70, 0.30),
                "Off. Awareness": (0.85, 0.15),
                "Def. Awareness": (0.94, 0.06),
                "Poise": (0.82, 0.18),
                "Discipline": (0.97, 0.03),
                "Body Checking": (0.88, 0.12),
                "Strength": (0.88, 0.12),
                "Aggressiveness": (0.95, 0.05),
                "Durability": (0.90, 0.10),
                "Stick Checking": (0.96, 0.04),
                "Shot Blocking": (0.96, 0.04),
                "Face-offs": (1.00, 0.00),
            },
            edge_source_name="NHL Edge",
        )

    @staticmethod
    def blend_goalie_with_edge(moneypuck: RecommendationSet, edge_suggestions: dict[str, int] | None = None) -> RecommendationSet:
        return LegacyAttributeMapper._blend(
            moneypuck,
            edge_suggestions,
            {
                "Glove Side Low": (0.80, 0.20),
                "Glove Side High": (0.78, 0.22),
                "Stick Side High": (0.78, 0.22),
                "Stick Side Low": (0.80, 0.20),
                "Five Hole": (0.78, 0.22),
                "Agility": (0.76, 0.24),
                "Speed": (0.65, 0.35),
                "Poke Check": (0.82, 0.18),
                "Consistency": (0.80, 0.20),
                "Breakaway": (0.78, 0.22),
                "Endurance": (0.88, 0.12),
                "Shot Recovery": (0.82, 0.18),
                "Rebound Control": (0.84, 0.16),
                "Poise": (0.80, 0.20),
                "Angles": (0.82, 0.18),
                "Aggressiveness": (0.88, 0.12),
                "Durability": (0.92, 0.08),
                "Vision": (0.82, 0.18),
            },
            edge_source_name="NHL Edge Goalie",
        )

    @staticmethod
    def _blend(
        moneypuck: RecommendationSet,
        edge_suggestions: dict[str, int] | None,
        weights: dict[str, tuple[float, float]],
        *,
        edge_source_name: str,
    ) -> RecommendationSet:
        edge_suggestions = edge_suggestions or {}
        result = RecommendationSet(
            suggestions=dict(moneypuck.suggestions),
            notes=dict(moneypuck.notes),
            sources=dict(moneypuck.sources),
            skipped_reason=moneypuck.skipped_reason,
            overall_baseline=moneypuck.overall_baseline,
            overall_note=moneypuck.overall_note,
        )
        for label, edge_value in edge_suggestions.items():
            if edge_value <= 0:
                continue
            if label in result.suggestions:
                mp_weight, edge_weight = weights.get(label, (0.70, 0.30))
                result.suggestions[label] = int(round((result.suggestions[label] * mp_weight) + (edge_value * edge_weight)))
                result.notes[label] = f"{result.notes.get(label, 'MoneyPuck')} | blended with {edge_source_name}"
                result.sources[label] = f"MoneyPuck + {edge_source_name}"
            else:
                result.add(label, edge_value, f"{edge_source_name} recommendation", edge_source_name)
        return result
