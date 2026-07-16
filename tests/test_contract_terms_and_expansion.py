from nhl_legacy_editor.capwages import estimate_remaining_contract_years
from nhl_legacy_editor.contract_models import DEFAULT_GAME_CAP_MILLIONS
from nhl_legacy_editor.roster_sync import (
    EXPANSION_DESTINATION_FREE_AGENCY,
    EXPANSION_DESTINATION_TEAMS,
    FREE_AGENCY_TARGET,
    build_capwages_roster_update,
    can_auto_apply_move_on_save,
    filter_redundant_organization_moves,
    move_is_already_satisfied,
)


def test_contract_editor_defaults_to_stock_legacy_cap():
    assert DEFAULT_GAME_CAP_MILLIONS == 71.4


def test_save_auto_apply_accepts_resolved_team_and_free_agency_destinations():
    assert can_auto_apply_move_on_save({"to_team": "TOR"}) is True
    assert can_auto_apply_move_on_save({"to_team": FREE_AGENCY_TARGET}) is True
    assert can_auto_apply_move_on_save({"to_team": ""}) is False
    assert can_auto_apply_move_on_save({}) is False


def test_free_agent_move_is_satisfied_when_player_is_already_unassigned():
    class Player:
        current_team_abbrev = None
        league_name = "Free Agents"
        is_hidden = False

    assert move_is_already_satisfied({"to_team": FREE_AGENCY_TARGET}, Player()) is True


def test_hidden_player_does_not_count_as_free_agent():
    class Player:
        current_team_abbrev = None
        league_name = "Free Agents"
        is_hidden = True

    assert move_is_already_satisfied({"to_team": FREE_AGENCY_TARGET}, Player()) is False


def test_contract_term_uses_expiry_when_scraped_details_only_show_current_year():
    contract = {
        "expiryStatus": "2034 UFA",
        "details": [{"season": "2026-27", "aav": "$4,000,000"}],
    }
    assert estimate_remaining_contract_years(contract, {}, as_of_year=2026) == 8


def test_contract_term_uses_detail_count_when_expiry_is_missing():
    contract = {
        "details": [
            {"season": "2026-27"},
            {"season": "2027-28"},
            {"season": "2028-29"},
        ],
    }
    assert estimate_remaining_contract_years(contract, {}, as_of_year=2026) == 3


def test_expansion_free_agency_mode_targets_free_agency(monkeypatch):
    class Player:
        full_name = "Test Kraken"
        player_id = 99
        current_team_abbrev = "SEA"
        organization_abbrev = "SEA"
        league_name = "NHL"
        is_hidden = False

    class Contract:
        name = "Test Kraken"
        position = "C"
        drafted_by = "SEA"
        draft_year = 2026
        status = "signed"

    def fake_fetch(_slug):
        return {"signed": [Contract()], "unsigned": [], "reserve": []}

    monkeypatch.setattr("nhl_legacy_editor.roster_sync.fetch_capwages_team_contracts", fake_fetch)
    queue = build_capwages_roster_update(
        [Player()],
        team_slugs={"SEA": "seattle_kraken"},
        expansion_destination=EXPANSION_DESTINATION_FREE_AGENCY,
    )
    assert queue["moves"][0]["to_team"] == FREE_AGENCY_TARGET


def test_expansion_team_mode_keeps_current_expansion_players_off_move_queue(monkeypatch):
    class Player:
        full_name = "Test Knight"
        player_id = 100
        current_team_abbrev = "VGK"
        organization_abbrev = "VGK"
        league_name = "NHL"
        is_hidden = False

    class Contract:
        name = "Test Knight"
        position = "C"
        drafted_by = "VGK"
        draft_year = 2026
        status = "signed"

    def fake_fetch(_slug):
        return {"signed": [Contract()], "unsigned": [], "reserve": []}

    monkeypatch.setattr("nhl_legacy_editor.roster_sync.fetch_capwages_team_contracts", fake_fetch)
    queue = build_capwages_roster_update(
        [Player()],
        team_slugs={"VGK": "vegas_golden_knights"},
        expansion_destination=EXPANSION_DESTINATION_TEAMS,
    )
    assert queue["moves"] == []


