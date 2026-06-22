from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from flask import Flask, redirect, render_template, request, url_for

from .capwages import fetch_capwages_team_contracts
from .comparison_tools import build_comparison_blend
from .contract_models import DEFAULT_REAL_CAP_MILLIONS
from .contract_sync import build_contract_update_queue
from .editor_state import default_trade_state, load_json_state, save_json_state
from .hockeydb import HOCKEYDB_BASE, fetch_hockeydb_profile_by_name
from .move_tools import get_player_current_team, move_player_to_team, move_player_to_team_code
from .nhl_remote import fetch_player_landing, fetch_trade_headlines, find_player_on_official_rosters
from .player_editing import build_player_attribute_rows, parse_attribute_form_updates, update_player_ratings
from .player_tools import get_player_snapshot
from .roster_sync import build_capwages_roster_update
from .roster_views import build_team_collections, filter_player_index, load_player_index
from .workspace import append_change_log, create_workspace, load_active_workspace, read_change_log, sync_working_db_to_roster


FREE_AGENCY_CODE = 255
FREE_AGENCY_LABEL = "Free Agency / Unassigned"
SCREEN_TABS = [
    ("movement", "Player Movement"),
    ("player", "Edit Player"),
    ("contracts", "Contracts"),
    ("create", "Create Player"),
    ("draft", "Draft Picks"),
    ("updates", "Roster Updates"),
    ("review", "Final Review"),
]
LEAGUE_OPTIONS = [
    ("all", "All Leagues"),
    ("nhl", "NHL"),
    ("prospects", "Prospects"),
    ("other_leagues", "Other Leagues"),
    ("free_agents", "Free Agents"),
]
TEAM_SLUGS = {
    "ANA": "anaheim_ducks",
    "BOS": "boston_bruins",
    "BUF": "buffalo_sabres",
    "CAR": "carolina_hurricanes",
    "CBJ": "columbus_blue_jackets",
    "CGY": "calgary_flames",
    "CHI": "chicago_blackhawks",
    "COL": "colorado_avalanche",
    "DAL": "dallas_stars",
    "DET": "detroit_red_wings",
    "EDM": "edmonton_oilers",
    "FLA": "florida_panthers",
    "LAK": "los_angeles_kings",
    "MIN": "minnesota_wild",
    "MTL": "montreal_canadiens",
    "NJD": "new_jersey_devils",
    "NSH": "nashville_predators",
    "NYI": "new_york_islanders",
    "NYR": "new_york_rangers",
    "OTT": "ottawa_senators",
    "PHI": "philadelphia_flyers",
    "PIT": "pittsburgh_penguins",
    "SEA": "seattle_kraken",
    "SJS": "san_jose_sharks",
    "STL": "st_louis_blues",
    "TB": "tampa_bay_lightning",
    "TBL": "tampa_bay_lightning",
    "TOR": "toronto_maple_leafs",
    "UTA": "utah_mammoth",
    "VAN": "vancouver_canucks",
    "VGK": "vegas_golden_knights",
    "WPG": "winnipeg_jets",
    "WSH": "washington_capitals",
}
EDGE_LINKS = [
    ("Skaters", "https://www.nhl.com/nhl-edge/skaters"),
    ("Comparisons", "https://www.nhl.com/nhl-edge/comparisons"),
]


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent / "templates"))
    app.config["SECRET_KEY"] = "nhl-legacy-local-editor"

    @app.get("/")
    def index():
        return render_app()

    @app.post("/workspace/open")
    def workspace_open():
        roster_path = Path(request.form["roster_path"]).expanduser()
        workspace = create_workspace(roster_path)
        return render_app(workspace=workspace, message=f"Opened workspace {workspace.name}.")

    @app.post("/player/move")
    def player_move():
        workspace = require_workspace()
        first_name = request.form["first_name"].strip()
        last_name = request.form["last_name"].strip()
        target_team = request.form["target_team"].strip().upper()
        result = move_player_to_team(workspace.working_db, first_name, last_name, target_team)
        sync_working_db_to_roster(workspace)
        log_action(workspace, "move-player", result)
        return render_app(
            workspace=workspace,
            screen="movement",
            selected_player=f"{first_name} {last_name}",
            message=f"Moved {first_name} {last_name} to {target_team}.",
        )

    @app.post("/player/free-agent")
    def player_free_agent():
        workspace = require_workspace()
        first_name = request.form["first_name"].strip()
        last_name = request.form["last_name"].strip()
        result = move_player_to_team_code(workspace.working_db, first_name, last_name, FREE_AGENCY_CODE)
        sync_working_db_to_roster(workspace)
        log_action(workspace, "move-to-free-agency", result)
        return render_app(
            workspace=workspace,
            screen="movement",
            selected_player=f"{first_name} {last_name}",
            message=f"Moved {first_name} {last_name} to {FREE_AGENCY_LABEL}.",
        )

    @app.post("/trade/stage-player")
    def trade_stage_player():
        workspace = require_workspace()
        state = load_json_state(workspace, "trade_state.json", default_trade_state())
        player_name = request.form["player_name"].strip()
        team_left = request.form["team_left"].strip().upper()
        team_right = request.form["team_right"].strip().upper()
        direction = request.form["direction"].strip()
        state["team_left"] = team_left
        state["team_right"] = team_right
        bucket = "to_right" if direction == "to_right" else "to_left"
        other_bucket = "to_left" if bucket == "to_right" else "to_right"
        state[other_bucket] = [item for item in state[other_bucket] if item.get("player_name") != player_name]
        if not any(item.get("player_name") == player_name for item in state[bucket]):
            state[bucket].append({"player_name": player_name})
        save_json_state(workspace, "trade_state.json", state)
        return redirect(_screen_url("movement", selected_player=player_name, lane_left=team_left, lane_right=team_right))

    @app.post("/trade/remove-player")
    def trade_remove_player():
        workspace = require_workspace()
        state = load_json_state(workspace, "trade_state.json", default_trade_state())
        player_name = request.form["player_name"].strip()
        bucket = request.form["bucket"].strip()
        if bucket in {"to_left", "to_right"}:
            state[bucket] = [item for item in state[bucket] if item.get("player_name") != player_name]
            save_json_state(workspace, "trade_state.json", state)
        return redirect(_screen_url("movement", lane_left=state["team_left"], lane_right=state["team_right"]))

    @app.post("/trade/add-pick")
    def trade_add_pick():
        workspace = require_workspace()
        state = load_json_state(workspace, "trade_state.json", default_trade_state())
        pick = {
            "year": request.form["year"].strip(),
            "round": request.form["round"].strip(),
            "from_team": request.form["from_team"].strip().upper(),
            "to_team": request.form["to_team"].strip().upper(),
            "note": request.form.get("note", "").strip(),
        }
        state["picks"].append(pick)
        save_json_state(workspace, "trade_state.json", state)
        return redirect(_screen_url("movement", lane_left=state["team_left"], lane_right=state["team_right"]))

    @app.post("/trade/apply")
    def trade_apply():
        workspace = require_workspace()
        state = load_json_state(workspace, "trade_state.json", default_trade_state())
        team_left = state["team_left"]
        team_right = state["team_right"]
        applied = []
        for item in state["to_left"]:
            first, last = item["player_name"].split(" ", 1)
            result = move_player_to_team(workspace.working_db, first, last, team_left)
            applied.append(result)
            log_action(workspace, "trade-player", result)
        for item in state["to_right"]:
            first, last = item["player_name"].split(" ", 1)
            result = move_player_to_team(workspace.working_db, first, last, team_right)
            applied.append(result)
            log_action(workspace, "trade-player", result)
        if state["picks"]:
            ledger = load_json_state(workspace, "draft_pick_ledger.json", [])
            stamp = datetime.now().isoformat()
            for pick in state["picks"]:
                entry = {"timestamp": stamp, **pick}
                ledger.append(entry)
                log_action(workspace, "draft-pick-move", entry)
            save_json_state(workspace, "draft_pick_ledger.json", ledger)
        sync_working_db_to_roster(workspace)
        save_json_state(workspace, "trade_state.json", default_trade_state() | {"team_left": team_left, "team_right": team_right})
        return render_app(
            workspace=workspace,
            screen="movement",
            message=f"Applied {len(applied)} player moves and {len(state['picks'])} draft-pick entries.",
            lane_left=team_left,
            lane_right=team_right,
        )

    @app.post("/player/ratings/save")
    def player_ratings_save():
        workspace = require_workspace()
        first_name = request.form["first_name"].strip()
        last_name = request.form["last_name"].strip()
        updates = parse_attribute_form_updates(request.form)
        result = update_player_ratings(workspace.working_db, first_name, last_name, updates)
        sync_working_db_to_roster(workspace)
        log_action(workspace, "update-ratings", result)
        return render_app(
            workspace=workspace,
            screen="player",
            selected_player=f"{first_name} {last_name}",
            message=f"Saved {len(updates)} attribute values for {first_name} {last_name}.",
        )

    @app.post("/comparison/build")
    def comparison_build():
        workspace = require_workspace()
        target_name = request.form["target_name"].strip()
        source_names = [item.strip() for item in request.form["source_names"].splitlines() if item.strip()]
        archetype = request.form["archetype"].strip()
        target_overall = int(request.form["target_overall"])
        result = build_comparison_blend(
            workspace.working_db,
            target_name=target_name,
            source_names=source_names,
            archetype=archetype,
            target_overall=target_overall,
        )
        return render_app(
            workspace=workspace,
            screen="create",
            selected_player=target_name,
            comparison_result=result,
        )

    @app.post("/contracts/build")
    def contracts_build():
        workspace = require_workspace()
        player_index = load_player_index(workspace.working_db)
        real_cap = float(request.form.get("real_cap") or DEFAULT_REAL_CAP_MILLIONS)
        game_cap = float(request.form.get("game_cap") or 78.6)
        queue = build_contract_update_queue(
            player_index,
            team_slugs=TEAM_SLUGS,
            real_cap=real_cap,
            game_cap=game_cap,
        )
        save_json_state(workspace, "contract_queue.json", queue)
        return render_app(
            workspace=workspace,
            screen="contracts",
            message=f"Built {len(queue)} contract update proposals.",
        )

    @app.post("/contracts/approve")
    def contracts_approve():
        workspace = require_workspace()
        queue = load_json_state(workspace, "contract_queue.json", [])
        approved_names = set(request.form.getlist("approved"))
        approved = [item for item in queue if item["player_name"] in approved_names]
        save_json_state(workspace, "contract_approved.json", approved)
        log_action(
            workspace,
            "approve-contract-pass",
            {
                "count": len(approved),
                "players": [item["player_name"] for item in approved[:25]],
                "note": "Contract save-back fields are still being mapped; approvals are stored as the contract pass list.",
            },
        )
        return render_app(
            workspace=workspace,
            screen="contracts",
            message=f"Saved {len(approved)} approved contract updates for the contract pass list.",
        )

    @app.post("/updates/scan")
    def updates_scan():
        workspace = require_workspace()
        player_index = load_player_index(workspace.working_db)
        queue = build_capwages_roster_update(player_index, team_slugs=TEAM_SLUGS)
        save_json_state(workspace, "update_queue.json", queue)
        return render_app(
            workspace=workspace,
            screen="updates",
            message=(
                f"Found {len(queue['moves'])} move proposals and "
                f"{len(queue['create_candidates'])} player creation candidates."
            ),
        )

    @app.post("/updates/apply")
    def updates_apply():
        workspace = require_workspace()
        queue = load_json_state(workspace, "update_queue.json", {"moves": [], "create_candidates": []})
        approved = set(request.form.getlist("approved_moves"))
        applied_count = 0
        for item in queue.get("moves", []):
            token = f"{item['player_name']}|{item['to_team']}"
            if token not in approved:
                continue
            first, last = item["player_name"].split(" ", 1)
            result = move_player_to_team(workspace.working_db, first, last, item["to_team"])
            log_action(workspace, "auto-update-move", result)
            applied_count += 1
        save_json_state(workspace, "create_candidates.json", queue.get("create_candidates", []))
        sync_working_db_to_roster(workspace)
        return render_app(
            workspace=workspace,
            screen="updates",
            message=(
                f"Applied {applied_count} approved roster moves. "
                f"Creation candidates were sent to the Create Player screen."
            ),
        )

    @app.get("/sync")
    def sync_roster():
        workspace = require_workspace()
        sync_working_db_to_roster(workspace)
        return redirect(url_for("index"))

    return app


