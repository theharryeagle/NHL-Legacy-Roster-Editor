from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
import io
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)

TEAM_ALIASES = {
    "LA": "LAK",
    "L.A": "LAK",
    "L.A.": "LAK",
    "SJ": "SJS",
    "S.J": "SJS",
    "S.J.": "SJS",
    "TB": "TBL",
    "T.B": "TBL",
    "T.B.": "TBL",
    "NJ": "NJD",
    "N.J": "NJD",
    "N.J.": "NJD",
}

_NHL_FACEOFF_INDEX_CACHE: dict[str, dict[str, dict[str, Any]]] = {}
_LOWER_KEY_CACHE_FIELD = "__nhl_editor_lower_keys"


def writable_data_dir(*parts: str) -> Path:
    """Return a metrics cache folder that works from the packaged .exe too."""
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    path = root / "NHLLegacyRosterEditor" / "data"
    for part in parts:
        path /= part
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace(".", "")
    text = text.replace("â€™", "'")
    text = re.sub(r"[^a-z0-9' -]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_team(value: Any) -> str:
    text = str(value or "").strip().upper().replace(".", "")
    return TEAM_ALIASES.get(text, text)


def number(row: dict[str, Any], *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(str(raw).replace("%", "").replace(",", "").strip())
        except (TypeError, ValueError):
            continue
    lowered = row.get(_LOWER_KEY_CACHE_FIELD)
    if not isinstance(lowered, dict):
        lowered = {
            str(key).strip().lower(): value
            for key, value in row.items()
            if key != _LOWER_KEY_CACHE_FIELD
        }
        row[_LOWER_KEY_CACHE_FIELD] = lowered
    for key in keys:
        raw = lowered.get(key.strip().lower())
        if raw in (None, ""):
            continue
        try:
            return float(str(raw).replace("%", "").replace(",", "").strip())
        except (TypeError, ValueError):
            continue
    return default


def text_value(row: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        raw = row.get(key)
        if raw not in (None, ""):
            return str(raw).strip()
    lowered = row.get(_LOWER_KEY_CACHE_FIELD)
    if not isinstance(lowered, dict):
        lowered = {
            str(key).strip().lower(): value
            for key, value in row.items()
            if key != _LOWER_KEY_CACHE_FIELD
        }
        row[_LOWER_KEY_CACHE_FIELD] = lowered
    for key in keys:
        raw = lowered.get(key.strip().lower())
        if raw not in (None, ""):
            return str(raw).strip()
    return default


def player_name(row: dict[str, Any]) -> str:
    return text_value(row, "name", "playerName", "player", "skater")


def player_team(row: dict[str, Any]) -> str:
    return normalize_team(text_value(row, "team", "teamCode", "teamAbbrev"))


def player_position(row: dict[str, Any]) -> str:
    value = text_value(row, "position", "positionCode", "pos").strip().upper()
    return {
        "L": "LW",
        "R": "RW",
        "LD": "D",
        "RD": "D",
        "DEF": "D",
        "GOALIE": "G",
    }.get(value, value)


def player_nhl_id(row: dict[str, Any]) -> int | None:
    value = number(row, "playerId", "player_id", "skaterId", "goalieId")
    return int(value) if value is not None and value > 0 else None


def _position_family(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    normalized = {"L": "LW", "R": "RW", "LD": "D", "RD": "D"}.get(normalized, normalized)
    if normalized == "G":
        return "G"
    if normalized == "D":
        return "D"
    if normalized in {"C", "LW", "RW", "F"}:
        return "F"
    return ""


def games_played(row: dict[str, Any]) -> int:
    return int(number(row, "games_played", "gamesPlayed", "GP", "games", default=0) or 0)


def _per_60(row: dict[str, Any], *keys: str) -> float | None:
    value = number(row, *keys)
    ice_time_seconds = number(row, "icetime", "timeOnIce")
    if value is None or ice_time_seconds is None or ice_time_seconds <= 0:
        return None
    return float(value) / (float(ice_time_seconds) / 3600.0)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _put(row: dict[str, Any], key: str, value: float | None) -> None:
    if value is not None:
        row[key] = value
        lowered = row.get(_LOWER_KEY_CACHE_FIELD)
        if isinstance(lowered, dict):
            lowered[key.strip().lower()] = value


def enrich_moneypuck_row(row: dict[str, Any], *, kind: str) -> None:
    """Add stable derived metrics so rating formulas compare full-season rows fairly."""
    gp = games_played(row)
    ice_time_seconds = number(row, "icetime", "timeOnIce")
    if gp > 0 and ice_time_seconds is not None:
        row["_toi_per_gp"] = float(ice_time_seconds) / gp

    if kind == "goalie":
        shots = number(row, "ongoal", "shotsOnGoal")
        goals = number(row, "goals", "goalsAgainst")
        x_goals = number(row, "xGoals", "xGoalsAgainst")
        rebounds = number(row, "rebounds")
        freezes = number(row, "freeze")
        high_shots = number(row, "highDangerShots")
        high_goals = number(row, "highDangerGoals")
        medium_shots = number(row, "mediumDangerShots")
        medium_goals = number(row, "mediumDangerGoals")
        low_shots = number(row, "lowDangerShots")
        low_goals = number(row, "lowDangerGoals")
        _put(row, "_shots_against_per60", _per_60(row, "ongoal", "shotsOnGoal"))
        _put(row, "_goals_against_per60", _per_60(row, "goals", "goalsAgainst"))
        _put(row, "_xgoals_against_per60", _per_60(row, "xGoals", "xGoalsAgainst"))
        if x_goals is not None and goals is not None:
            row["_goals_saved_above_expected"] = float(x_goals) - float(goals)
            _put(row, "_gsae_per60", _per_60({"value": float(x_goals) - float(goals), "icetime": ice_time_seconds}, "value"))
        save_pct = None if shots in (None, 0) or goals is None else 1.0 - (float(goals) / float(shots))
        row["_save_pct"] = save_pct if save_pct is not None else 0.0
        expected_save_pct = None if shots in (None, 0) or x_goals is None else 1.0 - (float(x_goals) / float(shots))
        _put(row, "_expected_save_pct", expected_save_pct)
        if save_pct is not None and expected_save_pct is not None:
            row["_save_pct_above_expected"] = save_pct - expected_save_pct
        high_save = None if high_shots in (None, 0) or high_goals is None else 1.0 - (float(high_goals) / float(high_shots))
        medium_save = None if medium_shots in (None, 0) or medium_goals is None else 1.0 - (float(medium_goals) / float(medium_shots))
        low_save = None if low_shots in (None, 0) or low_goals is None else 1.0 - (float(low_goals) / float(low_shots))
        _put(row, "_high_danger_save_pct", high_save)
        _put(row, "_medium_danger_save_pct", medium_save)
        _put(row, "_low_danger_save_pct", low_save)
        _put(row, "_rebound_rate", _ratio(rebounds, shots))
        _put(row, "_freeze_rate", _ratio(freezes, shots))
        return

    row["_points"] = (number(row, "I_F_points", "points", default=0) or 0)
    row["_goals"] = number(row, "I_F_goals", "goals", default=0) or 0
    row["_xgoals"] = number(row, "I_F_xGoals", "xGoals", default=0) or 0
    row["_assists_primary"] = number(row, "I_F_primaryAssists", "primaryAssists", default=0) or 0
    row["_assists_secondary"] = number(row, "I_F_secondaryAssists", "secondaryAssists", default=0) or 0
    row["_assists"] = float(row["_assists_primary"]) + float(row["_assists_secondary"])
    row["_shots"] = number(row, "I_F_shotsOnGoal", "shotsOnGoal", default=0) or 0
    row["_shot_attempts"] = number(row, "I_F_shotAttempts", "shotAttempts", default=0) or 0
    row["_hits"] = number(row, "I_F_hits", "hits", default=0) or 0
    row["_blocks"] = number(row, "shotsBlockedByPlayer", "blockedShotAttempts", default=0) or 0
    row["_takeaways"] = number(row, "I_F_takeaways", "takeaways", default=0) or 0
    row["_giveaways"] = number(row, "I_F_giveaways", "giveaways", default=0) or 0
    row["_penalties"] = number(row, "penalties", default=0) or 0
    row["_pim"] = number(row, "I_F_penalityMinutes", "penalityMinutes", "penaltyMinutes", default=0) or 0

    for key, source in {
        "_goals_per60": ("I_F_goals", "goals"),
        "_xgoals_per60": ("I_F_xGoals", "xGoals"),
        "_shots_per60": ("I_F_shotsOnGoal", "shotsOnGoal"),
        "_shot_attempts_per60": ("I_F_shotAttempts", "shotAttempts"),
        "_primary_assists_per60": ("I_F_primaryAssists", "primaryAssists"),
        "_assists_per60": ("_assists",),
        "_points_per60": ("_points",),
        "_rebounds_per60": ("I_F_rebounds", "rebounds"),
        "_hits_per60": ("I_F_hits", "hits"),
        "_blocks_per60": ("shotsBlockedByPlayer", "blockedShotAttempts"),
        "_takeaways_per60": ("I_F_takeaways", "takeaways"),
        "_giveaways_per60": ("I_F_giveaways", "giveaways"),
        "_penalties_per60": ("penalties",),
        "_pim_per60": ("I_F_penalityMinutes", "penalityMinutes", "penaltyMinutes"),
        "_onice_xgf_per60": ("OnIce_F_xGoals",),
        "_onice_xga_per60": ("OnIce_A_xGoals",),
        "_onice_hd_xgf_per60": ("OnIce_F_highDangerxGoals",),
        "_onice_hd_xga_per60": ("OnIce_A_highDangerxGoals",),
        "_onice_ca_per60": ("OnIce_A_shotAttempts",),
        "_onice_cf_per60": ("OnIce_F_shotAttempts",),
    }.items():
        _put(row, key, _per_60(row, *source))

    goals = float(row["_goals"])
    x_goals = float(row["_xgoals"])
    row["_goals_above_expected"] = goals - x_goals
    shots = float(row["_shots"])
    row["_shooting_pct"] = goals / shots if shots > 0 else 0.0


@dataclass(frozen=True, slots=True)
class MoneyPuckMatch:
    row: dict[str, Any]
    score: int
    reason: str


class MoneyPuckCSVClient:
    BASE_URL = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{season}/regular/skaters.csv"
    GOALIE_BASE_URL = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{season}/regular/goalies.csv"

    def __init__(self, cache_dir: str | Path | None = None, timeout: int = 30) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else writable_data_dir("moneypuck")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._rows_by_season: dict[int, list[dict[str, Any]]] = {}
        self._goalie_rows_by_season: dict[int, list[dict[str, Any]]] = {}
        self._name_index_by_rows_id: dict[int, dict[str, list[dict[str, Any]]]] = {}
        self._filtered_rows_cache: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
        self._match_cache: dict[tuple[object, ...], MoneyPuckMatch | None] = {}
        self._model_cache: dict[tuple[int, str, int], MoneyPuckPercentileModel] = {}
        self._load_lock = threading.RLock()

    def _name_index(self, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        key = id(rows)
        if key not in self._name_index_by_rows_id:
            index: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                normalized = normalize_name(player_name(row))
                if normalized:
                    index.setdefault(normalized, []).append(row)
            self._name_index_by_rows_id[key] = index
        return self._name_index_by_rows_id[key]

    @staticmethod
    def season_start_year(season: str | int | None = None) -> int:
        if season is None:
            return 2025
        text = str(season).strip()
        if len(text) >= 8 and text[:4].isdigit():
            return int(text[:4])
        match = re.search(r"(20\d{2})", text)
        if match:
            return int(match.group(1))
        return int(text)

    def _load_rows(
        self,
        *,
        season: str | int,
        force_refresh: bool,
        filename_suffix: str,
        url_template: str,
        cache: dict[int, list[dict[str, Any]]],
        row_kind: str,
        situation: str | None,
    ) -> list[dict[str, Any]]:
        start_year = self.season_start_year(season)
        with self._load_lock:
            if start_year not in cache or force_refresh:
                cache_path = self.cache_dir / f"{start_year}_{filename_suffix}.csv"
                if cache_path.exists() and not force_refresh:
                    text = cache_path.read_text(encoding="utf-8-sig", errors="replace")
                else:
                    url = url_template.format(season=start_year)
                    request = Request(url, headers={"User-Agent": "NHL-Legacy-Roster-Editor/1.0"})
                    with urlopen(request, timeout=self.timeout) as response:
                        text = response.read().decode("utf-8-sig", errors="replace")
                    cache_path.write_text(text, encoding="utf-8")

                rows = list(csv.DictReader(io.StringIO(text)))
                for row in rows:
                    enrich_moneypuck_row(row, kind=row_kind)
                cache[start_year] = rows
                self._filtered_rows_cache = {
                    key: value
                    for key, value in self._filtered_rows_cache.items()
                    if not (key[0] == start_year and key[1] == row_kind)
                }
                self._match_cache.clear()
                self._model_cache = {
                    key: value
                    for key, value in self._model_cache.items()
                    if not (key[0] == start_year and key[1] == row_kind)
                }

            rows = cache[start_year]
            if situation is None:
                return rows
            wanted = situation.lower()
            filter_key = (start_year, row_kind, wanted)
            filtered = self._filtered_rows_cache.get(filter_key)
            if filtered is None:
                filtered = [row for row in rows if str(row.get("situation") or "").lower() == wanted]
                self._filtered_rows_cache[filter_key] = filtered
            return filtered

    def _find_player(
        self,
        rows: list[dict[str, Any]],
        name: str,
        team: str | None,
        *,
        min_games: int,
        allow_partial: bool,
        player_kind: str,
        position: str | None,
        nhl_player_id: int | None,
    ) -> MoneyPuckMatch | None:
        wanted_name = normalize_name(name)
        wanted_team = normalize_team(team)
        wanted_position = player_position({"position": position})
        wanted_position_family = _position_family(wanted_position)
        wanted_player_id = int(nhl_player_id) if nhl_player_id is not None and int(nhl_player_id) > 0 else None
        cache_key = (
            id(rows),
            player_kind,
            wanted_name,
            wanted_team,
            wanted_position,
            wanted_player_id,
            min_games,
            allow_partial,
        )
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]
        candidates = self._name_index(rows).get(wanted_name)
        if candidates is None and allow_partial:
            candidates = [
                row
                for row in rows
                if wanted_name
                and (
                    wanted_name in normalize_name(player_name(row))
                    or normalize_name(player_name(row)) in wanted_name
                )
            ]
        matches: list[MoneyPuckMatch] = []
        if candidates is not None:
            candidates_have_ids = wanted_player_id is not None and any(player_nhl_id(row) is not None for row in candidates)
            for row in candidates:
                if games_played(row) < min_games:
                    continue
                row_name = normalize_name(player_name(row))
                if not row_name:
                    continue
                score = 0
                reasons: list[str] = []
                if row_name == wanted_name:
                    score += 100
                    reasons.append("exact name")
                elif wanted_name and (wanted_name in row_name or row_name in wanted_name):
                    score += 70
                    reasons.append("partial name")
                else:
                    continue
                row_player_id = player_nhl_id(row)
                if candidates_have_ids:
                    if row_player_id != wanted_player_id:
                        continue
                    score += 1000
                    reasons.append("NHL player ID match")
                row_position = player_position(row)
                row_position_family = _position_family(row_position)
                if wanted_position_family and row_position_family:
                    if wanted_position_family != row_position_family:
                        continue
                    if row_position == wanted_position:
                        score += 45
                        reasons.append("position match")
                    else:
                        score += 30
                        reasons.append("position family match")
                row_team = player_team(row)
                if wanted_team and row_team:
                    if row_team == wanted_team:
                        score += 35
                        reasons.append("team match")
                    else:
                        score -= 15
                        reasons.append(f"team differs: {row_team}")
                matches.append(MoneyPuckMatch(row=row, score=score, reason=", ".join(reasons)))

        best: MoneyPuckMatch | None = None
        if matches:
            matches.sort(key=lambda match: match.score, reverse=True)
            best = matches[0]
            tied = [match for match in matches if match.score == best.score]
            identities = {
                (player_nhl_id(match.row), player_team(match.row), player_position(match.row))
                for match in tied
            }
            if len(identities) > 1:
                # A wrong player is much worse than no recommendation. The caller
                # can leave an ambiguous same-name player for manual review.
                best = None
        self._match_cache[cache_key] = best
        return best

    def load_skaters(
        self,
        season: str | int = 2025,
        *,
        force_refresh: bool = False,
        situation: str | None = "all",
    ) -> list[dict[str, Any]]:
        return self._load_rows(
            season=season,
            force_refresh=force_refresh,
            filename_suffix="skaters",
            url_template=self.BASE_URL,
            cache=self._rows_by_season,
            row_kind="skater",
            situation=situation,
        )

    def load_goalies(
        self,
        season: str | int = 2025,
        *,
        force_refresh: bool = False,
        situation: str | None = "all",
    ) -> list[dict[str, Any]]:
        return self._load_rows(
            season=season,
            force_refresh=force_refresh,
            filename_suffix="goalies",
            url_template=self.GOALIE_BASE_URL,
            cache=self._goalie_rows_by_season,
            row_kind="goalie",
            situation=situation,
        )

    def find_skater(
        self,
        name: str,
        team: str | None = None,
        *,
        season: str | int = 2025,
        min_games: int = 20,
        rows: list[dict[str, Any]] | None = None,
        allow_partial: bool = True,
        position: str | None = None,
        nhl_player_id: int | None = None,
    ) -> MoneyPuckMatch | None:
        rows = rows if rows is not None else self.load_skaters(season)
        return self._find_player(
            rows,
            name,
            team,
            min_games=min_games,
            allow_partial=allow_partial,
            player_kind="skater",
            position=position,
            nhl_player_id=nhl_player_id,
        )

    def find_goalie(
        self,
        name: str,
        team: str | None = None,
        *,
        season: str | int = 2025,
        min_games: int = 20,
        rows: list[dict[str, Any]] | None = None,
        allow_partial: bool = True,
        position: str | None = "G",
        nhl_player_id: int | None = None,
    ) -> MoneyPuckMatch | None:
        rows = rows if rows is not None else self.load_goalies(season)
        return self._find_player(
            rows,
            name,
            team,
            min_games=min_games,
            allow_partial=allow_partial,
            player_kind="goalie",
            position=position,
            nhl_player_id=nhl_player_id,
        )

    def build_percentile_model(
        self,
        season: str | int = 2025,
        *,
        min_games: int = 20,
        player_kind: str = "skater",
    ) -> "MoneyPuckPercentileModel":
        start_year = self.season_start_year(season)
        cache_key = (start_year, player_kind, min_games)
        cached = self._model_cache.get(cache_key)
        if cached is not None:
            return cached
        if player_kind == "goalie":
            rows = [row for row in self.load_goalies(start_year) if games_played(row) >= min_games]
        else:
            rows = [row for row in self.load_skaters(start_year) if games_played(row) >= min_games]
        model = MoneyPuckPercentileModel(rows)
        self._model_cache[cache_key] = model
        return model


def _season_id_for_faceoffs(season: str | int = 20252026) -> str:
    text = str(season)
    if len(text) >= 8:
        return text[:8]
    start = int(text)
    if len(text) == 4:
        return f"{start}{start + 1}"
    return "20252026"


def _nhl_faceoff_index(season: str | int = 20252026) -> dict[str, dict[str, Any]]:
    season_id = _season_id_for_faceoffs(season)
    if season_id in _NHL_FACEOFF_INDEX_CACHE:
        return _NHL_FACEOFF_INDEX_CACHE[season_id]

    cache_dir = writable_data_dir("nhl")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"faceoffs_{season_id}.json"
    try:
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            cayenne = quote(f"seasonId={season_id} and gameTypeId=2")
            url = (
                "https://api.nhle.com/stats/rest/en/skater/faceoffwins"
                f"?isAggregate=true&isGame=false&limit=-1&cayenneExp={cayenne}"
            )
            request = Request(url, headers={"User-Agent": "NHL-Legacy-Roster-Editor/1.0"})
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
            cache_path.write_text(raw, encoding="utf-8")
            data = json.loads(raw)
        rows = data.get("data", []) if isinstance(data, dict) else []
        index: dict[str, dict[str, Any]] = {}
        ambiguous_names: set[str] = set()
        for item in rows:
            name_key = normalize_name(
                item.get("skaterFullName")
                or item.get("playerFullName")
                or item.get("fullName")
                or item.get("name")
            )
            if not name_key:
                continue
            team_key = normalize_team(item.get("teamAbbrevs") or item.get("teamAbbrev") or item.get("team"))
            item_player_id = number(item, "playerId", "skaterId")
            if item_player_id is not None and item_player_id > 0:
                index[f"id:{int(item_player_id)}"] = item
            if team_key:
                index[f"{name_key}|{team_key}"] = item
            if name_key in ambiguous_names:
                continue
            if name_key in index:
                # Do not retain a name-only fallback when two NHL players share
                # that name. Team or NHL player ID must identify the player.
                index.pop(name_key, None)
                ambiguous_names.add(name_key)
            else:
                index[name_key] = item
        _NHL_FACEOFF_INDEX_CACHE[season_id] = index
        return index
    except Exception as exc:
        logger.warning("Could not load NHL faceoff data for %s: %s", season_id, exc)
        _NHL_FACEOFF_INDEX_CACHE[season_id] = {}
        return {}


def nhl_faceoff_won_taken(row: dict[str, Any], season: str | int = 20252026) -> tuple[float | None, float | None]:
    name_key = normalize_name(player_name(row))
    if not name_key:
        return None, None
    team = player_team(row)
    index = _nhl_faceoff_index(season)
    row_player_id = player_nhl_id(row)
    item = index.get(f"id:{row_player_id}") if row_player_id is not None else None
    item = item or (index.get(f"{name_key}|{team}") if team else None)
    item = item or index.get(name_key)
    if not item:
        return None, None
    won = number(item, "faceoffWins", "faceoffsWon", "totalFaceoffWins", "wins", "FOW")
    taken = number(item, "totalFaceoffs", "faceoffs", "faceoffsTaken", "totalFaceoffsTaken", "FO")
    lost = number(item, "faceoffLosses", "faceoffsLost", "totalFaceoffLosses", "losses", "FOL")
    if taken is None and won is not None and lost is not None:
        taken = won + lost
    return won, taken


def faceoff_won_taken(row: dict[str, Any]) -> tuple[float | None, float | None]:
    won = number(row, "faceoffsWon", "faceOffsWon", "faceoffs_won", "Faceoffs Won", "FaceoffsWon")
    taken = number(
        row,
        "faceoffs",
        "faceOffs",
        "faceoffsTaken",
        "faceOffsTaken",
        "totalFaceoffs",
        "Faceoffs",
        "Faceoffs Taken",
        "FaceoffsTaken",
    )
    if taken is None:
        pct = number(row, "faceoffsWonPct", "faceOffsWonPct", "% of Face Offs Won", "faceoffWinPercentage")
        lost = number(row, "faceoffsLost", "faceOffsLost", "faceoffs_lost")
        if won is not None and lost is not None:
            taken = won + lost
        elif pct is not None and won is not None and pct > 0:
            pct_unit = pct / 100 if pct > 1 else pct
            taken = won / pct_unit

    nhl_won, nhl_taken = nhl_faceoff_won_taken(row, text_value(row, "season", default="20252026"))
    if nhl_won is not None and nhl_taken is not None and (taken is None or nhl_taken > taken):
        return nhl_won, nhl_taken
    return won, taken


class MoneyPuckPercentileModel:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self._cache: dict[tuple[tuple[str, ...], bool], list[float]] = {}

    def values(self, keys: Sequence[str], *, inverse: bool = False) -> list[float]:
        cache_key = (tuple(keys), inverse)
        if cache_key in self._cache:
            return self._cache[cache_key]
        values: list[float] = []
        for row in self.rows:
            value = number(row, *keys)
            if value is not None:
                values.append(float(value))
        values.sort()
        self._cache[cache_key] = values
        return values

    def percentile(self, row: dict[str, Any], keys: Sequence[str], *, inverse: bool = False, default: float = 0.5) -> float:
        value = number(row, *keys)
        if value is None:
            return default
        values = self.values(keys, inverse=inverse)
        if not values:
            return default
        if inverse:
            # Lower raw values are better for metrics like penalties/giveaways.
            # A player with fewer events should rank higher, not lower.
            count = len(values) - bisect_left(values, value)
            return max(0.0, min(1.0, count / len(values)))
        count = bisect_right(values, value)
        return max(0.0, min(1.0, count / len(values)))


def rating_from_score(score: float, floor: int = 36, ceiling: int = 96) -> int:
    score = max(0.0, min(1.0, float(score)))
    return int(round(floor + ((ceiling - floor) * score)))
