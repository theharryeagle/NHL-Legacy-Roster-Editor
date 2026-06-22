from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .attribute_map import SKATER_ATTRIBUTE_SPECS, attribute_specs_by_field, display_to_raw, raw_to_display
from .capwages import fetch_capwages_team_contracts
from .contract_models import DEFAULT_REAL_CAP_MILLIONS, scale_contract_by_cap_percentage
from .contract_sync import build_contract_update_queue
from .editor_state import load_json_state, save_json_state
from .hockeydb import fetch_hockeydb_profile_by_name
from .move_tools import move_player_to_team, move_player_to_team_code
from .nhl_remote import fetch_edge_skater_detail, fetch_player_landing, find_player_on_official_rosters
from .player_editing import update_player_bio, update_player_flags, update_player_instance_fields, update_player_ratings
from .player_tools import PlayerSnapshotCache, build_player_snapshot_cache, get_player_snapshot
from .rating_models import ARCHETYPE_WEIGHTS, calculate_weighted_overall, fit_ratings_to_overall, plan_rating_upgrade
from .roster_sync import build_capwages_roster_update, canonical_abbrev, normalize_name
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
    EditorWorkspace,
    append_change_log,
    create_workspace,
    load_active_workspace,
    read_change_log,
    save_active_workspace,
    sync_working_db_to_roster,
)


