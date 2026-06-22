from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .archive_tools import list_archive_matches
from .comparison_tools import build_comparison_blend
from .contract_models import DEFAULT_REAL_CAP_MILLIONS, scale_contract_by_cap_percentage, scale_contract_by_percent
from .db_probe import extract_ascii_strings, find_text_hits
from .hockeydb import fetch_hockeydb_profile_by_name, search_hockeydb_player
from .move_tools import get_player_current_team, move_player_to_team
from .nhl_remote import export_rosters_to_csv, fetch_team_roster, fetch_trade_headlines, find_player_on_official_rosters
from .player_tools import find_players, get_player_snapshot, get_player_snapshot_by_query
from .rating_models import ARCHETYPE_WEIGHTS, plan_rating_upgrade
from .review_tools import diff_player_between_dbs
from .roster_formats import extract_roster_payload, inspect_file
from .tdb_access import TdbAccess
from .workspace import create_workspace, load_active_workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nhl-legacy-editor",
        description="Inspect NHL Legacy Xbox 360 roster containers.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inspect a possible Xbox 360 roster save/container.",
    )
    inspect_parser.add_argument("file", type=Path, help="Path to the roster container.")

    extract_parser = subparsers.add_parser(
        "extract-db",
        help="Extract the decompressed roster payload from a RosterFile.",
    )
    extract_parser.add_argument("file", type=Path, help="Path to the roster file.")
    extract_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output path for the extracted payload.",
    )

    big_list_parser = subparsers.add_parser(
        "big-list",
        help="List contents of an NHL Legacy EA .big archive via QuickBMS.",
    )
    big_list_parser.add_argument("file", type=Path, help="Path to the .big archive.")
    big_list_parser.add_argument(
        "--filter",
        action="append",
        default=[],
        help="Case-insensitive path filter, repeat as needed.",
    )

    workspace_open_parser = subparsers.add_parser(
        "workspace-open",
        help="Create a working editor workspace from a Xenia roster file.",
    )
    workspace_open_parser.add_argument("file", type=Path, help="Path to the roster file.")

    workspace_info_parser = subparsers.add_parser(
        "workspace-info",
        help="Show the active editor workspace.",
    )

    player_team_parser = subparsers.add_parser(
        "player-team",
        help="Infer the current team assignment for a player from the working DB.",
    )
    player_team_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    player_team_parser.add_argument("first_name", help="Exact first name.")
    player_team_parser.add_argument("last_name", help="Exact last name.")

    move_player_parser = subparsers.add_parser(
        "move-player",
        help="Move a player between NHL teams by editing the inferred primary instance row.",
    )
    move_player_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    move_player_parser.add_argument("first_name", help="Exact first name.")
    move_player_parser.add_argument("last_name", help="Exact last name.")
    move_player_parser.add_argument("team", help="Target team abbreviation, for example TOR.")

    compare_parser = subparsers.add_parser(
        "compare-build",
        help="Blend comparison players into a target prospect plan.",
    )
    compare_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    compare_parser.add_argument("target_name", help="Name label for the created player/prospect.")
    compare_parser.add_argument("archetype", choices=sorted(ARCHETYPE_WEIGHTS), help="Target archetype.")
    compare_parser.add_argument("target_overall", type=int, help="Target overall cap.")
    compare_parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="Comparison player full name, repeat as needed.",
    )

    app_parser = subparsers.add_parser(
        "app",
        help="Run the local NHL Legacy roster editor web app.",
    )
    app_parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    app_parser.add_argument("--port", type=int, default=8765, help="Bind port.")

    subparsers.add_parser(
        "desktop",
        help="Run the native desktop NHL Legacy roster editor.",
    )

    roster_parser = subparsers.add_parser(
        "fetch-roster",
        help="Fetch the current official NHL roster for a team from api-web.nhle.com.",
    )
    roster_parser.add_argument("team", help="Team abbreviation, for example TOR or EDM.")
    roster_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional CSV output path.",
    )

    trades_parser = subparsers.add_parser(
        "fetch-trades",
        help="Fetch recent trade headlines from NHL.com trade coverage.",
    )
    trades_parser.add_argument("--limit", type=int, default=10, help="Number of headlines to return.")

    plan_parser = subparsers.add_parser(
        "plan-overall",
        help="Plan weighted stat upgrades toward a target overall.",
    )
    plan_parser.add_argument("archetype", choices=sorted(ARCHETYPE_WEIGHTS), help="Player archetype.")
    plan_parser.add_argument("target_overall", type=int, help="Desired overall cap/target.")
    plan_parser.add_argument(
        "--stat",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Current stat value, repeat as needed.",
    )

    find_text_parser = subparsers.add_parser(
        "find-text",
        help="Search the extracted DB for readable text and show nearby printable context.",
    )
    find_text_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    find_text_parser.add_argument("needle", help="Text to search for.")
    find_text_parser.add_argument(
        "--context",
        type=int,
        default=64,
        help="Bytes of printable context to show on each side.",
    )

    strings_parser = subparsers.add_parser(
        "dump-strings",
        help="Dump readable ASCII strings from an extracted DB file.",
    )
    strings_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    strings_parser.add_argument("--min-length", type=int, default=6, help="Minimum string length.")
    strings_parser.add_argument("--limit", type=int, help="Maximum number of strings to print.")

    tables_parser = subparsers.add_parser(
        "tdb-tables",
        help="List tables from an extracted TDB database using Artem's TDBAccess DLL.",
    )
    tables_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")

    fields_parser = subparsers.add_parser(
        "tdb-fields",
        help="List fields for a TDB table using Artem's TDBAccess DLL.",
    )
    fields_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    fields_parser.add_argument("table", help="Exact table name or numeric table index.")

    sample_parser = subparsers.add_parser(
        "tdb-sample",
        help="Print sample records from a TDB table by numeric index.",
    )
    sample_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    sample_parser.add_argument("table_index", type=int, help="Numeric table index.")
    sample_parser.add_argument("--limit", type=int, default=3, help="Number of records to sample.")

    player_find_parser = subparsers.add_parser(
        "player-find",
        help="Find matching players in the discovered bio table.",
    )
    player_find_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    player_find_parser.add_argument("query", help="Case-insensitive name fragment.")

    player_snapshot_parser = subparsers.add_parser(
        "player-snapshot",
        help="Show linked discovered rows for a player by exact first and last name.",
    )
    player_snapshot_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    player_snapshot_parser.add_argument("first_name", help="Exact first name.")
    player_snapshot_parser.add_argument("last_name", help="Exact last name.")

    player_review_parser = subparsers.add_parser(
        "player-review",
        help="Review before/after changes for a player between two extracted DBs.",
    )
    player_review_parser.add_argument("before_file", type=Path, help="Path to the original extracted DB file.")
    player_review_parser.add_argument("after_file", type=Path, help="Path to the edited extracted DB file.")
    player_review_parser.add_argument("first_name", help="Exact first name.")
    player_review_parser.add_argument("last_name", help="Exact last name.")

    hockeydb_find_parser = subparsers.add_parser(
        "hockeydb-find",
        help="Search HockeyDB player listings by name.",
    )
    hockeydb_find_parser.add_argument("query", help="Player name, for example 'Gavin McKenna'.")

    hockeydb_profile_parser = subparsers.add_parser(
        "hockeydb-profile",
        help="Fetch a HockeyDB profile summary by player name.",
    )
    hockeydb_profile_parser.add_argument("query", help="Player name, for example 'Connor McDavid'.")

    transaction_casefile_parser = subparsers.add_parser(
        "transaction-casefile",
        help="Show a combined live/offline casefile for a player while mapping roster edits.",
    )
    transaction_casefile_parser.add_argument("file", type=Path, help="Path to an extracted DB file.")
    transaction_casefile_parser.add_argument("query", help="Player name fragment, for example 'Darren Raddysh'.")

    contract_scale_parser = subparsers.add_parser(
        "contract-scale",
        help="Scale a real-world contract cap hit into the NHL Legacy in-game cap environment.",
    )
    contract_scale_parser.add_argument("player_name", help="Player name label for the calculation.")
    contract_scale_parser.add_argument("--game-cap", type=float, required=True, help="In-game salary cap in millions.")
    contract_scale_parser.add_argument(
        "--real-cap",
        type=float,
        default=DEFAULT_REAL_CAP_MILLIONS,
        help="Real NHL salary cap upper limit in millions. Defaults to 104.0 for 2026-27.",
    )
    contract_scale_parser.add_argument("--real-aav", type=float, help="Real-world cap hit / AAV in millions.")
    contract_scale_parser.add_argument(
        "--cap-hit-percent",
        type=float,
        help="Cap hit percentage as a decimal, for example 0.14 for 14%%.",
    )
    return parser


