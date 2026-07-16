from pathlib import Path

from nhl_legacy_editor.workspace import _frozen_data_root


def test_frozen_data_root_finds_project_above_nested_build(tmp_path: Path) -> None:
    project = tmp_path / "project"
    executable_dir = project / "dist" / "preview"
    executable_dir.mkdir(parents=True)
    active = project / "backups" / "editor_workspaces" / "active_workspace.json"
    active.parent.mkdir(parents=True)
    active.write_text("{}", encoding="utf-8")

    assert _frozen_data_root(executable_dir, tmp_path / "elsewhere", None) == project


def test_frozen_data_root_uses_local_appdata_for_standalone_exe(tmp_path: Path) -> None:
    executable_dir = tmp_path / "desktop"
    executable_dir.mkdir()
    local_appdata = tmp_path / "local"

    assert _frozen_data_root(executable_dir, tmp_path / "elsewhere", str(local_appdata)) == (
        local_appdata / "NHL Legacy Roster Editor"
    )
