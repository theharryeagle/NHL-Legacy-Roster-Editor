from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
import sys


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        if executable_dir.name.lower() == "dist" and (executable_dir.parent / "tools").exists():
            return executable_dir.parent
        bundled_root = getattr(sys, "_MEIPASS", None)
        if bundled_root:
            return Path(bundled_root)
        return executable_dir
    return Path(__file__).resolve().parents[2]


DEFAULT_DLL_PATH = _project_root() / "tools" / "tdbaccess" / "x64" / "tdbaccess.dll"

TABLE_NAME_FALLBACK_INDEXES = {
    "ttOk": 0,   # teams
    "caBZ": 2,   # player-to-instance relations
    "ulGe": 3,   # player instances
    "vbHh": 4,   # player instance aux rows
    "ajmx": 5,   # player flags
    "yvSd": 6,   # skater ratings
    "cPbu": 7,   # player bio
    "vuqu": 8,   # small player links
    "FSzD": 9,   # wide player links
    "yuHm": 15,  # goalie ratings
}


class TdbFieldType(IntEnum):
    STRING = 0
    BINARY = 1
    SINT = 2
    UINT = 3
    FLOAT = 4
    VARCHAR = 0xD
    LONGVARCHAR = 0xE
    INT = 0x2CE


class TdbTablePropertiesStruct(ctypes.Structure):
    _fields_ = [
        ("Name", wintypes.LPWSTR),
        ("FieldCount", ctypes.c_int),
        ("Capacity", ctypes.c_int),
        ("RecordCount", ctypes.c_int),
        ("DeletedCount", ctypes.c_int),
        ("NextDeletedRecord", ctypes.c_int),
        ("Flag0", wintypes.BOOL),
        ("Flag1", wintypes.BOOL),
        ("Flag2", wintypes.BOOL),
        ("Flag3", wintypes.BOOL),
        ("NonAllocated", wintypes.BOOL),
        ("HasVarchar", wintypes.BOOL),
        ("HasCompressedVarchar", wintypes.BOOL),
    ]


class TdbFieldPropertiesStruct(ctypes.Structure):
    _fields_ = [
        ("Name", wintypes.LPWSTR),
        ("Size", ctypes.c_int),
        ("FieldType", ctypes.c_int),
    ]


@dataclass(slots=True)
class TdbTableProperties:
    name: str
    field_count: int
    capacity: int
    record_count: int
    deleted_count: int
    has_varchar: bool
    has_compressed_varchar: bool


@dataclass(slots=True)
class TdbFieldProperties:
    name: str
    size: int
    field_type: TdbFieldType | int