def test_roster_sync_keeps_player_on_target_ahl_affiliate(monkeypatch):
    class Player:
        full_name = "Toronto Prospect"
        player_id = 201
        current_team_abbrev = "TOA"
        organization_abbrev = "TOR"
        league_name = "AHL"
        is_hidden = False

    class Contract:
        name = "Toronto Prospect"
        position = "C"
        drafted_by = "TOR"
        draft_year = 2024
        status = "signed"

    monkeypatch.setattr(
        "nhl_legacy_editor.roster_sync.fetch_capwages_team_contracts",
        lambda _slug: {"signed": [Contract()], "unsigned": [], "reserve": []},
    )

    queue = build_capwages_roster_update(
        [Player()],
        team_slugs={"TOR": "toronto_maple_leafs"},
    )

    assert queue["moves"] == []


def test_roster_sync_current_team_membership_overrides_stale_rights(monkeypatch):
    class Player:
        full_name = "Stale Rights Player"
        player_id = 202
        current_team_abbrev = "TOR"
        organization_abbrev = "BUF"
        league_name = "NHL"
        is_hidden = False

    class Contract:
        name = "Stale Rights Player"
        position = "LW"
        drafted_by = "BUF"
        draft_year = 2020
        status = "signed"

    monkeypatch.setattr(
        "nhl_legacy_editor.roster_sync.fetch_capwages_team_contracts",
        lambda _slug: {"signed": [Contract()], "unsigned": [], "reserve": []},
    )

    queue = build_capwages_roster_update(
        [Player()],
        team_slugs={"TOR": "toronto_maple_leafs"},
    )

    assert queue["moves"] == []


def test_roster_sync_still_moves_player_between_different_organizations(monkeypatch):
    class Player:
        full_name = "Actual Transaction"
        player_id = 203
        current_team_abbrev = "ROC"
        organization_abbrev = "BUF"
        league_name = "AHL"
        is_hidden = False

    class Contract:
        name = "Actual Transaction"
        position = "D"
        drafted_by = "BUF"
        draft_year = 2021
        status = "signed"

    monkeypatch.setattr(
        "nhl_legacy_editor.roster_sync.fetch_capwages_team_contracts",
        lambda _slug: {"signed": [Contract()], "unsigned": [], "reserve": []},
    )

    queue = build_capwages_roster_update(
        [Player()],
        team_slugs={"TOR": "toronto_maple_leafs"},
    )

    assert [(row["from_team"], row["to_team"]) for row in queue["moves"]] == [("ROC", "TOR")]


def test_old_queue_filter_removes_redundant_affiliate_move():
    class Player:
        full_name = "Queued Marlie"
        player_id = 204
        current_team_abbrev = "TOA"
        organization_abbrev = "TOR"
        league_name = "AHL"
        is_hidden = False

    queue = {
        "moves": [
            {
                "player_name": "Queued Marlie",
                "player_id": 204,
                "from_team": "TOA",
                "to_team": "TOR",
                "source": "CapWages",
                "reason": "Old proposal",
            }
        ],
        "create_candidates": [],
    }

    assert filter_redundant_organization_moves(queue, [Player()])["moves"] == []


def test_old_queue_filter_removes_completed_free_agent_move():
    class Player:
        full_name = "Queued Free Agent"
        player_id = 205
        current_team_abbrev = None
        organization_abbrev = None
        league_name = "Free Agents"
        is_hidden = False

    queue = {
        "moves": [
            {
                "player_name": "Queued Free Agent",
                "player_id": 205,
                "from_team": "SEA",
                "to_team": FREE_AGENCY_TARGET,
                "source": "CapWages",
                "reason": "Old proposal",
            }
        ],
        "create_candidates": [],
    }

    assert filter_redundant_organization_moves(queue, [Player()])["moves"] == []