def require_workspace():
    workspace = load_active_workspace()
    if workspace is None:
        raise RuntimeError("No active workspace. Open a roster first.")
    return workspace


def log_action(workspace, action_type: str, result: dict[str, object]) -> None:
    append_change_log(
        workspace,
        {
            "timestamp": datetime.now().isoformat(),
            "type": action_type,
            "result": result,
        },
    )


def _safe_trade_headlines():
    try:
        return fetch_trade_headlines(limit=10)
    except Exception:
        return []


def _safe_official_hits(query: str):
    if not query:
        return []
    try:
        return find_player_on_official_rosters(query)
    except Exception:
        return []


def _safe_official_player_details(official_hits):
    if not official_hits:
        return None
    try:
        return fetch_player_landing(official_hits[0].player_id)
    except Exception:
        return None


def _safe_hockeydb(query: str):
    if not query:
        return None
    try:
        return fetch_hockeydb_profile_by_name(query)
    except Exception:
        return None


def _safe_capwages_for_team(team_abbrev: str | None):
    if not team_abbrev:
        return None
    slug = TEAM_SLUGS.get(team_abbrev.upper())
    if not slug:
        return None
    try:
        return fetch_capwages_team_contracts(slug)
    except Exception:
        return None


def _normalize_name(value: str) -> str:
    cleaned = " ".join(value.lower().split())
    if "," in cleaned:
        last, first = [part.strip() for part in cleaned.split(",", 1)]
        return " ".join((first, last)).strip()
    return cleaned.replace(",", "")