def cmd_inspect(file_path: Path) -> int:
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1

    result = inspect_file(file_path)
    print(f"File: {result.path}")
    print(f"Size: {result.size_bytes} bytes")
    print(f"Header magic: {result.header_magic!r}")
    print(f"File kind: {result.file_kind}")

    if result.package_type:
        print(f"Package type: {result.package_type}")
    if result.roster_markers is not None:
        if result.roster_markers:
            print("Roster markers:")
            for marker in result.roster_markers[:20]:
                print(f"  - {marker}")
        else:
            print("Roster markers: none found")
    if result.compression_offset is not None:
        print(f"Compression offset: 0x{result.compression_offset:X}")
    if result.decompressed_size is not None:
        print(f"Decompressed payload size: {result.decompressed_size} bytes")
    if result.payload_magic:
        print(f"Payload magic: {result.payload_magic!r}")

    return 0


def cmd_extract_db(file_path: Path, output_path: Path | None) -> int:
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1

    target = output_path or file_path.with_suffix(file_path.suffix + ".db")
    try:
        written = extract_roster_payload(file_path, target)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Extracted payload to: {written}")
    return 0


def cmd_big_list(file_path: Path, filters: list[str]) -> int:
    try:
        entries = list_archive_matches(file_path, filters=filters)
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for entry in entries:
        print(json.dumps(entry))
    return 0


