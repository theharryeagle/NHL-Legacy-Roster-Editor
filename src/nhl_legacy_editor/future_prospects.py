from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import sys

from .attribute_map import (
    GOALIE_ATTRIBUTE_SPECS,
    SKATER_ATTRIBUTE_SPECS,
    display_to_raw,
    raw_to_display,
)
from .attribute_mapper import DISPLAY_TO_SEMANTIC, calculate_goalie_overall
from .player_editing import update_many_player_goalie_ratings, update_many_player_ratings
from .player_tools import build_player_snapshot_cache
from .rating_models import (
    ARCHETYPE_WEIGHTS,
    calculate_weighted_overall,
    plan_rating_upgrade,
)


POSITION_BY_CODE = {0: "C", 1: "LW", 2: "RW", 3: "D", 4: "G"}
STYLE_TO_ARCHETYPE = {
    1: "defensive_defenseman",
    2: "offensive_defenseman",
    3: "enforcer",
    4: "two_way_defenseman",
    5: "grinder",
    6: "playmaker",
    7: "sniper",
    8: "power_forward",
    9: "two_way_forward",
    10: "enforcer",
}


@dataclass(frozen=True, slots=True)
class FutureProspect:
    player_id: int
    name: str
    class_year: int


def _data_path() -> Path:
    if getattr(sys, "frozen", False):
        bundled = getattr(sys, "_MEIPASS", None)
        if bundled:
            candidate = Path(bundled) / "data" / "future_draft_prospects.csv"
            if candidate.is_file():
                return candidate
    return Path(__file__).resolve().parents[2] / "data" / "future_draft_prospects.csv"


def load_future_prospects() -> tuple[FutureProspect, ...]:
    with _data_path().open(encoding="utf-8-sig", newline="") as handle:
        return tuple(
            FutureProspect(
                player_id=int(row["player_id"]),
                name=str(row["player"]),
                class_year=int(row["class_year"]),
            )
            for row in csv.DictReader(handle)
        )


def projected_draft_overall(rank: int) -> int:
    """Return a realistic draft-day OVR target for a class rank.

    The first five land from 78-81. The curve then falls gradually so the
    entire created class improves without turning late selections into stars.
    """
    rank = max(1, int(rank))
    if rank <= 5:
        return (81, 80, 80, 79, 78)[rank - 1]
    tiers = (
        (10, 77, 75),
        (32, 74, 68),
        (64, 67, 60),
        (96, 59, 55),
        (160, 54, 50),
        (224, 49, 47),
    )
    previous_rank = 5
    for last_rank, top, bottom in tiers:
        if rank <= last_rank:
            fraction = (rank - previous_rank - 1) / max(1, last_rank - previous_rank - 1)
            return round(top - ((top - bottom) * fraction))
        previous_rank = last_rank
    return 47


def expected_growth_per_season(potential_code: int, color_code: int) -> float:
    base = {1: 3.0, 2: 2.5, 3: 2.0, 4: 1.5, 5: 1.0, 6: 0.75, 7: 0.5}.get(
        int(potential_code),
        0.75,
    )
    if int(potential_code) != 1:
        base += {1: 0.25, 2: 0.0, 4: -0.25}.get(int(color_code), 0.0)
    return max(0.25, base)


def starting_overall_target(
    *,
    rank: int,
    class_year: int,
    potential_code: int,
    color_code: int,
) -> int:
    seasons_until_draft = max(1, int(class_year) - 2026)
    expected_growth = round(
        seasons_until_draft * expected_growth_per_season(potential_code, color_code)
    )
    return max(36, projected_draft_overall(rank) - expected_growth)


def _goalie_upgrade(display: dict[str, int], target: int) -> dict[str, int]:
    suggested = dict(display)
    eligible = [spec.label for spec in GOALIE_ATTRIBUTE_SPECS if spec.mode != "raw"]
    while calculate_goalie_overall(suggested) < target:
        changed = False
        eligible.sort(key=lambda label: (suggested.get(label, 36), label))
        for label in eligible:
            if suggested.get(label, 36) >= 99:
                continue
            suggested[label] = suggested.get(label, 36) + 1
            changed = True
            if calculate_goalie_overall(suggested) >= target:
                break
        if not changed:
            break
    return suggested


