from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nhl_legacy_editor.desktop_app import NhlLegacyDesktopApp  # noqa: E402
from nhl_legacy_editor.player_tools import build_player_snapshot_cache  # noqa: E402
from nhl_legacy_editor.roster_sync import equivalent_name_key, normalize_name  # noqa: E402
from nhl_legacy_editor.roster_views import load_player_index  # noqa: E402
from nhl_legacy_editor.team_tools import load_teams  # noqa: E402


EA_RATINGS_URL = "https://www.ea.com/games/nhl/ratings?orderBy=ovr%3Adesc&page={page}"
STYLE_MAP = {
    "2 Way Forward": "2-Way Forward",
    "2 Way D-Man": "2-Way Defenseman",
    "Offensive D-man": "Offensive Defenseman",
    "Defensive D-man": "Defensive Defenseman",
}
SKATER_FEATURES = (
    "acceleration",
    "agility",
    "strength",
    "aggression",
    "balance",
    "bodyChecking",
    "defensiveAwareness",
    "handeye",
    "deking",
    "discipline",
    "durability",
    "endurance",
    "offensiveAwareness",
    "passing",
    "poise",
    "puckControl",
    "shotBlocking",
    "slapshotAccuracy",
    "slapshotPower",
    "speed",
    "stickChecking",
    "wristshotAccuracy",
    "wristshotPower",
)
TRAIT_LABELS = {
    "passing": "passing",
    "deking": "puck skill",
    "puckControl": "puck control",
    "offensiveAwareness": "offensive awareness",
    "wristshotAccuracy": "wrist-shot accuracy",
    "wristshotPower": "wrist-shot power",
    "slapshotAccuracy": "slap-shot accuracy",
    "slapshotPower": "slap-shot power",
    "strength": "strength",
    "bodyChecking": "physical play",
    "aggression": "aggression",
    "defensiveAwareness": "defensive awareness",
    "stickChecking": "stick checking",
    "shotBlocking": "shot blocking",
    "speed": "speed",
    "agility": "agility",
    "balance": "balance",
    "endurance": "endurance",
}
STYLE_TRAITS = {
    "Playmaker": ("passing", "deking", "puckControl", "offensiveAwareness"),
    "Sniper": ("wristshotAccuracy", "slapshotAccuracy", "wristshotPower", "offensiveAwareness"),
    "Power Forward": ("strength", "balance", "bodyChecking", "aggression"),
    "2-Way Forward": ("defensiveAwareness", "stickChecking", "passing", "endurance"),
    "Grinder": ("bodyChecking", "aggression", "strength", "defensiveAwareness"),
    "Offensive Defenseman": ("passing", "puckControl", "offensiveAwareness", "agility"),
    "Defensive Defenseman": ("defensiveAwareness", "stickChecking", "shotBlocking", "strength"),
    "2-Way Defenseman": ("defensiveAwareness", "passing", "stickChecking", "puckControl"),
}


def fetch_ea_players() -> list[dict[str, object]]:
    players: list[dict[str, object]] = []
    page = 1
    total = None
    while total is None or len(players) < total:
        url = EA_RATINGS_URL.format(page=page)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(request, timeout=30).read().decode("utf-8")
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
        )
        if not match:
            raise RuntimeError(f"EA ratings data was not found on page {page}")
        page_data = json.loads(match.group(1))["props"]["pageProps"]["ratingDetails"]
        items = page_data["items"]
        total = int(page_data.get("totalItems") or page_data.get("total") or 370)
        players.extend(
            item for item in items if item.get("gender", {}).get("label") == "Men's Hockey"
        )
        if not items:
            break
        page += 1
        if page > 20:
            raise RuntimeError("Unexpected EA ratings pagination")
    unique = {int(player["id"]): player for player in players}
    return list(unique.values())


def canonical_style(raw: str) -> str:
    return STYLE_MAP.get(raw, raw)