def cmd_workspace_open(file_path: Path) -> int:
    workspace = create_workspace(file_path)
    print(json.dumps(asdict(workspace), indent=2))
    return 0


def cmd_workspace_info() -> int:
    workspace = load_active_workspace()
    if workspace is None:
        print("No active workspace.", file=sys.stderr)
        return 1
    print(json.dumps(asdict(workspace), indent=2))
    return 0


def cmd_player_team(file_path: Path, first_name: str, last_name: str) -> int:
    result = get_player_current_team(file_path, first_name, last_name)
    print(json.dumps(result, indent=2))
    return 0


def cmd_move_player(file_path: Path, first_name: str, last_name: str, team: str) -> int:
    result = move_player_to_team(file_path, first_name, last_name, team)
    print(json.dumps(result, indent=2))
    return 0


def cmd_compare_build(
    file_path: Path,
    target_name: str,
    source_names: list[str],
    archetype: str,
    target_overall: int,
) -> int:
    result = build_comparison_blend(
        db_path=file_path,
        target_name=target_name,
        source_names=source_names,
        archetype=archetype,
        target_overall=target_overall,
    )
    print(
        json.dumps(
            {
                "target_name": result.target_name,
                "archetype": result.archetype,
                "target_overall": result.target_overall,
                "source_players": result.source_players,
                "blended_ratings": result.blended_ratings,
                "upgraded_ratings": result.upgraded_ratings,
                "estimated_overall_before_cap": result.estimated_overall_before_cap,
                "estimated_overall_after_cap": result.estimated_overall_after_cap,
            },
            indent=2,
        )
    )
    return 0


