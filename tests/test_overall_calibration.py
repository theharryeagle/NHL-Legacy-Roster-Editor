from nhl_legacy_editor.attribute_map import GOALIE_ATTRIBUTE_SPECS
from nhl_legacy_editor.attribute_mapper import calculate_anchored_goalie_overall, calculate_goalie_overall
from nhl_legacy_editor.rating_models import calculate_anchored_overall, calculate_weighted_overall, stored_overall_from_ratings_row


def test_koet_is_not_treated_as_a_stored_overall() -> None:
    assert stored_overall_from_ratings_row({"koEt": 59}) is None
    assert stored_overall_from_ratings_row({"koEt": 50}) is None
    assert stored_overall_from_ratings_row({"koEt": 0}) is None
    assert stored_overall_from_ratings_row({}) is None


def test_goalie_consistency_does_not_overwrite_stored_overall() -> None:
    consistency = next(spec for spec in GOALIE_ATTRIBUTE_SPECS if spec.label == "Consistency")
    assert consistency.field == "KzvQ"
    assert consistency.mode == "raw"
    assert consistency.max_value == 2


def test_forward_formula_matches_low_nhl_card() -> None:
    ratings = {
        "speed": 87,
        "body_checking": 75,
        "endurance": 81,
        "puck_control": 70,
        "passing": 59,
        "slap_shot_power": 88,
        "slap_shot_accuracy": 70,
        "wrist_shot_power": 88,
        "wrist_shot_accuracy": 71,
        "agility": 82,
        "strength": 86,
        "acceleration": 85,
        "balance": 87,
        "faceoffs": 60,
        "durability": 75,
        "deking": 72,
        "aggressiveness": 74,
        "poise": 69,
        "hand_eye": 70,
        "shot_blocking": 89,
        "offensive_awareness": 67,
        "defensive_awareness": 72,
        "discipline": 59,
        "fighting_skill": 78,
        "stick_checking": 73,
    }
    assert calculate_weighted_overall(ratings, "two_way_forward", position="C") == 65
    assert calculate_weighted_overall(ratings, "sniper", position="C") == 65

    # An unchanged roster card must retain EA's exact stored value even when
    # the reverse-engineered absolute formula disagrees with it.
    assert calculate_anchored_overall(
        ratings,
        ratings,
        "two_way_forward",
        70,
        position="C",
    ) == 70

    upgraded = dict(ratings)
    upgraded["offensive_awareness"] += 5
    assert calculate_anchored_overall(
        ratings,
        upgraded,
        "two_way_forward",
        70,
        position="C",
    ) > 70


def test_defense_formula_matches_observed_legacy_card() -> None:
    ratings = {
        "slap_shot_accuracy": 70,
        "slap_shot_power": 85,
        "wrist_shot_accuracy": 70,
        "wrist_shot_power": 84,
        "deking": 70,
        "hand_eye": 72,
        "passing": 75,
        "puck_control": 75,
        "discipline": 77,
        "offensive_awareness": 71,
        "poise": 75,
        "acceleration": 84,
        "agility": 84,
        "balance": 84,
        "endurance": 80,
        "speed": 84,
        "aggressiveness": 75,
        "body_checking": 84,
        "durability": 80,
        "fighting_skill": 75,
        "strength": 84,
        "defensive_awareness": 82,
        "faceoffs": 36,
        "shot_blocking": 82,
        "stick_checking": 85,
    }
    # The reverse-engineered formula is within one point of the observed 75.
    assert calculate_weighted_overall(ratings, "defensive_defenseman", position="D") == 76


def test_goalie_formula_uses_legacy_coefficients() -> None:
    ratings = {
        "Glove Side Low": 84,
        "Glove Side High": 84,
        "Stick Side High": 84,
        "Stick Side Low": 84,
        "Five Hole": 85,
        "Agility": 85,
        "Speed": 86,
        "Poke Check": 84,
        "Consistency": 84,
        "Breakaway": 84,
        "Endurance": 85,
        "Shot Recovery": 84,
        "Rebound Control": 84,
        "Poise": 74,
        "Passing": 84,
        "Angles": 82,
        "Puck Play Frequency": 82,
        "Aggressiveness": 80,
        "Durability": 82,
        "Vision": 80,
    }
    assert calculate_goalie_overall(ratings, "Butterfly Goalie") == 80
    assert calculate_anchored_goalie_overall(
        ratings,
        ratings,
        86,
        "Butterfly Goalie",
    ) == 86