def player_url(player: dict[str, object]) -> str:
    name = f"{player['firstName']} {player['lastName']}"
    folded = unicodedata.normalize("NFKD", name.casefold())
    ascii_name = "".join(c for c in folded if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return f"https://www.ea.com/games/nhl/ratings/player-ratings/{slug}/{player['id']}"


def value(player: dict[str, object], feature: str) -> float:
    return float(player.get("stats", {}).get(feature, {}).get("value") or 0)


def centered_vector(player: dict[str, object]) -> list[float]:
    values = [value(player, feature) for feature in SKATER_FEATURES]
    mean = sum(values) / len(values)
    return [item - mean for item in values]


def mean_vector(vectors: list[list[float]]) -> list[float]:
    return [sum(items) / len(vectors) for items in zip(*vectors)]


def distance(left: list[float], right: list[float], scales: list[float]) -> float:
    return math.sqrt(sum(((a - b) / scale) ** 2 for a, b, scale in zip(left, right, scales)))


def build_style_model(players: list[dict[str, object]]) -> tuple[dict[str, list[float]], list[float]]:
    groups: dict[str, list[list[float]]] = defaultdict(list)
    all_vectors: list[list[float]] = []
    for player in players:
        style = canonical_style(str(player.get("playerStyle", {}).get("label") or ""))
        if style not in STYLE_TRAITS:
            continue
        vector = centered_vector(player)
        groups[style].append(vector)
        all_vectors.append(vector)
    centroids = {style: mean_vector(vectors) for style, vectors in groups.items() if len(vectors) >= 3}
    feature_means = mean_vector(all_vectors)
    scales = []
    for index, mean in enumerate(feature_means):
        variance = sum((vector[index] - mean) ** 2 for vector in all_vectors) / len(all_vectors)
        scales.append(max(math.sqrt(variance), 1.0))
    return centroids, scales


def alternate_style(
    player: dict[str, object], centroids: dict[str, list[float]], scales: list[float]
) -> tuple[str, float, float] | None:
    current = canonical_style(str(player.get("playerStyle", {}).get("label") or ""))
    if current not in centroids:
        return None
    is_defense = "Defenseman" in current
    candidates = {
        style: centroid
        for style, centroid in centroids.items()
        if ("Defenseman" in style) == is_defense
    }
    vector = centered_vector(player)
    distances = sorted(
        ((distance(vector, centroid, scales), style) for style, centroid in candidates.items()),
        key=lambda item: (item[0], item[1]),
    )
    current_distance = next(item[0] for item in distances if item[1] == current)
    other_distance, other_style = min(item for item in distances if item[1] != current)
    ratio = other_distance / max(current_distance, 0.001)
    # A close centroid is evidence of an overlapping profile, not a correction.
    if ratio <= 1.10:
        return other_style, current_distance, ratio
    return None


def style_distance_ratio(
    player: dict[str, object],
    preferred: str,
    comparison: str,
    centroids: dict[str, list[float]],
    scales: list[float],
) -> float | None:
    if preferred not in centroids or comparison not in centroids:
        return None
    vector = centered_vector(player)
    preferred_distance = distance(vector, centroids[preferred], scales)
    comparison_distance = distance(vector, centroids[comparison], scales)
    return preferred_distance / max(comparison_distance, 0.001)


def alternate_reason(player: dict[str, object], alternate: str, ratio: float) -> str:
    traits = STYLE_TRAITS[alternate]
    ranked = sorted(traits, key=lambda trait: value(player, trait), reverse=True)[:3]
    details = ", ".join(f"{TRAIT_LABELS[trait]} {int(value(player, trait))}" for trait in ranked)
    return f"EA attribute profile is also close to {alternate} (distance ratio {ratio:.2f}); supporting traits: {details}."


def alternative_confidence(ratio: float | None) -> str:
    """Rate plausibility of an alternate archetype; this is not change confidence."""
    if ratio is None:
        return "Low"
    if ratio <= 0.80:
        return "High"
    if ratio <= 0.95:
        return "Medium"
    return "Low"


def alternate_fit_percent(ratio: float | None) -> str:
    """Return the alternate's inverse-squared-distance share versus current."""
    if ratio is None:
        return ""
    return f"{round(100.0 / (1.0 + ratio * ratio))}%"


def active_db_path() -> Path:
    workspace = json.loads((ROOT / "backups" / "editor_workspaces" / "active_workspace.json").read_text(encoding="utf-8"))
    return Path(workspace["working_db_path"])


def unique_name_index(players: list[dict[str, object]], key_function) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for player in players:
        result[key_function(f"{player['firstName']} {player['lastName']}")].append(player)
    return result


def match_player(
    name: str,
    local_position: str,
    exact: dict[str, list[dict[str, object]]],
    equivalent: dict[str, list[dict[str, object]]],
) -> tuple[dict[str, object] | None, str]:
    def compatible(candidate: dict[str, object]) -> bool:
        style = canonical_style(str(candidate.get("playerStyle", {}).get("label") or ""))
        if local_position == "G":
            return style == "Hybrid"
        if local_position == "D":
            return "Defenseman" in style
        return style in {"Playmaker", "Sniper", "Power Forward", "2-Way Forward", "Grinder"}

    exact_matches = [candidate for candidate in exact.get(normalize_name(name), []) if compatible(candidate)]
    if len(exact_matches) == 1:
        return exact_matches[0], "Official NHL 26 exact name"
    equivalent_matches = [
        candidate for candidate in equivalent.get(equivalent_name_key(name), []) if compatible(candidate)
    ]
    if len(equivalent_matches) == 1:
        return equivalent_matches[0], "Official NHL 26 equivalent name"
    return None, "No unambiguous NHL 26 listing"


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[dict[str, object]], columns: list[tuple[str, str]]) -> list[str]:
    lines = ["| " + " | ".join(label for _, label in columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        cells = [str(row.get(field, "")).replace("|", "\\|") for field, _ in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def audit(db_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ea_players = fetch_ea_players()
    exact = unique_name_index(ea_players, normalize_name)
    equivalent = unique_name_index(ea_players, equivalent_name_key)
    centroids, scales = build_style_model(ea_players)

    app = NhlLegacyDesktopApp.__new__(NhlLegacyDesktopApp)
    app.team_by_code = {team.code: team for team in load_teams(db_path)}
    index = [player for player in load_player_index(db_path) if player.league_name == "NHL"]
    snapshots = build_player_snapshot_cache(db_path)

    full: list[dict[str, object]] = []
    definite: list[dict[str, object]] = []
    possible: list[dict[str, object]] = []
    for local in index:
        snapshot = snapshots.get_player_snapshot(local.first_name, local.last_name, local.player_id)
        overall, position, current_style = app._snapshot_roster_summary(snapshot)
        ea, evidence = match_player(local.full_name, position, exact, equivalent)
        row: dict[str, object] = {
            "player": local.full_name,
            "local_team": local.current_team_abbrev or "",
            "local_position": position,
            "local_overall": overall if overall is not None else "",
            "current_type": current_style,
            "ea_team": "",
            "ea_position": "",
            "ea_overall": "",
            "nhl26_type": "",
            "classification": "No direct comparison",
            "recommended_type": "",
            "alternative_confidence": "",
            "alternate_fit_percent": "",
            "distance_ratio": "",
            "evidence": evidence,
            "reason": "No unambiguous player entry in EA's published NHL 26 ratings set.",
            "source_url": EA_RATINGS_URL.format(page=1),
            "player_id": local.player_id,
        }
        if ea is not None:
            ea_style = canonical_style(str(ea.get("playerStyle", {}).get("label") or ""))
            row.update(
                {
                    "ea_team": (ea.get("team") or {}).get("label", ""),
                    "ea_position": (ea.get("position") or {}).get("shortLabel", ""),
                    "ea_overall": ea.get("overallRating", ""),
                    "nhl26_type": ea_style,
                    "source_url": player_url(ea),
                }
            )
            if position == "G":
                row.update(
                    {
                        "classification": "Goalie reference only",
                        "reason": "EA NHL 26 publishes the generic Hybrid style; it is not enough evidence to replace Legacy's three goalie archetypes.",
                    }
                )
            elif current_style != ea_style:
                ratio = style_distance_ratio(ea, ea_style, current_style, centroids, scales)
                if ratio is not None and ratio <= 0.85:
                    row.update(
                        {
                            "classification": "Definite change",
                            "recommended_type": ea_style,
                            "reason": f"EA's published NHL 26 roster lists {ea_style}, and its attribute profile is substantially closer to that type than {current_style} (distance ratio {ratio:.2f}).",
                            "alternative_confidence": "High",
                            "alternate_fit_percent": alternate_fit_percent(ratio),
                            "distance_ratio": f"{ratio:.3f}",
                        }
                    )
                    definite.append(row)
                else:
                    ratio_text = f" (distance ratio {ratio:.2f})" if ratio is not None else ""
                    row.update(
                        {
                            "classification": "Current type defensible; NHL 26 differs",
                            "recommended_type": ea_style,
                            "reason": f"EA's published NHL 26 roster lists {ea_style}, but the attribute profile does not clearly disqualify {current_style}{ratio_text}.",
                            "alternative_confidence": alternative_confidence(ratio),
                            "alternate_fit_percent": alternate_fit_percent(ratio),
                            "distance_ratio": f"{ratio:.3f}" if ratio is not None else "",
                        }
                    )
                    possible.append(row)
            else:
                alternative = alternate_style(ea, centroids, scales)
                if alternative is not None:
                    other_style, _current_distance, ratio = alternative
                    row.update(
                        {
                            "classification": "Current fits; plausible alternative",
                            "recommended_type": other_style,
                            "reason": alternate_reason(ea, other_style, ratio),
                            "alternative_confidence": alternative_confidence(ratio),
                            "alternate_fit_percent": alternate_fit_percent(ratio),
                            "distance_ratio": f"{ratio:.3f}",
                        }
                    )
                    possible.append(row)
                else:
                    row.update(
                        {
                            "classification": "Current type confirmed",
                            "reason": f"Current type matches EA's published NHL 26 type ({ea_style}).",
                        }
                    )
        full.append(row)

    definite.sort(key=lambda row: (str(row["local_team"]), str(row["player"])))
    possible.sort(key=lambda row: (float(row.get("distance_ratio") or 99), str(row["player"])))
    full.sort(key=lambda row: (str(row["local_team"]), str(row["player"])))

    fields = [
        "player", "local_team", "local_position", "local_overall", "current_type",
        "ea_team", "ea_position", "ea_overall", "nhl26_type", "classification",
        "recommended_type", "alternative_confidence", "alternate_fit_percent", "distance_ratio",
        "evidence", "reason",
        "source_url", "player_id",
    ]
    write_csv(output_dir / "nhl_player_type_audit_full.csv", full, fields)
    write_csv(output_dir / "nhl_player_type_definite_changes.csv", definite, fields)
    write_csv(output_dir / "nhl_player_type_possible_alternatives.csv", possible, fields)

    counts = Counter(row["classification"] for row in full)
    report = [
        "# NHL player-type audit",
        "",
        f"Roster database: `{db_path}`",
        "",
        f"Audited **{len(full)}** players assigned to NHL teams. Matched **{sum(1 for row in full if row['nhl26_type'])}** unambiguously to EA's published NHL 26 ratings set.",
        "",
        "## Results",
        "",
        f"- Definite changes: **{len(definite)}**",
        f"- Current type is defensible, but a second type is plausible: **{len(possible)}**",
        f"  - High confidence: **{sum(row['alternative_confidence'] == 'High' for row in possible)}**",
        f"  - Medium confidence: **{sum(row['alternative_confidence'] == 'Medium' for row in possible)}**",
        f"  - Low confidence: **{sum(row['alternative_confidence'] == 'Low' for row in possible)}**",
        f"- Current type confirmed without a close alternative: **{counts['Current type confirmed']}**",
        f"- Goalies retained as reference-only comparisons: **{counts['Goalie reference only']}**",
        f"- No unambiguous NHL 26 match: **{counts['No direct comparison']}**",
        "",
        "A definite change requires an unambiguous name-and-position match, a direct disagreement with EA's published NHL 26 player style, and an EA attribute profile at least 15% closer to the published type than the roster's current type. The second list is deliberately broader: it includes direct EA disagreements where the current type remains defensible, plus players whose current type matches EA but whose attribute profile is also close to another archetype. Alternate fit is an inverse-squared-distance share between the two compared archetypes: 50% means an equal fit, above 50% favors the alternate, and below 50% favors the current type. It is a model score, not a statistically calibrated probability.",
        "",
        "## Definitely needs a change",
        "",
    ]
    report.extend(markdown_table(definite, [
        ("player", "Player"), ("local_team", "Team"), ("current_type", "Current"),
        ("recommended_type", "Change to"), ("local_overall", "Local OVR"),
        ("ea_overall", "NHL 26 OVR"),
    ]))
    report.extend(["", "## Current type is defensible, but another type is plausible", ""])
    report.extend(markdown_table(possible, [
        ("player", "Player"), ("local_team", "Team"), ("current_type", "Current"),
        ("recommended_type", "Also plausible"), ("alternative_confidence", "Confidence"),
        ("alternate_fit_percent", "Alternate fit"), ("reason", "Why"),
    ]))
    report.extend([
        "",
        "## Notes",
        "",
        "- Goalies are excluded from change recommendations because NHL 26 exposes only the generic Hybrid label, whereas NHL Legacy supports Stand-Up, Hybrid, and Butterfly.",
        "- Unmatched and ambiguous names are present in the full CSV and are never guessed.",
        "- Max Domi is already a Playmaker in this roster. NHL Ratings also lists him as a Playmaker, so the 2016 Grinder classification has already been corrected.",
        "- This report does not edit the roster.",
    ])
    (output_dir / "nhl_player_type_audit.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print(json.dumps({
        "audited": len(full),
        "matched": sum(1 for row in full if row["nhl26_type"]),
        "definite": len(definite),
        "possible": len(possible),
        "counts": counts,
        "output_dir": str(output_dir),
    }, indent=2, default=dict))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit NHL Legacy player types against EA NHL 26.")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    audit(args.db or active_db_path(), args.output_dir)


if __name__ == "__main__":
    main()