def cmd_fetch_roster(team: str, output_path: Path | None) -> int:
    players = fetch_team_roster(team)
    if output_path:
        written = export_rosters_to_csv(players, output_path)
        print(f"Wrote {len(players)} players to: {written}")
        return 0

    for player in players:
        print(
            json.dumps(
                {
                    "player_id": player.player_id,
                    "team": player.team_abbrev,
                    "name": player.full_name,
                    "position": player.position_code,
                    "shoots_catches": player.shoots_catches,
                    "sweater_number": player.sweater_number,
                }
            )
        )
    return 0


def cmd_fetch_trades(limit: int) -> int:
    headlines = fetch_trade_headlines(limit=limit)
    for headline in headlines:
        print(
            json.dumps(
                {
                    "title": headline.title,
                    "published_at": None if headline.published_at is None else headline.published_at.isoformat(),
                    "url": headline.url,
                }
            )
        )
    return 0


def _parse_stat_pairs(pairs: list[str]) -> dict[str, int]:
    stats: dict[str, int] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid stat pair: {pair}")
        name, raw_value = pair.split("=", 1)
        stats[name.strip()] = int(raw_value.strip())
    return stats


def cmd_plan_overall(archetype: str, target_overall: int, stat_pairs: list[str]) -> int:
    try:
        ratings = _parse_stat_pairs(stat_pairs)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    plan = plan_rating_upgrade(ratings, archetype, target_overall)
    print(
        json.dumps(
            {
                "archetype": plan.archetype,
                "current_overall": plan.current_overall,
                "target_overall": plan.target_overall,
                "points_used": plan.points_used,
                "weighted_delta_used": round(plan.weighted_delta_used, 2),
                "weighted_delta_remaining": round(plan.weighted_delta_remaining, 2),
                "suggested_ratings": plan.suggested_ratings,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_find_text(file_path: Path, needle: str, context_bytes: int) -> int:
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1

    hits = find_text_hits(file_path, needle, context_bytes=context_bytes)
    if not hits:
        print(f"No hits for: {needle}")
        return 0

    for hit in hits:
        print(f"0x{hit.offset:X}: {hit.text}")
        print(hit.context)
        print("---")
    return 0


def cmd_dump_strings(file_path: Path, min_length: int, limit: int | None) -> int:
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1

    for offset, text in extract_ascii_strings(file_path, min_length=min_length, limit=limit):
        print(f"0x{offset:X} {text}")
    return 0


def cmd_tdb_tables(file_path: Path) -> int:
    access = TdbAccess()
    for index, table in enumerate(access.list_tables(file_path)):
        print(
            json.dumps(
                {
                    "index": index,
                    "name": table.name,
                    "field_count": table.field_count,
                    "record_count": table.record_count,
                    "capacity": table.capacity,
                    "deleted_count": table.deleted_count,
                    "has_varchar": table.has_varchar,
                    "has_compressed_varchar": table.has_compressed_varchar,
                }
            )
        )
    return 0


def cmd_tdb_fields(file_path: Path, table_name: str) -> int:
    access = TdbAccess()
    if table_name.isdigit():
        fields = access.list_fields_by_index(file_path, int(table_name))
    else:
        fields = access.list_fields(file_path, table_name)
    for index, field in enumerate(fields):
        print(
            json.dumps(
                {
                    "index": index,
                    "name": field.name,
                    "size": field.size,
                    "field_type": field.field_type.name if hasattr(field.field_type, "name") else field.field_type,
                }
            )
        )
    return 0


def cmd_tdb_sample(file_path: Path, table_index: int, limit: int) -> int:
    access = TdbAccess()
    table, fields, rows = access.sample_records(file_path, table_index, limit=limit)
    print(
        json.dumps(
            {
                "table_index": table_index,
                "table_name": table.name,
                "field_count": table.field_count,
                "record_count": table.record_count,
                "fields": [field.name for field in fields],
                "rows": rows,
            },
            indent=2,
        )
    )
    return 0


def cmd_player_find(file_path: Path, query: str) -> int:
    matches = find_players(file_path, query)
    for row in matches:
        print(
            json.dumps(
                {
                    "first_name": row.get("PedH"),
                    "last_name": row.get("RMbQ"),
                    "city": row.get("JzFM"),
                    "player_id": row.get("zIBw"),
                    "long_id": row.get("DaPp"),
                }
            )
        )
    return 0


def cmd_player_snapshot(file_path: Path, first_name: str, last_name: str) -> int:
    snapshot = get_player_snapshot(file_path, first_name, last_name)
    if snapshot is None:
        print(f"Player not found: {first_name} {last_name}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "bio": snapshot.bio,
                "relation_rows": snapshot.relation_rows,
                "instance_rows": snapshot.instance_rows,
                "instance_aux_rows": snapshot.instance_aux_rows,
                "flags_row": snapshot.flags_row,
                "ratings_row": snapshot.ratings_row,
                "small_link_rows": snapshot.small_link_rows,
                "wide_link_rows": snapshot.wide_link_rows,
            },
            indent=2,
        )
    )
    return 0


def cmd_player_review(before_file: Path, after_file: Path, first_name: str, last_name: str) -> int:
    changes = diff_player_between_dbs(before_file, after_file, first_name, last_name)
    if changes is None:
        print(f"Player not found in one or both DBs: {first_name} {last_name}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            [
                {
                    "section": change.section,
                    "field": change.field,
                    "before": change.before,
                    "after": change.after,
                }
                for change in changes
            ],
            indent=2,
        )
    )
    return 0


