from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import html
import json
from pathlib import Path
import re


ARTICLE_TITLE = "Top 100 drafted NHL prospects ranking: McKenna, Stenberg, Martone lead summer 2026 list"
ARTICLE_URL = "https://www.nytimes.com/athletic/7416111/2026/07/14/nhl-prospects-top-100-ranking-2026/"

CARD_START = re.compile(
    r'<div id="(?P<article_id>\d+)" class="(?P<classes>[^"]*\bfc-card\b[^"]*)" data-name="(?P<name>[^"]+)">',
    re.IGNORECASE,
)

POSITION_CLASSES = {
    "c": "C",
    "lw": "LW",
    "rw": "RW",
    "lhd": "LHD",
    "rhd": "RHD",
}

NAME_CORRECTIONS = {
    # The article card transposes two letters; use the player's canonical name
    # so exact roster matching finds him.
    "Kevin Korchsinki": "Kevin Korchinski",
}

TRAIT_LABELS = {
    "speed": "Speed",
    "acceleration": "Acceleration",
    "agility": "Agility",
    "balance": "Balance",
    "strength": "Strength",
    "endurance": "Endurance",
    "puck_control": "Puck Control",
    "deking": "Deking",
    "passing": "Passing",
    "offensive_awareness": "Offensive Awareness",
    "wrist_shot_accuracy": "Wrist Shot Accuracy",
    "wrist_shot_power": "Wrist Shot Power",
    "slap_shot_accuracy": "Slap Shot Accuracy",
    "slap_shot_power": "Slap Shot Power",
    "hand_eye": "Hand-Eye",
    "body_checking": "Body Checking",
    "aggressiveness": "Aggressiveness",
    "defensive_awareness": "Defensive Awareness",
    "stick_checking": "Stick Checking",
    "shot_blocking": "Shot Blocking",
    "discipline": "Discipline",
    "faceoffs": "Face-offs",
    "poise": "Poise",
}

