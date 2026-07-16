$ErrorActionPreference = "Stop"

$root = "C:\Users\jesus\OneDrive\Documents\NHL legacy mods"
Set-Location $root

$path = "src\nhl_legacy_editor\desktop_app.py"
if (!(Test-Path $path)) {
    throw "Could not find $path. Run this from the NHL legacy mods folder."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = "src\nhl_legacy_editor\desktop_app.py.bak_bulk_stats_$timestamp"
Copy-Item $path $backup -Force

$script = @'
from pathlib import Path

path = Path(r"src\nhl_legacy_editor\desktop_app.py")
text = path.read_text(encoding="utf-8")

# 1) Import enhanced stats loader.
if "from .enhanced_loader import EnhancedStatsLoader" not in text:
    anchor = "from .hockeydb import fetch_hockeydb_profile_by_name\n"
    if anchor not in text:
        raise SystemExit("Could not find import anchor for hockeydb.")
    text = text.replace(anchor, anchor + "from .enhanced_loader import EnhancedStatsLoader\n")

# 2) Add app state.
if "self.stats_loader = EnhancedStatsLoader" not in text:
    old = '''        self.capwages_player = None
        self.official_player_hit = None

        self._configure_style()'''
    new = '''        self.capwages_player = None
        self.official_player_hit = None
        self.stats_loader = EnhancedStatsLoader(season=2025, min_games=20)
        self.bulk_stats_recommendations = []
        self.bulk_stats_player_lookup = {}

        self._configure_style()'''
    if old not in text:
        raise SystemExit("Could not find __init__ insertion point.")
    text = text.replace(old, new)

# 3) Replace Edge tab buttons with selected + bulk stats controls.
old_edge_tab = '''    def _build_edge_tab(self) -> None:
        top = ttk.Frame(self.edge_tab)
        top.pack(fill="x", padx=12, pady=12)
        ttk.Button(top, text="Load NHL Edge For Selected Player", style="Accent.TButton", command=self._load_edge_for_selected).pack(side="left")
        ttk.Label(top, text="Suggestions are staged into sliders only. Use Save Attributes to write them.", style="Muted.TLabel").pack(side="left", padx=12)
        self.edge_text = tk.Text(self.edge_tab, wrap="word", background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat")
        self.edge_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
'''
new_edge_tab = '''    def _build_edge_tab(self) -> None:
        top = ttk.Frame(self.edge_tab)
        top.pack(fill="x", padx=12, pady=12)
        ttk.Button(top, text="Load NHL Edge + MoneyPuck For Selected", style="Accent.TButton", command=self._load_edge_for_selected).pack(side="left")
        ttk.Button(top, text="Bulk Stats Preview (20+ GP)", command=self._bulk_stats_preview).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Apply Bulk Stats Report", command=self._apply_bulk_stats_suggestions).pack(side="left", padx=(8, 0))
        ttk.Label(top, text="Preview first, then apply. Save To Game writes the working roster back to the save target.", style="Muted.TLabel").pack(side="left", padx=12)
        self.edge_text = tk.Text(self.edge_tab, wrap="word", background="#0b1118", foreground=self.colors["ink"], insertbackground=self.colors["ink"], relief="flat")
        self.edge_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
'''
if old_edge_tab in text:
    text = text.replace(old_edge_tab, new_edge_tab)
elif "Bulk Stats Preview (20+ GP)" not in text:
    raise SystemExit("Could not replace _build_edge_tab.")

# 4) Add helper + bulk methods before _load_edge_for_selected.
if "def _bulk_stats_preview" not in text:
    insert_point = text.find("    def _load_edge_for_selected(self) -> None:")
    if insert_point == -1:
        raise SystemExit("Could not find _load_edge_for_selected insertion point.")
    methods = r'''
    def _current_attributes_by_label(self) -> dict[str, int]:
        values: dict[str, int] = {}
        for spec in SKATER_ATTRIBUTE_SPECS:
            if spec.field in self.attribute_vars:
                values[spec.label] = _safe_int(self.attribute_vars[spec.field].get(), self.attribute_original_values.get(spec.field, 0))
            elif spec.field in self.attribute_original_values:
                values[spec.label] = self.attribute_original_values.get(spec.field, 0)
        return values

    def _stats_payload_for_entry(self, entry: PlayerListEntry) -> dict[str, object]:
        position = ""
        if self.selected_player is entry and "position_label" in getattr(self, "info_vars", {}):
            position = self.info_vars["position_label"].get()
        return {
            "id": entry.player_id,
            "player_id": entry.player_id,
            "name": entry.full_name,
            "first_name": entry.first_name,
            "last_name": entry.last_name,
            "team": entry.current_team_abbrev or entry.organization_abbrev or "",
            "team_abbrev": entry.current_team_abbrev or entry.organization_abbrev or "",
            "position": position,
        }

    def _append_stats_review_text(self, text: str) -> None:
        if not hasattr(self, "edge_text"):
            return
        self.edge_text.insert("end", "\n\n" + text)

    def _bulk_stats_preview(self) -> None:
        if self.workspace is None:
            self._set_status("Open a roster before running bulk stats.")
            return
        players = [
            self._stats_payload_for_entry(entry)
            for entry in self.player_index
            if entry.league_name == "NHL" and entry.current_team_abbrev
        ]
        if not players:
            self._set_status("No NHL players found for bulk stats.")
            return
        if hasattr(self, "edge_text"):
            self.edge_text.delete("1.0", "end")
            self.edge_text.insert("1.0", f"Building bulk MoneyPuck/NHL faceoff recommendations for {len(players)} NHL players with 20+ GP...")

        def worker():
            recs = self.stats_loader.load_for_league(players)
            report = self.stats_loader.league_report_text()
            return recs, report

        def success(result):
            recs, report = result
            self.bulk_stats_recommendations = recs
            self.bulk_stats_player_lookup = {str(player["player_id"]): player for player in players}
            if hasattr(self, "edge_text"):
                self.edge_text.delete("1.0", "end")
                self.edge_text.insert("1.0", report)
                self.edge_text.insert(
                    "end",
                    "\n\nReview this report, then click Apply Bulk Stats Report to write these suggestions to the working database."
                    "\nThis bulk pass uses MoneyPuck plus cached NHL full-season faceoff totals. NHL Edge remains available for selected-player review."
                )
            updated = sum(1 for rec in recs if rec.has_changes())
            self._set_status(f"Bulk stats preview ready: {updated} players have recommendations.")

        self._run_background("Building bulk stats preview", worker, success)

    def _apply_bulk_stats_suggestions(self) -> None:
        if self.workspace is None:
            return
        if not self.bulk_stats_recommendations:
            self._set_status("Run Bulk Stats Preview first.")
            return
        recs = [rec for rec in self.bulk_stats_recommendations if rec.has_changes()]
        if not recs:
            self._set_status("No bulk stat recommendations to apply.")
            return
        if not messagebox.askyesno(
            "Apply bulk stats?",
            f"This will apply MoneyPuck/NHL faceoff recommendations to {len(recs)} players in the working roster. Continue?",
        ):
            return

        field_by_label = {spec.label: spec.field for spec in SKATER_ATTRIBUTE_SPECS}
        specs = attribute_specs_by_field()
        entry_by_id = {str(entry.player_id): entry for entry in self.player_index}

        def worker():
            applied_players = 0
            applied_attrs = 0
            skipped = []
            for rec in recs:
                entry = entry_by_id.get(str(rec.player_key))
                if entry is None:
                    skipped.append(f"{rec.player_name}: player not found in roster")
                    continue
                updates: dict[str, int] = {}
                for label, value in rec.suggestions.items():
                    field = field_by_label.get(label)
                    if not field or field not in specs:
                        continue
                    updates[field] = display_to_raw(specs[field], int(value))
                if not updates:
                    skipped.append(f"{entry.full_name}: no mapped attributes")
                    continue
                update_player_ratings(
                    self.workspace.working_db,
                    entry.first_name,
                    entry.last_name,
                    updates,
                )
                applied_players += 1
                applied_attrs += len(updates)
            sync_working_db_to_roster(self.workspace)
            return applied_players, applied_attrs, skipped[:30]

        def success(result):
            applied_players, applied_attrs, skipped = result
            self._rebuild_player_cache()
            if self.selected_player is not None:
                self.snapshot = get_player_snapshot(
                    self.player_snapshot_cache,
                    self.selected_player.first_name,
                    self.selected_player.last_name,
                )
                self._populate_attributes()
            self._refresh_review()
            if hasattr(self, "edge_text"):
                self.edge_text.insert(
                    "end",
                    f"\n\nApplied bulk stats to {applied_players} players ({applied_attrs} attribute values)."
                )
                if skipped:
                    self.edge_text.insert("end", "\n\nSkipped examples:\n" + "\n".join(skipped))
            self._set_status(f"Applied bulk stats to {applied_players} players. Use Save To Game when ready.")

        self._run_background("Applying bulk stats", worker, success)

'''
    text = text[:insert_point] + methods + text[insert_point:]

# 5) Update selected-player loader so the existing button blends NHL Edge with MoneyPuck.
old_success = '''        def success(result):
            hit, data = result
            self.official_player_hit = hit
            self.edge_suggestions, self.edge_suggestion_notes = self._build_edge_suggestions(data)
            self._refresh_attribute_edge_notes()
            self._render_edge(hit, data, self.edge_suggestions, self.edge_suggestion_notes)
            self.root.after(75, self._refresh_attribute_edge_notes)
            self._set_status(f"Loaded NHL Edge data for {hit.full_name}.")
'''
new_success = '''        def success(result):
            hit, data = result
            self.official_player_hit = hit
            edge_suggestions, edge_notes = self._build_edge_suggestions(data)
            player_payload = self._stats_payload_for_entry(self.selected_player)
            current_attributes = self._current_attributes_by_label()
            rec = self.stats_loader.load_for_selected_player(
                player_payload,
                current_attributes=current_attributes,
                edge_suggestions=edge_suggestions,
            )
            self.edge_suggestions = rec.suggestions
            self.edge_suggestion_notes = rec.notes
            for label, note in edge_notes.items():
                if label in self.edge_suggestions:
                    source = rec.sources.get(label, "")
                    if "NHL Edge" in source:
                        self.edge_suggestion_notes[label] = f"{self.edge_suggestion_notes.get(label, '')}; NHL Edge: {note}"
                else:
                    self.edge_suggestions[label] = edge_suggestions[label]
                    self.edge_suggestion_notes[label] = note
            self._refresh_attribute_edge_notes()
            self._render_edge(hit, data, self.edge_suggestions, self.edge_suggestion_notes)
            self._append_stats_review_text(self.stats_loader.review_text(rec, current_attributes=current_attributes))
            self.root.after(75, self._refresh_attribute_edge_notes)
            self._set_status(f"Loaded NHL Edge + MoneyPuck recommendations for {hit.full_name}.")
'''
if old_success in text:
    text = text.replace(old_success, new_success)
elif "Loaded NHL Edge + MoneyPuck recommendations" not in text:
    raise SystemExit("Could not update _load_edge_for_selected success block.")

path.write_text(text, encoding="utf-8")
print("desktop_app.py patched for NHL Edge + MoneyPuck selected-player loading and bulk stats preview/apply.")
'@

$script | python -

python -m py_compile .\src\nhl_legacy_editor\desktop_app.py
Write-Host "Patch complete. Backup saved as $backup"