def cmd_hockeydb_find(query: str) -> int:
    for result in search_hockeydb_player(query):
        print(
            json.dumps(
                {
                    "name": result.name,
                    "pid": result.pid,
                    "url": result.url,
                }
            )
        )
    return 0


def cmd_hockeydb_profile(query: str) -> int:
    profile = fetch_hockeydb_profile_by_name(query)
    if profile is None:
        print(f"No HockeyDB match found for: {query}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "name": profile.name,
                "url": profile.url,
                "position": profile.position,
                "shoots": profile.shoots,
                "born": profile.born,
                "birthplace": profile.birthplace,
                "height": profile.height,
                "weight": profile.weight,
                "draft_info": profile.draft_info,
            },
            indent=2,
        )
    )
    return 0


def cmd_transaction_casefile(file_path: Path, query: str) -> int:
    official_hits = find_player_on_official_rosters(query)
    local_snapshot = get_player_snapshot_by_query(file_path, query)

    try:
        hockeydb_profile = fetch_hockeydb_profile_by_name(query)
    except Exception:
        hockeydb_profile = None

    print(
        json.dumps(
            {
                "query": query,
                "official_roster_hits": [
                    {
                        "name": hit.full_name,
                        "team": hit.team_abbrev,
                        "position": hit.position_code,
                        "shoots_catches": hit.shoots_catches,
                        "sweater_number": hit.sweater_number,
                    }
                    for hit in official_hits
                ],
                "hockeydb_profile": None
                if hockeydb_profile is None
                else {
                    "name": hockeydb_profile.name,
                    "position": hockeydb_profile.position,
                    "shoots": hockeydb_profile.shoots,
                    "born": hockeydb_profile.born,
                    "birthplace": hockeydb_profile.birthplace,
                    "height": hockeydb_profile.height,
                    "weight": hockeydb_profile.weight,
                    "draft_info": hockeydb_profile.draft_info,
                    "url": hockeydb_profile.url,
                },
                "local_snapshot": None
                if local_snapshot is None
                else {
                    "bio": local_snapshot.bio,
                    "relation_rows": local_snapshot.relation_rows,
                    "instance_rows": local_snapshot.instance_rows,
                    "instance_aux_rows": local_snapshot.instance_aux_rows,
                    "flags_row": local_snapshot.flags_row,
                    "ratings_row": local_snapshot.ratings_row,
                },
            },
            indent=2,
        )
    )
    return 0