class TdbAccess:
    def __init__(self, dll_path: Path | None = None) -> None:
        self.dll_path = dll_path or DEFAULT_DLL_PATH
        self.dll = ctypes.WinDLL(str(self.dll_path))
        self._configure()

    def _configure(self) -> None:
        self.dll.TDBOpen.argtypes = [wintypes.LPCWSTR]
        self.dll.TDBOpen.restype = ctypes.c_int

        self.dll.TDBClose.argtypes = [ctypes.c_int]
        self.dll.TDBClose.restype = wintypes.BOOL

        self.dll.TDBSave.argtypes = [ctypes.c_int]
        self.dll.TDBSave.restype = wintypes.BOOL

        self.dll.TDBDatabaseGetTableCount.argtypes = [ctypes.c_int]
        self.dll.TDBDatabaseGetTableCount.restype = ctypes.c_int

        self.dll.TDBTableGetProperties.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(TdbTablePropertiesStruct),
        ]
        self.dll.TDBTableGetProperties.restype = wintypes.BOOL

        self.dll.TDBTableRecordAdd.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            wintypes.BOOL,
        ]
        self.dll.TDBTableRecordAdd.restype = ctypes.c_int

        self.dll.TDBTableRecordRemove.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        self.dll.TDBTableRecordRemove.restype = wintypes.BOOL

        self.dll.TDBFieldGetProperties.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(TdbFieldPropertiesStruct),
        ]
        self.dll.TDBFieldGetProperties.restype = wintypes.BOOL

        self.dll.TDBFieldGetValueAsInteger.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        self.dll.TDBFieldGetValueAsInteger.restype = ctypes.c_int

        self.dll.TDBFieldGetValueAsFloat.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        self.dll.TDBFieldGetValueAsFloat.restype = ctypes.c_float

        self.dll.TDBFieldGetValueAsString.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self.dll.TDBFieldGetValueAsString.restype = wintypes.BOOL

        self.dll.TDBFieldSetValueAsInteger.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self.dll.TDBFieldSetValueAsInteger.restype = wintypes.BOOL

        self.dll.TDBFieldSetValueAsFloat.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.c_float,
        ]
        self.dll.TDBFieldSetValueAsFloat.restype = wintypes.BOOL

        self.dll.TDBFieldSetValueAsString.argtypes = [
            ctypes.c_int,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
            wintypes.LPCWSTR,
        ]
        self.dll.TDBFieldSetValueAsString.restype = wintypes.BOOL

    @contextmanager
    def open_database(self, db_path: Path):
        db_index = self.dll.TDBOpen(str(db_path))
        if db_index < 0:
            raise RuntimeError(f"Failed to open TDB database: {db_path}")
        try:
            yield db_index
        finally:
            self.dll.TDBClose(db_index)

    def get_table_count(self, db_index: int) -> int:
        return int(self.dll.TDBDatabaseGetTableCount(db_index))

    def get_table_properties(self, db_index: int, table_index: int) -> TdbTableProperties:
        raw = TdbTablePropertiesStruct()
        name_buffer = ctypes.create_unicode_buffer(5)
        raw.Name = ctypes.cast(name_buffer, wintypes.LPWSTR)
        ok = self.dll.TDBTableGetProperties(db_index, table_index, ctypes.byref(raw))
        if not ok:
            raise RuntimeError(f"Failed to get table properties for index {table_index}")
        return TdbTableProperties(
            name=raw.Name or "",
            field_count=int(raw.FieldCount),
            capacity=int(raw.Capacity),
            record_count=int(raw.RecordCount),
            deleted_count=int(raw.DeletedCount),
            has_varchar=bool(raw.HasVarchar),
            has_compressed_varchar=bool(raw.HasCompressedVarchar),
        )

    def get_field_properties(
        self,
        db_index: int,
        table_name: str,
        field_index: int,
    ) -> TdbFieldProperties:
        raw = TdbFieldPropertiesStruct()
        name_buffer = ctypes.create_unicode_buffer(5)
        raw.Name = ctypes.cast(name_buffer, wintypes.LPWSTR)
        ok = self.dll.TDBFieldGetProperties(db_index, table_name, field_index, ctypes.byref(raw))
        if not ok:
            raise RuntimeError(f"Failed to get field properties for {table_name}[{field_index}]")
        field_type_value = int(raw.FieldType)
        try:
            field_type: TdbFieldType | int = TdbFieldType(field_type_value)
        except ValueError:
            field_type = field_type_value
        return TdbFieldProperties(
            name=raw.Name or "",
            size=int(raw.Size),
            field_type=field_type,
        )

    def list_tables(self, db_path: Path) -> list[TdbTableProperties]:
        with self.open_database(db_path) as db_index:
            return [
                self.get_table_properties(db_index, table_index)
                for table_index in range(self.get_table_count(db_index))
            ]

    def resolve_table_index(self, db_path: Path, table_ref: int | str) -> int:
        if isinstance(table_ref, int):
            return table_ref
        table_name = str(table_ref)
        tables = self.list_tables(db_path)
        for index, table in enumerate(tables):
            if table.name == table_name:
                return index
        fallback = TABLE_NAME_FALLBACK_INDEXES.get(table_name)
        if fallback is not None and 0 <= fallback < len(tables):
            return fallback
        raise RuntimeError(f"Table not found: {table_name}")

    def list_fields(self, db_path: Path, table_name: str) -> list[TdbFieldProperties]:
        with self.open_database(db_path) as db_index:
            tables = [
                self.get_table_properties(db_index, table_index)
                for table_index in range(self.get_table_count(db_index))
            ]
            table = next((item for item in tables if item.name == table_name), None)
            if table is None:
                raise RuntimeError(f"Table not found: {table_name}")
            return [
                self.get_field_properties(db_index, table_name, field_index)
                for field_index in range(table.field_count)
            ]

    def list_fields_by_index(self, db_path: Path, table_index: int | str) -> list[TdbFieldProperties]:
        table_index = self.resolve_table_index(db_path, table_index)
        with self.open_database(db_path) as db_index:
            table = self.get_table_properties(db_index, table_index)
            table_name = table.name
            return [
                self.get_field_properties(db_index, table_name, field_index)
                for field_index in range(table.field_count)
            ]

    def get_field_value(self, db_index: int, table_name: str, field: TdbFieldProperties, record_index: int):
        if field.field_type in (TdbFieldType.STRING, TdbFieldType.VARCHAR, TdbFieldType.LONGVARCHAR):
            buffer_chars = max(2, field.size // 8 + 1)
            value_buffer = ctypes.create_unicode_buffer(buffer_chars)
            ptr = ctypes.cast(value_buffer, wintypes.LPWSTR)
            ok = self.dll.TDBFieldGetValueAsString(
                db_index,
                table_name,
                field.name,
                record_index,
                ctypes.byref(ptr),
            )
            return value_buffer.value if ok else None
        if field.field_type == TdbFieldType.FLOAT:
            return float(self.dll.TDBFieldGetValueAsFloat(db_index, table_name, field.name, record_index))
        return int(self.dll.TDBFieldGetValueAsInteger(db_index, table_name, field.name, record_index))

    def sample_records(
        self,
        db_path: Path,
        table_index: int | str,
        limit: int = 5,
    ) -> tuple[TdbTableProperties, list[TdbFieldProperties], list[dict[str, object]]]:
        table_index = self.resolve_table_index(db_path, table_index)
        with self.open_database(db_path) as db_index:
            table = self.get_table_properties(db_index, table_index)
            fields = [
                self.get_field_properties(db_index, table.name, field_index)
                for field_index in range(table.field_count)
            ]
            rows: list[dict[str, object]] = []
            for record_index in range(min(limit, table.record_count)):
                row: dict[str, object] = {}
                for field in fields:
                    row[field.name] = self.get_field_value(db_index, table.name, field, record_index)
                rows.append(row)
            return table, fields, rows

    def save_database(self, db_index: int) -> None:
        ok = self.dll.TDBSave(db_index)
        if not ok:
            raise RuntimeError("Failed to save TDB database.")

    def add_record(self, db_index: int, table_name: str, *, allow_expand: bool = True) -> int:
        record_index = int(self.dll.TDBTableRecordAdd(db_index, table_name, bool(allow_expand)))
        if record_index < 0:
            raise RuntimeError(f"Failed to add a record to {table_name}.")
        return record_index

    def remove_record(self, db_index: int, table_name: str, record_index: int) -> None:
        ok = self.dll.TDBTableRecordRemove(db_index, table_name, record_index)
        if not ok:
            raise RuntimeError(f"Failed to remove {table_name} record {record_index}.")

    def copy_record_fields(
        self,
        db_index: int,
        table_name: str,
        source: dict[str, object],
        record_index: int,
        *,
        overrides: dict[str, object] | None = None,
    ) -> None:
        table_index = next(
            (
                index
                for index in range(self.get_table_count(db_index))
                if self.get_table_properties(db_index, index).name == table_name
            ),
            None,
        )
        if table_index is None:
            raise RuntimeError(f"Table not found: {table_name}")
        table = self.get_table_properties(db_index, table_index)
        fields = {
            field.name: field
            for field in (
                self.get_field_properties(db_index, table_name, field_index)
                for field_index in range(table.field_count)
            )
        }
        values = dict(source)
        values.update(overrides or {})
        for field_name, field in fields.items():
            if field_name not in values or values[field_name] is None:
                continue
            self.set_field_value(db_index, table_name, field, record_index, values[field_name])

    def set_field_value(
        self,
        db_index: int,
        table_name: str,
        field: TdbFieldProperties,
        record_index: int,
        value: object,
    ) -> None:
        if field.field_type in (TdbFieldType.STRING, TdbFieldType.VARCHAR, TdbFieldType.LONGVARCHAR):
            ok = self.dll.TDBFieldSetValueAsString(
                db_index,
                table_name,
                field.name,
                record_index,
                str(value),
            )
        elif field.field_type == TdbFieldType.FLOAT:
            ok = self.dll.TDBFieldSetValueAsFloat(
                db_index,
                table_name,
                field.name,
                record_index,
                float(value),
            )
        else:
            ok = self.dll.TDBFieldSetValueAsInteger(
                db_index,
                table_name,
                field.name,
                record_index,
                int(value),
            )
        if not ok:
            raise RuntimeError(
                f"Failed to set {table_name}.{field.name} at record {record_index} to {value!r}."
            )

    def update_record_fields(
        self,
        db_path: Path,
        table_index: int | str,
        record_index: int,
        updates: dict[str, object],
    ) -> None:
        table_index = self.resolve_table_index(db_path, table_index)
        with self.open_database(db_path) as db_index:
            table = self.get_table_properties(db_index, table_index)
            fields = {
                field.name: field
                for field in (
                    self.get_field_properties(db_index, table.name, field_index)
                    for field_index in range(table.field_count)
                )
            }
            for field_name, value in updates.items():
                field = fields.get(field_name)
                if field is None:
                    raise RuntimeError(f"Field not found: {table.name}.{field_name}")
                self.set_field_value(db_index, table.name, field, record_index, value)
            self.save_database(db_index)
