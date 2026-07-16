from nhl_legacy_editor.attribute_mapper import (
    DISPLAY_TO_SEMANTIC,
    ice_time_role_overall,
    nhl_role_overall_from_score,
    stabilize_recommendations,
    workload_adjusted_skater_overall,
)
from nhl_legacy_editor.rating_models import calculate_weighted_overall


def test_defenseman_faceoffs_are_fixed_at_36() -> None:
    result = stabilize_recommendations(
        {"Face-offs": 65, "Body Checking": 48},
        {"Face-offs": 36, "Body Checking": 84},
        position="D",
    )

    assert result["Face-offs"] == 36
    assert result["Body Checking"] == 81


def test_goalie_changes_are_confidence_limited() -> None:
    result = stabilize_recommendations(
        {"Angles": 72, "Rebound Control": 99},
        {"Angles": 88, "Rebound Control": 82},
        position="G",
        player_kind="goalie",
    )

    assert result == {"Angles": 83, "Rebound Control": 87}


def test_defensive_proxies_move_gradually() -> None:
    result = stabilize_recommendations(
        {"Stick Checking": 60, "Def. Awareness": 99},
        {"Stick Checking": 88, "Def. Awareness": 78},
        position="C",
    )

    assert result == {"Stick Checking": 83, "Def. Awareness": 84}


def test_nhl_role_scale_keeps_replacement_players_in_range() -> None:
    assert nhl_role_overall_from_score(0.0) == 72
    assert nhl_role_overall_from_score(0.25) == 77
    assert nhl_role_overall_from_score(0.70) == 85
    assert nhl_role_overall_from_score(0.95) == 93


def test_ice_time_role_curve_gives_full_credit_at_25_minutes() -> None:
    assert ice_time_role_overall(25.0, "RW") == 96
    assert ice_time_role_overall(25.0, "D") == 96


def test_fourth_line_minutes_cap_strong_rate_stats() -> None:
    assert workload_adjusted_skater_overall(0.41, 7.76, "RW") == 76
    assert workload_adjusted_skater_overall(0.95, 10.0, "C") == 78


def test_role_cap_preserves_specialist_physical_traits() -> None:
    current = {label: 76 for label in DISPLAY_TO_SEMANTIC}
    current.update({"Body Checking": 92, "Aggressiveness": 95, "Fighting Skill": 90})
    result = stabilize_recommendations(
        {
            "Body Checking": 96,
            "Aggressiveness": 98,
            "Off. Awareness": 94,
            "Def. Awareness": 94,
            "Passing": 92,
            "Puck Control": 93,
            "Stick Checking": 91,
            "Wrist Shot Accuracy": 92,
        },
        current,
        position="RW",
        target_overall=76,
        role_ceiling_overall=76,
    )

    assert result["Body Checking"] > 85
    assert result["Aggressiveness"] > 85
    assert result["Off. Awareness"] <= 80
    assert result["Def. Awareness"] <= 81
    assert result["Puck Control"] <= 82


def test_fourth_line_role_lowers_macewen_profile_into_mid_70s() -> None:
    current = {
        label: 76
        for label in DISPLAY_TO_SEMANTIC
    }
    current.update(
        {
            "Speed": 83,
            "Body Checking": 87,
            "Endurance": 65,
            "Puck Control": 87,
            "Passing": 82,
            "Slap Shot Power": 86,
            "Slap Shot Accuracy": 81,
            "Wrist Shot Power": 85,
            "Wrist Shot Accuracy": 85,
            "Agility": 83,
            "Strength": 78,
            "Acceleration": 83,
            "Balance": 86,
            "Face-offs": 60,
            "Durability": 55,
            "Aggressiveness": 95,
            "Discipline": 50,
            "Fighting Skill": 85,
            "Off. Awareness": 85,
            "Def. Awareness": 88,
            "Stick Checking": 84,
        }
    )
    suggestions = {
        "Wrist Shot Accuracy": 84,
        "Slap Shot Accuracy": 81,
        "Passing": 73,
        "Puck Control": 82,
        "Hand-Eye": 75,
        "Off. Awareness": 80,
        "Poise": 70,
        "Def. Awareness": 81,
        "Stick Checking": 81,
        "Shot Blocking": 65,
        "Body Checking": 87,
        "Aggressiveness": 95,
        "Discipline": 50,
        "Durability": 55,
        "Endurance": 69,
        "Strength": 74,
        "Deking": 76,
        "Face-offs": 60,
    }
    result = stabilize_recommendations(
        suggestions,
        current,
        position="RW",
        target_overall=76,
        role_ceiling_overall=76,
    )
    combined = dict(current)
    combined.update(result)
    semantic = {
        semantic_name: combined[label]
        for label, semantic_name in DISPLAY_TO_SEMANTIC.items()
    }

    assert calculate_weighted_overall(semantic, "two_way_forward", position="RW") == 76
    assert result["Body Checking"] == 87
    assert result["Aggressiveness"] == 95
    assert combined["Fighting Skill"] == 85


def test_stabilization_reaches_the_ea_overall_floor() -> None:
    current = {label: 72 for label in DISPLAY_TO_SEMANTIC}
    current.update({"Speed": 82, "Acceleration": 82, "Agility": 82, "Face-offs": 60})
    suggestions = {
        "Passing": 65,
        "Puck Control": 66,
        "Wrist Shot Accuracy": 66,
        "Off. Awareness": 64,
        "Def. Awareness": 64,
        "Stick Checking": 65,
    }
    result = stabilize_recommendations(
        suggestions,
        current,
        position="LW",
        target_overall=75,
    )
    combined = dict(current)
    combined.update(result)
    semantic = {
        semantic_name: combined[label]
        for label, semantic_name in DISPLAY_TO_SEMANTIC.items()
    }
    assert calculate_weighted_overall(semantic, "two_way_forward", position="LW") >= 75


def test_stabilization_does_not_invent_untracked_attribute_changes() -> None:
    current = {label: 72 for label in DISPLAY_TO_SEMANTIC}
    current.update({"Speed": 91, "Acceleration": 90, "Agility": 89, "Face-offs": 60})
    result = stabilize_recommendations(
        {
            "Passing": 74,
            "Off. Awareness": 74,
            "Def. Awareness": 72,
            "Stick Checking": 71,
        },
        current,
        position="RW",
        target_overall=78,
    )

    assert "Speed" not in result
    assert "Acceleration" not in result
    assert "Agility" not in result
