from __future__ import annotations

from src.nhl_legacy_editor.draft_class import (
    _draft_match_priority,
    _draft_name_key,
    _draft_contract_updates,
    _draft_metadata_updates,
    _franchise_rights_code,
    _goalie_raw_updates,
    load_2026_draft_class,
)
from src.nhl_legacy_editor.team_tools import TeamRecord


def test_known_draft_name_variants_resolve_to_canonical_identity() -> None:
    pairs = [
        ("Caleb Malholtra", "Caleb Malhotra"),
        ("Yegor Shilov", "Egor Shilov"),
        ("Oleg Kulebyakin", "Oleg Kulebiakin"),
        ("Zach Lansard", "Zachary Lansard"),
        ("Zac Olsen", "Zach Olsen"),
        ("Viktor Fyodorov", "Viktor Fedorov"),
        ("Noel Nord", "Noel Nordh"),
    ]
    for variant, canonical in pairs:
        assert _draft_name_key(variant) == _draft_name_key(canonical)


def test_exact_canonical_name_wins_when_alias_and_canonical_both_exist() -> None:
    variant = {"PedH": "Caleb", "RMbQ": "Malholtra", "zKKG": 126}
    canonical = {"PedH": "Caleb", "RMbQ": "Malhotra", "zKKG": 126}
    assert _draft_match_priority(canonical, "Caleb Malhotra") > _draft_match_priority(
        variant,
        "Caleb Malhotra",
    )


def test_draft_goalie_consistency_uses_native_zero_to_two_scale() -> None:
    goalies = [row for row in load_2026_draft_class() if row.position == "G"]
    assert goalies
    assert all(0 <= _goalie_raw_updates(row)["KzvQ"] <= 2 for row in goalies)


def test_drafted_players_receive_franchise_stable_entry_level_contracts() -> None:
    assert _draft_contract_updates(is_undrafted=False, force_free_agent=False) == {
        "QwoG": 1,
        "dhKk": 190,
        "GDhI": 3,
        "gHmt": 1,
        "yvUt": 1,
        "DVoL": 1,
    }


def test_drafted_players_receive_exact_draft_history_without_skin_colour() -> None:
    prospect = next(row for row in load_2026_draft_class() if row.pick == 224)
    updates = _draft_metadata_updates(prospect, is_undrafted=False)
    assert updates == {"WzKY": 126, "Ujcc": 7, "WfTt": 224, "QPrV": 0}
    assert "QDTK" not in updates


def test_expansion_free_agents_do_not_receive_team_contracts() -> None:
    assert _draft_contract_updates(is_undrafted=False, force_free_agent=True) == {
        "QwoG": 0,
        "dhKk": 0,
        "GDhI": 0,
        "gHmt": 0,
        "yvUt": 0,
        "DVoL": 0,
    }


def test_high_code_modern_teams_use_playable_five_bit_franchise_rights() -> None:
    teams = [
        TeamRecord(13, "LA", "Kings", "Los Angeles"),
        TeamRecord(25, "SJ", "Sharks", "San Jose"),
        TeamRecord(126, "SJS", "Sharks System", "San Jose"),
        TeamRecord(227, "LAK", "Kings System", "Los Angeles"),
        TeamRecord(228, "SEA", "Kraken", "Seattle"),
    ]
    assert _franchise_rights_code(teams[3], teams) == 14
    assert _franchise_rights_code(teams[2], teams) == 26
    assert _franchise_rights_code(teams[4], teams) == 0
