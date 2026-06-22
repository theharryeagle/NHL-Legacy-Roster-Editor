from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.request import Request, urlopen
import re


CAPWAGES_BASE = "https://capwages.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


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


def fetch_capwages_team_page(team_slug: str) -> dict:
    html = _fetch_text(f"{CAPWAGES_BASE}/teams/{team_slug}")
    return _extract_next_data(html)


def find_capwages_team_blocks(team_slug: str) -> dict[str, list[dict]]:
    data = fetch_capwages_team_page(team_slug)
    blocks: dict[str, list[dict]] = {"signed": [], "unsigned": [], "reserve": []}
    seen_ids: set[int] = set()
    for item in _walk_json(data):
        if not isinstance(item, dict):
            continue
        row_id = id(item)
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        if item.get("name") and isinstance(item.get("contracts"), list) and item.get("currentTeamSlug") == team_slug:
            blocks["signed"].append(item)
            continue
        if item.get("name") and item.get("mustSignBy") is not None and item.get("draftedBy") is not None:
            blocks["unsigned"].append(item)
            continue
        if item.get("name") and (item.get("rights") is not None or item.get("reserve") is not None):
            blocks["reserve"].append(item)
    return blocks


def _safe_int(value) -> int | None:
    try:
        return None if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return None


def _current_contract_detail(row: dict) -> tuple[dict | None, dict | None]:
    for contract in row.get("contracts", []):
        details = contract.get("details") or []
        if details:
            return contract, details[0]
    return None, None


def normalize_contract_rows(rows: list[dict], *, status: str) -> list[CapWagesPlayerContract]:
    normalized: list[CapWagesPlayerContract] = []
    for row in rows:
        name = row.get("name") or row.get("playerName") or row.get("fullName")
        if not name:
            continue
        contract, current_detail = _current_contract_detail(row)
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
                expiry=None
                if row.get("expiry") is None and row.get("mustSignBy") is None
                else str(
                    (contract or {}).get("expiryStatus")
                    or row.get("expiry")
                    or row.get("mustSignBy")
                ),
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
            )
        )
    return normalized


def fetch_capwages_team_contracts(team_slug: str) -> dict[str, list[CapWagesPlayerContract]]:
    blocks = find_capwages_team_blocks(team_slug)
    return {
        "signed": normalize_contract_rows(blocks["signed"], status="signed"),
        "unsigned": normalize_contract_rows(blocks["unsigned"], status="unsigned"),
        "reserve": normalize_contract_rows(blocks["reserve"], status="reserve"),
    }
