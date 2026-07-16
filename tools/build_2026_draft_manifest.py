from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re


REDDIT_THREAD = "https://www.reddit.com/r/hockey/comments/1uh3jem/game_thread_nhl_entry_draft_rounds_27_27_june/"
NHL_FIRST_ROUND = "https://www.nhl.com/news/topic/nhl-draft/2026-nhl-draft-first-round-tracker-analysis"

FIRST_ROUND = [
    (1, "TOR", "Gavin McKenna", "LW", "Penn State (NCAA)", "Playmaker", "Elite hockey IQ, vision and pace manipulation", "Needs normal physical maturation"),
    (2, "SJS", "Ivar Stenberg", "LW", "Frolunda (SHL)", "Playmaker", "Play-driving skill, motor, finishing and defensive reliability", "Average NHL size"),
    (3, "VAN", "Caleb Malhotra", "C", "Brantford (OHL)", "2-Way Forward", "Elite sense and speed with a mature 200-foot game", "Needs time to add pro strength"),
    (4, "BUF", "Daxon Rudolph", "D", "Prince Albert (WHL)", "2-Way Defenseman", "Mobility, offense, physicality and difficult-minute defending", "Can refine risk management"),
    (5, "NYR", "Alberts Smits", "D", "Jukurit (Liiga)", "2-Way Defenseman", "Poise, skating, physical maturity and efficient puck decisions", "Offensive ceiling is less certain"),
    (6, "CGY", "Carson Carels", "D", "Prince George (WHL)", "Offensive Defenseman", "High-end offense, power-play skill and strength", "Defensive detail can mature"),
    (7, "SEA", "Chase Reid", "D", "Sault Ste. Marie (OHL)", "Offensive Defenseman", "Elite skating, transition play and power-play creation", "Needs normal defensive refinement"),
    (8, "WPG", "Viggo Bjorck", "C", "Djurgarden (SHL)", "2-Way Forward", "Relentless motor, speed, competitiveness and pro experience", "Undersized for an NHL center"),
    (9, "SJS", "Keaton Verhoeff", "D", "North Dakota (NCAA)", "2-Way Defenseman", "Pro frame, skating, composure and gap control", "Offense may develop gradually"),
    (10, "NSH", "Wyatt Cullen", "RW", "USA U-18 (NTDP)", "Sniper", "Finishing touch and attacking instincts", "All-around impact needs consistency"),
    (11, "STL", "Tynan Lawrence", "C", "Boston University (NCAA)", "Playmaker", "Hockey sense, puck skill and distribution", "Needs strength and pro seasoning"),
    (12, "NJD", "Alexander Command", "C", "Orebro Jr. (Sweden)", "2-Way Forward", "Strong frame, edge and responsible center play", "Top-end offense remains a projection"),
    (13, "NYI", "Malte Gustafsson", "D", "HV71 (SHL)", "Offensive Defenseman", "Skating, poise, first pass and long reach", "Needs to fill out his large frame"),
    (14, "CBJ", "Oscar Hemming", "LW", "Boston College (NCAA)", "Power Forward", "Heavy north-south game and NHL size", "Pace and consistency can improve"),
    (15, "ANA", "Nikita Klepov", "RW", "Saginaw (OHL)", "Playmaker", "Elite vision, passing and productive offense", "Needs continued physical development"),
    (16, "STL", "Maddox Dagenais", "C", "Quebec (QMJHL)", "Power Forward", "Size, scoring touch, forecheck and heavy release", "Skating can become more dynamic"),
    (17, "UTA", "Ethan Belchetz", "LW", "Windsor (OHL)", "Power Forward", "Elite size, shot, net drive and physical presence", "Skating needs improvement"),
    (18, "WSH", "Oliver Suvanto", "C", "Tappara (Liiga)", "2-Way Forward", "Strength, poise and defense-first center habits", "Offensive ceiling is still developing"),
    (19, "LAK", "Elton Hermansson", "RW", "MoDo (Sweden-2)", "Playmaker", "Creativity, passing and high-skill offense", "Needs strength for NHL traffic"),
    (20, "BUF", "Ilia Morozov", "C", "Miami Ohio (NCAA)", "2-Way Forward", "Size, skating, puck skill and disciplined defense", "Faceoffs and offense are developing"),
    (21, "SJS", "Ryan Lin", "D", "Vancouver (WHL)", "Offensive Defenseman", "Elite sense, skating and puck creativity", "Smaller defender who must manage physical pressure"),
    (22, "PIT", "Liam Ruck", "RW", "Medicine Hat (WHL)", "Sniper", "Goal scoring, interior timing and finishing", "Needs to round out his 200-foot impact"),
    (23, "DET", "JP Hurlbert", "LW", "Kamloops (WHL)", "Sniper", "Scoring instincts, intelligence and positional versatility", "Requires normal pro seasoning"),
    (24, "VAN", "Adam Novotny", "LW", "Peterborough (OHL)", "Power Forward", "Motor, puck protection, traffic play and physical edge", "High-end creation is less proven"),
    (25, "OTT", "Jonas Lagerberg Hoen", "RW", "Leksand Jr. (Sweden)", "Sniper", "Dynamic skating, one-timer and shoot-first mentality", "Limited draft-year sample after injury"),
    (26, "MTL", "Gleb Pugachyov", "RW", "Nizhny Novgorod Jr. (Russia)", "Power Forward", "Size, puck protection, agility and one-on-one skill", "Needs time and consistency against men"),
    (27, "PHI", "Maksim Sokolovskii", "D", "London (OHL)", "2-Way Defenseman", "Size, mobility and balanced defensive tools", "Offense is not a primary strength"),
    (28, "ANA", "Marcus Nordmark", "LW", "Djurgarden Jr. (Sweden)", "Playmaker", "Poise, offense and playmaking from the wing", "Needs physical development"),
    (29, "VGK", "Juho Piiparinen", "D", "Tappara (Liiga)", "Defensive Defenseman", "Shutdown reads, edgework and net-front control", "Limited offensive upside"),
    (30, "CGY", "Jack Hextall", "C", "Youngstown (USHL)", "2-Way Forward", "Hockey IQ, 200-foot detail and growing offense", "No single elite offensive tool yet"),
    (31, "NSH", "Tommy Bleyl", "D", "Moncton (QMJHL)", "Offensive Defenseman", "Elite skating, deception and power-play creation", "Size and defensive strength need development"),
    (32, "OTT", "Jaxon Cover", "RW", "London (OHL)", "2-Way Forward", "Skating, competitiveness and a high growth ceiling", "Raw after a late transition to ice hockey"),
]


