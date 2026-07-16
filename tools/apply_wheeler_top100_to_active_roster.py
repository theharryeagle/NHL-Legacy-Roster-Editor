from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import unicodedata

from src.nhl_legacy_editor.attribute_map import SKATER_ATTRIBUTE_SPECS, display_to_raw, raw_to_display
from src.nhl_legacy_editor.draft_class import POTENTIAL_COLOR_CODES, POTENTIAL_STAR_CODES
from src.nhl_legacy_editor.player_tools import build_player_snapshot_cache
from src.nhl_legacy_editor.roster_formats import replace_roster_payload, validate_rosterfile
from src.nhl_legacy_editor.tdb_access import TdbAccess
from src.nhl_legacy_editor.workspace import append_change_logs, load_active_workspace


ROSTER_NAME_ALIASES = {
    "Axel Sandin-Pellikka": "Axel Sandin-Pellika",
    "Will Horcoff": "William Horcoff",
    "Ike Howard": "Isaac Howard",
}

SEMANTIC_TO_LABEL = {
    "speed": "Speed",
    "body_checking": "Body Checking",
    "endurance": "Endurance",
    "puck_control": "Puck Control",
    "passing": "Passing",
    "slap_shot_power": "Slap Shot Power",
    "slap_shot_accuracy": "Slap Shot Accuracy",
    "wrist_shot_power": "Wrist Shot Power",
    "wrist_shot_accuracy": "Wrist Shot Accuracy",
    "agility": "Agility",
    "strength": "Strength",
    "acceleration": "Acceleration",
    "balance": "Balance",
    "faceoffs": "Face-offs",
    "durability": "Durability",
    "deking": "Deking",
    "aggressiveness": "Aggressiveness",
    "poise": "Poise",
    "hand_eye": "Hand-Eye",
    "shot_blocking": "Shot Blocking",
    "offensive_awareness": "Off. Awareness",
    "defensive_awareness": "Def. Awareness",
    "discipline": "Discipline",
    "fighting_skill": "Fighting Skill",
    "stick_checking": "Stick Checking",
}

STAR_CODE_TO_VALUE = {code: stars for stars, code in POTENTIAL_STAR_CODES.items()}
COLOR_CODE_TO_VALUE = {code: color for color, code in POTENTIAL_COLOR_CODES.items()}


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in folded if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]", "", ascii_value.lower())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_replace(source: Path, target: Path, *, attempts: int = 20) -> None:
    last_error: OSError | None = None
    for _attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.25)
    assert last_error is not None
    raise last_error


def _validate_manifest(payload: dict[str, object]) -> list[dict[str, object]]:
    prospects = list(payload.get("prospects") or [])
    if len(prospects) != 100:
        raise RuntimeError(f"The scouting manifest must contain 100 players; found {len(prospects)}.")
    ranks = [int(row["rank"]) for row in prospects]
    if ranks != list(range(1, 101)):
        raise RuntimeError("The scouting manifest does not contain a complete rank sequence from 1 through 100.")
    for row in prospects:
        stars = float(row["potential_stars"])
        color = str(row["potential_color"])
        name = str(row["name"])
        if stars < 3.5:
            raise RuntimeError(f"Potential floor violation for {name}: {stars} {color}")
        if stars == 5.0 and name != "Gavin McKenna":
            raise RuntimeError(f"Only Gavin McKenna may receive 5-star potential: {name}")
        if stars > 4.5 and name != "Gavin McKenna":
            raise RuntimeError(f"Non-McKenna potential ceiling violation for {name}: {stars} {color}")
        if name != "Gavin McKenna" and stars == 4.5 and color == "Green":
            pass
        for semantic, delta in dict(row.get("modifiers") or {}).items():
            if semantic not in SEMANTIC_TO_LABEL:
                raise RuntimeError(f"Unknown attribute '{semantic}' for {name}.")
            if not -2 <= int(delta) <= 3:
                raise RuntimeError(f"Unsafe attribute delta for {name} {semantic}: {delta}")
    return prospects


