from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys

from .roster_formats import extract_roster_payload, replace_roster_payload, validate_rosterfile


def _frozen_data_root(executable_dir: Path, current_dir: Path, local_appdata: str | None) -> Path:
    candidates: list[Path] = []
    for start in (executable_dir, current_dir):
        candidates.extend((start, *start.parents))

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "backups" / "editor_workspaces" / "active_workspace.json").is_file():
            return resolved

    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "src" / "nhl_legacy_editor").is_dir() and (resolved / "backups").is_dir():
            return resolved

    if local_appdata:
        return Path(local_appdata).resolve() / "NHL Legacy Roster Editor"
    return executable_dir.resolve()


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return _frozen_data_root(
            Path(sys.executable).resolve().parent,
            Path.cwd().resolve(),
            os.environ.get("LOCALAPPDATA"),
        )
    return Path(__file__).resolve().parents[2]


WORKSPACES_DIR = _project_root() / "backups" / "editor_workspaces"
ACTIVE_WORKSPACE_PATH = WORKSPACES_DIR / "active_workspace.json"


@dataclass(slots=True)
class EditorWorkspace:
    name: str
    root_dir: str
    original_roster_path: str
    working_roster_path: str
    original_db_path: str
    working_db_path: str
    change_log_path: str
    created_at: str
    source_roster_path: str | None = None

    @property
    def root(self) -> Path:
        return Path(self.root_dir)

    @property
    def original_roster(self) -> Path:
        return Path(self.original_roster_path)

    @property
    def working_roster(self) -> Path:
        return Path(self.working_roster_path)

    @property
    def original_db(self) -> Path:
        return Path(self.original_db_path)

    @property
    def working_db(self) -> Path:
        return Path(self.working_db_path)

    @property
    def change_log(self) -> Path:
        return Path(self.change_log_path)

    @property
    def source_roster(self) -> Path | None:
        return None if self.source_roster_path is None else Path(self.source_roster_path)


def create_workspace(roster_path: Path) -> EditorWorkspace:
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = roster_path.parent.name.replace(" ", "_")
    root = WORKSPACES_DIR / f"{stamp}-{slug}"
    root.mkdir(parents=True, exist_ok=True)

    original_roster = root / "original_roster.bin"
    working_roster = root / "working_roster.bin"
    original_db = root / "original.db"
    working_db = root / "working.db"
    change_log = root / "changes.json"

    shutil.copy2(roster_path, original_roster)
    shutil.copy2(roster_path, working_roster)
    extract_roster_payload(original_roster, original_db)
    shutil.copy2(original_db, working_db)
    change_log.write_text("[]\n", encoding="utf-8")

    workspace = EditorWorkspace(
        name=root.name,
        root_dir=str(root),
        original_roster_path=str(original_roster),
        working_roster_path=str(working_roster),
        original_db_path=str(original_db),
        working_db_path=str(working_db),
        change_log_path=str(change_log),
        created_at=datetime.now().isoformat(),
        source_roster_path=str(roster_path),
    )
    save_active_workspace(workspace)
    return workspace


def save_active_workspace(workspace: EditorWorkspace) -> None:
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_WORKSPACE_PATH.write_text(json.dumps(asdict(workspace), indent=2) + "\n", encoding="utf-8")


def load_active_workspace() -> EditorWorkspace | None:
    if not ACTIVE_WORKSPACE_PATH.exists():
        return None
    data = json.loads(ACTIVE_WORKSPACE_PATH.read_text(encoding="utf-8"))
    data.setdefault("source_roster_path", None)
    return EditorWorkspace(**data)


def append_change_log(workspace: EditorWorkspace, entry: dict[str, object]) -> None:
    append_change_logs(workspace, [entry])


def append_change_logs(workspace: EditorWorkspace, entries: list[dict[str, object]]) -> None:
    if not entries:
        return
    if workspace.change_log.exists():
        data = json.loads(workspace.change_log.read_text(encoding="utf-8"))
    else:
        data = []
    data.extend(entries)
    workspace.change_log.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_change_log(workspace: EditorWorkspace) -> list[dict[str, object]]:
    if not workspace.change_log.exists():
        return []
    return json.loads(workspace.change_log.read_text(encoding="utf-8"))


def archive_and_clear_change_log(workspace: EditorWorkspace) -> Path | None:
    entries = read_change_log(workspace)
    if not entries:
        workspace.change_log.write_text("[]\n", encoding="utf-8")
        return None
    archive_dir = workspace.root / "review_history"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"changes-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    archive_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    workspace.change_log.write_text("[]\n", encoding="utf-8")
    return archive_path


def sync_working_db_to_roster(workspace: EditorWorkspace) -> Path:
    payload = workspace.working_db.read_bytes()
    target = workspace.working_roster
    temp = target.with_name(f"{target.name}.tmp-sync")
    if temp.exists():
        temp.unlink()
    try:
        shutil.copy2(target, temp)
        replace_roster_payload(temp, payload)
        validate_rosterfile(temp)
        os.replace(temp, target)
        validate_rosterfile(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target
