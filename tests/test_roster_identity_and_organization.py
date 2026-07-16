from nhl_legacy_editor.contract_sync import build_contract_update_queue
from nhl_legacy_editor.roster_sync import (
    build_capwages_roster_update,
    build_player_name_indexes,
    find_player_name_matches,
)
from nhl_legacy_editor.roster_views import resolve_player_organization
from nhl_legacy_editor.team_tools import TeamRecord


class _Player:
    def __init__(
        self,
        name: str,
        player_id: int,
        team: str,
        organization: str,
        league: str = "NHL",
    ) -> None:
        self.full_name = name
        self.player_id = player_id
        self.current_team_abbrev = team
        self.organization_abbrev = organization
        self.league_name = league
        self.is_hidden = False


def test_active_nhl_team_overrides_stale_draft_rights_organization() -> None:
    tampa = TeamRecord(code=26, abbrev="TB", name="Tampa Bay Lightning", city="Tampa Bay")
    toronto = TeamRecord(code=27, abbrev="TOR", name="Toronto Maple Leafs", city="Toronto")

    assert resolve_player_organization(
        tampa,
        toronto,
        current_team_code=26,
        current_league_name="NHL",
    ) == "TB"


def test_junior_player_keeps_parent_rights_organization() -> None:
    barrie = TeamRecord(code=154, abbrev="BARR", name="Barrie Colts", city="Barrie")
    toronto = TeamRecord(code=27, abbrev="TOR", name="Toronto Maple Leafs", city="Toronto")

    assert resolve_player_organization(
        barrie,
        toronto,
        current_team_code=154,
        current_league_name="CHL / Juniors",
    ) == "TOR"


def test_formal_first_name_matches_one_unambiguous_roster_player() -> None:
    player = _Player("Nick Robertson", 13506, "TOR", "TOR")
    exact, equivalent = build_player_name_indexes([player])

    assert find_player_name_matches("Robertson, Nicholas", exact, equivalent) == [player]


def test_roster_sync_detects_nicholas_robertson_trade(monkeypatch) -> None:
    player = _Player("Nick Robertson", 13506, "TOR", "TOR")

    class Contract:
        name = "Robertson, Nicholas"
        position = "RW, LW"
        drafted_by = "Toronto Maple Leafs"
        draft_year = 2019
        status = "signed"

    monkeypatch.setattr(
        "nhl_legacy_editor.roster_sync.fetch_capwages_team_contracts",
        lambda _slug: {"signed": [Contract()], "unsigned": [], "reserve": []},
    )

    queue = build_capwages_roster_update(
        [player],
        team_slugs={"PIT": "pittsburgh_penguins"},
    )

    assert [(row["player_id"], row["from_team"], row["to_team"]) for row in queue["moves"]] == [
        (13506, "TOR", "PIT")
    ]
    assert queue["create_candidates"] == []


def test_contract_sync_detects_nicholas_robertson_contract(monkeypatch) -> None:
    player = _Player("Nick Robertson", 13506, "PIT", "PIT")

    class Contract:
        name = "Robertson, Nicholas"
        aav = "$3,250,000"
        cap_hit = "$3,250,000"
        clause = ""
        expiry = "RFA 2028"
        term_years = 2

    monkeypatch.setattr(
        "nhl_legacy_editor.contract_sync.fetch_capwages_team_contracts",
        lambda _slug: {"signed": [Contract()], "unsigned": [], "reserve": []},
    )

    queue = build_contract_update_queue(
        [player],
        team_slugs={"PIT": "pittsburgh_penguins"},
        real_cap=104.0,
        game_cap=71.4,
    )

    assert len(queue) == 1
    assert queue[0]["player_id"] == 13506
    assert queue[0]["team"] == "PIT"
    assert queue[0]["term_years"] == 2
