from nhl_legacy_editor.capwages import normalize_contract_rows
from nhl_legacy_editor.player_editing import (
    CONTRACT_ENTRY_LEVEL_FIELD,
    CONTRACT_MULTI_YEAR_FIELD,
)


def test_contract_fields_match_nhl_legacy_metadata():
    assert CONTRACT_MULTI_YEAR_FIELD == "gHmt"
    assert CONTRACT_ENTRY_LEVEL_FIELD == "yvUt"


def test_capwages_selects_new_active_contract_and_deduplicates_rows():
    row = {
        "name": "Raddysh, Darren",
        "currentTeamSlug": "toronto_maple_leafs",
        "currentTeam": "Toronto Maple Leafs",
        "born": "Feb. 28, 1996",
        "contracts": [
            {
                "type": "Standard Contract (Extension)",
                "expiryStatus": "UFA",
                "details": [{"season": "2025-26", "aav": "$4,000,000"}],
            },
            {
                "type": "Standard Contract",
                "expiryStatus": "UFA",
                "details": [
                    {"season": f"{year}-{str(year + 1)[-2:]}", "aav": "$8,500,000"}
                    for year in range(2026, 2034)
                ],
            },
        ],
    }

    contracts = normalize_contract_rows([row, dict(row)], status="signed", as_of_year=2026)

    assert len(contracts) == 1
    assert contracts[0].name == "Raddysh, Darren"
    assert contracts[0].aav == "$8,500,000"
    assert contracts[0].current_detail["season"] == "2026-27"
    assert contracts[0].term_years == 8
