from __future__ import annotations

from src.nhl_legacy_editor.draft_class import (
    _draft_match_priority,
    _draft_name_key,
)


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
