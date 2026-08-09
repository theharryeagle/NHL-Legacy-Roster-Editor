from nhl_legacy_editor.contract_sync import build_contract_update_queue
from types import SimpleNamespace

from nhl_legacy_editor.roster_sync import (
    build_capwages_roster_update,
    build_official_nhl_roster_update,
    build_puckpedia_assignment_update,
    build_player_name_indexes,
    find_player_name_matches,
    move_is_already_satisfied,
    normalize_name,
)
from nhl_legacy_editor.nhl_remote import TeamRosterPlayer
from nhl_legacy_editor.roster_views import build_player_index_from_tables, resolve_player_organization
from nhl_legacy_editor.team_tools import TeamRecord, franchise_contract_team_code, resolve_playable_nhl_team, resolve_team_abbrev


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


def test_dapp_is_not_used_as_an_alternate_player_relation_id() -> None:
    anaheim = TeamRecord(code=0, abbrev="ANA", name="Anaheim Ducks", city="Anaheim")
    entries = build_player_index_from_tables(
        bio_rows=[
            {"zIBw": 101, "DaPp": 202, "PedH": "Unlinked", "RMbQ": "Player", "WBbd": 0},
            {"zIBw": 202, "DaPp": 999, "PedH": "Linked", "RMbQ": "Player", "WBbd": 0},
        ],
        relation_rows=[{"qFky": 202, "qEfv": 77}],
        instance_rows=[{"TWSX": 77, "BSXd": 0}],
        team_by_code={0: anaheim},
    )
    by_id = {entry.player_id: entry for entry in entries}

    assert by_id[101].is_hidden
    assert by_id[101].current_team_abbrev is None
    assert by_id[202].current_team_abbrev == "ANA"


def test_duplicate_instance_id_keeps_base_nhl_row_as_primary() -> None:
    anaheim = TeamRecord(code=0, abbrev="ANA", name="Ducks", city="Anaheim")
    organization_clone = TeamRecord(code=225, abbrev="AND", name="Ducks Organization", city="Anaheim")
    entries = build_player_index_from_tables(
        bio_rows=[{"zIBw": 101, "PedH": "Clone", "RMbQ": "Player", "WBbd": 1}],
        relation_rows=[{"qFky": 101, "qEfv": 77}],
        instance_rows=[
            {"TWSX": 77, "BSXd": 0, "XWot": 0},
            {"TWSX": 77, "BSXd": 225, "XWot": 9000},
        ],
        team_by_code={0: anaheim, 225: organization_clone},
    )

    assert entries[0].current_team_abbrev == "ANA"
    assert entries[0].league_name == "NHL"


def test_franchise_builder_requires_exact_team_not_just_organization() -> None:
    player = _Player("AHL Player", 1, "TOA", "TOR", league="AHL")
    row = {
        "to_team": "TOR",
        "source": "Franchise Roster Builder",
    }

    assert not move_is_already_satisfied(row, player)


def test_exact_ahl_target_is_not_resolved_back_to_its_nhl_parent() -> None:
    anaheim = TeamRecord(code=0, abbrev="ANA", name="Ducks", city="Anaheim")
    san_diego = TeamRecord(code=32, abbrev="SD", name="Gulls", city="San Diego")

    assert resolve_team_abbrev("SD", [anaheim, san_diego]) == san_diego
    assert resolve_team_abbrev("ANA", [anaheim, san_diego]) == anaheim


def test_ahl_move_keeps_parent_nhl_contract_team_code() -> None:
    toronto = TeamRecord(code=27, abbrev="TOR", name="Maple Leafs", city="Toronto")
    marlies = TeamRecord(code=59, abbrev="TOA", name="Marlies", city="Toronto")

    assert franchise_contract_team_code(marlies, [toronto, marlies]) == 28
    assert franchise_contract_team_code(toronto, [toronto, marlies]) == 28


def test_puckpedia_assigns_minors_and_prospects_without_demoting_official_nhl_player() -> None:
    toronto = TeamRecord(code=27, abbrev="TOR", name="Maple Leafs", city="Toronto")
    marlies = TeamRecord(code=59, abbrev="TOA", name="Marlies", city="Toronto")
    system = TeamRecord(code=128, abbrev="TORS", name="Maple Leafs System", city="SCL")
    nhl_player = _Player("NHL Player", 1, "TOR", "TOR")
    minor = _Player("Minor Player", 2, "TORS", "TOR", league="Organization")
    prospect = _Player("Prospect Player", 3, "TOA", "TOR", league="AHL")
    chart = SimpleNamespace(
        team_abbrev="TOR",
        players=(
            SimpleNamespace(full_name="NHL Player", status="minors", section="Minor-League Skaters"),
            SimpleNamespace(full_name="Minor Player", status="minors", section="Minor-League Skaters"),
            SimpleNamespace(full_name="Prospect Player", status="prospects", section="Prospect Skaters"),
        ),
    )

    queue = build_puckpedia_assignment_update(
        [nhl_player, minor, prospect],
        [chart],
        teams=[toronto, marlies, system],
        protected_nhl_names={"NHL Player"},
    )

    moves = {row["player_id"]: row["to_team"] for row in queue["moves"]}
    assert 1 not in moves
    assert moves[2] == "TOA"
    assert moves[3] == "TORS"


def test_modern_system_team_resolves_to_playable_legacy_franchise_slot() -> None:
    legacy = TeamRecord(code=25, abbrev="SJ", name="Sharks", city="San Jose")
    system = TeamRecord(code=126, abbrev="SJS", name="Sharks System", city="San Jose")
    assert resolve_playable_nhl_team("SJS", [legacy, system]) == legacy


def test_formal_first_name_matches_one_unambiguous_roster_player() -> None:
    player = _Player("Nick Robertson", 13506, "TOR", "TOR")
    exact, equivalent = build_player_name_indexes([player])

    assert find_player_name_matches("Robertson, Nicholas", exact, equivalent) == [player]


def test_name_normalization_handles_roster_diacritics_and_dotless_i() -> None:
    assert normalize_name("Noah Östlund") == normalize_name("Noah Ostlund")
    assert normalize_name("Adam Novotnı") == normalize_name("Adam Novotny")


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


def test_official_nhl_roster_promotes_player_from_same_org_affiliate(monkeypatch) -> None:
    player = _Player("Nick Robertson", 13506, "WBS", "PIT", league="AHL")
    official = TeamRosterPlayer(
        player_id=8478427,
        team_abbrev="PIT",
        first_name="Nicholas",
        last_name="Robertson",
        position_code="L",
        shoots_catches="L",
        sweater_number=89,
    )
    monkeypatch.setattr(
        "nhl_legacy_editor.roster_sync.fetch_league_rosters",
        lambda *_args, **_kwargs: [official],
    )

    queue = build_official_nhl_roster_update([player], force_refresh=True)

    assert [(row["player_id"], row["from_team"], row["to_team"]) for row in queue["moves"]] == [
        (13506, "WBS", "PIT")
    ]


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