def _match_capwages_player(capwages_data, full_name: str):
    if not capwages_data or not full_name:
        return None
    target = _normalize_name(full_name)
    for bucket in ("signed", "unsigned", "reserve"):
        for row in capwages_data.get(bucket, []):
            if _normalize_name(row.name) == target:
                return row
    return None


def _team_counts(player_index):
    counts: dict[str, int] = {"ALL": len(player_index), "FREE": 0}
    for player in player_index:
        if player.current_team_abbrev:
            counts[player.current_team_abbrev] = counts.get(player.current_team_abbrev, 0) + 1
        else:
            counts["FREE"] += 1
    return counts


def _league_counts(player_index):
    counts: dict[str, int] = {}
    for player in player_index:
        counts[player.league_group] = counts.get(player.league_group, 0) + 1
    return counts


def _screen_url(screen: str, **values) -> str:
    query = {"screen": screen}
    query.update({key: value for key, value in values.items() if value not in {None, ""}})
    return url_for("index", **query)


def render_app(
    *,
    workspace=None,
    screen: str | None = None,
    selected_player: str | None = None,
    comparison_result=None,
    message: str | None = None,
    lane_left: str | None = None,
    lane_right: str | None = None,
):
    workspace = workspace or load_active_workspace()
    screen = (screen or request.args.get("screen") or "movement").strip()
    query = (selected_player or request.args.get("selected_player") or request.args.get("player_query") or "").strip()
    team_filter = (request.args.get("team_filter") or "ALL").strip()
    league_filter = (request.args.get("league_filter") or "all").strip()
    lane_left = (lane_left or request.args.get("lane_left") or "TOR").strip().upper()
    lane_right = (lane_right or request.args.get("lane_right") or "TB").strip().upper()

    player_index = []
    filtered_players = []
    left_lane_players = []
    right_lane_players = []
    team_collections = {"nhl": [], "prospects": [], "other_leagues": []}
    team_counts = {"ALL": 0, "FREE": 0}
    league_counts = {}
    snapshot = None
    current_team = None
    official_hits = []
    official_player_details = None
    hockeydb_profile = None
    capwages_data = None
    capwages_player = None
    review_entries = []
    trade_headlines = _safe_trade_headlines()
    trade_state = default_trade_state() | {"team_left": lane_left, "team_right": lane_right}
    update_queue = {"moves": [], "create_candidates": []}
    contract_queue = []
    draft_pick_ledger = []
    create_candidates = []
    attribute_rows = []
    contract_source_team = None

    if workspace is not None:
        player_index = load_player_index(workspace.working_db)
        filtered_players = filter_player_index(
            player_index,
            team_filter=team_filter,
            league_filter=league_filter,
            search=query,
        )
        left_lane_players = filter_player_index(player_index, team_filter=lane_left, league_filter="all", search="")
        right_lane_players = filter_player_index(player_index, team_filter=lane_right, league_filter="all", search="")
        team_collections = build_team_collections(workspace.working_db)
        team_counts = _team_counts(player_index)
        league_counts = _league_counts(player_index)
        review_entries = read_change_log(workspace)
        trade_state = load_json_state(workspace, "trade_state.json", default_trade_state())
        trade_state["team_left"] = lane_left
        trade_state["team_right"] = lane_right
        save_json_state(workspace, "trade_state.json", trade_state)
        update_queue = load_json_state(workspace, "update_queue.json", {"moves": [], "create_candidates": []})
        contract_queue = load_json_state(workspace, "contract_queue.json", [])
        draft_pick_ledger = load_json_state(workspace, "draft_pick_ledger.json", [])
        create_candidates = load_json_state(workspace, "create_candidates.json", update_queue.get("create_candidates", []))

        if query:
            matched = next((item for item in player_index if item.full_name.lower() == query.lower()), None)
            if matched is None:
                matched = next((item for item in filtered_players if item.full_name.lower() == query.lower()), None)
            if matched is None and filtered_players:
                matched = filtered_players[0]
            if matched is not None:
                query = matched.full_name
                snapshot = get_player_snapshot(workspace.working_db, matched.first_name, matched.last_name)
                current_team = get_player_current_team(workspace.working_db, matched.first_name, matched.last_name)
                official_hits = _safe_official_hits(query)
                official_player_details = _safe_official_player_details(official_hits)
                hockeydb_profile = _safe_hockeydb(query)
                contract_source_team = getattr(official_hits[0], "team_abbrev", None) if official_hits else matched.current_team_abbrev
                capwages_data = _safe_capwages_for_team(contract_source_team)
                capwages_player = _match_capwages_player(capwages_data, query)
                attribute_rows = build_player_attribute_rows(snapshot)

    return render_template(
        "dashboard.html",
        workspace=workspace,
        message=message,
        screen=screen,
        screen_tabs=SCREEN_TABS,
        selected_player=query,
        team_filter=team_filter,
        league_filter=league_filter,
        lane_left=lane_left,
        lane_right=lane_right,
        player_index=player_index,
        filtered_players=filtered_players,
        left_lane_players=left_lane_players,
        right_lane_players=right_lane_players,
        team_collections=team_collections,
        team_counts=team_counts,
        league_counts=league_counts,
        league_options=LEAGUE_OPTIONS,
        snapshot=snapshot,
        current_team=current_team,
        official_hits=official_hits,
        official_player_details=official_player_details,
        hockeydb_profile=hockeydb_profile,
        attribute_rows=attribute_rows,
        trade_state=trade_state,
        update_queue=update_queue,
        contract_queue=contract_queue,
        draft_pick_ledger=draft_pick_ledger,
        create_candidates=create_candidates,
        comparison_result=comparison_result,
        capwages_player=capwages_player,
        capwages_data=capwages_data,
        contract_source_team=contract_source_team,
        hockeydb_search_url=f"{HOCKEYDB_BASE}/ihdb/stats/find_player.php?full_name={quote_plus(query)}" if query else None,
        edge_links=EDGE_LINKS,
        trade_headlines=trade_headlines,
        review_entries=review_entries,
        free_agency_label=FREE_AGENCY_LABEL,
        default_real_cap=DEFAULT_REAL_CAP_MILLIONS,
    )


def run_app(host: str = "127.0.0.1", port: int = 8765) -> None:
    app = create_app()
    app.run(host=host, port=port, debug=False)