TRAIT_PATTERNS = {
    "speed": (
        r"\bspeed\b", r"\bfast\b", r"straight[- ]line", r"\bpace\b", r"\bskater\b", r"\bskating\b",
    ),
    "acceleration": (
        r"accelerat", r"explosi", r"first (?:step|steps)", r"quick burst", r"straight burst",
    ),
    "agility": (
        r"\bagil", r"edgework", r"\bedges\b", r"mobility", r"footwork", r"maneuver", r"crossovers?",
        r"change(?:s)? of direction", r"cuts?", r"pivots?", r"four-way",
    ),
    "balance": (
        r"\bbalance\b", r"stay(?:s|ing)? (?:over|on) pucks", r"through contact", r"puck protection",
        r"protect(?:s|ing)? (?:the )?puck", r"hold(?:s|ing)? off",
    ),
    "strength": (
        r"\bstrength\b", r"\bstrong\b", r"\bpowerful\b", r"physical tools", r"pro build", r"big frame",
        r"fill out", r"add muscle", r"through contact", r"puck protection", r"protect(?:s|ing)? (?:the )?puck",
    ),
    "endurance": (
        r"\bmotor\b", r"work rate", r"work ethic", r"\beffort\b", r"shift[- ]to[- ]shift", r"\bhustle\b",
        r"consisten(?:t|cy)", r"keep his feet moving", r"keeps? (?:his )?feet moving",
    ),
    "puck_control": (
        r"puck skill", r"puck control", r"\bhandles?\b", r"handling", r"in possession", r"possession game",
        r"first touch", r"puck protection", r"puck carrier", r"puck carrying", r"carr(?:y|ies|ying) the puck",
        r"transport(?:er|ing)?", r"on the puck",
    ),
    "deking": (
        r"\bdekes?\b", r"\bdangl", r"one[- ]on[- ]one", r"toe[- ]drag", r"\bshifty\b", r"shoulder fake",
        r"make(?:s)? (?:guys|defenders) miss", r"deception", r"deceptive",
    ),
    "passing": (
        r"\bpasser\b", r"\bpassing\b", r"\bpasses\b", r"playmak", r"facilitat", r"distribut", r"\bvision\b",
        r"sees the ice", r"feeds?", r"puck movement", r"move(?:s)? pucks?", r"play[- ]creat",
    ),
    "offensive_awareness": (
        r"offensive instincts", r"offensive sense", r"offensive awareness", r"hockey iq", r"hockey sense",
        r"scoring sense", r"find(?:s|ing)? (?:open|space)", r"anticipat", r"read(?:s|ing)? the game",
        r"gets? open", r"timing offensively", r"offensive-zone",
    ),
    "wrist_shot_accuracy": (
        r"\bwrister\b", r"wrist shot", r"\brelease\b", r"shot accuracy", r"pinpoint", r"goal[- ]scor",
        r"\bfinisher\b", r"finishing", r"scoring ability", r"scoring touch", r"shooting talent",
    ),
    "wrist_shot_power": (
        r"hard shot", r"heavy shot", r"powerful shot", r"strong shot", r"shot power", r"shooting power",
        r"\bwrister\b", r"wrist shot", r"\brelease\b",
    ),
    "slap_shot_accuracy": (
        r"one[- ]timer", r"point shot", r"slap shot", r"shoot(?:s|ing)? through traffic",
    ),
    "slap_shot_power": (
        r"one[- ]timer", r"point shot", r"slap shot", r"\bcannon\b", r"\bbomb\b",
    ),
    "hand_eye": (
        r"hand[- ]eye", r"deflect", r"\btips?\b", r"net[- ]front", r"around the net", r"first touch",
    ),
    "body_checking": (
        r"body check", r"finishe?s? (?:his )?checks", r"\bhits?\b", r"physicality", r"\bphysical\b",
        r"\bcontact\b", r"wall play", r"along the wall", r"along the boards", r"net[- ]front",
    ),
    "aggressiveness": (
        r"aggress", r"\bmean\b", r"\bcompete\b", r"competitive", r"battle level", r"\bmotor\b",
        r"\beffort\b", r"\bhustle\b", r"forecheck", r"relentless", r"finishe?s? (?:his )?checks",
    ),
    "defensive_awareness": (
        r"defensive instincts", r"defensive awareness", r"defensive sense", r"defensive play", r"defensively",
        r"off[- ]puck", r"backcheck", r"\bcoverage\b", r"defensive zone", r"d-zone", r"two[- ]way",
        r"defensive detail", r"defensive habits", r"defensive positioning", r"shutdown",
    ),
    "stick_checking": (
        r"good stick", r"active stick", r"stick defensively", r"stick lift", r"stick check", r"poke check",
        r"cut off pass", r"intercept", r"passing lane", r"defensive stick",
    ),
    "shot_blocking": (
        r"shot block", r"block(?:s|ing)? shots", r"shooting lane",
    ),
    "discipline": (
        r"\bdiscipline\b", r"bad penalt", r"takes? penalties", r"penalty trouble", r"undisciplined",
    ),
    "faceoffs": (
        r"face[- ]?offs?", r"draws? in the circle",
    ),
    "poise": (
        r"\bpoise\b", r"\bpoised\b", r"composure", r"\bcalm\b", r"under pressure", r"\bpatience\b",
        r"patient with the puck", r"fearless",
    ),
}

STRONG_POSITIVE = re.compile(
    r"elite|high[- ]end|excellent|exceptional|special|lethal|deadly|supremely|great|dynamic|beautiful|"
    r"impressive|advanced|premium|one of (?:the )?best|standout|very,? very strong|very strong",
)
POSITIVE = re.compile(
    r"\bgood\b|\bstrong\b|above[- ]average|quality|solid|effective|smart|smooth|skilled|confident|"
    r"\bability\b|\btool\b|\basset\b|\bknack\b|\bcomfortable\b|\breliable\b",
)
STRONG_NEGATIVE = re.compile(
    r"poor|bad habit|major (?:issue|concern)|significant (?:issue|concern)|below[- ]average|liabilit|"
    r"\bweak\b|very limited|really struggle|serious concern|lacks? explos|low pace|unfinished defense",
)
NEGATIVE = re.compile(
    r"\bneeds?\b|need to|must improve|must add|inconsisten|average|limited|struggle|concern|issue|flaw|"
    r"not explosive|isn't explosive|is not explosive|doesn't always|does not always|can be passive|"
    r"too passive|still developing|still learning|unrefined|raw|question|risk|lean build|small frame|"
    r"undersized|not big|lack of|lacks? (?:speed|strength|pace|detail)|losing his man",
)
NEGATION_AS_POSITIVE = re.compile(r"not a concern|isn't a concern|won't be an issue|not an issue")

