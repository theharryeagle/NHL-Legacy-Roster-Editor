from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zlib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.nhl_legacy_editor.tdb_access import TdbAccess  # noqa: E402
from src.nhl_legacy_editor.team_tools import load_teams, league_name_for_team  # noqa: E402


ZLIB_SECOND_BYTES = {0x01, 0x5E, 0x9C, 0xDA}


def _try_decompress_at(data: bytes, offset: int) -> tuple[bytes, int] | None:
    if offset + 2 > len(data):
        return None
    if data[offset] != 0x78 or data[offset + 1] not in ZLIB_SECOND_BYTES:
        return None
    obj = zlib.decompressobj()
    try:
        payload = obj.decompress(data[offset:])
        payload += obj.flush()
    except zlib.error:
        return None
    if not payload:
        return None
    consumed = len(data[offset:]) - len(obj.unused_data)
    if consumed <= 2:
        return None
    return payload, consumed


def discover_zlib_streams(data: bytes) -> list[dict[str, object]]:
    streams: list[dict[str, object]] = []
    offset = 0
    while offset < len(data) - 2:
        result = _try_decompress_at(data, offset)
        if result is None:
            offset += 1
            continue
        payload, consumed = result
        streams.append(
            {
                "offset": offset,
                "offset_hex": f"0x{offset:X}",
                "compressed_size": consumed,
                "decompressed_size": len(payload),
                "magic": payload[:12].hex(" "),
                "is_db": payload.startswith(b"DB\x00"),
            }
        )
        offset += max(consumed, 1)
    return streams


