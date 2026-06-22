from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen


NHL_API_BASE = "https://api-web.nhle.com/v1"
NHL_WEB_BASE = "https://www.nhl.com"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TEAM_ABBREVIATIONS = (
    "ANA",
    "BOS",
    "BUF",
    "CAR",
    "CBJ",
    "CGY",
    "CHI",
    "COL",
    "DAL",
    "DET",
    "EDM",
    "FLA",
    "LAK",
    "MIN",
    "MTL",
    "NJD",
    "NSH",
    "NYI",
    "NYR",
    "OTT",
    "PHI",
    "PIT",
    "SJS",
    "SEA",
    "STL",
    "TBL",
    "TOR",
    "UTA",
    "VAN",
    "VGK",
    "WPG",
    "WSH",
)
_LEAGUE_ROSTER_CACHE: dict[tuple[str, ...], list["TeamRosterPlayer"]] = {}


@dataclass(slots=True)
class TeamRosterPlayer:
    player_id: int
    team_abbrev: str
    first_name: str
    last_name: str
    position_code: str
    shoots_catches: str
    sweater_number: int | None
    headshot: str | None = None
    height_in_inches: int | None = None
    weight_in_pounds: int | None = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass(slots=True)
class TransactionHeadline:
    title: str
    url: str
    published_at: datetime | None


class _TradeCoverageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[TransactionHeadline] = []
        self._current_href: str | None = None
        self._capture_title = False
        self._title_parts: list[str] = []
        self._published_at: datetime | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "a" and attrs_dict.get("class", "").startswith("nhl-c-card-wrap"):
            href = attrs_dict.get("href")
            self._current_href = f"{NHL_WEB_BASE}{href}" if href else None
            self._published_at = None
            self._title_parts = []
        elif tag == "h3" and attrs_dict.get("class") == "fa-text__title":
            self._capture_title = True
        elif tag == "time":
            raw = attrs_dict.get("datetime")
            if raw:
                try:
                    self._published_at = datetime.fromisoformat(raw)
                except ValueError:
                    self._published_at = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._capture_title = False
        elif tag == "a" and self._current_href and self._title_parts:
            title = unescape("".join(self._title_parts)).strip()
            if title:
                self.items.append(
                    TransactionHeadline(
                        title=title,
                        url=self._current_href,
                        published_at=self._published_at,
                    )
                )
            self._current_href = None
            self._title_parts = []
            self._published_at = None

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_parts.append(data)


def _fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urlopen(request, timeout=20) as response:
        return response.read()


def fetch_json(url: str) -> dict:
    return json.loads(_fetch_bytes(url).decode("utf-8"))


def fetch_player_landing(player_id: int) -> dict:
    return fetch_json(f"{NHL_API_BASE}/player/{player_id}/landing")


def fetch_edge_skater_detail(player_id: int) -> dict:
    return fetch_json(f"{NHL_API_BASE}/edge/skater-detail/{player_id}/now")


def fetch_team_roster(team_abbrev: str) -> list[TeamRosterPlayer]:
    normalized = team_abbrev.upper()
    payload = fetch_json(f"{NHL_API_BASE}/roster/{normalized}/current")
    players: list[TeamRosterPlayer] = []
    for section_name in ("forwards", "defensemen", "goalies"):
        for entry in payload.get(section_name, []):
            players.append(
                TeamRosterPlayer(
                    player_id=entry["id"],
                    team_abbrev=normalized,
                    first_name=entry["firstName"]["default"],
                    last_name=entry["lastName"]["default"],
                    position_code=entry["positionCode"],
                    shoots_catches=entry.get("shootsCatches", ""),
                    sweater_number=entry.get("sweaterNumber"),
                    headshot=entry.get("headshot"),
                    height_in_inches=entry.get("heightInInches"),
                    weight_in_pounds=entry.get("weightInPounds"),
                )
            )
    return players


def fetch_league_rosters(team_abbrevs: tuple[str, ...] = TEAM_ABBREVIATIONS, *, use_cache: bool = True) -> list[TeamRosterPlayer]:
    cache_key = tuple(team_abbrevs)
    if use_cache and cache_key in _LEAGUE_ROSTER_CACHE:
        return list(_LEAGUE_ROSTER_CACHE[cache_key])
    all_players: list[TeamRosterPlayer] = []
    for team_abbrev in team_abbrevs:
        all_players.extend(fetch_team_roster(team_abbrev))
    if use_cache:
        _LEAGUE_ROSTER_CACHE[cache_key] = list(all_players)
    return all_players


def find_player_on_official_rosters(query: str) -> list[TeamRosterPlayer]:
    normalized = query.strip().lower()
    if not normalized:
        return []
    return [
        player
        for player in fetch_league_rosters()
        if normalized in player.full_name.lower()
    ]


def export_rosters_to_csv(players: list[TeamRosterPlayer], output_path: Path) -> Path:
    rows = [
        "player_id,team_abbrev,first_name,last_name,full_name,position_code,shoots_catches,sweater_number"
    ]
    for player in players:
        safe_name = player.full_name.replace('"', '""')
        rows.append(
            ",".join(
                [
                    str(player.player_id),
                    player.team_abbrev,
                    player.first_name,
                    player.last_name,
                    f'"{safe_name}"',
                    player.position_code,
                    player.shoots_catches,
                    "" if player.sweater_number is None else str(player.sweater_number),
                ]
            )
        )
    output_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return output_path


def fetch_trade_headlines(limit: int = 10) -> list[TransactionHeadline]:
    html = _fetch_bytes(f"{NHL_WEB_BASE}/news/topic/trade-coverage/").decode("utf-8", errors="replace")
    parser = _TradeCoverageParser()
    parser.feed(html)

    unique: list[TransactionHeadline] = []
    seen: set[str] = set()
    for item in parser.items:
        if item.url in seen:
            continue
        seen.add(item.url)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def find_trade_tracker_url(season: str = "2025-26") -> str | None:
    html = _fetch_bytes(f"{NHL_WEB_BASE}/news/topic/trade-coverage/").decode("utf-8", errors="replace")
    match = re.search(r'href="(?P<href>/news/[^"]*%s-nhl-trades)"' % re.escape(season), html)
    if not match:
        return None
    return f"{NHL_WEB_BASE}{match.group('href')}"
