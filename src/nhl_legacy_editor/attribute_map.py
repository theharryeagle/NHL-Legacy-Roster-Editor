from __future__ import annotations

from dataclasses import dataclass


SKATER_ATTRIBUTE_OFFSET = 36


@dataclass(frozen=True, slots=True)
class AttributeSpec:
    field: str
    label: str
    mode: str = "offset36"
    min_value: int = 0
    max_value: int = 99


# These fields are labeled to match NHL Legacy's in-game attribute screen.
# NHLViewNG exposes several of the same TDB fields under labels that do not
# line up with the in-game NHL Legacy player-card order.
SKATER_ATTRIBUTE_SPECS: list[AttributeSpec] = [
    AttributeSpec("bEdA", "Speed"),
    AttributeSpec("seUB", "Endurance"),
    AttributeSpec("oRUd", "Passing"),
    AttributeSpec("KQBB", "Slap Shot Accuracy"),
    AttributeSpec("ObeE", "Wrist Shot Accuracy"),
    AttributeSpec("TUty", "Strength"),
    AttributeSpec("YqOX", "Balance"),
    AttributeSpec("kIjD", "Durability"),
    AttributeSpec("oskO", "Aggressiveness"),
    AttributeSpec("ujcW", "Hand-Eye"),
    AttributeSpec("VlLd", "Off. Awareness"),
    AttributeSpec("ShqX", "Discipline"),
    AttributeSpec("POYr", "Stick Checking"),
    AttributeSpec("EksY", "Pass-Carry Bias", mode="raw", min_value=0, max_value=15),
    AttributeSpec("oisw", "Body Checking"),
    AttributeSpec("YqXz", "Puck Control"),
    AttributeSpec("DCvJ", "Slap Shot Power"),
    AttributeSpec("Hvje", "Wrist Shot Power"),
    AttributeSpec("pyYq", "Agility"),
    AttributeSpec("ckxF", "Acceleration"),
    AttributeSpec("KrwV", "Face-offs"),
    AttributeSpec("iCvN", "Deking"),
    AttributeSpec("fRaZ", "Poise"),
    AttributeSpec("zRrS", "Shot Blocking"),
    AttributeSpec("OTvp", "Def. Awareness"),
    AttributeSpec("gUBy", "Fighting Skill"),
    AttributeSpec("baRY", "Defence-Offence Bias", mode="raw", min_value=0, max_value=15),
    AttributeSpec("hlsv", "Shoot-Pass Bias", mode="raw", min_value=0, max_value=15),
]


def raw_to_display(spec: AttributeSpec, value: int | None) -> int:
    value = int(value or 0)
    if spec.mode == "offset36":
        return value + SKATER_ATTRIBUTE_OFFSET
    return value


def display_to_raw(spec: AttributeSpec, value: int | str) -> int:
    display_value = int(value)
    if spec.mode == "offset36":
        return max(0, display_value - SKATER_ATTRIBUTE_OFFSET)
    return display_value


def build_attribute_editor_rows(ratings_row: dict[str, object] | None) -> list[dict[str, object]]:
    if not ratings_row:
        return []
    rows: list[dict[str, object]] = []
    for spec in SKATER_ATTRIBUTE_SPECS:
        raw_value = int(ratings_row.get(spec.field) or 0)
        rows.append(
            {
                "field": spec.field,
                "label": spec.label,
                "mode": spec.mode,
                "display_value": raw_to_display(spec, raw_value),
                "raw_value": raw_value,
                "min_value": spec.min_value if spec.mode == "raw" else spec.min_value + SKATER_ATTRIBUTE_OFFSET,
                "max_value": spec.max_value if spec.mode == "raw" else spec.max_value,
            }
        )
    return rows


def attribute_specs_by_field() -> dict[str, AttributeSpec]:
    return {spec.field: spec for spec in SKATER_ATTRIBUTE_SPECS}
