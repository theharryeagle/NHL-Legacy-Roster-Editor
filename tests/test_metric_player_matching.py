from nhl_legacy_editor import desktop_app
from nhl_legacy_editor.desktop_app import NhlLegacyDesktopApp
from nhl_legacy_editor import moneypuck_scraper
from nhl_legacy_editor.moneypuck_scraper import MoneyPuckCSVClient, nhl_faceoff_won_taken
from nhl_legacy_editor.nhl_remote import TeamRosterPlayer


def _moneypuck_aho_rows() -> list[dict[str, object]]:
    return [
        {
            "playerId": "8478427",
            "name": "Sebastian Aho",
            "team": "CAR",
            "position": "C",
            "games_played": "79",
        },
        {
            "playerId": "8477986",
            "name": "Sebastian Aho",
            "team": "PIT",
            "position": "D",
            "games_played": "60",
        },
    ]


def test_moneypuck_same_name_match_requires_the_correct_position(tmp_path) -> None:
    client = MoneyPuckCSVClient(cache_dir=tmp_path)
    rows = _moneypuck_aho_rows()

    forward = client.find_skater("Sebastian Aho", "CAR", rows=rows, position="C")
    defenseman = client.find_skater("Sebastian Aho", "CAR", rows=rows, position="D")

    assert forward is not None
    assert forward.row["playerId"] == "8478427"
    assert defenseman is not None
    assert defenseman.row["playerId"] == "8477986"


def test_moneypuck_does_not_assign_forward_metrics_to_same_name_defenseman(tmp_path) -> None:
    client = MoneyPuckCSVClient(cache_dir=tmp_path)
    forward_only = [_moneypuck_aho_rows()[0]]

    assert client.find_skater("Sebastian Aho", None, rows=forward_only, position="D") is None


def test_moneypuck_ambiguous_same_name_match_fails_closed(tmp_path) -> None:
    client = MoneyPuckCSVClient(cache_dir=tmp_path)

    assert client.find_skater("Sebastian Aho", None, rows=_moneypuck_aho_rows()) is None


def test_edge_same_name_match_uses_position_before_list_order(monkeypatch) -> None:
    forward = TeamRosterPlayer(8478427, "CAR", "Sebastian", "Aho", "C", "L", 20)
    defenseman = TeamRosterPlayer(8477986, "PIT", "Sebastian", "Aho", "D", "L", 25)
    monkeypatch.setattr(
        desktop_app,
        "find_player_on_official_rosters",
        lambda _query: [forward, defenseman],
    )
    app = NhlLegacyDesktopApp.__new__(NhlLegacyDesktopApp)

    assert app._select_edge_hit("Sebastian Aho", None, "D") is defenseman
    assert app._select_edge_hit("Sebastian Aho", None, "C") is forward
    assert app._select_edge_hit("Sebastian Aho", None, "") is None


def test_faceoff_lookup_uses_nhl_player_id_before_duplicate_name(monkeypatch) -> None:
    monkeypatch.setitem(
        moneypuck_scraper._NHL_FACEOFF_INDEX_CACHE,
        "20252026",
        {
            "id:8478427": {"faceoffWins": 640, "totalFaceoffs": 1150},
            "id:8477986": {"faceoffWins": 0, "totalFaceoffs": 0},
        },
    )

    won, taken = nhl_faceoff_won_taken(
        {"playerId": "8478427", "name": "Sebastian Aho", "team": "CAR"},
        2025,
    )

    assert won == 640
    assert taken == 1150
