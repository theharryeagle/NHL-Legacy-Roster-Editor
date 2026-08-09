import pytest

from nhl_legacy_editor.move_tools import plan_roster_slot_updates


def instance(instance_id: int, team: int, slot: int) -> dict[str, int]:
    return {"TWSX": instance_id, "BSXd": team, "XWot": team * 40 + slot}


def test_swap_reallocates_each_player_inside_the_destination_team_block() -> None:
    rows = [
        instance(1, 27, 0),
        instance(2, 27, 1),
        instance(3, 59, 0),
        instance(4, 59, 1),
    ]

    updates = plan_roster_slot_updates(rows, {1: 59, 2: 27})

    assert updates == {2: 27 * 40 + 1, 1: 59 * 40 + 0}


def test_unbalanced_moves_use_free_slots_without_changing_retained_slots() -> None:
    rows = [
        instance(1, 27, 0),
        instance(2, 59, 0),
        instance(3, 59, 2),
    ]

    updates = plan_roster_slot_updates(rows, {1: 27, 2: 27})

    assert updates == {1: 27 * 40 + 1, 2: 27 * 40 + 2}


def test_move_is_rejected_before_a_team_exceeds_40_player_slots() -> None:
    rows = [instance(index + 1, 27, index) for index in range(40)]
    rows.append(instance(100, 59, 0))

    with pytest.raises(RuntimeError, match="at most 40"):
        plan_roster_slot_updates(rows, {40: 27})


def test_invalid_retained_slot_is_rejected_instead_of_being_guessed() -> None:
    rows = [instance(1, 27, 0), instance(2, 59, 0)]
    rows[0]["XWot"] = 59 * 40

    with pytest.raises(RuntimeError, match="Unsafe roster slot"):
        plan_roster_slot_updates(rows, {1: 27})
