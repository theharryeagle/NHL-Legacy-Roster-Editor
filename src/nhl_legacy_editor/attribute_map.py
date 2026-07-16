from __future__ import annotations

from dataclasses import dataclass


ATTRIBUTE_OFFSET = 36


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
    AttributeSpec("oisw", "Body Checking"),
    AttributeSpec("kIjD", "Endurance"),
    AttributeSpec("YqXz", "Puck Control"),
    AttributeSpec("oRUd", "Passing"),
    AttributeSpec("DCvJ", "Slap Shot Power"),
    AttributeSpec("KQBB", "Slap Shot Accuracy"),
    AttributeSpec("Hvje", "Wrist Shot Power"),
    AttributeSpec("ObeE", "Wrist Shot Accuracy"),
    AttributeSpec("pyYq", "Agility"),
    AttributeSpec("POYr", "Strength"),
    AttributeSpec("ckxF", "Acceleration"),
    AttributeSpec("YqOX", "Balance"),
    AttributeSpec("KrwV", "Face-offs"),
    AttributeSpec("seUB", "Durability"),
    AttributeSpec("iCvN", "Deking"),
    AttributeSpec("oskO", "Aggressiveness"),
    AttributeSpec("fRaZ", "Poise"),
    AttributeSpec("ujcW", "Hand-Eye"),
    AttributeSpec("zRrS", "Shot Blocking"),
    AttributeSpec("VlLd", "Off. Awareness"),
    AttributeSpec("OTvp", "Def. Awareness"),
    AttributeSpec("ShqX", "Discipline"),
    AttributeSpec("gUBy", "Fighting Skill"),
    AttributeSpec("TUty", "Stick Checking"),
    AttributeSpec("EksY", "Pass-Carry Bias", mode="raw", min_value=0, max_value=15),
    AttributeSpec("baRY", "Defence-Offence Bias", mode="raw", min_value=0, max_value=15),
    AttributeSpec("hlsv", "Shoot-Pass Bias", mode="raw", min_value=0, max_value=15),
]

# Goalies live in a separate ratings table from skaters. These labels follow
# NHLViewNG's goalie attribute screen; the confirmed fields are anchored from
# Joseph Woll, John Gibson, and Kevin Mandolese screenshots.
GOALIE_ATTRIBUTE_SPECS: list[AttributeSpec] = [
    AttributeSpec("ejux", "Glove Side Low"),
    AttributeSpec("DTrq", "Glove Side High"),
    AttributeSpec("vcIl", "Stick Side High"),
    AttributeSpec("SiKH", "Stick Side Low"),
    AttributeSpec("nMNR", "Five Hole"),
    AttributeSpec("pyYq", "Agility"),
    AttributeSpec("bEdA", "Speed"),
    AttributeSpec("UqhP", "Poke Check"),
    AttributeSpec("koEt", "Consistency"),
    AttributeSpec("Nhuq", "Breakaway"),
    AttributeSpec("kIjD", "Endurance"),
    AttributeSpec("oLxj", "Shot Recovery"),
    AttributeSpec("fdgB", "Rebound Control"),
    AttributeSpec("fRaZ", "Poise"),
    AttributeSpec("oRUd", "Passing"),
    AttributeSpec("miXH", "Angles"),
    AttributeSpec("LMNx", "Puck Play Frequency"),
    AttributeSpec("oskO", "Aggressiveness"),
    AttributeSpec("seUB", "Durability"),
    AttributeSpec("mshm", "Vision"),
]


def raw_to_display(spec: AttributeSpec, value: int | None) -> int:
    value = int(value or 0)
    if spec.mode == "offset36":
        return value + ATTRIBUTE_OFFSET
    return value


def display_to_raw(spec: AttributeSpec, value: int | str) -> int:
    display_value = int(value)
    if spec.mode == "offset36":
        return max(0, display_value - ATTRIBUTE_OFFSET)
    return display_value


def specs_for_player_kind(kind: str | None) -> list[AttributeSpec]:
    return GOALIE_ATTRIBUTE_SPECS if kind == "goalie" else SKATER_ATTRIBUTE_SPECS


def build_attribute_editor_rows(
    ratings_row: dict[str, object] | None,
    *,
    kind: str | None = None,
) -> list[dict[str, object]]:
    if not ratings_row:
        return []
    rows: list[dict[str, object]] = []
    for spec in specs_for_player_kind(kind):
        raw_value = int(ratings_row.get(spec.field) or 0)
        rows.append(
            {
                "field": spec.field,
                "label": spec.label,
                "mode": spec.mode,
                "display_value": raw_to_display(spec, raw_value),
                "raw_value": raw_value,
                "min_value": spec.min_value if spec.mode == "raw" else spec.min_value + ATTRIBUTE_OFFSET,
                "max_value": spec.max_value if spec.mode == "raw" else spec.max_value,
            }
        )
    return rows


def attribute_specs_by_field(kind: str | None = None) -> dict[str, AttributeSpec]:
    return {spec.field: spec for spec in specs_for_player_kind(kind)}
