from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from .attribute_map import SKATER_ATTRIBUTE_SPECS, attribute_specs_by_field, display_to_raw, raw_to_display, specs_for_player_kind
from .attribute_mapper import LegacyAttributeMapper, RecommendationSet, blend_season_recommendations, calculate_goalie_overall, stabilize_recommendations
from .capwages import CapWagesDraftPick, fetch_capwages_team_contracts, fetch_capwages_team_draft_picks
from .contract_models import DEFAULT_GAME_CAP_MILLIONS, DEFAULT_REAL_CAP_MILLIONS, scale_contract_by_cap_percentage
from .contract_sync import build_contract_update_queue
from .draft_class import DraftRosterStatus, apply_draft_class, apply_elite_prospects_scouting, load_2026_draft_class, scan_draft_class, validate_draft_players
from .editor_state import load_json_state, save_json_state
from .hockeydb import fetch_hockeydb_profile_by_name
from .move_tools import move_player_to_team, move_player_to_team_code, move_players_to_free_agency, move_players_to_teams
from .moneypuck_scraper import MoneyPuckCSVClient, games_played, number, player_name, player_team
from .nhl_remote import fetch_edge_goalie_detail, fetch_edge_skater_detail, fetch_player_landing, find_player_on_official_rosters
from .player_editing import contract_cap_hit_millions_from_raw, update_many_player_contract_cap_hits, update_many_player_goalie_ratings, update_many_player_ratings, update_player_bio, update_player_contract_cap_hit, update_player_contract_details, update_player_flags, update_player_goalie_ratings, update_player_instance_fields, update_player_ratings
from .player_tools import PlayerSnapshotCache, build_player_snapshot_cache, get_player_snapshot
from .rating_models import ARCHETYPE_WEIGHTS, calculate_weighted_overall, fit_ratings_to_overall
from .roster_formats import validate_rosterfile
from .roster_sync import (
    EXPANSION_DESTINATION_FREE_AGENCY,
    EXPANSION_DESTINATION_TEAMS,
    FREE_AGENCY_TARGET,
    build_capwages_roster_update,
    can_auto_apply_move_on_save,
    canonical_abbrev,
    equivalent_name_key,
    filter_redundant_organization_moves,
    move_is_already_satisfied,
    normalize_name,
)
from .roster_views import PlayerListEntry, build_player_index_from_tables
from .team_tools import (
    TeamRecord,
    default_organization_links,
    league_name_for_team,
    load_teams,
    normalize_org_abbrev,
    organization_for_abbrev,
)
from .workspace import (
    ACTIVE_WORKSPACE_PATH,
    EditorWorkspace,
    append_change_log,
    append_change_logs,
    archive_and_clear_change_log,
    create_workspace,
    load_active_workspace,
    read_change_log,
    save_active_workspace,
    sync_working_db_to_roster,
)


FREE_AGENCY_CODE = 255
FREE_AGENCY_LABEL = "Free Agency / Unassigned"
APP_SETTINGS_PATH = ACTIVE_WORKSPACE_PATH.parent / "app_settings.json"
FONT_SCALE_CHOICES = {
    "Small (85%)": 0.85,
    "Compact (95%)": 0.95,
    "Standard (100%)": 1.0,
    "Large (110%)": 1.10,
    "Extra Large (125%)": 1.25,
}
DENSITY_CHOICES = ("Auto", "Compact", "Comfortable")
WINDOW_SIZE_CHOICES: dict[str, tuple[int, int] | None] = {
    "Keep current size": None,
    "Handheld 16:10 (1280 x 800)": (1280, 800),
    "Small window (1024 x 640)": (1024, 640),
    "Desktop (1600 x 900)": (1600, 900),
}
ROSTER_COLUMN_LABELS = {
    "overall": "OVR",
    "position": "Pos",
    "player_type": "Player Type",
    "team": "Team",
    "league": "League",
    "org": "Organization",
}
ROSTER_DEFAULT_COLUMNS = tuple(ROSTER_COLUMN_LABELS)


def _load_app_settings() -> dict[str, object]:
    defaults: dict[str, object] = {
        "font_scale": 1.0,
        "density": "Auto",
        "window_width": None,
        "window_height": None,
        "roster_columns": list(ROSTER_DEFAULT_COLUMNS),
    }
    try:
        saved = json.loads(APP_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return defaults
    if not isinstance(saved, dict):
        return defaults
    scale = saved.get("font_scale", defaults["font_scale"])
    try:
        defaults["font_scale"] = max(0.85, min(1.25, float(scale)))
    except (TypeError, ValueError):
        pass
    density = str(saved.get("density") or "Auto")
    defaults["density"] = density if density in DENSITY_CHOICES else "Auto"
    for key, minimum in (("window_width", 920), ("window_height", 620)):
        try:
            defaults[key] = max(minimum, int(saved[key])) if saved.get(key) is not None else None
        except (TypeError, ValueError):
            pass
    roster_columns = saved.get("roster_columns")
    if isinstance(roster_columns, list):
        valid_columns = [str(column) for column in roster_columns if str(column) in ROSTER_COLUMN_LABELS]
        defaults["roster_columns"] = valid_columns
    return defaults


def _save_app_settings(settings: dict[str, object]) -> None:
    APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    APP_SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
LEAGUE_FILTERS = [
    "All Leagues",
    "NHL",
    "AHL",
    "Organization",
    "Prospects",
    "Europe",
    "International",
    "CHL / Juniors",
    "World Cup",
    "EASHL",
    "Exhibition",
    "Free Agents",
    "Hidden",
    "Other League",
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
LABEL_TO_SEMANTIC = {
    "Speed": "speed",
    "Endurance": "endurance",
    "Passing": "passing",
    "Slap Shot Accuracy": "slap_shot_accuracy",
    "Wrist Shot Accuracy": "wrist_shot_accuracy",
    "Strength": "strength",
    "Balance": "balance",
    "Durability": "durability",
    "Aggressiveness": "aggressiveness",
    "Hand-Eye": "hand_eye",
    "Off. Awareness": "offensive_awareness",
    "Discipline": "discipline",
    "Stick Checking": "stick_checking",
    "Body Checking": "body_checking",
    "Puck Control": "puck_control",
    "Slap Shot Power": "slap_shot_power",
    "Wrist Shot Power": "wrist_shot_power",
    "Agility": "agility",
    "Acceleration": "acceleration",
    "Face-offs": "faceoffs",
    "Deking": "deking",
    "Poise": "poise",
    "Shot Blocking": "shot_blocking",
    "Def. Awareness": "defensive_awareness",
    "Fighting Skill": "fighting_skill",
}
ADVANCED_METRICS_STATE_FILE = "advanced_metrics_applied.json"
ADVANCED_METRICS_MANUAL_REVIEW_FILE = "advanced_metrics_manual_review.json"
ADVANCED_METRICS_MODEL_VERSION = "2026-07-14-toi-role-v2"
NHL_METRIC_ROSTER_LEAGUES = frozenset({"NHL", "AHL", "Organization", "Prospects", "Free Agents"})


def advanced_metric_signature(
    suggestions: dict[str, int],
    *,
    overall_baseline: int | None,
    season_used: int,
    include_edge: bool,
    player_kind: str,
) -> str:
    """Identify the source/model inputs without depending on current ratings."""
    payload = {
        "model_version": ADVANCED_METRICS_MODEL_VERSION,
        "season_used": int(season_used),
        "include_edge": bool(include_edge),
        "player_kind": str(player_kind),
        "overall_baseline": overall_baseline,
        "suggestions": {key: int(value) for key, value in sorted(suggestions.items())},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def metric_targets_match(current: dict[str, int] | None, targets: dict[str, int] | None) -> bool:
    if not current or not targets:
        return False
    return all(int(current.get(label, -1)) == int(value) for label, value in targets.items())


def bulk_metric_player_in_scope(
    player,
    selected_league: str,
    nhl_eligible_names: set[str],
) -> bool:
    if selected_league == "All Leagues":
        return True
    if selected_league != "NHL":
        return player.league_name == selected_league
    return (
        player.league_name in NHL_METRIC_ROSTER_LEAGUES
        and normalize_name(player.full_name) in nhl_eligible_names
    )


FORWARD_STYLE_CODES = {
    "Playmaker": 6,
    "Sniper": 7,
    "Power Forward": 8,
    "2-Way Forward": 9,
    "Grinder": 5,
    "Enforcer": 10,
}
DEFENSE_STYLE_CODES = {
    "Defensive Defenseman": 1,
    "Offensive Defenseman": 2,
    "Enforcer": 3,
    "2-Way Defenseman": 4,
}
GOALIE_STYLE_CODES = {
    "Stand-Up Goalie": 0,
    "Hybrid Goalie": 1,
    "Butterfly Goalie": 2,
}
PLAYER_STYLE_CODES = FORWARD_STYLE_CODES | DEFENSE_STYLE_CODES | GOALIE_STYLE_CODES
STYLE_TO_ARCHETYPE = {
    "Sniper": "sniper",
    "Playmaker": "playmaker",
    "Power Forward": "power_forward",
    "2-Way Forward": "two_way_forward",
    "Grinder": "grinder",
    "Enforcer": "enforcer",
    "Offensive Defenseman": "offensive_defenseman",
    "Defensive Defenseman": "defensive_defenseman",
    "2-Way Defenseman": "two_way_defenseman",
}
FIGHTING_CODES = {
    "Never": 0,
    "Rarely": 1,
    "Sometimes": 2,
    "Often": 3,
}
UNMAPPED_CHOICE = "Unmapped in this roster - needs NHLViewNG check"
POSITION_CHOICES = [
    UNMAPPED_CHOICE,
    "C",
    "LW",
    "RW",
    "D",
    "G",
]
POSITION_CODES = {
    "C": 0,
    "LW": 1,
    "RW": 2,
    "D": 3,
    "G": 4,
}
PLAYER_TYPE_CHOICES = [
    UNMAPPED_CHOICE,
    "Sniper",
    "Playmaker",
    "Power Forward",
    "2-Way Forward",
    "Grinder",
    "Enforcer",
    "Offensive Defenseman",
    "Defensive Defenseman",
    "2-Way Defenseman",
    "Butterfly Goalie",
    "Hybrid Goalie",
    "Stand-Up Goalie",
]
POTENTIAL_ROLES = [
    "5.0 Stars (Franchise)",
    "4.5 Stars (Elite)",
    "4.0 Stars (Top 6 Forward / Top 4 D / Starter)",
    "3.5 Stars (Top 9 Forward / Top 6 D / Low Starter)",
    "3.0 Stars (Bottom 6 Forward / 7th D / Backup)",
    "2.5 Stars (Depth / Minor)",
    "2.0 Stars (AHL / Replacement)",
    "1.5 Stars (Career Minor / Emergency)",
    "1.0 Stars (Minor League Depth)",
    "0.5 Stars (No Growth / Retired Depth)",
]
POTENTIAL_ROLE_TO_STARS = {
    "5.0 Stars (Franchise)": "5.0",
    "4.5 Stars (Elite)": "4.5",
    "4.0 Stars (Top 6 Forward / Top 4 D / Starter)": "4.0",
    "3.5 Stars (Top 9 Forward / Top 6 D / Low Starter)": "3.5",
    "3.0 Stars (Bottom 6 Forward / 7th D / Backup)": "3.0",
    "2.5 Stars (Depth / Minor)": "2.5",
    "2.0 Stars (AHL / Replacement)": "2.0",
    "1.5 Stars (Career Minor / Emergency)": "1.5",
    "1.0 Stars (Minor League Depth)": "1.0",
    "0.5 Stars (No Growth / Retired Depth)": "0.5",
}
POTENTIAL_STAR_CODE_TO_STARS = {
    1: "5.0",
    2: "4.5",
    3: "4.0",
    4: "3.5",
    5: "3.0",
    6: "2.5",
    7: "2.0",
    8: "1.5",
    9: "1.0",
    10: "0.5",
}
POTENTIAL_STARS_TO_CODE = {value: key for key, value in POTENTIAL_STAR_CODE_TO_STARS.items()}
POTENTIAL_STAR_TO_ROLE = {value: key for key, value in POTENTIAL_ROLE_TO_STARS.items()}
POTENTIAL_ACCURACY = ["High / Green", "Medium / Yellow", "Low / Red", "Exact / Silver (game-derived)"]
POTENTIAL_ACCURACY_TO_CODE = {
    "High / Green": 1,
    "Medium / Yellow": 2,
    "Low / Red": 4,
    "Exact / Silver (game-derived)": 1,
}
POTENTIAL_CODE_TO_ACCURACY = {
    1: "High / Green",
    2: "Medium / Yellow",
    3: "Medium / Yellow",
    4: "Low / Red",
    5: "Low / Red",
    6: "Low / Red",
}
POTENTIAL_STARS = ["0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0"]


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _money_to_millions(value: str | None) -> float | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9.]", "", value)
    if not digits:
        return None
    return float(digits) / 1_000_000.0


def _format_money_millions(value: float | None) -> str:
    if value is None:
        return "Unknown"
    return f"${value:,.3f}M"


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _percentile_to_rating(value: object, floor: int = 62, ceiling: int = 99) -> int | None:
    percentile = _edge_percentile(value)
    if percentile is None:
        return None
    return round(floor + (ceiling - floor) * percentile)


def _edge_percentile(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        percentile = float(value)
    except (TypeError, ValueError):
        return None
    if percentile > 1:
        percentile /= 100.0
    if percentile <= 0:
        return None
    return max(0.0, min(1.0, percentile))


def _edge_rating_from_score(score: float | None, floor: int = 70, ceiling: int = 96) -> int | None:
    if score is None:
        return None
    score = max(0.0, min(1.0, score))
    return round(floor + (ceiling - floor) * score)


def _weighted_edge_score(*weighted_percentiles: tuple[float | None, float]) -> float | None:
    total_weight = 0.0
    total = 0.0
    for percentile, weight in weighted_percentiles:
        if percentile is None or weight <= 0:
            continue
        total += percentile * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return total / total_weight


def _edge_pct_label(value: float | None) -> str:
    if value is None:
        return "n/a"
    percentile = round(value * 100)
    if 10 <= percentile % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(percentile % 10, "th")
    return f"{percentile}{suffix}"


def _edge_number(value: object, suffix: str = "") -> str:
    if value in (None, ""):
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number)}{suffix}"
    return f"{number:.2f}{suffix}"


def _edge_summary(data: dict, code: str) -> dict:
    for row in data.get("sogSummary") or []:
        if str(row.get("locationCode") or "").lower() == code.lower():
            return row
    return {}


def _goalie_edge_summary(data: dict, code: str) -> dict:
    for row in data.get("shotLocationSummary") or []:
        if str(row.get("locationCode") or "").lower() == code.lower():
            return row
    return {}


def _edge_area(data: dict, area: str) -> dict:
    for row in data.get("sogDetails") or []:
        if str(row.get("area") or "").lower() == area.lower():
            return row
    return {}


def _edge_area_score(data: dict, areas: tuple[str, ...]) -> float | None:
    values = [
        _edge_percentile((_edge_area(data, area) or {}).get("shotsPercentile"))
        for area in areas
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


class ScrollFrame(ttk.Frame):
    def __init__(self, parent, *, background: str = "#101821"):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=background)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)


class NhlLegacyDesktopApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("NHL Legacy Roster Studio")
        self.ui_settings = _load_app_settings()
        self.font_scale = float(self.ui_settings.get("font_scale", 1.0))
        self.ui_density = str(self.ui_settings.get("density") or "Auto")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        saved_width = self.ui_settings.get("window_width")
        saved_height = self.ui_settings.get("window_height")
        initial_width = max(920, min(int(saved_width or 1680), screen_width - 32))
        initial_height = max(620, min(int(saved_height or 960), screen_height - 64))
        self.root.geometry(f"{initial_width}x{initial_height}")
        self.root.minsize(920, 620)
        self.root.resizable(True, True)

        self.workspace: EditorWorkspace | None = load_active_workspace()
        self.player_index: list[PlayerListEntry] = []
        self.player_by_id: dict[int, PlayerListEntry] = {}
        self.player_overall_by_id: dict[int, int] = {}
        self.player_position_by_id: dict[int, str] = {}
        self.player_type_by_id: dict[int, str] = {}
        self.roster_sort_column = "#0"
        self.roster_sort_descending = False
        self.players_by_team_abbrev: dict[str, list[PlayerListEntry]] = {}
        self.players_by_org_abbrev: dict[str, list[PlayerListEntry]] = {}
        self.potential_sorted_players: list[PlayerListEntry] = []
        self.comparable_players: list[PlayerListEntry] = []
        self.teams: list[TeamRecord] = []
        self.team_by_code: dict[int, TeamRecord] = {}
        self.team_display_to_abbrev: dict[str, str] = {}
        self.team_choice_values: list[str] = []
        self.roster_team_filter_display_to_filter: dict[str, tuple[str, str | None]] = {}
        self.selected_player: PlayerListEntry | None = None
        self.snapshot = None
        self.player_snapshot_cache: PlayerSnapshotCache | None = None
        self.current_team: dict[str, object] | None = None
        self.attribute_vars: dict[str, tk.IntVar] = {}
        self.attribute_edge_vars: dict[str, tk.StringVar] = {}
        self.attribute_original_values: dict[str, int] = {}
        self.edge_suggestions: dict[str, int] = {}
        self.edge_suggestion_notes: dict[str, str] = {}
        self.edge_metric_context: dict[str, object] = {}
        self.bulk_attribute_recommendations: list[dict[str, object]] = []
        self.manual_metric_review: list[dict[str, object]] = []
        self.advanced_metric_targets: dict[str, dict[str, object]] = {}
        self.moneypuck_client: MoneyPuckCSVClient | None = None
        self.moneypuck_mapper: LegacyAttributeMapper | None = None
        self.moneypuck_goalie_mapper: LegacyAttributeMapper | None = None
        self.edge_detail_cache: dict[tuple[int, int], dict] = {}
        self.edge_goalie_detail_cache: dict[tuple[int, int], dict] = {}
        self.player_landing_cache: dict[int, dict] = {}
        self.metric_bundle_cache: dict[tuple[int, str], tuple[list[dict[str, object]], LegacyAttributeMapper]] = {}
        self.metric_bundle_lock = threading.RLock()
        self.metrics_prewarm_started = False
        self.metrics_prewarm_complete = False
        self._after_callbacks: dict[str, str] = {}
        self._background_task_running = False
        self._background_task_label = ""
        self._compact_layout: bool | None = None
        self.manual_metrics_expanded = False
        self.flags_vars: dict[str, tk.IntVar] = {}
        self.contract_queue: list[dict[str, object]] = []
        self.draft_pick_rows: list[CapWagesDraftPick] = []
        self.draft_class_prospects = load_2026_draft_class()
        self.draft_class_statuses: list[DraftRosterStatus] = []
        self.potential_pending_updates: dict[int, dict[str, object]] = {}
        self.update_queue: dict[str, list[dict[str, object]]] = {"moves": [], "create_candidates": []}
        self.update_vetoes: set[str] = set()
        self.update_applied: set[str] = set()
        self.update_errors: dict[str, str] = {}
        self.organization_links: dict[str, str] = default_organization_links()
        self.expansion_destination_var = tk.StringVar(value=EXPANSION_DESTINATION_TEAMS)
        self.capwages_player = None
        self.official_player_hit = None

        self._configure_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.workspace is not None:
            self._set_status("Loading roster, ratings, and cached metrics...")
            # Let Tk paint the shell before the intentional startup prewarm.
            self.root.after(75, self._initialize_active_workspace)
        else:
            self._set_status("Open an NHL Legacy roster save to begin.")

    def run(self) -> None:
        self.root.mainloop()

    def _initialize_active_workspace(self) -> None:
        if self.workspace is None:
            return
        if hasattr(self, "activity_progress"):
            self.activity_progress.start(12)
        try:
            self._reload_workspace()
            self._prewarm_default_metric_data(synchronous=True)
        except Exception as exc:
            self._show_error("Load roster", exc)
        finally:
            if hasattr(self, "activity_progress"):
                self.activity_progress.stop()

    def _configure_style(self) -> None:
        self.colors = {
            "ice": "#f4fbff",
            "ink": "#dceaf5",
            "muted": "#8299aa",
            "panel": "#06111d",
            "panel2": "#0d2135",
            "panel3": "#163d5d",
            "raised": "#1d5477",
            "line": "#2b5872",
            "accent": "#38c3f2",
            "accent_hover": "#74d9fa",
            "blue": "#3eb9e8",
            "danger": "#e45f68",
            "success": "#4fc18c",
        }
        families = set(tkfont.families(self.root))
        self.body_font = next(
            (name for name in ("Aptos", "Segoe UI Variable Text", "Segoe UI") if name in families),
            "Segoe UI",
        )
        self.display_font = next(
            (name for name in ("Bahnschrift", "Aptos Display", "Segoe UI Variable Display") if name in families),
            self.body_font,
        )
        font_size = lambda value: max(8, int(round(value * self.font_scale)))
        for named_font in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(named_font).configure(family=self.body_font, size=font_size(10))
            except tk.TclError:
                pass
        self.root.configure(background=self.colors["panel"])
        self.root.option_add("*tearOff", False)
        self.root.option_add("*TCombobox*Listbox.background", self.colors["panel2"])
        self.root.option_add("*TCombobox*Listbox.foreground", self.colors["ink"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#245b78")
        self.root.option_add("*TCombobox*Listbox.selectForeground", self.colors["ice"])
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            ".",
            background=self.colors["panel"],
            foreground=self.colors["ink"],
            fieldbackground=self.colors["panel2"],
            font=(self.body_font, font_size(10)),
        )
        style.configure("TFrame", background=self.colors["panel"])
        style.configure("AppBar.TFrame", background="#091625")
        style.configure("AccentRail.TFrame", background=self.colors["accent"])
        style.configure("Status.TFrame", background="#091625")
        style.configure("Card.TFrame", background=self.colors["panel2"], relief="flat", borderwidth=1)
        style.configure("TLabel", background=self.colors["panel"], foreground=self.colors["ink"])
        style.configure("Muted.TLabel", background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("Card.TLabel", background=self.colors["panel2"], foreground=self.colors["ink"])
        style.configure("CardMuted.TLabel", background=self.colors["panel2"], foreground=self.colors["muted"])
        style.configure("Bar.TLabel", background="#091625", foreground=self.colors["ink"])
        style.configure("Status.TLabel", background="#091625", foreground=self.colors["muted"])
        style.configure("Eyebrow.TLabel", background="#091625", foreground=self.colors["accent"], font=(self.body_font, font_size(9), "bold"))
        style.configure("Title.TLabel", background="#091625", foreground=self.colors["ice"], font=(self.display_font, font_size(21), "bold"))
        style.configure("Player.TLabel", background=self.colors["panel"], foreground=self.colors["ice"], font=(self.display_font, font_size(24), "bold"))
        style.configure("CardTitle.TLabel", background=self.colors["panel2"], foreground=self.colors["ice"], font=(self.display_font, font_size(14), "bold"))
        style.configure("Section.TLabel", background=self.colors["panel"], foreground=self.colors["ice"], font=(self.display_font, font_size(14), "bold"))
        style.configure("Accent.TLabel", background=self.colors["panel"], foreground=self.colors["accent"], font=(self.body_font, font_size(10), "bold"))
        style.configure("Badge.TLabel", background=self.colors["raised"], foreground=self.colors["ice"], padding=(9, 4), font=(self.body_font, font_size(9), "bold"))
        style.configure(
            "TButton",
            background=self.colors["panel3"],
            foreground=self.colors["ink"],
            bordercolor=self.colors["line"],
            lightcolor=self.colors["panel3"],
            darkcolor=self.colors["panel3"],
            borderwidth=1,
            focusthickness=0,
            padding=(12, 7),
            font=(self.body_font, font_size(9), "bold"),
        )
        style.map(
            "TButton",
            background=[("pressed", "#203e56"), ("active", self.colors["raised"])],
            foreground=[("disabled", self.colors["muted"])],
            bordercolor=[("focus", self.colors["accent"]), ("active", "#35546c")],
        )
        style.configure("Toolbar.TButton", padding=(10, 6), background="#102438")
        style.configure(
            "Toolbar.TMenubutton",
            padding=(9, 5),
            background="#102438",
            foreground=self.colors["ink"],
            arrowcolor=self.colors["accent"],
            bordercolor=self.colors["line"],
            font=(self.body_font, font_size(8), "bold"),
        )
        style.map("Toolbar.TMenubutton", background=[("active", self.colors["raised"])])
        style.configure("Disclosure.TButton", padding=(10, 6), background=self.colors["panel2"], anchor="w")
        style.configure("Accent.TButton", background=self.colors["accent"], foreground="#06111b", bordercolor=self.colors["accent"])
        style.map("Accent.TButton", background=[("active", self.colors["accent_hover"]), ("pressed", "#2999c4")])
        style.configure("Danger.TButton", background=self.colors["danger"], foreground="#fff8f8", bordercolor=self.colors["danger"])
        style.map("Danger.TButton", background=[("active", "#f1777f")])
        style.configure("TEntry", fieldbackground="#07101a", foreground=self.colors["ice"], insertcolor=self.colors["ice"], bordercolor=self.colors["line"], padding=7)
        style.map("TEntry", bordercolor=[("focus", self.colors["accent"])])
        style.configure("TCombobox", fieldbackground="#07101a", background=self.colors["panel3"], foreground=self.colors["ice"], arrowcolor=self.colors["accent"], bordercolor=self.colors["line"], padding=6)
        style.map("TCombobox", bordercolor=[("focus", self.colors["accent"])], fieldbackground=[("readonly", "#07101a")])
        style.configure("TSpinbox", fieldbackground="#07101a", foreground=self.colors["ice"], arrowcolor=self.colors["accent"], bordercolor=self.colors["line"], padding=5)
        style.configure("Treeview", background="#081622", fieldbackground="#081622", foreground=self.colors["ink"], rowheight=font_size(29), borderwidth=0, relief="flat", font=(self.body_font, font_size(10)))
        style.configure("Treeview.Heading", background=self.colors["panel3"], foreground=self.colors["ice"], borderwidth=0, relief="flat", padding=(8, 8), font=(self.body_font, font_size(9), "bold"))
        style.map("Treeview", background=[("selected", "#205a79")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", self.colors["raised"])])
        style.configure("TNotebook", background=self.colors["panel"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=self.colors["panel2"], foreground=self.colors["muted"], borderwidth=0, padding=(12, 10), font=(self.body_font, font_size(9), "bold"))
        style.map("TNotebook.Tab", background=[("selected", self.colors["raised"]), ("active", self.colors["panel3"])], foreground=[("selected", "#ffffff"), ("active", self.colors["ink"])])
        style.configure("TPanedwindow", background=self.colors["line"], sashwidth=5)
        style.configure("TProgressbar", background=self.colors["accent"], troughcolor=self.colors["panel2"], borderwidth=0)
        style.configure(
            "Attribute.Horizontal.TScale",
            background=self.colors["accent"],
            troughcolor="#102a40",
            bordercolor="#bfeeff",
            lightcolor=self.colors["accent_hover"],
            darkcolor="#1484ae",
            sliderlength=22,
        )
        style.map(
            "Attribute.Horizontal.TScale",
            background=[("active", self.colors["ice"]), ("focus", self.colors["accent_hover"])],
            bordercolor=[("active", self.colors["accent"]), ("focus", self.colors["ice"])],
        )
        style.configure("TScrollbar", background=self.colors["panel3"], troughcolor=self.colors["panel"], bordercolor=self.colors["panel"])
        style.configure("TLabelframe", background=self.colors["panel2"], bordercolor=self.colors["line"], relief="flat")
        style.configure("TLabelframe.Label", background=self.colors["panel2"], foreground=self.colors["ice"], font=(self.body_font, font_size(10), "bold"))
        style.configure("TCheckbutton", background=self.colors["panel"], foreground=self.colors["ink"])
        style.configure("TRadiobutton", background=self.colors["panel"], foreground=self.colors["ink"])

    def _build_ui(self) -> None:
        self.top_bar = ttk.Frame(self.root, style="AppBar.TFrame")
        self.top_bar.pack(fill="x")
        brand = ttk.Frame(self.top_bar, style="AppBar.TFrame")
        brand.pack(side="left", padx=18, pady=11)
        ttk.Frame(brand, width=5, style="AccentRail.TFrame").grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 11))
        ttk.Label(brand, text="ROSTER WORKBENCH", style="Eyebrow.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(brand, text="NHL LEGACY", style="Title.TLabel").grid(row=1, column=1, sticky="w")
        self.brand_subtitle_label = ttk.Label(brand, text="Xbox 360 roster studio", style="Bar.TLabel")
        self.brand_subtitle_label.grid(row=1, column=2, sticky="sw", padx=(12, 0), pady=(0, 3))

        actions = ttk.Frame(self.top_bar, style="AppBar.TFrame")
        actions.pack(side="right", padx=20, pady=16)
        ttk.Button(actions, text="SAVE TO GAME", style="Accent.TButton", command=self._save_to_game).pack(side="right", padx=(8, 0))
        self.open_roster_button = ttk.Button(actions, text="OPEN ROSTER", style="Toolbar.TButton", command=self._open_roster)
        self.open_roster_button.pack(side="right", padx=(8, 0))
        self.reload_game_button = ttk.Button(actions, text="RELOAD GAME FILE", style="Toolbar.TButton", command=self._reload_from_game_file)
        self.reload_game_button.pack(side="right", padx=(8, 0))
        self.workspace_badge_var = tk.StringVar(value="NO ROSTER LOADED")
        self.workspace_badge = ttk.Label(actions, textvariable=self.workspace_badge_var, style="Badge.TLabel")
        self.workspace_badge.pack(side="right", padx=(0, 14))

        self.status_var = tk.StringVar(value="")
        status_bar = ttk.Frame(self.root, style="Status.TFrame")
        status_bar.pack(side="bottom", fill="x")
        ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel").pack(side="left", fill="x", expand=True, padx=20, pady=8)
        self.shortcut_label = ttk.Label(status_bar, text="Ctrl+F find   Ctrl+A select all   Ctrl+S save   PgUp/PgDn tabs", style="Status.TLabel")
        self.shortcut_label.pack(side="right", padx=(12, 20))
        self.activity_progress = ttk.Progressbar(status_bar, mode="indeterminate", length=180)
        self.activity_progress.pack(side="right", padx=(12, 0), pady=8)

        self.body_pane = tk.PanedWindow(
            self.root,
            orient="horizontal",
            background=self.colors["line"],
            borderwidth=0,
            sashwidth=6,
            sashrelief="flat",
            opaqueresize=True,
        )
        self.body_pane.pack(fill="both", expand=True, padx=16, pady=14)

        left = ttk.Frame(self.body_pane, width=300, style="Card.TFrame")
        right = ttk.Frame(self.body_pane, width=650)
        self.body_pane.add(left, minsize=230, width=300, stretch="never")
        self.body_pane.add(right, minsize=600, stretch="always")
        self._build_roster_panel(left)
        self._build_editor_panel(right)
        self._bind_keyboard_navigation()
        self._update_workspace_badge()
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.root.after_idle(lambda: self._apply_responsive_layout(force=True))

    def _update_workspace_badge(self) -> None:
        if not hasattr(self, "workspace_badge_var"):
            return
        if self.workspace is None:
            self.workspace_badge_var.set("NO ROSTER LOADED")
            self._refresh_settings_paths()
            return
        source = self.workspace.source_roster
        self.workspace_badge_var.set(source.parent.name.upper() if source is not None else "WORKSPACE ACTIVE")
        self._refresh_settings_paths()

    def _bind_keyboard_navigation(self) -> None:
        self.root.bind_all("<Control-s>", lambda _event: self._save_to_game())
        self.root.bind_all("<Control-f>", lambda _event: self._focus_roster_search())
        self.root.bind_all("<Prior>", lambda _event: self._select_relative_tab(-1))
        self.root.bind_all("<Next>", lambda _event: self._select_relative_tab(1))
        self.root.bind_all("<Control-Left>", lambda _event: self._select_relative_tab(-1))
        self.root.bind_all("<Control-Right>", lambda _event: self._select_relative_tab(1))
        self.root.bind_all("<Control-a>", self._select_all_focused)
        self.root.bind_all("<Control-A>", self._select_all_focused)
        self.root.bind_all("<Escape>", lambda _event: self.player_tree.focus_set() if hasattr(self, "player_tree") else None)
        self.root.bind_all("<Return>", self._activate_focused_widget)
        for index in range(1, 9):
            self.root.bind_all(f"<Alt-Key-{index}>", lambda _event, tab_index=index - 1: self._select_tab(tab_index))

    def _on_close(self) -> None:
        self.ui_settings["font_scale"] = self.font_scale
        self.ui_settings["density"] = self.ui_density
        self.ui_settings["window_width"] = self.root.winfo_width()
        self.ui_settings["window_height"] = self.root.winfo_height()
        try:
            _save_app_settings(self.ui_settings)
        finally:
            self.root.destroy()

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        if hasattr(self, "window_size_var"):
            self.window_size_var.set(f"{event.width} x {event.height}")
        self._debounce("responsive-layout", 120, self._apply_responsive_layout)

    def _use_compact_layout(self) -> bool:
        if self.ui_density == "Compact":
            return True
        if self.ui_density == "Comfortable":
            return False
        return self.root.winfo_width() < 1450 or self.root.winfo_height() < 780

    def _apply_responsive_layout(self, *, force: bool = False) -> None:
        compact = self._use_compact_layout()
        if not force and compact == self._compact_layout:
            return
        self._compact_layout = compact
        style = ttk.Style()
        tab_padding = (7, 8) if compact else (12, 10)
        style.configure("TNotebook.Tab", padding=tab_padding)
        if compact:
            self.brand_subtitle_label.grid_remove()
            self.workspace_badge.pack_forget()
            self.shortcut_label.pack_forget()
            self.body_pane.pack_configure(padx=7, pady=7)
        else:
            self.brand_subtitle_label.grid()
            if not self.workspace_badge.winfo_manager():
                self.workspace_badge.pack(side="right", padx=(0, 14), before=self.reload_game_button)
            if not self.shortcut_label.winfo_manager():
                self.shortcut_label.pack(side="right", padx=(12, 20))
            self.body_pane.pack_configure(padx=16, pady=14)
        labels = self.compact_tab_labels if compact else self.full_tab_labels
        for tab, label in labels.items():
            if str(tab) in self.tabs.tabs():
                self.tabs.tab(tab, text=label)
        self.root.after_idle(lambda: self._set_initial_sash(300 if compact else 390))
        if hasattr(self, "attribute_scroll") and self.attribute_vars:
            self._populate_attributes()

    def _set_initial_sash(self, position: int) -> None:
        try:
            if isinstance(self.body_pane, ttk.PanedWindow):
                self.body_pane.sashpos(0, position)
            else:
                self.body_pane.sash_place(0, position, 0)
        except tk.TclError:
            pass

    def _debounce(self, key: str, delay_ms: int, callback) -> None:
        pending = self._after_callbacks.pop(key, None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except tk.TclError:
                pass

        def run() -> None:
            self._after_callbacks.pop(key, None)
            callback()

        self._after_callbacks[key] = self.root.after(delay_ms, run)

    @staticmethod
    def _clear_tree(tree: ttk.Treeview, *, chunk_size: int = 750) -> None:
        children = tree.get_children()
        for start in range(0, len(children), chunk_size):
            tree.delete(*children[start:start + chunk_size])

    def _focus_roster_search(self):
        if hasattr(self, "search_entry"):
            self.search_entry.focus_set()
            self.search_entry.select_range(0, "end")

    def _select_tab(self, index: int) -> None:
        if not hasattr(self, "tabs"):
            return
        tabs = self.tabs.tabs()
        if 0 <= index < len(tabs):
            self.tabs.select(tabs[index])

    def _select_relative_tab(self, delta: int) -> None:
        if not hasattr(self, "tabs"):
            return
        tabs = self.tabs.tabs()
        if not tabs:
            return
        current = self.tabs.index(self.tabs.select())
        self.tabs.select(tabs[(current + delta) % len(tabs)])

    def _activate_focused_widget(self, event=None):
        widget = self.root.focus_get()
        if widget == getattr(self, "player_tree", None):
            self._on_player_selected()
            return "break"
        if widget == getattr(self, "move_left_tree", None):
            self._move_between_lanes("left_to_right")
            return "break"
        if widget == getattr(self, "move_right_tree", None):
            self._move_between_lanes("right_to_left")
            return "break"
        if widget == getattr(self, "update_tree", None):
            self._apply_selected_update_moves()
            return "break"
        if widget == getattr(self, "review_tree", None):
            self._apply_selected_review_move()
            return "break"
        return None

    def _build_roster_panel(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent, style="Card.TFrame")
        header.pack(fill="x", padx=14, pady=(15, 12))
        ttk.Label(header, text="PLAYERS", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.roster_count_var = tk.StringVar(value="0 players")
        ttk.Label(header, textvariable=self.roster_count_var, style="CardMuted.TLabel").grid(row=0, column=1, sticky="e")
        ttk.Label(header, text="Filter the entire database by league, club, organization, or name.", style="CardMuted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        header.columnconfigure(0, weight=1)

        filters = ttk.Frame(parent, style="Card.TFrame")
        filters.pack(fill="x", padx=14, pady=(0, 10))
        ttk.Label(filters, text="League", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        ttk.Label(filters, text="Team / organization (type to search)", style="Card.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 3))
        self.league_var = tk.StringVar(value="All Leagues")
        self.team_var = tk.StringVar(value="All Teams")
        self.search_var = tk.StringVar(value="")
        league_combo = ttk.Combobox(filters, textvariable=self.league_var, values=LEAGUE_FILTERS, state="readonly", width=18)
        self.team_combo = ttk.Combobox(filters, textvariable=self.team_var, values=["All Teams"], state="normal", width=28)
        league_combo.grid(row=1, column=0, sticky="ew")
        self.team_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(filters, text="Player search  (Ctrl+F)", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 3))
        self.roster_columns_button = ttk.Menubutton(filters, text="COLUMNS", style="Toolbar.TMenubutton")
        self.roster_columns_button.grid(row=2, column=1, sticky="e", pady=(7, 3))
        self.roster_columns_menu = tk.Menu(
            self.roster_columns_button,
            tearoff=False,
            background=self.colors["panel2"],
            foreground=self.colors["ink"],
            activebackground=self.colors["raised"],
            activeforeground=self.colors["ice"],
        )
        self.roster_columns_button.configure(menu=self.roster_columns_menu)
        saved_columns = self.ui_settings.get("roster_columns")
        visible_columns = set(saved_columns if isinstance(saved_columns, list) else ROSTER_DEFAULT_COLUMNS)
        self.roster_column_vars: dict[str, tk.BooleanVar] = {}
        for column, label in ROSTER_COLUMN_LABELS.items():
            variable = tk.BooleanVar(value=column in visible_columns)
            self.roster_column_vars[column] = variable
            self.roster_columns_menu.add_checkbutton(
                label=label,
                variable=variable,
                command=self._apply_roster_columns,
            )
        self.roster_columns_menu.add_separator()
        self.roster_columns_menu.add_command(
            label="Show all columns",
            command=lambda: self._set_roster_columns(ROSTER_DEFAULT_COLUMNS),
        )
        self.roster_columns_menu.add_command(
            label="Compact: OVR, Pos, Team",
            command=lambda: self._set_roster_columns(("overall", "position", "team")),
        )
        self.search_entry = ttk.Entry(filters, textvariable=self.search_var)
        self.search_entry.grid(row=3, column=0, columnspan=2, sticky="ew")
        filters.columnconfigure(0, weight=1)
        filters.columnconfigure(1, weight=1)
        self.league_var.trace_add("write", lambda *_: self._on_league_changed())
        self.team_var.trace_add("write", lambda *_: self._debounce("roster-filter", 40, self._refresh_player_list))
        self.search_var.trace_add("write", lambda *_: self._debounce("roster-filter", 140, self._refresh_player_list))
        self.team_combo.bind("<KeyRelease>", self._filter_roster_team_combo)

        tree_frame = ttk.Frame(parent, style="Card.TFrame")
        tree_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        columns = tuple(ROSTER_COLUMN_LABELS)
        self.player_tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")
        self.player_tree.heading("#0", text="Player", command=lambda: self._sort_roster_players("#0"))
        for column, label in ROSTER_COLUMN_LABELS.items():
            self.player_tree.heading(
                column,
                text=label,
                command=lambda selected_column=column: self._sort_roster_players(selected_column),
            )
        self.player_tree.column("#0", width=175, minwidth=135, stretch=True)
        self.player_tree.column("overall", width=52, minwidth=48, anchor="center", stretch=False)
        self.player_tree.column("position", width=48, minwidth=42, anchor="center", stretch=False)
        self.player_tree.column("player_type", width=145, minwidth=105, anchor="w", stretch=False)
        self.player_tree.column("team", width=64, minwidth=52, anchor="center", stretch=False)
        self.player_tree.column("league", width=110, minwidth=80, anchor="center", stretch=False)
        self.player_tree.column("org", width=92, minwidth=70, anchor="center", stretch=False)
        player_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.player_tree.yview)
        player_x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.player_tree.xview)
        self.player_tree.configure(yscrollcommand=player_scroll.set, xscrollcommand=player_x_scroll.set)
        self.player_tree.grid(row=0, column=0, sticky="nsew")
        player_scroll.grid(row=0, column=1, sticky="ns")
        player_x_scroll.grid(row=1, column=0, sticky="ew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self._apply_roster_columns(save=False)
        self._update_roster_sort_headings()
        self.player_tree.bind("<<TreeviewSelect>>", self._on_player_selected)
        self.player_tree.bind("<Button-3>", self._show_roster_context_menu)
        self.player_tree.tag_configure("elite", foreground="#6edcff")
        self.player_tree.tag_configure("nhl", foreground=self.colors["ice"])
        self.player_tree.tag_configure("depth", foreground="#b6c9d7")
        self.player_tree.tag_configure("fringe", foreground=self.colors["muted"])

    def _apply_roster_columns(self, *, save: bool = True) -> None:
        visible_columns = tuple(
            column
            for column in ROSTER_COLUMN_LABELS
            if self.roster_column_vars.get(column) is not None and self.roster_column_vars[column].get()
        )
        if hasattr(self, "player_tree"):
            self.player_tree.configure(displaycolumns=visible_columns)
        self.ui_settings["roster_columns"] = list(visible_columns)
        if save:
            _save_app_settings(self.ui_settings)

    def _set_roster_columns(self, columns: tuple[str, ...]) -> None:
        selected_columns = set(columns)
        for column, variable in self.roster_column_vars.items():
            variable.set(column in selected_columns)
        self._apply_roster_columns()

    def _sort_roster_players(self, column: str) -> None:
        if column == self.roster_sort_column:
            self.roster_sort_descending = not self.roster_sort_descending
        else:
            self.roster_sort_column = column
            self.roster_sort_descending = column == "overall"
        self._update_roster_sort_headings()
        self._refresh_player_list()

    def _update_roster_sort_headings(self) -> None:
        if not hasattr(self, "player_tree"):
            return
        labels = {"#0": "Player", **ROSTER_COLUMN_LABELS}
        for column, label in labels.items():
            suffix = ""
            if column == self.roster_sort_column:
                suffix = " v" if self.roster_sort_descending else " ^"
            self.player_tree.heading(column, text=f"{label}{suffix}")

    def _roster_sort_value(self, entry: PlayerListEntry):
        column = self.roster_sort_column
        if column == "#0":
            return (entry.last_name.casefold(), entry.first_name.casefold(), entry.player_id)
        if column == "overall":
            return self.player_overall_by_id.get(entry.player_id, -1)
        if column == "position":
            return self.player_position_by_id.get(entry.player_id, "Unknown").casefold()
        if column == "player_type":
            return self.player_type_by_id.get(entry.player_id, "Unknown").casefold()
        if column == "team":
            return (entry.current_team_abbrev or ("Hidden" if entry.is_hidden else "FA")).casefold()
        if column == "league":
            return entry.league_name.casefold()
        if column == "org":
            return (entry.organization_abbrev or "").casefold()
        return entry.full_name.casefold()

    def _build_editor_panel(self, parent: ttk.Frame) -> None:
        player_header = ttk.Frame(parent)
        player_header.pack(fill="x", padx=(18, 0), pady=(2, 12))
        self.player_title_var = tk.StringVar(value="Select a player")
        self.player_subtitle_var = tk.StringVar(value="Choose a league/team on the left, then click a player.")
        ttk.Label(player_header, textvariable=self.player_title_var, style="Player.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(player_header, textvariable=self.player_subtitle_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w")
        player_header.columnconfigure(0, weight=1)

        movement = ttk.Frame(parent, style="Card.TFrame")
        movement.pack(fill="x", padx=(18, 0), pady=(0, 12))
        ttk.Label(movement, text="QUICK MOVE", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(14, 12), pady=12)
        self.target_team_var = tk.StringVar(value="")
        self.target_team_combo = ttk.Combobox(movement, textvariable=self.target_team_var, values=[], state="normal", width=34)
        self.target_team_combo.grid(row=0, column=1, sticky="ew", pady=9)
        ttk.Button(movement, text="Move Player", style="Accent.TButton", command=self._move_selected_player).grid(row=0, column=2, sticky="ew", padx=8, pady=9)
        ttk.Button(movement, text="Free Agency", style="Danger.TButton", command=self._send_selected_to_free_agency).grid(row=0, column=3, sticky="ew", padx=(0, 12), pady=9)
        movement.columnconfigure(1, weight=1)

        self.tabs = ttk.Notebook(parent)
        self.tabs.pack(fill="both", expand=True, padx=(18, 0))
        self.movement_tab = ttk.Frame(self.tabs)
        self.player_tab = ttk.Frame(self.tabs)
        self.potential_tab = ttk.Frame(self.tabs)
        self.attributes_tab = ttk.Frame(self.tabs)
        self.contracts_tab = ttk.Frame(self.tabs)
        self.draft_class_tab = ttk.Frame(self.tabs)
        self.edge_tab = ttk.Frame(self.tabs)
        self.updates_tab = ttk.Frame(self.tabs)
        self.create_tab = ttk.Frame(self.tabs)
        self.review_tab = ttk.Frame(self.tabs)
        self.settings_tab = ttk.Frame(self.tabs)
        ordered_tabs = (
            (self.player_tab, "PLAYER"),
            (self.attributes_tab, "ATTRIBUTES"),
            (self.edge_tab, "METRICS"),
            (self.potential_tab, "POTENTIAL"),
            (self.contracts_tab, "CONTRACTS"),
            (self.movement_tab, "MOVEMENT"),
            (self.updates_tab, "ROSTER SYNC"),
            (self.draft_class_tab, "DRAFT CLASS"),
            (self.create_tab, "CREATE PLAYER"),
            (self.review_tab, "FINAL REVIEW"),
            (self.settings_tab, "SETTINGS"),
        )
        for tab, label in ordered_tabs:
            self.tabs.add(tab, text=label)
        self.full_tab_labels = {tab: label for tab, label in ordered_tabs}
        self.compact_tab_labels = {
            self.player_tab: "INFO",
            self.attributes_tab: "ATTR",
            self.edge_tab: "METRICS",
            self.potential_tab: "POT",
            self.contracts_tab: "CONTRACTS",
            self.movement_tab: "MOVE",
            self.updates_tab: "SYNC",
            self.draft_class_tab: "DRAFT",
            self.create_tab: "NEW",
            self.review_tab: "REVIEW",
            self.settings_tab: "SET",
        }
        self._build_movement_tab()
        self._build_player_info_tab()
        self._build_potential_tab()
        self._build_attributes_tab()
        self._build_contracts_tab()
        self._build_draft_class_tab()
        self._build_edge_tab()
        self._build_updates_tab()
        self._build_create_tab()
        self._build_review_tab()
        self._build_settings_tab()

    def _build_movement_tab(self) -> None:
        controls = ttk.Frame(self.movement_tab)
        controls.pack(fill="x", padx=12, pady=12)
        self.move_left_team_var = tk.StringVar(value="")
        self.move_right_team_var = tk.StringVar(value="")
        ttk.Label(controls, text="Team 1 (type to search)").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="Team 2 (type to search)").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.move_left_combo = ttk.Combobox(controls, textvariable=self.move_left_team_var, values=[], state="normal", width=34)
        self.move_right_combo = ttk.Combobox(controls, textvariable=self.move_right_team_var, values=[], state="normal", width=34)
        self.move_left_combo.grid(row=1, column=0, sticky="ew")
        self.move_right_combo.grid(row=1, column=2, sticky="ew", padx=(12, 0))
        ttk.Button(controls, text="Refresh Teams", command=self._refresh_trade_lanes).grid(row=1, column=3, sticky="ew", padx=(12, 0))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(2, weight=1)
        self.move_left_team_var.trace_add("write", lambda *_: self._debounce("trade-lanes", 80, self._refresh_trade_lanes))
        self.move_right_team_var.trace_add("write", lambda *_: self._debounce("trade-lanes", 80, self._refresh_trade_lanes))
        for combo in (self.target_team_combo, self.move_left_combo, self.move_right_combo):
            combo.bind("<KeyRelease>", self._filter_team_combo)

        lanes = ttk.PanedWindow(self.movement_tab, orient="horizontal")
        lanes.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        left_frame = ttk.Frame(lanes)
        middle_frame = ttk.Frame(lanes, width=180)
        right_frame = ttk.Frame(lanes)
        lanes.add(left_frame, weight=1)
        lanes.add(middle_frame, weight=0)
        lanes.add(right_frame, weight=1)

        self.move_left_title_var = tk.StringVar(value="Team 1")
        self.move_right_title_var = tk.StringVar(value="Team 2")
        ttk.Label(left_frame, textvariable=self.move_left_title_var, style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(right_frame, textvariable=self.move_right_title_var, style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        columns = ("team", "league", "org")
        self.move_left_tree = ttk.Treeview(left_frame, columns=columns, show="tree headings", selectmode="extended")
        self.move_right_tree = ttk.Treeview(right_frame, columns=columns, show="tree headings", selectmode="extended")
        for tree in (self.move_left_tree, self.move_right_tree):
            tree.heading("#0", text="Player")
            tree.heading("team", text="Team")
            tree.heading("league", text="League")
            tree.heading("org", text="Org")
            tree.column("#0", width=260, stretch=True)
            tree.column("team", width=65, anchor="center", stretch=False)
            tree.column("league", width=105, anchor="center", stretch=False)
            tree.column("org", width=55, anchor="center", stretch=False)
            tree.pack(fill="both", expand=True)
            tree.bind("<Double-1>", self._on_trade_lane_double_click)
            tree.bind("<Button-3>", self._show_trade_context_menu)

        ttk.Label(middle_frame, text="TRADE", style="Section.TLabel").pack(pady=(48, 12))
        ttk.Button(middle_frame, text="Team 1  ->  Team 2", style="Accent.TButton", command=lambda: self._move_between_lanes("left_to_right")).pack(fill="x", padx=12, pady=6)
        ttk.Button(middle_frame, text="Team 1  <-  Team 2", style="Accent.TButton", command=lambda: self._move_between_lanes("right_to_left")).pack(fill="x", padx=12, pady=6)
        ttk.Button(middle_frame, text="Drop Selected To FA", style="Danger.TButton", command=self._drop_lane_selection_to_fa).pack(fill="x", padx=12, pady=(20, 6))
        ttk.Label(
            middle_frame,
            text="Select one or more players in either list. Double-click also moves across.",
            style="Muted.TLabel",
            wraplength=150,
            justify="center",
        ).pack(fill="x", padx=12, pady=12)

    def _build_player_info_tab(self) -> None:
        wrapper = ScrollFrame(self.player_tab)
        wrapper.pack(fill="both", expand=True)
        frame = wrapper.inner

        confirmed = ttk.LabelFrame(frame, text="Confirmed / Useful Fields")
        confirmed.pack(fill="x", padx=12, pady=12)
        self.info_vars = {
            "first_name": tk.StringVar(),
            "last_name": tk.StringVar(),
            "player_id": tk.StringVar(),
            "instance_id": tk.StringVar(),
            "team": tk.StringVar(),
            "organization": tk.StringVar(),
            "league": tk.StringVar(),
            "jersey": tk.StringVar(),
            "birthplace": tk.StringVar(),
            "position_label": tk.StringVar(),
            "junior_rights": tk.StringVar(),
            "style_label": tk.StringVar(),
            "fighting_label": tk.StringVar(),
        }
        self._field(confirmed, "First Name", self.info_vars["first_name"], row=0, column=0, readonly=True)
        self._field(confirmed, "Last Name", self.info_vars["last_name"], row=0, column=1, readonly=True)
        self._field(confirmed, "Player ID", self.info_vars["player_id"], row=1, column=0, readonly=True)
        self._field(confirmed, "Instance ID", self.info_vars["instance_id"], row=1, column=1, readonly=True)
        self._field(confirmed, "Team", self.info_vars["team"], row=2, column=0, readonly=True)
        self._field(confirmed, "Organization", self.info_vars["organization"], row=2, column=1, readonly=True)
        self._field(confirmed, "League", self.info_vars["league"], row=3, column=0, readonly=True)
        self._field(confirmed, "Junior / CHL Rights", self.info_vars["junior_rights"], row=3, column=1, readonly=True)
        self._field(confirmed, "Jersey Number", self.info_vars["jersey"], row=4, column=0)
        self._field(confirmed, "Birthplace / Hometown", self.info_vars["birthplace"], row=4, column=1)
        self._choice_field(confirmed, "Position", self.info_vars["position_label"], POSITION_CHOICES, row=5, column=0)
        self.style_combo = self._choice_field(confirmed, "Player Type", self.info_vars["style_label"], PLAYER_TYPE_CHOICES, row=5, column=1)
        self.fighting_combo = self._choice_field(confirmed, "Fighting", self.info_vars["fighting_label"], [UNMAPPED_CHOICE, *list(FIGHTING_CODES.keys())], row=6, column=0)
        self.style_hint_var = tk.StringVar(value="")
        ttk.Label(confirmed, textvariable=self.style_hint_var, style="Muted.TLabel").grid(row=6, column=1, sticky="w", padx=8, pady=(20, 4))
        ttk.Button(confirmed, text="Save Player Info", style="Accent.TButton", command=self._save_player_info).grid(row=7, column=0, columnspan=2, sticky="ew", padx=8, pady=12)
        confirmed.columnconfigure(0, weight=1)
        confirmed.columnconfigure(1, weight=1)

        remote = ttk.LabelFrame(frame, text="HockeyDB / NHL Bio Lookup")
        remote.pack(fill="x", padx=12, pady=(0, 12))
        self.remote_bio_text = tk.Text(remote, height=9, wrap="word", background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat")
        self.remote_bio_text.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        ttk.Button(remote, text="Load HockeyDB + NHL Bio", command=self._load_remote_bio).pack(anchor="e", padx=10, pady=(0, 10))

        potential = ttk.LabelFrame(frame, text="Potential Star / Color System")
        potential.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Label(
            potential,
            text="Potential now writes confirmed roster fields: stars use the growth tier, and green/yellow/red use the growth accuracy. Exact/Silver is game-derived for fully developed players, so choosing it preserves the current accuracy code while saving the star tier.",
            style="Muted.TLabel",
            wraplength=900,
        ).grid(row=0, column=0, columnspan=4, sticky="ew", padx=10, pady=(10, 8))
        self.potential_role_var = tk.StringVar(value="3.0 Stars (Bottom 6 Forward / 7th D / Backup)")
        self.potential_stars_var = tk.StringVar(value="3.0")
        self.potential_accuracy_var = tk.StringVar(value="Medium / Yellow")
        self.potential_role_var.trace_add("write", lambda *_: self._sync_potential_stars_from_role())
        self.info_vars["position_label"].trace_add("write", lambda *_: self._refresh_style_choices_for_position())
        self.info_vars["style_label"].trace_add("write", lambda *_: self._sync_archetype_to_player_type(self.info_vars["style_label"].get()))
        ttk.Label(potential, text="Potential Role").grid(row=1, column=0, sticky="w", padx=10)
        ttk.Label(potential, text="Stars").grid(row=1, column=1, sticky="w", padx=10)
        ttk.Label(potential, text="Likelihood / Color").grid(row=1, column=2, sticky="w", padx=10)
        ttk.Combobox(potential, textvariable=self.potential_role_var, values=POTENTIAL_ROLES, state="readonly").grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 10))
        ttk.Combobox(potential, textvariable=self.potential_stars_var, values=POTENTIAL_STARS, state="readonly").grid(row=2, column=1, sticky="ew", padx=10, pady=(2, 10))
        ttk.Combobox(potential, textvariable=self.potential_accuracy_var, values=POTENTIAL_ACCURACY, state="readonly").grid(row=2, column=2, sticky="ew", padx=10, pady=(2, 10))
        ttk.Button(potential, text="Save Potential To Roster", command=self._save_potential_to_roster).grid(row=2, column=3, sticky="ew", padx=10, pady=(2, 10))
        potential.columnconfigure(0, weight=1)
        potential.columnconfigure(1, weight=1)
        potential.columnconfigure(2, weight=1)

    def _build_potential_tab(self) -> None:
        controls = ttk.Frame(self.potential_tab)
        controls.pack(fill="x", padx=12, pady=12)
        self.potential_league_filter_var = tk.StringVar(value="NHL")
        self.potential_team_filter_var = tk.StringVar(value="All Teams")
        self.potential_search_var = tk.StringVar(value="")
        self.potential_min_stars_var = tk.StringVar(value="0.5")
        self.potential_max_stars_var = tk.StringVar(value="5.0")
        ttk.Label(controls, text="League").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="Team").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(controls, text="Player search").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.potential_league_combo = ttk.Combobox(
            controls,
            textvariable=self.potential_league_filter_var,
            values=LEAGUE_FILTERS,
            state="readonly",
            width=18,
        )
        self.potential_league_combo.grid(row=1, column=0, sticky="ew")
        self.potential_team_combo = ttk.Combobox(controls, textvariable=self.potential_team_filter_var, values=["All Teams"], state="readonly", width=34)
        self.potential_team_combo.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        ttk.Entry(controls, textvariable=self.potential_search_var).grid(row=1, column=2, sticky="ew", padx=(10, 0))
        ttk.Label(controls, text="Stars from").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(controls, text="Stars to").grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        ttk.Label(controls, text="Pending edits").grid(row=2, column=2, sticky="w", padx=(10, 0), pady=(8, 0))
        ttk.Combobox(controls, textvariable=self.potential_min_stars_var, values=POTENTIAL_STARS, state="readonly", width=8).grid(row=3, column=0, sticky="ew")
        ttk.Combobox(controls, textvariable=self.potential_max_stars_var, values=POTENTIAL_STARS, state="readonly", width=8).grid(row=3, column=1, sticky="ew", padx=(10, 0))
        self.save_pending_potentials_button = ttk.Button(
            controls,
            text="Save All Pending (0)",
            style="Accent.TButton",
            command=self._save_all_pending_potentials,
        )
        self.save_pending_potentials_button.grid(row=3, column=2, sticky="ew", padx=(10, 0))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=1)
        self.potential_league_combo.bind("<<ComboboxSelected>>", self._on_potential_league_changed)
        self.potential_team_filter_var.trace_add("write", lambda *_: self._debounce("potential-filter", 50, self._refresh_potential_tree))
        self.potential_search_var.trace_add("write", lambda *_: self._debounce("potential-filter", 140, self._refresh_potential_tree))
        self.potential_min_stars_var.trace_add("write", lambda *_: self._debounce("potential-filter", 50, self._refresh_potential_tree))
        self.potential_max_stars_var.trace_add("write", lambda *_: self._debounce("potential-filter", 50, self._refresh_potential_tree))

        editor = ttk.LabelFrame(self.potential_tab, text="Selected Player Potential")
        editor.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Label(editor, text="Role").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        ttk.Label(editor, text="Stars").grid(row=0, column=1, sticky="w", padx=10, pady=(8, 2))
        ttk.Label(editor, text="Likelihood / Color").grid(row=0, column=2, sticky="w", padx=10, pady=(8, 2))
        self.potential_role_combo = ttk.Combobox(editor, textvariable=self.potential_role_var, values=POTENTIAL_ROLES, state="readonly")
        self.potential_stars_combo = ttk.Combobox(editor, textvariable=self.potential_stars_var, values=POTENTIAL_STARS, state="readonly")
        self.potential_accuracy_combo = ttk.Combobox(editor, textvariable=self.potential_accuracy_var, values=POTENTIAL_ACCURACY, state="readonly")
        self.potential_role_combo.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.potential_stars_combo.grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 8))
        self.potential_accuracy_combo.grid(row=1, column=2, sticky="ew", padx=10, pady=(0, 8))
        ttk.Label(
            editor,
            text="Changes are staged automatically. Edit as many players as needed, then use Save All Pending once.",
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))
        for combo in (self.potential_role_combo, self.potential_stars_combo, self.potential_accuracy_combo):
            combo.bind("<<ComboboxSelected>>", self._stage_selected_potential)
        for column in range(3):
            editor.columnconfigure(column, weight=1)

        columns = ("league", "team", "organization", "position", "stars", "color", "role", "status")
        potential_tree_frame = ttk.Frame(self.potential_tab)
        potential_tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.potential_tree = ttk.Treeview(
            potential_tree_frame,
            columns=columns,
            show="tree headings",
            selectmode="extended",
        )
        headings = {
            "#0": "Player",
            "league": "League",
            "team": "Team",
            "organization": "Organization",
            "position": "Position",
            "stars": "Stars",
            "color": "Color",
            "role": "Potential Role",
            "status": "Edit Status",
        }
        for column, label in headings.items():
            self.potential_tree.heading(
                column,
                text=label,
                command=lambda key=column: self._sort_potential_tree(key, False),
            )
        self.potential_tree.column("#0", width=230, stretch=True)
        self.potential_tree.column("league", width=105, anchor="center", stretch=False)
        self.potential_tree.column("team", width=80, anchor="center", stretch=False)
        self.potential_tree.column("organization", width=100, anchor="center", stretch=False)
        self.potential_tree.column("position", width=75, anchor="center", stretch=False)
        self.potential_tree.column("stars", width=75, anchor="center", stretch=False)
        self.potential_tree.column("color", width=125, anchor="center", stretch=False)
        self.potential_tree.column("role", width=360, stretch=True)
        self.potential_tree.column("status", width=90, anchor="center", stretch=False)
        potential_y_scroll = ttk.Scrollbar(potential_tree_frame, orient="vertical", command=self.potential_tree.yview)
        potential_x_scroll = ttk.Scrollbar(potential_tree_frame, orient="horizontal", command=self.potential_tree.xview)
        self.potential_tree.configure(yscrollcommand=potential_y_scroll.set, xscrollcommand=potential_x_scroll.set)
        self.potential_tree.grid(row=0, column=0, sticky="nsew")
        potential_y_scroll.grid(row=0, column=1, sticky="ns")
        potential_x_scroll.grid(row=1, column=0, sticky="ew")
        potential_tree_frame.columnconfigure(0, weight=1)
        potential_tree_frame.rowconfigure(0, weight=1)
        self.potential_tree.bind("<<TreeviewSelect>>", self._on_potential_selected)
        self.potential_tree.bind("<Control-a>", lambda _event: self._select_all_potentials())
        self.potential_tree.bind("<Control-A>", lambda _event: self._select_all_potentials())
        self.potential_tree.bind("<Button-3>", self._show_potential_context_menu)

    def _field(self, parent, label: str, variable: tk.StringVar, *, row: int, column: int, readonly: bool = False):
        box = ttk.Frame(parent)
        box.grid(row=row, column=column, sticky="ew", padx=8, pady=4)
        ttk.Label(box, text=label, style="Muted.TLabel").pack(anchor="w")
        state = "readonly" if readonly else "normal"
        ttk.Entry(box, textvariable=variable, state=state).pack(fill="x")

    def _choice_field(self, parent, label: str, variable: tk.StringVar, values: list[str], *, row: int, column: int):
        box = ttk.Frame(parent)
        box.grid(row=row, column=column, sticky="ew", padx=8, pady=4)
        ttk.Label(box, text=label, style="Muted.TLabel").pack(anchor="w")
        combo = ttk.Combobox(box, textvariable=variable, values=values, state="readonly")
        combo.pack(fill="x")
        return combo

    def _build_attributes_tab(self) -> None:
        top = ttk.Frame(self.attributes_tab)
        top.pack(fill="x", padx=12, pady=12)
        self.archetype_var = tk.StringVar(value="two_way_forward")
        self.attribute_summary_var = tk.StringVar(value="Select a player to estimate their overall.")
        ttk.Label(top, text="Rating Profile").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="Player / Goalie Type").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Combobox(top, textvariable=self.archetype_var, values=sorted(ARCHETYPE_WEIGHTS.keys()), state="readonly", width=24).grid(row=1, column=0, sticky="ew")
        self.attribute_style_combo = ttk.Combobox(
            top,
            textvariable=self.info_vars["style_label"],
            values=PLAYER_TYPE_CHOICES,
            state="readonly",
            width=22,
        )
        self.attribute_style_combo.grid(row=1, column=1, sticky="ew", padx=(10, 0))
        ttk.Label(top, textvariable=self.attribute_summary_var, style="Accent.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        actions = ttk.Frame(top)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="LOAD ADVANCED METRICS", command=self._load_edge_for_selected).pack(side="left")
        ttk.Button(actions, text="APPLY SUGGESTIONS", command=self._apply_edge_suggestions).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="SAVE ATTRIBUTES", style="Accent.TButton", command=self._save_attributes).pack(side="right")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        self.archetype_var.trace_add("write", lambda *_: self._update_attribute_budget())

        self.manual_metric_frame = ttk.Frame(self.attributes_tab, style="Card.TFrame")
        self.manual_metric_frame.pack(fill="x", padx=12, pady=(0, 12))
        manual_header = ttk.Frame(self.manual_metric_frame, style="Card.TFrame")
        manual_header.pack(fill="x")
        self.manual_metric_toggle_var = tk.StringVar(value="+  MANUAL METRICS REVIEW (0)")
        ttk.Button(
            manual_header,
            textvariable=self.manual_metric_toggle_var,
            style="Disclosure.TButton",
            command=self._toggle_manual_metrics_panel,
        ).pack(side="left", fill="x", expand=True)
        self.manual_metric_body = ttk.Frame(self.manual_metric_frame, style="Card.TFrame")
        manual_controls = ttk.Frame(self.manual_metric_body, style="Card.TFrame")
        manual_controls.pack(fill="x", padx=8, pady=(6, 4))
        ttk.Label(
            manual_controls,
            text="Select a player to load their fields and display the queued suggestions beside each attribute.",
            style="CardMuted.TLabel",
        ).pack(side="left")
        ttk.Button(
            manual_controls,
            text="Apply Suggested Edits To Sliders",
            command=self._apply_edge_suggestions,
        ).pack(side="right")
        ttk.Button(
            manual_controls,
            text="Remove Selected",
            command=self._remove_selected_manual_metric_players,
        ).pack(side="right", padx=(0, 8))

        manual_columns = ("position", "league", "team", "current", "suggested", "change")
        self.manual_metric_tree = ttk.Treeview(
            self.manual_metric_body,
            columns=manual_columns,
            show="tree headings",
            selectmode="extended",
            height=4,
        )
        self.manual_metric_tree.heading("#0", text="Player")
        self.manual_metric_tree.heading("position", text="Pos")
        self.manual_metric_tree.heading("league", text="League")
        self.manual_metric_tree.heading("team", text="Roster Team")
        self.manual_metric_tree.heading("current", text="Current OVR")
        self.manual_metric_tree.heading("suggested", text="Suggested OVR")
        self.manual_metric_tree.heading("change", text="OVR Change")
        self.manual_metric_tree.column("#0", width=260, stretch=True)
        self.manual_metric_tree.column("position", width=55, anchor="center", stretch=False)
        self.manual_metric_tree.column("league", width=110, anchor="center", stretch=False)
        self.manual_metric_tree.column("team", width=100, anchor="center", stretch=False)
        self.manual_metric_tree.column("current", width=100, anchor="center", stretch=False)
        self.manual_metric_tree.column("suggested", width=110, anchor="center", stretch=False)
        self.manual_metric_tree.column("change", width=90, anchor="center", stretch=False)
        self.manual_metric_tree.pack(fill="x", padx=8, pady=(0, 8))
        self.manual_metric_tree.bind("<<TreeviewSelect>>", self._on_manual_metric_selected)
        self.manual_metric_tree.bind("<Button-3>", self._show_manual_metrics_context_menu)
        self.manual_metric_tree.bind("<Control-a>", lambda _event: self._select_all_manual_metrics())
        self.manual_metric_tree.bind("<Control-A>", lambda _event: self._select_all_manual_metrics())

        self.attribute_scroll = ScrollFrame(self.attributes_tab)
        self.attribute_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_contracts_tab(self) -> None:
        wrapper = ScrollFrame(self.contracts_tab, background=self.colors["panel"])
        wrapper.pack(fill="both", expand=True)
        content = wrapper.inner
        controls = ttk.Frame(content)
        controls.pack(fill="x", padx=12, pady=12)
        self.real_cap_var = tk.StringVar(value=str(DEFAULT_REAL_CAP_MILLIONS))
        self.game_cap_var = tk.StringVar(value=str(DEFAULT_GAME_CAP_MILLIONS))
        ttk.Label(controls, text="Current NHL Cap (M)").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="NHL Legacy Cap (M)").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Entry(controls, textvariable=self.real_cap_var, width=14).grid(row=1, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.game_cap_var, width=14).grid(row=1, column=1, sticky="w", padx=(8, 0))
        ttk.Button(controls, text="Load Selected Contract", command=self._load_selected_contract).grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(controls, text="Apply Selected Contract", command=self._approve_selected_contract).grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Button(controls, text="Update All Contracts From CapWages", style="Accent.TButton", command=self._build_all_contracts).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(controls, text="Apply Selected Queue", command=lambda: self._approve_contract_queue_selection(apply_all=False)).grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(controls, text="Apply All Queue", command=lambda: self._approve_contract_queue_selection(apply_all=True)).grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)

        manual = ttk.LabelFrame(content, text="Manual Selected Player Contract")
        manual.pack(fill="x", padx=12, pady=(0, 12))
        self.manual_contract_aav_var = tk.StringVar(value="0.000")
        self.manual_contract_length_var = tk.IntVar(value=0)
        self.manual_contract_status_var = tk.StringVar(value="Signed / Restricted")
        self.manual_contract_two_way_var = tk.BooleanVar(value=False)
        self.manual_contract_entry_level_var = tk.BooleanVar(value=False)
        self.manual_extension_aav_var = tk.StringVar(value="0.000")
        self.manual_extension_length_var = tk.IntVar(value=0)
        self.manual_extension_two_way_var = tk.BooleanVar(value=False)
        labels = ("Game AAV (M)", "Years Remaining", "Status")
        for column, label in enumerate(labels):
            ttk.Label(manual, text=label).grid(row=0, column=column, sticky="w", padx=8, pady=(8, 2))
        ttk.Entry(manual, textvariable=self.manual_contract_aav_var, width=14).grid(row=1, column=0, sticky="ew", padx=8)
        tk.Spinbox(manual, textvariable=self.manual_contract_length_var, from_=0, to=15, width=8).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Combobox(
            manual,
            textvariable=self.manual_contract_status_var,
            values=("Signed / Restricted", "Unrestricted"),
            state="readonly",
            width=20,
        ).grid(row=1, column=2, sticky="ew", padx=8)
        ttk.Label(manual, text="Extension AAV (M)").grid(row=2, column=0, sticky="w", padx=8, pady=(8, 2))
        ttk.Label(manual, text="Extension Years").grid(row=2, column=1, sticky="w", padx=8, pady=(8, 2))
        ttk.Label(manual, text="Multi-year: automatic", style="Muted.TLabel").grid(row=2, column=2, sticky="w", padx=8, pady=(8, 2))
        ttk.Entry(manual, textvariable=self.manual_extension_aav_var, width=14).grid(row=3, column=0, sticky="ew", padx=8)
        tk.Spinbox(manual, textvariable=self.manual_extension_length_var, from_=0, to=15, width=8).grid(row=3, column=1, sticky="ew", padx=8)
        ttk.Checkbutton(manual, text="Two-way", variable=self.manual_contract_two_way_var).grid(row=4, column=0, sticky="w", padx=8, pady=8)
        ttk.Checkbutton(manual, text="Entry-level contract", variable=self.manual_contract_entry_level_var).grid(row=4, column=1, sticky="w", padx=8, pady=8)
        ttk.Checkbutton(manual, text="Extension two-way", variable=self.manual_extension_two_way_var).grid(row=4, column=2, sticky="w", padx=8, pady=8)
        ttk.Button(manual, text="Save Manual Contract", style="Accent.TButton", command=self._save_manual_contract).grid(row=5, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))
        for column in range(3):
            manual.columnconfigure(column, weight=1)

        self.contract_detail_text = tk.Text(content, height=6, wrap="word", background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat")
        self.contract_detail_text.pack(fill="x", padx=12, pady=(0, 12))

        columns = ("team", "current", "real", "scaled", "percent", "term", "expiry")
        contract_tree_frame = ttk.Frame(content)
        contract_tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.contract_tree = ttk.Treeview(contract_tree_frame, columns=columns, show="tree headings", selectmode="extended", height=10)
        self.contract_tree.heading("#0", text="Player")
        self.contract_tree.heading("team", text="Team")
        self.contract_tree.heading("current", text="Roster Team")
        self.contract_tree.heading("real", text="Real AAV")
        self.contract_tree.heading("scaled", text="Game AAV")
        self.contract_tree.heading("percent", text="Cap %")
        self.contract_tree.heading("term", text="Years")
        self.contract_tree.heading("expiry", text="Expiry")
        self.contract_tree.column("#0", width=240, stretch=True)
        for column in columns:
            self.contract_tree.column(column, width=105, anchor="center", stretch=False)
        contract_y_scroll = ttk.Scrollbar(contract_tree_frame, orient="vertical", command=self.contract_tree.yview)
        contract_x_scroll = ttk.Scrollbar(contract_tree_frame, orient="horizontal", command=self.contract_tree.xview)
        self.contract_tree.configure(yscrollcommand=contract_y_scroll.set, xscrollcommand=contract_x_scroll.set)
        self.contract_tree.grid(row=0, column=0, sticky="nsew")
        contract_y_scroll.grid(row=0, column=1, sticky="ns")
        contract_x_scroll.grid(row=1, column=0, sticky="ew")
        contract_tree_frame.columnconfigure(0, weight=1)
        contract_tree_frame.rowconfigure(0, weight=1)
        self.contract_tree.bind("<Control-a>", lambda _event: self._select_all_contracts())
        self.contract_tree.bind("<Control-A>", lambda _event: self._select_all_contracts())

    def _build_draft_picks_tab(self) -> None:
        controls = ttk.Frame(self.draft_picks_tab)
        controls.pack(fill="x", padx=12, pady=12)
        self.draft_pick_team_var = tk.StringVar(value="")
        ttk.Label(controls, text="Organization (type abbreviation or name)").grid(row=0, column=0, sticky="w")
        self.draft_pick_team_combo = ttk.Combobox(
            controls,
            textvariable=self.draft_pick_team_var,
            values=[],
            state="normal",
            width=46,
        )
        self.draft_pick_team_combo.grid(row=1, column=0, sticky="ew")
        self.draft_pick_team_combo.bind("<KeyRelease>", self._filter_team_combo)
        ttk.Button(
            controls,
            text="Load CapWages Picks",
            style="Accent.TButton",
            command=self._load_capwages_draft_picks,
        ).grid(row=1, column=1, padx=(10, 0))
        controls.columnconfigure(0, weight=1)

        ttk.Label(
            self.draft_picks_tab,
            text=(
                "Owned and acquired picks are loaded from CapWages and cached with this roster workspace. "
                "Direct game write-back is not enabled yet: pick ownership lives in a Dynasty/Franchise save, "
                "not the roster payload, and its fields must be confirmed with a controlled before/after trade."
            ),
            style="Muted.TLabel",
            wraplength=1180,
            justify="left",
        ).pack(fill="x", padx=12, pady=(0, 12))

        columns = ("year", "round", "original", "status", "date", "details")
        self.draft_pick_tree = ttk.Treeview(
            self.draft_picks_tab,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "year": "Draft Year",
            "round": "Round",
            "original": "Original Team",
            "status": "CapWages Status",
            "date": "Trade Date",
            "details": "Conditions / Trade Details",
        }
        for column, label in headings.items():
            self.draft_pick_tree.heading(column, text=label)
        self.draft_pick_tree.column("year", width=95, anchor="center", stretch=False)
        self.draft_pick_tree.column("round", width=70, anchor="center", stretch=False)
        self.draft_pick_tree.column("original", width=190, stretch=False)
        self.draft_pick_tree.column("status", width=135, anchor="center", stretch=False)
        self.draft_pick_tree.column("date", width=125, anchor="center", stretch=False)
        self.draft_pick_tree.column("details", width=620, stretch=True)
        self.draft_pick_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_draft_class_tab(self) -> None:
        controls = ttk.Frame(self.draft_class_tab)
        controls.pack(fill="x", padx=12, pady=12)
        self.draft_class_round_var = tk.StringVar(value="All Rounds")
        self.draft_class_team_var = tk.StringVar(value="All Teams")
        self.draft_class_status_var = tk.StringVar(value="All Statuses")
        self.draft_class_search_var = tk.StringVar(value="")
        ttk.Label(controls, text="Round").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.draft_class_round_var,
            values=["All Rounds", *[f"Round {value}" for value in range(1, 8)]],
            state="readonly",
            width=14,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 10))
        ttk.Label(controls, text="Drafting team").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.draft_class_team_var,
            values=["All Teams", *sorted({row.team for row in self.draft_class_prospects})],
            state="readonly",
            width=16,
        ).grid(row=0, column=3, sticky="ew", padx=(6, 0))
        ttk.Label(controls, text="Status").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            controls,
            textvariable=self.draft_class_status_var,
            values=["All Statuses", "Missing", "Present"],
            state="readonly",
            width=16,
        ).grid(row=1, column=1, sticky="ew", padx=(6, 10), pady=(8, 0))
        ttk.Label(controls, text="Search").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.draft_class_search_var).grid(
            row=1, column=3, sticky="ew", padx=(6, 0), pady=(8, 0)
        )
        ttk.Button(controls, text="Scan Loaded Roster", command=self._scan_2026_draft_class).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text="Create / Sync Selected", command=lambda: self._apply_2026_draft_class(False)).grid(
            row=2, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        ttk.Button(
            controls,
            text="Sync Full 2026 Draft Class",
            style="Accent.TButton",
            command=lambda: self._apply_2026_draft_class(True),
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(
            controls,
            text="Apply Elite Prospects Scouting",
            command=self._apply_2026_elite_prospects,
        ).grid(row=3, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=2)
        self.draft_class_count_var = tk.StringVar(value="223 confirmed selections | scan the loaded roster to compare")
        ttk.Label(controls, textvariable=self.draft_class_count_var, style="Muted.TLabel").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )
        for variable in (
            self.draft_class_round_var,
            self.draft_class_team_var,
            self.draft_class_status_var,
            self.draft_class_search_var,
        ):
            variable.trace_add("write", lambda *_: self._render_2026_draft_class())

        columns = ("round", "team", "position", "status", "roster_team", "overall", "archetype", "potential", "club")
        draft_tree_frame = ttk.Frame(self.draft_class_tab)
        draft_tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.draft_class_tree = ttk.Treeview(
            draft_tree_frame,
            columns=columns,
            show="tree headings",
            selectmode="extended",
        )
        headings = {
            "#0": "Pick / Player",
            "round": "Round",
            "team": "NHL Team",
            "position": "Pos",
            "status": "Roster",
            "roster_team": "Current Club",
            "overall": "Proj. OVR",
            "archetype": "Player Type",
            "potential": "Potential",
            "club": "2025-26 Club",
        }
        for column, label in headings.items():
            self.draft_class_tree.heading(column, text=label)
        self.draft_class_tree.column("#0", width=250, stretch=True)
        self.draft_class_tree.column("round", width=62, anchor="center", stretch=False)
        self.draft_class_tree.column("team", width=78, anchor="center", stretch=False)
        self.draft_class_tree.column("position", width=55, anchor="center", stretch=False)
        self.draft_class_tree.column("status", width=82, anchor="center", stretch=False)
        self.draft_class_tree.column("roster_team", width=105, anchor="center", stretch=False)
        self.draft_class_tree.column("overall", width=85, anchor="center", stretch=False)
        self.draft_class_tree.column("archetype", width=175, stretch=False)
        self.draft_class_tree.column("potential", width=120, anchor="center", stretch=False)
        self.draft_class_tree.column("club", width=280, stretch=True)
        draft_y_scroll = ttk.Scrollbar(draft_tree_frame, orient="vertical", command=self.draft_class_tree.yview)
        draft_x_scroll = ttk.Scrollbar(draft_tree_frame, orient="horizontal", command=self.draft_class_tree.xview)
        self.draft_class_tree.configure(yscrollcommand=draft_y_scroll.set, xscrollcommand=draft_x_scroll.set)
        self.draft_class_tree.grid(row=0, column=0, sticky="nsew")
        draft_y_scroll.grid(row=0, column=1, sticky="ns")
        draft_x_scroll.grid(row=1, column=0, sticky="ew")
        draft_tree_frame.columnconfigure(0, weight=1)
        draft_tree_frame.rowconfigure(0, weight=1)
        self.draft_class_tree.bind("<Control-a>", lambda _event: self._select_all_2026_draft_rows())
        self.draft_class_tree.bind("<Control-A>", lambda _event: self._select_all_2026_draft_rows())
        self.draft_class_tree.bind("<<TreeviewSelect>>", self._show_2026_draft_details)

        self.draft_class_details = tk.Text(
            self.draft_class_tab,
            height=3,
            wrap="word",
            background="#0b1118",
            foreground=self.colors["ink"],
            insertbackground=self.colors["ink"],
            relief="flat",
        )
        self.draft_class_details.pack(fill="x", padx=12, pady=(0, 12))
        self._render_2026_draft_class()

    def _build_edge_tab(self) -> None:
        wrapper = ScrollFrame(self.edge_tab, background=self.colors["panel"])
        wrapper.pack(fill="both", expand=True)
        content = wrapper.inner
        top = ttk.Frame(content)
        top.pack(fill="x", padx=12, pady=12)
        ttk.Button(top, text="Load Advanced Metrics For Selected Player", style="Accent.TButton", command=self._load_edge_for_selected).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            top,
            text="Suggestions are staged into sliders only. Use Save Attributes to write them.",
            style="Muted.TLabel",
            wraplength=780,
        ).grid(row=1, column=0, sticky="w", pady=(7, 0))
        top.columnconfigure(0, weight=1)

        bulk = ttk.LabelFrame(content, text="Organization Advanced Metrics Preview")
        bulk.pack(fill="x", padx=12, pady=(0, 12))
        self.bulk_org_var = tk.StringVar(value="")
        self.bulk_league_var = tk.StringVar(value="NHL")
        self.bulk_season_var = tk.StringVar(value="2025")
        self.bulk_position_filter_var = tk.StringVar(value="All Positions")
        ttk.Label(bulk, text="League scope").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        ttk.Label(bulk, text="Team / Organization (type to search)").grid(row=0, column=1, sticky="w", padx=10, pady=(8, 2))
        self.bulk_league_combo = ttk.Combobox(bulk, textvariable=self.bulk_league_var, values=LEAGUE_FILTERS[:-2], state="readonly", width=18)
        self.bulk_league_combo.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.bulk_org_combo = ttk.Combobox(bulk, textvariable=self.bulk_org_var, values=[], state="normal", width=42)
        self.bulk_org_combo.grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 8))
        ttk.Label(bulk, text="MoneyPuck season start year").grid(row=2, column=0, sticky="w", padx=10, pady=(0, 2))
        ttk.Label(bulk, text="Position filter").grid(row=2, column=1, sticky="w", padx=10, pady=(0, 2))
        ttk.Entry(bulk, textvariable=self.bulk_season_var, width=10).grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.bulk_position_filter_combo = ttk.Combobox(
            bulk,
            textvariable=self.bulk_position_filter_var,
            values=("All Positions", "Goalies", "Defensemen", "Forwards", "Centers", "Left Wings", "Right Wings"),
            state="readonly",
            width=16,
        )
        self.bulk_position_filter_combo.grid(row=3, column=1, sticky="ew", padx=10, pady=(0, 8))
        ttk.Button(bulk, text="Preview Organization Metrics", style="Accent.TButton", command=self._preview_org_attribute_updates).grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 8))
        ttk.Button(bulk, text="Preview League Metrics", command=lambda: self._preview_org_attribute_updates(league_wide=True)).grid(row=4, column=1, sticky="ew", padx=10, pady=(0, 8))
        ttk.Button(bulk, text="Apply Selected", command=lambda: self._apply_org_attribute_preview(apply_all=False)).grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 8))
        ttk.Button(bulk, text="Apply All Preview", command=lambda: self._apply_org_attribute_preview(apply_all=True)).grid(row=5, column=1, sticky="ew", padx=10, pady=(0, 8))
        ttk.Button(bulk, text="Largest OVR Changes", command=self._sort_bulk_by_largest_change).grid(row=6, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))
        ttk.Label(
            bulk,
            text="Selected season requires 20+ GP. Players below 20 GP fall back one season. TOI/game sets position-aware role credit and reaches full workload credit at 25:00.",
            style="Muted.TLabel",
            wraplength=880,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        bulk.columnconfigure(0, weight=1)
        bulk.columnconfigure(1, weight=2)
        self.bulk_org_combo.bind("<KeyRelease>", self._filter_team_combo)
        self.bulk_league_combo.bind("<<ComboboxSelected>>", self._on_bulk_league_changed)
        self.bulk_position_filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_bulk_stats_tree())

        columns = ("position", "league", "team", "gp", "toi", "current", "suggested", "change", "suggestions")
        bulk_tree_frame = ttk.Frame(content)
        bulk_tree_frame.pack(fill="x", padx=12, pady=(0, 12))
        self.bulk_stats_tree = ttk.Treeview(bulk_tree_frame, columns=columns, show="tree headings", selectmode="extended", height=8)
        self.bulk_stats_tree.heading("#0", text="Player", command=lambda: self._sort_bulk_stats_tree("#0", False))
        self.bulk_stats_tree.heading("position", text="Pos", command=lambda: self._sort_bulk_stats_tree("position", False))
        self.bulk_stats_tree.heading("league", text="League", command=lambda: self._sort_bulk_stats_tree("league", False))
        self.bulk_stats_tree.heading("team", text="Roster Team", command=lambda: self._sort_bulk_stats_tree("team", False))
        self.bulk_stats_tree.heading("gp", text="GP", command=lambda: self._sort_bulk_stats_tree("gp", True))
        self.bulk_stats_tree.heading("toi", text="TOI/GP", command=lambda: self._sort_bulk_stats_tree("toi", True))
        self.bulk_stats_tree.heading("current", text="Current OVR", command=lambda: self._sort_bulk_stats_tree("current", True))
        self.bulk_stats_tree.heading("suggested", text="Suggested OVR", command=lambda: self._sort_bulk_stats_tree("suggested", True))
        self.bulk_stats_tree.heading("change", text="OVR Change", command=lambda: self._sort_bulk_stats_tree("change", True))
        self.bulk_stats_tree.heading("suggestions", text="Suggested Attribute Ratings")
        self.bulk_stats_tree.column("#0", width=220, stretch=True)
        self.bulk_stats_tree.column("position", width=55, anchor="center", stretch=False)
        self.bulk_stats_tree.column("league", width=95, anchor="center", stretch=False)
        self.bulk_stats_tree.column("team", width=95, anchor="center", stretch=False)
        self.bulk_stats_tree.column("gp", width=60, anchor="center", stretch=False)
        self.bulk_stats_tree.column("toi", width=70, anchor="center", stretch=False)
        self.bulk_stats_tree.column("current", width=90, anchor="center", stretch=False)
        self.bulk_stats_tree.column("suggested", width=100, anchor="center", stretch=False)
        self.bulk_stats_tree.column("change", width=90, anchor="center", stretch=False)
        self.bulk_stats_tree.column("suggestions", width=560, stretch=True)
        bulk_y_scroll = ttk.Scrollbar(bulk_tree_frame, orient="vertical", command=self.bulk_stats_tree.yview)
        bulk_x_scroll = ttk.Scrollbar(bulk_tree_frame, orient="horizontal", command=self.bulk_stats_tree.xview)
        self.bulk_stats_tree.configure(yscrollcommand=bulk_y_scroll.set, xscrollcommand=bulk_x_scroll.set)
        self.bulk_stats_tree.grid(row=0, column=0, sticky="nsew")
        bulk_y_scroll.grid(row=0, column=1, sticky="ns")
        bulk_x_scroll.grid(row=1, column=0, sticky="ew")
        bulk_tree_frame.columnconfigure(0, weight=1)
        bulk_tree_frame.rowconfigure(0, weight=1)
        self.bulk_stats_tree.bind("<Control-a>", lambda _event: self._select_all_bulk_stats())
        self.bulk_stats_tree.bind("<Control-A>", lambda _event: self._select_all_bulk_stats())
        self.bulk_stats_tree.bind("<<TreeviewSelect>>", self._on_bulk_metric_selected)
        self.bulk_stats_tree.bind("<Button-3>", self._show_bulk_metrics_context_menu)

        self.edge_text = tk.Text(content, wrap="word", background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat", height=12)
        self.edge_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_updates_tab(self) -> None:
        controls = ttk.Frame(self.updates_tab)
        controls.pack(fill="x", padx=12, pady=12)
        ttk.Button(controls, text="Scan CapWages", style="Accent.TButton", command=self._scan_capwages_updates).grid(row=0, column=0, sticky="ew")
        ttk.Button(controls, text="Apply Selected", command=self._apply_selected_update_moves).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(controls, text="Apply All", command=lambda: self._apply_update_moves(apply_all=True)).grid(row=0, column=2, sticky="ew", padx=(8, 0))
        ttk.Button(controls, text="Veto Selected", command=self._veto_selected_update_moves).grid(row=0, column=3, sticky="ew", padx=(8, 0))
        ttk.Label(
            controls,
            text="Organization-aware: AHL, system, and prospect players already inside their NHL organization are not flagged.",
            style="Muted.TLabel",
            wraplength=900,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for column in range(4):
            controls.columnconfigure(column, weight=1)

        expansion_frame = ttk.LabelFrame(self.updates_tab, text="Expansion Team Handling")
        expansion_frame.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Radiobutton(
            expansion_frame,
            text="Put Seattle/Vegas players on their expansion teams",
            variable=self.expansion_destination_var,
            value=EXPANSION_DESTINATION_TEAMS,
            command=self._save_expansion_destination,
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        ttk.Radiobutton(
            expansion_frame,
            text="Release Seattle/Vegas signed players to Free Agency",
            variable=self.expansion_destination_var,
            value=EXPANSION_DESTINATION_FREE_AGENCY,
            command=self._save_expansion_destination,
        ).grid(row=1, column=0, sticky="w", padx=10, pady=2)
        ttk.Label(
            expansion_frame,
            text="Used by Scan CapWages / auto-update. Flip this when your native expansion-team mod is ready.",
            style="Muted.TLabel",
            wraplength=880,
        ).grid(row=2, column=0, sticky="w", padx=10, pady=(2, 8))
        expansion_frame.columnconfigure(0, weight=1)

        org_frame = ttk.LabelFrame(self.updates_tab, text="Organization Links")
        org_frame.pack(fill="x", padx=12, pady=(0, 12))
        self.org_team_var = tk.StringVar(value="")
        self.org_parent_var = tk.StringVar(value="TOR")
        ttk.Label(org_frame, text="Team / league code (type to search)").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        ttk.Label(org_frame, text="Belongs to NHL organization").grid(row=0, column=1, sticky="w", padx=10, pady=(8, 2))
        self.org_team_combo = ttk.Combobox(org_frame, textvariable=self.org_team_var, values=[], state="normal")
        self.org_parent_combo = ttk.Combobox(org_frame, textvariable=self.org_parent_var, values=sorted(TEAM_SLUGS.keys()), state="readonly")
        self.org_team_combo.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        self.org_parent_combo.grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 8))
        ttk.Button(org_frame, text="Link Organization", command=self._save_organization_link).grid(row=1, column=2, sticky="ew", padx=10, pady=(0, 8))
        org_frame.columnconfigure(0, weight=1)
        org_frame.columnconfigure(1, weight=1)
        self.org_team_combo.bind("<KeyRelease>", self._filter_team_combo)

        update_pane = ttk.PanedWindow(self.updates_tab, orient="vertical")
        update_pane.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        move_frame = ttk.Frame(update_pane)
        create_frame = ttk.Frame(update_pane)
        update_pane.add(move_frame, weight=3)
        update_pane.add(create_frame, weight=1)

        move_columns = ("from", "to", "source", "reason")
        self.update_tree = ttk.Treeview(move_frame, columns=move_columns, show="tree headings", selectmode="extended")
        self.update_tree.heading("#0", text="Player")
        self.update_tree.heading("from", text="From")
        self.update_tree.heading("to", text="To")
        self.update_tree.heading("source", text="Source")
        self.update_tree.heading("reason", text="Reason")
        self.update_tree.column("#0", width=220, stretch=True)
        self.update_tree.column("from", width=70, anchor="center", stretch=False)
        self.update_tree.column("to", width=70, anchor="center", stretch=False)
        self.update_tree.column("source", width=95, anchor="center", stretch=False)
        self.update_tree.column("reason", width=520, stretch=True)
        update_y_scroll = ttk.Scrollbar(move_frame, orient="vertical", command=self.update_tree.yview)
        update_x_scroll = ttk.Scrollbar(move_frame, orient="horizontal", command=self.update_tree.xview)
        self.update_tree.configure(yscrollcommand=update_y_scroll.set, xscrollcommand=update_x_scroll.set)
        self.update_tree.grid(row=0, column=0, sticky="nsew")
        update_y_scroll.grid(row=0, column=1, sticky="ns")
        update_x_scroll.grid(row=1, column=0, sticky="ew")
        move_frame.columnconfigure(0, weight=1)
        move_frame.rowconfigure(0, weight=1)
        self.update_tree.bind("<Button-3>", self._show_update_context_menu)

        ttk.Label(create_frame, text="Create candidates from CapWages (players missing in roster with draft info)", style="Muted.TLabel").pack(anchor="w", pady=(8, 4))
        self.create_candidate_list = tk.Listbox(create_frame, background="#0b1118", foreground=self.colors["ink"], selectbackground="#244e76", relief="flat", height=8, selectmode="extended")
        self.create_candidate_list.pack(fill="both", expand=True)

    def _build_create_tab(self) -> None:
        wrapper = ScrollFrame(self.create_tab)
        wrapper.pack(fill="both", expand=True)
        frame = wrapper.inner
        ttk.Label(frame, text="COMPARISON BUILDER", style="Section.TLabel").pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(
            frame,
            text="Use this to stage ratings for an existing created/prospect player by blending comparable players, then apply those values to the selected player's sliders.",
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w", padx=12, pady=(0, 10))

        form = ttk.Frame(frame)
        form.pack(fill="x", padx=12, pady=(0, 10))
        self.create_name_var = tk.StringVar(value="")
        self.compare_search_var = tk.StringVar(value="")
        self.compare_target_overall_var = tk.IntVar(value=82)
        self.compare_archetype_var = tk.StringVar(value="playmaker")
        ttk.Label(form, text="Create / target player name").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="Comparable search").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Entry(form, textvariable=self.create_name_var).grid(row=1, column=0, sticky="ew")
        self.compare_search_entry = ttk.Entry(form, textvariable=self.compare_search_var, width=34)
        self.compare_search_entry.grid(row=1, column=1, sticky="ew", padx=(12, 0))
        ttk.Button(form, text="Add Comparable", command=self._add_compare_player).grid(row=1, column=2, sticky="ew", padx=(8, 0))
        ttk.Button(form, text="Remove Selected", command=self._remove_compare_player).grid(row=1, column=3, sticky="ew", padx=(8, 0))

        ttk.Label(form, text="Selected comparables").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(form, text="Real-player search results").grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(10, 0))
        ttk.Label(form, text="Archetype").grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Label(form, text="Target OVR").grid(row=2, column=3, sticky="w", padx=(8, 0), pady=(10, 0))
        self.compare_sources_listbox = tk.Listbox(form, height=7, background="#0b1118", foreground=self.colors["ink"], selectbackground="#244e76", relief="flat", selectmode="extended")
        self.compare_sources_listbox.grid(row=3, column=0, rowspan=3, sticky="nsew")
        self.compare_results_listbox = tk.Listbox(form, height=7, background="#0b1118", foreground=self.colors["ink"], selectbackground="#244e76", relief="flat", selectmode="extended")
        self.compare_results_listbox.grid(row=3, column=1, rowspan=3, sticky="nsew", padx=(12, 0))
        ttk.Combobox(form, textvariable=self.compare_archetype_var, values=sorted(ARCHETYPE_WEIGHTS.keys()), state="readonly").grid(row=3, column=2, sticky="ew", padx=(8, 0))
        tk.Spinbox(form, textvariable=self.compare_target_overall_var, from_=36, to=99, width=8).grid(row=3, column=3, sticky="w", padx=(8, 0))
        ttk.Button(form, text="Build Blend", command=self._build_comparison).grid(row=4, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Button(form, text="Apply Blend To Attribute Sliders", style="Accent.TButton", command=self._apply_comparison_to_sliders).grid(row=5, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        self.compare_search_var.trace_add("write", lambda *_: self._debounce("compare-search", 120, self._refresh_compare_search_results))
        self.compare_results_listbox.bind("<Double-Button-1>", lambda _event: self._add_compare_player())

        self.comparison_result_text = tk.Text(frame, height=18, wrap="word", background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat")
        self.comparison_result_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.comparison_blend_values: dict[str, int] = {}

        ttk.Label(
            frame,
            text="Create-player writeback is intentionally not enabled yet. NHLViewNG's own help warns that create-player writes can produce database states the game may not expect, so this app stages candidates and comparison ratings first.",
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w", padx=12, pady=(0, 12))

    def _build_review_tab(self) -> None:
        controls = ttk.Frame(self.review_tab)
        controls.pack(fill="x", padx=12, pady=12)
        ttk.Button(controls, text="Reload Review", command=self._refresh_review).grid(row=0, column=0, sticky="ew")
        ttk.Button(controls, text="Apply Selected Pending Move", command=self._apply_selected_review_move).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(controls, text="Veto Selected Pending Move", command=self._veto_selected_review_move).grid(row=0, column=2, sticky="ew")
        ttk.Button(controls, text="Revert Selected Logged Edit", command=self._revert_selected_review_change).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(controls, text="Save To Game", style="Accent.TButton", command=self._save_to_game).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Label(
            controls,
            text="Save applies every non-vetoed pending move, writes the roster to Xenia, and keeps only unresolved errors in this list.",
            style="Muted.TLabel",
            wraplength=880,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        for column in range(3):
            controls.columnconfigure(column, weight=1)
        columns = ("time", "type", "details")
        self.review_tree = ttk.Treeview(
            ttk.Frame(self.review_tab),
            columns=columns,
            show="tree headings",
            selectmode="extended",
        )
        review_tree_frame = self.review_tree.master
        review_tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.review_tree.heading("#0", text="Player / Item")
        self.review_tree.heading("time", text="Time")
        self.review_tree.heading("type", text="Change Type")
        self.review_tree.heading("details", text="Details")
        self.review_tree.column("#0", width=220, stretch=True)
        self.review_tree.column("time", width=155, anchor="center", stretch=False)
        self.review_tree.column("type", width=170, anchor="center", stretch=False)
        self.review_tree.column("details", width=620, stretch=True)
        review_y_scroll = ttk.Scrollbar(review_tree_frame, orient="vertical", command=self.review_tree.yview)
        review_x_scroll = ttk.Scrollbar(review_tree_frame, orient="horizontal", command=self.review_tree.xview)
        self.review_tree.configure(yscrollcommand=review_y_scroll.set, xscrollcommand=review_x_scroll.set)
        self.review_tree.grid(row=0, column=0, sticky="nsew")
        review_y_scroll.grid(row=0, column=1, sticky="ns")
        review_x_scroll.grid(row=1, column=0, sticky="ew")
        review_tree_frame.columnconfigure(0, weight=1)
        review_tree_frame.rowconfigure(0, weight=1)
        self.review_tree.bind("<Button-3>", self._show_review_context_menu)

    def _build_settings_tab(self) -> None:
        wrapper = ScrollFrame(self.settings_tab, background=self.colors["panel"])
        wrapper.pack(fill="both", expand=True)
        frame = wrapper.inner

        ttk.Label(frame, text="DISPLAY & LAYOUT", style="Section.TLabel").pack(anchor="w", padx=14, pady=(14, 8))
        appearance = ttk.LabelFrame(frame, text="Screen Profile")
        appearance.pack(fill="x", padx=14, pady=(0, 14))
        self.settings_font_scale_var = tk.StringVar(value=self._font_scale_label())
        self.settings_density_var = tk.StringVar(value=self.ui_density)
        self.settings_window_profile_var = tk.StringVar(value="Keep current size")
        self.window_size_var = tk.StringVar(value=f"{self.root.winfo_width()} x {self.root.winfo_height()}")
        ttk.Label(appearance, text="Font size").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 3))
        ttk.Label(appearance, text="Layout density").grid(row=0, column=1, sticky="w", padx=10, pady=(10, 3))
        ttk.Combobox(
            appearance,
            textvariable=self.settings_font_scale_var,
            values=list(FONT_SCALE_CHOICES),
            state="readonly",
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        ttk.Combobox(
            appearance,
            textvariable=self.settings_density_var,
            values=DENSITY_CHOICES,
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 10))
        ttk.Label(appearance, text="Window preset").grid(row=2, column=0, sticky="w", padx=10, pady=(0, 3))
        ttk.Label(appearance, text="Current window").grid(row=2, column=1, sticky="w", padx=10, pady=(0, 3))
        ttk.Combobox(
            appearance,
            textvariable=self.settings_window_profile_var,
            values=list(WINDOW_SIZE_CHOICES),
            state="readonly",
        ).grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        ttk.Label(appearance, textvariable=self.window_size_var, style="CardMuted.TLabel").grid(row=3, column=1, sticky="w", padx=10, pady=(0, 10))
        ttk.Button(appearance, text="APPLY DISPLAY SETTINGS", style="Accent.TButton", command=self._apply_ui_preferences).grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=10,
            pady=(0, 10),
        )
        ttk.Label(
            appearance,
            text="Auto switches to the compact one-column editor on smaller 16:10 screens. Every pane remains resizable by dragging its divider.",
            style="CardMuted.TLabel",
            wraplength=900,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 10))
        for column in range(2):
            appearance.columnconfigure(column, weight=1)

        ttk.Label(frame, text="ROSTER FILES", style="Section.TLabel").pack(anchor="w", padx=14, pady=(2, 8))
        roster = ttk.LabelFrame(frame, text="Active Load / Save Paths")
        roster.pack(fill="x", padx=14, pady=(0, 14))
        self.settings_game_roster_var = tk.StringVar(value="No roster loaded")
        self.settings_working_roster_var = tk.StringVar(value="No workspace loaded")
        ttk.Label(roster, text="Live Xenia roster used by Reload and Save To Game").grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 3))
        ttk.Entry(roster, textvariable=self.settings_game_roster_var, state="readonly").grid(row=1, column=0, columnspan=3, sticky="ew", padx=10)
        ttk.Label(roster, text="Editor working copy").grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 3))
        ttk.Entry(roster, textvariable=self.settings_working_roster_var, state="readonly").grid(row=3, column=0, columnspan=3, sticky="ew", padx=10)
        ttk.Button(roster, text="OPEN DIFFERENT ROSTER", command=self._open_roster).grid(row=4, column=0, sticky="ew", padx=10, pady=10)
        ttk.Button(roster, text="CHOOSE GAME SAVE TARGET", command=self._choose_game_target_from_settings).grid(row=4, column=1, sticky="ew", padx=10, pady=10)
        ttk.Button(roster, text="RELOAD FROM GAME FILE", command=self._reload_from_game_file).grid(row=4, column=2, sticky="ew", padx=10, pady=10)
        roster.columnconfigure(0, weight=1)
        roster.columnconfigure(1, weight=1)
        roster.columnconfigure(2, weight=1)

        ttk.Label(frame, text="FEATURE STATUS", style="Section.TLabel").pack(anchor="w", padx=14, pady=(2, 8))
        feature = ttk.LabelFrame(frame, text="Franchise Draft Pick Ownership")
        feature.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Label(
            feature,
            text="Draft Assets is hidden for now. CapWages can report ownership, but the NHL Legacy franchise ownership table has not been mapped safely enough to write without risking a damaged or crashing save.",
            style="CardMuted.TLabel",
            wraplength=960,
        ).pack(anchor="w", fill="x", padx=10, pady=10)
        self._refresh_settings_paths()

    def _font_scale_label(self) -> str:
        return min(FONT_SCALE_CHOICES, key=lambda label: abs(FONT_SCALE_CHOICES[label] - self.font_scale))

    def _apply_ui_preferences(self) -> None:
        self.font_scale = FONT_SCALE_CHOICES.get(self.settings_font_scale_var.get(), 1.0)
        density = self.settings_density_var.get()
        self.ui_density = density if density in DENSITY_CHOICES else "Auto"
        window_size = WINDOW_SIZE_CHOICES.get(self.settings_window_profile_var.get())
        if window_size is not None:
            width, height = window_size
            self.root.geometry(f"{width}x{height}")
            self.root.update_idletasks()
            self.settings_window_profile_var.set("Keep current size")
        self.ui_settings.update(
            {
                "font_scale": self.font_scale,
                "density": self.ui_density,
                "window_width": self.root.winfo_width(),
                "window_height": self.root.winfo_height(),
            }
        )
        _save_app_settings(self.ui_settings)
        self._configure_style()
        self._apply_responsive_layout(force=True)
        self._set_status("Display settings applied.")

    def _choose_game_target_from_settings(self) -> None:
        self._set_game_save_target()
        self._refresh_settings_paths()

    def _refresh_settings_paths(self) -> None:
        if not hasattr(self, "settings_game_roster_var"):
            return
        if self.workspace is None:
            self.settings_game_roster_var.set("No roster loaded")
            self.settings_working_roster_var.set("No workspace loaded")
            return
        self.settings_game_roster_var.set(str(self.workspace.source_roster or "No game target selected"))
        self.settings_working_roster_var.set(str(self.workspace.working_roster))

    def _build_advanced_tab(self) -> None:
        wrapper = ScrollFrame(self.advanced_tab)
        wrapper.pack(fill="both", expand=True)
        frame = wrapper.inner
        ttk.Label(frame, text="RAW FLAGS / POTENTIAL RESEARCH", style="Section.TLabel").pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(
            frame,
            text="These are the linked player flag fields from the roster. They are useful for potential/star research, but only edit them when you know the code you want.",
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w", padx=12, pady=(0, 10))
        self.flags_frame = ttk.Frame(frame)
        self.flags_frame.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(frame, text="Save Raw Flags", command=self._save_raw_flags).pack(anchor="e", padx=12, pady=(0, 12))

    def _open_roster(self) -> None:
        path = filedialog.askopenfilename(
            title="Open NHL Legacy roster save",
            initialdir=str(Path(r"D:\Emulation\xenia_manager\Emulators\Xenia Canary\content")),
            filetypes=[("NHL roster saves", "*"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.workspace = create_workspace(Path(path))
            self._reset_roster_filters()
            self._reload_workspace()
            self._set_status(f"Opened workspace {self.workspace.name}")
        except Exception as exc:
            self._show_error("Open roster failed", exc)

    def _reload_from_game_file(self) -> None:
        if self.workspace is None or self.workspace.source_roster is None:
            self._set_status("No game file source is attached. Use Open Roster Save first.")
            return
        source = self.workspace.source_roster
        if not source.exists():
            self._set_status(f"Game file source not found: {source}")
            return
        if not messagebox.askyesno(
            "Reload From Game File",
            "This will create a fresh editor workspace from the live Xenia roster file and discard unsaved editor-only workspace edits. Continue?",
        ):
            return
        try:
            self.workspace = create_workspace(source)
            self._reset_roster_filters()
            self._reload_workspace()
            self._set_status(f"Reloaded fresh workspace from game file: {source}")
        except Exception as exc:
            self._show_error("Reload from game file failed", exc)

    def _reset_roster_filters(self) -> None:
        if hasattr(self, "league_var"):
            self.league_var.set("All Leagues")
        if hasattr(self, "team_var"):
            self.team_var.set("All Teams")
        if hasattr(self, "search_var"):
            self.search_var.set("")

    def _reload_workspace(self) -> None:
        if self.workspace is None:
            self.workspace = load_active_workspace()
        if self.workspace is None:
            self._set_status("No workspace is active. Open a roster save.")
            self._update_workspace_badge()
            return
        try:
            self._update_workspace_badge()
            self._load_organization_links()
            self._load_expansion_destination()
            self._load_update_vetoes()
            self._load_advanced_metric_targets()
            self._load_manual_metric_review()
            self.teams = load_teams(self.workspace.working_db)
            self.team_by_code = {team.code: team for team in self.teams}
            self._rebuild_player_cache()
            self._rebuild_team_choices()
            self._refresh_trade_lanes()
            self._refresh_player_list()
            self._refresh_contract_queue()
            self._refresh_update_queue()
            self._refresh_review()
            self._refresh_potential_tree()
            self._render_manual_metric_review_tree()
            self._ensure_roster_visible_after_reload()
            self._set_status(f"Workspace: {self.workspace.name} | Working roster: {self.workspace.working_roster}")
            self.root.after(350, self._scan_2026_draft_class)
        except Exception as exc:
            self._show_error("Reload failed", exc)

    def _ensure_roster_visible_after_reload(self) -> None:
        if not hasattr(self, "player_tree") or not self.player_index:
            return
        if self.player_tree.get_children():
            return
        had_filter = (
            self.league_var.get() != "All Leagues"
            or self.team_var.get() not in {"", "All Teams"}
            or bool(self.search_var.get().strip())
        )
        if not had_filter:
            return
        self._reset_roster_filters()
        self._on_league_changed()

    def _rebuild_player_cache(self) -> None:
        if self.workspace is None:
            self.player_snapshot_cache = None
            self.player_index = []
            self._reindex_player_collections()
            return
        self.player_snapshot_cache = build_player_snapshot_cache(self.workspace.working_db)
        self.player_index = build_player_index_from_tables(
            self.player_snapshot_cache.bio_rows,
            self.player_snapshot_cache.relation_rows,
            self.player_snapshot_cache.instance_rows,
            self.team_by_code,
            self.organization_links,
        )
        self._reindex_player_collections()

    def _refresh_player_index_from_cache(self) -> None:
        if self.player_snapshot_cache is None:
            self.player_index = []
            self._reindex_player_collections()
            return
        self.player_index = build_player_index_from_tables(
            self.player_snapshot_cache.bio_rows,
            self.player_snapshot_cache.relation_rows,
            self.player_snapshot_cache.instance_rows,
            self.team_by_code,
            self.organization_links,
        )
        self._reindex_player_collections()

    def _reindex_player_collections(self) -> None:
        self.player_by_id = {entry.player_id: entry for entry in self.player_index}
        self.player_overall_by_id = {}
        self.player_position_by_id = {}
        self.player_type_by_id = {}
        if self.player_snapshot_cache is not None:
            for player_id, entry in self.player_by_id.items():
                snapshot = self.player_snapshot_cache.get_player_snapshot(
                    entry.first_name,
                    entry.last_name,
                    player_id,
                )
                overall, position, player_type = self._snapshot_roster_summary(snapshot)
                if overall is not None:
                    self.player_overall_by_id[player_id] = overall
                self.player_position_by_id[player_id] = position
                self.player_type_by_id[player_id] = player_type
        self.players_by_team_abbrev = {}
        self.players_by_org_abbrev = {}
        for entry in self.player_index:
            if entry.current_team_abbrev:
                self.players_by_team_abbrev.setdefault(entry.current_team_abbrev.upper(), []).append(entry)
            if entry.organization_abbrev:
                self.players_by_org_abbrev.setdefault(entry.organization_abbrev.upper(), []).append(entry)
        self.potential_sorted_players = sorted(
            self.player_index,
            key=lambda item: (item.league_name, item.current_team_abbrev or "ZZZ", item.last_name, item.first_name),
        )
        seen_ids: set[int] = set()
        self.comparable_players = []
        for player in sorted(self.player_index, key=lambda item: (item.last_name, item.first_name, item.player_id)):
            if player.player_id in seen_ids or not self._is_real_comparable_player(player):
                continue
            seen_ids.add(player.player_id)
            self.comparable_players.append(player)

    def _refresh_player_overall(self, player_id: int) -> None:
        if self.player_snapshot_cache is None:
            return
        entry = self.player_by_id.get(player_id)
        if entry is None:
            return
        snapshot = self.player_snapshot_cache.get_player_snapshot(
            entry.first_name,
            entry.last_name,
            player_id,
        )
        overall, position, player_type = self._snapshot_roster_summary(snapshot)
        if overall is None:
            self.player_overall_by_id.pop(player_id, None)
        else:
            self.player_overall_by_id[player_id] = overall
        self.player_position_by_id[player_id] = position
        self.player_type_by_id[player_id] = player_type
        for iid, visible_entry in getattr(self, "player_iid_to_entry", {}).items():
            if visible_entry.player_id != player_id or not self.player_tree.exists(iid):
                continue
            values = list(self.player_tree.item(iid, "values"))
            if len(values) >= 3:
                values[0] = overall if overall is not None else "--"
                values[1] = position
                values[2] = player_type
                self.player_tree.item(iid, values=values, tags=(self._overall_row_tag(overall),))

    def _team_json(self, team: TeamRecord) -> dict[str, object]:
        return {
            "code": team.code,
            "abbrev": team.abbrev,
            "name": team.name,
            "city": team.city,
        }

    def _combo_search_values(self, values: list[str], typed: str) -> list[str]:
        query = " ".join(typed.lower().split())
        if not query:
            return values
        terms = query.split()
        matches = [
            value
            for value in values
            if all(term in value.lower() for term in terms)
        ]
        return matches or values

    def _filter_team_combo(self, event=None) -> None:
        if event is not None and event.keysym in {"Up", "Down", "Left", "Right", "Return", "Escape", "Tab"}:
            return
        combo = event.widget if event is not None else None
        if combo is None:
            return
        source_values = self.team_choice_values
        if hasattr(self, "bulk_org_combo") and combo == self.bulk_org_combo:
            selected_league = self.bulk_league_var.get() or "NHL"
            source_values = [
                value for value in self.team_choice_values
                if selected_league == "All Leagues" or value.endswith(f"[{selected_league}]")
            ]
        combo.configure(values=self._combo_search_values(source_values, combo.get())[:80])

    def _filter_roster_team_combo(self, event=None) -> None:
        if event is not None and event.keysym in {"Up", "Down", "Left", "Right", "Return", "Escape", "Tab"}:
            return
        values = getattr(self, "roster_team_values", ["All Teams", "Free Agents"])
        self.team_combo.configure(values=self._combo_search_values(values, self.team_combo.get())[:80])

    def _is_real_comparable_player(self, player: PlayerListEntry) -> bool:
        name = player.full_name.strip()
        if not name or player.is_hidden:
            return False
        if re.search(r"\b(system|custom team|created player|placeholder|prospects? \d+)\b", name, re.IGNORECASE):
            return False
        if re.match(r"^\d{4}\b", name):
            return False
        return bool(player.first_name.strip() and player.last_name.strip())

    def _real_comparable_players(self) -> list[PlayerListEntry]:
        return self.comparable_players

    def _compare_display(self, player: PlayerListEntry) -> str:
        return f"{player.full_name} [{player.current_team_abbrev or 'FA'} | {player.league_name} | ID {player.player_id}]"

    def _refresh_compare_search_results(self) -> None:
        if not hasattr(self, "compare_results_listbox"):
            return
        query = " ".join(self.compare_search_var.get().lower().split())
        terms = query.split()
        self.compare_display_to_entry: dict[str, PlayerListEntry] = {}
        self.compare_results_listbox.delete(0, "end")
        if not terms:
            return
        for player in self._real_comparable_players():
            display = self._compare_display(player)
            if not all(term in display.lower() for term in terms):
                continue
            self.compare_display_to_entry[display] = player
            self.compare_results_listbox.insert("end", display)
            if self.compare_results_listbox.size() >= 200:
                break

    def _compare_name_from_display(self, display: str) -> str:
        return display.split(" [", 1)[0].strip()

    def _add_compare_player(self) -> None:
        if not hasattr(self, "compare_sources_listbox"):
            return
        selection = self.compare_results_listbox.curselection() if hasattr(self, "compare_results_listbox") else ()
        display = self.compare_results_listbox.get(selection[0]) if selection else ""
        if not display:
            self._set_status("Search for a comparable player first.")
            return
        existing = set(self.compare_sources_listbox.get(0, "end"))
        if display not in existing:
            self.compare_sources_listbox.insert("end", display)
        self.compare_search_var.set("")
        self._set_status(f"Added comparable: {self._compare_name_from_display(display)}.")

    def _remove_compare_player(self) -> None:
        if not hasattr(self, "compare_sources_listbox"):
            return
        for index in reversed(self.compare_sources_listbox.curselection()):
            self.compare_sources_listbox.delete(index)
        self._set_status("Removed selected comparable(s).")

    def _comparison_source_entries(self) -> list[PlayerListEntry]:
        entries: list[PlayerListEntry] = []
        if hasattr(self, "compare_sources_listbox"):
            displays = [str(value).strip() for value in self.compare_sources_listbox.get(0, "end") if str(value).strip()]
        else:
            displays = []
        seen_ids: set[int] = set()
        for display in displays:
            match = re.search(r"\| ID (\d+)\]$", display)
            player_id = _safe_int(match.group(1), -1) if match else -1
            entry = self.player_by_id.get(player_id)
            if entry is None or entry.player_id in seen_ids or not self._is_real_comparable_player(entry):
                continue
            seen_ids.add(entry.player_id)
            entries.append(entry)
        return entries

    def _current_team_from_snapshot(self) -> dict[str, object] | None:
        if self.snapshot is None:
            return None
        candidates: list[tuple[int, int, int, TeamRecord | None]] = []
        for row in self.snapshot.instance_rows:
            instance_id = _safe_int(row.get("TWSX"), -1)
            team_code = _safe_int(row.get("BSXd"), -1)
            team = self.team_by_code.get(team_code)
            score = 0
            if team is not None:
                league = league_name_for_team(team)
                if league == "NHL" and 0 <= team.code <= 29:
                    score += 300
                elif league == "NHL":
                    score += 260
                elif league == "AHL":
                    score += 220
                elif league in {"Organization", "Prospects"}:
                    score += 200
                elif league in {"International", "World Cup", "Exhibition"}:
                    score += 30
                else:
                    score += 100
            if _safe_int(row.get("XWot"), 0) < 2000:
                score += 20
            if _safe_int(row.get("Imzy"), 0) == 0:
                score += 5
            candidates.append((-score, instance_id, team_code, team))
        if not candidates:
            return None
        candidates.sort()
        _score, instance_id, team_code, team = candidates[0]
        return {
            "instance_id": instance_id,
            "team_code": team_code,
            "team": None if team is None else self._team_json(team),
        }

    def _rebuild_team_choices(self) -> None:
        self.team_display_to_abbrev.clear()
        target_values: list[str] = []
        for team in sorted(self.teams, key=lambda item: (league_name_for_team(item), item.city, item.abbrev)):
            display = f"{team.abbrev} - {team.city} {team.name} [{league_name_for_team(team)}]"
            self.team_display_to_abbrev[display] = team.abbrev
            target_values.append(display)
        self.team_choice_values = target_values
        self.target_team_combo.configure(values=target_values)
        if target_values and not self.target_team_var.get():
            self.target_team_var.set(target_values[0])
        if hasattr(self, "move_left_combo"):
            self.move_left_combo.configure(values=target_values)
            self.move_right_combo.configure(values=target_values)
            if target_values and not self.move_left_team_var.get():
                self.move_left_team_var.set(self._team_display_for_abbrev("TOR") or target_values[0])
            if target_values and not self.move_right_team_var.get():
                self.move_right_team_var.set(self._team_display_for_abbrev("TB") or target_values[min(1, len(target_values) - 1)])
        if hasattr(self, "org_team_combo"):
            self.org_team_combo.configure(values=target_values)
        if hasattr(self, "bulk_org_combo"):
            self._on_bulk_league_changed()
        if hasattr(self, "potential_team_combo"):
            self._on_potential_league_changed()
        if hasattr(self, "draft_pick_team_combo"):
            self.draft_pick_team_combo.configure(values=target_values)
            if target_values and not self.draft_pick_team_var.get():
                self.draft_pick_team_var.set(self._team_display_for_abbrev("TOR") or target_values[0])
        if hasattr(self, "compare_results_listbox"):
            self._refresh_compare_search_results()
        self._on_league_changed()

    def _on_bulk_league_changed(self, _event=None) -> None:
        if not hasattr(self, "bulk_org_combo"):
            return
        selected_league = self.bulk_league_var.get() or "NHL"
        values = [
            display
            for display in self.team_choice_values
            if selected_league == "All Leagues" or display.endswith(f"[{selected_league}]")
        ]
        self.bulk_org_combo.configure(values=values)
        if self.bulk_org_var.get() not in values:
            preferred = self._team_display_for_abbrev("TOR") if selected_league == "NHL" else None
            self.bulk_org_var.set(preferred if preferred in values else (values[0] if values else ""))

    def _on_potential_league_changed(self, _event=None) -> None:
        if not hasattr(self, "potential_team_combo"):
            return
        selected_league = self.potential_league_filter_var.get() or "All Leagues"
        team_values = ["All Teams"]
        team_values.extend(
            display
            for display in self.team_choice_values
            if selected_league == "All Leagues" or display.endswith(f"[{selected_league}]")
        )
        self.potential_team_combo.configure(values=team_values)
        if self.potential_team_filter_var.get() not in team_values:
            self.potential_team_filter_var.set("All Teams")
        self._refresh_potential_tree()

    def _team_display_for_abbrev(self, abbrev: str) -> str | None:
        target = abbrev.upper()
        for display, candidate in self.team_display_to_abbrev.items():
            if candidate.upper() == target:
                return display
        return None

    def _on_league_changed(self) -> None:
        league = self.league_var.get()
        values = ["All Teams", "Free Agents", "Hidden"]
        self.roster_team_filter_display_to_filter = {
            "All Teams": ("all", None),
            "Free Agents": ("free", None),
            "Hidden": ("hidden", None),
        }
        exact_counts: dict[str, int] = {}
        org_counts: dict[str, int] = {}
        for player in self.player_index:
            if player.current_team_abbrev:
                exact_counts[player.current_team_abbrev.upper()] = exact_counts.get(player.current_team_abbrev.upper(), 0) + 1
            if player.organization_abbrev:
                org_counts[player.organization_abbrev.upper()] = org_counts.get(player.organization_abbrev.upper(), 0) + 1
        if league in {"All Leagues", "NHL", "Organization"}:
            for org_abbrev, count in sorted(org_counts.items()):
                parent_team = self._parent_team_for_org(org_abbrev)
                if parent_team is None:
                    display = f"{org_abbrev} Organization ({count} players)"
                else:
                    display = f"{parent_team.city} {parent_team.name} Organization ({org_abbrev}, {count} players)"
                values.append(display)
                self.roster_team_filter_display_to_filter[display] = ("org", org_abbrev)
        for team in sorted(self.teams, key=lambda item: (item.city, item.abbrev)):
            team_league = league_name_for_team(team)
            if league != "All Leagues" and team_league != league:
                continue
            count = exact_counts.get(team.abbrev.upper(), 0)
            display = f"{team.abbrev} - {team.city} {team.name} [{team_league}, {count} players]"
            values.append(display)
            self.roster_team_filter_display_to_filter[display] = ("exact", team.abbrev.upper())
        self.roster_team_values = values
        self.team_combo.configure(values=values)
        if self.team_var.get() not in values:
            self.team_var.set(values[0] if values else "All Teams")
        self._refresh_player_list()

    def _parent_team_for_org(self, org_abbrev: str) -> TeamRecord | None:
        target = normalize_org_abbrev(org_abbrev)
        if target is None:
            return None
        matches = [
            team
            for team in self.teams
            if normalize_org_abbrev(team.abbrev) == target and league_name_for_team(team) == "NHL"
        ]
        if not matches:
            return None
        matches.sort(key=lambda team: (0 if 0 <= team.code <= 31 else 1, team.code))
        return matches[0]

    def _refresh_player_list(self) -> None:
        self._clear_tree(self.player_tree)
        league = self.league_var.get()
        team_filter = self.team_var.get()
        filter_kind, filter_value = self.roster_team_filter_display_to_filter.get(
            team_filter,
            ("all", None),
        )
        if filter_kind == "all" and team_filter.strip():
            typed_team = self._team_abbrev_from_display(team_filter)
            if typed_team:
                filter_kind, filter_value = ("exact", typed_team.upper())
        search = self.search_var.get().strip().lower()
        inserted = 0
        self.player_iid_to_entry: dict[str, PlayerListEntry] = {}
        if filter_kind == "exact" and filter_value:
            source_entries = self.players_by_team_abbrev.get(filter_value.upper(), [])
        elif filter_kind == "org" and filter_value:
            source_entries = self.players_by_org_abbrev.get(filter_value.upper(), [])
        else:
            source_entries = self.player_index
        visible_entries: list[PlayerListEntry] = []
        for entry in source_entries:
            if filter_kind not in {"org"} and league != "All Leagues" and entry.league_name != league:
                continue
            if filter_kind == "free" and entry.league_name != "Free Agents":
                continue
            if filter_kind == "hidden" and not entry.is_hidden:
                continue
            if filter_kind == "exact" and (entry.current_team_abbrev or "").upper() != (filter_value or ""):
                continue
            if filter_kind == "org" and (entry.organization_abbrev or "").upper() != (filter_value or ""):
                continue
            if search and search not in entry.full_name.lower():
                continue
            visible_entries.append(entry)
        visible_entries.sort(key=self._roster_sort_value, reverse=self.roster_sort_descending)
        selected_iid = None
        selected_player_id = self.selected_player.player_id if self.selected_player is not None else None
        for entry in visible_entries:
            iid = str(entry.player_id)
            suffix = 1
            while self.player_tree.exists(iid):
                suffix += 1
                iid = f"{entry.player_id}-{suffix}"
            overall = self.player_overall_by_id.get(entry.player_id)
            self.player_tree.insert(
                "",
                "end",
                iid=iid,
                text=entry.full_name,
                values=(
                    overall if overall is not None else "--",
                    self.player_position_by_id.get(entry.player_id, "Unknown"),
                    self.player_type_by_id.get(entry.player_id, "Unknown"),
                    entry.current_team_abbrev or ("Hidden" if entry.is_hidden else "FA"),
                    entry.league_name,
                    entry.organization_abbrev or "",
                ),
                tags=(self._overall_row_tag(overall),),
            )
            self.player_iid_to_entry[iid] = entry
            if entry.player_id == selected_player_id and selected_iid is None:
                selected_iid = iid
            inserted += 1
        if selected_iid is not None:
            self.player_tree.selection_set(selected_iid)
            self.player_tree.focus(selected_iid)
            self.player_tree.see(selected_iid)
        self.roster_count_var.set(f"{inserted} shown / {len(self.player_index)} players")

    def _team_abbrev_from_display(self, display: str) -> str | None:
        if display in self.team_display_to_abbrev:
            return self.team_display_to_abbrev[display]
        cleaned = display.strip().upper()
        if " - " in cleaned:
            cleaned = cleaned.split(" - ", 1)[0].strip()
        if cleaned in {team.abbrev.upper() for team in self.teams}:
            return cleaned
        lowered = display.lower().strip()
        matches = [
            abbrev
            for team_display, abbrev in self.team_display_to_abbrev.items()
            if lowered and lowered in team_display.lower()
        ]
        return matches[0] if len(matches) == 1 else None

    def _players_for_exact_team(self, abbrev: str | None) -> list[PlayerListEntry]:
        if not abbrev:
            return []
        return self.players_by_team_abbrev.get(abbrev.upper(), [])

    def _refresh_trade_lanes(self) -> None:
        if not hasattr(self, "move_left_tree"):
            return
        left_abbrev = self._team_abbrev_from_display(self.move_left_team_var.get())
        right_abbrev = self._team_abbrev_from_display(self.move_right_team_var.get())
        self.move_left_title_var.set(f"{left_abbrev or 'Team 1'} Players")
        self.move_right_title_var.set(f"{right_abbrev or 'Team 2'} Players")
        self.move_left_iid_to_entry = self._fill_trade_tree(self.move_left_tree, self._players_for_exact_team(left_abbrev), "L")
        self.move_right_iid_to_entry = self._fill_trade_tree(self.move_right_tree, self._players_for_exact_team(right_abbrev), "R")

    def _fill_trade_tree(self, tree: ttk.Treeview, players: list[PlayerListEntry], prefix: str) -> dict[str, PlayerListEntry]:
        self._clear_tree(tree)
        lookup: dict[str, PlayerListEntry] = {}
        for index, entry in enumerate(sorted(players, key=lambda item: (item.last_name, item.first_name))):
            iid = f"{prefix}-{entry.player_id}-{index}"
            tree.insert(
                "",
                "end",
                iid=iid,
                text=entry.full_name,
                values=(entry.current_team_abbrev or "FA", entry.league_name, entry.organization_abbrev or ""),
            )
            lookup[iid] = entry
        return lookup

    def _selected_trade_entries(self, side: str) -> list[PlayerListEntry]:
        if side == "left":
            return [self.move_left_iid_to_entry[item] for item in self.move_left_tree.selection() if item in self.move_left_iid_to_entry]
        return [self.move_right_iid_to_entry[item] for item in self.move_right_tree.selection() if item in self.move_right_iid_to_entry]

    def _move_between_lanes(self, direction: str) -> None:
        if self.workspace is None:
            return
        if direction == "left_to_right":
            entries = self._selected_trade_entries("left")
            target = self._team_abbrev_from_display(self.move_right_team_var.get())
        else:
            entries = self._selected_trade_entries("right")
            target = self._team_abbrev_from_display(self.move_left_team_var.get())
        if not entries or not target:
            self._set_status("Select players and a target team first.")
            return
        entries = list(entries)

        def worker():
            errors: list[str] = []
            try:
                results = move_players_to_teams(
                    self.workspace.working_db,
                    [(entry.first_name, entry.last_name, target, entry.player_id) for entry in entries],
                    snapshot_cache=self.player_snapshot_cache,
                    cached_team_by_code=self.team_by_code,
                )
            except Exception as exc:
                results = []
                errors.append(str(exc))
            sync_working_db_to_roster(self.workspace)
            return results, errors

        def success(result):
            results, errors = result
            for move_result in results:
                self._log_action("move-player", move_result)
            self._reload_after_player_write(
                f"Moved {len(results)} player(s) to {target}.",
                results=results,
            )
            if errors:
                messagebox.showwarning("Some moves failed", "\n".join(errors[:8]))

        self._run_background("Moving players", worker, success)

    def _drop_lane_selection_to_fa(self) -> None:
        if self.workspace is None:
            return
        entries = self._selected_trade_entries("left") + self._selected_trade_entries("right")
        if not entries:
            self._set_status("Select players in either team list first.")
            return
        if not messagebox.askyesno("Drop To Free Agency", f"Move {len(entries)} selected player(s) to free agency?"):
            return
        entries = list(entries)

        def worker():
            results = move_players_to_free_agency(
                self.workspace.working_db,
                [(entry.first_name, entry.last_name, entry.player_id) for entry in entries],
                snapshot_cache=self.player_snapshot_cache,
                cached_team_by_code=self.team_by_code,
            )
            sync_working_db_to_roster(self.workspace)
            return results

        def success(results):
            for result in results:
                self._log_action("move-to-free-agency", result)
            self._reload_after_player_write(
                f"Moved {len(results)} player(s) to {FREE_AGENCY_LABEL}.",
                results=results,
            )

        self._run_background("Moving players to free agency", worker, success)

    def _on_trade_lane_double_click(self, event) -> None:
        if event.widget == self.move_left_tree:
            self._move_between_lanes("left_to_right")
        elif event.widget == self.move_right_tree:
            self._move_between_lanes("right_to_left")

    def _on_player_selected(self, _event=None) -> None:
        selection = self.player_tree.selection()
        if not selection:
            return
        entry = self.player_iid_to_entry.get(selection[0])
        if entry is None:
            return
        self._load_player(entry)

    def _show_roster_context_menu(self, event) -> None:
        iid = self.player_tree.identify_row(event.y)
        if iid:
            self.player_tree.selection_set(iid)
            self._on_player_selected()
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Edit Player", command=lambda: self.tabs.select(self.player_tab))
        menu.add_command(label="Attributes", command=lambda: self.tabs.select(self.attributes_tab))
        menu.add_command(label="Send To Free Agency", command=self._send_selected_to_free_agency)
        menu.add_separator()
        menu.add_command(label="Move With Two-Team Screen", command=lambda: self.tabs.select(self.movement_tab))
        menu.tk_popup(event.x_root, event.y_root)

    def _show_trade_context_menu(self, event) -> None:
        tree = event.widget
        iid = tree.identify_row(event.y)
        if iid:
            tree.selection_set(iid)
        direction = "left_to_right" if tree == self.move_left_tree else "right_to_left"
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Move Across", command=lambda: self._move_between_lanes(direction))
        menu.add_command(label="Drop To Free Agency", command=self._drop_lane_selection_to_fa)
        menu.tk_popup(event.x_root, event.y_root)

    def _show_update_context_menu(self, event) -> None:
        iid = self.update_tree.identify_row(event.y)
        if iid:
            self.update_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Apply Selected Move(s)", command=self._apply_selected_update_moves)
        menu.add_command(label="Veto Selected Move(s)", command=self._veto_selected_update_moves)
        menu.tk_popup(event.x_root, event.y_root)

    def _show_review_context_menu(self, event) -> None:
        iid = self.review_tree.identify_row(event.y)
        if iid:
            self.review_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Apply Pending Move Now", command=self._apply_selected_review_move)
        menu.add_command(label="Veto Pending Move", command=self._veto_selected_review_move)
        menu.add_command(label="Revert Logged Edit", command=self._revert_selected_review_change)
        menu.add_separator()
        menu.add_command(label="Save To Game", command=self._save_to_game)
        menu.tk_popup(event.x_root, event.y_root)

    def _load_player(self, entry: PlayerListEntry) -> None:
        if self.workspace is None:
            return
        try:
            self.selected_player = entry
            if self.player_snapshot_cache is not None:
                self.snapshot = self.player_snapshot_cache.get_player_snapshot(entry.first_name, entry.last_name, entry.player_id)
            else:
                self.snapshot = get_player_snapshot(self.workspace.working_db, entry.first_name, entry.last_name, entry.player_id)
            if self.snapshot is None:
                raise RuntimeError(f"Player not found: {entry.full_name}")
            self.current_team = self._current_team_from_snapshot()
            self.player_title_var.set(entry.full_name)
            self.player_subtitle_var.set(
                f"{entry.current_team_abbrev or ('Hidden' if entry.is_hidden else 'FA')} | {entry.league_name} | Organization: {entry.organization_abbrev or 'None'}"
            )
            if hasattr(self, "create_name_var") and not self.create_name_var.get().strip():
                self.create_name_var.set(entry.full_name)
            self._populate_player_info()
            self._populate_attributes()
            self._populate_manual_contract_editor()
            self.capwages_player = None
            self.official_player_hit = None
            self.contract_detail_text.delete("1.0", "end")
            self.edge_text.delete("1.0", "end")
            self.edge_suggestions = {}
            self.edge_suggestion_notes = {}
            self.edge_metric_context = {}
            self._refresh_attribute_edge_notes()
            self._set_status(f"Loaded {entry.full_name}")
        except Exception as exc:
            self._show_error("Load player failed", exc)

    def _ratings_row_for_selected_player(self) -> dict[str, object] | None:
        if self.snapshot is None:
            return None
        if self._player_kind() == "goalie":
            return self.snapshot.goalie_ratings_row
        return self.snapshot.ratings_row

    def _attribute_specs_for_selected_player(self):
        return specs_for_player_kind(self._player_kind())

    def _update_selected_player_ratings(self, updates: dict[str, int]) -> dict[str, object]:
        if self.workspace is None or self.selected_player is None:
            raise RuntimeError("No player selected.")
        prepared = [(
            self.selected_player.first_name,
            self.selected_player.last_name,
            updates,
            self.selected_player.player_id,
        )]
        if self._player_kind() == "goalie":
            return update_many_player_goalie_ratings(
                self.workspace.working_db,
                prepared,
                snapshot_cache=self.player_snapshot_cache,
            )[0]
        return update_many_player_ratings(
            self.workspace.working_db,
            prepared,
            snapshot_cache=self.player_snapshot_cache,
        )[0]

    def _populate_player_info(self) -> None:
        if self.selected_player is None or self.snapshot is None:
            return
        bio = self.snapshot.bio
        self.info_vars["first_name"].set(str(bio.get("PedH") or self.selected_player.first_name))
        self.info_vars["last_name"].set(str(bio.get("RMbQ") or self.selected_player.last_name))
        self.info_vars["player_id"].set(str(bio.get("zIBw") or self.selected_player.player_id))
        instance_id = ""
        if self.current_team:
            instance_id = str(self.current_team.get("instance_id") or "")
        self.info_vars["instance_id"].set(instance_id)
        team_label = self.selected_player.current_team_abbrev or ("Hidden / no team instance" if self.selected_player.is_hidden else FREE_AGENCY_LABEL)
        self.info_vars["team"].set(team_label)
        self.info_vars["organization"].set(self.selected_player.organization_abbrev or "")
        self.info_vars["league"].set(self.selected_player.league_name)
        junior = self.selected_player.current_team_name if self.selected_player.league_name == "CHL / Juniors" else "Not mapped / not currently a CHL player"
        self.info_vars["junior_rights"].set(junior or "")
        self.info_vars["jersey"].set(str(bio.get("tRVs") or ""))
        self.info_vars["birthplace"].set(str(bio.get("JzFM") or ""))
        ratings = self._ratings_row_for_selected_player() or {}
        position_label = self._position_label_from_code(_safe_int(bio.get("aljv"), -1))
        self.info_vars["position_label"].set(position_label)
        instance = {}
        current_instance_id = None if not self.current_team else _safe_int(self.current_team.get("instance_id"), -1)
        if current_instance_id is not None:
            instance = next(
                (
                    row
                    for row in self.snapshot.instance_rows
                    if _safe_int(row.get("TWSX"), -2) == current_instance_id
                ),
                {},
            )
        if not instance:
            instance = self.snapshot.instance_rows[0] if self.snapshot.instance_rows else {}
        rating_style_code = None if not ratings else _safe_int(ratings.get("sFgQ"), -1)
        instance_style_code = None if not instance else _safe_int(instance.get("sFgQ"), -1)
        style_code = instance_style_code if instance_style_code is not None and instance_style_code >= 0 else rating_style_code
        fighting_code = None if not ratings else _safe_int(ratings.get("YqJH"), -1)
        self.style_combo.configure(values=self._style_choices_for_selected_player())
        if hasattr(self, "attribute_style_combo"):
            self.attribute_style_combo.configure(values=self._style_choices_for_selected_player())
        style_label = self._style_label_from_code(style_code)
        fighting_label = next((label for label, code in FIGHTING_CODES.items() if code == fighting_code), UNMAPPED_CHOICE)
        if self._player_kind() == "goalie" and fighting_label == UNMAPPED_CHOICE:
            fighting_label = "Never"
        self.info_vars["style_label"].set(style_label)
        self.info_vars["fighting_label"].set(fighting_label if fighting_label in FIGHTING_CODES else UNMAPPED_CHOICE)
        self._sync_archetype_to_player_type(style_label)
        if (
            rating_style_code is not None
            and instance_style_code is not None
            and rating_style_code >= 0
            and instance_style_code >= 0
            and rating_style_code != instance_style_code
        ):
            self.style_hint_var.set(
                "This player has mismatched base/team style rows. Saving Player Info will sync every linked style row."
            )
        else:
            self.style_hint_var.set("Mapped choices save to the roster. If a current value is unknown, choose a mapped value to overwrite it.")
        self._load_potential_note()

    def _player_kind(self) -> str:
        if self.snapshot is None:
            return "unknown"
        selected_position = self.info_vars.get("position_label").get() if hasattr(self, "info_vars") and "position_label" in self.info_vars else ""
        if selected_position == "G":
            return "goalie"
        if selected_position == "D":
            return "defense"
        if selected_position in {"C", "LW", "RW"}:
            return "forward"
        bio_kind = _safe_int(self.snapshot.bio.get("aljv"), -1)
        if bio_kind == 4 or self.snapshot.ratings_row is None:
            return "goalie"
        if bio_kind == 3:
            return "defense"
        if bio_kind in {0, 1, 2}:
            return "forward"
        return "unknown"

    def _position_label_from_code(self, code: int | None) -> str:
        return next((label for label, value in POSITION_CODES.items() if value == code), UNMAPPED_CHOICE)

    def _refresh_style_choices_for_position(self) -> None:
        if not hasattr(self, "style_combo"):
            return
        current = self.info_vars["style_label"].get()
        choices = self._style_choices_for_selected_player()
        self.style_combo.configure(values=choices)
        if hasattr(self, "attribute_style_combo"):
            self.attribute_style_combo.configure(values=choices)
        if current not in choices:
            self.info_vars["style_label"].set(UNMAPPED_CHOICE)
        self._sync_archetype_to_player_type(self.info_vars["style_label"].get())

    def _sync_archetype_to_player_type(self, style_label: str) -> None:
        if not hasattr(self, "archetype_var"):
            return
        archetype = STYLE_TO_ARCHETYPE.get(style_label)
        if archetype in ARCHETYPE_WEIGHTS:
            self.archetype_var.set(archetype)

    def _style_choices_for_selected_player(self) -> list[str]:
        kind = self._player_kind()
        if kind == "goalie":
            return [UNMAPPED_CHOICE, *GOALIE_STYLE_CODES.keys()]
        if kind == "defense":
            return [UNMAPPED_CHOICE, *DEFENSE_STYLE_CODES.keys()]
        if kind == "forward":
            return [UNMAPPED_CHOICE, *FORWARD_STYLE_CODES.keys()]
        return [UNMAPPED_CHOICE, *PLAYER_STYLE_CODES.keys()]

    def _style_label_from_code(self, code: int | None) -> str:
        if code is None:
            return UNMAPPED_CHOICE
        kind = self._player_kind()
        style_map = PLAYER_STYLE_CODES
        if kind == "goalie":
            style_map = GOALIE_STYLE_CODES
        elif kind == "defense":
            style_map = DEFENSE_STYLE_CODES
        elif kind == "forward":
            style_map = FORWARD_STYLE_CODES
        return next((label for label, value in style_map.items() if value == code), UNMAPPED_CHOICE)

    def _style_code_for_selected_player(self, label: str) -> int | None:
        kind = self._player_kind()
        if kind == "goalie":
            return GOALIE_STYLE_CODES.get(label)
        if kind == "defense":
            return DEFENSE_STYLE_CODES.get(label)
        if kind == "forward":
            return FORWARD_STYLE_CODES.get(label)
        matches = [
            code
            for style_map in (FORWARD_STYLE_CODES, DEFENSE_STYLE_CODES, GOALIE_STYLE_CODES)
            for style_label, code in style_map.items()
            if style_label == label
        ]
        return matches[0] if len(set(matches)) == 1 else None

    def _populate_attributes(self) -> None:
        for child in self.attribute_scroll.inner.winfo_children():
            child.destroy()
        self.attribute_vars.clear()
        self.attribute_edge_vars.clear()
        self.attribute_original_values.clear()
        ratings_row = self._ratings_row_for_selected_player()
        if self.snapshot is None or not ratings_row:
            ttk.Label(self.attribute_scroll.inner, text="No ratings row found for this player.", style="Muted.TLabel").pack(anchor="w", padx=12, pady=12)
            return
        specs = self._attribute_specs_for_selected_player()
        compact = self._use_compact_layout()
        per_column = len(specs) if compact else 14
        for index, spec in enumerate(specs):
            row = index % per_column
            column = (index // per_column) * 4
            raw_value = _safe_int(ratings_row.get(spec.field), 0)
            display_value = raw_to_display(spec, raw_value)
            self.attribute_original_values[spec.field] = display_value
            var = tk.IntVar(value=display_value)
            edge_var = tk.StringVar(value="")
            self.attribute_vars[spec.field] = var
            self.attribute_edge_vars[spec.label] = edge_var
            ttk.Label(self.attribute_scroll.inner, text=spec.label).grid(row=row, column=column, sticky="w", padx=(8, 8), pady=5)
            ttk.Scale(
                self.attribute_scroll.inner,
                variable=var,
                from_=spec.min_value if spec.mode == "raw" else spec.min_value + 36,
                to=spec.max_value,
                orient="horizontal",
                length=165 if compact else 210,
                style="Attribute.Horizontal.TScale",
                command=lambda _value: self._update_attribute_budget(),
            ).grid(row=row, column=column + 1, sticky="ew", padx=(0, 8), pady=5)
            tk.Spinbox(
                self.attribute_scroll.inner,
                textvariable=var,
                from_=spec.min_value if spec.mode == "raw" else spec.min_value + 36,
                to=spec.max_value,
                width=5,
                command=self._update_attribute_budget,
                background="#0b1118",
                foreground=self.colors["ink"],
                insertbackground=self.colors["ink"],
                buttonbackground=self.colors["panel3"],
            ).grid(row=row, column=column + 2, sticky="w", padx=(0, 8), pady=5)
            ttk.Label(
                self.attribute_scroll.inner,
                textvariable=edge_var,
                style="Muted.TLabel",
                wraplength=175 if compact else 215,
            ).grid(row=row, column=column + 3, sticky="w", padx=(0, 18), pady=5)
            var.trace_add("write", lambda *_: self._update_attribute_budget())
        self.attribute_scroll.inner.columnconfigure(1, weight=1)
        if not compact:
            self.attribute_scroll.inner.columnconfigure(5, weight=1)
        self._refresh_attribute_edge_notes()
        self._update_attribute_budget()

    def _refresh_attribute_edge_notes(self) -> None:
        if not self.attribute_edge_vars:
            return
        for label, var in self.attribute_edge_vars.items():
            suggestion = self.edge_suggestions.get(label)
            note = self.edge_suggestion_notes.get(label, "")
            if suggestion is None and not note:
                var.set("")
                continue
            if suggestion is None:
                var.set(f"Metrics: {note}")
            elif self._current_attribute_rating(label) == suggestion:
                var.set(f"Metrics target {suggestion} (current)")
            elif note:
                var.set(f"Metrics {suggestion}: {note}")
            else:
                var.set(f"Metrics {suggestion}")

    def _populate_raw_flags(self) -> None:
        for child in self.flags_frame.winfo_children():
            child.destroy()
        self.flags_vars.clear()
        if self.snapshot is None or not self.snapshot.flags_row:
            ttk.Label(self.flags_frame, text="No flags row found.", style="Muted.TLabel").pack(anchor="w")
            return
        editable = [(key, value) for key, value in sorted(self.snapshot.flags_row.items()) if key != "zIBw" and isinstance(value, int)]
        for index, (field, value) in enumerate(editable):
            row = index // 4
            column = (index % 4) * 2
            var = tk.IntVar(value=int(value))
            self.flags_vars[field] = var
            ttk.Label(self.flags_frame, text=field).grid(row=row, column=column, sticky="e", padx=(8, 4), pady=4)
            tk.Spinbox(
                self.flags_frame,
                textvariable=var,
                from_=0,
                to=9999,
                width=8,
                background="#0b1118",
                foreground=self.colors["ink"],
                insertbackground=self.colors["ink"],
                buttonbackground=self.colors["panel3"],
            ).grid(row=row, column=column + 1, sticky="w", padx=(0, 12), pady=4)

    def _ratings_as_semantic(self, *, current: bool) -> dict[str, int]:
        values: dict[str, int] = {}
        for spec in self._attribute_specs_for_selected_player():
            semantic = LABEL_TO_SEMANTIC.get(spec.label)
            if semantic is None:
                continue
            if current and spec.field in self.attribute_vars:
                values[semantic] = _safe_int(self.attribute_vars[spec.field].get(), 0)
            else:
                values[semantic] = self.attribute_original_values.get(spec.field, 0)
        return values

    def _update_attribute_budget(self) -> None:
        if not self.attribute_vars:
            return
        if self._player_kind() == "goalie":
            display_values = {
                spec.label: _safe_int(self.attribute_vars[spec.field].get(), 0)
                for spec in self._attribute_specs_for_selected_player()
                if spec.field in self.attribute_vars
            }
            style = self.info_vars["style_label"].get()
            if style not in GOALIE_STYLE_CODES:
                style = "Hybrid Goalie"
            overall = calculate_goalie_overall(display_values, style)
            self.attribute_summary_var.set(
                f"EA OVR ESTIMATE  {overall}   |   {style}"
            )
            return
        archetype = self.archetype_var.get()
        if archetype not in ARCHETYPE_WEIGHTS:
            return
        current = self._ratings_as_semantic(current=True)
        position = self.info_vars["position_label"].get()
        current_overall = calculate_weighted_overall(current, archetype, position=position)
        self.attribute_summary_var.set(
            f"EA OVR ESTIMATE  {current_overall}   |   {archetype.replace('_', ' ').title()}"
        )

    def _save_attributes(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        specs = attribute_specs_by_field(self._player_kind())
        updates: dict[str, int] = {}
        for field, var in self.attribute_vars.items():
            spec = specs[field]
            display_value = _safe_int(var.get(), self.attribute_original_values.get(field, 0))
            if display_value != self.attribute_original_values.get(field):
                updates[field] = display_to_raw(spec, display_value)
        style_code = self._style_code_for_selected_player(self.info_vars["style_label"].get())
        ratings_row = self._ratings_row_for_selected_player() or {}
        style_changed = style_code is not None and _safe_int(ratings_row.get("sFgQ"), -1) != style_code
        if style_changed:
            updates["sFgQ"] = int(style_code)
        instance_style_changed = style_code is not None and any(
            _safe_int(row.get("sFgQ"), -1) != style_code
            for row in (self.snapshot.instance_rows if self.snapshot is not None else [])
        )
        if not updates and not instance_style_changed:
            self._set_status("No attribute changes to save.")
            return
        displayed_by_label = {
            spec.label: _safe_int(self.attribute_vars[spec.field].get(), -1)
            for spec in self._attribute_specs_for_selected_player()
            if spec.field in self.attribute_vars
        }
        metric_target_applied = (
            bool(self.edge_metric_context)
            and _safe_int(self.edge_metric_context.get("player_id"), -1) == self.selected_player.player_id
            and metric_targets_match(displayed_by_label, self.edge_suggestions)
        )
        try:
            result = (
                self._update_selected_player_ratings(updates)
                if updates
                else {"player": self.selected_player.full_name, "updated_fields": {}, "changes": []}
            )
            instance_result = None
            if instance_style_changed and style_code is not None:
                instance_result = update_player_instance_fields(
                    self.workspace.working_db,
                    self.selected_player.first_name,
                    self.selected_player.last_name,
                    {"sFgQ": int(style_code)},
                    self.selected_player.player_id,
                    snapshot_cache=self.player_snapshot_cache,
                )
                result["instance_style"] = instance_result
            self._log_action("update-attributes", result)
            sync_working_db_to_roster(self.workspace)
            if ratings_row is not None:
                ratings_row.update(updates)
            if instance_result is not None and self.snapshot is not None:
                for row in self.snapshot.instance_rows:
                    row["sFgQ"] = int(style_code)
            if metric_target_applied:
                self._remember_advanced_metric_target(self.edge_metric_context)
                self.edge_suggestions = {}
                self.edge_suggestion_notes = {}
                self.edge_metric_context = {}
            self._populate_attributes()
            self._refresh_player_overall(self.selected_player.player_id)
            self._refresh_review()
            self._set_status("Saved attributes and player type.")
        except Exception as exc:
            self._show_error("Save attributes failed", exc)

    def _save_player_info(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        bio_updates = {
            "tRVs": _safe_int(self.info_vars["jersey"].get(), 0),
            "JzFM": self.info_vars["birthplace"].get().strip().upper(),
        }
        position_label = self.info_vars["position_label"].get()
        if position_label in POSITION_CODES:
            bio_updates["aljv"] = POSITION_CODES[position_label]
        rating_updates: dict[str, int] = {}
        instance_updates: dict[str, int] = {}
        style_label = self.info_vars["style_label"].get()
        fighting_label = self.info_vars["fighting_label"].get()
        style_code = self._style_code_for_selected_player(style_label)
        if style_code is not None:
            if self._ratings_row_for_selected_player() is not None:
                rating_updates["sFgQ"] = int(style_code)
            instance_updates["sFgQ"] = int(style_code)
        if fighting_label in FIGHTING_CODES and self._ratings_row_for_selected_player() is not None:
            rating_updates["YqJH"] = int(FIGHTING_CODES[fighting_label])
        try:
            bio_result = update_player_bio(
                self.workspace.working_db,
                self.selected_player.first_name,
                self.selected_player.last_name,
                bio_updates,
                self.selected_player.player_id,
                snapshot_cache=self.player_snapshot_cache,
            )
            rating_result = {"player": self.selected_player.full_name, "updated_fields": {}, "changes": []}
            instance_result = {"player": self.selected_player.full_name, "updated_fields": {}, "changes": []}
            if rating_updates:
                rating_result = self._update_selected_player_ratings(rating_updates)
            if instance_updates:
                instance_result = update_player_instance_fields(
                    self.workspace.working_db,
                    self.selected_player.first_name,
                    self.selected_player.last_name,
                    instance_updates,
                    self.selected_player.player_id,
                    snapshot_cache=self.player_snapshot_cache,
                )
            self._log_action("update-player-info", {"bio": bio_result, "ratings": rating_result, "instance": instance_result})
            sync_working_db_to_roster(self.workspace)
            if self.snapshot is not None:
                self.snapshot.bio.update(bio_updates)
                ratings_row = self._ratings_row_for_selected_player()
                if ratings_row is not None:
                    ratings_row.update(rating_updates)
                for row in self.snapshot.instance_rows:
                    row.update(instance_updates)
            self._populate_player_info()
            self._populate_attributes()
            self._refresh_player_overall(self.selected_player.player_id)
            self._refresh_review()
            self._set_status("Saved player info/type.")
        except Exception as exc:
            self._show_error("Save player info failed", exc)

    def _save_raw_flags(self) -> None:
        if self.workspace is None or self.selected_player is None or not self.flags_vars:
            return
        updates = {field: _safe_int(var.get(), 0) for field, var in self.flags_vars.items()}
        try:
            result = update_player_flags(
                self.workspace.working_db,
                self.selected_player.first_name,
                self.selected_player.last_name,
                updates,
                self.selected_player.player_id,
            )
            self._log_action("update-raw-flags", result)
            sync_working_db_to_roster(self.workspace)
            if self.snapshot is not None and self.snapshot.flags_row is not None:
                self.snapshot.flags_row.update(updates)
            self._populate_raw_flags()
            self._refresh_review()
            self._set_status("Saved raw flags.")
        except Exception as exc:
            self._show_error("Save raw flags failed", exc)

    def _apply_write_results_to_cache(self, results, *, hinted_kind: str | None = None) -> None:
        cache = self.player_snapshot_cache
        if cache is None or results is None:
            return
        if isinstance(results, (list, tuple)):
            for result in results:
                self._apply_write_results_to_cache(result, hinted_kind=hinted_kind)
            return
        if not isinstance(results, dict):
            return

        result_kind = str(results.get("player_kind") or hinted_kind or "")
        for key in ("bio", "ratings", "instance", "result"):
            nested = results.get(key)
            if isinstance(nested, (dict, list, tuple)) and nested is not results:
                self._apply_write_results_to_cache(nested, hinted_kind=result_kind or hinted_kind)

        player_id = _safe_int(results.get("player_id"), -1)
        if player_id < 0:
            return
        updates = results.get("updated_fields")
        updates = dict(updates) if isinstance(updates, dict) else {}
        sections = {
            str(change.get("section") or "")
            for change in results.get("changes", [])
            if isinstance(change, dict)
        }
        if updates:
            if result_kind == "goalie" or "goalie_ratings" in sections:
                target = cache.goalie_ratings_by_player_id.get(player_id)
            elif "ratings" in sections:
                target = cache.ratings_by_player_id.get(player_id)
            elif "flags" in sections:
                target = cache.flags_by_player_id.get(player_id)
            elif "instance_rows" in sections:
                target = None
                instance_ids = results.get("instance_ids") or []
                for instance_id in instance_ids:
                    instance_row = cache.instance_by_id.get(_safe_int(instance_id, -1))
                    if instance_row is not None:
                        instance_row.update(updates)
            else:
                target = cache.bio_by_player_id.get(player_id)
            if target is not None:
                target.update(updates)

        instance_id = _safe_int(results.get("instance_id"), -1)
        target_team_code = _safe_int(results.get("target_team_code"), -1)
        if instance_id >= 0 and target_team_code >= 0:
            instance_row = cache.instance_by_id.get(instance_id)
            if instance_row is not None:
                instance_row["BSXd"] = target_team_code
                if target_team_code == FREE_AGENCY_CODE:
                    instance_row["jZSh"] = 0
            if target_team_code == FREE_AGENCY_CODE:
                for aux_row in cache.instance_aux_by_id.get(instance_id, []):
                    for field in tuple(aux_row):
                        if field != "qEfv":
                            aux_row[field] = FREE_AGENCY_CODE if field == "BSXd" else 0
                bio = cache.bio_by_player_id.get(player_id)
                if bio is not None:
                    for field in ("dhKk", "GDhI", "NYKk", "DVoL", "IzRv", "IrlK", "xdoJ", "LcvS", "WBbd", "uWgv"):
                        bio[field] = 0

    def _reload_after_player_write(
        self,
        status: str,
        *,
        results=None,
        index_changed: bool = True,
    ) -> None:
        selected_id = None if self.selected_player is None else self.selected_player.player_id
        if results is None or self.player_snapshot_cache is None:
            self._rebuild_player_cache()
        else:
            self._apply_write_results_to_cache(results)
            if index_changed:
                self._refresh_player_index_from_cache()
        if index_changed:
            self._on_league_changed()
            self._refresh_trade_lanes()
            self._refresh_potential_tree()
        if selected_id is not None:
            entry = self.player_by_id.get(selected_id)
            if entry is not None:
                self._load_player(entry)
        self._refresh_review()
        self._set_status(status)

    def _move_selected_player(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        display = self.target_team_var.get()
        target = self.team_display_to_abbrev.get(display)
        if not target:
            self._set_status("Choose a target team first.")
            return
        player = self.selected_player

        def worker():
            result = move_players_to_teams(
                self.workspace.working_db,
                [(player.first_name, player.last_name, target, player.player_id)],
                snapshot_cache=self.player_snapshot_cache,
                cached_team_by_code=self.team_by_code,
            )[0]
            sync_working_db_to_roster(self.workspace)
            return result

        def success(result):
            self._log_action("move-player", result)
            self._reload_after_player_write(
                f"Moved {player.full_name} to {target}.",
                results=[result],
            )

        self._run_background("Moving player", worker, success)

    def _send_selected_to_free_agency(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        if not messagebox.askyesno("Send to Free Agency", f"Move {self.selected_player.full_name} to free agency/unassigned?"):
            return
        player = self.selected_player

        def worker():
            result = move_players_to_free_agency(
                self.workspace.working_db,
                [(player.first_name, player.last_name, player.player_id)],
                snapshot_cache=self.player_snapshot_cache,
                cached_team_by_code=self.team_by_code,
            )[0]
            sync_working_db_to_roster(self.workspace)
            return result

        def success(result):
            self._log_action("move-to-free-agency", result)
            self._reload_after_player_write(
                f"Moved {player.full_name} to {FREE_AGENCY_LABEL}.",
                results=[result],
            )

        self._run_background("Moving player to free agency", worker, success)

    def _load_remote_bio(self) -> None:
        if self.selected_player is None:
            return
        player_name = self.selected_player.full_name
        roster_team = self.selected_player.current_team_abbrev or self.selected_player.organization_abbrev
        position = (
            self._position_label_from_code(_safe_int(self.snapshot.bio.get("aljv"), -1))
            if self.snapshot is not None
            else ""
        )

        def worker():
            official = self._select_edge_hit(player_name, roster_team, position)
            landing = fetch_player_landing(official.player_id) if official else None
            hockeydb = fetch_hockeydb_profile_by_name(player_name)
            if (
                hockeydb is not None
                and position
                and hockeydb.position
                and self._metric_position_family(position) != self._metric_position_family(hockeydb.position)
            ):
                hockeydb = None
            return official, landing, hockeydb

        def success(result):
            official, landing, hockeydb = result
            self.official_player_hit = official
            lines = []
            if official:
                lines.append(f"NHL roster: {official.full_name} | {official.team_abbrev} | {official.position_code} | #{official.sweater_number or '?'}")
                lines.append(f"NHL size: {official.height_in_inches or '?'} in | {official.weight_in_pounds or '?'} lb | Shoots/Catches: {official.shoots_catches or '?'}")
            if landing:
                birth_city = ((landing.get("birthCity") or {}).get("default") if isinstance(landing.get("birthCity"), dict) else landing.get("birthCity"))
                birth_state = ((landing.get("birthStateProvince") or {}).get("default") if isinstance(landing.get("birthStateProvince"), dict) else landing.get("birthStateProvince"))
                lines.append(f"NHL birth: {birth_city or '?'} {birth_state or ''} {landing.get('birthCountry') or ''}".strip())
                lines.append(f"NHL draft: {landing.get('draftDetails') or 'Not listed'}")
            if hockeydb:
                lines.append("")
                lines.append(f"HockeyDB: {hockeydb.name}")
                lines.append(f"Position/Shoots: {hockeydb.position or '?'} / {hockeydb.shoots or '?'}")
                lines.append(f"Born: {hockeydb.born or '?'} | Birthplace: {hockeydb.birthplace or '?'}")
                lines.append(f"Height/Weight: {hockeydb.height or '?'} / {hockeydb.weight or '?'}")
                lines.append(f"Draft: {hockeydb.draft_info or 'Not listed'}")
                lines.append(hockeydb.url)
            self.remote_bio_text.delete("1.0", "end")
            self.remote_bio_text.insert("1.0", "\n".join(lines) if lines else "No remote profile found.")
            self._set_status(f"Loaded remote bio for {player_name}.")

        self._run_background("Loading player bio", worker, success)

    def _load_capwages_draft_picks(self) -> None:
        if self.workspace is None:
            return
        source_team = canonical_abbrev(self._team_abbrev_from_display(self.draft_pick_team_var.get()))
        if source_team == "LA":
            source_team = "LAK"
        if source_team == "SJ":
            source_team = "SJS"
        slug = TEAM_SLUGS.get(source_team or "")
        if not slug:
            self._set_status(f"No CapWages team slug mapped for {source_team or 'unknown team'}.")
            return

        def worker():
            return fetch_capwages_team_draft_picks(slug)

        def success(rows: list[CapWagesDraftPick]):
            self.draft_pick_rows = rows
            self._clear_tree(self.draft_pick_tree)
            for row in rows:
                detail = row.conditions or row.trade_details or ""
                original_slug = re.sub(r"[^a-z0-9]+", "_", row.original_team.lower()).strip("_")
                status = "Traded away" if row.is_traded_away else (
                    "Owned" if original_slug == slug else "Acquired"
                )
                self.draft_pick_tree.insert(
                    "",
                    "end",
                    values=(row.year, row.round, row.original_team, status, row.traded_date or "", detail),
                )
            save_json_state(
                self.workspace,
                "draft_pick_ownership.json",
                {
                    "source": "CapWages",
                    "team": source_team,
                    "team_slug": slug,
                    "loaded_at": datetime.now().isoformat(),
                    "picks": [
                        {
                            "year": row.year,
                            "round": row.round,
                            "original_team": row.original_team,
                            "is_traded_away": row.is_traded_away,
                            "conditions": row.conditions,
                            "traded_date": row.traded_date,
                            "trade_id": row.trade_id,
                            "trade_details": row.trade_details,
                        }
                        for row in rows
                    ],
                },
            )
            owned = sum(1 for row in rows if not row.is_traded_away)
            self._set_status(f"Loaded {len(rows)} CapWages pick records for {source_team}; {owned} currently owned/acquired.")

        self._run_background("Loading CapWages draft picks", worker, success)

    def _scan_2026_draft_class(self) -> None:
        if self.workspace is None:
            self._set_status("Open a roster before scanning the 2026 draft class.")
            return

        def worker():
            return scan_draft_class(self.workspace.working_db, self.draft_class_prospects)

        def success(rows: list[DraftRosterStatus]):
            self.draft_class_statuses = rows
            self._render_2026_draft_class()
            missing = sum(row.status == "Missing" for row in rows)
            self._set_status(f"2026 draft scan complete: {len(rows) - missing} present, {missing} missing.")

        self._run_background("Scanning 2026 draft class", worker, success)

    def _render_2026_draft_class(self) -> None:
        if not hasattr(self, "draft_class_tree"):
            return
        self._clear_tree(self.draft_class_tree)
        statuses = self.draft_class_statuses or [
            DraftRosterStatus(prospect, "Not scanned", None, "")
            for prospect in self.draft_class_prospects
        ]
        round_filter = self.draft_class_round_var.get()
        team_filter = self.draft_class_team_var.get()
        status_filter = self.draft_class_status_var.get()
        search = " ".join(self.draft_class_search_var.get().lower().split())
        self.draft_class_iid_to_status: dict[str, DraftRosterStatus] = {}
        visible = []
        for row in statuses:
            prospect = row.prospect
            if round_filter != "All Rounds" and round_filter != f"Round {prospect.round}":
                continue
            if team_filter != "All Teams" and team_filter != prospect.team:
                continue
            if status_filter != "All Statuses" and status_filter != row.status:
                continue
            searchable = f"{prospect.name} {prospect.team} {prospect.amateur_team} {prospect.archetype}".lower()
            if search and search not in searchable:
                continue
            iid = f"draft-class-{prospect.pick}"
            self.draft_class_tree.insert(
                "",
                "end",
                iid=iid,
                text=f"{prospect.pick}. {prospect.name}",
                values=(
                    prospect.round,
                    prospect.team,
                    prospect.position,
                    row.status,
                    row.current_team,
                    prospect.projected_overall,
                    prospect.archetype,
                    f"{prospect.potential_stars:.1f} {prospect.potential_color}",
                    prospect.amateur_team,
                ),
            )
            self.draft_class_iid_to_status[iid] = row
            visible.append(row)
        missing = sum(row.status == "Missing" for row in statuses)
        present = sum(row.status == "Present" for row in statuses)
        self.draft_class_count_var.set(
            f"{len(visible)} shown | {present} present | {missing} missing | 223 confirmed selections (pick 63 forfeited)"
        )

    def _select_all_2026_draft_rows(self):
        self.draft_class_tree.selection_set(self.draft_class_tree.get_children())
        self._set_status(f"Selected {len(self.draft_class_tree.selection())} visible draft rows.")
        return "break"

    def _show_2026_draft_details(self, _event=None) -> None:
        if not hasattr(self, "draft_class_details"):
            return
        selection = self.draft_class_tree.selection()
        if not selection:
            return
        row = self.draft_class_iid_to_status.get(selection[0])
        if row is None:
            return
        prospect = row.prospect
        details = (
            f"{prospect.name} | Pick {prospect.pick}, Round {prospect.round} by {prospect.team} | "
            f"{prospect.position} | {prospect.archetype} | projected {prospect.projected_overall} OVR | "
            f"{prospect.potential_stars:.1f} {prospect.potential_color}\n"
            f"Strengths: {prospect.strengths}\n"
            f"Development concerns: {prospect.weaknesses}\n"
            f"Club: {prospect.amateur_team} | Central Scouting: {prospect.cs_rank or 'not listed'} | "
            f"Roster: {row.status}{f' ({row.current_team})' if row.current_team else ''}\n"
            f"Scouting source: {prospect.scouting_source or 'round/position projection'}"
        )
        self.draft_class_details.delete("1.0", "end")
        self.draft_class_details.insert("1.0", details)

    def _apply_2026_draft_class(self, apply_all: bool) -> None:
        if self.workspace is None:
            self._set_status("Open a roster before syncing the draft class.")
            return
        if apply_all:
            prospects = list(self.draft_class_prospects)
        else:
            prospects = [
                self.draft_class_iid_to_status[iid].prospect
                for iid in self.draft_class_tree.selection()
                if iid in getattr(self, "draft_class_iid_to_status", {})
            ]
        if not prospects:
            self._set_status("Select one or more draft rows first.")
            return
        workspace = self.workspace

        def worker():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_dir = workspace.root / "draft_sync_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"before-2026-draft-{timestamp}.db"
            shutil.copy2(workspace.working_db, backup_path)
            temp_db = workspace.root / "draft-sync-working.tmp.db"
            if temp_db.exists():
                temp_db.unlink()
            try:
                shutil.copy2(workspace.working_db, temp_db)
                results = apply_draft_class(temp_db, prospects)
                errors = validate_draft_players(temp_db, prospects)
                if errors:
                    raise RuntimeError("Draft validation failed:\n" + "\n".join(errors[:12]))
                os.replace(temp_db, workspace.working_db)
                sync_working_db_to_roster(workspace)
                statuses = scan_draft_class(workspace.working_db, self.draft_class_prospects)
                return results, statuses, backup_path
            finally:
                if temp_db.exists():
                    temp_db.unlink()

        def success(payload):
            results, statuses, backup_path = payload
            self._log_actions("sync-2026-draft-class", results)
            self.draft_class_statuses = statuses
            self._rebuild_player_cache()
            self._refresh_player_list()
            self._refresh_trade_lanes()
            self._refresh_potential_tree()
            self._render_2026_draft_class()
            self._refresh_review()
            created = sum(result.get("action") == "created" for result in results)
            updated = sum(result.get("action") == "rights-updated" for result in results)
            self._set_status(
                f"2026 draft sync complete: {created} created, {updated} existing prospects assigned. "
                f"Backup: {backup_path.name}"
            )

        self._run_background("Syncing 2026 draft class", worker, success)

    def _apply_2026_elite_prospects(self) -> None:
        if self.workspace is None:
            self._set_status("Open a roster before applying scouting updates.")
            return
        workspace = self.workspace
        profiled = [row for row in self.draft_class_prospects if row.scouting_source]

        def worker():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_dir = workspace.root / "draft_sync_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"before-elite-prospects-{timestamp}.db"
            shutil.copy2(workspace.working_db, backup_path)
            original_size = workspace.working_db.stat().st_size
            temp_db = workspace.root / "elite-prospects-working.tmp.db"
            if temp_db.exists():
                temp_db.unlink()
            try:
                shutil.copy2(workspace.working_db, temp_db)
                results = apply_elite_prospects_scouting(temp_db, profiled)
                errors = validate_draft_players(temp_db, profiled)
                if errors:
                    raise RuntimeError("Scouting validation failed:\n" + "\n".join(errors[:12]))
                if temp_db.stat().st_size != original_size:
                    raise RuntimeError("Scouting update changed the fixed Xbox roster payload size.")
                os.replace(temp_db, workspace.working_db)
                sync_working_db_to_roster(workspace)
                statuses = scan_draft_class(workspace.working_db, self.draft_class_prospects)
                return results, statuses, backup_path
            finally:
                if temp_db.exists():
                    temp_db.unlink()

        def success(payload):
            results, statuses, backup_path = payload
            self._log_actions("elite-prospects-2026-scouting", results)
            self.draft_class_statuses = statuses
            self._rebuild_player_cache()
            self._refresh_player_list()
            self._refresh_trade_lanes()
            self._refresh_potential_tree()
            self._render_2026_draft_class()
            self._refresh_review()
            self._set_status(
                f"Applied Elite Prospects scouting profiles to {len(results)} players. "
                f"Backup: {backup_path.name}"
            )

        self._run_background("Applying Elite Prospects scouting", worker, success)

    def _populate_manual_contract_editor(self) -> None:
        if self.snapshot is None or not hasattr(self, "manual_contract_aav_var"):
            return
        bio = self.snapshot.bio
        self.manual_contract_aav_var.set(f"{contract_cap_hit_millions_from_raw(bio.get('dhKk')):.3f}")
        self.manual_contract_length_var.set(_safe_int(bio.get("GDhI"), 0))
        self.manual_contract_status_var.set(
            "Signed / Restricted" if _safe_int(bio.get("QwoG"), 0) else "Unrestricted"
        )
        self.manual_contract_two_way_var.set(bool(_safe_int(bio.get("DVoL"), 0)))
        self.manual_contract_entry_level_var.set(bool(_safe_int(bio.get("yvUt"), 0)))
        self.manual_extension_aav_var.set(f"{contract_cap_hit_millions_from_raw(bio.get('IzRv')):.3f}")
        self.manual_extension_length_var.set(_safe_int(bio.get("IrlK"), 0))
        self.manual_extension_two_way_var.set(bool(_safe_int(bio.get("xdoJ"), 0)))

    def _save_manual_contract(self) -> None:
        if self.workspace is None or self.selected_player is None:
            self._set_status("Select a player first.")
            return
        try:
            cap_hit = float(self.manual_contract_aav_var.get())
            extension_cap_hit = float(self.manual_extension_aav_var.get())
            length = int(self.manual_contract_length_var.get())
            extension_length = int(self.manual_extension_length_var.get())
        except (TypeError, ValueError):
            self._set_status("Contract AAV and length fields must be numeric.")
            return
        selected = self.selected_player
        signed_or_restricted = self.manual_contract_status_var.get() == "Signed / Restricted"
        two_way = bool(self.manual_contract_two_way_var.get())
        entry_level_required = bool(self.manual_contract_entry_level_var.get())
        extension_two_way = bool(self.manual_extension_two_way_var.get())

        def worker():
            result = update_player_contract_details(
                self.workspace.working_db,
                selected.first_name,
                selected.last_name,
                cap_hit_millions=cap_hit,
                length=length,
                signed_or_restricted=signed_or_restricted,
                two_way=two_way,
                entry_level_required=entry_level_required,
                extension_cap_hit_millions=extension_cap_hit,
                extension_length=extension_length,
                extension_two_way=extension_two_way,
                player_id=selected.player_id,
            )
            sync_working_db_to_roster(self.workspace)
            return result

        def success(result):
            result["source"] = "Manual contract editor"
            self._log_action("update-contract", result)
            if self.snapshot is not None:
                self.snapshot.bio.update(result.get("updated_fields") or {})
            self._populate_manual_contract_editor()
            self._refresh_review()
            self._set_status(f"Saved contract and extension for {selected.full_name}.")

        self._run_background("Saving manual contract", worker, success)

    def _load_selected_contract(self) -> None:
        if self.selected_player is None:
            return
        player_name = self.selected_player.full_name
        source_team = canonical_abbrev(
            organization_for_abbrev(self.selected_player.current_team_abbrev)
            or self.selected_player.organization_abbrev
        )
        if source_team == "LA":
            source_team = "LAK"
        if source_team == "SJ":
            source_team = "SJS"
        slug = TEAM_SLUGS.get(source_team or "")
        if not slug:
            self._set_status(f"No CapWages team slug mapped for {source_team or 'unknown team'}.")
            return

        def worker():
            data = fetch_capwages_team_contracts(slug, force_refresh=True)
            target = normalize_name(player_name)
            equivalent_target = equivalent_name_key(player_name)
            equivalent_matches = []
            for bucket in ("signed", "unsigned", "reserve"):
                for row in data.get(bucket, []):
                    if normalize_name(row.name) == target:
                        return row
                    if equivalent_name_key(row.name) == equivalent_target:
                        equivalent_matches.append(row)
            return equivalent_matches[0] if len(equivalent_matches) == 1 else None

        def success(row):
            self.capwages_player = row
            self._render_selected_contract(row)

        self._run_background("Loading CapWages contract", worker, success)

    def _render_selected_contract(self, row) -> None:
        self.contract_detail_text.delete("1.0", "end")
        if row is None:
            self.contract_detail_text.insert("1.0", "No CapWages contract match found for selected player/team.")
            return
        real_aav = _money_to_millions(row.aav or row.cap_hit)
        try:
            scaled = scale_contract_by_cap_percentage(
                row.name,
                real_aav or 0.0,
                float(self.game_cap_var.get()),
                float(self.real_cap_var.get()),
            ) if real_aav is not None else None
        except ValueError:
            scaled = None
        lines = [
            f"CapWages: {row.name} | {row.position or '?'} | status {row.status or '?'}",
            f"Real AAV: {_format_money_millions(real_aav)} | Cap hit: {row.cap_hit or '?'} | Expiry: {row.expiry or '?'} | Clause: {row.clause or '?'}",
            f"Draft: {row.drafted_by or '?'} {row.draft_year or ''} round {row.draft_round or '?'} overall {row.draft_overall or '?'}",
            f"Born/Shoots: {row.born or '?'} | {row.shoots_catches or '?'}",
        ]
        if scaled:
            lines.append(
                f"Scaled to NHL Legacy cap: {_format_money_millions(scaled.scaled_aav_millions)} ({scaled.cap_hit_percent * 100:.2f}% of cap)"
            )
        current_raw = 0 if self.snapshot is None else _safe_int(self.snapshot.bio.get("dhKk"), 0)
        current_term = 0 if self.snapshot is None else _safe_int(self.snapshot.bio.get("GDhI"), 0)
        lines.append(f"Current roster cap hit: {_format_money_millions(contract_cap_hit_millions_from_raw(current_raw))}")
        lines.append(f"Contract years: CapWages remaining {row.term_years if row.term_years is not None else '?'} | roster current {current_term}")
        lines.append("")
        lines.append("Apply Selected Contract updates scaled cap hit and remaining years when CapWages exposes a reliable term. Use Manual Selected Player Contract for status, flags, and extensions.")
        self.contract_detail_text.insert("1.0", "\n".join(lines))

    def _approve_selected_contract(self) -> None:
        if self.workspace is None or self.selected_player is None or self.capwages_player is None:
            self._set_status("Load a selected contract first.")
            return
        row = self.capwages_player
        real_aav = _money_to_millions(row.aav or row.cap_hit)
        if real_aav is None:
            self._set_status("Selected contract has no AAV/cap hit to scale.")
            return
        scaled = scale_contract_by_cap_percentage(
            row.name,
            real_aav,
            float(self.game_cap_var.get()),
            float(self.real_cap_var.get()),
        )
        first_name = self.selected_player.first_name
        last_name = self.selected_player.last_name

        def worker():
            if row.term_years is None:
                result = update_player_contract_cap_hit(
                    self.workspace.working_db,
                    first_name,
                    last_name,
                    scaled.scaled_aav_millions,
                    self.selected_player.player_id,
                )
            else:
                result = update_many_player_contract_cap_hits(
                    self.workspace.working_db,
                    [(first_name, last_name, scaled.scaled_aav_millions, self.selected_player.player_id, int(row.term_years))],
                    snapshot_cache=self.player_snapshot_cache,
                )[0]
            sync_working_db_to_roster(self.workspace)
            return result

        def success(result):
            result["real_aav_millions"] = scaled.real_aav_millions
            result["cap_hit_percent"] = scaled.cap_hit_percent
            result["expiry_preserved"] = True
            result["source"] = "CapWages"
            self._log_action("update-contract", result)
            if self.snapshot is not None:
                self.snapshot.bio.update(result.get("updated_fields") or {})
            self._render_selected_contract(row)
            self._refresh_review()
            self._set_status(f"Applied scaled contract for {self.selected_player.full_name}.")

        self._run_background("Applying selected contract", worker, success)

    def _build_all_contracts(self) -> None:
        if self.workspace is None:
            return
        real_cap = float(self.real_cap_var.get())
        game_cap = float(self.game_cap_var.get())

        def worker():
            return build_contract_update_queue(
                self.player_index,
                team_slugs=TEAM_SLUGS,
                real_cap=real_cap,
                game_cap=game_cap,
                force_refresh=True,
            )

        def success(queue):
            self.contract_queue = queue
            save_json_state(self.workspace, "contract_queue.json", queue)
            self._render_contract_queue()
            self._log_action("build-contract-pass", {"count": len(queue), "real_cap": real_cap, "game_cap": game_cap})
            self._refresh_review()
            self._set_status(f"Built {len(queue)} scaled contract proposals from CapWages.")

        self._run_background("Building all contracts", worker, success)

    def _refresh_contract_queue(self) -> None:
        if self.workspace is None:
            return
        self.contract_queue = load_json_state(self.workspace, "contract_queue.json", [])
        self._render_contract_queue()

    def _render_contract_queue(self) -> None:
        self._clear_tree(self.contract_tree)
        for index, row in enumerate(self.contract_queue):
            percent = float(row.get("real_aav_millions", 0.0)) / float(self.real_cap_var.get() or DEFAULT_REAL_CAP_MILLIONS)
            self.contract_tree.insert(
                "",
                "end",
                iid=f"contract-{index}",
                text=str(row.get("player_name") or ""),
                values=(
                    row.get("team") or "",
                    row.get("current_team") or "",
                    _format_money_millions(float(row.get("real_aav_millions") or 0)),
                    _format_money_millions(float(row.get("game_aav_millions") or 0)),
                    f"{percent * 100:.2f}%",
                    row.get("term_years") if row.get("term_years") is not None else "?",
                    row.get("expiry") or "",
                ),
            )

    def _select_all_contracts(self):
        if not hasattr(self, "contract_tree"):
            return "break"
        self.contract_tree.selection_set(self.contract_tree.get_children())
        self._set_status(f"Selected {len(self.contract_tree.selection())} contract proposal(s).")
        return "break"

    def _approve_contract_queue_selection(self, *, apply_all: bool) -> None:
        if self.workspace is None:
            return
        if apply_all:
            indices = list(range(len(self.contract_queue)))
        else:
            indices = [
                int(item.split("-", 1)[1])
                for item in self.contract_tree.selection()
                if item.startswith("contract-")
            ]
        if not indices:
            self._set_status("Select contract proposals first, or use Apply All Queue.")
            return
        selected_rows = [
            (index, dict(self.contract_queue[index]))
            for index in indices
            if 0 <= index < len(self.contract_queue)
        ]

        def worker():
            prepared: list[tuple[str, str, float] | tuple[str, str, float, int] | tuple[str, str, float, int, int]] = []
            row_by_player: dict[tuple[str, object], list[tuple[int, dict[str, object]]]] = {}
            errors: list[str] = []
            for index, proposal in selected_rows:
                try:
                    first, last = _split_name(str(proposal.get("player_name") or ""))
                    game_aav = float(proposal.get("game_aav_millions") or 0.675)
                    proposal_player_id = _safe_int(proposal.get("player_id"), -1)
                    raw_term = proposal.get("term_years")
                    term_years = None if raw_term in (None, "") else max(0, _safe_int(raw_term, 0))
                    if proposal_player_id >= 0:
                        if term_years is None:
                            prepared.append((first, last, game_aav, proposal_player_id))
                        else:
                            prepared.append((first, last, game_aav, proposal_player_id, term_years))
                        row_key = ("id", proposal_player_id)
                    else:
                        prepared.append((first, last, game_aav))
                        row_key = ("name", normalize_name(f"{first} {last}"))
                    row_by_player.setdefault(row_key, []).append((index, proposal))
                except Exception as exc:
                    errors.append(f"{proposal.get('player_name')}: {exc}")
            results: list[dict[str, object]] = []
            if prepared:
                batch_errors: list[str] = []
                try:
                    results = update_many_player_contract_cap_hits(
                        self.workspace.working_db,
                        prepared,
                        error_messages=batch_errors,
                        snapshot_cache=self.player_snapshot_cache,
                    )
                except Exception as exc:
                    errors.append(f"Contract batch write failed: {exc}")
                errors.extend(batch_errors)
            if results:
                sync_working_db_to_roster(self.workspace)
            return results, errors, row_by_player

        def success(payload):
            results, errors, row_by_player = payload
            applied_indices: set[int] = set()
            logged_results: list[dict[str, object]] = []
            for result in results:
                player = str(result.get("player") or "")
                result_player_id = _safe_int(result.get("player_id"), -1)
                result_key = (
                    ("id", result_player_id)
                    if result_player_id >= 0
                    else ("name", normalize_name(player))
                )
                indexed_rows = row_by_player.get(result_key)
                if not indexed_rows:
                    continue
                index, proposal = indexed_rows.pop(0)
                applied_indices.add(index)
                result["real_aav_millions"] = proposal.get("real_aav_millions")
                result["expiry_preserved"] = True
                result["source"] = "CapWages"
                logged_results.append(result)
                if self.player_snapshot_cache is not None:
                    first, last = _split_name(player)
                    snapshot = self.player_snapshot_cache.get_player_snapshot(
                        first,
                        last,
                        result_player_id if result_player_id >= 0 else None,
                    )
                    if snapshot is not None:
                        snapshot.bio.update(result.get("updated_fields") or {})
            self._log_actions("update-contract", logged_results)
            self.contract_queue = [
                proposal
                for index, proposal in enumerate(self.contract_queue)
                if index not in applied_indices
            ]
            save_json_state(self.workspace, "contract_queue.json", self.contract_queue)
            self._render_contract_queue()
            self._refresh_review()
            self._set_status(f"Applied {len(results)} contract update(s) to the roster.")
            if errors:
                messagebox.showwarning("Some contracts failed", "\n".join(errors[:8]))

        self._run_background("Applying contract updates", worker, success)

    def _moneypuck_tools(self, season: int, *, player_kind: str = "skater") -> tuple[MoneyPuckCSVClient, LegacyAttributeMapper]:
        with self.metric_bundle_lock:
            if self.moneypuck_client is None:
                self.moneypuck_client = MoneyPuckCSVClient()
            key = (season, player_kind)
            cached = self.metric_bundle_cache.get(key)
            if cached is not None:
                return self.moneypuck_client, cached[1]
            rows = self.moneypuck_client.load_goalies(season) if player_kind == "goalie" else self.moneypuck_client.load_skaters(season)
            model = self.moneypuck_client.build_percentile_model(season, min_games=20, player_kind=player_kind)
            mapper = LegacyAttributeMapper(model)
            self.metric_bundle_cache[key] = (rows, mapper)
            if player_kind == "goalie":
                self.moneypuck_goalie_mapper = mapper
            else:
                self.moneypuck_mapper = mapper
            return self.moneypuck_client, mapper

    def _metric_cache_available_locally(self, season: int) -> bool:
        if self.moneypuck_client is None:
            self.moneypuck_client = MoneyPuckCSVClient()
        cache_dir = self.moneypuck_client.cache_dir
        seasons = (season, season - 1)
        metric_files = [
            cache_dir / f"{year}_{kind}.csv"
            for year in seasons
            for kind in ("skaters", "goalies")
        ]
        faceoff_dir = cache_dir.parent / "nhl"
        faceoff_files = [faceoff_dir / f"faceoffs_{year}{year + 1}.json" for year in seasons]
        return all(path.exists() for path in (*metric_files, *faceoff_files))

    def _prewarm_default_metric_data(self, *, synchronous: bool = False) -> None:
        if self.metrics_prewarm_started:
            return
        self.metrics_prewarm_started = True
        try:
            season = int(self.bulk_season_var.get() or 2025)
        except ValueError:
            season = 2025

        def task():
            try:
                for year in (season, season - 1):
                    for player_kind in ("skater", "goalie"):
                        _client, mapper = self._moneypuck_tools(year, player_kind=player_kind)
                        rows = self.metric_bundle_cache[(year, player_kind)][0]
                        for row in rows:
                            if games_played(row) < 20:
                                continue
                            if player_kind == "goalie":
                                mapper.goalie_recommendations(row, min_games=20)
                            else:
                                mapper.money_puck_recommendations(row, min_games=20)
                self.metrics_prewarm_complete = True
            except Exception:
                # A later explicit metrics load will surface any source error.
                self.metrics_prewarm_started = False
                self.metrics_prewarm_complete = False

        if synchronous and self._metric_cache_available_locally(season):
            task()
        else:
            threading.Thread(target=task, daemon=True).start()

    def _style_label_for_position_code(self, position_code: int, style_code: int | None) -> str:
        if style_code is None:
            return UNMAPPED_CHOICE
        if position_code == POSITION_CODES["G"]:
            style_map = GOALIE_STYLE_CODES
        elif position_code == POSITION_CODES["D"]:
            style_map = DEFENSE_STYLE_CODES
        elif position_code in {POSITION_CODES["C"], POSITION_CODES["LW"], POSITION_CODES["RW"]}:
            style_map = FORWARD_STYLE_CODES
        else:
            style_map = PLAYER_STYLE_CODES
        return next((label for label, code in style_map.items() if code == style_code), UNMAPPED_CHOICE)

    def _style_code_from_snapshot(self, snapshot) -> int | None:
        position_code = _safe_int(snapshot.bio.get("aljv"), -1)
        style_code = None
        if snapshot.instance_rows:
            ranked_instances: list[tuple[int, int, dict[str, object]]] = []
            for row in snapshot.instance_rows:
                team = self.team_by_code.get(_safe_int(row.get("BSXd"), -1))
                score = 0
                if team is not None:
                    league = league_name_for_team(team)
                    if league == "NHL" and 0 <= team.code <= 29:
                        score += 300
                    elif league == "NHL":
                        score += 260
                    elif league == "AHL":
                        score += 220
                    elif league in {"Organization", "Prospects"}:
                        score += 200
                    elif league in {"International", "World Cup", "Exhibition"}:
                        score += 30
                    else:
                        score += 100
                ranked_instances.append((-score, _safe_int(row.get("TWSX"), 0), row))
            ranked_instances.sort()
            style_code = _safe_int(ranked_instances[0][2].get("sFgQ"), -1)
        ratings_row = snapshot.goalie_ratings_row if position_code == POSITION_CODES["G"] else snapshot.ratings_row
        if (style_code is None or style_code < 0) and ratings_row is not None:
            style_code = _safe_int(ratings_row.get("sFgQ"), -1)
        return style_code if style_code is not None and style_code >= 0 else None

    def _archetype_for_snapshot(self, snapshot) -> str:
        position_code = _safe_int(snapshot.bio.get("aljv"), -1)
        style_code = self._style_code_from_snapshot(snapshot)
        style_label = self._style_label_for_position_code(position_code, style_code)
        archetype = STYLE_TO_ARCHETYPE.get(style_label)
        if archetype in ARCHETYPE_WEIGHTS:
            return archetype
        if position_code == POSITION_CODES["D"]:
            return "two_way_defenseman"
        return "two_way_forward"

    def _snapshot_roster_summary(self, snapshot) -> tuple[int | None, str, str]:
        if snapshot is None:
            return None, "Unknown", "Unknown"
        position_code = _safe_int(snapshot.bio.get("aljv"), -1)
        position = self._position_label_from_code(position_code)
        if position not in POSITION_CODES:
            position = "Unknown"
        style_code = self._style_code_from_snapshot(snapshot)
        style_label = self._style_label_for_position_code(position_code, style_code)
        display_style = style_label if style_label != UNMAPPED_CHOICE else "Unknown"
        if position_code == POSITION_CODES["G"]:
            ratings_row = snapshot.goalie_ratings_row
            if ratings_row is None:
                return None, position, display_style
            display_values = {
                spec.label: raw_to_display(spec, _safe_int(ratings_row.get(spec.field), 0))
                for spec in specs_for_player_kind("goalie")
            }
            style = style_label
            if style not in GOALIE_STYLE_CODES:
                style = "Hybrid Goalie"
            return calculate_goalie_overall(display_values, style), position, display_style
        if snapshot.ratings_row is None:
            return None, position, display_style
        archetype = STYLE_TO_ARCHETYPE.get(style_label)
        if archetype not in ARCHETYPE_WEIGHTS:
            archetype = "two_way_defenseman" if position_code == POSITION_CODES["D"] else "two_way_forward"
        overall = calculate_weighted_overall(
            self._semantic_from_skater_row(snapshot.ratings_row),
            archetype,
            position=position,
        )
        return overall, position, display_style

    def _estimate_snapshot_overall(self, snapshot) -> int | None:
        return self._snapshot_roster_summary(snapshot)[0]

    @staticmethod
    def _overall_row_tag(overall: int | None) -> str:
        if overall is None or overall < 72:
            return "fringe"
        if overall >= 88:
            return "elite"
        if overall >= 78:
            return "nhl"
        return "depth"

    def _semantic_from_skater_row(self, ratings_row: dict[str, object]) -> dict[str, int]:
        semantic: dict[str, int] = {}
        for spec in SKATER_ATTRIBUTE_SPECS:
            mapped = LABEL_TO_SEMANTIC.get(spec.label)
            if mapped:
                semantic[mapped] = raw_to_display(spec, _safe_int(ratings_row.get(spec.field), 0))
        return semantic

    def _display_ratings_from_skater_row(self, ratings_row: dict[str, object]) -> dict[str, int]:
        return {
            spec.label: raw_to_display(spec, _safe_int(ratings_row.get(spec.field), 0))
            for spec in SKATER_ATTRIBUTE_SPECS
        }

    def _display_ratings_from_row(self, ratings_row: dict[str, object], *, player_kind: str) -> dict[str, int]:
        return {
            spec.label: raw_to_display(spec, _safe_int(ratings_row.get(spec.field), 0))
            for spec in specs_for_player_kind(player_kind)
        }

    @staticmethod
    def _simple_overall_from_display(display_values: dict[str, int]) -> int:
        if not display_values:
            return 0
        return round(sum(display_values.values()) / len(display_values))

    @staticmethod
    def _nhl_edge_team_abbrev(value: str | None) -> str | None:
        normalized = canonical_abbrev(value)
        if normalized is None:
            return None
        return {
            "LA": "LAK",
            "NJ": "NJD",
            "SJS": "SJS",
            "TB": "TBL",
        }.get(normalized, normalized)

    @staticmethod
    def _metric_position_family(value: str | None) -> str:
        position = str(value or "").strip().upper()
        position = {"L": "LW", "R": "RW", "LD": "D", "RD": "D"}.get(position, position)
        if position in {"G", "GOALIE", "GOALTENDER"}:
            return "G"
        if position in {"D", "DEFENSE", "DEFENCE", "DEFENSEMAN", "DEFENCEMAN"}:
            return "D"
        if position in {
            "C",
            "LW",
            "RW",
            "F",
            "CENTER",
            "CENTRE",
            "LEFT WING",
            "RIGHT WING",
            "FORWARD",
        }:
            return "F"
        return ""

    def _select_edge_hit(self, player_name: str, roster_team: str | None, position: str = ""):
        wanted_name = normalize_name(player_name)
        hits = [
            hit
            for hit in find_player_on_official_rosters(player_name)
            if normalize_name(hit.full_name) == wanted_name
        ]
        if not hits:
            return None
        wanted_team = self._nhl_edge_team_abbrev(roster_team)
        wanted_position = str(position or "").strip().upper()
        wanted_family = self._metric_position_family(wanted_position)
        scored: list[tuple[int, object]] = []
        for hit in hits:
            hit_position = str(hit.position_code or "").strip().upper()
            hit_family = self._metric_position_family(hit_position)
            if wanted_family and hit_family and wanted_family != hit_family:
                continue
            score = 100
            if wanted_family and hit_family == wanted_family:
                score += 40
            normalized_hit_position = {"L": "LW", "R": "RW"}.get(hit_position, hit_position)
            if wanted_position and normalized_hit_position == wanted_position:
                score += 15
            if wanted_team and self._nhl_edge_team_abbrev(hit.team_abbrev) == wanted_team:
                score += 35
            scored.append((score, hit))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        top_score = scored[0][0]
        tied = [hit for score, hit in scored if score == top_score]
        if len({hit.player_id for hit in tied}) > 1:
            return None
        return tied[0]

    def _fetch_cached_edge_detail(self, player_id: int, season: int) -> dict:
        key = (player_id, season)
        if key not in self.edge_detail_cache:
            self.edge_detail_cache[key] = fetch_edge_skater_detail(player_id, season)
        return self.edge_detail_cache[key]

    def _fetch_cached_goalie_edge_detail(self, player_id: int, season: int) -> dict:
        key = (player_id, season)
        if key not in self.edge_goalie_detail_cache:
            self.edge_goalie_detail_cache[key] = fetch_edge_goalie_detail(player_id, season)
        return self.edge_goalie_detail_cache[key]

    def _fetch_cached_player_landing(self, player_id: int) -> dict:
        if player_id not in self.player_landing_cache:
            self.player_landing_cache[player_id] = fetch_player_landing(player_id)
        return self.player_landing_cache[player_id]

    def _player_bio_for_metric_match(self, metric_row: dict[str, object] | None, edge_hit) -> dict[str, object] | None:
        if edge_hit is not None:
            bio: dict[str, object] = {}
            if edge_hit.height_in_inches is not None:
                bio["heightInInches"] = edge_hit.height_in_inches
            if edge_hit.weight_in_pounds is not None:
                bio["weightInPounds"] = edge_hit.weight_in_pounds
            if bio:
                return bio
        if metric_row:
            player_id = _safe_int(metric_row.get("playerId"), -1)
            if player_id > 0:
                try:
                    return self._fetch_cached_player_landing(player_id)
                except Exception:
                    return None
        return None

    @staticmethod
    def _tree_items(tree: ttk.Treeview) -> list[str]:
        items: list[str] = []
        pending = list(tree.get_children(""))
        while pending:
            item = pending.pop(0)
            items.append(item)
            pending[0:0] = list(tree.get_children(item))
        return items

    def _select_all_focused(self, event=None):
        widget = self.root.focus_get()
        if widget is None:
            return None
        if isinstance(widget, ttk.Treeview):
            if str(widget.cget("selectmode")) == "browse":
                return None
            items = self._tree_items(widget)
            if items:
                widget.selection_set(items)
                self._set_status(f"Selected {len(items)} row(s).")
            return "break"
        if isinstance(widget, tk.Listbox):
            if widget.size():
                widget.selection_set(0, "end")
                self._set_status(f"Selected {widget.size()} item(s).")
            return "break"
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "1.0")
            widget.see("insert")
            return "break"
        if isinstance(widget, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox, ttk.Combobox)):
            try:
                widget.selection_range(0, "end")
                widget.icursor("end")
                return "break"
            except tk.TclError:
                return None
        return None

    def _load_advanced_metric_targets(self) -> None:
        if self.workspace is None:
            self.advanced_metric_targets = {}
            return
        saved = load_json_state(self.workspace, ADVANCED_METRICS_STATE_FILE, {"targets": {}})
        records = saved.get("targets", {}) if isinstance(saved, dict) else {}
        self.advanced_metric_targets = {
            str(key): dict(value)
            for key, value in records.items()
            if isinstance(value, dict)
        }

    def _load_manual_metric_review(self) -> None:
        if self.workspace is None:
            self.manual_metric_review = []
            return
        saved = load_json_state(
            self.workspace,
            ADVANCED_METRICS_MANUAL_REVIEW_FILE,
            {"players": []},
        )
        records = saved.get("players", []) if isinstance(saved, dict) else saved
        rows: list[dict[str, object]] = []
        seen_player_ids: set[int] = set()
        if isinstance(records, list):
            for value in records:
                if not isinstance(value, dict):
                    continue
                player_id = _safe_int(value.get("player_id"), -1)
                if player_id < 0 or player_id in seen_player_ids:
                    continue
                seen_player_ids.add(player_id)
                rows.append(dict(value))
        self.manual_metric_review = rows

    def _save_manual_metric_review(self) -> None:
        if self.workspace is None:
            return
        save_json_state(
            self.workspace,
            ADVANCED_METRICS_MANUAL_REVIEW_FILE,
            {"version": 1, "players": self.manual_metric_review},
        )

    def _save_advanced_metric_targets(self) -> None:
        if self.workspace is None:
            return
        try:
            save_json_state(
                self.workspace,
                ADVANCED_METRICS_STATE_FILE,
                {
                    "version": 1,
                    "model_version": ADVANCED_METRICS_MODEL_VERSION,
                    "targets": self.advanced_metric_targets,
                },
            )
        except OSError:
            # The roster write is authoritative; a transient state-file issue
            # should not turn a successful player edit into a failed save.
            return

    @staticmethod
    def _advanced_metric_state_key(
        player_id: int,
        season: int,
        player_kind: str,
        include_edge: bool,
    ) -> str:
        source_mode = "combined" if include_edge else "bulk"
        return f"{int(player_id)}:{int(season)}:{player_kind}:{source_mode}"

    def _remember_advanced_metric_target(
        self,
        context: dict[str, object],
        *,
        persist: bool = True,
    ) -> None:
        state_key = str(context.get("state_key") or "")
        signature = str(context.get("signature") or "")
        targets = context.get("targets")
        if not state_key or not signature or not isinstance(targets, dict) or not targets:
            return
        self.advanced_metric_targets[state_key] = {
            "signature": signature,
            "targets": {str(label): int(value) for label, value in targets.items()},
            "player_id": _safe_int(context.get("player_id"), -1),
            "season": _safe_int(context.get("season"), 0),
            "player_kind": str(context.get("player_kind") or "skater"),
            "source_mode": str(context.get("source_mode") or "bulk"),
            "applied_at": datetime.now().isoformat(),
        }
        if persist:
            self._save_advanced_metric_targets()

    def _build_advanced_metric_recommendation(
        self,
        *,
        player_name: str,
        roster_team: str | None,
        season: int,
        player_kind: str = "skater",
        client: MoneyPuckCSVClient | None = None,
        mapper: LegacyAttributeMapper | None = None,
        mp_rows: list[dict[str, object]] | None = None,
        include_edge: bool = True,
        current_attributes: dict[str, int] | None = None,
        position: str = "",
        player_id: int | None = None,
    ) -> dict[str, object]:
        if client is None or mapper is None:
            client, mapper = self._moneypuck_tools(season, player_kind=player_kind)
        cached_bundle = self.metric_bundle_cache.get((season, player_kind))
        rows = mp_rows if mp_rows is not None else (
            cached_bundle[0]
            if cached_bundle is not None
            else (client.load_goalies(season) if player_kind == "goalie" else client.load_skaters(season))
        )

        edge_hit = None
        edge_data = None
        edge_error = None
        edge_suggestions: dict[str, int] = {}
        edge_notes: dict[str, str] = {}
        if include_edge:
            try:
                edge_hit = self._select_edge_hit(player_name, roster_team, position)
                if edge_hit is None:
                    edge_error = "No unambiguous current NHL roster match found."
            except Exception as exc:
                edge_error = str(exc)
        matched_nhl_player_id = edge_hit.player_id if edge_hit is not None else None

        mp_match = None
        mp_rec: RecommendationSet | None = None
        mp_error = None
        try:
            if player_kind == "goalie":
                mp_match = client.find_goalie(
                    player_name,
                    roster_team,
                    season=season,
                    min_games=20,
                    rows=rows,
                    allow_partial=include_edge,
                    position=position or "G",
                    nhl_player_id=matched_nhl_player_id,
                )
            else:
                mp_match = client.find_skater(
                    player_name,
                    roster_team,
                    season=season,
                    min_games=20,
                    rows=rows,
                    allow_partial=include_edge,
                    position=position,
                    nhl_player_id=matched_nhl_player_id,
                )
            if mp_match is not None:
                # Player size is especially important for strength; use NHL bio
                # data when available rather than forcing MoneyPuck to explain it.
                mp_rec = mapper.goalie_recommendations(mp_match.row, min_games=20) if player_kind == "goalie" else None
            else:
                mp_error = f"No MoneyPuck 20+ GP {player_kind} match found."
        except Exception as exc:
            mp_error = str(exc)

        if include_edge and edge_hit is not None:
            try:
                if player_kind == "goalie":
                    edge_data = self._fetch_cached_goalie_edge_detail(edge_hit.player_id, season)
                    if edge_data:
                        edge_suggestions, edge_notes = self._build_goalie_edge_suggestions(edge_data)
                else:
                    edge_data = self._fetch_cached_edge_detail(edge_hit.player_id, season)
                    if edge_data:
                        edge_suggestions, edge_notes = self._build_edge_suggestions(edge_data)
            except Exception as exc:
                edge_error = str(exc)

        if mp_match is not None and player_kind != "goalie" and mp_rec is None:
            try:
                player_bio = self._player_bio_for_metric_match(mp_match.row, edge_hit) if include_edge else None
                mp_rec = mapper.money_puck_recommendations(mp_match.row, min_games=20, player_bio=player_bio)
            except Exception as exc:
                mp_error = str(exc)

        season_used = season
        prior_match = None
        prior_rec: RecommendationSet | None = None
        try:
            prior_season = season - 1
            prior_client, prior_mapper = self._moneypuck_tools(prior_season, player_kind=player_kind)
            prior_rows = self.metric_bundle_cache[(prior_season, player_kind)][0]
            if player_kind == "goalie":
                prior_match = prior_client.find_goalie(
                    player_name,
                    roster_team,
                    season=prior_season,
                    min_games=20,
                    rows=prior_rows,
                    allow_partial=include_edge,
                    position=position or "G",
                    nhl_player_id=matched_nhl_player_id,
                )
                if prior_match is not None:
                    prior_rec = prior_mapper.goalie_recommendations(prior_match.row, min_games=20)
            elif mp_match is None:
                prior_match = prior_client.find_skater(
                    player_name,
                    roster_team,
                    season=prior_season,
                    min_games=20,
                    rows=prior_rows,
                    allow_partial=include_edge,
                    position=position,
                    nhl_player_id=matched_nhl_player_id,
                )
                if prior_match is not None:
                    player_bio = self._player_bio_for_metric_match(prior_match.row, edge_hit) if include_edge else None
                    prior_rec = prior_mapper.money_puck_recommendations(
                        prior_match.row,
                        min_games=20,
                        player_bio=player_bio,
                    )
        except Exception:
            prior_match = None
            prior_rec = None

        if player_kind == "goalie" and mp_rec is not None and prior_rec is not None:
            mp_rec = blend_season_recommendations(
                mp_rec,
                prior_rec,
                current_weight=0.50,
                previous_source=f"MoneyPuck {season - 1}",
            )
        elif mp_rec is None and prior_rec is not None:
            mp_rec = blend_season_recommendations(
                RecommendationSet(skipped_reason=f"No 20+ GP season in {season}."),
                prior_rec,
                current_weight=0.0,
                previous_source=f"MoneyPuck {season - 1}",
            )
            mp_match = prior_match
            season_used = season - 1
            mp_error = None

        suggestions: dict[str, int] = {}
        notes: dict[str, str] = {}
        metric_signature = ""
        metric_state_key = ""
        reused_applied_target = False
        if mp_rec is not None and not mp_rec.skipped_reason:
            blended = (
                LegacyAttributeMapper.blend_goalie_with_edge(mp_rec, edge_suggestions)
                if player_kind == "goalie"
                else LegacyAttributeMapper.blend_with_edge(mp_rec, edge_suggestions)
            )
            notes = dict(blended.notes)
            metric_signature = advanced_metric_signature(
                blended.suggestions,
                overall_baseline=mp_rec.overall_baseline,
                season_used=season_used,
                include_edge=include_edge,
                player_kind=player_kind,
            )
            if player_id is not None:
                metric_state_key = self._advanced_metric_state_key(
                    player_id,
                    season,
                    player_kind,
                    include_edge,
                )
            saved_target = self.advanced_metric_targets.get(metric_state_key, {})
            saved_values = saved_target.get("targets") if isinstance(saved_target, dict) else None
            if (
                metric_state_key
                and saved_target.get("signature") == metric_signature
                and isinstance(saved_values, dict)
                and saved_values
            ):
                suggestions = {str(label): int(value) for label, value in saved_values.items()}
                reused_applied_target = True
                for label in suggestions:
                    prior_note = notes.get(label, "advanced metrics")
                    notes[label] = f"{prior_note} | previously applied stable target"
            else:
                suggestions = stabilize_recommendations(
                    blended.suggestions,
                    current_attributes,
                    position=position,
                    player_kind=player_kind,
                    target_overall=mp_rec.overall_baseline,
                    role_ceiling_overall=(
                        mp_rec.overall_baseline if player_kind == "skater" else None
                    ),
                    notes=notes,
                )

        return {
            "edge_hit": edge_hit,
            "edge_data": edge_data or {},
            "edge_error": edge_error,
            "mp_match": mp_match,
            "mp_rec": mp_rec,
            "mp_error": mp_error,
            "suggestions": suggestions,
            "notes": notes,
            "season_used": season_used,
            "metric_signature": metric_signature,
            "metric_state_key": metric_state_key,
            "metric_targets": dict(suggestions),
            "metrics_up_to_date": reused_applied_target and metric_targets_match(current_attributes, suggestions),
        }

    def _preview_org_attribute_updates(self, *, league_wide: bool = False) -> None:
        if self.workspace is None:
            return
        org = self._team_abbrev_from_display(self.bulk_org_var.get())
        selected_league = self.bulk_league_var.get() if hasattr(self, "bulk_league_var") else "NHL"
        target_org = (
            self.organization_links.get((org or "").upper())
            or organization_for_abbrev(org)
            or normalize_org_abbrev(org)
            or org
        )
        if not league_wide and not org:
            self._set_status("Choose an organization first. You can type the abbreviation or team name.")
            return
        try:
            season = int(self.bulk_season_var.get())
        except ValueError:
            self._set_status("Enter a MoneyPuck season start year, for example 2025.")
            return
        manual_review_ids = {
            _safe_int(row.get("player_id"), -1)
            for row in self.manual_metric_review
        }

        def worker():
            client, skater_mapper = self._moneypuck_tools(season, player_kind="skater")
            _client, goalie_mapper = self._moneypuck_tools(season, player_kind="goalie")
            skater_rows = client.load_skaters(season)
            goalie_rows = client.load_goalies(season)
            nhl_eligible_names: set[str] = set()
            if league_wide and selected_league == "NHL":
                self._moneypuck_tools(season - 1, player_kind="skater")
                self._moneypuck_tools(season - 1, player_kind="goalie")
                prior_skater_rows = self.metric_bundle_cache[(season - 1, "skater")][0]
                prior_goalie_rows = self.metric_bundle_cache[(season - 1, "goalie")][0]
                nhl_eligible_names = {
                    normalize_name(player_name(row))
                    for metric_rows in (skater_rows, goalie_rows, prior_skater_rows, prior_goalie_rows)
                    for row in metric_rows
                    if games_played(row) >= 20 and player_name(row)
                }
            preview: list[dict[str, object]] = []
            candidates = [
                player
                for player in self.player_index
                if not player.is_hidden
                and player.player_id not in manual_review_ids
                and (
                    (
                        league_wide
                        and bulk_metric_player_in_scope(player, selected_league, nhl_eligible_names)
                    )
                    or (
                        not league_wide
                        and player.organization_abbrev
                        and (player.organization_abbrev or "").upper() == (target_org or "").upper()
                    )
                )
            ]
            org_players: list[PlayerListEntry] = []
            seen_player_ids: set[int] = set()
            for player in candidates:
                if player.player_id in seen_player_ids:
                    continue
                seen_player_ids.add(player.player_id)
                org_players.append(player)
            if league_wide and selected_league == "NHL":
                placement_priority = {
                    "NHL": 0,
                    "AHL": 1,
                    "Organization": 2,
                    "Free Agents": 3,
                    "Prospects": 4,
                }
                org_players.sort(
                    key=lambda player: (
                        placement_priority.get(player.league_name, 9),
                        player.current_team_abbrev or "ZZZ",
                        player.player_id,
                    )
                )
            seen_metric_identities: set[tuple[str, str]] = set()
            for entry in org_players:
                snapshot = self.player_snapshot_cache.get_player_snapshot(entry.first_name, entry.last_name, entry.player_id) if self.player_snapshot_cache else None
                if snapshot is None:
                    continue
                position = self._position_label_from_code(_safe_int(snapshot.bio.get("aljv"), -1))
                if league_wide and selected_league == "NHL":
                    position_family = self._metric_position_family(position)
                    metric_identity = (normalize_name(entry.full_name), position_family)
                    if position_family and metric_identity in seen_metric_identities:
                        continue
                    if position_family:
                        seen_metric_identities.add(metric_identity)
                player_kind = "goalie" if position == "G" else "skater"
                ratings_row = snapshot.goalie_ratings_row if player_kind == "goalie" else snapshot.ratings_row
                if ratings_row is None:
                    continue
                mapper = goalie_mapper if player_kind == "goalie" else skater_mapper
                mp_rows = goalie_rows if player_kind == "goalie" else skater_rows
                field_by_label = {spec.label: spec for spec in specs_for_player_kind(player_kind)}
                current_display_map = self._display_ratings_from_row(ratings_row, player_kind=player_kind)
                metric = self._build_advanced_metric_recommendation(
                    player_name=entry.full_name,
                    roster_team=entry.current_team_abbrev or entry.organization_abbrev,
                    season=season,
                    player_kind=player_kind,
                    client=client,
                    mapper=mapper,
                    mp_rows=mp_rows,
                    include_edge=False,
                    current_attributes=current_display_map,
                    position=position,
                    player_id=entry.player_id,
                )
                match = metric.get("mp_match")
                rec = metric.get("mp_rec")
                if match is None or rec is None or rec.skipped_reason:
                    continue
                suggestions = dict(metric.get("suggestions") or {})
                if not suggestions:
                    continue
                raw_updates: dict[str, int] = {}
                display_updates: dict[str, int] = {}
                suggested_display_map = dict(current_display_map)
                current_semantic = self._semantic_from_skater_row(ratings_row) if player_kind == "skater" else {}
                suggested_semantic = dict(current_semantic)
                suggestion_parts: list[str] = []
                for label, value in sorted(suggestions.items()):
                    spec = field_by_label.get(label)
                    if spec is None:
                        continue
                    current_display = current_display_map.get(label, raw_to_display(spec, _safe_int(ratings_row.get(spec.field), 0)))
                    if current_display == int(value):
                        continue
                    raw_updates[spec.field] = display_to_raw(spec, int(value))
                    display_updates[label] = int(value)
                    suggested_display_map[label] = int(value)
                    mapped = LABEL_TO_SEMANTIC.get(label)
                    if mapped:
                        suggested_semantic[mapped] = int(value)
                    suggestion_parts.append(f"{label} {current_display}->{int(value)}")
                if not raw_updates:
                    continue
                archetype = "goalie" if player_kind == "goalie" else self._archetype_for_snapshot(snapshot)
                if player_kind == "goalie":
                    style_code = _safe_int(ratings_row.get("sFgQ"), -1)
                    style_label = self._style_label_for_position_code(POSITION_CODES["G"], style_code)
                    if style_label not in GOALIE_STYLE_CODES:
                        style_label = "Hybrid Goalie"
                    current_overall = calculate_goalie_overall(current_display_map, style_label)
                    suggested_overall = calculate_goalie_overall(suggested_display_map, style_label)
                    archetype = style_label
                else:
                    current_overall = calculate_weighted_overall(current_semantic, archetype, position=position)
                    suggested_overall = calculate_weighted_overall(suggested_semantic, archetype, position=position)
                preview.append(
                    {
                        "player_name": entry.full_name,
                        "player_id": entry.player_id,
                        "first_name": entry.first_name,
                        "last_name": entry.last_name,
                        "player_kind": player_kind,
                        "position": position,
                        "league": entry.league_name,
                        "team": entry.current_team_abbrev or "",
                        "organization": entry.organization_abbrev or "",
                        "games_played": games_played(match.row),
                        "ice_time_minutes": round((number(match.row, "_toi_per_gp", default=0.0) or 0.0) / 60.0, 1),
                        "season_used": metric.get("season_used", season),
                        "moneypuck_team": player_team(match.row),
                        "match_reason": match.reason,
                        "archetype": archetype,
                        "current_overall": current_overall,
                        "suggested_overall": suggested_overall,
                        "overall_change": suggested_overall - current_overall,
                        "metrics_baseline": rec.overall_baseline,
                        "metrics_baseline_note": rec.overall_note,
                        "goalie_baseline": rec.overall_baseline if player_kind == "goalie" else None,
                        "goalie_baseline_note": rec.overall_note if player_kind == "goalie" else None,
                        "raw_updates": raw_updates,
                        "display_updates": display_updates,
                        "notes": metric.get("notes") or {},
                        "metric_signature": metric.get("metric_signature") or "",
                        "metric_state_key": metric.get("metric_state_key") or "",
                        "metric_targets": metric.get("metric_targets") or {},
                        "sources": f"MoneyPuck {metric.get('season_used', season)}",
                        "summary": "; ".join(suggestion_parts[:10]) + ("; ..." if len(suggestion_parts) > 10 else ""),
                    }
                )
            preview.sort(key=lambda row: (str(row["team"]), str(row["player_name"])))
            return preview

        def success(preview):
            self.bulk_attribute_recommendations = preview
            self._render_bulk_stats_tree()
            scope = f"the {selected_league} league" if league_wide else str(org)
            omitted = len(manual_review_ids)
            omitted_note = f" {omitted} manual-review player(s) remained excluded." if omitted else ""
            outside_nhl = sum(1 for row in preview if selected_league == "NHL" and row.get("league") != "NHL")
            placement_note = f" {outside_nhl} are currently outside an NHL roster slot." if outside_nhl else ""
            self._set_status(
                f"Previewed {len(preview)} player rating update(s) for {scope}.{placement_note}{omitted_note}"
            )

        self._run_background("Previewing organization ratings", worker, success)

    def _bulk_position_matches(self, row: dict[str, object]) -> bool:
        selected = self.bulk_position_filter_var.get() if hasattr(self, "bulk_position_filter_var") else "All Positions"
        position = str(row.get("position") or "")
        if selected == "Goalies":
            return position == "G"
        if selected == "Defensemen":
            return position == "D"
        if selected == "Forwards":
            return position in {"C", "LW", "RW"}
        if selected == "Centers":
            return position == "C"
        if selected == "Left Wings":
            return position == "LW"
        if selected == "Right Wings":
            return position == "RW"
        return True

    def _render_bulk_stats_tree(self) -> None:
        if not hasattr(self, "bulk_stats_tree"):
            return
        self._clear_tree(self.bulk_stats_tree)
        for index, row in enumerate(self.bulk_attribute_recommendations):
            if not self._bulk_position_matches(row):
                continue
            change = _safe_int(row.get("overall_change"), _safe_int(row.get("suggested_overall"), 0) - _safe_int(row.get("current_overall"), 0))
            summary = str(row.get("summary") or "")
            if row.get("metrics_baseline_note"):
                summary = f"{row.get('metrics_baseline_note')} | {summary}"
            self.bulk_stats_tree.insert(
                "",
                "end",
                iid=f"bulk-{index}",
                text=str(row["player_name"]),
                values=(
                    row.get("position") or "",
                    row.get("league") or "",
                    row.get("team") or "",
                    f"{row.get('season_used')}:{row.get('games_played')}",
                    f"{float(row.get('ice_time_minutes') or 0):.1f}",
                    row.get("current_overall"),
                    row.get("suggested_overall"),
                    f"{change:+d}",
                    summary,
                ),
            )

    def _selected_bulk_metric_indices(self) -> list[int]:
        if not hasattr(self, "bulk_stats_tree"):
            return []
        indices = {
            _safe_int(iid.split("-", 1)[1], -1)
            for iid in self.bulk_stats_tree.selection()
            if iid.startswith("bulk-")
        }
        return sorted(index for index in indices if 0 <= index < len(self.bulk_attribute_recommendations))

    def _show_bulk_metrics_context_menu(self, event) -> None:
        iid = self.bulk_stats_tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.bulk_stats_tree.selection():
            self.bulk_stats_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Open In Attributes", command=self._open_selected_bulk_metric_player)
        menu.add_separator()
        menu.add_command(label="Remove From This Preview", command=self._remove_selected_bulk_metric_players)
        menu.add_command(
            label="Move To Manual Review (Attributes)",
            command=self._move_selected_bulk_metrics_to_manual_review,
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _remove_selected_bulk_metric_players(self) -> None:
        indices = set(self._selected_bulk_metric_indices())
        if not indices:
            self._set_status("Select one or more metric preview players first.")
            return
        self.bulk_attribute_recommendations = [
            row
            for index, row in enumerate(self.bulk_attribute_recommendations)
            if index not in indices
        ]
        self._render_bulk_stats_tree()
        self._set_status(f"Removed {len(indices)} player(s) from this metrics preview.")

    def _move_selected_bulk_metrics_to_manual_review(self) -> None:
        indices = self._selected_bulk_metric_indices()
        if not indices:
            self._set_status("Select one or more metric preview players first.")
            return
        existing_index = {
            _safe_int(row.get("player_id"), -1): index
            for index, row in enumerate(self.manual_metric_review)
        }
        for index in indices:
            record = dict(self.bulk_attribute_recommendations[index])
            record["manual_review_added_at"] = datetime.now().isoformat()
            record["manual_review_reason"] = "Moved from advanced metrics preview"
            player_id = _safe_int(record.get("player_id"), -1)
            if player_id in existing_index:
                self.manual_metric_review[existing_index[player_id]] = record
            else:
                existing_index[player_id] = len(self.manual_metric_review)
                self.manual_metric_review.append(record)
        self._save_manual_metric_review()
        self._render_manual_metric_review_tree()
        excluded = set(indices)
        self.bulk_attribute_recommendations = [
            row
            for index, row in enumerate(self.bulk_attribute_recommendations)
            if index not in excluded
        ]
        self._render_bulk_stats_tree()
        self._set_status(
            f"Moved {len(indices)} player(s) to the Attributes manual-review list and excluded them from bulk previews."
        )

    def _toggle_manual_metrics_panel(self) -> None:
        self.manual_metrics_expanded = not self.manual_metrics_expanded
        if self.manual_metrics_expanded:
            self.manual_metric_body.pack(fill="x", after=self.manual_metric_frame.winfo_children()[0])
        else:
            self.manual_metric_body.pack_forget()
        self._update_manual_metric_toggle_label()

    def _update_manual_metric_toggle_label(self) -> None:
        if not hasattr(self, "manual_metric_toggle_var"):
            return
        marker = "-" if self.manual_metrics_expanded else "+"
        self.manual_metric_toggle_var.set(
            f"{marker}  MANUAL METRICS REVIEW ({len(self.manual_metric_review)})"
        )

    def _render_manual_metric_review_tree(self) -> None:
        if not hasattr(self, "manual_metric_tree"):
            return
        self._update_manual_metric_toggle_label()
        self._clear_tree(self.manual_metric_tree)
        indexed_rows = sorted(
            enumerate(self.manual_metric_review),
            key=lambda item: (
                str(item[1].get("team") or ""),
                str(item[1].get("player_name") or ""),
            ),
        )
        for index, row in indexed_rows:
            change = _safe_int(
                row.get("overall_change"),
                _safe_int(row.get("suggested_overall"), 0) - _safe_int(row.get("current_overall"), 0),
            )
            self.manual_metric_tree.insert(
                "",
                "end",
                iid=f"manual-metric-{index}",
                text=str(row.get("player_name") or "Unknown player"),
                values=(
                    row.get("position") or "",
                    row.get("league") or "",
                    row.get("team") or "",
                    row.get("current_overall") or "",
                    row.get("suggested_overall") or "",
                    f"{change:+d}",
                ),
            )

    def _selected_manual_metric_indices(self) -> list[int]:
        if not hasattr(self, "manual_metric_tree"):
            return []
        indices = {
            _safe_int(iid.rsplit("-", 1)[1], -1)
            for iid in self.manual_metric_tree.selection()
            if iid.startswith("manual-metric-")
        }
        return sorted(index for index in indices if 0 <= index < len(self.manual_metric_review))

    def _show_manual_metrics_context_menu(self, event) -> None:
        iid = self.manual_metric_tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.manual_metric_tree.selection():
            self.manual_metric_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Load Player And Suggested Edits", command=self._open_manual_metric_player)
        menu.add_command(label="Remove From Manual Review", command=self._remove_selected_manual_metric_players)
        menu.tk_popup(event.x_root, event.y_root)

    def _on_manual_metric_selected(self, _event=None) -> None:
        if len(self.manual_metric_tree.selection()) == 1:
            self._open_manual_metric_player()

    def _select_all_manual_metrics(self):
        if not hasattr(self, "manual_metric_tree"):
            return "break"
        self.manual_metric_tree.selection_set(self.manual_metric_tree.get_children())
        self._set_status(f"Selected {len(self.manual_metric_tree.selection())} manual-review player(s).")
        return "break"

    def _remove_selected_manual_metric_players(self) -> None:
        indices = set(self._selected_manual_metric_indices())
        if not indices:
            self._set_status("Select one or more manual-review players first.")
            return
        self.manual_metric_review = [
            row
            for index, row in enumerate(self.manual_metric_review)
            if index not in indices
        ]
        self._save_manual_metric_review()
        self._render_manual_metric_review_tree()
        self._set_status(
            f"Removed {len(indices)} player(s) from Manual Metrics Review; future previews may include them again."
        )

    def _stage_metric_review_suggestions(self, metric_row: dict[str, object], player_id: int) -> None:
        if self.selected_player is None or self.selected_player.player_id != player_id:
            return
        queued_suggestions = metric_row.get("display_updates") or metric_row.get("metric_targets") or {}
        queued_notes = metric_row.get("notes") or {}
        self.edge_suggestions = {
            str(label): _safe_int(value, 0)
            for label, value in dict(queued_suggestions).items()
        }
        self.edge_suggestion_notes = {
            str(label): str(note)
            for label, note in dict(queued_notes).items()
        }
        self.edge_metric_context = {
            "state_key": metric_row.get("metric_state_key") or "",
            "signature": metric_row.get("metric_signature") or "",
            "targets": dict(metric_row.get("metric_targets") or self.edge_suggestions),
            "player_id": player_id,
            "season": _safe_int(metric_row.get("season_used"), 0),
            "player_kind": metric_row.get("player_kind") or "skater",
            "source_mode": "bulk-manual-review",
        }
        self._refresh_attribute_edge_notes()

    def _focus_metric_player(
        self,
        player_id: int,
        *,
        open_attributes: bool,
        metric_row: dict[str, object] | None = None,
    ) -> bool:
        entry = self.player_by_id.get(player_id)
        if entry is None:
            self._set_status("That player is no longer present in the active roster.")
            return False
        self._load_player(entry)
        for iid, visible_entry in getattr(self, "player_iid_to_entry", {}).items():
            if visible_entry.player_id == player_id:
                self.player_tree.selection_set(iid)
                self.player_tree.see(iid)
                break
        if metric_row is not None:
            queued_row = dict(metric_row)
            self._stage_metric_review_suggestions(queued_row, player_id)
            # Selecting the matching row in the main roster can enqueue its own
            # load event. Restage after idle so those suggestions cannot be
            # cleared by that harmless second load.
            self.root.after_idle(
                lambda row=queued_row, queued_player_id=player_id: self._stage_metric_review_suggestions(
                    row,
                    queued_player_id,
                )
            )
        if open_attributes:
            self.tabs.select(self.attributes_tab)
        if metric_row is not None:
            self._set_status(
                f"Loaded {entry.full_name} with queued suggestions shown beside the attributes."
            )
        return True

    def _open_selected_bulk_metric_player(self) -> None:
        indices = self._selected_bulk_metric_indices()
        if not indices:
            self._set_status("Select a metric preview player first.")
            return
        row = self.bulk_attribute_recommendations[indices[0]]
        self._focus_metric_player(
            _safe_int(row.get("player_id"), -1),
            open_attributes=True,
            metric_row=row,
        )

    def _open_manual_metric_player(self) -> None:
        indices = self._selected_manual_metric_indices()
        if not indices:
            self._set_status("Select a manual-review player first.")
            return
        row = self.manual_metric_review[indices[0]]
        self._focus_metric_player(
            _safe_int(row.get("player_id"), -1),
            open_attributes=True,
            metric_row=row,
        )

    def _sort_bulk_stats_tree(self, column: str, reverse: bool) -> None:
        if not hasattr(self, "bulk_stats_tree"):
            return
        numeric_columns = {"gp", "toi", "current", "suggested", "change"}
        rows: list[tuple[object, str]] = []
        for iid in self.bulk_stats_tree.get_children(""):
            value = self.bulk_stats_tree.item(iid, "text") if column == "#0" else self.bulk_stats_tree.set(iid, column)
            if column == "gp" and ":" in str(value):
                value = str(value).split(":", 1)[1]
            if column in numeric_columns:
                try:
                    sort_value: object = float(str(value).replace("+", ""))
                except ValueError:
                    sort_value = -999.0
            else:
                sort_value = str(value).lower()
            rows.append((sort_value, iid))
        rows.sort(key=lambda item: item[0], reverse=reverse)
        for index, (_value, iid) in enumerate(rows):
            self.bulk_stats_tree.move(iid, "", index)
        label = self.bulk_stats_tree.heading(column, "text")
        self.bulk_stats_tree.heading(
            column,
            text=label,
            command=lambda key=column: self._sort_bulk_stats_tree(key, not reverse),
        )

    def _sort_bulk_by_largest_change(self) -> None:
        if not hasattr(self, "bulk_stats_tree"):
            return
        rows = []
        for iid in self.bulk_stats_tree.get_children(""):
            index = _safe_int(iid.split("-", 1)[1], -1)
            if not (0 <= index < len(self.bulk_attribute_recommendations)):
                continue
            row = self.bulk_attribute_recommendations[index]
            change = _safe_int(row.get("overall_change"), 0)
            rows.append((abs(change), change, iid))
        rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for index, (_absolute, _change, iid) in enumerate(rows):
            self.bulk_stats_tree.move(iid, "", index)
        self._set_status("Sorted preview by largest overall change.")

    def _select_all_bulk_stats(self):
        if not hasattr(self, "bulk_stats_tree"):
            return "break"
        self.bulk_stats_tree.selection_set(self.bulk_stats_tree.get_children())
        self._set_status(f"Selected {len(self.bulk_stats_tree.selection())} visible metric preview row(s).")
        return "break"

    def _on_bulk_metric_selected(self, _event=None) -> None:
        selection = self.bulk_stats_tree.selection() if hasattr(self, "bulk_stats_tree") else ()
        if len(selection) != 1:
            return
        index = _safe_int(selection[0].split("-", 1)[1], -1)
        if not (0 <= index < len(self.bulk_attribute_recommendations)):
            return
        row = self.bulk_attribute_recommendations[index]
        player_id = _safe_int(row.get("player_id"), -1)
        self._focus_metric_player(player_id, open_attributes=False)

    def _apply_org_attribute_preview(self, *, apply_all: bool) -> None:
        if self.workspace is None:
            return
        if not self.bulk_attribute_recommendations:
            self._set_status("Preview an organization first.")
            return
        if apply_all:
            indices = [
                int(item.split("-", 1)[1])
                for item in self.bulk_stats_tree.get_children()
                if item.startswith("bulk-")
            ]
        else:
            indices = [
                int(item.split("-", 1)[1])
                for item in self.bulk_stats_tree.selection()
                if item.startswith("bulk-")
            ]
        if not indices:
            self._set_status("Select preview rows first, or use Apply All Preview.")
            return

        def worker():
            prepared: list[tuple[str, str, dict[str, int], dict[str, object]]] = []
            errors: list[str] = []
            for index in indices:
                if not (0 <= index < len(self.bulk_attribute_recommendations)):
                    continue
                row = self.bulk_attribute_recommendations[index]
                try:
                    prepared.append((str(row["first_name"]), str(row["last_name"]), dict(row["raw_updates"]), row))
                except Exception as exc:
                    errors.append(f"{row.get('player_name')}: {exc}")
            results = []
            log_results: list[dict[str, object]] = []
            applied_player_ids: list[int] = []
            metric_contexts: list[dict[str, object]] = []
            if prepared:
                skater_prepared = [
                    (first, last, updates, row)
                    for first, last, updates, row in prepared
                    if row.get("player_kind") != "goalie"
                ]
                goalie_prepared = [
                    (first, last, updates, row)
                    for first, last, updates, row in prepared
                    if row.get("player_kind") == "goalie"
                ]
                if skater_prepared:
                    results.extend(update_many_player_ratings(
                        self.workspace.working_db,
                        [(first, last, updates, _safe_int(row.get("player_id"), -1)) for first, last, updates, row in skater_prepared],
                        snapshot_cache=self.player_snapshot_cache,
                    ))
                if goalie_prepared:
                    results.extend(update_many_player_goalie_ratings(
                        self.workspace.working_db,
                        [(first, last, updates, _safe_int(row.get("player_id"), -1)) for first, last, updates, row in goalie_prepared],
                        snapshot_cache=self.player_snapshot_cache,
                    ))
                result_by_id = {_safe_int(result.get("player_id"), -1): result for result in results}
                for first, last, _updates, row in prepared:
                    player_id = _safe_int(row.get("player_id"), -1)
                    result = result_by_id.get(player_id, {})
                    if result:
                        applied_player_ids.append(player_id)
                        metric_contexts.append(
                            {
                                "state_key": row.get("metric_state_key") or "",
                                "signature": row.get("metric_signature") or "",
                                "targets": row.get("metric_targets") or {},
                                "player_id": player_id,
                                "season": row.get("season_used") or season,
                                "player_kind": row.get("player_kind") or "skater",
                                "source_mode": "bulk",
                            }
                        )
                    log_results.append(
                        {
                            "player": row["player_name"],
                            "player_kind": row.get("player_kind"),
                            "organization": row.get("organization"),
                            "overall": f"{row.get('current_overall')}->{row.get('suggested_overall')}",
                            "updates": row.get("display_updates"),
                            "result": result,
                        }
                    )
            if results:
                sync_working_db_to_roster(self.workspace)
            return len(results), errors, log_results, applied_player_ids, metric_contexts

        def success(result):
            applied, errors, log_results, applied_player_ids, metric_contexts = result
            for context in metric_contexts:
                self._remember_advanced_metric_target(context, persist=False)
            if metric_contexts:
                self._save_advanced_metric_targets()
            applied_id_set = set(applied_player_ids)
            self.bulk_attribute_recommendations = [
                row
                for row in self.bulk_attribute_recommendations
                if _safe_int(row.get("player_id"), -1) not in applied_id_set
            ]
            self._render_bulk_stats_tree()
            self._log_actions("bulk-organization-attributes", log_results)
            self._reload_after_player_write(
                f"Applied bulk attribute updates to {applied} player(s).",
                results=log_results,
                index_changed=False,
            )
            if errors:
                messagebox.showwarning("Some bulk updates failed", "\n".join(errors[:8]))

        self._run_background("Applying organization ratings", worker, success)

    def _load_edge_for_selected(self) -> None:
        if self.selected_player is None:
            self._on_player_selected()
        if self.selected_player is None:
            self._set_status("Select a player before loading advanced metrics.")
            return
        player_name = self.selected_player.full_name
        selected_player_id = self.selected_player.player_id
        roster_team = self.selected_player.current_team_abbrev or self.selected_player.organization_abbrev
        try:
            metrics_season = int(self.bulk_season_var.get() or 2025)
        except ValueError:
            metrics_season = 2025
        if hasattr(self, "edge_text"):
            self.edge_text.delete("1.0", "end")
            self.edge_text.insert("1.0", f"Loading advanced metrics for {player_name}...")
        player_kind = "goalie" if self._player_kind() == "goalie" else "skater"
        ratings_row = self._current_ratings_row()
        current_attributes = (
            self._display_ratings_from_row(ratings_row, player_kind=player_kind)
            if ratings_row is not None
            else None
        )
        position = self._position_label_from_code(_safe_int(self.snapshot.bio.get("aljv"), -1)) if self.snapshot else ""

        def worker():
            return self._build_advanced_metric_recommendation(
                player_name=player_name,
                roster_team=roster_team,
                season=metrics_season,
                player_kind=player_kind,
                current_attributes=current_attributes,
                position=position,
                player_id=selected_player_id,
            )

        def success(metric):
            hit = metric.get("edge_hit")
            data = dict(metric.get("edge_data") or {})
            edge_error = metric.get("edge_error")
            mp_match = metric.get("mp_match")
            mp_rec = metric.get("mp_rec")
            mp_error = metric.get("mp_error")
            self.official_player_hit = hit
            self.edge_suggestions = dict(metric.get("suggestions") or {})
            self.edge_suggestion_notes = dict(metric.get("notes") or {})
            self.edge_metric_context = {
                "state_key": metric.get("metric_state_key") or "",
                "signature": metric.get("metric_signature") or "",
                "targets": metric.get("metric_targets") or {},
                "player_id": selected_player_id,
                "season": metrics_season,
                "player_kind": player_kind,
                "source_mode": "combined",
            }
            self._refresh_attribute_edge_notes()
            self._render_edge(
                hit,
                data,
                self.edge_suggestions,
                self.edge_suggestion_notes,
                mp_match=mp_match,
                mp_rec=mp_rec,
                edge_error=edge_error,
                mp_error=mp_error,
                player_kind=player_kind,
            )
            self.root.after(75, self._refresh_attribute_edge_notes)
            source_bits = []
            if mp_rec is not None and not mp_rec.skipped_reason:
                source_bits.append("MoneyPuck")
            if data:
                source_bits.append("NHL Edge Goalie" if player_kind == "goalie" else "NHL Edge")
            if metric.get("metrics_up_to_date"):
                self._set_status(f"{player_name} already matches the last applied Advanced Metrics target.")
            else:
                self._set_status(f"Loaded advanced metrics for {player_name}: {', '.join(source_bits) or 'no usable source match'}.")

        self._run_background("Loading advanced metrics", worker, success)

    def _current_attribute_rating(self, label: str) -> int | None:
        field_by_label = {spec.label: spec.field for spec in self._attribute_specs_for_selected_player()}
        field = field_by_label.get(label)
        if not field:
            return None
        if field in self.attribute_vars:
            return _safe_int(self.attribute_vars[field].get(), self.attribute_original_values.get(field, 0))
        if field in self.attribute_original_values:
            return self.attribute_original_values[field]
        return None

    def _guard_edge_rating(self, label: str, target: int, *, max_drop: int = 6) -> tuple[int, str]:
        current = self._current_attribute_rating(label)
        if current is None or current <= 0:
            return target, ""
        guarded = max(target, current - max_drop)
        if guarded != target:
            return guarded, f"raw tracking target {target}; guarded from current {current}"
        return target, ""

    def _add_edge_suggestion(
        self,
        suggestions: dict[str, int],
        notes: dict[str, str],
        label: str,
        score: float | None,
        *,
        floor: int,
        ceiling: int,
        note: str,
        max_drop: int = 6,
    ) -> None:
        target = _edge_rating_from_score(score, floor=floor, ceiling=ceiling)
        if target is None:
            return
        suggestions[label] = target
        notes[label] = note

    def _build_edge_suggestions(self, data: dict) -> tuple[dict[str, int], dict[str, str]]:
        suggestions: dict[str, int] = {}
        notes: dict[str, str] = {}
        top_shot = data.get("topShotSpeed") or {}
        skating = data.get("skatingSpeed") or {}
        speed = skating.get("speedMax") or {}
        bursts = skating.get("burstsOver20") or {}
        distance = data.get("totalDistanceSkated") or {}
        max_game = data.get("distanceMaxGame") or {}
        zone = data.get("zoneTimeDetails") or {}
        all_sog = _edge_summary(data, "all")
        high_sog = _edge_summary(data, "high")
        mid_sog = _edge_summary(data, "mid")
        long_sog = _edge_summary(data, "long")

        max_speed_pct = _edge_percentile(speed.get("percentile"))
        burst_pct = _edge_percentile(bursts.get("percentile"))
        distance_pct = _edge_percentile(distance.get("percentile"))
        max_game_pct = _edge_percentile(max_game.get("percentile"))
        top_shot_pct = _edge_percentile(top_shot.get("percentile"))
        shots_pct = _edge_percentile(all_sog.get("shotsPercentile"))
        goals_pct = _edge_percentile(all_sog.get("goalsPercentile"))
        shooting_pct = _edge_percentile(all_sog.get("shootingPctgPercentile"))
        high_shots_pct = _edge_percentile(high_sog.get("shotsPercentile"))
        mid_shots_pct = _edge_percentile(mid_sog.get("shotsPercentile"))
        long_shots_pct = _edge_percentile(long_sog.get("shotsPercentile"))
        long_goals_pct = _edge_percentile(long_sog.get("goalsPercentile"))
        long_eff_pct = _edge_percentile(long_sog.get("shootingPctgPercentile"))
        offensive_zone_pct = _edge_percentile(zone.get("offensiveZonePercentile"))
        offensive_ev_pct = _edge_percentile(zone.get("offensiveZoneEvPercentile"))
        neutral_zone_pct = _edge_percentile(zone.get("neutralZonePercentile"))
        defensive_zone_pct = _edge_percentile(zone.get("defensiveZonePercentile"))
        point_shots_pct = _edge_area_score(data, ("Center Point", "L Point", "R Point"))
        circle_shots_pct = _edge_area_score(data, ("L Circle", "R Circle"))
        slot_shots_pct = _edge_area_score(data, ("Crease", "High Slot", "Low Slot"))
        net_side_pct = _edge_area_score(data, ("L Net Side", "R Net Side", "Behind the Net"))

        self._add_edge_suggestion(
            suggestions,
            notes,
            "Speed",
            _weighted_edge_score((max_speed_pct, 0.50), (burst_pct, 0.35), (max_game_pct, 0.15)),
            floor=78,
            ceiling=96,
            note=f"max {_edge_pct_label(max_speed_pct)}, bursts {_edge_pct_label(burst_pct)}, max-game distance {_edge_pct_label(max_game_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Acceleration",
            _weighted_edge_score((burst_pct, 0.60), (max_speed_pct, 0.25), (max_game_pct, 0.15)),
            floor=76,
            ceiling=96,
            note=f"bursts {_edge_pct_label(burst_pct)} weighted above one-off max speed {_edge_pct_label(max_speed_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Agility",
            _weighted_edge_score((burst_pct, 0.35), (max_speed_pct, 0.25), (offensive_zone_pct, 0.20), (slot_shots_pct, 0.20)),
            floor=74,
            ceiling=96,
            note=f"bursts {_edge_pct_label(burst_pct)}, offensive-zone {_edge_pct_label(offensive_zone_pct)}, slot activity {_edge_pct_label(slot_shots_pct)}",
            max_drop=5,
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Endurance",
            _weighted_edge_score((distance_pct, 0.65), (max_game_pct, 0.35)),
            floor=70,
            ceiling=97,
            note=f"total distance {_edge_pct_label(distance_pct)}, max-game distance {_edge_pct_label(max_game_pct)}",
        )

        self._add_edge_suggestion(
            suggestions,
            notes,
            "Slap Shot Power",
            _weighted_edge_score((top_shot_pct, 0.60), (shots_pct, 0.15), (long_shots_pct, 0.15), (point_shots_pct, 0.10)),
            floor=72,
            ceiling=98,
            note=f"top shot {_edge_pct_label(top_shot_pct)} with volume check: all {_edge_pct_label(shots_pct)}, long/point {_edge_pct_label(long_shots_pct)}/{_edge_pct_label(point_shots_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Wrist Shot Power",
            _weighted_edge_score((top_shot_pct, 0.55), (shots_pct, 0.20), (mid_shots_pct, 0.10), (high_shots_pct, 0.10), (circle_shots_pct, 0.05)),
            floor=72,
            ceiling=97,
            note=f"top shot {_edge_pct_label(top_shot_pct)} plus repeat shot volume {_edge_pct_label(shots_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Slap Shot Accuracy",
            _weighted_edge_score((long_eff_pct, 0.35), (long_goals_pct, 0.25), (long_shots_pct, 0.20), (point_shots_pct, 0.20)),
            floor=68,
            ceiling=95,
            note=f"long-shot efficiency {_edge_pct_label(long_eff_pct)}, goals {_edge_pct_label(long_goals_pct)}, long/point volume {_edge_pct_label(long_shots_pct)}/{_edge_pct_label(point_shots_pct)}",
            max_drop=5,
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Wrist Shot Accuracy",
            _weighted_edge_score((shooting_pct, 0.35), (goals_pct, 0.25), (shots_pct, 0.20), (high_shots_pct, 0.10), (mid_shots_pct, 0.10)),
            floor=68,
            ceiling=96,
            note=f"shooting pct {_edge_pct_label(shooting_pct)} tempered by goals {_edge_pct_label(goals_pct)} and shot volume {_edge_pct_label(shots_pct)}",
            max_drop=5,
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Off. Awareness",
            _weighted_edge_score((offensive_zone_pct, 0.25), (offensive_ev_pct, 0.20), (shots_pct, 0.25), (goals_pct, 0.20), (slot_shots_pct, 0.10)),
            floor=72,
            ceiling=98,
            note=f"offensive zone {_edge_pct_label(offensive_zone_pct)}, EV zone {_edge_pct_label(offensive_ev_pct)}, shots/goals {_edge_pct_label(shots_pct)}/{_edge_pct_label(goals_pct)}",
            max_drop=5,
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Def. Awareness",
            _weighted_edge_score((defensive_zone_pct, 0.45), (neutral_zone_pct, 0.20), (distance_pct, 0.15), (offensive_zone_pct, 0.20)),
            floor=68,
            ceiling=94,
            note=f"zone-time context only: defensive {_edge_pct_label(defensive_zone_pct)}, neutral {_edge_pct_label(neutral_zone_pct)}; no direct blocks/takeaways feed",
            max_drop=4,
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Puck Control",
            _weighted_edge_score((offensive_zone_pct, 0.30), (offensive_ev_pct, 0.20), (slot_shots_pct, 0.20), (net_side_pct, 0.15), (shots_pct, 0.15)),
            floor=70,
            ceiling=96,
            note=f"offensive possession {_edge_pct_label(offensive_zone_pct)}, slot/net activity {_edge_pct_label(slot_shots_pct)}/{_edge_pct_label(net_side_pct)}",
            max_drop=5,
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Deking",
            _weighted_edge_score((burst_pct, 0.30), (max_speed_pct, 0.25), (offensive_zone_pct, 0.20), (circle_shots_pct, 0.15), (neutral_zone_pct, 0.10)),
            floor=70,
            ceiling=96,
            note=f"burst skating {_edge_pct_label(burst_pct)}, max speed {_edge_pct_label(max_speed_pct)}, circle activity {_edge_pct_label(circle_shots_pct)}",
            max_drop=5,
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Hand-Eye",
            _weighted_edge_score((shooting_pct, 0.25), (goals_pct, 0.25), (high_shots_pct, 0.20), (slot_shots_pct, 0.20), (shots_pct, 0.10)),
            floor=68,
            ceiling=96,
            note=f"finishing {_edge_pct_label(shooting_pct)}, goals {_edge_pct_label(goals_pct)}, high/slot looks {_edge_pct_label(high_shots_pct)}/{_edge_pct_label(slot_shots_pct)}",
            max_drop=5,
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Poise",
            _weighted_edge_score((goals_pct, 0.30), (shooting_pct, 0.25), (offensive_zone_pct, 0.20), (distance_pct, 0.15), (high_shots_pct, 0.10)),
            floor=68,
            ceiling=96,
            note=f"goals {_edge_pct_label(goals_pct)}, shooting pct {_edge_pct_label(shooting_pct)}, usage {_edge_pct_label(distance_pct)}",
            max_drop=5,
        )
        return suggestions, notes

    def _build_goalie_edge_suggestions(self, data: dict) -> tuple[dict[str, int], dict[str, str]]:
        suggestions: dict[str, int] = {}
        notes: dict[str, str] = {}
        stats = data.get("stats") or {}
        all_saves = _goalie_edge_summary(data, "all")
        high = _goalie_edge_summary(data, "high")
        mid = _goalie_edge_summary(data, "mid")
        long = _goalie_edge_summary(data, "long")

        all_save_pct = _edge_percentile(all_saves.get("savePctgPercentile"))
        all_saves_volume = _edge_percentile(all_saves.get("savesPercentile"))
        high_save_pct = _edge_percentile(high.get("savePctgPercentile"))
        high_saves_volume = _edge_percentile(high.get("savesPercentile"))
        mid_save_pct = _edge_percentile(mid.get("savePctgPercentile"))
        long_save_pct = _edge_percentile(long.get("savePctgPercentile"))
        games_above_900 = _edge_percentile((stats.get("gamesAbove900") or {}).get("percentile"))
        gaa_pct = _edge_percentile((stats.get("goalsAgainstAvg") or {}).get("percentile"))
        goal_diff_pct = _edge_percentile((stats.get("goalDifferentialPer60") or {}).get("percentile"))

        self._add_edge_suggestion(
            suggestions,
            notes,
            "Glove Side Low",
            _weighted_edge_score((mid_save_pct, 0.35), (long_save_pct, 0.30), (all_save_pct, 0.20), (games_above_900, 0.15)),
            floor=58,
            ceiling=96,
            note=f"mid {_edge_pct_label(mid_save_pct)}, long {_edge_pct_label(long_save_pct)}, all save {_edge_pct_label(all_save_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Stick Side Low",
            _weighted_edge_score((mid_save_pct, 0.35), (long_save_pct, 0.30), (all_save_pct, 0.20), (games_above_900, 0.15)),
            floor=58,
            ceiling=96,
            note=f"mid {_edge_pct_label(mid_save_pct)}, long {_edge_pct_label(long_save_pct)}, all save {_edge_pct_label(all_save_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Glove Side High",
            _weighted_edge_score((high_save_pct, 0.42), (mid_save_pct, 0.22), (all_save_pct, 0.20), (high_saves_volume, 0.16)),
            floor=58,
            ceiling=97,
            note=f"high-danger save {_edge_pct_label(high_save_pct)}, mid {_edge_pct_label(mid_save_pct)}, workload {_edge_pct_label(high_saves_volume)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Stick Side High",
            _weighted_edge_score((high_save_pct, 0.42), (mid_save_pct, 0.22), (all_save_pct, 0.20), (high_saves_volume, 0.16)),
            floor=58,
            ceiling=97,
            note=f"high-danger save {_edge_pct_label(high_save_pct)}, mid {_edge_pct_label(mid_save_pct)}, workload {_edge_pct_label(high_saves_volume)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Five Hole",
            _weighted_edge_score((high_save_pct, 0.34), (mid_save_pct, 0.28), (all_save_pct, 0.22), (games_above_900, 0.16)),
            floor=58,
            ceiling=97,
            note=f"high/mid danger save {_edge_pct_label(high_save_pct)}/{_edge_pct_label(mid_save_pct)}, all save {_edge_pct_label(all_save_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Agility",
            _weighted_edge_score((high_save_pct, 0.34), (high_saves_volume, 0.22), (all_save_pct, 0.18), (games_above_900, 0.16), (goal_diff_pct, 0.10)),
            floor=58,
            ceiling=97,
            note=f"high-danger save/workload {_edge_pct_label(high_save_pct)}/{_edge_pct_label(high_saves_volume)}, consistency {_edge_pct_label(games_above_900)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Consistency",
            _weighted_edge_score((games_above_900, 0.38), (all_save_pct, 0.28), (goal_diff_pct, 0.18), (gaa_pct, 0.16)),
            floor=58,
            ceiling=98,
            note=f"games above .900 {_edge_pct_label(games_above_900)}, all save {_edge_pct_label(all_save_pct)}, GAA {_edge_pct_label(gaa_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Breakaway",
            _weighted_edge_score((high_save_pct, 0.48), (high_saves_volume, 0.22), (games_above_900, 0.18), (all_save_pct, 0.12)),
            floor=58,
            ceiling=98,
            note=f"high-danger save {_edge_pct_label(high_save_pct)} with workload {_edge_pct_label(high_saves_volume)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Angles",
            _weighted_edge_score((all_save_pct, 0.30), (mid_save_pct, 0.24), (high_save_pct, 0.22), (gaa_pct, 0.14), (games_above_900, 0.10)),
            floor=58,
            ceiling=98,
            note=f"all/mid/high save percentiles {_edge_pct_label(all_save_pct)}/{_edge_pct_label(mid_save_pct)}/{_edge_pct_label(high_save_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Poise",
            _weighted_edge_score((games_above_900, 0.30), (high_save_pct, 0.24), (all_save_pct, 0.22), (goal_diff_pct, 0.14), (all_saves_volume, 0.10)),
            floor=58,
            ceiling=98,
            note=f"consistency {_edge_pct_label(games_above_900)}, high/all save {_edge_pct_label(high_save_pct)}/{_edge_pct_label(all_save_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Vision",
            _weighted_edge_score((all_save_pct, 0.30), (high_save_pct, 0.28), (mid_save_pct, 0.22), (games_above_900, 0.20)),
            floor=58,
            ceiling=98,
            note=f"all/high/mid save percentiles {_edge_pct_label(all_save_pct)}/{_edge_pct_label(high_save_pct)}/{_edge_pct_label(mid_save_pct)}",
        )
        self._add_edge_suggestion(
            suggestions,
            notes,
            "Endurance",
            _weighted_edge_score((all_saves_volume, 0.70), (high_saves_volume, 0.30)),
            floor=58,
            ceiling=96,
            note=f"save volume {_edge_pct_label(all_saves_volume)}, high-danger workload {_edge_pct_label(high_saves_volume)}",
        )
        return suggestions, notes

    def _render_edge(
        self,
        hit,
        data: dict,
        suggestions: dict[str, int],
        notes: dict[str, str],
        *,
        mp_match=None,
        mp_rec=None,
        edge_error: str | None = None,
        mp_error: str | None = None,
        player_kind: str = "skater",
    ) -> None:
        if player_kind == "goalie":
            self._render_goalie_edge(
                hit,
                data,
                suggestions,
                notes,
                mp_match=mp_match,
                mp_rec=mp_rec,
                edge_error=edge_error,
                mp_error=mp_error,
            )
            return
        self.edge_text.delete("1.0", "end")
        lines = ["Advanced Metrics sources:"]
        if mp_match is not None:
            lines.append(
                f"- MoneyPuck match: {mp_match.row.get('name') or mp_match.row.get('playerName') or 'matched player'}"
                f" | {player_team(mp_match.row)} | {games_played(mp_match.row)} GP | {mp_match.reason}"
            )
        elif mp_error:
            lines.append(f"- MoneyPuck: {mp_error}")
        if hit is not None:
            lines.append(f"- NHL Edge match: {hit.full_name} | {hit.team_abbrev} | NHL ID {hit.player_id}")
        elif edge_error:
            lines.append(f"- NHL Edge: {edge_error}")
        if mp_rec is not None and mp_rec.skipped_reason:
            lines.append(f"- MoneyPuck skipped: {mp_rec.skipped_reason}")
        elif mp_rec is not None and mp_rec.overall_note:
            lines.append(f"- Role target: {mp_rec.overall_note}")
        lines.append("")
        top_shot = data.get("topShotSpeed") or {}
        skating = data.get("skatingSpeed") or {}
        speed = skating.get("speedMax") or {}
        bursts = skating.get("burstsOver20") or {}
        distance = data.get("totalDistanceSkated") or {}
        max_game = data.get("distanceMaxGame") or {}
        zone = data.get("zoneTimeDetails") or {}
        all_sog = _edge_summary(data, "all")
        high_sog = _edge_summary(data, "high")
        mid_sog = _edge_summary(data, "mid")
        long_sog = _edge_summary(data, "long")

        lines.append("How suggestions are blended:")
        lines.append("- MoneyPuck supplies the broad advanced-stat base: xG, assists, possession, defensive suppression, hits, blocks, penalties, workload, and faceoffs.")
        lines.append("- Absolute TOI/game supplies position-aware role credit. Fourth-line and third-pair workloads cap role-driving ratings; 25:00 receives full workload credit.")
        lines.append("- Specialist traits such as hitting, aggressiveness, fighting, shot power, and skating are not reduced by the TOI role cap.")
        lines.append("- Face-offs use won/taken totals: win percentage sets the base rating, and total draws taken caps small-sample players.")
        lines.append("- Shoot-Pass Bias follows the game scale: 0=shoot-heavy, 15=pass-heavy.")
        lines.append("- Discipline follows the game scale: lower values mean more penalty-prone, higher values mean more disciplined.")
        lines.append("- NHL Edge adjusts tracking-sensitive traits: skating speed, bursts, distance, shot speed, shot-location volume, and zone-time context.")
        lines.append("- 0th percentile tracking rows are ignored so missing/bad Edge values do not drag ratings down.")
        lines.append("")
        if top_shot:
            lines.append(f"Top shot speed: {_edge_number(top_shot.get('imperial'), ' mph')} | percentile {_edge_pct_label(_edge_percentile(top_shot.get('percentile')))}")
        if skating:
            lines.append(f"Max skating speed: {_edge_number(speed.get('imperial'), ' mph')} | percentile {_edge_pct_label(_edge_percentile(speed.get('percentile')))}")
            lines.append(f"Bursts over 20 mph: {_edge_number(bursts.get('value'))} | percentile {_edge_pct_label(_edge_percentile(bursts.get('percentile')))}")
        if distance:
            lines.append(f"Total distance skated: {_edge_number(distance.get('imperial'), ' mi')} | percentile {_edge_pct_label(_edge_percentile(distance.get('percentile')))}")
        if max_game:
            lines.append(f"Max distance in one game: {_edge_number(max_game.get('imperial'), ' mi')} | percentile {_edge_pct_label(_edge_percentile(max_game.get('percentile')))}")
        if all_sog:
            lines.append("")
            lines.append("Shot profile:")
            for label, row in (("All", all_sog), ("High danger", high_sog), ("Mid range", mid_sog), ("Long range", long_sog)):
                if not row:
                    continue
                lines.append(
                    f"- {label}: shots {row.get('shots', 0)} ({_edge_pct_label(_edge_percentile(row.get('shotsPercentile')))}), "
                    f"goals {row.get('goals', 0)} ({_edge_pct_label(_edge_percentile(row.get('goalsPercentile')))}), "
                    f"shooting pct {_edge_number(float(row.get('shootingPctg') or 0) * 100, '%')} ({_edge_pct_label(_edge_percentile(row.get('shootingPctgPercentile')))})"
                )
        if zone:
            lines.append("")
            lines.append("Zone-time context:")
            for label, pct_key, rank_key in (
                ("Offensive", "offensiveZonePctg", "offensiveZonePercentile"),
                ("Offensive EV", "offensiveZoneEvPctg", "offensiveZoneEvPercentile"),
                ("Neutral", "neutralZonePctg", "neutralZonePercentile"),
                ("Defensive", "defensiveZonePctg", "defensiveZonePercentile"),
            ):
                raw_pct = zone.get(pct_key)
                pct_text = "n/a" if raw_pct in (None, "") else f"{float(raw_pct) * 100:.1f}%"
                lines.append(f"- {label}: {pct_text} | percentile {_edge_pct_label(_edge_percentile(zone.get(rank_key)))}")
        lines.append("")
        lines.append("Suggested attribute targets:")
        if suggestions:
            for label, value in sorted(suggestions.items()):
                note = notes.get(label, "")
                lines.append(f"{label}: {value}" + (f" | {note}" if note else ""))
        else:
            lines.append("No percentile-based advanced metric suggestions were available.")
        self.edge_text.insert("1.0", "\n".join(lines))

    def _render_goalie_edge(
        self,
        hit,
        data: dict,
        suggestions: dict[str, int],
        notes: dict[str, str],
        *,
        mp_match=None,
        mp_rec=None,
        edge_error: str | None = None,
        mp_error: str | None = None,
    ) -> None:
        self.edge_text.delete("1.0", "end")
        lines = ["Advanced Metrics sources:"]
        if mp_match is not None:
            mp_row = mp_match.row
            lines.append(
                f"- MoneyPuck goalie match: {mp_row.get('name') or mp_row.get('playerName') or 'matched goalie'}"
                f" | {player_team(mp_row)} | {games_played(mp_row)} GP | {mp_match.reason}"
            )
            lines.append(
                f"- Results baseline: {float(mp_row.get('_save_pct') or 0):.3f} SV% | "
                f"{float(mp_row.get('_goals_against_per60') or 0):.2f} GAA | "
                f"{float(mp_row.get('_expected_save_pct') or 0):.3f} expected SV% | "
                f"{float(mp_row.get('_goals_saved_above_expected') or 0):+.1f} goals saved above expected"
            )
        elif mp_error:
            lines.append(f"- MoneyPuck: {mp_error}")
        if hit is not None:
            lines.append(f"- NHL Edge goalie match: {hit.full_name} | {hit.team_abbrev} | NHL ID {hit.player_id}")
        elif edge_error:
            lines.append(f"- NHL Edge Goalie: {edge_error}")
        if mp_rec is not None and mp_rec.skipped_reason:
            lines.append(f"- MoneyPuck skipped: {mp_rec.skipped_reason}")
        elif mp_rec is not None and mp_rec.overall_baseline is not None:
            lines.append(f"- Workload-adjusted goalie quality baseline: {mp_rec.overall_baseline} OVR")
        lines.append("")
        lines.append("How goalie suggestions are blended:")
        lines.append("- MoneyPuck anchors goalie quality with save percentage, GAA, expected save percentage, goals saved above expected, GSAE rate, and games played.")
        lines.append("- Games played controls confidence and the rating ceiling, preventing a small-sample backup from grading like a full-season elite starter.")
        lines.append("- NHL Edge goalie data adjusts location-sensitive traits using all/high/mid/long save-percentile context and games-above-.900 consistency.")
        lines.append("- Raw goals-against volume is not treated as skill by itself because heavy-starting goalies face more shots.")

        stats = data.get("stats") or {}
        if stats:
            lines.append("")
            lines.append("Goalie Edge summary:")
            for label, key in (
                ("GAA", "goalsAgainstAvg"),
                ("Games above .900", "gamesAbove900"),
                ("Goal differential / 60", "goalDifferentialPer60"),
                ("Point %", "pointPctg"),
            ):
                row = stats.get(key) or {}
                if row:
                    lines.append(f"- {label}: {_edge_number(row.get('value'))} | percentile {_edge_pct_label(_edge_percentile(row.get('percentile')))}")
        summaries = [
            ("All", _goalie_edge_summary(data, "all")),
            ("High danger", _goalie_edge_summary(data, "high")),
            ("Mid range", _goalie_edge_summary(data, "mid")),
            ("Long range", _goalie_edge_summary(data, "long")),
        ]
        if any(row for _label, row in summaries):
            lines.append("")
            lines.append("Shot-location save profile:")
            for label, row in summaries:
                if not row:
                    continue
                lines.append(
                    f"- {label}: save pct {_edge_number(float(row.get('savePctg') or 0) * 100, '%')} "
                    f"({_edge_pct_label(_edge_percentile(row.get('savePctgPercentile')))}), "
                    f"saves {row.get('saves', 0)} ({_edge_pct_label(_edge_percentile(row.get('savesPercentile')))})"
                )
        lines.append("")
        lines.append("Suggested attribute targets:")
        if suggestions:
            for label, value in sorted(suggestions.items()):
                note = notes.get(label, "")
                lines.append(f"{label}: {value}" + (f" | {note}" if note else ""))
        else:
            lines.append("No 20+ GP MoneyPuck goalie base match was available, so no attribute targets were staged.")
        self.edge_text.insert("1.0", "\n".join(lines))

    def _apply_edge_suggestions(self) -> None:
        if not self.edge_suggestions:
            self._set_status("Load advanced metric suggestions first.")
            return
        field_by_label = {spec.label: spec.field for spec in self._attribute_specs_for_selected_player()}
        applied = 0
        changed = 0
        for label, value in self.edge_suggestions.items():
            field = field_by_label.get(label)
            if field and field in self.attribute_vars:
                if _safe_int(self.attribute_vars[field].get(), -1) != int(value):
                    changed += 1
                self.attribute_vars[field].set(value)
                applied += 1
        self._update_attribute_budget()
        if changed:
            self._set_status(f"Applied {changed} changed Advanced Metrics target(s) to the sliders ({applied} checked).")
        else:
            self._set_status("This player already matches the loaded Advanced Metrics target.")

    def _scan_capwages_updates(self) -> None:
        if self.workspace is None:
            return
        player_index = tuple(self.player_index)
        organization_links = dict(self.organization_links)
        expansion_destination = self.expansion_destination_var.get()

        def worker():
            return build_capwages_roster_update(
                player_index,
                team_slugs=TEAM_SLUGS,
                organization_links=organization_links,
                expansion_destination=expansion_destination,
                force_refresh=True,
            )

        def success(queue):
            self.update_queue = queue
            self.update_errors = {}
            save_json_state(self.workspace, "update_queue.json", queue)
            save_json_state(self.workspace, "update_errors.json", {})
            self._render_update_queue()
            self._refresh_review()
            self._set_status(f"Found {len(queue.get('moves', []))} move proposals and {len(queue.get('create_candidates', []))} create candidates.")

        self._run_background("Scanning CapWages", worker, success)

    def _refresh_update_queue(self) -> None:
        if self.workspace is None:
            return
        loaded_queue = load_json_state(self.workspace, "update_queue.json", {"moves": [], "create_candidates": []})
        self.update_queue = filter_redundant_organization_moves(
            loaded_queue,
            self.player_index,
            self.organization_links,
        )
        if self.update_queue != loaded_queue:
            save_json_state(self.workspace, "update_queue.json", self.update_queue)
        self._load_update_vetoes()
        self._load_update_applied()
        self.update_errors = {
            str(key): str(value)
            for key, value in load_json_state(self.workspace, "update_errors.json", {}).items()
        }
        hidden_tokens = {
            self._update_move_token(row)
            for row in self.update_queue.get("moves", [])
            if self._move_is_hidden_player(row)
        }
        if hidden_tokens:
            self.update_vetoes.update(hidden_tokens)
            self._save_update_vetoes()
        self._render_update_queue()

    def _render_update_queue(self) -> None:
        self._clear_tree(self.update_tree)
        for index, row in enumerate(self.update_queue.get("moves", [])):
            token = self._update_move_token(row)
            if token in self.update_vetoes or token in self.update_applied:
                continue
            reason = str(row.get("reason") or "")
            if token in self.update_errors:
                reason = f"ERROR: {self.update_errors[token]} | {reason}"
            self.update_tree.insert(
                "",
                "end",
                iid=f"move-{index}",
                text=str(row.get("player_name") or ""),
                values=(row.get("from_team") or "", row.get("to_team") or "", row.get("source") or "", reason),
            )
        self.create_candidate_list.delete(0, "end")
        for row in self.update_queue.get("create_candidates", []):
            self.create_candidate_list.insert(
                "end",
                f"{row.get('player_name')} | {row.get('team')} | {row.get('position') or '?'} | drafted {row.get('drafted_by') or '?'} {row.get('draft_year') or ''}",
            )

    def _update_move_token(self, row: dict[str, object]) -> str:
        return f"{row.get('player_name')}|{row.get('from_team')}|{row.get('to_team')}"

    def _move_is_hidden_player(self, row: dict[str, object]) -> bool:
        wanted = normalize_name(str(row.get("player_name") or ""))
        matches = [player for player in self.player_index if normalize_name(player.full_name) == wanted]
        return bool(matches) and all(player.is_hidden for player in matches)

    def _save_update_errors(self) -> None:
        if self.workspace is not None:
            save_json_state(self.workspace, "update_errors.json", self.update_errors)

    def _load_update_vetoes(self) -> None:
        if self.workspace is None:
            self.update_vetoes = set()
            return
        values = load_json_state(self.workspace, "update_vetoes.json", [])
        self.update_vetoes = {str(item) for item in values}

    def _save_update_vetoes(self) -> None:
        if self.workspace is None:
            return
        save_json_state(self.workspace, "update_vetoes.json", sorted(self.update_vetoes))

    def _load_update_applied(self) -> None:
        if self.workspace is None:
            self.update_applied = set()
            return
        values = load_json_state(self.workspace, "update_applied.json", [])
        self.update_applied = {str(item) for item in values}

    def _save_update_applied(self) -> None:
        if self.workspace is None:
            return
        save_json_state(self.workspace, "update_applied.json", sorted(self.update_applied))

    def _pending_update_indices(self) -> list[int]:
        moves = self.update_queue.get("moves", [])
        return [
            index
            for index, row in enumerate(moves)
            if self._update_move_token(row) not in self.update_vetoes
            and self._update_move_token(row) not in self.update_applied
        ]

    def _visible_move_indices_from_selection(self, tree: ttk.Treeview) -> list[int]:
        indices: list[int] = []
        for item in tree.selection():
            if item.startswith("move-") or item.startswith("pending-"):
                try:
                    indices.append(int(item.split("-", 1)[1]))
                except ValueError:
                    continue
        return indices

    def _veto_selected_update_moves(self) -> None:
        moves = self.update_queue.get("moves", [])
        indices = self._visible_move_indices_from_selection(self.update_tree)
        if not indices:
            self._set_status("Select one or more proposed moves to veto.")
            return
        for index in indices:
            if 0 <= index < len(moves):
                self.update_vetoes.add(self._update_move_token(moves[index]))
                self._log_action("veto-auto-update-move", moves[index])
        self._save_update_vetoes()
        self._render_update_queue()
        self._refresh_review()
        self._set_status(f"Vetoed {len(indices)} proposed move(s).")

    def _load_organization_links(self) -> None:
        if self.workspace is None:
            self.organization_links = default_organization_links()
            return
        custom = load_json_state(self.workspace, "organization_links.json", {})
        self.organization_links = default_organization_links()
        for team_abbrev, org in custom.items():
            normalized = normalize_org_abbrev(str(org))
            if normalized:
                self.organization_links[str(team_abbrev).upper()] = normalized

    def _save_organization_link(self) -> None:
        if self.workspace is None:
            return
        team_abbrev = self._team_abbrev_from_display(self.org_team_var.get())
        org = normalize_org_abbrev(self.org_parent_var.get())
        if not team_abbrev or not org:
            self._set_status("Choose a team and parent organization first.")
            return
        custom = load_json_state(self.workspace, "organization_links.json", {})
        custom[team_abbrev.upper()] = org
        save_json_state(self.workspace, "organization_links.json", custom)
        self.organization_links[team_abbrev.upper()] = org
        self._log_action("link-organization", {"team": team_abbrev.upper(), "organization": org})
        self._set_status(f"Linked {team_abbrev.upper()} to {org}. Re-scan CapWages to update proposals.")
        self._refresh_review()

    def _load_expansion_destination(self) -> None:
        if self.workspace is None:
            self.expansion_destination_var.set(EXPANSION_DESTINATION_TEAMS)
            return
        saved = load_json_state(
            self.workspace,
            "expansion_settings.json",
            {"destination": EXPANSION_DESTINATION_TEAMS},
        )
        destination = str(saved.get("destination") or EXPANSION_DESTINATION_TEAMS)
        if destination not in {EXPANSION_DESTINATION_TEAMS, EXPANSION_DESTINATION_FREE_AGENCY}:
            destination = EXPANSION_DESTINATION_TEAMS
        self.expansion_destination_var.set(destination)

    def _save_expansion_destination(self) -> None:
        if self.workspace is None:
            return
        destination = self.expansion_destination_var.get()
        if destination not in {EXPANSION_DESTINATION_TEAMS, EXPANSION_DESTINATION_FREE_AGENCY}:
            destination = EXPANSION_DESTINATION_TEAMS
            self.expansion_destination_var.set(destination)
        save_json_state(self.workspace, "expansion_settings.json", {"destination": destination})
        label = "expansion teams" if destination == EXPANSION_DESTINATION_TEAMS else "free agency"
        self._set_status(f"Expansion auto-update destination set to {label}. Re-scan CapWages to rebuild proposals.")

    def _apply_selected_update_moves(self) -> None:
        self._apply_update_moves(apply_all=False)

    def _apply_update_moves(self, *, apply_all: bool, indices_override: list[int] | None = None) -> None:
        if self.workspace is None:
            return
        original_moves = self.update_queue.get("moves", [])
        requested_tokens: set[str] | None = None
        if indices_override is not None:
            requested_tokens = {
                self._update_move_token(original_moves[index])
                for index in indices_override
                if 0 <= index < len(original_moves)
            }
        elif not apply_all:
            requested_tokens = {
                self._update_move_token(original_moves[int(item.split("-", 1)[1])])
                for item in self.update_tree.selection()
                if item.startswith("move-") and 0 <= int(item.split("-", 1)[1]) < len(original_moves)
            }

        filtered_queue = filter_redundant_organization_moves(
            self.update_queue,
            self.player_index,
            self.organization_links,
        )
        if filtered_queue != self.update_queue:
            self.update_queue = filtered_queue
            save_json_state(self.workspace, "update_queue.json", self.update_queue)
            self._render_update_queue()
            self._refresh_review()
        moves = self.update_queue.get("moves", [])
        if requested_tokens is not None:
            indices = [
                index
                for index, row in enumerate(moves)
                if self._update_move_token(row) in requested_tokens
            ]
        elif apply_all:
            indices = self._pending_update_indices()
        else:
            indices = []
        if not indices:
            self._set_status("No update moves selected.")
            return
        errors: list[str] = []
        prepared: list[tuple[str, str, str] | tuple[str, str, str, int]] = []
        free_agency_moves: list[tuple[str, str, int | None, str]] = []
        tokens: list[str] = []
        for index in indices:
            row = moves[index]
            token = self._update_move_token(row)
            if token in self.update_vetoes or token in self.update_applied:
                continue
            try:
                first, last = _split_name(str(row["player_name"]))
                player_id = _safe_int(row.get("player_id"), -1)
                target = str(row["to_team"])
                if target == FREE_AGENCY_TARGET:
                    free_agency_moves.append((first, last, player_id if player_id >= 0 else None, token))
                else:
                    prepared.append((first, last, target, player_id) if player_id >= 0 else (first, last, target))
                    tokens.append(token)
            except Exception as exc:
                errors.append(f"{row.get('player_name')}: {exc}")

        def worker():
            worker_errors = list(errors)
            applied_pairs: list[tuple[str, dict[str, object]]] = []
            if prepared:
                try:
                    batch_results = move_players_to_teams(
                        self.workspace.working_db,
                        prepared,
                        snapshot_cache=self.player_snapshot_cache,
                        cached_team_by_code=self.team_by_code,
                    )
                    applied_pairs.extend(zip(tokens, batch_results))
                except Exception as exc:
                    worker_errors.append(str(exc))
            if free_agency_moves:
                try:
                    batch_results = move_players_to_free_agency(
                        self.workspace.working_db,
                        [
                            (first, last, player_id) if player_id is not None else (first, last)
                            for first, last, player_id, _token in free_agency_moves
                        ],
                        snapshot_cache=self.player_snapshot_cache,
                        cached_team_by_code=self.team_by_code,
                    )
                    applied_pairs.extend(
                        (token, result)
                        for (_first, _last, _player_id, token), result in zip(free_agency_moves, batch_results)
                    )
                except Exception as exc:
                    worker_errors.append(str(exc))
            if applied_pairs:
                sync_working_db_to_roster(self.workspace)
            return applied_pairs, worker_errors

        def success(payload):
            applied_pairs, worker_errors = payload
            results = [result for _token, result in applied_pairs]
            for token, result in applied_pairs:
                self.update_applied.add(token)
                self._log_action("auto-update-move", result)
            self._save_update_applied()
            self._reload_after_player_write(
                f"Applied {len(results)} CapWages roster moves.",
                results=results,
            )
            if worker_errors:
                messagebox.showwarning("Some moves failed", "\n".join(worker_errors[:8]))

        self._run_background("Applying CapWages roster moves", worker, success)

    def _build_comparison(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        source_entries = self._comparison_source_entries()
        if not source_entries:
            self._set_status("Add at least one comparable player.")
            return
        sources = [entry.full_name for entry in source_entries]
        target = self.create_name_var.get().strip() or self.selected_player.full_name
        try:
            source_values: list[dict[str, int]] = []
            for source_entry in source_entries:
                source_name = source_entry.full_name
                first, last = source_entry.first_name, source_entry.last_name
                if self.player_snapshot_cache is not None:
                    snapshot = self.player_snapshot_cache.get_player_snapshot(first, last, source_entry.player_id)
                else:
                    snapshot = get_player_snapshot(self.workspace.working_db, first, last, source_entry.player_id)
                if snapshot is None or snapshot.ratings_row is None:
                    raise RuntimeError(f"Skater ratings row not found for comparison player: {source_name}")
                semantic: dict[str, int] = {}
                for spec in SKATER_ATTRIBUTE_SPECS:
                    mapped = LABEL_TO_SEMANTIC.get(spec.label)
                    if mapped:
                        semantic[mapped] = raw_to_display(spec, _safe_int(snapshot.ratings_row.get(spec.field), 0))
                source_values.append(semantic)
            all_semantics = sorted({key for row in source_values for key in row})
            blended = {
                key: round(sum(row.get(key, 75) for row in source_values) / len(source_values))
                for key in all_semantics
            }
            plan = fit_ratings_to_overall(
                blended,
                self.compare_archetype_var.get(),
                int(self.compare_target_overall_var.get()),
            )
            self.comparison_blend_values = plan.suggested_ratings
            result = {
                "target_name": target,
                "source_players": sources,
                "archetype": plan.archetype,
                "target_overall": plan.target_overall,
                "estimated_overall_before_cap": plan.current_overall,
                "estimated_overall_after_cap": calculate_weighted_overall(plan.suggested_ratings, plan.archetype),
                "points_used": plan.points_used,
                "blended_ratings": blended,
                "upgraded_ratings": plan.suggested_ratings,
            }
            self.comparison_result_text.delete("1.0", "end")
            self.comparison_result_text.insert("1.0", json.dumps(result, indent=2))
            self._set_status(f"Built comparison blend for {target}.")
        except Exception as exc:
            self._show_error("Build comparison failed", exc)

    def _apply_comparison_to_sliders(self) -> None:
        if not self.comparison_blend_values:
            self._set_status("Build a comparison blend first.")
            return
        semantic_to_field = {
            semantic: spec.field
            for spec in SKATER_ATTRIBUTE_SPECS
            for semantic in [LABEL_TO_SEMANTIC.get(spec.label)]
            if semantic
        }
        applied = 0
        for semantic, value in self.comparison_blend_values.items():
            field = semantic_to_field.get(semantic)
            if field in self.attribute_vars:
                self.attribute_vars[field].set(_safe_int(value, self.attribute_vars[field].get()))
                applied += 1
        self._update_attribute_budget()
        self._set_status(f"Applied {applied} comparison values to sliders.")

    def _refresh_potential_tree(self) -> None:
        if not hasattr(self, "potential_tree") or self.player_snapshot_cache is None:
            return
        self._clear_tree(self.potential_tree)
        selected_league = self.potential_league_filter_var.get() or "All Leagues"
        team_text = self.potential_team_filter_var.get().strip()
        selected_team = None if team_text in {"", "All Teams"} else self._team_abbrev_from_display(team_text)
        search = self.potential_search_var.get().strip().lower()
        try:
            min_stars = float(self.potential_min_stars_var.get())
            max_stars = float(self.potential_max_stars_var.get())
        except ValueError:
            min_stars, max_stars = 0.5, 5.0
        if min_stars > max_stars:
            min_stars, max_stars = max_stars, min_stars
        self.potential_iid_to_entry: dict[str, PlayerListEntry] = {}
        seen_player_ids: set[int] = set()
        for entry in self.potential_sorted_players:
            if entry.player_id in seen_player_ids:
                continue
            seen_player_ids.add(entry.player_id)
            if selected_league != "All Leagues" and entry.league_name != selected_league:
                continue
            if selected_team and (entry.current_team_abbrev or "").upper() != selected_team.upper():
                continue
            if search and search not in entry.full_name.lower():
                continue
            snapshot = self.player_snapshot_cache.get_player_snapshot(entry.first_name, entry.last_name, entry.player_id)
            if snapshot is None:
                continue
            position = self._position_label_from_code(_safe_int(snapshot.bio.get("aljv"), -1))
            ratings = snapshot.goalie_ratings_row if position == "G" else snapshot.ratings_row
            if not ratings:
                continue
            stars = POTENTIAL_STAR_CODE_TO_STARS.get(_safe_int(ratings.get("AMoQ"), -1), "?")
            color = POTENTIAL_CODE_TO_ACCURACY.get(_safe_int(ratings.get("feBm"), -1), "Unknown")
            role = POTENTIAL_STAR_TO_ROLE.get(stars, f"{stars} Stars" if stars != "?" else "Unknown")
            pending = self.potential_pending_updates.get(entry.player_id)
            if pending is not None:
                stars = str(pending.get("stars") or stars)
                color = str(pending.get("accuracy") or color)
                role = str(pending.get("role") or role)
            try:
                if not min_stars <= float(stars) <= max_stars:
                    continue
            except ValueError:
                continue
            iid = f"potential-{entry.player_id}"
            suffix = 1
            while self.potential_tree.exists(iid):
                suffix += 1
                iid = f"potential-{entry.player_id}-{suffix}"
            self.potential_tree.insert(
                "",
                "end",
                iid=iid,
                text=entry.full_name,
                values=(
                    entry.league_name,
                    entry.current_team_abbrev or ("Hidden" if entry.is_hidden else "FA"),
                    entry.organization_abbrev or "",
                    position,
                    stars,
                    color.split(" / ", 1)[0],
                    role,
                    "Pending" if pending is not None else "",
                ),
            )
            self.potential_iid_to_entry[iid] = entry

    def _on_potential_selected(self, _event=None) -> None:
        if not hasattr(self, "potential_iid_to_entry"):
            return
        selection = self.potential_tree.selection()
        if not selection:
            return
        entry = self.potential_iid_to_entry.get(selection[0])
        if entry is not None:
            self._load_player(entry)
            pending = self.potential_pending_updates.get(entry.player_id)
            if pending is not None:
                self.potential_role_var.set(str(pending.get("role") or self.potential_role_var.get()))
                self.potential_stars_var.set(str(pending.get("stars") or self.potential_stars_var.get()))
                self.potential_accuracy_var.set(str(pending.get("accuracy") or self.potential_accuracy_var.get()))

    def _sort_potential_tree(self, column: str, reverse: bool) -> None:
        if not hasattr(self, "potential_tree"):
            return
        rows = []
        for item in self.potential_tree.get_children(""):
            value = self.potential_tree.item(item, "text") if column == "#0" else self.potential_tree.set(item, column)
            if column == "stars":
                try:
                    sort_value: object = float(value)
                except (TypeError, ValueError):
                    sort_value = -1.0
            else:
                sort_value = str(value).lower()
            rows.append((sort_value, item))
        rows.sort(reverse=reverse)
        for index, (_value, item) in enumerate(rows):
            self.potential_tree.move(item, "", index)
        label = self.potential_tree.heading(column, "text")
        self.potential_tree.heading(
            column,
            text=label,
            command=lambda key=column: self._sort_potential_tree(key, not reverse),
        )

    def _stage_selected_potential(self, _event=None) -> None:
        star_value = self.potential_stars_var.get()
        if star_value not in POTENTIAL_STARS_TO_CODE:
            self._set_status("Choose a valid potential star value first.")
            return
        accuracy_label = self.potential_accuracy_var.get()
        selected_entries = [
            self.potential_iid_to_entry[iid]
            for iid in self.potential_tree.selection()
            if iid in getattr(self, "potential_iid_to_entry", {})
        ] if hasattr(self, "potential_tree") else []
        if not selected_entries and self.selected_player is not None:
            selected_entries = [self.selected_player]
        self._stage_potential_entries(selected_entries, stars=star_value, accuracy=accuracy_label)

    def _stage_potential_entries(
        self,
        entries: list[PlayerListEntry],
        *,
        stars: str | None = None,
        accuracy: str | None = None,
    ) -> None:
        if self.player_snapshot_cache is None:
            return
        staged_count = 0
        for entry in entries:
            snapshot = self.player_snapshot_cache.get_player_snapshot(entry.first_name, entry.last_name, entry.player_id)
            if snapshot is None:
                continue
            position = self._position_label_from_code(_safe_int(snapshot.bio.get("aljv"), -1))
            ratings = snapshot.goalie_ratings_row if position == "G" else snapshot.ratings_row
            if ratings is None:
                continue
            current_stars = POTENTIAL_STAR_CODE_TO_STARS.get(_safe_int(ratings.get("AMoQ"), -1), "3.0")
            current_accuracy = POTENTIAL_CODE_TO_ACCURACY.get(_safe_int(ratings.get("feBm"), -1), "Medium / Yellow")
            star_value = stars or current_stars
            accuracy_label = accuracy or current_accuracy
            star_code = POTENTIAL_STARS_TO_CODE.get(star_value)
            if star_code is None:
                continue
            updates: dict[str, int] = {"AMoQ": star_code}
            accuracy_code = POTENTIAL_ACCURACY_TO_CODE.get(accuracy_label)
            if accuracy_code is not None:
                updates["feBm"] = accuracy_code
            role = POTENTIAL_STAR_TO_ROLE.get(star_value, f"{star_value} Stars")
            self.potential_pending_updates[entry.player_id] = {
                "first_name": entry.first_name,
                "last_name": entry.last_name,
                "player_id": entry.player_id,
                "goalie": position == "G",
                "updates": updates,
                "role": role,
                "stars": star_value,
                "accuracy": accuracy_label,
            }
            staged_count += 1
        self._update_pending_potential_button()
        for iid, visible_entry in getattr(self, "potential_iid_to_entry", {}).items():
            pending = self.potential_pending_updates.get(visible_entry.player_id)
            if pending is None:
                continue
            values = list(self.potential_tree.item(iid, "values"))
            if len(values) >= 8:
                values[4] = pending["stars"]
                values[5] = str(pending["accuracy"]).split(" / ", 1)[0]
                values[6] = pending["role"]
                values[7] = "Pending"
                self.potential_tree.item(iid, values=values)
        self._set_status(
            f"Staged {staged_count} potential edit(s). {len(self.potential_pending_updates)} player(s) pending."
        )

    def _select_all_potentials(self):
        if not hasattr(self, "potential_tree"):
            return "break"
        self.potential_tree.selection_set(self.potential_tree.get_children())
        self._set_status(f"Selected {len(self.potential_tree.selection())} visible player(s).")
        return "break"

    def _show_potential_context_menu(self, event) -> None:
        iid = self.potential_tree.identify_row(event.y)
        if iid and iid not in self.potential_tree.selection():
            self.potential_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        star_menu = tk.Menu(menu, tearoff=0)
        for star_value in reversed(POTENTIAL_STARS):
            star_menu.add_command(
                label=f"{star_value} Stars",
                command=lambda value=star_value: self._stage_potential_entries(
                    [self.potential_iid_to_entry[item] for item in self.potential_tree.selection() if item in self.potential_iid_to_entry],
                    stars=value,
                ),
            )
        color_menu = tk.Menu(menu, tearoff=0)
        for accuracy_label in POTENTIAL_ACCURACY:
            color_menu.add_command(
                label=accuracy_label,
                command=lambda value=accuracy_label: self._stage_potential_entries(
                    [self.potential_iid_to_entry[item] for item in self.potential_tree.selection() if item in self.potential_iid_to_entry],
                    accuracy=value,
                ),
            )
        menu.add_cascade(label="Set Stars For Selected", menu=star_menu)
        menu.add_cascade(label="Set Color For Selected", menu=color_menu)
        menu.add_separator()
        menu.add_command(label="Save All Pending", command=self._save_all_pending_potentials)
        menu.tk_popup(event.x_root, event.y_root)

    def _update_pending_potential_button(self) -> None:
        if hasattr(self, "save_pending_potentials_button"):
            self.save_pending_potentials_button.configure(
                text=f"Save All Pending ({len(self.potential_pending_updates)})"
            )

    def _save_all_pending_potentials(self) -> None:
        if self.workspace is None:
            return
        pending = [dict(row) for row in self.potential_pending_updates.values()]
        if not pending:
            self._set_status("No pending potential edits to save.")
            return

        def worker():
            skaters = [
                (row["first_name"], row["last_name"], row["updates"], row["player_id"])
                for row in pending
                if not row.get("goalie")
            ]
            goalies = [
                (row["first_name"], row["last_name"], row["updates"], row["player_id"])
                for row in pending
                if row.get("goalie")
            ]
            results: list[dict[str, object]] = []
            errors: list[str] = []
            if skaters:
                try:
                    results.extend(update_many_player_ratings(
                        self.workspace.working_db,
                        skaters,
                        snapshot_cache=self.player_snapshot_cache,
                    ))
                except Exception as exc:
                    errors.append(f"Skater potentials: {exc}")
            if goalies:
                try:
                    results.extend(update_many_player_goalie_ratings(
                        self.workspace.working_db,
                        goalies,
                        snapshot_cache=self.player_snapshot_cache,
                    ))
                except Exception as exc:
                    errors.append(f"Goalie potentials: {exc}")
            if results:
                sync_working_db_to_roster(self.workspace)
            return results, errors

        def success(payload):
            results, errors = payload
            saved_ids: set[int] = set()
            logged_results: list[dict[str, object]] = []
            for result in results:
                player_id = _safe_int(result.get("player_id"), -1)
                staged = self.potential_pending_updates.get(player_id)
                if staged is None:
                    continue
                result["potential_display"] = {
                    "role": staged.get("role"),
                    "stars": staged.get("stars"),
                    "accuracy": staged.get("accuracy"),
                    "exact_silver_note": "Exact/Silver shares the high-accuracy roster code in this save; the game derives silver display from player/development state.",
                }
                logged_results.append(result)
                saved_ids.add(player_id)
                if self.player_snapshot_cache is not None:
                    snapshot = self.player_snapshot_cache.get_player_snapshot(
                        str(staged.get("first_name") or ""),
                        str(staged.get("last_name") or ""),
                        player_id,
                    )
                    if snapshot is not None:
                        ratings = snapshot.goalie_ratings_row if staged.get("goalie") else snapshot.ratings_row
                        if ratings is not None:
                            ratings.update(staged.get("updates") or {})
            self._log_actions("update-potential", logged_results)
            for player_id in saved_ids:
                self.potential_pending_updates.pop(player_id, None)
            self._update_pending_potential_button()
            self._refresh_potential_tree()
            self._refresh_review()
            self._set_status(
                f"Saved {len(saved_ids)} potential edit(s); "
                f"{len(self.potential_pending_updates)} remain pending."
            )
            if errors:
                messagebox.showwarning("Some potentials were not saved", "\n".join(errors))

        self._run_background("Saving pending potentials", worker, success)

    def _save_potential_to_roster(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        star_code = POTENTIAL_STARS_TO_CODE.get(self.potential_stars_var.get())
        if star_code is None:
            self._set_status("Choose a valid potential star value first.")
            return
        updates: dict[str, int] = {"AMoQ": star_code}
        accuracy_label = self.potential_accuracy_var.get()
        accuracy_code = POTENTIAL_ACCURACY_TO_CODE.get(accuracy_label)
        if accuracy_code is not None:
            updates["feBm"] = accuracy_code
        try:
            result = self._update_selected_player_ratings(updates)
            result["potential_display"] = {
                "role": self.potential_role_var.get(),
                "stars": self.potential_stars_var.get(),
                "accuracy": accuracy_label,
                "exact_silver_note": "Exact/Silver shares the high-accuracy roster code in this save; the game appears to derive silver display from player/development state.",
            }
            self._log_action("update-potential", result)
            sync_working_db_to_roster(self.workspace)
            ratings_row = self._ratings_row_for_selected_player()
            if ratings_row is not None:
                ratings_row.update(updates)
            self._load_potential_note()
            self._refresh_potential_tree()
            self._refresh_review()
            self._set_status("Saved potential to roster.")
        except Exception as exc:
            self._show_error("Save potential failed", exc)

    def _load_potential_note(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        ratings = self._ratings_row_for_selected_player() if self.snapshot else None
        if ratings:
            star_value = POTENTIAL_STAR_CODE_TO_STARS.get(_safe_int(ratings.get("AMoQ"), -1))
            if star_value:
                self.potential_stars_var.set(star_value)
                self.potential_role_var.set(POTENTIAL_STAR_TO_ROLE.get(star_value, f"{star_value} Stars"))
            accuracy = POTENTIAL_CODE_TO_ACCURACY.get(_safe_int(ratings.get("feBm"), -1))
            if accuracy:
                self.potential_accuracy_var.set(accuracy)
            return
        notes = load_json_state(self.workspace, "player_notes.json", {})
        note = notes.get(str(self.selected_player.player_id))
        if not note:
            return
        self.potential_role_var.set(str(note.get("potential_role") or "3.0 Stars (Bottom 6 Forward / 7th D / Backup)"))
        self.potential_stars_var.set(str(note.get("potential_stars") or "3.0"))
        self.potential_accuracy_var.set(str(note.get("potential_accuracy") or "Medium / Yellow"))

    def _sync_potential_stars_from_role(self) -> None:
        stars = POTENTIAL_ROLE_TO_STARS.get(self.potential_role_var.get())
        if stars:
            self.potential_stars_var.set(stars)

    def _refresh_review(self) -> None:
        if self.workspace is None:
            return
        self._clear_tree(self.review_tree)
        self._load_update_vetoes()
        self._load_update_applied()
        self.update_errors = {
            str(key): str(value)
            for key, value in load_json_state(self.workspace, "update_errors.json", {}).items()
        }
        self.update_queue = load_json_state(self.workspace, "update_queue.json", {"moves": [], "create_candidates": []})
        for index, move in enumerate(self.update_queue.get("moves", [])):
            token = self._update_move_token(move)
            if token in self.update_vetoes or token in self.update_applied:
                continue
            error = self.update_errors.get(token)
            detail = f"{move.get('from_team') or 'None'} -> {move.get('to_team')} | {move.get('reason') or ''}"
            change_type = "pending-auto-update"
            if error:
                change_type = "pending-error"
                detail = f"{detail} | ERROR: {error}"
            self.review_tree.insert(
                "",
                "end",
                iid=f"pending-{index}",
                text=str(move.get("player_name") or ""),
                values=("", change_type, detail),
            )
        for index, entry in enumerate(read_change_log(self.workspace)):
            result = entry.get("result", {}) if isinstance(entry, dict) else {}
            player = ""
            if isinstance(result, dict):
                player = str(result.get("player") or result.get("player_name") or "")
                if not player and "bio" in result and isinstance(result["bio"], dict):
                    player = str(result["bio"].get("player") or "")
            details = self._review_details(result)
            self.review_tree.insert(
                "",
                "end",
                iid=f"review-{index}",
                text=player or "Workspace",
                values=(str(entry.get("timestamp", ""))[:19], entry.get("type", ""), details),
            )

    def _review_details(self, result: object) -> str:
        if not isinstance(result, dict):
            return str(result)
        if "changes" in result and isinstance(result["changes"], list):
            return f"{len(result['changes'])} changed fields"
        if "updated_fields" in result:
            return ", ".join(sorted(str(key) for key in result["updated_fields"].keys()))
        if "count" in result:
            return f"{result.get('count')} items"
        if "from_team" in result and "to_team" in result:
            return f"{result.get('from_team')} -> {result.get('to_team')}"
        return json.dumps(result, default=str)[:500]

    def _selected_review_pending_indices(self) -> list[int]:
        indices: list[int] = []
        for item in self.review_tree.selection():
            if not item.startswith("pending-"):
                continue
            try:
                indices.append(int(item.split("-", 1)[1]))
            except ValueError:
                continue
        return indices

    def _selected_review_log_indices(self) -> list[int]:
        indices: list[int] = []
        for item in self.review_tree.selection():
            if not item.startswith("review-"):
                continue
            try:
                indices.append(int(item.split("-", 1)[1]))
            except ValueError:
                continue
        return indices

    def _apply_selected_review_move(self) -> None:
        if self.workspace is None:
            return
        moves = self.update_queue.get("moves", [])
        indices = self._selected_review_pending_indices()
        if not indices:
            self._set_status("Select a pending auto-update move in Final Review first.")
            return
        self._apply_update_moves(apply_all=False, indices_override=indices)

    def _veto_selected_review_move(self) -> None:
        if self.workspace is None:
            return
        moves = self.update_queue.get("moves", [])
        indices = self._selected_review_pending_indices()
        if not indices:
            self._set_status("Select a pending auto-update move in Final Review first.")
            return
        for index in indices:
            if 0 <= index < len(moves):
                self.update_vetoes.add(self._update_move_token(moves[index]))
                self._log_action("veto-auto-update-move", moves[index])
        self._save_update_vetoes()
        self._render_update_queue()
        self._refresh_review()
        self._set_status(f"Vetoed {len(indices)} pending move(s) from Final Review.")

    def _revert_selected_review_change(self) -> None:
        if self.workspace is None:
            return
        entries = read_change_log(self.workspace)
        indices = self._selected_review_log_indices()
        if not indices:
            self._set_status("Select a logged edit in Final Review first.")
            return
        reverted = 0
        errors: list[str] = []
        for index in indices:
            if not (0 <= index < len(entries)):
                continue
            entry = entries[index]
            if not isinstance(entry, dict):
                continue
            try:
                self._revert_review_entry(entry)
                reverted += 1
            except Exception as exc:
                errors.append(f"{entry.get('type', 'change')}: {exc}")
        if reverted:
            sync_working_db_to_roster(self.workspace)
            self._reload_after_player_write(f"Reverted {reverted} logged edit(s).")
        if errors:
            messagebox.showwarning("Some reverts failed", "\n".join(errors[:8]))

    def _revert_review_entry(self, entry: dict[str, object]) -> None:
        change_type = str(entry.get("type") or "")
        result = entry.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Review entry has no structured result to revert.")
        if change_type in {"save-to-game", "veto-auto-update-move", "revert-change"}:
            raise RuntimeError(f"{change_type} is not revertible.")

        if change_type in {"move-player", "move-to-free-agency", "auto-update-move"}:
            player = str(result.get("player") or "")
            from_team = result.get("from_team")
            if not player or not from_team:
                raise RuntimeError("Move entry does not include a previous team.")
            first, last = _split_name(player)
            revert_result = move_player_to_team(
                self.workspace.working_db,
                first,
                last,
                str(from_team),
                _safe_int(result.get("player_id"), -1) if result.get("player_id") is not None else None,
            )
            self._log_action("revert-change", {"reverted_type": change_type, "original": result, "result": revert_result})
            return

        if change_type in {"update-attributes", "update-potential"}:
            player = str(result.get("player") or "")
            if not player:
                raise RuntimeError("Rating entry does not include a player.")
            first, last = _split_name(player)
            player_id = _safe_int(result.get("player_id"), -1) if result.get("player_id") is not None else None
            rating_updates = {
                str(change.get("field")): int(change.get("before"))
                for change in result.get("changes", [])
                if isinstance(change, dict)
                and change.get("section") in {"ratings", "goalie_ratings"}
                and change.get("before") is not None
            }
            if not rating_updates:
                raise RuntimeError("No rating fields available to revert.")
            if any(change.get("section") == "goalie_ratings" for change in result.get("changes", []) if isinstance(change, dict)):
                revert_result = update_player_goalie_ratings(self.workspace.working_db, first, last, rating_updates, player_id)
            else:
                revert_result = update_player_ratings(self.workspace.working_db, first, last, rating_updates, player_id)
            self._log_action("revert-change", {"reverted_type": change_type, "original": result, "result": revert_result})
            return

        if change_type == "update-contract":
            player = str(result.get("player") or "")
            first, last = _split_name(player)
            player_id = _safe_int(result.get("player_id"), -1) if result.get("player_id") is not None else None
            updates = {
                str(change.get("field")): change.get("before")
                for change in result.get("changes", [])
                if isinstance(change, dict)
                and change.get("section") == "bio"
                and change.get("before") is not None
            }
            if not updates:
                raise RuntimeError("No contract fields available to revert.")
            revert_result = update_player_bio(
                self.workspace.working_db,
                first,
                last,
                updates,
                player_id,
            )
            self._log_action("revert-change", {"reverted_type": change_type, "original": result, "result": revert_result})
            return

        if change_type == "bulk-organization-attributes":
            nested = result.get("result")
            if not isinstance(nested, dict):
                raise RuntimeError("Bulk attribute entry has no nested rating result.")
            nested_entry = {"type": "update-attributes", "result": nested}
            self._revert_review_entry(nested_entry)
            return

        if change_type == "update-player-info":
            reverted_parts: dict[str, object] = {}
            bio = result.get("bio")
            if isinstance(bio, dict):
                player = str(bio.get("player") or "")
                first, last = _split_name(player)
                updates = {
                    str(change.get("field")): change.get("before")
                    for change in bio.get("changes", [])
                    if isinstance(change, dict) and change.get("before") is not None
                }
                if updates:
                    reverted_parts["bio"] = update_player_bio(self.workspace.working_db, first, last, updates)
            ratings = result.get("ratings")
            if isinstance(ratings, dict) and ratings.get("changes"):
                player = str(ratings.get("player") or "")
                first, last = _split_name(player)
                updates = {
                    str(change.get("field")): int(change.get("before"))
                    for change in ratings.get("changes", [])
                    if isinstance(change, dict) and change.get("before") is not None
                }
                if updates:
                    reverted_parts["ratings"] = update_player_ratings(self.workspace.working_db, first, last, updates)
            instance = result.get("instance")
            if isinstance(instance, dict) and instance.get("changes"):
                player = str(instance.get("player") or "")
                first, last = _split_name(player)
                updates = {
                    str(change.get("field")): change.get("before")
                    for change in instance.get("changes", [])
                    if isinstance(change, dict) and change.get("before") is not None
                }
                if updates:
                    reverted_parts["instance"] = update_player_instance_fields(self.workspace.working_db, first, last, updates)
            if not reverted_parts:
                raise RuntimeError("No player info fields available to revert.")
            self._log_action("revert-change", {"reverted_type": change_type, "original": result, "result": reverted_parts})
            return

        raise RuntimeError(f"{change_type} is not revertible yet.")

    def _sync_roster(self) -> None:
        if self.workspace is None:
            return
        try:
            path = sync_working_db_to_roster(self.workspace)
            self._set_status(f"Synced working roster payload: {path}")
        except Exception as exc:
            self._show_error("Sync failed", exc)

    def _queued_move_player(self, row: dict[str, object]) -> PlayerListEntry | None:
        player_id = _safe_int(row.get("player_id"), -1)
        if player_id >= 0 and player_id in self.player_by_id:
            return self.player_by_id[player_id]
        wanted = equivalent_name_key(str(row.get("player_name") or ""))
        matches = [
            player
            for player in self.player_index
            if equivalent_name_key(player.full_name) == wanted
        ]
        unique = {player.player_id: player for player in matches}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def _apply_pending_update_moves_for_save(self) -> tuple[int, list[str], list[dict[str, object]]]:
        if self.workspace is None:
            return 0, [], []
        self._load_update_vetoes()
        self._load_update_applied()
        loaded_queue = load_json_state(self.workspace, "update_queue.json", {"moves": [], "create_candidates": []})
        self.update_queue = filter_redundant_organization_moves(
            loaded_queue,
            self.player_index,
            self.organization_links,
        )
        if self.update_queue != loaded_queue:
            save_json_state(self.workspace, "update_queue.json", self.update_queue)
        moves = self.update_queue.get("moves", [])
        team_moves: list[tuple[str, str, str, int | None, str, dict[str, object]]] = []
        free_agent_moves: list[tuple[str, str, int | None, str, dict[str, object]]] = []
        errors: list[str] = []
        for index in self._pending_update_indices():
            row = moves[index]
            token = self._update_move_token(row)
            try:
                first, last = _split_name(str(row["player_name"]))
                player_id = _safe_int(row.get("player_id"), -1)
                target = str(row["to_team"])
                if not can_auto_apply_move_on_save(row):
                    message = "Move has no resolved destination."
                    self.update_errors[token] = message
                    errors.append(f"{row.get('player_name')}: {message}")
                    continue
                matched_player = self._queued_move_player(row)
                if matched_player is not None and move_is_already_satisfied(
                    row,
                    matched_player,
                    self.organization_links,
                ):
                    self.update_applied.add(token)
                    self.update_errors.pop(token, None)
                    continue
                resolved_player_id = player_id if player_id >= 0 else None
                if target == FREE_AGENCY_TARGET:
                    free_agent_moves.append((first, last, resolved_player_id, token, row))
                else:
                    team_moves.append((first, last, target, resolved_player_id, token, row))
            except Exception as exc:
                message = str(exc)
                self.update_errors[token] = message
                errors.append(f"{row.get('player_name')}: {message}")
        results: list[dict[str, object]] = []

        def record_success(token: str, result: dict[str, object]) -> None:
            self.update_applied.add(token)
            self.update_errors.pop(token, None)
            self._log_action("auto-update-move", result)
            results.append(result)

        if team_moves:
            try:
                batch_results = move_players_to_teams(
                    self.workspace.working_db,
                    [
                        (first, last, target, player_id) if player_id is not None else (first, last, target)
                        for first, last, target, player_id, _token, _row in team_moves
                    ],
                    snapshot_cache=self.player_snapshot_cache,
                    cached_team_by_code=self.team_by_code,
                )
                if len(batch_results) != len(team_moves):
                    raise RuntimeError("Team move batch returned an incomplete result set.")
                for (_first, _last, _target, _player_id, token, _row), result in zip(team_moves, batch_results):
                    record_success(token, result)
            except Exception:
                # Retry one-by-one so one malformed/hidden/unknown move cannot block
                # every valid transaction in the save.
                for first, last, target, player_id, token, row in team_moves:
                    try:
                        result = move_players_to_teams(
                            self.workspace.working_db,
                            [(first, last, target, player_id) if player_id is not None else (first, last, target)],
                            snapshot_cache=self.player_snapshot_cache,
                            cached_team_by_code=self.team_by_code,
                        )[0]
                        record_success(token, result)
                    except Exception as exc:
                        message = str(exc)
                        self.update_errors[token] = message
                        errors.append(f"{row.get('player_name')}: {message}")

        if free_agent_moves:
            try:
                batch_results = move_players_to_free_agency(
                    self.workspace.working_db,
                    [
                        (first, last, player_id) if player_id is not None else (first, last)
                        for first, last, player_id, _token, _row in free_agent_moves
                    ],
                    snapshot_cache=self.player_snapshot_cache,
                    cached_team_by_code=self.team_by_code,
                )
                if len(batch_results) != len(free_agent_moves):
                    raise RuntimeError("Free-agent move batch returned an incomplete result set.")
                for (_first, _last, _player_id, token, _row), result in zip(free_agent_moves, batch_results):
                    record_success(token, result)
            except Exception:
                for first, last, player_id, token, row in free_agent_moves:
                    try:
                        result = move_players_to_free_agency(
                            self.workspace.working_db,
                            [(first, last, player_id) if player_id is not None else (first, last)],
                            snapshot_cache=self.player_snapshot_cache,
                            cached_team_by_code=self.team_by_code,
                        )[0]
                        record_success(token, result)
                    except Exception as exc:
                        message = str(exc)
                        self.update_errors[token] = message
                        errors.append(f"{row.get('player_name')}: {message}")
        self._save_update_applied()
        self._save_update_errors()
        return len(results), errors, results

    def _prune_committed_update_queue(self) -> None:
        if self.workspace is None:
            return
        kept_moves = []
        kept_tokens: set[str] = set()
        for row in self.update_queue.get("moves", []):
            token = self._update_move_token(row)
            if token in self.update_applied or token in self.update_vetoes:
                continue
            kept_moves.append(row)
            kept_tokens.add(token)
        self.update_queue["moves"] = kept_moves
        self.update_applied = set()
        self.update_vetoes = set()
        self.update_errors = {
            token: message
            for token, message in self.update_errors.items()
            if token in kept_tokens
        }
        save_json_state(self.workspace, "update_queue.json", self.update_queue)
        self._save_update_applied()
        self._save_update_vetoes()
        self._save_update_errors()

    def _guess_game_save_target(self) -> Path | None:
        if self.workspace is None:
            return None
        source = self.workspace.source_roster
        if source and source.exists():
            return source
        roster_name = self.workspace.name.split("-", 2)[-1].replace("_", " ")
        roots = [
            Path(r"D:\Emulation\xenia_manager\Emulators\Xenia Canary\content"),
        ]
        for root in roots:
            if not root.exists():
                continue
            matches = list(root.rglob(roster_name))
            for match in matches:
                if match.is_file():
                    return match
                candidate = match / roster_name
                if candidate.exists():
                    return candidate
        return None

    def _set_game_save_target(self) -> None:
        if self.workspace is None:
            return
        initial = self._guess_game_save_target()
        path = filedialog.askopenfilename(
            title="Choose the Xenia roster save file to overwrite when saving",
            initialdir=str(initial.parent if initial else Path(r"D:\Emulation\xenia_manager\Emulators\Xenia Canary\content")),
            filetypes=[("NHL roster saves", "*"), ("All files", "*.*")],
        )
        if not path:
            return
        self.workspace.source_roster_path = path
        save_active_workspace(self.workspace)
        self._update_workspace_badge()
        self._set_status(f"Game save target set: {path}")

    def _save_to_game(self) -> None:
        if self.workspace is None:
            return
        target = self._guess_game_save_target()
        if target is None:
            self._set_game_save_target()
            target = self._guess_game_save_target()
        if target is None:
            self._set_status("No game save target set. Choose Set Game Save Target first.")
            return
        workspace = self.workspace

        def file_hash(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        def worker():
            applied_pending, pending_errors, pending_results = self._apply_pending_update_moves_for_save()
            working_roster = sync_working_db_to_roster(self.workspace)
            validation = validate_rosterfile(working_roster)
            if target.is_dir():
                raise RuntimeError(f"Save target is a folder, not the inner roster file: {target}")
            if target.exists():
                with target.open("rb") as stream:
                    header = stream.read(10)
                if not header.startswith(b"RosterFile"):
                    raise RuntimeError(
                        "Save target is not the inner NHL Legacy RosterFile. "
                        f"Choose the file inside the roster folder instead: {target}"
                    )
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = target.with_name(f"{target.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
                shutil.copy2(target, backup)
            temp = target.with_name(f"{target.name}.tmp-save-{datetime.now().strftime('%Y%m%d%H%M%S')}")
            if temp.exists():
                temp.unlink()
            try:
                shutil.copy2(working_roster, temp)
                validate_rosterfile(temp)
                os.replace(temp, target)
            finally:
                if temp.exists():
                    temp.unlink()
            validate_rosterfile(target)
            expected_hash = file_hash(working_roster)
            actual_hash = file_hash(target)
            if expected_hash != actual_hash:
                raise RuntimeError(
                    "Save verification failed: game target bytes do not match the rebuilt roster."
                )
            workspace.source_roster_path = str(target)
            save_active_workspace(workspace)
            self._log_action(
                "save-to-game",
                {
                    "target": str(target),
                    "working_roster": str(working_roster),
                    "sha256": actual_hash,
                    "validation": validation,
                    "pending_moves_applied": applied_pending,
                    "pending_move_errors": pending_errors,
                },
            )
            archive_and_clear_change_log(workspace)
            self._prune_committed_update_queue()
            return {
                "target": target,
                "applied_pending": applied_pending,
                "pending_errors": pending_errors,
                "pending_results": pending_results,
            }

        def success(payload):
            pending_results = payload["pending_results"]
            if pending_results:
                selected_id = None if self.selected_player is None else self.selected_player.player_id
                self._apply_write_results_to_cache(pending_results)
                self._refresh_player_index_from_cache()
                self._on_league_changed()
                self._refresh_trade_lanes()
                self._refresh_potential_tree()
                if selected_id is not None and selected_id in self.player_by_id:
                    self._load_player(self.player_by_id[selected_id])
            self._render_update_queue()
            self._refresh_review()
            applied_pending = payload["applied_pending"]
            pending_errors = payload["pending_errors"]
            suffix = f" Applied {applied_pending} pending move(s)." if applied_pending else ""
            error_suffix = f" {len(pending_errors)} move(s) remain pending with errors." if pending_errors else ""
            self._set_status(f"Saved and verified roster to game file: {target}.{suffix}{error_suffix}")
            if pending_errors:
                self.tabs.select(self.review_tab)

        self._run_background("Saving roster to game", worker, success)

    def _log_action(self, action_type: str, result: dict[str, object]) -> None:
        if self.workspace is None:
            return
        append_change_log(
            self.workspace,
            {
                "timestamp": datetime.now().isoformat(),
                "type": action_type,
                "result": result,
            },
        )

    def _log_actions(self, action_type: str, results: list[dict[str, object]]) -> None:
        if self.workspace is None or not results:
            return
        timestamp = datetime.now().isoformat()
        append_change_logs(
            self.workspace,
            [
                {
                    "timestamp": timestamp,
                    "type": action_type,
                    "result": result,
                }
                for result in results
            ],
        )

    def _run_background(self, label: str, worker, success) -> None:
        if self._background_task_running:
            self._set_status(
                f"{self._background_task_label} is still running. Please let it finish before starting {label.lower()}."
            )
            return
        self._background_task_running = True
        self._background_task_label = label
        self._set_status(f"{label}...")
        if hasattr(self, "activity_progress"):
            self.activity_progress.start(12)

        def finish() -> None:
            self._background_task_running = False
            self._background_task_label = ""
            if hasattr(self, "activity_progress"):
                self.activity_progress.stop()

        def show_error(error: Exception) -> None:
            finish()
            self._show_error(label, error)

        def show_success(value) -> None:
            finish()
            try:
                success(value)
            except Exception as exc:
                self._show_error(label, exc)

        def task():
            try:
                result = worker()
            except Exception as exc:
                self.root.after(0, lambda error=exc: show_error(error))
            else:
                self.root.after(0, lambda value=result: show_success(value))

        threading.Thread(target=task, daemon=True).start()

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _show_error(self, title: str, exc: Exception) -> None:
        self._set_status(f"{title}: {exc}")
        messagebox.showerror(title, str(exc))


def main() -> None:
    app = NhlLegacyDesktopApp()
    app.run()


if __name__ == "__main__":
    main()