def cmd_contract_scale(
    player_name: str,
    game_cap: float,
    real_cap: float,
    real_aav: float | None,
    cap_hit_percent: float | None,
) -> int:
    if real_aav is None and cap_hit_percent is None:
        print("Provide either --real-aav or --cap-hit-percent.", file=sys.stderr)
        return 2
    if real_aav is not None and cap_hit_percent is not None:
        print("Provide only one of --real-aav or --cap-hit-percent.", file=sys.stderr)
        return 2

    if real_aav is not None:
        result = scale_contract_by_cap_percentage(
            player_name=player_name,
            real_aav_millions=real_aav,
            game_cap_millions=game_cap,
            real_cap_millions=real_cap,
        )
    else:
        result = scale_contract_by_percent(
            player_name=player_name,
            cap_hit_percent=float(cap_hit_percent),
            game_cap_millions=game_cap,
            real_cap_millions=real_cap,
        )

    print(
        json.dumps(
            {
                "player_name": result.player_name,
                "real_cap_millions": result.real_cap_millions,
                "game_cap_millions": result.game_cap_millions,
                "real_aav_millions": round(result.real_aav_millions, 4),
                "cap_hit_percent": round(result.cap_hit_percent * 100, 4),
                "scaled_aav_millions": round(result.scaled_aav_millions, 4),
                "scaled_aav_dollars": result.scaled_aav_dollars,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "inspect":
        return cmd_inspect(args.file)
    if args.command == "extract-db":
        return cmd_extract_db(args.file, args.output)
    if args.command == "big-list":
        return cmd_big_list(args.file, args.filter)
    if args.command == "workspace-open":
        return cmd_workspace_open(args.file)
    if args.command == "workspace-info":
        return cmd_workspace_info()
    if args.command == "fetch-roster":
        return cmd_fetch_roster(args.team, args.output)
    if args.command == "fetch-trades":
        return cmd_fetch_trades(args.limit)
    if args.command == "plan-overall":
        return cmd_plan_overall(args.archetype, args.target_overall, args.stat)
    if args.command == "find-text":
        return cmd_find_text(args.file, args.needle, args.context)
    if args.command == "dump-strings":
        return cmd_dump_strings(args.file, args.min_length, args.limit)
    if args.command == "tdb-tables":
        return cmd_tdb_tables(args.file)
    if args.command == "tdb-fields":
        return cmd_tdb_fields(args.file, args.table)
    if args.command == "tdb-sample":
        return cmd_tdb_sample(args.file, args.table_index, args.limit)
    if args.command == "player-find":
        return cmd_player_find(args.file, args.query)
    if args.command == "player-snapshot":
        return cmd_player_snapshot(args.file, args.first_name, args.last_name)
    if args.command == "player-team":
        return cmd_player_team(args.file, args.first_name, args.last_name)
    if args.command == "move-player":
        return cmd_move_player(args.file, args.first_name, args.last_name, args.team)
    if args.command == "player-review":
        return cmd_player_review(args.before_file, args.after_file, args.first_name, args.last_name)
    if args.command == "hockeydb-find":
        return cmd_hockeydb_find(args.query)
    if args.command == "hockeydb-profile":
        return cmd_hockeydb_profile(args.query)
    if args.command == "transaction-casefile":
        return cmd_transaction_casefile(args.file, args.query)
    if args.command == "contract-scale":
        return cmd_contract_scale(
            args.player_name,
            args.game_cap,
            args.real_cap,
            args.real_aav,
            args.cap_hit_percent,
        )
    if args.command == "compare-build":
        return cmd_compare_build(
            args.file,
            args.target_name,
            args.source,
            args.archetype,
            args.target_overall,
        )
    if args.command == "app":
        from .web_app import run_app

        run_app(host=args.host, port=args.port)
        return 0
    if args.command == "desktop":
        from .desktop_app import main as run_desktop

        run_desktop()
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
