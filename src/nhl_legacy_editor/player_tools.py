from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tdb_access import TdbAccess


TEAM_TABLE_INDEX = "ttOk"
PLAYER_RELATION_TABLE_INDEX = "caBZ"
PLAYER_BIO_TABLE_INDEX = "cPbu"
PLAYER_FLAGS_TABLE_INDEX = "ajmx"
PLAYER_RATINGS_TABLE_INDEX = "yvSd"
GOALIE_RATINGS_TABLE_INDEX = "yuHm"
PLAYER_SMALL_LINK_TABLE_INDEX = "vuqu"
PLAYER_WIDE_LINK_TABLE_INDEX = "FSzD"
PLAYER_INSTANCE_TABLE_INDEX = "ulGe"
PLAYER_INSTANCE_AUX_TABLE_INDEX = "vbHh"


@dataclass(slots=True)
class PlayerSnapshot:
    bio: dict[str, object]
    relation_rows: list[dict[str, object]]
    instance_rows: list[dict[str, object]]
    instance_aux_rows: list[dict[str, object]]
    flags_row: dict[str, object] | None
    ratings_row: dict[str, object] | None
    goalie_ratings_row: dict[str, object] | None
    small_link_rows: list[dict[str, object]]
    wide_link_rows: list[dict[str, object]]


@dataclass(slots=True)
class PlayerSnapshotCache:
    db_path: Path
    bio_rows: list[dict[str, object]]
    relation_rows: list[dict[str, object]]
    instance_rows: list[dict[str, object]]
    instance_aux_rows: list[dict[str, object]]
    flags_rows: list[dict[str, object]]
    ratings_rows: list[dict[str, object]]
    goalie_ratings_rows: list[dict[str, object]]
    small_link_rows: list[dict[str, object]]
    wide_link_rows: list[dict[str, object]]
    bio_by_name: dict[tuple[str, str], dict[str, object]]
    bio_by_player_id: dict[object, dict[str, object]]
    relation_by_player_key: dict[object, list[dict[str, object]]]
    instance_by_id: dict[object, dict[str, object]]
    instance_aux_by_id: dict[object, list[dict[str, object]]]
    flags_by_player_id: dict[object, dict[str, object]]
    ratings_by_player_id: dict[object, dict[str, object]]
    goalie_ratings_by_player_id: dict[object, dict[str, object]]
    small_links_by_long_id: dict[object, list[dict[str, object]]]
    wide_links_by_long_id: dict[object, list[dict[str, object]]]

    def get_player_snapshot(
        self,
        first_name: str,
        last_name: str,
        player_id: int | None = None,
    ) -> PlayerSnapshot | None:
        bio = self.bio_by_player_id.get(player_id) if player_id is not None else None
        if bio is None:
            bio = self.bio_by_name.get((first_name, last_name))
        if bio is None:
            return None
        player_id = bio.get("zIBw")
        long_id = bio.get("DaPp")
        relation_rows = list(self.relation_by_player_key.get(player_id, []))
        if long_id != player_id:
            relation_rows.extend(
                row
                for row in self.relation_by_player_key.get(long_id, [])
                if row not in relation_rows
            )
        instance_ids = {row.get("qEfv") for row in relation_rows}
        return PlayerSnapshot(
            bio=bio,
            relation_rows=relation_rows,
            instance_rows=[
                row
                for instance_id in instance_ids
                for row in [self.instance_by_id.get(instance_id)]
                if row is not None
            ],
            instance_aux_rows=[
                row
                for instance_id in instance_ids
                for row in self.instance_aux_by_id.get(instance_id, [])
            ],
            flags_row=self.flags_by_player_id.get(player_id),
            ratings_row=self.ratings_by_player_id.get(player_id),
            goalie_ratings_row=self.goalie_ratings_by_player_id.get(player_id),
            small_link_rows=list(self.small_links_by_long_id.get(long_id, [])),
            wide_link_rows=list(self.wide_links_by_long_id.get(long_id, [])),
        )


