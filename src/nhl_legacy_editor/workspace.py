from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

from .roster_formats import extract_roster_payload, replace_roster_payload


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        if executable_dir.name.lower() == "dist" and (executable_dir.parent / "backups").exists():
            return executable_dir.parent
        return executable_dir
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
    if workspace.change_log.exists():
        data = json.loads(workspace.change_log.read_text(encoding="utf-8"))
    else:
        data = []
    data.append(entry)
    workspace.change_log.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_change_log(workspace: EditorWorkspace) -> list[dict[str, object]]:
    if not workspace.change_log.exists():
        return []
    return json.loads(workspace.change_log.read_text(encoding="utf-8"))


def sync_working_db_to_roster(workspace: EditorWorkspace) -> Path:
    payload = workspace.working_db.read_bytes()
    return replace_roster_payload(workspace.working_roster, payload)
