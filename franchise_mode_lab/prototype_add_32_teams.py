from __future__ import annotations

import argparse
import shutil
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.nhl_legacy_editor.tdb_access import TdbAccess  # noqa: E402


TEAM_OVERRIDES = {
    "VGK": {
        "source_code": 226,
        "active_slot": 30,
        "abbrev": "VGK",
        "city": "LAS VEGAS",
        "name": "GOLDEN KNIGHTS",
    },
    "SEA": {
        "source_code": 228,
        "active_slot": 31,
        "abbrev": "SEA",
        "city": "SEATTLE",
        "name": "KRAKEN",
    },
}


def _all_rows(access: TdbAccess, db_path: Path, table_name: str) -> list[dict[str, object]]:
    tables = access.list_tables(db_path)
    table = next(item for item in tables if item.name == table_name)
    _table, _fields, rows = access.sample_records(db_path, table_name, limit=table.record_count)
    return rows


def _field_map(access: TdbAccess, db_index: int, table_name: str):
    table_index = next(
        index
        for index in range(access.get_table_count(db_index))
        if access.get_table_properties(db_index, index).name == table_name
    )
    table = access.get_table_properties(db_index, table_index)
    return {
        field.name: field
        for field in (
            access.get_field_properties(db_index, table_name, field_index)
            for field_index in range(table.field_count)
        )
    }


def _write_values(access: TdbAccess, db_index: int, table_name: str, record_index: int, values: dict[str, object]) -> None:
    fields = _field_map(access, db_index, table_name)
    for field_name, value in values.items():
        field = fields.get(field_name)
        if field is None:
            continue
        access.set_field_value(db_index, table_name, field, record_index, value)


def build_probe(input_db: Path, roster_db: Path, output_db: Path) -> None:
    output_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_db, output_db)

    access = TdbAccess()
    roster_team_rows = _all_rows(access, roster_db, "ttOk")
    roster_by_code = {int(row["qEfv"]): row for row in roster_team_rows}
    dynasty_team_rows = _all_rows(access, output_db, "sbGR")
    dynasty_by_abbrev = {str(row.get("RPbr") or "").upper(): row for row in dynasty_team_rows}
    dynasty_index_by_code = {int(row["qEfv"]): index for index, row in enumerate(dynasty_team_rows)}
    active_rows = _all_rows(access, output_db, "xLuM")
    standings_rows = _all_rows(access, output_db, "RZbd")

    # Utah is the closest modern row already present in the dynasty DB. It keeps unknown
    # presentation fields safer than cloning an unrelated historical team.
    sbgr_template = dynasty_by_abbrev.get("UHC") or dynasty_team_rows[0]
    xlum_template = next(row for row in active_rows if int(row.get("qEfv") or -1) == 22)
    rzbd_template = standings_rows[22] if len(standings_rows) > 22 else standings_rows[-1]

    with access.open_database(output_db) as db_index:
        for item in TEAM_OVERRIDES.values():
            roster_source = roster_by_code[item["source_code"]]

            # The active franchise tables use 5-bit team indexes, so the new NHL clubs
            # must occupy slots 30 and 31. In this probe, we overwrite the existing
            # dynasty metadata rows for those slots instead of adding unreachable rows.
            sbgr_index = dynasty_index_by_code[item["active_slot"]]
            sbgr_values = dict(sbgr_template)
            for field_name in ("AmXZ", "AuWq", "CuYS", "FbOU", "FvEq", "KMGC", "LSre", "MKjg", "QWUE", "QjbN", "QmTT"):
                if field_name in roster_source:
                    sbgr_values[field_name] = roster_source[field_name]
            sbgr_values.update(
                {
                    "qEfv": item["active_slot"],
                    "RPbr": item["abbrev"],
                    "nnsx": item["abbrev"],
                    "ITNQ": item["city"],
                    "JkmY": item["name"],
                    "UUTA": 30000 + item["active_slot"],
                    "Nzao": item["active_slot"],
                    "XZtS": item["active_slot"],
                    "aDub": item["active_slot"],
                    "rnOl": item["active_slot"],
                    "gXCm": item["active_slot"],
                }
            )
            access.copy_record_fields(db_index, "sbGR", sbgr_values, sbgr_index)

            xlum_index = access.add_record(db_index, "xLuM", allow_expand=True)
            xlum_values = dict(xlum_template)
            xlum_values.update(
                {
                    "qEfv": item["active_slot"],
                    "PedH": item["city"].replace(" ", "_")[:16],
                    "RMbQ": item["name"][:16],
                    "uKDH": item["active_slot"],
                }
            )
            access.copy_record_fields(db_index, "xLuM", xlum_values, xlum_index)

            rzbd_index = access.add_record(db_index, "RZbd", allow_expand=True)
            rzbd_values = dict(rzbd_template)
            rzbd_values.update({"BSXd": item["active_slot"]})
            access.copy_record_fields(db_index, "RZbd", rzbd_values, rzbd_index)

        access.save_database(db_index)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prototype a DB-only 32-team Dynasty table expansion.")
    parser.add_argument("--input-db", type=Path, default=Path("working/dynasty_stream_00_0x30.db"))
    parser.add_argument("--roster-db", type=Path, default=PROJECT_ROOT / "backups" / "roster_20260626154043.db")
    parser.add_argument("--output-db", type=Path, default=Path("working/dynasty_32team_probe.db"))
    args = parser.parse_args()
    build_probe(args.input_db, args.roster_db, args.output_db)
    print(f"wrote {args.output_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