def _table_rows(
    access: TdbAccess,
    db_path: Path,
    table_counts: list[int],
    table_index: int | str,
) -> list[dict[str, object]]:
    resolved_index = access.resolve_table_index(db_path, table_index)
    _table, _fields, rows = access.sample_records(
        db_path,
        table_index,
        limit=table_counts[resolved_index],
    )
    return rows


def _append_lookup(
    lookup: dict[object, list[dict[str, object]]],
    key: object,
    row: dict[str, object],
) -> None:
    if key is None:
        return
    lookup.setdefault(key, []).append(row)


def build_player_snapshot_cache(db_path: Path) -> PlayerSnapshotCache:
    access = TdbAccess()
    tables = access.list_tables(db_path)
    table_counts = [table.record_count for table in tables]
    bio_rows = _table_rows(access, db_path, table_counts, PLAYER_BIO_TABLE_INDEX)
    relation_rows = _table_rows(access, db_path, table_counts, PLAYER_RELATION_TABLE_INDEX)
    instance_rows = _table_rows(access, db_path, table_counts, PLAYER_INSTANCE_TABLE_INDEX)
    instance_aux_rows = _table_rows(access, db_path, table_counts, PLAYER_INSTANCE_AUX_TABLE_INDEX)
    flags_rows = _table_rows(access, db_path, table_counts, PLAYER_FLAGS_TABLE_INDEX)
    ratings_rows = _table_rows(access, db_path, table_counts, PLAYER_RATINGS_TABLE_INDEX)
    goalie_ratings_rows = _table_rows(access, db_path, table_counts, GOALIE_RATINGS_TABLE_INDEX)
    small_link_rows = _table_rows(access, db_path, table_counts, PLAYER_SMALL_LINK_TABLE_INDEX)
    wide_link_rows = _table_rows(access, db_path, table_counts, PLAYER_WIDE_LINK_TABLE_INDEX)

    bio_by_name = {
        (str(row.get("PedH") or "").strip(), str(row.get("RMbQ") or "").strip()): row
        for row in bio_rows
    }
    relation_by_player_key: dict[object, list[dict[str, object]]] = {}
    for row in relation_rows:
        _append_lookup(relation_by_player_key, row.get("qFky"), row)
    instance_by_id = {row.get("TWSX"): row for row in instance_rows if row.get("TWSX") is not None}
    instance_aux_by_id: dict[object, list[dict[str, object]]] = {}
    for row in instance_aux_rows:
        _append_lookup(instance_aux_by_id, row.get("qEfv"), row)
    flags_by_player_id = {row.get("zIBw"): row for row in flags_rows if row.get("zIBw") is not None}
    ratings_by_player_id = {row.get("zIBw"): row for row in ratings_rows if row.get("zIBw") is not None}
    goalie_ratings_by_player_id = {
        row.get("zIBw"): row
        for row in goalie_ratings_rows
        if row.get("zIBw") is not None
    }
    bio_by_player_id = {
        row.get("zIBw"): row
        for row in bio_rows
        if row.get("zIBw") is not None
    }
    small_links_by_long_id: dict[object, list[dict[str, object]]] = {}
    for row in small_link_rows:
        _append_lookup(small_links_by_long_id, row.get("DaPp"), row)
    wide_links_by_long_id: dict[object, list[dict[str, object]]] = {}
    for row in wide_link_rows:
        _append_lookup(wide_links_by_long_id, row.get("DaPp"), row)

    return PlayerSnapshotCache(
        db_path=db_path,
        bio_rows=bio_rows,
        relation_rows=relation_rows,
        instance_rows=instance_rows,
        instance_aux_rows=instance_aux_rows,
        flags_rows=flags_rows,
        ratings_rows=ratings_rows,
        goalie_ratings_rows=goalie_ratings_rows,
        small_link_rows=small_link_rows,
        wide_link_rows=wide_link_rows,
        bio_by_name=bio_by_name,
        bio_by_player_id=bio_by_player_id,
        relation_by_player_key=relation_by_player_key,
        instance_by_id=instance_by_id,
        instance_aux_by_id=instance_aux_by_id,
        flags_by_player_id=flags_by_player_id,
        ratings_by_player_id=ratings_by_player_id,
        goalie_ratings_by_player_id=goalie_ratings_by_player_id,
        small_links_by_long_id=small_links_by_long_id,
        wide_links_by_long_id=wide_links_by_long_id,
    )