def calibrate_future_draft_classes(db_path: Path) -> list[dict[str, object]]:
    cache = build_player_snapshot_cache(db_path)
    bio_by_id = {int(row.get("zIBw") or -1): row for row in cache.bio_rows}
    skater_by_id = {int(row.get("zIBw") or -1): row for row in cache.ratings_rows}
    goalie_by_id = {int(row.get("zIBw") or -1): row for row in cache.goalie_ratings_rows}
    manifest = load_future_prospects()

    def current_overall(item: tuple[FutureProspect, dict[str, object], dict[str, object]]) -> int:
        _prospect, bio, ratings = item
        position_code = int(bio.get("aljv")) if bio.get("aljv") is not None else -1
        position = POSITION_BY_CODE.get(position_code, "C")
        if position == "G":
            display = {
                spec.label: raw_to_display(spec, int(ratings.get(spec.field) or 0))
                for spec in GOALIE_ATTRIBUTE_SPECS
            }
            return calculate_goalie_overall(display)
        display = {
            spec.label: raw_to_display(spec, int(ratings.get(spec.field) or 0))
            for spec in SKATER_ATTRIBUTE_SPECS
        }
        semantic = {
            semantic_name: display[label]
            for label, semantic_name in DISPLAY_TO_SEMANTIC.items()
            if label in display
        }
        archetype = STYLE_TO_ARCHETYPE.get(int(ratings.get("sFgQ") or 0))
        if archetype not in ARCHETYPE_WEIGHTS:
            archetype = "two_way_defenseman" if position == "D" else "two_way_forward"
        return calculate_weighted_overall(semantic, archetype, position=position)

    ranked_by_year: dict[int, list[tuple[FutureProspect, dict[str, object], dict[str, object]]]] = {}
    for prospect in manifest:
        bio = bio_by_id.get(prospect.player_id)
        if bio is None:
            continue
        position_code = int(bio.get("aljv")) if bio.get("aljv") is not None else -1
        ratings = goalie_by_id.get(prospect.player_id) if position_code == 4 else skater_by_id.get(prospect.player_id)
        if ratings is None:
            continue
        ranked_by_year.setdefault(prospect.class_year, []).append((prospect, bio, ratings))

    skater_updates = []
    goalie_updates = []
    results: list[dict[str, object]] = []
    for class_year, rows in sorted(ranked_by_year.items()):
        rows.sort(
            key=lambda item: (
                int(item[2].get("AMoQ") or 99),
                int(item[2].get("feBm") or 99),
                -current_overall(item),
                item[0].name.casefold(),
                item[0].player_id,
            )
        )
        for rank, (prospect, bio, ratings) in enumerate(rows, start=1):
            position_code = int(bio.get("aljv")) if bio.get("aljv") is not None else -1
            position = POSITION_BY_CODE.get(position_code, "C")
            potential_code = int(ratings.get("AMoQ") or 99)
            color_code = int(ratings.get("feBm") or 99)
            target = starting_overall_target(
                rank=rank,
                class_year=class_year,
                potential_code=potential_code,
                color_code=color_code,
            )
            first_name, _, last_name = prospect.name.partition(" ")
            if position == "G":
                display = {
                    spec.label: raw_to_display(spec, int(ratings.get(spec.field) or 0))
                    for spec in GOALIE_ATTRIBUTE_SPECS
                }
                current = calculate_goalie_overall(display)
                if current >= target:
                    continue
                suggested = _goalie_upgrade(display, target)
                updates = {
                    spec.field: display_to_raw(spec, suggested[spec.label])
                    for spec in GOALIE_ATTRIBUTE_SPECS
                    if suggested[spec.label] != display[spec.label]
                }
                goalie_updates.append((first_name, last_name, updates, prospect.player_id))
                after = calculate_goalie_overall(suggested)
            else:
                display = {
                    spec.label: raw_to_display(spec, int(ratings.get(spec.field) or 0))
                    for spec in SKATER_ATTRIBUTE_SPECS
                }
                semantic = {
                    semantic_name: display[label]
                    for label, semantic_name in DISPLAY_TO_SEMANTIC.items()
                    if label in display
                }
                style_code = int(ratings.get("sFgQ") or 0)
                archetype = STYLE_TO_ARCHETYPE.get(style_code)
                if archetype not in ARCHETYPE_WEIGHTS:
                    archetype = "two_way_defenseman" if position == "D" else "two_way_forward"
                current = calculate_weighted_overall(semantic, archetype, position=position)
                if current >= target:
                    continue
                plan = plan_rating_upgrade(
                    semantic,
                    archetype,
                    target,
                    position=position,
                )
                updates = {
                    spec.field: display_to_raw(spec, plan.suggested_ratings[semantic_name])
                    for spec in SKATER_ATTRIBUTE_SPECS
                    if (semantic_name := DISPLAY_TO_SEMANTIC.get(spec.label)) in plan.suggested_ratings
                    and plan.suggested_ratings[semantic_name] != semantic.get(semantic_name)
                }
                skater_updates.append((first_name, last_name, updates, prospect.player_id))
                after = calculate_weighted_overall(plan.suggested_ratings, archetype, position=position)
            results.append(
                {
                    "player": prospect.name,
                    "player_id": prospect.player_id,
                    "class_year": class_year,
                    "class_rank": rank,
                    "current_overall": current,
                    "target_starting_overall": target,
                    "overall_after": after,
                    "projected_draft_overall": projected_draft_overall(rank),
                    "action": "future-prospect-calibration",
                }
            )

    update_many_player_ratings(db_path, skater_updates, snapshot_cache=cache)
    update_many_player_goalie_ratings(db_path, goalie_updates)
    return results
