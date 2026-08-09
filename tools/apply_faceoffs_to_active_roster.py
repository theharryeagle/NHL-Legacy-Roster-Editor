from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from nhl_legacy_editor.attribute_map import SKATER_ATTRIBUTE_SPECS, display_to_raw, raw_to_display
from nhl_legacy_editor.attribute_mapper import faceoff_effective_percentage, faceoff_rating_from_percentage
from nhl_legacy_editor.player_editing import update_many_player_ratings
from nhl_legacy_editor.player_tools import build_player_snapshot_cache
from nhl_legacy_editor.roster_formats import extract_roster_payload_bytes, replace_roster_payload, validate_rosterfile
from nhl_legacy_editor.roster_sync import normalize_name
from nhl_legacy_editor.workspace import ACTIVE_WORKSPACE_PATH, load_active_workspace


FORWARD_POSITION_CODES = {0, 1, 2}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def atomic_copy(source: Path, target: Path) -> None:
    temporary = target.with_name(f"{target.name}.faceoff-copy.tmp")
    if temporary.exists():
        raise RuntimeError(f"Refusing to overwrite stale temporary file: {temporary}")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def int_or(value: object, default: int = -1) -> int:
    return int(value) if value is not None else default


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the calibrated faceoff curve to the active Xenia roster.")
    parser.add_argument("--apply", action="store_true", help="Write the verified update; otherwise perform a dry run.")
    args = parser.parse_args()

    workspace = load_active_workspace()
    if workspace is None:
        raise RuntimeError(f"No active editor workspace at {ACTIVE_WORKSPACE_PATH}")
    if not workspace.source_roster_path:
        raise RuntimeError("The active workspace has no Xenia source roster path.")

    live_roster = Path(workspace.source_roster_path).resolve()
    working_roster = workspace.working_roster.resolve()
    working_db = workspace.working_db.resolve()
    for path in (live_roster, working_roster, working_db):
        if not path.is_file():
            raise FileNotFoundError(path)
    validate_rosterfile(live_roster)
    validate_rosterfile(working_roster)
    if sha256(live_roster) != sha256(working_roster):
        raise RuntimeError("The active editor roster and Xenia roster differ; reload the game file before applying.")

    faceoff_path = PROJECT_ROOT / "data" / "nhl" / "faceoffs_20252026.json"
    nhl_rows = json.loads(faceoff_path.read_text(encoding="utf-8"))["data"]
    qualified: list[tuple[float, float, object]] = []
    for row in nhl_rows:
        position = str(row.get("positionCode") or "").upper()
        won = row.get("totalFaceoffWins")
        taken = row.get("totalFaceoffs")
        _raw, effective, _participation = faceoff_effective_percentage(won, taken, position)
        if position != "D" and effective is not None and taken is not None and float(taken) >= 200:
            qualified.append((effective, float(taken), row.get("playerId")))
    qualified.sort(key=lambda item: (-item[0], -item[1], f"id:{item[2]}"))
    rank_by_nhl_id = {player_id: rank for rank, (_pct, _taken, player_id) in enumerate(qualified, start=1)}

    nhl_by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in nhl_rows:
        nhl_by_name[normalize_name(str(row.get("skaterFullName") or ""))].append(row)

    cache = build_player_snapshot_cache(working_db)
    faceoff_spec = next(spec for spec in SKATER_ATTRIBUTE_SPECS if spec.label == "Face-offs")
    planned: list[dict[str, object]] = []
    skipped_ambiguous: list[str] = []
    for bio in cache.bio_rows:
        roster_position = int_or(bio.get("aljv"))
        if roster_position not in FORWARD_POSITION_CODES:
            continue
        player_id = int_or(bio.get("zIBw"))
        ratings = cache.ratings_by_player_id.get(player_id)
        if ratings is None:
            continue
        first_name = str(bio.get("PedH") or "").strip()
        last_name = str(bio.get("RMbQ") or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        # A same-name NHL defenseman cannot be the match for a roster forward.
        matches = [
            row
            for row in nhl_by_name.get(normalize_name(full_name), [])
            if str(row.get("positionCode") or "").upper() != "D"
        ]
        if len(matches) != 1:
            if len(matches) > 1:
                skipped_ambiguous.append(full_name)
            continue
        match = matches[0]
        won = match.get("totalFaceoffWins")
        taken = match.get("totalFaceoffs")
        rank = rank_by_nhl_id.get(match.get("playerId"))
        target, raw_percentage = faceoff_rating_from_percentage(
            won,
            taken,
            str(match.get("positionCode") or ""),
            league_rank=rank,
        )
        current = raw_to_display(faceoff_spec, int_or(ratings.get(faceoff_spec.field), 0))
        planned.append(
            {
                "player": full_name,
                "player_id": player_id,
                "first_name": first_name,
                "last_name": last_name,
                "before": current,
                "after": target,
                "wins": won,
                "draws": taken,
                "raw_percentage": raw_percentage,
                "league_rank": rank,
            }
        )

    changed = [row for row in planned if row["before"] != row["after"]]
    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "live_roster": str(live_roster),
        "source_sha256": sha256(live_roster),
        "matched_forwards": len(planned),
        "changed_forwards": len(changed),
        "unchanged_forwards": len(planned) - len(changed),
        "skipped_ambiguous": skipped_ambiguous,
        "examples": [
            row
            for row in planned
            if row["player"] in {
                "Sidney Crosby",
                "Leon Draisaitl",
                "Auston Matthews",
                "Scott Laughton",
                "William Nylander",
                "Shane Wright",
                "Connor Bedard",
            }
        ],
    }
    if not args.apply:
        print(json.dumps(summary, indent=2))
        return 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = PROJECT_ROOT / "backups" / "mapping_probes" / datetime.now().strftime("%Y%m%d") / f"faceoffs_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(live_roster, backup_dir / "xenia_roster_before.bin")
    shutil.copy2(working_roster, backup_dir / "working_roster_before.bin")
    shutil.copy2(working_db, backup_dir / "working_db_before.db")
    shutil.copy2(ACTIVE_WORKSPACE_PATH, backup_dir / "active_workspace_before.json")
    if workspace.change_log.is_file():
        shutil.copy2(workspace.change_log, backup_dir / "changes_before.json")

    staged_db = backup_dir / "working_db_after.db"
    staged_roster = live_roster.with_name(f"{live_roster.name}.faceoff-update.tmp")
    if staged_roster.exists():
        raise RuntimeError(f"Refusing to overwrite stale staged roster: {staged_roster}")
    shutil.copy2(working_db, staged_db)
    staged_cache = build_player_snapshot_cache(staged_db)
    updates = [
        (
            str(row["first_name"]),
            str(row["last_name"]),
            {faceoff_spec.field: display_to_raw(faceoff_spec, int(row["after"]))},
            int(row["player_id"]),
        )
        for row in changed
    ]
    update_many_player_ratings(staged_db, updates, snapshot_cache=staged_cache)

    try:
        shutil.copy2(live_roster, staged_roster)
        replace_roster_payload(staged_roster, staged_db.read_bytes())
        validate_rosterfile(staged_roster)
        _offset, embedded_db = extract_roster_payload_bytes(staged_roster)
        staged_bytes = staged_db.read_bytes()
        if embedded_db[: len(staged_bytes)] != staged_bytes:
            raise RuntimeError("The staged roster does not contain the verified edited database.")

        atomic_copy(staged_db, working_db)
        atomic_copy(staged_roster, working_roster)
        atomic_copy(staged_roster, live_roster)
    finally:
        if staged_roster.exists():
            staged_roster.unlink()

    validate_rosterfile(live_roster)
    validate_rosterfile(working_roster)
    if sha256(live_roster) != sha256(working_roster):
        raise RuntimeError("Post-write validation failed: Xenia and editor roster hashes differ.")
    summary["backup_dir"] = str(backup_dir)
    summary["result_sha256"] = sha256(live_roster)
    (backup_dir / "faceoff_update_report.json").write_text(json.dumps({**summary, "changes": changed}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
