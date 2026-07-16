from types import SimpleNamespace

from nhl_legacy_editor.desktop_app import (
    advanced_metric_signature,
    bulk_metric_player_in_scope,
    metric_targets_match,
)


def test_advanced_metric_signature_is_stable_for_the_same_source_target() -> None:
    first = advanced_metric_signature(
        {"Passing": 84, "Off. Awareness": 86},
        overall_baseline=85,
        season_used=2025,
        include_edge=True,
        player_kind="skater",
    )
    second = advanced_metric_signature(
        {"Off. Awareness": 86, "Passing": 84},
        overall_baseline=85,
        season_used=2025,
        include_edge=True,
        player_kind="skater",
    )

    assert first == second


def test_advanced_metric_signature_changes_with_source_mode() -> None:
    combined = advanced_metric_signature(
        {"Passing": 84},
        overall_baseline=85,
        season_used=2025,
        include_edge=True,
        player_kind="skater",
    )
    bulk = advanced_metric_signature(
        {"Passing": 84},
        overall_baseline=85,
        season_used=2025,
        include_edge=False,
        player_kind="skater",
    )

    assert combined != bulk


def test_metric_targets_match_only_after_every_target_is_applied() -> None:
    targets = {"Passing": 84, "Off. Awareness": 86}

    assert metric_targets_match({"Passing": 84, "Off. Awareness": 86}, targets)
    assert not metric_targets_match({"Passing": 84, "Off. Awareness": 85}, targets)


def test_nhl_metric_scope_includes_eligible_player_on_ahl_team() -> None:
    player = SimpleNamespace(full_name="AHL NHL Player", league_name="AHL")

    assert bulk_metric_player_in_scope(player, "NHL", {"ahl nhl player"})


def test_nhl_metric_scope_excludes_non_nhl_placement_and_ineligible_name() -> None:
    international = SimpleNamespace(full_name="NHL Veteran", league_name="International")
    ahl_without_games = SimpleNamespace(full_name="AHL Prospect", league_name="AHL")

    assert not bulk_metric_player_in_scope(international, "NHL", {"nhl veteran"})
    assert not bulk_metric_player_in_scope(ahl_without_games, "NHL", {"someone else"})
