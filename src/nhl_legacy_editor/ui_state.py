from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Hashable, Iterable, Mapping, Sequence, TypeVar


Row = TypeVar("Row")
Identifier = Hashable


@dataclass(frozen=True)
class SortKey:
    column: str
    descending: bool = False

    @classmethod
    def from_setting(cls, value: object) -> "SortKey | None":
        if not isinstance(value, Mapping):
            return None
        column = str(value.get("column") or "").strip()
        if not column:
            return None
        return cls(column=column, descending=bool(value.get("descending", False)))

    def to_setting(self) -> dict[str, object]:
        return {"column": self.column, "descending": self.descending}


@dataclass
class MultiSortState:
    keys: list[SortKey] = field(default_factory=list)
    max_keys: int = 2

    def __post_init__(self) -> None:
        self.keys = self._normalized(self.keys)

    def _normalized(self, keys: Iterable[SortKey]) -> list[SortKey]:
        normalized: list[SortKey] = []
        seen: set[str] = set()
        for key in keys:
            if not key.column or key.column in seen:
                continue
            normalized.append(key)
            seen.add(key.column)
            if len(normalized) >= self.max_keys:
                break
        return normalized

    @classmethod
    def from_setting(
        cls,
        value: object,
        *,
        default: Sequence[SortKey] = (),
        allowed_columns: Iterable[str] | None = None,
        max_keys: int = 2,
    ) -> "MultiSortState":
        allowed = set(allowed_columns) if allowed_columns is not None else None
        parsed: list[SortKey] = []
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                key = SortKey.from_setting(item)
                if key is not None and (allowed is None or key.column in allowed):
                    parsed.append(key)
        return cls(parsed or list(default), max_keys=max_keys)

    def to_setting(self) -> list[dict[str, object]]:
        return [key.to_setting() for key in self.keys]

    def set_keys(self, keys: Iterable[SortKey]) -> None:
        self.keys = self._normalized(keys)

    def quick_sort(self, column: str, *, descending_default: bool = False) -> None:
        if self.keys and self.keys[0].column == column:
            self.keys = [SortKey(column, not self.keys[0].descending)]
        else:
            self.keys = [SortKey(column, descending_default)]

    def indicator(self, column: str) -> str:
        for index, key in enumerate(self.keys, start=1):
            if key.column == column:
                return f"{index}{'▼' if key.descending else '▲'}"
        return ""

    def sort_rows(
        self,
        rows: Iterable[Row],
        value_for: Callable[[Row, str], object],
        stable_value_for: Callable[[Row], object],
    ) -> list[Row]:
        result = list(rows)
        result.sort(key=lambda row: _sortable(stable_value_for(row)))
        for key in reversed(self.keys):
            result.sort(
                key=lambda row, column=key.column: _sortable(value_for(row, column)),
                reverse=key.descending,
            )
        return result


@dataclass
class SelectionState:
    checked: set[Identifier] = field(default_factory=set)

    def toggle(self, identifier: Identifier) -> bool:
        if identifier in self.checked:
            self.checked.remove(identifier)
            return False
        self.checked.add(identifier)
        return True

    def select_visible(self, identifiers: Iterable[Identifier]) -> None:
        self.checked.update(identifiers)

    def clear(self) -> None:
        self.checked.clear()

    def discard(self, identifiers: Iterable[Identifier]) -> None:
        self.checked.difference_update(identifiers)

    def hidden_count(self, visible_identifiers: Iterable[Identifier]) -> int:
        return len(self.checked.difference(set(visible_identifiers)))

    def summary(self, visible_identifiers: Iterable[Identifier]) -> str:
        hidden = self.hidden_count(visible_identifiers)
        suffix = f" · {hidden} hidden by filters" if hidden else ""
        return f"{len(self.checked)} selected{suffix}"

    def context_targets(self, clicked: Identifier) -> tuple[Identifier, ...]:
        if clicked in self.checked:
            return tuple(self.checked)
        return (clicked,)


def _sortable(value: object) -> tuple[int, object]:
    if value is None:
        return (2, "")
    if isinstance(value, bool):
        return (0, int(value))
    if isinstance(value, (int, float)):
        return (0, value)
    return (1, str(value).casefold())