def _build_plans(db_path: Path, prospects: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    cache = build_player_snapshot_cache(db_path)
    bios_by_name: dict[str, list[dict[str, object]]] = {}
    for bio in cache.bio_rows:
        full_name = f"{bio.get('PedH') or ''} {bio.get('RMbQ') or ''}".strip()
        if full_name:
            bios_by_name.setdefault(_normalized(full_name), []).append(bio)

    rating_index_by_player_id = {
        int(row.get("zIBw") if row.get("zIBw") is not None else -1): index
        for index, row in enumerate(cache.ratings_rows)
    }
    spec_by_label = {spec.label: spec for spec in SKATER_ATTRIBUTE_SPECS}
    plans: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    for prospect in prospects:
        article_name = str(prospect["name"]).strip()
        roster_name = ROSTER_NAME_ALIASES.get(article_name, article_name)
        candidates = bios_by_name.get(_normalized(roster_name), [])
        if len(candidates) != 1:
            skipped.append(
                {
                    "rank": int(prospect["rank"]),
                    "player": article_name,
                    "roster_name": roster_name,
                    "reason": "missing" if not candidates else "ambiguous duplicate bios",
                    "candidate_player_ids": [int(row.get("zIBw") or -1) for row in candidates],
                }
            )
            continue

        bio = candidates[0]
        player_id = int(bio.get("zIBw") if bio.get("zIBw") is not None else -1)
        row_index = rating_index_by_player_id.get(player_id)
        current_row = cache.ratings_by_player_id.get(player_id)
        if row_index is None or current_row is None:
            skipped.append(
                {
                    "rank": int(prospect["rank"]),
                    "player": article_name,
                    "roster_name": roster_name,
                    "player_id": player_id,
                    "reason": "skater ratings row missing",
                }
            )
            continue

        updates: dict[str, int] = {}
        changes: list[dict[str, object]] = []
        for semantic, delta_value in dict(prospect.get("modifiers") or {}).items():
            label = SEMANTIC_TO_LABEL[str(semantic)]
            spec = spec_by_label[label]
            before_raw = int(current_row.get(spec.field) or 0)
            before_display = raw_to_display(spec, before_raw)
            delta = int(delta_value)
            after_display = max(
                spec.min_value if spec.mode == "raw" else 36,
                min(spec.max_value, before_display + delta),
            )
            after_raw = display_to_raw(spec, after_display)
            if after_raw == before_raw:
                continue
            updates[spec.field] = after_raw
            changes.append(
                {
                    "section": "ratings",
                    "field": spec.field,
                    "attribute": label,
                    "before": before_raw,
                    "after": after_raw,
                    "before_display": before_display,
                    "after_display": after_display,
                    "delta": after_display - before_display,
                }
            )

        stars = float(prospect["potential_stars"])
        color = str(prospect["potential_color"])
        potential_updates = {
            "AMoQ": POTENTIAL_STAR_CODES[stars],
            "feBm": POTENTIAL_COLOR_CODES[color],
        }
        for field, after_raw in potential_updates.items():
            before_raw = int(current_row.get(field) or 0)
            updates[field] = after_raw
            if before_raw != after_raw:
                changes.append(
                    {
                        "section": "potential",
                        "field": field,
                        "before": before_raw,
                        "after": after_raw,
                    }
                )

        plans.append(
            {
                "rank": int(prospect["rank"]),
                "tier": int(prospect["tier"]),
                "player": article_name,
                "roster_name": roster_name,
                "player_id": player_id,
                "row_index": row_index,
                "team": str(prospect.get("team") or ""),
                "position": str(prospect.get("position") or ""),
                "projection": str(prospect.get("projection_label") or prospect.get("projection") or ""),
                "strengths": list(prospect.get("strengths") or []),
                "weaknesses": list(prospect.get("weaknesses") or []),
                "potential": f"{stars:.1f} {color}",
                "potential_reason": str(prospect.get("potential_reason") or ""),
                "potential_before": (
                    f"{STAR_CODE_TO_VALUE.get(int(current_row.get('AMoQ') or 0), '?')} "
                    f"{COLOR_CODE_TO_VALUE.get(int(current_row.get('feBm') or 0), 'Unknown')}"
                ),
                "updates": updates,
                "changes": changes,
            }
        )
    return plans, skipped


def _write_plans(db_path: Path, plans: list[dict[str, object]]) -> None:
    access = TdbAccess()
    table_indexes = {table.name: index for index, table in enumerate(access.list_tables(db_path))}
    ratings_table_name = "yvSd"
    if ratings_table_name not in table_indexes:
        raise RuntimeError("The NHL Legacy skater ratings table was not found.")
    with access.open_database(db_path) as db_index:
        table = access.get_table_properties(db_index, table_indexes[ratings_table_name])
        fields = {
            field.name: field
            for field in (
                access.get_field_properties(db_index, ratings_table_name, field_index)
                for field_index in range(table.field_count)
            )
        }
        for plan in plans:
            row_index = int(plan["row_index"])
            for field_name, value in dict(plan["updates"]).items():
                field = fields.get(field_name)
                if field is None:
                    raise RuntimeError(f"Roster ratings field not found: {field_name}")
                access.set_field_value(db_index, ratings_table_name, field, row_index, int(value))
        access.save_database(db_index)


def _verify_plans(db_path: Path, plans: list[dict[str, object]]) -> None:
    cache = build_player_snapshot_cache(db_path)
    errors: list[str] = []
    for plan in plans:
        player_id = int(plan["player_id"])
        row = cache.ratings_by_player_id.get(player_id)
        if row is None:
            errors.append(f"{plan['player']}: ratings row disappeared")
            continue
        for field, expected in dict(plan["updates"]).items():
            actual = int(row.get(field) or 0)
            if actual != int(expected):
                errors.append(f"{plan['player']} {field}: expected {expected}, read {actual}")
    if errors:
        raise RuntimeError("Read-back verification failed:\n" + "\n".join(errors[:20]))


def _log_results(workspace, plans: list[dict[str, object]], source: str) -> None:
    timestamp = datetime.now().isoformat()
    append_change_logs(
        workspace,
        [
            {
                "timestamp": timestamp,
                "type": "prospect-scouting-top-100-2026",
                "result": {
                    "player": plan["roster_name"],
                    "player_id": plan["player_id"],
                    "rank": plan["rank"],
                    "tier": plan["tier"],
                    "projection": plan["projection"],
                    "potential_before": plan["potential_before"],
                    "potential": plan["potential"],
                    "strengths": plan["strengths"],
                    "weaknesses": plan["weaknesses"],
                    "source": source,
                    "changes": plan["changes"],
                },
            }
            for plan in plans
            if plan["changes"]
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the derived summer 2026 Top 100 scouting pass once.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/wheeler_top100_2026.json"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workspace-only", action="store_true")
    args = parser.parse_args()

    workspace = load_active_workspace()
    if workspace is None:
        raise RuntimeError("No active NHL Legacy editor workspace was found.")
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    prospects = _validate_manifest(payload)
    plans, skipped = _build_plans(workspace.working_db, prospects)
    changed_plans = [plan for plan in plans if plan["changes"]]
    print(
        f"Audit: {len(plans)} unique roster matches, {len(changed_plans)} with changes, "
        f"{len(skipped)} skipped."
    )
    if skipped:
        for row in skipped:
            print(f"SKIP #{row['rank']} {row['player']}: {row['reason']}")
    if len(plans) < 100 or skipped:
        raise RuntimeError("The one-time update requires 100 unique roster matches; no roster data was changed.")
    if args.dry_run:
        potential_counts: dict[str, int] = {}
        for plan in plans:
            potential_counts[str(plan["potential"])] = potential_counts.get(str(plan["potential"]), 0) + 1
        print("Potential distribution:", json.dumps(potential_counts, sort_keys=True))
        print("Dry run complete; no files were changed.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = workspace.root / "prospect_scouting_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    db_backup = backup_dir / f"before-wheeler-top100-{stamp}.db"
    roster_backup = backup_dir / f"before-wheeler-top100-{stamp}.roster"
    shutil.copy2(workspace.working_db, db_backup)
    shutil.copy2(workspace.working_roster, roster_backup)

    temp_db = workspace.root / f"wheeler-top100-{stamp}.tmp.db"
    temp_roster = workspace.root / f"wheeler-top100-{stamp}.tmp.roster"
    shutil.copy2(workspace.working_db, temp_db)
    shutil.copy2(workspace.working_roster, temp_roster)
    original_db_size = workspace.working_db.stat().st_size

    source_backup: Path | None = None
    try:
        _write_plans(temp_db, plans)
        _verify_plans(temp_db, plans)
        if temp_db.stat().st_size != original_db_size:
            raise RuntimeError(
                f"Fixed roster DB size changed from {original_db_size} to {temp_db.stat().st_size} bytes."
            )
        replace_roster_payload(temp_roster, temp_db.read_bytes())
        validate_rosterfile(temp_roster)

        _atomic_replace(temp_db, workspace.working_db)
        _atomic_replace(temp_roster, workspace.working_roster)
        _verify_plans(workspace.working_db, plans)
        validate_rosterfile(workspace.working_roster)

        source = workspace.source_roster
        if not args.workspace_only:
            if source is None or not source.exists() or source.is_dir():
                raise RuntimeError(f"The configured game roster target is invalid: {source}")
            source_backup = source.with_name(f"{source.name}.bak-prospects-{stamp}")
            shutil.copy2(source, source_backup)
            game_temp = source.with_name(f"{source.name}.tmp-prospects-{stamp}")
            shutil.copy2(workspace.working_roster, game_temp)
            validate_rosterfile(game_temp)
            _atomic_replace(game_temp, source)
            validate_rosterfile(source)
            if _sha256(source) != _sha256(workspace.working_roster):
                raise RuntimeError("The game roster does not match the verified working roster after saving.")

        _log_results(workspace, plans, str(payload.get("source") or ""))
        report_path = workspace.root / f"wheeler-top100-report-{stamp}.json"
        report_path.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now().isoformat(),
                    "source": payload.get("source"),
                    "workspace": workspace.name,
                    "game_roster": None if args.workspace_only else str(workspace.source_roster),
                    "working_db_backup": str(db_backup),
                    "working_roster_backup": str(roster_backup),
                    "game_roster_backup": str(source_backup) if source_backup else None,
                    "players_matched": len(plans),
                    "players_changed": len(changed_plans),
                    "players_skipped": skipped,
                    "players": [
                        {key: value for key, value in plan.items() if key not in {"updates", "row_index"}}
                        for plan in plans
                    ],
                    "working_roster_sha256": _sha256(workspace.working_roster),
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Applied scouting changes to {len(changed_plans)} players.")
        print(f"Working roster: {workspace.working_roster}")
        if not args.workspace_only:
            print(f"Game roster: {workspace.source_roster}")
        print(f"Report: {report_path}")
        print(f"Backup: {roster_backup}")
    finally:
        for temp in (temp_db, temp_roster):
            if temp.exists():
                temp.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
