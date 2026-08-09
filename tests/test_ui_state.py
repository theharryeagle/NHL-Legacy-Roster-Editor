from nhl_legacy_editor.ui_state import MultiSortState, SelectionState, SortKey


PLAYERS = [
    {"id": 4, "name": "Zed", "team": "BOS", "overall": 88, "potential": 4.0},
    {"id": 2, "name": "Able", "team": "BOS", "overall": 88, "potential": 4.5},
    {"id": 3, "name": "Mika", "team": "BOS", "overall": 91, "potential": 4.0},
    {"id": 1, "name": "Cale", "team": "ANA", "overall": 91, "potential": 5.0},
]


def sorted_ids(state: MultiSortState) -> list[int]:
    rows = state.sort_rows(
        PLAYERS,
        lambda row, column: row[column],
        lambda row: (row["name"], row["id"]),
    )
    return [row["id"] for row in rows]


def test_selection_survives_filtering_and_reports_hidden_rows() -> None:
    state = SelectionState({1, 2, 3})
    assert state.summary([2, 4]) == "3 selected · 2 hidden by filters"
    state.select_visible([4])
    assert state.checked == {1, 2, 3, 4}
    state.clear()
    assert state.summary([1, 2]) == "0 selected"


def test_context_menu_preserves_checked_set_only_when_clicked_row_is_checked() -> None:
    state = SelectionState({1, 3})
    assert set(state.context_targets(1)) == {1, 3}
    assert state.context_targets(2) == (2,)


def test_team_then_overall_compound_sort_has_stable_name_tie_breaker() -> None:
    state = MultiSortState([SortKey("team"), SortKey("overall", descending=True)])
    assert sorted_ids(state) == [1, 3, 2, 4]


def test_overall_then_potential_compound_sort() -> None:
    state = MultiSortState([SortKey("overall", descending=True), SortKey("potential", descending=True)])
    assert sorted_ids(state) == [1, 3, 2, 4]


def test_sort_settings_ignore_duplicates_and_unknown_columns() -> None:
    state = MultiSortState.from_setting(
        [
            {"column": "team", "descending": False},
            {"column": "team", "descending": True},
            {"column": "unknown", "descending": False},
            {"column": "overall", "descending": True},
        ],
        allowed_columns={"team", "overall"},
    )
    assert state.keys == [SortKey("team"), SortKey("overall", True)]
    assert MultiSortState.from_setting(state.to_setting()).keys == state.keys


def test_quick_sort_is_single_column_and_toggles_direction() -> None:
    state = MultiSortState([SortKey("team"), SortKey("overall", True)])
    state.quick_sort("overall", descending_default=True)
    assert state.keys == [SortKey("overall", True)]
    state.quick_sort("overall")
    assert state.keys == [SortKey("overall", False)]