def get_player_snapshot_by_query(db_path: Path, query: str) -> PlayerSnapshot | None:
    matches = find_players(db_path, query)
    if not matches:
        return None
    first = matches[0]
    return get_player_snapshot(db_path, str(first["PedH"]), str(first["RMbQ"]))


def _read_full_table(access: TdbAccess, db_path: Path, table_index: int | str) -> list[dict[str, object]]:
    resolved_index = access.resolve_table_index(db_path, table_index)
    _table, _fields, rows = access.sample_records(
        db_path,
        table_index,
        limit=access.list_tables(db_path)[resolved_index].record_count,
    )
    return rows


def find_players(db_path: Path, query: str) -> list[dict[str, object]]:
    access = TdbAccess()
    rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    query_lower = query.lower()
    matches = []
    for row in rows:
        full_name = f"{row.get('PedH', '')} {row.get('RMbQ', '')}".strip()
        if query_lower in full_name.lower():
            matches.append(row)
    return matches


def get_player_snapshot(
    db_path: Path,
    first_name: str,
    last_name: str,
    player_id: int | None = None,
) -> PlayerSnapshot | None:
    access = TdbAccess()
    bio_rows = _read_full_table(access, db_path, PLAYER_BIO_TABLE_INDEX)
    bio = next(
        (
            row
            for row in bio_rows
            if (
                int(row.get("zIBw") or -1) == player_id
                if player_id is not None
                else row.get("PedH") == first_name and row.get("RMbQ") == last_name
            )
        ),
        None,
    )
    if bio is None:
        return None

    player_id = bio.get("zIBw")
    long_id = bio.get("DaPp")

    relation_rows = [
        row
        for row in _read_full_table(access, db_path, PLAYER_RELATION_TABLE_INDEX)
        if row.get("qFky") in {player_id, long_id}
    ]
    instance_ids = {row.get("qEfv") for row in relation_rows}
    instance_rows = [
        row
        for row in _read_full_table(access, db_path, PLAYER_INSTANCE_TABLE_INDEX)
        if row.get("TWSX") in instance_ids
    ]
    instance_aux_rows = [
        row
        for row in _read_full_table(access, db_path, PLAYER_INSTANCE_AUX_TABLE_INDEX)
        if row.get("qEfv") in instance_ids
    ]
    flags_row = next(
        (row for row in _read_full_table(access, db_path, PLAYER_FLAGS_TABLE_INDEX) if row.get("zIBw") == player_id),
        None,
    )
    ratings_row = next(
        (row for row in _read_full_table(access, db_path, PLAYER_RATINGS_TABLE_INDEX) if row.get("zIBw") == player_id),
        None,
    )
    goalie_ratings_row = next(
        (row for row in _read_full_table(access, db_path, GOALIE_RATINGS_TABLE_INDEX) if row.get("zIBw") == player_id),
        None,
    )
    small_link_rows = [
        row
        for row in _read_full_table(access, db_path, PLAYER_SMALL_LINK_TABLE_INDEX)
        if row.get("DaPp") == long_id
    ]
    wide_link_rows = [
        row
        for row in _read_full_table(access, db_path, PLAYER_WIDE_LINK_TABLE_INDEX)
        if row.get("DaPp") == long_id
    ]

    return PlayerSnapshot(
        bio=bio,
        relation_rows=relation_rows,
        instance_rows=instance_rows,
        instance_aux_rows=instance_aux_rows,
        flags_row=flags_row,
        ratings_row=ratings_row,
        goalie_ratings_row=goalie_ratings_row,
        small_link_rows=small_link_rows,
        wide_link_rows=wide_link_rows,
    )
