from src.nhl_legacy_editor.future_prospects import (
    expected_growth_per_season,
    projected_draft_overall,
    starting_overall_target,
)


def test_top_five_project_to_high_70s_and_low_80s() -> None:
    assert [projected_draft_overall(rank) for rank in range(1, 6)] == [81, 80, 80, 79, 78]


def test_rj_celebrini_starts_high_enough_to_reach_low_80s() -> None:
    assert expected_growth_per_season(1, 1) == 3.0
    assert starting_overall_target(
        rank=1,
        class_year=2030,
        potential_code=1,
        color_code=1,
    ) == 69


def test_later_ranked_created_players_receive_a_useful_floor() -> None:
    assert projected_draft_overall(32) == 68
    assert projected_draft_overall(64) == 60
    assert projected_draft_overall(160) == 50
