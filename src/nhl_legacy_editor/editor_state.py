from __future__ import annotations

import json
from pathlib import Path


def workspace_state_path(workspace, name: str) -> Path:
    return workspace.root / name


def load_json_state(workspace, name: str, default):
    path = workspace_state_path(workspace, name)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_state(workspace, name: str, value) -> Path:
    path = workspace_state_path(workspace, name)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


def default_trade_state() -> dict[str, object]:
    return {
        "team_left": "TOR",
        "team_right": "TB",
        "to_left": [],
        "to_right": [],
        "picks": [],
    }