def _cells(row_html: str) -> list[str]:
    values = []
    for value in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S):
        values.append(html.unescape(re.sub(r"<[^>]+>", "", value)).strip())
    return values


def parse_round(path: Path, round_number: int) -> list[dict[str, object]]:
    page = path.read_text(encoding="utf-8")
    match = re.search(rf"<h1>Round\s+{round_number}</h1>.*?<table>(.*?)</table>", page, re.S)
    if match is None:
        raise RuntimeError(f"Round {round_number} table was not found in {path}")
    picks = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", match.group(1), re.S)[1:]:
        row = _cells(row_html)
        if len(row) < 6 or not row[2]:
            continue
        picks.append(
            {
                "round": round_number,
                "pick": int(row[0]),
                "team": row[1],
                "name": row[2],
                "position": row[3],
                "amateur_team": row[4],
                "cs_rank": row[5],
                "source": REDDIT_THREAD,
            }
        )
    return picks


def build_manifest(html_dir: Path) -> dict[str, object]:
    round_comment_ids = {
        2: "ou5gm4l",
        3: "ou5sxn8",
        4: "ou68hjd",
        5: "ou6gjr3",
        6: "ou6p07f",
        7: "ou6vy8g",
    }
    picks = [
        {
            "round": 1,
            "pick": pick,
            "team": team,
            "name": name,
            "position": position,
            "amateur_team": amateur_team,
            "cs_rank": "",
            "archetype": archetype,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "source": NHL_FIRST_ROUND,
        }
        for pick, team, name, position, amateur_team, archetype, strengths, weaknesses in FIRST_ROUND
    ]
    for round_number, comment_id in round_comment_ids.items():
        picks.extend(parse_round(html_dir / f"draft_{comment_id}.html", round_number))
    picks.sort(key=lambda row: int(row["pick"]))
    return {
        "draft_year": 2026,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [NHL_FIRST_ROUND, REDDIT_THREAD],
        "pick_count": len(picks),
        "picks": picks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/2026_draft_class.json"))
    args = parser.parse_args()
    manifest = build_manifest(args.html_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote {manifest['pick_count']} confirmed selections to {args.output}")


if __name__ == "__main__":
    main()
