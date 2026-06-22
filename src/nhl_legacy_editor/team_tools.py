from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tdb_access import TdbAccess


TEAM_TABLE_INDEX = 0
PLAYER_INSTANCE_TABLE_INDEX = 3
TEAM_CODE_FIELD = "qEfv"
TEAM_ABBREV_FIELD = "RPbr"
INSTANCE_KEY_FIELD = "TWSX"
INSTANCE_TEAM_FIELD = "BSXd"


@dataclass(slots=True)
class TeamRecord:
    code: int
    abbrev: str
    name: str
    city: str


NHL_ABBREVS = {
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET", "EDM", "FLA",
    "LA", "LAK", "MIN", "MTL", "NSH", "NJ", "NJD", "NYI", "NYR", "OTT", "PHI", "PIT",
    "SJ", "SJS", "STL", "TB", "TOR", "VAN", "WSH", "WPG", "UHC", "UTA", "VGK", "SEA",
}
ORGANIZATION_ALIASES = {
    "ANA": {"ANA", "SD", "ANAS", "AND"},
    "BOS": {"BOS", "PRO", "BOSS", "BST"},
    "BUF": {"BUF", "ROC", "BUFS"},
    "CAR": {"CAR", "CHW", "CARS", "HTF"},
    "CBJ": {"CBJ", "CLE", "CBJS"},
    "CGY": {"CGY", "CLG", "CGYS"},
    "CHI": {"CHI", "RCK", "CHIS"},
    "COL": {"COL", "COLE", "COLS"},
    "DAL": {"DAL", "TEX", "DALS"},
    "DET": {"DET", "GRR", "DETS"},
    "EDM": {"EDM", "BAKE", "BAKI", "EDMS"},
    "FLA": {"FLA", "CHR", "FLAS", "FLP"},
    "LAK": {"LA", "LAK", "ONT", "LAKS"},
    "MIN": {"MIN", "IOW", "MINS", "MNW"},
    "MTL": {"MTL", "LAV", "MTLS"},
    "NSH": {"NSH", "MIL", "NSHS"},
    "NJD": {"NJ", "NJD", "UTI", "NJS"},
    "NYI": {"NYI", "BRP", "NYIS"},
    "NYR": {"NYR", "HAR", "NYRS"},
    "OTT": {"OTT", "BELL", "OTTS", "OTS"},
    "PHI": {"PHI", "LEH", "PHIS"},
    "PIT": {"PIT", "WBS", "PITS"},
    "SJS": {"SJ", "SJS", "SJA", "SJS"},
    "STL": {"STL", "SPR", "STLS", "SLB"},
    "TB": {"TB", "SYR", "TBS"},
    "TOR": {"TOR", "TOA", "TORS"},
    "UTA": {"UHC", "UTA", "TUC", "UMS"},
    "VAN": {"VAN", "ABB", "VANS", "VNC"},
    "VGK": {"VGK", "HSK"},
    "WPG": {"WPG", "MTB", "WPGS"},
    "WSH": {"WSH", "HER", "WSHS"},
    "SEA": {"SEA", "CVF"},
}


def normalize_org_abbrev(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.upper().strip()
    alias_map = {
        "LA": "LAK",
        "NJ": "NJD",
        "SJ": "SJS",
        "TBL": "TB",
        "UHC": "UTA",
    }
    return alias_map.get(cleaned, cleaned)


def default_organization_links() -> dict[str, str]:
    links: dict[str, str] = {}
    for org, members in ORGANIZATION_ALIASES.items():
        normalized_org = normalize_org_abbrev(org)
        if normalized_org is None:
            continue
        for member in members:
            links[member.upper()] = normalized_org
    return links


def organization_for_abbrev(
    abbrev: str | None,
    custom_links: dict[str, str] | None = None,
) -> str | None:
    if abbrev is None:
        return None
    cleaned = abbrev.upper().strip()
    links = default_organization_links()
    if custom_links:
        for team_abbrev, org in custom_links.items():
            normalized_org = normalize_org_abbrev(org)
            if normalized_org:
                links[team_abbrev.upper().strip()] = normalized_org
    if cleaned in links:
        return links[cleaned]
    normalized = normalize_org_abbrev(cleaned)
    return normalized if normalized in NHL_ABBREVS else None


def load_teams(db_path: Path) -> list[TeamRecord]:
    access = TdbAccess()
    _table, _fields, rows = access.sample_records(
        db_path,
        TEAM_TABLE_INDEX,
        limit=access.list_tables(db_path)[TEAM_TABLE_INDEX].record_count,
    )
    teams: list[TeamRecord] = []
    for row in rows:
        abbrev = str(row.get(TEAM_ABBREV_FIELD) or "").strip()
        if not abbrev:
            continue
        teams.append(
            TeamRecord(
                code=int(row.get(TEAM_CODE_FIELD, -1)),
                abbrev=abbrev,
                name=str(row.get("JkmY") or "").rstrip("\ufffd"),
                city=str(row.get("ITNQ") or ""),
            )
        )
    return teams


def get_team_maps(db_path: Path) -> tuple[dict[int, TeamRecord], dict[str, TeamRecord]]:
    teams = load_teams(db_path)
    by_code = {team.code: team for team in teams}
    by_abbrev = {team.abbrev.upper(): team for team in teams}
    return by_code, by_abbrev


def league_name_for_team(team: TeamRecord | None) -> str:
    if team is None:
        return "Free Agents"
    code = team.code
    abbrev = team.abbrev.upper()
    name = team.name.lower()
    if abbrev in NHL_ABBREVS or 0 <= code <= 31:
        return "NHL"
    if 32 <= code <= 61:
        return "AHL"
    if 62 <= code <= 67 or 76 <= code <= 79 or 91 <= code <= 93 or abbrev.startswith("P2"):
        return "Prospects"
    if 68 <= code <= 100:
        return "Europe"
    if 101 <= code <= 130 or "system" in name:
        return "Organization"
    if 131 <= code <= 153:
        return "International"
    if 154 <= code <= 213:
        return "CHL / Juniors"
    if 214 <= code <= 219:
        return "World Cup"
    if 220 <= code <= 221:
        return "EASHL"
    if 222 <= code <= 235:
        return "Exhibition"
    return "Other League"


def canonical_organization_abbrev(
    team: TeamRecord | None,
    custom_links: dict[str, str] | None = None,
) -> str | None:
    if team is None:
        return None
    return organization_for_abbrev(team.abbrev, custom_links)