FREE_AGENCY_CODE = 255
FREE_AGENCY_LABEL = "Free Agency / Unassigned"
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
        self.root.title("NHL Legacy Roster Editor")
        self.root.geometry("1540x920")
        self.root.minsize(1240, 760)

        self.workspace: EditorWorkspace | None = load_active_workspace()
        self.player_index: list[PlayerListEntry] = []
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
        self.flags_vars: dict[str, tk.IntVar] = {}
        self.contract_queue: list[dict[str, object]] = []
        self.update_queue: dict[str, list[dict[str, object]]] = {"moves": [], "create_candidates": []}
        self.update_vetoes: set[str] = set()
        self.organization_links: dict[str, str] = default_organization_links()
        self.capwages_player = None
        self.official_player_hit = None

        self._configure_style()
        self._build_ui()
        if self.workspace is not None:
            self._reload_workspace()
        else:
            self._set_status("Open an NHL Legacy roster save to begin.")

    def run(self) -> None:
        self.root.mainloop()

    def _configure_style(self) -> None:
        self.colors = {
            "ice": "#e9f4ff",
            "ink": "#f2f6fb",
            "muted": "#8fa2b7",
            "panel": "#101821",
            "panel2": "#162332",
            "panel3": "#1d2c3e",
            "line": "#2d4258",
            "accent": "#f2b705",
            "blue": "#3aa0ff",
            "danger": "#f05d5e",
        }
        self.root.configure(background=self.colors["panel"])
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=self.colors["panel"], foreground=self.colors["ink"], fieldbackground=self.colors["panel2"])
        style.configure("TFrame", background=self.colors["panel"])
        style.configure("Card.TFrame", background=self.colors["panel2"], relief="flat")
        style.configure("TLabel", background=self.colors["panel"], foreground=self.colors["ink"])
        style.configure("Muted.TLabel", background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("Card.TLabel", background=self.colors["panel2"], foreground=self.colors["ink"])
        style.configure("Title.TLabel", background=self.colors["panel"], foreground=self.colors["ice"], font=("Segoe UI Semibold", 18))
        style.configure("Player.TLabel", background=self.colors["panel"], foreground=self.colors["ice"], font=("Segoe UI Semibold", 22))
        style.configure("Accent.TLabel", background=self.colors["panel"], foreground=self.colors["accent"], font=("Segoe UI Semibold", 10))
        style.configure("TButton", background=self.colors["panel3"], foreground=self.colors["ink"], bordercolor=self.colors["line"], focusthickness=0)
        style.map("TButton", background=[("active", "#263a50")], foreground=[("disabled", self.colors["muted"])])
        style.configure("Accent.TButton", background=self.colors["accent"], foreground="#0d141d")
        style.map("Accent.TButton", background=[("active", "#ffd45a")])
        style.configure("Danger.TButton", background=self.colors["danger"], foreground="#fff8f8")
        style.configure("TEntry", fieldbackground="#0b1118", foreground=self.colors["ink"], insertcolor=self.colors["ink"])
        style.configure("TCombobox", fieldbackground="#0b1118", background=self.colors["panel3"], foreground=self.colors["ink"], arrowcolor=self.colors["ink"])
        style.configure("Treeview", background="#0b1118", fieldbackground="#0b1118", foreground=self.colors["ink"], rowheight=26, bordercolor=self.colors["line"])
        style.configure("Treeview.Heading", background=self.colors["panel3"], foreground=self.colors["ice"], font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#244e76")], foreground=[("selected", "#ffffff")])
        style.configure("TNotebook", background=self.colors["panel"])
        style.configure("TNotebook.Tab", background=self.colors["panel2"], foreground=self.colors["muted"], padding=(14, 8), font=("Segoe UI Semibold", 10))
        style.map("TNotebook.Tab", background=[("selected", self.colors["panel3"])], foreground=[("selected", self.colors["ice"])])

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=16, pady=(14, 8))
        ttk.Label(top, text="NHL Legacy Roster Editor", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Open Roster Save", style="Accent.TButton", command=self._open_roster).pack(side="right", padx=(8, 0))
        ttk.Button(top, text="Reload", command=self._reload_workspace).pack(side="right", padx=(8, 0))
        ttk.Button(top, text="Set Game Save Target", command=self._set_game_save_target).pack(side="right", padx=(8, 0))
        ttk.Button(top, text="Save To Game", style="Accent.TButton", command=self._save_to_game).pack(side="right", padx=(8, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.status_var, style="Muted.TLabel").pack(fill="x", padx=18, pady=(0, 8))

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        left = ttk.Frame(body, width=460, style="Card.TFrame")
        right = ttk.Frame(body)
        body.add(left, weight=0)
        body.add(right, weight=1)
        self._build_roster_panel(left)
        self._build_editor_panel(right)
        self._bind_keyboard_navigation()

    def _bind_keyboard_navigation(self) -> None:
        self.root.bind_all("<Control-s>", lambda _event: self._save_to_game())
        self.root.bind_all("<Control-f>", lambda _event: self._focus_roster_search())
        self.root.bind_all("<Prior>", lambda _event: self._select_relative_tab(-1))
        self.root.bind_all("<Next>", lambda _event: self._select_relative_tab(1))
        self.root.bind_all("<Control-Left>", lambda _event: self._select_relative_tab(-1))
        self.root.bind_all("<Control-Right>", lambda _event: self._select_relative_tab(1))
        self.root.bind_all("<Escape>", lambda _event: self.player_tree.focus_set() if hasattr(self, "player_tree") else None)
        self.root.bind_all("<Return>", self._activate_focused_widget)
        for index in range(1, 9):
            self.root.bind_all(f"<Alt-Key-{index}>", lambda _event, tab_index=index - 1: self._select_tab(tab_index))

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
        header.pack(fill="x", padx=12, pady=12)
        ttk.Label(header, text="Roster", style="Card.TLabel", font=("Segoe UI Semibold", 15)).grid(row=0, column=0, sticky="w")
        self.roster_count_var = tk.StringVar(value="0 players")
        ttk.Label(header, textvariable=self.roster_count_var, style="Card.TLabel").grid(row=0, column=1, sticky="e")
        header.columnconfigure(0, weight=1)

        filters = ttk.Frame(parent, style="Card.TFrame")
        filters.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Label(filters, text="League", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 3))
        ttk.Label(filters, text="Team", style="Card.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 3))
        self.league_var = tk.StringVar(value="All Leagues")
        self.team_var = tk.StringVar(value="All Teams")
        self.search_var = tk.StringVar(value="")
        league_combo = ttk.Combobox(filters, textvariable=self.league_var, values=LEAGUE_FILTERS, state="readonly", width=18)
        self.team_combo = ttk.Combobox(filters, textvariable=self.team_var, values=["All Teams"], state="normal", width=28)
        league_combo.grid(row=1, column=0, sticky="ew")
        self.team_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(filters, text="Search", style="Card.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 3))
        self.search_entry = ttk.Entry(filters, textvariable=self.search_var)
        self.search_entry.grid(row=3, column=0, columnspan=2, sticky="ew")
        filters.columnconfigure(0, weight=1)
        filters.columnconfigure(1, weight=1)
        self.league_var.trace_add("write", lambda *_: self._on_league_changed())
        self.team_var.trace_add("write", lambda *_: self._refresh_player_list())
        self.search_var.trace_add("write", lambda *_: self._refresh_player_list())
        self.team_combo.bind("<KeyRelease>", self._filter_roster_team_combo)

        tree_frame = ttk.Frame(parent, style="Card.TFrame")
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        columns = ("team", "league", "org")
        self.player_tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")
        self.player_tree.heading("#0", text="Player")
        self.player_tree.heading("team", text="Team")
        self.player_tree.heading("league", text="League")
        self.player_tree.heading("org", text="Org")
        self.player_tree.column("#0", width=205, minwidth=160, stretch=True)
        self.player_tree.column("team", width=64, minwidth=52, anchor="center", stretch=False)
        self.player_tree.column("league", width=110, minwidth=80, anchor="center", stretch=False)
        self.player_tree.column("org", width=54, minwidth=44, anchor="center", stretch=False)
        player_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.player_tree.yview)
        self.player_tree.configure(yscrollcommand=player_scroll.set)
        self.player_tree.grid(row=0, column=0, sticky="nsew")
        player_scroll.grid(row=0, column=1, sticky="ns")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.player_tree.bind("<<TreeviewSelect>>", self._on_player_selected)
        self.player_tree.bind("<Button-3>", self._show_roster_context_menu)

    def _build_editor_panel(self, parent: ttk.Frame) -> None:
        player_header = ttk.Frame(parent)
        player_header.pack(fill="x", padx=(16, 0), pady=(0, 10))
        self.player_title_var = tk.StringVar(value="Select a player")
        self.player_subtitle_var = tk.StringVar(value="Choose a league/team on the left, then click a player.")
        ttk.Label(player_header, textvariable=self.player_title_var, style="Player.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(player_header, textvariable=self.player_subtitle_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w")
        player_header.columnconfigure(0, weight=1)

        movement = ttk.Frame(parent, style="Card.TFrame")
        movement.pack(fill="x", padx=(16, 0), pady=(0, 10))
        ttk.Label(movement, text="Player Movement", style="Card.TLabel", font=("Segoe UI Semibold", 12)).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 3))
        ttk.Label(movement, text="Target team", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))
        self.target_team_var = tk.StringVar(value="")
        self.target_team_combo = ttk.Combobox(movement, textvariable=self.target_team_var, values=[], state="normal", width=34)
        self.target_team_combo.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        ttk.Button(movement, text="Move Selected Player", style="Accent.TButton", command=self._move_selected_player).grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(0, 12))
        ttk.Button(movement, text="Send to Free Agency", style="Danger.TButton", command=self._send_selected_to_free_agency).grid(row=2, column=2, sticky="ew", padx=(0, 12), pady=(0, 12))
        movement.columnconfigure(0, weight=1)

        self.tabs = ttk.Notebook(parent)
        self.tabs.pack(fill="both", expand=True, padx=(16, 0))
        self.movement_tab = ttk.Frame(self.tabs)
        self.player_tab = ttk.Frame(self.tabs)
        self.attributes_tab = ttk.Frame(self.tabs)
        self.contracts_tab = ttk.Frame(self.tabs)
        self.edge_tab = ttk.Frame(self.tabs)
        self.updates_tab = ttk.Frame(self.tabs)
        self.create_tab = ttk.Frame(self.tabs)
        self.review_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.movement_tab, text="Move Players")
        self.tabs.add(self.player_tab, text="Player Info")
        self.tabs.add(self.attributes_tab, text="Attributes")
        self.tabs.add(self.contracts_tab, text="Contracts")
        self.tabs.add(self.edge_tab, text="NHL Edge")
        self.tabs.add(self.updates_tab, text="Auto Update")
        self.tabs.add(self.create_tab, text="Create / Compare")
        self.tabs.add(self.review_tab, text="Final Review")
        self._build_movement_tab()
        self._build_player_info_tab()
        self._build_attributes_tab()
        self._build_contracts_tab()
        self._build_edge_tab()
        self._build_updates_tab()
        self._build_create_tab()
        self._build_review_tab()

    def _build_movement_tab(self) -> None:
        controls = ttk.Frame(self.movement_tab)
        controls.pack(fill="x", padx=12, pady=12)
        self.move_left_team_var = tk.StringVar(value="")
        self.move_right_team_var = tk.StringVar(value="")
        ttk.Label(controls, text="Team 1").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="Team 2").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.move_left_combo = ttk.Combobox(controls, textvariable=self.move_left_team_var, values=[], state="normal", width=34)
        self.move_right_combo = ttk.Combobox(controls, textvariable=self.move_right_team_var, values=[], state="normal", width=34)
        self.move_left_combo.grid(row=1, column=0, sticky="ew")
        self.move_right_combo.grid(row=1, column=2, sticky="ew", padx=(12, 0))
        ttk.Button(controls, text="Refresh Teams", command=self._refresh_trade_lanes).grid(row=1, column=3, sticky="ew", padx=(12, 0))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(2, weight=1)
        self.move_left_team_var.trace_add("write", lambda *_: self._refresh_trade_lanes())
        self.move_right_team_var.trace_add("write", lambda *_: self._refresh_trade_lanes())
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
        ttk.Label(left_frame, textvariable=self.move_left_title_var, font=("Segoe UI Semibold", 13)).pack(anchor="w", pady=(0, 6))
        ttk.Label(right_frame, textvariable=self.move_right_title_var, font=("Segoe UI Semibold", 13)).pack(anchor="w", pady=(0, 6))
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

        ttk.Label(middle_frame, text="Move", font=("Segoe UI Semibold", 14)).pack(pady=(48, 12))
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
        self.target_overall_var = tk.IntVar(value=85)
        self.attribute_xp_var = tk.StringVar(value="Select a player to calculate attribute budget.")
        ttk.Label(top, text="Archetype").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="Overall Cap").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Combobox(top, textvariable=self.archetype_var, values=sorted(ARCHETYPE_WEIGHTS.keys()), state="readonly", width=24).grid(row=1, column=0, sticky="ew")
        tk.Spinbox(top, textvariable=self.target_overall_var, from_=36, to=99, width=8, command=self._update_attribute_budget).grid(row=1, column=1, sticky="w", padx=(10, 0))
        ttk.Label(top, textvariable=self.attribute_xp_var, style="Accent.TLabel").grid(row=1, column=2, sticky="w", padx=18)
        ttk.Button(top, text="Apply Overall Cap Plan", command=self._apply_overall_cap_plan).grid(row=1, column=3, sticky="e", padx=(8, 0))
        ttk.Button(top, text="Load NHL Edge", command=self._load_edge_for_selected).grid(row=1, column=4, sticky="e", padx=(8, 0))
        ttk.Button(top, text="Apply NHL Edge Suggestions", command=self._apply_edge_suggestions).grid(row=1, column=5, sticky="e", padx=(8, 0))
        ttk.Button(top, text="Save Attributes", style="Accent.TButton", command=self._save_attributes).grid(row=1, column=6, sticky="e", padx=(8, 0))
        top.columnconfigure(0, weight=1)
        top.columnconfigure(2, weight=1)
        self.archetype_var.trace_add("write", lambda *_: self._update_attribute_budget())
        self.target_overall_var.trace_add("write", lambda *_: self._update_attribute_budget())

        self.attribute_scroll = ScrollFrame(self.attributes_tab)
        self.attribute_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_contracts_tab(self) -> None:
        controls = ttk.Frame(self.contracts_tab)
        controls.pack(fill="x", padx=12, pady=12)
        self.real_cap_var = tk.StringVar(value=str(DEFAULT_REAL_CAP_MILLIONS))
        self.game_cap_var = tk.StringVar(value="78.6")
        ttk.Label(controls, text="Current NHL Cap (M)").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="NHL Legacy Cap (M)").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Entry(controls, textvariable=self.real_cap_var, width=14).grid(row=1, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.game_cap_var, width=14).grid(row=1, column=1, sticky="w", padx=(8, 0))
        ttk.Button(controls, text="Load Selected Contract", command=self._load_selected_contract).grid(row=1, column=2, padx=(18, 0))
        ttk.Button(controls, text="Approve Selected Scaled Contract", command=self._approve_selected_contract).grid(row=1, column=3, padx=(8, 0))
        ttk.Button(controls, text="Update All Contracts From CapWages", style="Accent.TButton", command=self._build_all_contracts).grid(row=1, column=4, padx=(8, 0))
        controls.columnconfigure(5, weight=1)

        self.contract_detail_text = tk.Text(self.contracts_tab, height=8, wrap="word", background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat")
        self.contract_detail_text.pack(fill="x", padx=12, pady=(0, 12))

        columns = ("team", "current", "real", "scaled", "percent", "expiry")
        self.contract_tree = ttk.Treeview(self.contracts_tab, columns=columns, show="tree headings")
        self.contract_tree.heading("#0", text="Player")
        self.contract_tree.heading("team", text="Team")
        self.contract_tree.heading("current", text="Roster Team")
        self.contract_tree.heading("real", text="Real AAV")
        self.contract_tree.heading("scaled", text="Game AAV")
        self.contract_tree.heading("percent", text="Cap %")
        self.contract_tree.heading("expiry", text="Expiry")
        self.contract_tree.column("#0", width=240, stretch=True)
        for column in columns:
            self.contract_tree.column(column, width=105, anchor="center", stretch=False)
        self.contract_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_edge_tab(self) -> None:
        top = ttk.Frame(self.edge_tab)
        top.pack(fill="x", padx=12, pady=12)
        ttk.Button(top, text="Load NHL Edge For Selected Player", style="Accent.TButton", command=self._load_edge_for_selected).pack(side="left")
        ttk.Label(top, text="Suggestions are staged into sliders only. Use Save Attributes to write them.", style="Muted.TLabel").pack(side="left", padx=12)
        self.edge_text = tk.Text(self.edge_tab, wrap="word", background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat")
        self.edge_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_updates_tab(self) -> None:
        controls = ttk.Frame(self.updates_tab)
        controls.pack(fill="x", padx=12, pady=12)
        ttk.Button(controls, text="Scan CapWages For Roster Moves", style="Accent.TButton", command=self._scan_capwages_updates).pack(side="left")
        ttk.Button(controls, text="Apply Selected Moves", command=self._apply_selected_update_moves).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Apply All Moves", command=lambda: self._apply_update_moves(apply_all=True)).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Veto Selected", command=self._veto_selected_update_moves).pack(side="left", padx=(8, 0))
        ttk.Label(controls, text="Organization-aware: AHL/system/prospect players already inside their NHL org are not flagged.", style="Muted.TLabel").pack(side="left", padx=12)

        org_frame = ttk.LabelFrame(self.updates_tab, text="Organization Links")
        org_frame.pack(fill="x", padx=12, pady=(0, 12))
        self.org_team_var = tk.StringVar(value="")
        self.org_parent_var = tk.StringVar(value="TOR")
        ttk.Label(org_frame, text="Team / league code").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
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
        self.update_tree.pack(fill="both", expand=True)
        self.update_tree.bind("<Button-3>", self._show_update_context_menu)

        ttk.Label(create_frame, text="Create candidates from CapWages (players missing in roster with draft info)", style="Muted.TLabel").pack(anchor="w", pady=(8, 4))
        self.create_candidate_list = tk.Listbox(create_frame, background="#0b1118", foreground=self.colors["ink"], selectbackground="#244e76", relief="flat", height=8)
        self.create_candidate_list.pack(fill="both", expand=True)

    def _build_create_tab(self) -> None:
        wrapper = ScrollFrame(self.create_tab)
        wrapper.pack(fill="both", expand=True)
        frame = wrapper.inner
        ttk.Label(frame, text="Comparison Builder", font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(
            frame,
            text="Use this to stage ratings for an existing created/prospect player by blending comparable players, then apply those values to the selected player's sliders.",
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w", padx=12, pady=(0, 10))

        form = ttk.Frame(frame)
        form.pack(fill="x", padx=12, pady=(0, 10))
        self.compare_sources_text = tk.Text(form, height=5, width=48, background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat")
        self.compare_target_overall_var = tk.IntVar(value=82)
        self.compare_archetype_var = tk.StringVar(value="playmaker")
        ttk.Label(form, text="Comparable Players (one per line)").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="Archetype").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Label(form, text="Target OVR").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.compare_sources_text.grid(row=1, column=0, rowspan=3, sticky="nsew")
        ttk.Combobox(form, textvariable=self.compare_archetype_var, values=sorted(ARCHETYPE_WEIGHTS.keys()), state="readonly").grid(row=1, column=1, sticky="ew", padx=(12, 0))
        tk.Spinbox(form, textvariable=self.compare_target_overall_var, from_=36, to=99, width=8).grid(row=1, column=2, sticky="w", padx=(12, 0))
        ttk.Button(form, text="Build Blend", command=self._build_comparison).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(8, 0))
        ttk.Button(form, text="Apply Blend To Attribute Sliders", style="Accent.TButton", command=self._apply_comparison_to_sliders).grid(row=3, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=(8, 0))
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=0)

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
        ttk.Button(controls, text="Reload Review", command=self._refresh_review).pack(side="left")
        ttk.Button(controls, text="Apply Selected Pending Move", command=self._apply_selected_review_move).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Veto Selected Pending Move", command=self._veto_selected_review_move).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Save To Game", style="Accent.TButton", command=self._save_to_game).pack(side="left", padx=(8, 0))
        ttk.Label(controls, text="Review completed edits plus pending auto-update moves before saving to Xenia.", style="Muted.TLabel").pack(side="left", padx=12)
        columns = ("time", "type", "details")
        self.review_tree = ttk.Treeview(self.review_tab, columns=columns, show="tree headings")
        self.review_tree.heading("#0", text="Player / Item")
        self.review_tree.heading("time", text="Time")
        self.review_tree.heading("type", text="Change Type")
        self.review_tree.heading("details", text="Details")
        self.review_tree.column("#0", width=220, stretch=True)
        self.review_tree.column("time", width=155, anchor="center", stretch=False)
        self.review_tree.column("type", width=170, anchor="center", stretch=False)
        self.review_tree.column("details", width=620, stretch=True)
        self.review_tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.review_tree.bind("<Button-3>", self._show_review_context_menu)

    def _build_advanced_tab(self) -> None:
        wrapper = ScrollFrame(self.advanced_tab)
        wrapper.pack(fill="both", expand=True)
        frame = wrapper.inner
        ttk.Label(frame, text="Raw Flags / Potential Research", font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=12, pady=(12, 4))
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
            filetypes=[("NHL roster saves", "*"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.workspace = create_workspace(Path(path))
            self._reload_workspace()
            self._set_status(f"Opened workspace {self.workspace.name}")
        except Exception as exc:
            self._show_error("Open roster failed", exc)

    def _reload_workspace(self) -> None:
        if self.workspace is None:
            self.workspace = load_active_workspace()
        if self.workspace is None:
            self._set_status("No workspace is active. Open a roster save.")
            return
        try:
            self._load_organization_links()
            self._load_update_vetoes()
            self.teams = load_teams(self.workspace.working_db)
            self.team_by_code = {team.code: team for team in self.teams}
            self._rebuild_player_cache()
            self._rebuild_team_choices()
            self._refresh_trade_lanes()
            self._refresh_player_list()
            self._refresh_contract_queue()
            self._refresh_update_queue()
            self._refresh_review()
            self._set_status(f"Workspace: {self.workspace.name} | Working roster: {self.workspace.working_roster}")
        except Exception as exc:
            self._show_error("Reload failed", exc)

    def _rebuild_player_cache(self) -> None:
        if self.workspace is None:
            self.player_snapshot_cache = None
            self.player_index = []
            return
        self.player_snapshot_cache = build_player_snapshot_cache(self.workspace.working_db)
        self.player_index = build_player_index_from_tables(
            self.player_snapshot_cache.bio_rows,
            self.player_snapshot_cache.relation_rows,
            self.player_snapshot_cache.instance_rows,
            self.team_by_code,
            self.organization_links,
        )

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
        combo.configure(values=self._combo_search_values(self.team_choice_values, combo.get())[:80])

    def _filter_roster_team_combo(self, event=None) -> None:
        if event is not None and event.keysym in {"Up", "Down", "Left", "Right", "Return", "Escape", "Tab"}:
            return
        values = getattr(self, "roster_team_values", ["All Teams", "Free Agents"])
        self.team_combo.configure(values=self._combo_search_values(values, self.team_combo.get())[:80])

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
        self._on_league_changed()

    def _team_display_for_abbrev(self, abbrev: str) -> str | None:
        target = abbrev.upper()
        for display, candidate in self.team_display_to_abbrev.items():
            if candidate.upper() == target:
                return display
        return None

    def _on_league_changed(self) -> None:
        league = self.league_var.get()
        values = ["All Teams", "Free Agents"]
        self.roster_team_filter_display_to_filter = {
            "All Teams": ("all", None),
            "Free Agents": ("free", None),
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
        for item in self.player_tree.get_children():
            self.player_tree.delete(item)
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
        for entry in self.player_index:
            if filter_kind not in {"org"} and league != "All Leagues" and entry.league_name != league:
                continue
            if filter_kind == "free" and entry.league_name != "Free Agents":
                continue
            if filter_kind == "exact" and (entry.current_team_abbrev or "").upper() != (filter_value or ""):
                continue
            if filter_kind == "org" and (entry.organization_abbrev or "").upper() != (filter_value or ""):
                continue
            if search and search not in entry.full_name.lower():
                continue
            iid = str(entry.player_id)
            suffix = 1
            while self.player_tree.exists(iid):
                suffix += 1
                iid = f"{entry.player_id}-{suffix}"
            self.player_tree.insert(
                "",
                "end",
                iid=iid,
                text=entry.full_name,
                values=(
                    entry.current_team_abbrev or "FA",
                    entry.league_name,
                    entry.organization_abbrev or "",
                ),
            )
            self.player_iid_to_entry[iid] = entry
            inserted += 1
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
        return [
            player
            for player in self.player_index
            if (player.current_team_abbrev or "").upper() == abbrev.upper()
        ]

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
        for item in tree.get_children():
            tree.delete(item)
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
            results = []
            errors: list[str] = []
            for entry in entries:
                try:
                    results.append(move_player_to_team(self.workspace.working_db, entry.first_name, entry.last_name, target))
                except Exception as exc:
                    errors.append(f"{entry.full_name}: {exc}")
            sync_working_db_to_roster(self.workspace)
            return results, errors

        def success(result):
            results, errors = result
            for move_result in results:
                self._log_action("move-player", move_result)
            self._reload_workspace()
            if errors:
                messagebox.showwarning("Some moves failed", "\n".join(errors[:8]))
            self._set_status(f"Moved {len(results)} player(s) to {target}.")

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
            results = [
                move_player_to_team_code(self.workspace.working_db, entry.first_name, entry.last_name, FREE_AGENCY_CODE)
                for entry in entries
            ]
            sync_working_db_to_roster(self.workspace)
            return results

        def success(results):
            for result in results:
                self._log_action("move-to-free-agency", result)
            self._reload_workspace()
            self._set_status(f"Moved {len(results)} player(s) to {FREE_AGENCY_LABEL}.")

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
        menu.add_command(label="Apply Pending Move", command=self._apply_selected_review_move)
        menu.add_command(label="Veto Pending Move", command=self._veto_selected_review_move)
        menu.add_separator()
        menu.add_command(label="Save To Game", command=self._save_to_game)
        menu.tk_popup(event.x_root, event.y_root)

    def _load_player(self, entry: PlayerListEntry) -> None:
        if self.workspace is None:
            return
        try:
            self.selected_player = entry
            if self.player_snapshot_cache is not None:
                self.snapshot = self.player_snapshot_cache.get_player_snapshot(entry.first_name, entry.last_name)
            else:
                self.snapshot = get_player_snapshot(self.workspace.working_db, entry.first_name, entry.last_name)
            if self.snapshot is None:
                raise RuntimeError(f"Player not found: {entry.full_name}")
            self.current_team = self._current_team_from_snapshot()
            self.player_title_var.set(entry.full_name)
            self.player_subtitle_var.set(
                f"{entry.current_team_abbrev or 'FA'} | {entry.league_name} | Organization: {entry.organization_abbrev or 'None'}"
            )
            self._populate_player_info()
            self._populate_attributes()
            self.capwages_player = None
            self.official_player_hit = None
            self.contract_detail_text.delete("1.0", "end")
            self.edge_text.delete("1.0", "end")
            self.edge_suggestions = {}
            self.edge_suggestion_notes = {}
            self._refresh_attribute_edge_notes()
            self._set_status(f"Loaded {entry.full_name}")
        except Exception as exc:
            self._show_error("Load player failed", exc)

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
        team_label = self.selected_player.current_team_abbrev or FREE_AGENCY_LABEL
        self.info_vars["team"].set(team_label)
        self.info_vars["organization"].set(self.selected_player.organization_abbrev or "")
        self.info_vars["league"].set(self.selected_player.league_name)
        junior = self.selected_player.current_team_name if self.selected_player.league_name == "CHL / Juniors" else "Not mapped / not currently a CHL player"
        self.info_vars["junior_rights"].set(junior or "")
        self.info_vars["jersey"].set(str(bio.get("tRVs") or ""))
        self.info_vars["birthplace"].set(str(bio.get("JzFM") or ""))
        ratings = self.snapshot.ratings_row or {}
        position_label = self._position_label_from_code(_safe_int(bio.get("aljv"), -1))
        self.info_vars["position_label"].set(position_label)
        instance = self.snapshot.instance_rows[0] if self.snapshot.instance_rows else {}
        rating_style_code = None if not ratings else _safe_int(ratings.get("sFgQ"), -1)
        instance_style_code = None if not instance else _safe_int(instance.get("sFgQ"), -1)
        style_code = rating_style_code if rating_style_code is not None and rating_style_code >= 0 else instance_style_code
        fighting_code = None if not ratings else _safe_int(ratings.get("YqJH"), -1)
        self.style_combo.configure(values=self._style_choices_for_selected_player())
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
        if self.snapshot is None or not self.snapshot.ratings_row:
            ttk.Label(self.attribute_scroll.inner, text="No skater ratings row found for this player.", style="Muted.TLabel").pack(anchor="w", padx=12, pady=12)
            return
        specs = SKATER_ATTRIBUTE_SPECS
        for index, spec in enumerate(specs):
            row = index % 14
            column = 0 if index < 14 else 4
            raw_value = _safe_int(self.snapshot.ratings_row.get(spec.field), 0)
            display_value = raw_to_display(spec, raw_value)
            self.attribute_original_values[spec.field] = display_value
            var = tk.IntVar(value=display_value)
            edge_var = tk.StringVar(value="")
            self.attribute_vars[spec.field] = var
            self.attribute_edge_vars[spec.label] = edge_var
            ttk.Label(self.attribute_scroll.inner, text=spec.label).grid(row=row, column=column, sticky="w", padx=(8, 8), pady=5)
            tk.Scale(
                self.attribute_scroll.inner,
                variable=var,
                from_=spec.min_value if spec.mode == "raw" else spec.min_value + 36,
                to=spec.max_value,
                orient="horizontal",
                showvalue=False,
                length=220,
                background=self.colors["panel"],
                foreground=self.colors["ink"],
                troughcolor="#0b1118",
                highlightthickness=0,
                activebackground=self.colors["accent"],
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
                wraplength=210,
            ).grid(row=row, column=column + 3, sticky="w", padx=(0, 18), pady=5)
            var.trace_add("write", lambda *_: self._update_attribute_budget())
        self.attribute_scroll.inner.columnconfigure(1, weight=1)
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
                var.set(f"Edge: {note}")
            elif note:
                var.set(f"Edge {suggestion}: {note}")
            else:
                var.set(f"Edge {suggestion}")

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
        for spec in SKATER_ATTRIBUTE_SPECS:
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
        archetype = self.archetype_var.get()
        if archetype not in ARCHETYPE_WEIGHTS:
            return
        try:
            target = int(self.target_overall_var.get())
        except tk.TclError:
            return
        base = self._ratings_as_semantic(current=False)
        current = self._ratings_as_semantic(current=True)
        base_overall = calculate_weighted_overall(base, archetype)
        current_overall = calculate_weighted_overall(current, archetype)
        weights = ARCHETYPE_WEIGHTS[archetype]
        required = max(0.0, (target - base_overall) * sum(weights.values()))
        used = 0.0
        points_used = 0
        for spec in SKATER_ATTRIBUTE_SPECS:
            semantic = LABEL_TO_SEMANTIC.get(spec.label)
            if semantic not in weights:
                continue
            delta = max(0, current.get(semantic, 0) - base.get(semantic, 0))
            points_used += delta
            used += delta * weights[semantic]
        remaining = required - used
        self.attribute_xp_var.set(
            f"Est. OVR {current_overall} | cap {target} | weighted XP remaining {remaining:.1f} | points used {points_used}"
        )

    def _apply_overall_cap_plan(self) -> None:
        if not self.attribute_vars:
            self._set_status("Select a player before applying an overall cap plan.")
            return
        archetype = self.archetype_var.get()
        if archetype not in ARCHETYPE_WEIGHTS:
            self._set_status("Choose an archetype first.")
            return
        try:
            target = int(self.target_overall_var.get())
        except tk.TclError:
            self._set_status("Enter a valid overall cap.")
            return
        current = self._ratings_as_semantic(current=True)
        plan = plan_rating_upgrade(current, archetype, target)
        semantic_to_field = {
            semantic: spec.field
            for spec in SKATER_ATTRIBUTE_SPECS
            for semantic in [LABEL_TO_SEMANTIC.get(spec.label)]
            if semantic
        }
        applied = 0
        for semantic, value in plan.suggested_ratings.items():
            field = semantic_to_field.get(semantic)
            if field in self.attribute_vars:
                self.attribute_vars[field].set(value)
                applied += 1
        self._update_attribute_budget()
        self._set_status(
            f"Applied overall cap plan to {applied} sliders. Press Save Attributes when you want to write it."
        )

    def _save_attributes(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        specs = attribute_specs_by_field()
        updates: dict[str, int] = {}
        for field, var in self.attribute_vars.items():
            spec = specs[field]
            display_value = _safe_int(var.get(), self.attribute_original_values.get(field, 0))
            if display_value != self.attribute_original_values.get(field):
                updates[field] = display_to_raw(spec, display_value)
        if not updates:
            self._set_status("No attribute changes to save.")
            return
        try:
            result = update_player_ratings(
                self.workspace.working_db,
                self.selected_player.first_name,
                self.selected_player.last_name,
                updates,
            )
            self._log_action("update-attributes", result)
            sync_working_db_to_roster(self.workspace)
            if self.snapshot is not None and self.snapshot.ratings_row is not None:
                self.snapshot.ratings_row.update(updates)
            self._populate_attributes()
            self._refresh_review()
            self._set_status("Saved attributes.")
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
            if self.snapshot is not None and self.snapshot.ratings_row is not None:
                rating_updates["sFgQ"] = int(style_code)
            instance_updates["sFgQ"] = int(style_code)
        if fighting_label in FIGHTING_CODES and self.snapshot is not None and self.snapshot.ratings_row is not None:
            rating_updates["YqJH"] = int(FIGHTING_CODES[fighting_label])
        try:
            bio_result = update_player_bio(
                self.workspace.working_db,
                self.selected_player.first_name,
                self.selected_player.last_name,
                bio_updates,
            )
            rating_result = {"player": self.selected_player.full_name, "updated_fields": {}, "changes": []}
            instance_result = {"player": self.selected_player.full_name, "updated_fields": {}, "changes": []}
            if rating_updates:
                rating_result = update_player_ratings(
                    self.workspace.working_db,
                    self.selected_player.first_name,
                    self.selected_player.last_name,
                    rating_updates,
                )
            if instance_updates:
                instance_result = update_player_instance_fields(
                    self.workspace.working_db,
                    self.selected_player.first_name,
                    self.selected_player.last_name,
                    instance_updates,
                )
            self._log_action("update-player-info", {"bio": bio_result, "ratings": rating_result, "instance": instance_result})
            sync_working_db_to_roster(self.workspace)
            if self.snapshot is not None:
                self.snapshot.bio.update(bio_updates)
                if self.snapshot.ratings_row is not None:
                    self.snapshot.ratings_row.update(rating_updates)
                for row in self.snapshot.instance_rows:
                    row.update(instance_updates)
            self._populate_player_info()
            self._populate_attributes()
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

    def _reload_after_player_write(self, status: str) -> None:
        selected_name = None if self.selected_player is None else self.selected_player.full_name
        self._rebuild_player_cache()
        self._refresh_player_list()
        if selected_name:
            entry = next((item for item in self.player_index if item.full_name == selected_name), None)
            if entry:
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
            result = move_player_to_team(self.workspace.working_db, player.first_name, player.last_name, target)
            sync_working_db_to_roster(self.workspace)
            return result

        def success(result):
            self._log_action("move-player", result)
            self._reload_workspace()
            self._set_status(f"Moved {player.full_name} to {target}.")

        self._run_background("Moving player", worker, success)

    def _send_selected_to_free_agency(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        if not messagebox.askyesno("Send to Free Agency", f"Move {self.selected_player.full_name} to free agency/unassigned?"):
            return
        player = self.selected_player

        def worker():
            result = move_player_to_team_code(
                self.workspace.working_db,
                player.first_name,
                player.last_name,
                FREE_AGENCY_CODE,
            )
            sync_working_db_to_roster(self.workspace)
            return result

        def success(result):
            self._log_action("move-to-free-agency", result)
            self._reload_workspace()
            self._set_status(f"Moved {player.full_name} to {FREE_AGENCY_LABEL}.")

        self._run_background("Moving player to free agency", worker, success)

    def _load_remote_bio(self) -> None:
        if self.selected_player is None:
            return
        player_name = self.selected_player.full_name

        def worker():
            official_hits = find_player_on_official_rosters(player_name)
            official = official_hits[0] if official_hits else None
            landing = fetch_player_landing(official.player_id) if official else None
            hockeydb = fetch_hockeydb_profile_by_name(player_name)
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

    def _load_selected_contract(self) -> None:
        if self.selected_player is None:
            return
        player_name = self.selected_player.full_name
        source_team = canonical_abbrev(self.selected_player.organization_abbrev or self.selected_player.current_team_abbrev)
        if source_team == "LA":
            source_team = "LAK"
        if source_team == "SJ":
            source_team = "SJS"
        slug = TEAM_SLUGS.get(source_team or "")
        if not slug:
            self._set_status(f"No CapWages team slug mapped for {source_team or 'unknown team'}.")
            return

        def worker():
            data = fetch_capwages_team_contracts(slug)
            target = normalize_name(player_name)
            for bucket in ("signed", "unsigned", "reserve"):
                for row in data.get(bucket, []):
                    if normalize_name(row.name) == target:
                        return row
            return None

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
        lines.append("")
        lines.append("Roster salary save-back field is not decoded yet. Approval stores the scaled contract pass in the workspace review log.")
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
        approved = load_json_state(self.workspace, "contract_approved.json", [])
        entry = {
            "player_name": self.selected_player.full_name,
            "real_aav_millions": scaled.real_aav_millions,
            "game_aav_millions": scaled.scaled_aav_millions,
            "cap_hit_percent": scaled.cap_hit_percent,
            "expiry": row.expiry,
            "source": "CapWages",
            "note": "Approved scaled contract. Roster salary field mapping pending.",
        }
        approved.append(entry)
        save_json_state(self.workspace, "contract_approved.json", approved)
        self._log_action("approve-scaled-contract", entry)
        self._refresh_review()
        self._set_status(f"Approved scaled contract for {self.selected_player.full_name}.")

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
        for item in self.contract_tree.get_children():
            self.contract_tree.delete(item)
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
                    row.get("expiry") or "",
                ),
            )

    def _load_edge_for_selected(self) -> None:
        if self.selected_player is None:
            self._on_player_selected()
        if self.selected_player is None:
            self._set_status("Select a player before loading NHL Edge.")
            return
        player_name = self.selected_player.full_name
        if hasattr(self, "edge_text"):
            self.edge_text.delete("1.0", "end")
            self.edge_text.insert("1.0", f"Loading NHL Edge data for {player_name}...")

        def worker():
            hits = find_player_on_official_rosters(player_name)
            if not hits:
                raise RuntimeError("No current NHL roster match found. NHL Edge only covers current NHL players.")
            hit = hits[0]
            return hit, fetch_edge_skater_detail(hit.player_id)

        def success(result):
            hit, data = result
            self.official_player_hit = hit
            self.edge_suggestions, self.edge_suggestion_notes = self._build_edge_suggestions(data)
            self._refresh_attribute_edge_notes()
            self._render_edge(hit, data, self.edge_suggestions, self.edge_suggestion_notes)
            self.root.after(75, self._refresh_attribute_edge_notes)
            self._set_status(f"Loaded NHL Edge data for {hit.full_name}.")

        self._run_background("Loading NHL Edge", worker, success)

    def _current_attribute_rating(self, label: str) -> int | None:
        field_by_label = {spec.label: spec.field for spec in SKATER_ATTRIBUTE_SPECS}
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
            return guarded, f"raw Edge target {target}; guarded from current {current}"
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
        guarded, guard_note = self._guard_edge_rating(label, target, max_drop=max_drop)
        suggestions[label] = guarded
        notes[label] = f"{note}; {guard_note}" if guard_note else note

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

    def _render_edge(self, hit, data: dict, suggestions: dict[str, int], notes: dict[str, str]) -> None:
        self.edge_text.delete("1.0", "end")
        lines = [f"NHL Edge match: {hit.full_name} | {hit.team_abbrev} | NHL ID {hit.player_id}", ""]
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

        lines.append("Available Edge data in this feed:")
        lines.append("- Skating: max speed, bursts over 20 mph, total distance, max distance in a game.")
        lines.append("- Shooting: top shot speed, shot/goal/shooting-percentile summaries, and shot-location volume.")
        lines.append("- Zone time: offensive, offensive even-strength, neutral, and defensive zone shares.")
        lines.append("- Not exposed here: hits, blocks, takeaways, giveaways, puck battles, entry denial, or direct stick-checking events.")
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
            lines.append("No percentile-based suggestions were available from this Edge response.")
        self.edge_text.insert("1.0", "\n".join(lines))

    def _apply_edge_suggestions(self) -> None:
        if not self.edge_suggestions:
            self._set_status("Load NHL Edge suggestions first.")
            return
        field_by_label = {spec.label: spec.field for spec in SKATER_ATTRIBUTE_SPECS}
        applied = 0
        for label, value in self.edge_suggestions.items():
            field = field_by_label.get(label)
            if field and field in self.attribute_vars:
                self.attribute_vars[field].set(value)
                applied += 1
        self._update_attribute_budget()
        self._set_status(f"Applied {applied} NHL Edge suggestions to the sliders.")

    def _scan_capwages_updates(self) -> None:
        if self.workspace is None:
            return

        def worker():
            return build_capwages_roster_update(
                self.player_index,
                team_slugs=TEAM_SLUGS,
                organization_links=self.organization_links,
            )

        def success(queue):
            self.update_queue = queue
            save_json_state(self.workspace, "update_queue.json", queue)
            self._render_update_queue()
            self._set_status(f"Found {len(queue.get('moves', []))} move proposals and {len(queue.get('create_candidates', []))} create candidates.")

        self._run_background("Scanning CapWages", worker, success)

    def _refresh_update_queue(self) -> None:
        if self.workspace is None:
            return
        self.update_queue = load_json_state(self.workspace, "update_queue.json", {"moves": [], "create_candidates": []})
        self._load_update_vetoes()
        self._render_update_queue()

    def _render_update_queue(self) -> None:
        for item in self.update_tree.get_children():
            self.update_tree.delete(item)
        for index, row in enumerate(self.update_queue.get("moves", [])):
            token = self._update_move_token(row)
            if token in self.update_vetoes:
                continue
            self.update_tree.insert(
                "",
                "end",
                iid=f"move-{index}",
                text=str(row.get("player_name") or ""),
                values=(row.get("from_team") or "", row.get("to_team") or "", row.get("source") or "", row.get("reason") or ""),
            )
        self.create_candidate_list.delete(0, "end")
        for row in self.update_queue.get("create_candidates", []):
            self.create_candidate_list.insert(
                "end",
                f"{row.get('player_name')} | {row.get('team')} | {row.get('position') or '?'} | drafted {row.get('drafted_by') or '?'} {row.get('draft_year') or ''}",
            )

    def _update_move_token(self, row: dict[str, object]) -> str:
        return f"{row.get('player_name')}|{row.get('from_team')}|{row.get('to_team')}"

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

    def _apply_selected_update_moves(self) -> None:
        self._apply_update_moves(apply_all=False)

    def _apply_update_moves(self, *, apply_all: bool) -> None:
        if self.workspace is None:
            return
        moves = self.update_queue.get("moves", [])
        if apply_all:
            indices = [
                index
                for index, row in enumerate(moves)
                if self._update_move_token(row) not in self.update_vetoes
            ]
        else:
            selected = self.update_tree.selection()
            indices = [int(item.split("-", 1)[1]) for item in selected if item.startswith("move-")]
        if not indices:
            self._set_status("No update moves selected.")
            return
        applied = 0
        errors: list[str] = []
        for index in indices:
            row = moves[index]
            try:
                first, last = _split_name(str(row["player_name"]))
                result = move_player_to_team(self.workspace.working_db, first, last, str(row["to_team"]))
                self._log_action("auto-update-move", result)
                applied += 1
            except Exception as exc:
                errors.append(f"{row.get('player_name')}: {exc}")
        sync_working_db_to_roster(self.workspace)
        self._reload_workspace()
        if errors:
            messagebox.showwarning("Some moves failed", "\n".join(errors[:8]))
        self._set_status(f"Applied {applied} CapWages roster moves.")

    def _build_comparison(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        sources = [line.strip() for line in self.compare_sources_text.get("1.0", "end").splitlines() if line.strip()]
        if not sources:
            self._set_status("Add at least one comparable player.")
            return
        target = self.selected_player.full_name
        try:
            source_values: list[dict[str, int]] = []
            for source_name in sources:
                first, last = _split_name(source_name)
                if self.player_snapshot_cache is not None:
                    snapshot = self.player_snapshot_cache.get_player_snapshot(first, last)
                else:
                    snapshot = get_player_snapshot(self.workspace.working_db, first, last)
                if snapshot is None or snapshot.ratings_row is None:
                    raise RuntimeError(f"Ratings row not found for comparison player: {source_name}")
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
            result = update_player_ratings(
                self.workspace.working_db,
                self.selected_player.first_name,
                self.selected_player.last_name,
                updates,
            )
            result["potential_display"] = {
                "role": self.potential_role_var.get(),
                "stars": self.potential_stars_var.get(),
                "accuracy": accuracy_label,
                "exact_silver_note": (
                    "Exact/Silver is game-derived; current growth accuracy was preserved."
                    if accuracy_code is None
                    else ""
                ),
            }
            self._log_action("update-potential", result)
            sync_working_db_to_roster(self.workspace)
            if self.snapshot is not None and self.snapshot.ratings_row is not None:
                self.snapshot.ratings_row.update(updates)
            self._load_potential_note()
            self._refresh_review()
            self._set_status("Saved potential to roster.")
        except Exception as exc:
            self._show_error("Save potential failed", exc)

    def _load_potential_note(self) -> None:
        if self.workspace is None or self.selected_player is None:
            return
        ratings = self.snapshot.ratings_row if self.snapshot else None
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
        for item in self.review_tree.get_children():
            self.review_tree.delete(item)
        self._load_update_vetoes()
        self.update_queue = load_json_state(self.workspace, "update_queue.json", {"moves": [], "create_candidates": []})
        for index, move in enumerate(self.update_queue.get("moves", [])):
            token = self._update_move_token(move)
            if token in self.update_vetoes:
                continue
            self.review_tree.insert(
                "",
                "end",
                iid=f"pending-{index}",
                text=str(move.get("player_name") or ""),
                values=("", "pending-auto-update", f"{move.get('from_team') or 'None'} -> {move.get('to_team')} | {move.get('reason') or ''}"),
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

    def _apply_selected_review_move(self) -> None:
        if self.workspace is None:
            return
        moves = self.update_queue.get("moves", [])
        indices = self._selected_review_pending_indices()
        if not indices:
            self._set_status("Select a pending auto-update move in Final Review first.")
            return
        applied = 0
        for index in indices:
            if not (0 <= index < len(moves)):
                continue
            row = moves[index]
            if self._update_move_token(row) in self.update_vetoes:
                continue
            first, last = _split_name(str(row["player_name"]))
            result = move_player_to_team(self.workspace.working_db, first, last, str(row["to_team"]))
            self._log_action("auto-update-move", result)
            applied += 1
        sync_working_db_to_roster(self.workspace)
        self._reload_workspace()
        self._set_status(f"Applied {applied} pending move(s) from Final Review.")

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

    def _sync_roster(self) -> None:
        if self.workspace is None:
            return
        try:
            path = sync_working_db_to_roster(self.workspace)
            self._set_status(f"Synced working roster payload: {path}")
        except Exception as exc:
            self._show_error("Sync failed", exc)

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
        self._set_status(f"Game save target set: {path}")

    def _save_to_game(self) -> None:
        if self.workspace is None:
            return
        try:
            working_roster = sync_working_db_to_roster(self.workspace)
            target = self._guess_game_save_target()
            if target is None:
                self._set_game_save_target()
                target = self._guess_game_save_target()
            if target is None:
                self._set_status("No game save target set. Choose Set Game Save Target first.")
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = target.with_name(f"{target.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
                shutil.copy2(target, backup)
            shutil.copy2(working_roster, target)
            self.workspace.source_roster_path = str(target)
            save_active_workspace(self.workspace)
            self._log_action("save-to-game", {"target": str(target), "working_roster": str(working_roster)})
            self._refresh_review()
            self._set_status(f"Saved roster to game file: {target}")
        except Exception as exc:
            self._show_error("Save to game failed", exc)

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

    def _run_background(self, label: str, worker, success) -> None:
        self._set_status(f"{label}...")

        def task():
            try:
                result = worker()
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._show_error(label, error))
            else:
                self.root.after(0, lambda value=result: success(value))

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
