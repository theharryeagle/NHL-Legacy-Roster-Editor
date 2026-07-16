from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import time
from urllib.request import Request, urlopen
import re

from .moneypuck_scraper import writable_data_dir


CAPWAGES_BASE = "https://capwages.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CAPWAGES_CACHE_TTL_SECONDS = 60 * 60 * 6


@dataclass(slots=True)
class CapWagesPlayerContract:
    name: str
    position: str | None
    age: int | None
    cap_hit: str | None
    aav: str | None
    expiry: str | None
    status: str | None
    clause: str | None
    contract_type: str | None
    signing_team: str | None
    current_team: str | None
    acquired: str | None
    acquisition_details: str | None
    drafted_by: str | None
    draft_year: int | None
    draft_round: int | None
    draft_overall: int | None
    born: str | None
    shoots_catches: str | None
    current_detail: dict[str, str] | None
    term_years: int | None


@dataclass(slots=True)
class CapWagesDraftPick:
    year: int
    round: int
    original_team: str
    is_traded_away: bool
    conditions: str | None
    traded_date: str | None
    trade_id: str | None
    trade_details: str | None


def _fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def _extract_next_data(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(?P<data>.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("CapWages page did not expose __NEXT_DATA__.")
    return json.loads(match.group("data"))


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def fetch_capwages_team_page(team_slug: str, *, force_refresh: bool = False) -> dict:
    cache_dir = writable_data_dir("capwages")
    cache_path = cache_dir / f"{team_slug}.json"
    cached_data = None
    if cache_path.exists():
        try:
            cached_data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached_data = None
        if (
            not force_refresh
            and cached_data is not None
            and time.time() - cache_path.stat().st_mtime < CAPWAGES_CACHE_TTL_SECONDS
        ):
            return cached_data
    try:
        html = _fetch_text(f"{CAPWAGES_BASE}/teams/{team_slug}")
        data = _extract_next_data(html)
    except Exception:
        if cached_data is not None:
            return cached_data
        raise
    try:
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        # Live data is still usable when a packaged app cannot update its cache.
        pass
    return data


def find_capwages_team_blocks(team_slug: str, *, force_refresh: bool = False) -> dict[str, list[dict]]:
    data = fetch_capwages_team_page(team_slug, force_refresh=force_refresh)
    blocks: dict[str, list[dict]] = {"signed": [], "unsigned": [], "reserve": []}
    seen_rows: set[tuple[str, str, str]] = set()
    for item in _walk_json(data):
        if not isinstance(item, dict):
            continue
        bucket = None
        if item.get("name") and isinstance(item.get("contracts"), list) and item.get("currentTeamSlug") == team_slug:
            bucket = "signed"
        elif item.get("name") and item.get("mustSignBy") is not None and item.get("draftedBy") is not None:
            bucket = "unsigned"
        elif item.get("name") and (item.get("rights") is not None or item.get("reserve") is not None):
            bucket = "reserve"
        if bucket is None:
            continue
        identity = next(
            (
                str(item.get(field))
                for field in ("playerId", "nhlId", "slug", "url", "href", "id")
                if item.get(field) not in (None, "")
            ),
            "|".join(
                str(item.get(field) or "").strip().casefold()
                for field in ("name", "born", "pos")
            ),
        )
        row_key = (bucket, str(item.get("currentTeamSlug") or team_slug), identity)
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)
        blocks[bucket].append(item)
    return blocks


def _safe_int(value) -> int | None:
    try:
        return None if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return None


def _extract_year(value: object) -> int | None:
    if value in (None, ""):
        return None
    match = re.search(r"(20\d{2})", str(value))
    return None if match is None else int(match.group(1))


def _current_league_year() -> int:
    today = date.today()
    return today.year if today.month >= 7 else today.year - 1


def _current_contract_detail(
    row: dict,
    *,
    as_of_year: int | None = None,
) -> tuple[dict | None, dict | None]:
    """Choose the contract active in the requested league year.

    CapWages may expose an expired contract and its extension in the same row.
    Selecting the first item can therefore write the old one-year term over a
    newly active multi-year deal.
    """
    as_of_year = _current_league_year() if as_of_year is None else as_of_year
    candidates: list[tuple[tuple[int, int, int, int], dict, dict]] = []
    for index, contract in enumerate(row.get("contracts", [])):
        details = [detail for detail in (contract.get("details") or []) if isinstance(detail, dict)]
        if not details:
            continue
        detail_pairs = [
            (
                _extract_year(
                    detail.get("season")
                    or detail.get("year")
                    or detail.get("seasonEnd")
                    or detail.get("leagueYear")
                ),
                detail,
            )
            for detail in details
        ]
        dated = [(year, detail) for year, detail in detail_pairs if year is not None]
        current = next((detail for year, detail in dated if year == as_of_year), None)
        future = sorted((year, detail) for year, detail in dated if year >= as_of_year)
        selected_detail = current or (future[0][1] if future else details[0])
        latest_year = max((year for year, _detail in dated), default=-1)
        if current is not None:
            state = 2
            start_priority = 0
        elif future:
            state = 1
            start_priority = -future[0][0]
        else:
            state = 0
            start_priority = latest_year
        rank = (state, start_priority, latest_year, -index)
        candidates.append((rank, contract, selected_detail))
    if not candidates:
        return None, None
    _rank, contract, detail = max(candidates, key=lambda item: item[0])
    return contract, detail


def estimate_remaining_contract_years(
    contract: dict | None,
    row: dict,
    *,
    as_of_year: int | None = None,
) -> int | None:
    """Estimate the in-game remaining term from CapWages data.

    CapWages sometimes exposes every year of a contract in details, and
    sometimes the page data we can scrape only contains the active year. The
    game needs remaining years, so use the expiry season when it gives a
    stronger signal than the scraped detail count.
    """
    if as_of_year is None:
        as_of_year = _current_league_year()
    details = [] if contract is None else list(contract.get("details") or [])
    detail_years = [
        year
        for detail in details
        for year in (_extract_year(detail.get("season") or detail.get("year") or detail.get("seasonEnd") or detail.get("leagueYear")),)
        if year is not None
    ]
    detail_remaining = sum(1 for year in detail_years if year >= as_of_year) if detail_years else len(details) or None
    expiry_year = _extract_year((contract or {}).get("expiryStatus") or row.get("expiry") or row.get("mustSignBy"))
    expiry_remaining = None if expiry_year is None else max(0, expiry_year - as_of_year)
    candidates = [value for value in (detail_remaining, expiry_remaining) if value not in (None, 0)]
    return None if not candidates else max(1, min(15, max(candidates)))


def normalize_contract_rows(
    rows: list[dict],
    *,
    status: str,
    as_of_year: int | None = None,
) -> list[CapWagesPlayerContract]:
    normalized: list[CapWagesPlayerContract] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        name = row.get("name") or row.get("playerName") or row.get("fullName")
        if not name:
            continue
        row_key = (
            str(name).strip().casefold(),
            str(row.get("currentTeamSlug") or row.get("currentTeam") or "").strip().casefold(),
            str(row.get("born") or "").strip().casefold(),
            status,
        )
        if row_key in seen:
            continue
        seen.add(row_key)
        contract, current_detail = _current_contract_detail(row, as_of_year=as_of_year)
        expiry_value = (contract or {}).get("expiryStatus") or row.get("expiry") or row.get("mustSignBy")
        normalized.append(
            CapWagesPlayerContract(
                name=str(name),
                position=None if row.get("pos") is None else str(row.get("pos")),
                age=_safe_int(row.get("age")),
                cap_hit=None
                if current_detail is None or current_detail.get("capHit") is None
                else str(current_detail.get("capHit")),
                aav=None
                if current_detail is None or current_detail.get("aav") is None
                else str(current_detail.get("aav")),
                expiry=None if expiry_value in (None, "") else str(expiry_value),
                status=status,
                clause=None
                if current_detail is None
                else str(current_detail.get("clause") or row.get("terms") or ""),
                contract_type=None if contract is None else str(contract.get("type") or ""),
                signing_team=None if contract is None else str(contract.get("signingTeam") or ""),
                current_team=None if row.get("currentTeam") is None else str(row.get("currentTeam")),
                acquired=None if row.get("acquired") is None else str(row.get("acquired")),
                acquisition_details=None
                if row.get("acquisitionDetails") is None
                else str(row.get("acquisitionDetails")),
                drafted_by=None if row.get("draftedBy") is None else str(row.get("draftedBy")),
                draft_year=_safe_int(row.get("draftYear") or row.get("draft_year")),
                draft_round=_safe_int(row.get("round")),
                draft_overall=_safe_int(row.get("overall")),
                born=None if row.get("born") is None else str(row.get("born")),
                shoots_catches=None if row.get("shootsCatches") is None else str(row.get("shootsCatches")),
                current_detail=None if current_detail is None else {
                    str(key): str(value) for key, value in current_detail.items() if value is not None
                },
                term_years=estimate_remaining_contract_years(contract, row, as_of_year=as_of_year),
            )
        )
    return normalized


def fetch_capwages_team_contracts(
    team_slug: str,
    *,
    force_refresh: bool = False,
) -> dict[str, list[CapWagesPlayerContract]]:
    blocks = find_capwages_team_blocks(team_slug, force_refresh=force_refresh)
    return {
        "signed": normalize_contract_rows(blocks["signed"], status="signed"),
        "unsigned": normalize_contract_rows(blocks["unsigned"], status="unsigned"),
        "reserve": normalize_contract_rows(blocks["reserve"], status="reserve"),
    }


def _condition_text(value) -> str | None:
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [text for item in value if (text := _condition_text(item))]
        return "; ".join(parts) or None
    if isinstance(value, dict):
        preferred = value.get("description") or value.get("text") or value.get("condition")
        if preferred:
            return str(preferred).strip() or None
        return "; ".join(f"{key}: {item}" for key, item in value.items()) or None
    return str(value).strip() or None


def fetch_capwages_team_draft_picks(team_slug: str) -> list[CapWagesDraftPick]:
    data = fetch_capwages_team_page(team_slug)
    raw_rows: list[dict] = []
    for item in _walk_json(data):
        picks = item.get("draftPicks") if isinstance(item, dict) else None
        if not isinstance(picks, list):
            continue
        if item.get("team") == team_slug:
            raw_rows = [row for row in picks if isinstance(row, dict)]
            break
        if not raw_rows:
            raw_rows = [row for row in picks if isinstance(row, dict)]

    normalized: list[CapWagesDraftPick] = []
    seen: set[tuple[object, ...]] = set()
    for row in raw_rows:
        year = _safe_int(row.get("year"))
        round_number = _safe_int(row.get("round"))
        original_team = str(row.get("team") or "").strip()
        if year is None or round_number is None or not original_team:
            continue
        key = (year, round_number, original_team, bool(row.get("isTradedAway")), row.get("tradeId"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            CapWagesDraftPick(
                year=year,
                round=round_number,
                original_team=original_team,
                is_traded_away=bool(row.get("isTradedAway")),
                conditions=_condition_text(row.get("conditions")),
                traded_date=None if row.get("tradedDate") is None else str(row.get("tradedDate")),
                trade_id=None if row.get("tradeId") is None else str(row.get("tradeId")),
                trade_details=None if row.get("tradeDetails") is None else str(row.get("tradeDetails")),
            )
        )
    return sorted(normalized, key=lambda pick: (pick.year, pick.round, pick.original_team))