def write_db_streams(data: bytes, streams: list[dict[str, object]], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, stream in enumerate(streams):
        if not stream["is_db"]:
            continue
        offset = int(stream["offset"])
        payload, _consumed = _try_decompress_at(data, offset) or (b"", 0)
        if not payload:
            continue
        path = output_dir / f"dynasty_stream_{index:02d}_0x{offset:X}.db"
        path.write_bytes(payload)
        written.append(path)
    return written


def _rows_for_table(access: TdbAccess, db_path: Path, table_name: str, limit: int) -> list[dict[str, object]]:
    _table, _fields, rows = access.sample_records(db_path, table_name, limit=limit)
    return rows


def summarize_dynasty_db(db_path: Path, roster_db_path: Path | None) -> dict[str, object]:
    access = TdbAccess()
    tables = access.list_tables(db_path)
    summary: dict[str, object] = {
        "db_path": str(db_path),
        "tables": [
            {
                "index": index,
                "name": table.name,
                "field_count": table.field_count,
                "record_count": table.record_count,
                "capacity": table.capacity,
                "deleted_count": table.deleted_count,
            }
            for index, table in enumerate(tables)
        ],
    }

    dynasty_team_rows = _rows_for_table(access, db_path, "sbGR", 1000)
    dynasty_teams = {
        int(row["qEfv"]): {
            "abbrev": str(row.get("RPbr") or ""),
            "city": str(row.get("ITNQ") or ""),
            "name": str(row.get("JkmY") or ""),
        }
        for row in dynasty_team_rows
        if "qEfv" in row
    }
    summary["dynasty_team_count"] = len(dynasty_teams)
    summary["dynasty_modern_team_rows"] = {
        code: team
        for code, team in dynasty_teams.items()
        if team["abbrev"].upper() in {"VGK", "SEA", "UHC", "UTA"}
        or "VEGAS" in team["city"].upper()
        or "SEATTLE" in team["city"].upper()
        or "UTAH" in team["city"].upper()
    }

    thirty_row_tables: list[dict[str, object]] = []
    for table in tables:
        if table.record_count != 30:
            continue
        _table, fields, rows = access.sample_records(db_path, table.name, limit=30)
        field_names = [field.name for field in fields]
        qefv_codes = []
        if "qEfv" in field_names:
            qefv_codes = sorted({int(row["qEfv"]) for row in rows if row.get("qEfv") is not None})
        thirty_row_tables.append(
            {
                "name": table.name,
                "field_count": table.field_count,
                "capacity": table.capacity,
                "fields": field_names,
                "qEfv_codes": qefv_codes,
                "qEfv_abbrevs": [(code, dynasty_teams.get(code, {}).get("abbrev", "?")) for code in qefv_codes],
            }
        )
    summary["thirty_row_tables"] = thirty_row_tables

    if roster_db_path and roster_db_path.exists():
        roster_teams = load_teams(roster_db_path)
        roster_nhl = [
            {
                "code": team.code,
                "abbrev": team.abbrev,
                "city": team.city,
                "name": team.name,
            }
            for team in roster_teams
            if league_name_for_team(team) == "NHL"
        ]
        summary["roster_nhl_count"] = len(roster_nhl)
        summary["roster_modern_teams"] = [
            team
            for team in roster_nhl
            if team["abbrev"].upper() in {"VGK", "SEA", "UHC", "UTA"}
            or "VEGAS" in team["city"].upper()
            or "SEATTLE" in team["city"].upper()
            or "UTAH" in team["city"].upper()
        ]
        dynasty_abbrevs = {team["abbrev"].upper() for team in dynasty_teams.values()}
        summary["roster_nhl_missing_from_dynasty"] = [
            team for team in roster_nhl if team["abbrev"].upper() not in dynasty_abbrevs
        ]

    return summary


def write_markdown(summary: dict[str, object], output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# Dynasty Analysis Report")
    lines.append("")
    lines.append(f"DB: `{summary['db_path']}`")
    lines.append("")
    if "roster_nhl_count" in summary:
        lines.append(f"- Roster NHL-like team count: `{summary['roster_nhl_count']}`")
    lines.append(f"- Dynasty team metadata rows: `{summary['dynasty_team_count']}`")
    lines.append(f"- 30-row candidate tables: `{len(summary['thirty_row_tables'])}`")
    lines.append("")
    lines.append("## Modern Team Rows In Dynasty")
    lines.append("")
    modern = summary.get("dynasty_modern_team_rows", {})
    if modern:
        for code, team in sorted(modern.items(), key=lambda item: int(item[0])):
            lines.append(f"- `{code}` `{team['abbrev']}` {team['city']} {team['name']}")
    else:
        lines.append("- None found.")
    lines.append("")
    if "roster_modern_teams" in summary:
        lines.append("## Modern Team Rows In Roster")
        lines.append("")
        for team in summary["roster_modern_teams"]:
            lines.append(f"- `{team['code']}` `{team['abbrev']}` {team['city']} {team['name']}")
        lines.append("")
    if "roster_nhl_missing_from_dynasty" in summary:
        lines.append("## Roster NHL Teams Missing From Dynasty Metadata")
        lines.append("")
        for team in summary["roster_nhl_missing_from_dynasty"]:
            lines.append(f"- `{team['code']}` `{team['abbrev']}` {team['city']} {team['name']}")
        lines.append("")
    lines.append("## 30-Row Candidate Tables")
    lines.append("")
    for item in summary["thirty_row_tables"]:
        lines.append(
            f"- `{item['name']}` fields `{item['field_count']}`, capacity `{item['capacity']}`"
        )
        if item["qEfv_abbrevs"]:
            mapped = ", ".join(f"{code}:{abbrev}" for code, abbrev in item["qEfv_abbrevs"])
            lines.append(f"  qEfv: {mapped}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect NHL Legacy Dynasty container chunks and team tables.")
    parser.add_argument("--dynasty", type=Path, default=Path("inputs/DYNASTY 20260620125233"))
    parser.add_argument("--roster-db", type=Path, default=PROJECT_ROOT / "backups" / "roster_20260626154043.db")
    parser.add_argument("--workdir", type=Path, default=Path("working"))
    parser.add_argument("--report", type=Path, default=Path("notes/dynasty_analysis_report.md"))
    args = parser.parse_args()

    data = args.dynasty.read_bytes()
    streams = discover_zlib_streams(data)
    args.workdir.mkdir(parents=True, exist_ok=True)
    stream_json = args.workdir / "dynasty_zlib_streams.json"
    stream_json.write_text(json.dumps(streams, indent=2) + "\n", encoding="utf-8")
    db_paths = write_db_streams(data, streams, args.workdir)
    if not db_paths:
        raise SystemExit("No embedded DB streams found.")

    summary = summarize_dynasty_db(db_paths[0], args.roster_db)
    summary_path = args.workdir / "dynasty_db_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_markdown(summary, args.report)

    print(f"zlib streams: {len(streams)}")
    print(f"embedded DB streams: {len(db_paths)}")
    print(f"primary DB: {db_paths[0]}")
    print(f"summary: {summary_path}")
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
