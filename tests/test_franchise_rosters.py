from types import SimpleNamespace

import pytest

from nhl_legacy_editor.franchise_rosters import (
    FranchiseCandidate,
    build_franchise_roster_update,
    select_franchise_roster,
)
from nhl_legacy_editor.team_tools import TeamRecord


def candidate(player_id: int, position: str, overall: int, cap: float) -> FranchiseCandidate:
    return FranchiseCandidate(
        player_id=player_id,
        player_name=f"Player {player_id}",
        first_name="Player",
        last_name=str(player_id),
        organization="TOR",
        current_team_abbrev="TOA",
        current_team_code=59,
        position=position,
        overall=overall,
        cap_hit_millions=cap,
    )


def test_selection_has_best_20_player_core_and_cap_safe_backups() -> None:
    players = (
        [candidate(i, "C", 95 - i, 8.0 if i < 6 else 0.8) for i in range(18)]
        + [candidate(100 + i, "D", 90 - i, 4.0) for i in range(9)]
        + [candidate(200 + i, "G", 88 - i, 3.0) for i in range(3)]
    )

    selected = select_franchise_roster(players, cap_millions=71.4)

    assert 20 <= len(selected) <= 23
    assert sum(player.position_group == "F" for player in selected) >= 12
    assert sum(player.position_group == "D" for player in selected) >= 6
    assert sum(player.position_group == "G" for player in selected) == 2
    core_ids = {player.player_id for player in selected}
    assert set(range(12)).issubset(core_ids)
    assert set(range(100, 106)).issubset(core_ids)


def test_selection_rejects_position_shortage() -> None:
    with pytest.raises(ValueError, match="G: need 2, found 1"):
        select_franchise_roster(
            [candidate(i, "C", 80, 1.0) for i in range(12)]
            + [candidate(100 + i, "D", 80, 1.0) for i in range(6)]
            + [candidate(200, "G", 80, 1.0)]
        )


def test_external_preference_cannot_displace_a_better_core_player() -> None:
    preferred = candidate(1, "C", 78, 2.23)
    higher_unlisted = candidate(2, "C", 82, 0.95)
    pool = (
        [preferred, higher_unlisted]
        + [candidate(10 + i, "C", 80, 1.0) for i in range(11)]
        + [candidate(100 + i, "D", 80, 1.0) for i in range(6)]
        + [candidate(200 + i, "G", 80, 1.0) for i in range(2)]
    )

    selected = select_franchise_roster(pool, cap_millions=1.0, preferred_player_ids={preferred.player_id})

    assert preferred not in selected
    assert higher_unlisted in selected
    assert preferred.cap_hit_millions == 2.23


def test_over_cap_core_keeps_highest_overalls_and_adds_no_backups() -> None:
    players = (
        [candidate(i, "C", 95 - i, 8.0) for i in range(15)]
        + [candidate(100 + i, "D", 90 - i, 8.0) for i in range(8)]
        + [candidate(200 + i, "G", 88 - i, 8.0) for i in range(3)]
    )

    selected = select_franchise_roster(players, cap_millions=71.4)

    assert len(selected) == 20
    assert {player.player_id for player in selected if player.position_group == "F"} == set(range(12))
    assert {player.player_id for player in selected if player.position_group == "D"} == set(range(100, 106))


def test_builder_promotes_selected_and_demotes_extra_base_player(monkeypatch) -> None:
    base = TeamRecord(27, "TOR", "Maple Leafs", "Toronto")
    affiliate = TeamRecord(59, "TOA", "Marlies", "Toronto")
    chosen = candidate(1, "C", 90, 1.0)
    extra = FranchiseCandidate(
        player_id=2,
        player_name="Extra Player",
        first_name="Extra",
        last_name="Player",
        organization="TOR",
        current_team_abbrev="TOR",
        current_team_code=27,
        position="C",
        overall=70,
        cap_hit_millions=1.0,
    )
    selected = tuple([chosen] + [candidate(10 + i, "C", 80, 1.0) for i in range(11)]
                     + [candidate(100 + i, "D", 80, 1.0) for i in range(6)]
                     + [candidate(200 + i, "G", 80, 1.0) for i in range(2)])

    monkeypatch.setattr(
        "nhl_legacy_editor.franchise_rosters.build_franchise_candidates",
        lambda *_args, **_kwargs: list(selected) + [extra],
    )
    monkeypatch.setattr(
        "nhl_legacy_editor.franchise_rosters.select_franchise_roster",
        lambda *_args, **_kwargs: selected,
    )

    result = build_franchise_roster_update([], SimpleNamespace(), {}, [base, affiliate])

    assert any(row["player_id"] == 1 and row["to_team"] == "TOR" for row in result["moves"])
    assert any(row["player_id"] == 2 and row["to_team"] == "TOA" for row in result["moves"])
    assert result["franchise_summaries"][0]["players"] == 20