ROLE_PATTERNS = (
    ("first_line", re.compile(r"first[- ]line|top[- ]line|star (?:quality|winger|forward|center)|high[- ]end point[- ]produc|no\. ?1 (?:defen[cs]eman|d-man|d)")),
    ("top_pair", re.compile(r"top[- ]pair")),
    ("top_six", re.compile(r"top[- ]six|second[- ]line")),
    ("top_four", re.compile(r"top[- ]four|second[- ]pair|no\. ?[234] (?:defen[cs]eman|d-man|d)")),
    ("middle_six", re.compile(r"middle[- ]six|third[- ]line")),
    ("depth", re.compile(r"bottom[- ]six|fourth[- ]line|third[- ]pair|no\. ?[56] (?:defen[cs]eman|d-man|d)|depth (?:forward|defen[cs]eman)")),
)

ROLE_LABELS = {
    "first_line": "First-line / star",
    "top_pair": "Top-pair defenseman",
    "top_six": "Top-six forward",
    "top_four": "Top-four defenseman",
    "middle_six": "Middle-six forward",
    "depth": "NHL depth role",
    "unspecified": "Tier-based NHL projection",
}


def _strip_markup(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(_repair_text(html.unescape(value)).replace("\xa0", " ").split())


def _repair_text(value: str) -> str:
    # The interactive cards occasionally arrive with UTF-8 text decoded once
    # as Latin-1. Repair only when that round-trip is valid.
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if repaired != value else value


def _tier_for_rank(rank: int) -> int:
    if rank <= 4:
        return 1
    if rank <= 28:
        return 2
    if rank <= 54:
        return 3
    if rank <= 71:
        return 4
    if rank <= 88:
        return 5
    return 6


def _extract_cards(source: str) -> list[dict[str, object]]:
    starts = list(CARD_START.finditer(source))
    cards: list[dict[str, object]] = []
    for index, match in enumerate(starts):
        segment = source[match.start() : starts[index + 1].start() if index + 1 < len(starts) else len(source)]
        classes = match.group("classes").lower()
        rank_match = re.search(r'<div class="fc-rank-text">\s*(\d+)\s*</div>', segment)
        year_match = re.search(r"\bfcf-(20\d{2})\b", classes)
        position = next(
            (display for key, display in POSITION_CLASSES.items() if re.search(rf"\bfcf-{key}\b", classes)),
            "",
        )
        top_stat_match = re.search(r'<div class="fc-topstat">(?P<body>.*?)</div>\s*</div>', segment, re.DOTALL)
        top_stats = []
        if top_stat_match:
            top_stats = [
                _strip_markup(value)
                for value in re.findall(r'<div class="fc-stat [^"]*">(.*?)</div>', top_stat_match.group("body"), re.DOTALL)
            ]
        paragraphs = [
            _strip_markup(value)
            for value in re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", segment, re.DOTALL | re.IGNORECASE)
        ]
        report = " ".join(value for value in paragraphs if value)
        if not rank_match or not position or not report:
            continue
        rank = int(rank_match.group(1))
        source_name = _repair_text(html.unescape(match.group("name"))).strip()
        class_tokens = re.findall(r"\bfcf-([a-z0-9]+)\b", classes)
        excluded_tokens = {*POSITION_CLASSES, *(str(year) for year in range(2000, 2031))}
        team = next(
            (
                token.upper()
                for token in class_tokens
                if token not in excluded_tokens and not token.startswith("tier_")
            ),
            "",
        )
        cards.append(
            {
                "article_player_id": int(match.group("article_id")),
                "rank": rank,
                "tier": _tier_for_rank(rank),
                "name": NAME_CORRECTIONS.get(source_name, source_name),
                "source_name": source_name,
                "team": top_stats[1] if len(top_stats) > 1 else team,
                "draft_year": int(year_match.group(1)) if year_match else None,
                "position": position,
                "report": report,
            }
        )
    return sorted(cards, key=lambda row: int(row["rank"]))


def _clause_sentiment(clause: str) -> int:
    if NEGATION_AS_POSITIVE.search(clause):
        return 1
    if STRONG_NEGATIVE.search(clause):
        return -3
    if NEGATIVE.search(clause):
        return -2
    if STRONG_POSITIVE.search(clause):
        return 3
    if POSITIVE.search(clause):
        return 2
    return 1


def _trait_evidence(report: str) -> dict[str, int]:
    lowered = report.lower().replace("’", "'")
    clauses = re.split(
        r"(?<=[.!?;])\s+|\s+(?:but|however|though|although|yet)\s+|\s*[—–]\s*",
        lowered,
    )
    evidence: defaultdict[str, int] = defaultdict(int)
    for clause in clauses:
        if not clause.strip():
            continue
        sentiment = _clause_sentiment(clause)
        for trait, patterns in TRAIT_PATTERNS.items():
            if any(re.search(pattern, clause) for pattern in patterns):
                evidence[trait] += sentiment

    # Explicit constructions are more reliable than general clause sentiment.
    explicit_rules = (
        (r"not (?:an? )?explosive|lacks? explosiveness", {"acceleration": -3}),
        (r"average feet|foot(?: |-)?speed (?:is |remains )?(?:an? )?(?:issue|concern)|skating (?:is |remains )?(?:an? )?(?:issue|concern)", {"speed": -3, "acceleration": -2}),
        (r"elite (?:skater|skating)|explosive skater|dynamic skater", {"speed": 3, "acceleration": 3, "agility": 2}),
        (r"elite (?:playmaker|playmaking)|high[- ]end (?:playmaker|playmaking)", {"passing": 4, "offensive_awareness": 3}),
        (r"elite (?:hockey iq|hockey sense)|reads the game at an elite level", {"offensive_awareness": 4, "defensive_awareness": 2}),
        (r"great shot|elite shot|lethal (?:shot|wrister|release)|one of the best shots", {"wrist_shot_accuracy": 4, "wrist_shot_power": 3}),
        (r"bad penalties|penalty trouble|undisciplined", {"discipline": -4}),
        (r"good stick defensively|excellent defensive stick|active defensive stick", {"stick_checking": 2}),
        (r"need[^.]*off[- ]puck|work harder[^.]*backcheck|cheating up ice", {"defensive_awareness": -4, "aggressiveness": -2}),
        (r"fill out[^.]*stronger|get stronger", {"strength": -4}),
        (r"doesn't block shots|does not block shots|shot blocking (?:is |remains )?(?:a )?weakness", {"shot_blocking": -3}),
        (r"high[- ]end defensive|elite defensively|excellent defensively|shutdown", {"defensive_awareness": 4, "stick_checking": 3}),
        (r"drop(?:s)? the gloves|will fight|willing fighter", {"aggressiveness": 3}),
    )
    for pattern, updates in explicit_rules:
        if re.search(pattern, lowered):
            for trait, value in updates.items():
                evidence[trait] += value
    return dict(evidence)


def _modifiers_from_evidence(evidence: dict[str, int]) -> dict[str, int]:
    converted: dict[str, int] = {}
    for trait, score in evidence.items():
        if score >= 6:
            converted[trait] = 3
        elif score >= 3:
            converted[trait] = 2
        elif score >= 1:
            converted[trait] = 1
        elif score <= -5:
            converted[trait] = -2
        elif score <= -1:
            converted[trait] = -1

    positives = sorted(
        ((trait, delta, evidence[trait]) for trait, delta in converted.items() if delta > 0),
        key=lambda item: (-item[2], TRAIT_LABELS[item[0]]),
    )[:7]
    negatives = sorted(
        ((trait, delta, evidence[trait]) for trait, delta in converted.items() if delta < 0),
        key=lambda item: (item[2], TRAIT_LABELS[item[0]]),
    )[:3]
    selected = positives + negatives
    return {trait: delta for trait, delta, _score in sorted(selected, key=lambda item: TRAIT_LABELS[item[0]])}


def _projected_role(report: str) -> str:
    lowered = report.lower().replace("’", "'")
    matches: list[tuple[int, str]] = []
    for role, pattern in ROLE_PATTERNS:
        matches.extend((match.start(), role) for match in pattern.finditer(lowered))
    return max(matches, default=(-1, "unspecified"))[1]


def _potential(rank: int, tier: int, name: str, projection: str, report: str) -> tuple[float, str, str]:
    if name == "Gavin McKenna":
        return 5.0, "Green", "Franchise ceiling; the only 5-star prospect in this set."
    if rank <= 4:
        return 4.5, "Green", "Tier 1 first-line ceiling with high confidence."

    lowered = report.lower().replace("’", "'")
    high_floor = bool(re.search(r"high[- ]floor|safe bet|projects as|likely (?:an? )?nhl|clear nhl role|will be (?:an? )?", lowered))

    if projection == "middle_six":
        return 4.0, "Red", "Middle-six projection mapped to Top 6 Low/Red."

    if tier == 2:
        if projection in {"first_line", "top_pair"}:
            color = "Green" if high_floor else "Yellow"
            return 4.5, color, f"{ROLE_LABELS[projection]} ceiling in Tier 2."
        if projection in {"top_six", "top_four"}:
            color = "Green" if high_floor else "Yellow"
            return 4.0, color, f"{ROLE_LABELS[projection]} projection in Tier 2."
        if projection == "depth":
            return 3.5, "Green", "High-ranked prospect with a dependable NHL role."
        return 4.5, "Yellow", "Tier 2 impact ceiling; role is not stated firmly enough for Green."

    if tier == 3:
        if projection in {"first_line", "top_pair"}:
            return 4.5, "Red", f"High {ROLE_LABELS[projection].lower()} ceiling with material development risk."
        if projection in {"top_six", "top_four"}:
            color = "Green" if high_floor else "Yellow"
            return 4.0, color, f"{ROLE_LABELS[projection]} projection in Tier 3."
        if projection == "depth":
            return 3.5, "Green", "Strong NHL-role certainty, with a lower ceiling."
        return 4.0, "Yellow", "Tier 3 projects above the Top 100 minimum."

    if tier == 4:
        if projection in {"first_line", "top_pair"}:
            return 4.5, "Red", f"High {ROLE_LABELS[projection].lower()} ceiling with low certainty."
        if projection in {"top_six", "top_four"}:
            color = "Green" if high_floor else "Yellow"
            return 4.0, color, f"{ROLE_LABELS[projection]} projection in Tier 4."
        if projection == "depth":
            return 3.5, "Green", "Likely NHL depth outcome with meaningful role certainty."
        return 4.0, "Yellow", "Tier 4 carries a Top 6/Top 4 medium projection."

    if tier == 5:
        if projection in {"first_line", "top_pair"}:
            return 4.5, "Red", f"High-end {ROLE_LABELS[projection].lower()} ceiling with low certainty."
        if projection in {"top_six", "top_four"}:
            return 4.0, "Red", f"{ROLE_LABELS[projection]} ceiling with low certainty."
        if projection == "depth":
            return 3.5, "Green", "NHL depth role is the stronger part of the projection."
        return 3.5, "Green", "Tier 5 prospect with an NHL projection above the minimum floor."

    if projection in {"first_line", "top_pair"}:
        return 4.5, "Red", f"High-end {ROLE_LABELS[projection].lower()} ceiling with low certainty."
    if projection in {"top_six", "top_four"}:
        return 4.0, "Red", f"{ROLE_LABELS[projection]} ceiling with low certainty."
    if projection == "depth":
        return 3.5, "Green", "Lower ceiling but comparatively clear NHL-role path."
    return 3.5, "Yellow", "Top 100 floor: Top 9/Top 6 D with medium certainty."


def build_manifest(source_path: Path) -> dict[str, object]:
    cards = _extract_cards(source_path.read_text(encoding="utf-8"))
    if len(cards) != 100 or [int(row["rank"]) for row in cards] != list(range(1, 101)):
        raise RuntimeError(f"Expected ranks 1-100, found {len(cards)} valid cards.")

    prospects = []
    for card in cards:
        report = str(card.pop("report"))
        evidence = _trait_evidence(report)
        modifiers = _modifiers_from_evidence(evidence)
        projection = _projected_role(report)
        stars, color, reason = _potential(
            int(card["rank"]),
            int(card["tier"]),
            str(card["name"]),
            projection,
            report,
        )
        strengths = [TRAIT_LABELS[key] for key, value in modifiers.items() if value > 0]
        weaknesses = [TRAIT_LABELS[key] for key, value in modifiers.items() if value < 0]
        prospects.append(
            {
                **card,
                "projection": projection,
                "projection_label": ROLE_LABELS[projection],
                "potential_stars": stars,
                "potential_color": color,
                "potential_reason": reason,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "modifiers": modifiers,
            }
        )

    return {
        "title": ARTICLE_TITLE,
        "source": ARTICLE_URL,
        "published": "2026-07-14",
        "derived": date.today().isoformat(),
        "policy": {
            "attribute_delta_min": -2,
            "attribute_delta_max": 3,
            "minimum_potential": "3.5 Yellow",
            "middle_six_potential": "4.0 Red",
            "maximum_non_mckenna_potential": "4.5 Green",
            "mckenna_potential": "5.0 Green",
        },
        "prospects": prospects,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the derived Scott Wheeler summer 2026 Top 100 manifest.")
    parser.add_argument("source_html", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()
    payload = build_manifest(args.source_html)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['prospects'])} prospects to {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
