from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import unicodedata

from .attribute_map import (
    GOALIE_ATTRIBUTE_SPECS,
    SKATER_ATTRIBUTE_SPECS,
    display_to_raw,
)
from .player_tools import build_player_snapshot_cache
from .rating_models import fit_ratings_to_overall
from .tdb_access import TdbAccess
from .team_tools import TeamRecord, franchise_rights_code, league_name_for_team, load_teams, organization_for_abbrev, resolve_playable_nhl_team, resolve_team_abbrev


POSITION_CODES = {"C": 0, "LW": 1, "RW": 2, "D": 3, "G": 4}
MAX_TEAM_INSTANCES = 40
DRAFT_ELC_CAP_HIT_RAW = 190  # $950,000 in NHL Legacy's $5,000 units.
DRAFT_ELC_YEARS = 3
STYLE_CODES = {
    "Defensive Defenseman": 1,
    "Offensive Defenseman": 2,
    "Enforcer Defenseman": 3,
    "2-Way Defenseman": 4,
    "Grinder": 5,
    "Playmaker": 6,
    "Sniper": 7,
    "Power Forward": 8,
    "2-Way Forward": 9,
    "Enforcer": 10,
    "Stand-Up Goalie": 0,
    "Hybrid Goalie": 1,
    "Butterfly Goalie": 2,
}
ARCHETYPE_KEYS = {
    "Defensive Defenseman": "defensive_defenseman",
    "Offensive Defenseman": "offensive_defenseman",
    "2-Way Defenseman": "two_way_defenseman",
    "Grinder": "grinder",
    "Playmaker": "playmaker",
    "Sniper": "sniper",
    "Power Forward": "power_forward",
    "2-Way Forward": "two_way_forward",
    "Enforcer": "enforcer",
}
POTENTIAL_STAR_CODES = {5.0: 1, 4.5: 2, 4.0: 3, 3.5: 4, 3.0: 5, 2.5: 6, 2.0: 7}
POTENTIAL_COLOR_CODES = {"Green": 1, "Yellow": 2, "Red": 4}
REDDIT_THREAD = "https://www.reddit.com/r/hockey/comments/1uh3jem/game_thread_nhl_entry_draft_rounds_27_27_june/"
ELITE_PROSPECTS_GRADES = "https://www.eliteprospects.com/news/2026-nhl-draft/elite-prospects-2026-nhl-draft-grades"


def _ep(
    strengths: str,
    weaknesses: str,
    *,
    archetype: str | None = None,
    overall_delta: int = 0,
    stars: float | None = None,
    color: str | None = None,
    **modifiers: int,
) -> dict[str, object]:
    return {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "archetype": archetype,
        "overall_delta": overall_delta,
        "stars": stars,
        "color": color,
        "modifiers": modifiers,
    }


# Concise game translations of David St-Louis' team-by-team draft analysis.
EP_PROFILES = {
    "gavinmckenna": _ep("Elite playmaking, deception and immediate power-play value", "Professional habits still need rounding out", archetype="Playmaker", stars=5.0, color="Green", passing=5, puck_control=4, deking=4, offensive_awareness=5),
    "ivarstenberg": _ep("Top-line playmaking with strong physical and professional habits", "Must translate control-driving impact to the NHL", archetype="Playmaker", overall_delta=1, stars=4.5, color="Green", passing=5, puck_control=3, strength=3, defensive_awareness=2),
    "calebmalhotra": _ep("High-floor center with defensive, physical and possession habits", "Skating and high-end skill need further growth", archetype="2-Way Forward", stars=4.5, color="Yellow", defensive_awareness=5, stick_checking=4, strength=3, speed=-2),
    "daxonrudolph": _ep("Anticipation, vision, playmaking and shooting create elite offensive upside", "Slower pace with limited physical and defensive engagement", archetype="Offensive Defenseman", stars=4.5, color="Red", passing=6, offensive_awareness=6, slap_shot_accuracy=4, puck_control=4, speed=-2, defensive_awareness=-5, body_checking=-5),
    "albertssmits": _ep("NHL-ready skating, physical tools and puck carrying", "Reads under pressure may require sheltered usage", archetype="2-Way Defenseman", stars=4.0, color="Green", speed=4, strength=4, puck_control=3, defensive_awareness=-2),
    "carsoncarels": _ep("Foundational all-around tools, physicality and aggressive rush defense", "Must balance frequent activation against stronger opponents", archetype="2-Way Defenseman", overall_delta=1, stars=4.5, color="Green", speed=3, body_checking=5, defensive_awareness=4, passing=3),
    "chasereid": _ep("High-end tools, transition offense, power-play vision and rush defense", "Offensive lean still needs mature risk management", archetype="Offensive Defenseman", overall_delta=2, stars=4.5, color="Green", speed=5, acceleration=4, passing=6, puck_control=5, offensive_awareness=6, defensive_awareness=3),
    "viggobjorck": _ep("NHL-ready pace, habits, decisions and rotational defense", "Small frame and uncertain top-six offensive ceiling", archetype="2-Way Forward", overall_delta=1, stars=4.5, color="Yellow", speed=5, acceleration=4, defensive_awareness=5, discipline=3, strength=-2),
    "keatonverhoeff": _ep("Top-four physical and skating tools", "Hockey sense concerns require structure and sheltering", archetype="2-Way Defenseman", stars=4.0, color="Yellow", speed=3, strength=4, body_checking=3, defensive_awareness=-3),
    "wyattcullen": _ep("Dynamic puck rushing, deception, dangling and power-play creation", "Star-level projection remains a development gamble", archetype="Playmaker", overall_delta=2, stars=4.5, color="Red", speed=4, puck_control=6, deking=6, passing=5, offensive_awareness=4),
    "tynanlawrence": _ep("Strong tool base with a very high likelihood of an NHL role", "More likely supportive middle-six than primary creator", archetype="2-Way Forward", stars=4.0, color="Green", passing=3, defensive_awareness=3, endurance=3),
    "alexandercommand": _ep("Power, wall play, constant defensive energy and safe NHL habits", "Likely middle-six rather than top-line offensive driver", archetype="2-Way Forward", stars=4.0, color="Green", strength=5, body_checking=4, defensive_awareness=5, endurance=4),
    "maltegustafsson": _ep("Range, skating, quick puck movement and strong defensive projection", "Must continue building strength and consistency", archetype="2-Way Defenseman", overall_delta=1, stars=4.5, color="Yellow", speed=4, passing=4, defensive_awareness=5, stick_checking=4),
    "oscarhemming": _ep("Size, vision, hockey sense and power-playmaker upside", "Major performance inconsistency creates projection risk", archetype="Power Forward", stars=4.5, color="Red", strength=5, passing=4, puck_control=3, body_checking=3, endurance=-2),
    "nikitaklepov": _ep("Creative passing, wall retrieval and forechecking motor", "Needs continued physical development", archetype="Playmaker", stars=4.0, color="Green", passing=6, puck_control=4, endurance=4, offensive_awareness=3),
    "maddoxdagenais": _ep("Relentless forecheck and NHL-grade scoring shot", "Skating and all-around creation need refinement", archetype="Power Forward", stars=4.0, color="Green", body_checking=5, strength=5, wrist_shot_accuracy=5, wrist_shot_power=5),
    "ethanbelchetz": _ep("Power, skill, playmaking and top-six physical upside", "Needs to rediscover consistent edge and meanness", archetype="Power Forward", stars=4.5, color="Yellow", strength=6, body_checking=5, puck_control=3, passing=3),
    "oliversuvanto": _ep("Mature defensive and physical foundation", "Must translate playmaking and add more physical impact", archetype="2-Way Forward", stars=4.0, color="Green", defensive_awareness=6, stick_checking=4, strength=4, passing=2),
    "eltonhermansson": _ep("Top-six puck skill, deception and power-play creativity", "Physical game and decisions need refinement", archetype="Playmaker", stars=4.0, color="Green", deking=6, puck_control=5, passing=5, offensive_awareness=4, strength=-3),
    "iliamorozov": _ep("NHL-like rotations, engagement and dependable bottom-six floor", "Skating and playmaking limit his higher-role projection", archetype="2-Way Forward", stars=4.0, color="Green", defensive_awareness=5, body_checking=3, speed=-2, passing=-1),
    "ryanlin": _ep("NHL-ready angles, stick habits and puck retrieval", "Needs more consistent offensive creation", archetype="2-Way Defenseman", stars=4.0, color="Green", defensive_awareness=5, stick_checking=5, speed=3, offensive_awareness=-1),
    "liamruck": _ep("Motor, physical engagement, playmaking vision and off-pass shooting", "Small frame and skating cap the projection", archetype="Playmaker", stars=4.0, color="Yellow", passing=4, wrist_shot_accuracy=4, endurance=4, strength=-2, speed=-2),
    "jphurlbert": _ep("Dual-threat scoring and elite ability to find open ice", "All-around game requires significant rounding out", archetype="Sniper", stars=4.0, color="Green", offensive_awareness=5, wrist_shot_accuracy=5, hand_eye=4, defensive_awareness=-2),
    "adamnovotny": _ep("High-end shooting, forechecking and top-six play-driving tools", "Needs to sustain pace and consistency", archetype="Power Forward", stars=4.0, color="Green", wrist_shot_accuracy=5, body_checking=5, endurance=4, strength=4),
    "jonaslagerberghoen": _ep("Powerful skating and shooting tools", "Older, injury-limited profile with major hockey-sense risk", archetype="Sniper", stars=4.0, color="Red", speed=4, wrist_shot_power=6, wrist_shot_accuracy=4, passing=-4, offensive_awareness=-3),
    "glebpugachyov": _ep("Rare speed, force and punishing physical engagement", "Reads and passing remain uncertain", archetype="Power Forward", stars=4.0, color="Red", speed=4, body_checking=7, strength=5, aggressiveness=6, passing=-3, offensive_awareness=-2),
    "maksimsokolovskii": _ep("Massive top-four shutdown tools with a useful puck-skill foundation", "Skill flashes were inconsistent in limited minutes", archetype="Defensive Defenseman", overall_delta=2, stars=4.0, color="Green", strength=8, body_checking=7, shot_blocking=6, defensive_awareness=6, stick_checking=5, speed=-2),
    "marcusnordmark": _ep("Above-average tools across the board and real boom potential", "Floating, forced plays and weak all-around engagement", archetype="Playmaker", stars=4.0, color="Red", puck_control=4, passing=4, deking=3, defensive_awareness=-4, endurance=-3),
    "juhopiiparinen": _ep("High-floor, reliable do-everything defensive profile", "Most likely a third-pair player rather than impact creator", archetype="2-Way Defenseman", stars=3.5, color="Green", defensive_awareness=5, stick_checking=4, discipline=4, offensive_awareness=-2),
    "jackhextall": _ep("NHL habits, board play and playmaking", "Skating requires meaningful development", archetype="2-Way Forward", stars=4.0, color="Yellow", passing=4, strength=3, defensive_awareness=3, speed=-4),
    "tommybleyl": _ep("Elite skating, puck carrying and transition-play upside", "Playmaking and defensive detail are still developing", archetype="Offensive Defenseman", overall_delta=2, stars=4.0, color="Green", speed=6, acceleration=5, puck_control=5, passing=4, defensive_awareness=-2),
    "jaxoncover": _ep("High-end skill, advanced reads and major development runway", "Newer ice player still refining forechecking and details", archetype="Playmaker", stars=4.5, color="Red", puck_control=5, passing=5, offensive_awareness=5, deking=4),
    "xaviervilleneuve": _ep("Exceptional skating, vision, one-on-one skill and power-play-quarterback upside", "Size and physical projection create substantial risk", archetype="Offensive Defenseman", overall_delta=3, stars=4.5, color="Red", speed=6, passing=7, puck_control=6, deking=5, offensive_awareness=6, strength=-5, body_checking=-5),
    "ryanroobroeck": _ep("Top-six tools and NHL scoring shot", "Urgency, aggression and play-driving attitude must improve", archetype="Sniper", overall_delta=2, stars=4.0, color="Red", wrist_shot_accuracy=6, wrist_shot_power=5, offensive_awareness=3, endurance=-3, aggressiveness=-3),
    "tobiastrejbal": _ep("Top-ranked goalie with pro habits and starter-level tools", "Still projects initially as a tandem goalie", overall_delta=3, stars=4.0, color="Green", agility=4, consistency=4, angles=3, rebound_control=3),
    "egorshilov": _ep("Rare vision, deception and rush orchestration", "Very low pace and poor habits create a low floor", archetype="Playmaker", overall_delta=2, stars=4.0, color="Red", passing=7, deking=6, offensive_awareness=6, speed=-5, endurance=-5),
    "nikitashcherbakov": _ep("Agile 6-foot-5 defender with puck skill on both sides", "Needs time to turn tools into consistent reads", archetype="2-Way Defenseman", overall_delta=2, stars=4.0, color="Green", agility=5, puck_control=4, passing=4, defensive_awareness=3),
    "alexanderbilecki": _ep("High-end tools and spectacular offensive flashes", "Limited role and unfinished defense create uncertainty", archetype="Offensive Defenseman", overall_delta=2, stars=4.0, color="Red", puck_control=5, passing=5, offensive_awareness=5, defensive_awareness=-4),
    "juusoainasto": _ep("Flashy, confident goaltending with elite battle level", "Wide outcome ranging from backup to starter", overall_delta=3, stars=4.0, color="Red", agility=5, consistency=2, breakaway=4, poise=4),
    "dmitriborichev": _ep("Second-ranked goalie with strong value and starter tools", "Requires normal technical development", overall_delta=3, stars=4.0, color="Yellow", consistency=4, angles=4, rebound_control=3),
    "yuriivanov": _ep("Athleticism and strong junior results", "Play reading and posture limit upside", overall_delta=-2, stars=3.0, color="Yellow", agility=3, speed=2, vision=-5, angles=-4, consistency=-3),
    "robertohenriquez": _ep("Dynamic skating and high-end USHL puck stopping", "Undersized profile likely caps him near tandem level", overall_delta=3, stars=3.5, color="Green", agility=6, speed=5, consistency=4, rebound_control=2),
    "martinpsohlavec": _ep("Large frame and sound baseline positioning", "Reads and technique received late-round grades", overall_delta=-2, stars=3.0, color="Yellow", angles=3, vision=-5, consistency=-4, rebound_control=-3),
    "simasignatavicius": _ep("Physical utility game and passing talent", "Skating needs continued development", archetype="2-Way Forward", stars=3.5, color="Green", body_checking=4, passing=3, defensive_awareness=3, speed=-3),
    "rydercali": _ep("Physical utility tools and passing", "Play-driving impact is inconsistent", archetype="2-Way Forward", stars=3.5, color="Green", strength=4, body_checking=3, passing=3, offensive_awareness=-2),
    "liamlefebvre": _ep("Pro-style physical game and off-pass scoring", "Mechanical refinement suggests a depth ceiling", archetype="Power Forward", overall_delta=-1, stars=3.5, color="Yellow", strength=4, body_checking=4, wrist_shot_accuracy=3, deking=-3),
    "timofeiruntso": _ep("Athletic puck carrier and passer with No. 4 defender upside", "Older curve and unfinished details", archetype="Offensive Defenseman", overall_delta=2, stars=4.0, color="Yellow", speed=4, puck_control=5, passing=5, defensive_awareness=-2),
    "markusruck": _ep("Connective playmaking that links possessions", "Small frame and skating issues cap certainty", archetype="Playmaker", stars=3.5, color="Green", passing=6, puck_control=4, speed=-3, strength=-3),
    "brekliske": _ep("Clever habits, puck movement and transition control", "Tools must improve to reach the NHL", archetype="2-Way Defenseman", stars=3.5, color="Yellow", passing=5, defensive_awareness=4, speed=-2, strength=-2),
    "caseymutryn": _ep("Power playmaking, puck retrieval and lineup versatility", "Skating is the main limiting tool", archetype="Power Forward", overall_delta=2, stars=4.0, color="Green", passing=5, body_checking=4, endurance=4, speed=-3),
    "ethanmackenzie": _ep("Pace, meanness and offensive upside", "Must sharpen decisions and defensive consistency", archetype="2-Way Defenseman", overall_delta=2, stars=3.5, color="Green", speed=5, aggressiveness=5, body_checking=4, offensive_awareness=2),
    "zacholsen": _ep("Explosive skating, NHL build and checking-line energy", "Limited offensive projection", archetype="Grinder", stars=3.5, color="Green", speed=7, acceleration=6, body_checking=5, offensive_awareness=-4),
    "mansgudmundsson": _ep("Tall, physical right-shot defender with useful pace", "Puck decisions and offensive ceiling remain limited", archetype="Defensive Defenseman", stars=3.5, color="Yellow", strength=5, body_checking=5, speed=3, defensive_awareness=3),
    "adamvalentini": _ep("Aggression, net-front skill and playmaking flashes", "Needs to rediscover his former creative level", archetype="Power Forward", stars=3.5, color="Red", aggressiveness=6, strength=4, hand_eye=4, passing=2),
    "niklasaaramolsen": _ep("Developed shooting skill", "Skating and all-around skill set need rounding out", archetype="Sniper", stars=3.5, color="Yellow", wrist_shot_accuracy=5, wrist_shot_power=4, passing=-2, defensive_awareness=-2),
    "dmitriivchenko": _ep("Calm, technical goaltending with breakout potential", "Needs to prove it in a starting workload", overall_delta=2, stars=3.5, color="Green", consistency=4, angles=4, rebound_control=4, poise=3),
    "samuelhrenak": _ep("Highly competitive goalie on a rapid upward trajectory", "Still needs technical refinement", overall_delta=2, stars=3.5, color="Green", breakaway=4, poise=4, consistency=3),
    "giorgospantelas": _ep("Modern shutdown tools across the board", "Puck management remains a clear weakness", archetype="Defensive Defenseman", overall_delta=2, stars=3.5, color="Green", defensive_awareness=6, stick_checking=5, shot_blocking=5, passing=-4, puck_control=-3),
    "jonahsivertson": _ep("Heavy pass-first game with strong contextual production", "Projects more as a complementary player", archetype="Power Forward", overall_delta=2, stars=3.5, color="Green", passing=5, strength=4, body_checking=3),
    "jakubfloris": _ep("Robust shutdown game with a clear NHL role", "Limited offensive ceiling", archetype="Defensive Defenseman", overall_delta=2, stars=3.5, color="Green", defensive_awareness=5, stick_checking=5, body_checking=5, offensive_awareness=-4),
    "lukenhuff": _ep("Mobile defender with developmental upside", "Long-term projection remains uncertain", archetype="2-Way Defenseman", stars=3.0, color="Yellow", speed=3, defensive_awareness=2),
    "jakegustafson": _ep("Defensive detail and flashes of fourth-line center skill", "Offensive ceiling is limited", archetype="2-Way Forward", stars=3.0, color="Green", defensive_awareness=5, stick_checking=4, offensive_awareness=-3),
    "olapalme": _ep("Strong all-around value with balanced tools", "No standout top-line trait", archetype="2-Way Defenseman", overall_delta=2, stars=3.5, color="Green", defensive_awareness=3, passing=3, speed=3),
    "zachwooten": _ep("Explosive speed, sudden breakout and checking upside", "Scoring growth must prove sustainable", archetype="Grinder", overall_delta=2, stars=3.5, color="Green", speed=7, acceleration=6, body_checking=3, offensive_awareness=-2),
    "noahkosick": _ep("Pure playmaking with late physical growth and high-end flashes", "Very long development path", archetype="Playmaker", overall_delta=2, stars=3.5, color="Red", passing=6, puck_control=4, strength=2, defensive_awareness=-3),
    "johnparsons": _ep("Refined, reliable and technically mature goaltending", "Older prospect with limited ceiling", overall_delta=2, stars=3.0, color="Green", consistency=5, angles=4, rebound_control=4),
    "alofatunoataamu": _ep("Physical, mobile shutdown profile", "Puck skill and offense are limited", archetype="Defensive Defenseman", overall_delta=2, stars=3.0, color="Green", body_checking=6, strength=5, speed=3, defensive_awareness=4, offensive_awareness=-4),
}


@dataclass(frozen=True, slots=True)
class DraftProspect:
    round: int
    pick: int
    team: str
    name: str
    position: str
    amateur_team: str
    cs_rank: str
    archetype: str
    strengths: str
    weaknesses: str
    projected_overall: int
    potential_stars: float
    potential_color: str
    source: str
    scouting_source: str
    scouting_modifiers: dict[str, int]


@dataclass(frozen=True, slots=True)
class DraftRosterStatus:
    prospect: DraftProspect
    status: str
    player_id: int | None
    current_team: str


def _data_path() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        candidate = Path(bundled) / "data" / "2026_draft_class.json"
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parents[2] / "data" / "2026_draft_class.json"


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in folded if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]", "", ascii_value.lower())


_DRAFT_NAME_ALIASES = {
    "calebmalholtra": "calebmalhotra",
    "noelnord": "noelnordh",
    "olegkulebyakin": "olegkulebiakin",
    "viktorfyodorov": "viktorfedorov",
    "yegorshilov": "egorshilov",
    "zachlansard": "zacharylansard",
    "zacolsen": "zacholsen",
}


def _draft_name_key(value: str) -> str:
    normalized = _normalized(value)
    return _DRAFT_NAME_ALIASES.get(normalized, normalized)


def _draft_match_priority(bio: dict[str, object], prospect_name: str) -> tuple[int, int]:
    name = f"{bio.get('PedH') or ''} {bio.get('RMbQ') or ''}".strip()
    return (
        int(_normalized(name) == _normalized(prospect_name)),
        int(bio.get("zKKG") or 0),
    )


def _central_rank(value: str) -> int | None:
    match = re.search(r"(\d+)\s*$", value or "")
    return int(match.group(1)) if match else None


def _default_archetype(position: str, pick: int, cs_rank: str) -> str:
    rank = _central_rank(cs_rank)
    if position == "G":
        return "Hybrid Goalie"
    if position == "D":
        if rank is not None and rank <= 35:
            return "2-Way Defenseman"
        return "Defensive Defenseman" if pick % 3 == 0 else "2-Way Defenseman"
    if position == "C":
        return "2-Way Forward" if pick % 3 == 0 else "Playmaker"
    return "Sniper" if pick % 2 == 0 else "Playmaker"


def _projected_overall(round_number: int, pick: int, cs_rank: str) -> int:
    ranges = {
        1: (77, 69),
        2: (70, 64),
        3: (66, 60),
        4: (63, 57),
        5: (60, 54),
        6: (57, 52),
        7: (55, 51),
    }
    first_pick = 1 if round_number == 1 else 33 + (round_number - 2) * 32
    top, bottom = ranges[round_number]
    index = max(0, min(31, pick - first_pick))
    projected = round(top - (top - bottom) * index / 31)
    rank = _central_rank(cs_rank)
    if rank is not None and rank <= 20 and round_number >= 3:
        projected += 1
    return max(bottom, min(top, projected))


def _projected_potential(round_number: int, pick: int, cs_rank: str) -> tuple[float, str]:
    rank = _central_rank(cs_rank)
    if round_number == 1:
        if pick == 1:
            return 5.0, "Green"
        if pick <= 3:
            return 4.5, "Green"
        if pick <= 10:
            return 4.5, "Yellow"
        if pick <= 24:
            return 4.0, "Green"
        return 4.0, "Yellow"
    if round_number == 2:
        return (4.0, "Yellow") if pick <= 45 else (3.5, "Green" if rank and rank <= 20 else "Yellow")
    if round_number == 3:
        return 3.5, "Green" if rank and rank <= 20 else "Yellow"
    if round_number == 4:
        return (3.5, "Yellow") if pick <= 112 or (rank and rank <= 20) else (3.0, "Yellow")
    if round_number == 5:
        return 3.0, "Yellow"
    if round_number == 6:
        return 3.0, "Yellow" if rank and rank <= 40 else "Red"
    return (3.0, "Red") if rank and rank <= 30 else (2.5, "Yellow")


def load_2026_draft_class() -> list[DraftProspect]:
    payload = json.loads(_data_path().read_text(encoding="utf-8"))
    prospects = []
    for row in payload.get("picks") or []:
        position = str(row.get("position") or "C").upper()
        pick = int(row["pick"])
        round_number = int(row["round"])
        cs_rank = str(row.get("cs_rank") or "")
        name = str(row["name"])
        profile = EP_PROFILES.get(_normalized(name), {})
        archetype = str(profile.get("archetype") or row.get("archetype") or _default_archetype(position, pick, cs_rank))
        stars, color = _projected_potential(round_number, pick, cs_rank)
        stars = float(profile.get("stars") or stars)
        color = str(profile.get("color") or color)
        overall = _projected_overall(round_number, pick, cs_rank) + int(profile.get("overall_delta") or 0)
        overall = max(51 if round_number >= 2 else 69, min(74 if round_number >= 2 else 77, overall))
        prospects.append(
            DraftProspect(
                round=round_number,
                pick=pick,
                team=str(row["team"]),
                name=name,
                position=position,
                amateur_team=str(row.get("amateur_team") or ""),
                cs_rank=cs_rank,
                archetype=archetype,
                strengths=str(profile.get("strengths") or row.get("strengths") or _generic_strengths(position, archetype)),
                weaknesses=str(profile.get("weaknesses") or row.get("weaknesses") or _generic_weaknesses(round_number)),
                projected_overall=overall,
                potential_stars=stars,
                potential_color=color,
                source=str(row.get("source") or REDDIT_THREAD),
                scouting_source=ELITE_PROSPECTS_GRADES if profile else "",
                scouting_modifiers={str(key): int(value) for key, value in dict(profile.get("modifiers") or {}).items()},
            )
        )
    return prospects


def _generic_strengths(position: str, archetype: str) -> str:
    if position == "G":
        return "Developmental goalie profile; balanced baseline ratings"
    labels = {
        "Sniper": "Shot, release and offensive finishing",
        "Playmaker": "Puck skill, passing and offensive creation",
        "Power Forward": "Strength, puck protection and interior offense",
        "2-Way Forward": "Hockey sense and balanced 200-foot play",
        "Offensive Defenseman": "Skating, passing and puck movement",
        "Defensive Defenseman": "Positioning, checking and defensive detail",
        "2-Way Defenseman": "Balanced mobility, puck movement and defending",
    }
    return labels.get(archetype, "Balanced developmental tools")


def _generic_weaknesses(round_number: int) -> str:
    if round_number <= 2:
        return "Requires normal physical and tactical development"
    return "Projection is uncertain; needs substantial development time"


def scan_draft_class(db_path: Path, prospects: list[DraftProspect] | None = None) -> list[DraftRosterStatus]:
    prospects = prospects or load_2026_draft_class()
    cache = build_player_snapshot_cache(db_path)
    by_name: dict[str, list[dict[str, object]]] = {}
    for bio in cache.bio_rows:
        name = f"{bio.get('PedH') or ''} {bio.get('RMbQ') or ''}".strip()
        if name:
            by_name.setdefault(_draft_name_key(name), []).append(bio)
    instance_teams_by_id: dict[int, list[int]] = {}
    for row in cache.instance_rows:
        instance_teams_by_id.setdefault(int(row.get("TWSX") or -1), []).append(
            int(row["BSXd"]) if row.get("BSXd") is not None else -1
        )
    relation_by_player: dict[int, list[int]] = {}
    for relation in cache.relation_rows:
        relation_by_player.setdefault(int(relation.get("qFky") or -1), []).append(int(relation.get("qEfv") or -1))
    team_by_code = {team.code: team for team in load_teams(db_path)}
    statuses = []
    for prospect in prospects:
        matches = by_name.get(_draft_name_key(prospect.name), [])
        if not matches:
            statuses.append(DraftRosterStatus(prospect, "Missing", None, ""))
            continue
        bio = max(matches, key=lambda row: _draft_match_priority(row, prospect.name))
        player_id = int(bio.get("zIBw") or -1)
        team_names = []
        for instance_id in relation_by_player.get(player_id, []):
            for team_code in instance_teams_by_id.get(instance_id, []):
                if team_code == 255 and "Free Agents" not in team_names:
                    team_names.append("Free Agents")
                    continue
                team = team_by_code.get(team_code)
                if team and team.abbrev not in team_names:
                    team_names.append(team.abbrev)
        statuses.append(DraftRosterStatus(prospect, "Present", player_id, ", ".join(team_names)))
    return statuses


def _team_for_amateur_club(prospect: DraftProspect, teams: list[TeamRecord]) -> TeamRecord | None:
    club = prospect.amateur_team.split("(", 1)[0].strip()
    normalized_club = _normalized(club.replace("JR.", "").replace("JR", ""))
    if "USAU18" in _normalized(prospect.amateur_team):
        return next((team for team in teams if team.abbrev.upper() == "USN1"), None)
    candidates: list[tuple[int, TeamRecord]] = []
    for team in teams:
        values = [_normalized(team.city), _normalized(team.name), _normalized(f"{team.city} {team.name}")]
        score = 0
        for value in values:
            if not value:
                continue
            if value == normalized_club:
                score = max(score, 100)
            elif len(normalized_club) >= 5 and (normalized_club in value or value in normalized_club):
                score = max(score, 70)
        if score:
            candidates.append((score, team))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1].code))
    return candidates[0][1]


def _expansion_holding_team(team_abbrev: str, teams: list[TeamRecord]) -> TeamRecord | None:
    holding_abbrev = {"SEA": "CVF", "UTA": "UMS", "VGK": "HSK"}.get(team_abbrev)
    if holding_abbrev is None:
        return None
    return next((team for team in teams if team.abbrev.upper() == holding_abbrev), None)


def _semantic_skater_ratings(prospect: DraftProspect) -> dict[str, int]:
    overall = prospect.projected_overall
    ratings = {
        "speed": overall + 8,
        "body_checking": overall - 1,
        "endurance": overall + 5,
        "puck_control": overall + 3,
        "passing": overall + 2,
        "slap_shot_power": overall + 5,
        "slap_shot_accuracy": overall,
        "wrist_shot_power": overall + 5,
        "wrist_shot_accuracy": overall + 1,
        "agility": overall + 7,
        "strength": overall + 1,
        "acceleration": overall + 7,
        "balance": overall + 3,
        "faceoffs": overall if prospect.position == "C" else 36,
        "durability": overall + 8,
        "deking": overall + 2,
        "aggressiveness": overall - 2,
        "poise": overall - 2,
        "hand_eye": overall + 1,
        "shot_blocking": overall - 2,
        "offensive_awareness": overall,
        "defensive_awareness": overall,
        "discipline": overall + 4,
        "fighting_skill": 55,
        "stick_checking": overall,
    }
    modifiers = {
        "Sniper": {"wrist_shot_accuracy": 7, "slap_shot_accuracy": 4, "offensive_awareness": 4, "passing": -3},
        "Playmaker": {"passing": 7, "puck_control": 5, "deking": 5, "offensive_awareness": 4, "wrist_shot_accuracy": -2},
        "Power Forward": {"strength": 7, "body_checking": 6, "balance": 5, "wrist_shot_power": 4, "speed": -2},
        "2-Way Forward": {"defensive_awareness": 5, "stick_checking": 4, "discipline": 3, "passing": 2},
        "Offensive Defenseman": {"passing": 6, "puck_control": 4, "offensive_awareness": 5, "slap_shot_power": 4, "body_checking": -3},
        "Defensive Defenseman": {"defensive_awareness": 6, "stick_checking": 6, "shot_blocking": 6, "body_checking": 4, "offensive_awareness": -4},
        "2-Way Defenseman": {"defensive_awareness": 4, "stick_checking": 4, "shot_blocking": 3, "passing": 3},
    }
    for field, delta in modifiers.get(prospect.archetype, {}).items():
        ratings[field] += delta
    for field, delta in prospect.scouting_modifiers.items():
        if field in ratings:
            ratings[field] += delta
    ratings = {field: max(36, min(88, value)) for field, value in ratings.items()}
    archetype_key = ARCHETYPE_KEYS.get(prospect.archetype, "two_way_forward")
    suggested = fit_ratings_to_overall(
        ratings,
        archetype_key,
        overall,
        min_stat=36,
        max_stat=88,
        position=prospect.position,
    ).suggested_ratings

    # Legacy penalizes young players far more heavily than our linear model when
    # awareness is near their nominal overall. Keep both awareness ratings above
    # the target, then preserve the player's offensive/defensive identity.
    awareness_base = overall + max(3, (75 - overall) // 2)
    awareness_offsets = {
        "Sniper": (4, -1),
        "Playmaker": (4, -1),
        "Power Forward": (3, 0),
        "2-Way Forward": (1, 2),
        "Offensive Defenseman": (4, 0),
        "Defensive Defenseman": (-2, 5),
        "2-Way Defenseman": (1, 3),
    }
    offensive_offset, defensive_offset = awareness_offsets.get(prospect.archetype, (1, 1))
    suggested["offensive_awareness"] = max(
        suggested["offensive_awareness"],
        min(88, awareness_base + offensive_offset),
    )
    suggested["defensive_awareness"] = max(
        suggested["defensive_awareness"],
        min(88, awareness_base + defensive_offset),
    )
    return {field: min(91, value + 3) for field, value in suggested.items()}


def _skater_raw_updates(prospect: DraftProspect) -> dict[str, int]:
    labels = {
        "Speed": "speed", "Body Checking": "body_checking", "Endurance": "endurance",
        "Puck Control": "puck_control", "Passing": "passing", "Slap Shot Power": "slap_shot_power",
        "Slap Shot Accuracy": "slap_shot_accuracy", "Wrist Shot Power": "wrist_shot_power",
        "Wrist Shot Accuracy": "wrist_shot_accuracy", "Agility": "agility", "Strength": "strength",
        "Acceleration": "acceleration", "Balance": "balance", "Face-offs": "faceoffs",
        "Durability": "durability", "Deking": "deking", "Aggressiveness": "aggressiveness",
        "Poise": "poise", "Hand-Eye": "hand_eye", "Shot Blocking": "shot_blocking",
        "Off. Awareness": "offensive_awareness", "Def. Awareness": "defensive_awareness",
        "Discipline": "discipline", "Fighting Skill": "fighting_skill", "Stick Checking": "stick_checking",
    }
    semantic = _semantic_skater_ratings(prospect)
    updates = {}
    for spec in SKATER_ATTRIBUTE_SPECS:
        key = labels.get(spec.label)
        if key:
            updates[spec.field] = display_to_raw(spec, semantic[key])
    updates.update(
        {
            "EksY": 10 if prospect.archetype == "Playmaker" else 7,
            "baRY": 4 if prospect.position == "D" and prospect.archetype == "Offensive Defenseman" else 8,
            "hlsv": 2 if prospect.archetype == "Sniper" else 11 if prospect.archetype == "Playmaker" else 7,
        }
    )
    return updates


def _goalie_raw_updates(prospect: DraftProspect) -> dict[str, int]:
    overall = prospect.projected_overall
    displays = {
        "Glove Side Low": overall, "Glove Side High": overall, "Stick Side High": overall,
        "Stick Side Low": overall, "Five Hole": overall, "Agility": overall + 5, "Speed": overall + 4,
        "Poke Check": overall, "Consistency": 1, "Breakaway": overall, "Endurance": overall + 4,
        "Shot Recovery": overall + 1, "Rebound Control": overall + 1, "Poise": overall - 1,
        "Passing": overall - 2, "Angles": overall + 2, "Puck Play Frequency": overall,
        "Aggressiveness": overall, "Durability": overall + 7, "Vision": overall,
    }
    modifier_labels = {
        "agility": "Agility", "speed": "Speed", "consistency": "Consistency",
        "breakaway": "Breakaway", "poise": "Poise", "angles": "Angles",
        "rebound_control": "Rebound Control", "vision": "Vision",
        "poke_check": "Poke Check", "shot_recovery": "Shot Recovery",
    }
    for key, delta in prospect.scouting_modifiers.items():
        label = modifier_labels.get(key)
        if label in displays:
            displays[label] += delta
    updates: dict[str, int] = {}
    for spec in GOALIE_ATTRIBUTE_SPECS:
        value = displays[spec.label]
        if spec.mode == "raw":
            # Goalie consistency is a native 0-2 tendency, not an EA-style
            # 36-99 attribute.  Applying the usual display offset here wrote
            # values around 80 and caused TDB to reject created goalies.
            updates[spec.field] = max(spec.min_value, min(spec.max_value, int(value)))
        else:
            updates[spec.field] = display_to_raw(spec, max(36, min(91, value + 3)))
    return updates


def _find_template(cache, goalie: bool):
    names = [("Tobias", "Trejbal"), ("Kevin", "Mandolese")] if goalie else [("Gavin", "McKenna"), ("Ivar", "Stenberg")]
    for first_name, last_name in names:
        snapshot = cache.get_player_snapshot(first_name, last_name)
        ratings = None if snapshot is None else (
            snapshot.goalie_ratings_row if goalie else snapshot.ratings_row
        )
        if snapshot is not None and snapshot.instance_rows and ratings is not None:
            return snapshot
    # Roster revisions can hide or repurpose the preferred placeholders.  A
    # template only needs a valid bio, matching ratings row and one real team
    # instance; select the first stable matching player instead of failing the
    # entire draft sync because a particular name is absent.
    for bio in cache.bio_rows:
        if (int(bio.get("aljv") or -1) == POSITION_CODES["G"]) != goalie:
            continue
        snapshot = cache.get_player_snapshot(
            str(bio.get("PedH") or ""),
            str(bio.get("RMbQ") or ""),
            int(bio.get("zIBw") or -1),
        )
        ratings = None if snapshot is None else (
            snapshot.goalie_ratings_row if goalie else snapshot.ratings_row
        )
        if snapshot is not None and snapshot.instance_rows and ratings is not None:
            return snapshot
    raise RuntimeError("A safe prospect template could not be found in this roster.")


def _franchise_rights_code(target_team: TeamRecord, teams: list[TeamRecord]) -> int:
    """Return the 5-bit NHL organization code used by franchise imports.

    WBbd/uWgv can only store 0-31. Modern display/system teams such as LAK
    and SJS can use higher roster codes, but their franchise rights must point
    at the playable legacy organization (LA and SJ). Seattle and Vegas have
    no legacy 0-29 counterpart, so their custom-team instance and cPbu.BSXd
    ownership remain authoritative and the narrow rights fields stay zero.
    """
    return franchise_rights_code(target_team, teams)


def _franchise_prospect_team(target_team: TeamRecord, teams: list[TeamRecord]) -> TeamRecord:
    """Place drafted prospects in the organization's franchise system pool."""
    organization = organization_for_abbrev(target_team.abbrev)
    candidates = sorted(
        (
            team for team in teams
            if league_name_for_team(team) == "Organization"
            and organization_for_abbrev(team.abbrev) == organization
        ),
        key=lambda team: team.code,
    )
    return candidates[0] if candidates else target_team


def _draft_contract_updates(*, is_undrafted: bool, force_free_agent: bool) -> dict[str, int]:
    """Return a franchise-stable contract state for a newly drafted player.

    Existing prospect records often carry a zero-term placeholder contract.
    Franchise initialization treats those players as free agents even when
    their rights and roster instance point at the correct organization.  A
    consistent three-year, two-way entry-level deal keeps every supported-team
    selection in organizational depth.  Expansion teams intentionally routed
    to free agency retain an uncontracted state.
    """
    if is_undrafted or force_free_agent:
        return {"QwoG": 0, "dhKk": 0, "GDhI": 0, "gHmt": 0, "yvUt": 0, "DVoL": 0}
    return {
        "QwoG": 1,
        "dhKk": DRAFT_ELC_CAP_HIT_RAW,
        "GDhI": DRAFT_ELC_YEARS,
        "gHmt": 1,
        "yvUt": 1,
        "DVoL": 1,
    }


def _draft_metadata_updates(
    prospect: DraftProspect,
    *,
    is_undrafted: bool,
) -> dict[str, int]:
    """Return the actual cPbu draft-history fields for a selection.

    WzKY is draft year, Ujcc is draft round, and WfTt is the overall pick.
    QDTK is skin colour and must never be changed by the draft importer.
    """
    if is_undrafted:
        return {"WzKY": 255, "Ujcc": 0, "WfTt": 0, "QPrV": 0}
    return {
        "WzKY": 126,
        "Ujcc": prospect.round,
        "WfTt": prospect.pick,
        "QPrV": 0,
    }


def _consolidate_draft_player_instances(
    db_path: Path,
    prospects: list[DraftProspect],
    *,
    free_agent_teams: set[str],
) -> None:
    """Leave each drafted player with one unambiguous active roster instance.

    A player may legitimately have NHL rights while playing in junior, but the
    retail roster format does not safely represent that by linking the same
    bio to two active ulGe rows. Franchise import treats both rows as players,
    producing visible duplicates. Rights remain in cPbu; ulGe is consolidated
    to the requested NHL team (or free agency for disabled expansion teams).
    """
    cache = build_player_snapshot_cache(db_path)
    teams = load_teams(db_path)
    bio_by_name: dict[str, tuple[int, dict[str, object]]] = {}
    for bio_index, bio in enumerate(cache.bio_rows):
        name = f"{bio.get('PedH') or ''} {bio.get('RMbQ') or ''}".strip()
        if name:
            key = _draft_name_key(name)
            previous = bio_by_name.get(key)
            if previous is None or _draft_match_priority(bio, name) > _draft_match_priority(previous[1], name):
                bio_by_name[key] = (bio_index, bio)

    relations_by_player: dict[int, list[tuple[int, int]]] = {}
    relation_indexes_by_instance: dict[int, set[int]] = {}
    for relation_index, relation in enumerate(cache.relation_rows):
        player_id = int(relation.get("qFky") or -1)
        instance_id = int(relation.get("qEfv") or -1)
        relations_by_player.setdefault(player_id, []).append((relation_index, instance_id))
        relation_indexes_by_instance.setdefault(instance_id, set()).add(relation_index)
    instance_rows_by_id: dict[int, list[tuple[int, dict[str, object]]]] = {}
    for instance_index, instance in enumerate(cache.instance_rows):
        instance_id = int(instance.get("TWSX") or -1)
        instance_rows_by_id.setdefault(instance_id, []).append((instance_index, instance))

    plans: list[dict[str, object]] = []
    relation_indexes_to_remove: set[int] = set()
    instance_indexes_to_remove: set[int] = set()
    keep_instance_indexes: set[int] = set()
    for prospect in sorted(prospects, key=lambda item: item.pick):
        bio_match = bio_by_name.get(_draft_name_key(prospect.name))
        if bio_match is None:
            continue
        bio_index, bio = bio_match
        player_id = int(bio.get("zIBw") or -1)
        target_team = resolve_playable_nhl_team(prospect.team, teams)
        if target_team is None:
            raise RuntimeError(f"Roster team not found for {prospect.team} ({prospect.name}).")
        placement_team = _franchise_prospect_team(target_team, teams)
        desired_team_code = 255 if prospect.team.upper() in free_agent_teams else placement_team.code
        player_relations = relations_by_player.get(player_id, [])
        occurrences = [
            (relation_index, instance_id, instance_index, instance)
            for relation_index, instance_id in player_relations
            for instance_index, instance in instance_rows_by_id.get(instance_id, [])
        ]
        if not occurrences:
            raise RuntimeError(f"{prospect.name} has no roster instance to consolidate.")
        keep = min(
            occurrences,
            key=lambda item: (
                0 if int(item[3].get("BSXd", -1)) == desired_team_code else 1,
                item[2],
            ),
        )
        keep_relation_index, keep_instance_id, keep_instance_index, keep_instance = keep
        keep_instance_indexes.add(keep_instance_index)
        kept_relation = False
        removed_player_relation_indexes: set[int] = set()
        for relation_index, instance_id in player_relations:
            if instance_id == keep_instance_id and not kept_relation:
                kept_relation = True
                continue
            relation_indexes_to_remove.add(relation_index)
            removed_player_relation_indexes.add(relation_index)
        for _relation_index, instance_id, instance_index, _instance in occurrences:
            if instance_index == keep_instance_index:
                continue
            external_relations = relation_indexes_by_instance.get(instance_id, set()) - removed_player_relation_indexes
            if instance_id == keep_instance_id:
                external_relations.discard(keep_relation_index)
            if external_relations:
                raise RuntimeError(
                    f"{prospect.name} shares roster instance {instance_id}; refusing unsafe consolidation."
                )
            instance_indexes_to_remove.add(instance_index)
        plans.append(
            {
                "bio_index": bio_index,
                "instance_index": keep_instance_index,
                "instance": keep_instance,
                "team_code": desired_team_code,
                "ownership_code": 0 if desired_team_code == 255 else _franchise_rights_code(target_team, teams),
            }
        )

    occupied_xwot = {
        int(row["XWot"])
        for index, row in enumerate(cache.instance_rows)
        if index not in instance_indexes_to_remove
        and index not in keep_instance_indexes
        and row.get("XWot") is not None
    }
    planned_xwot: dict[int, int] = {}
    for plan in plans:
        instance_index = int(plan["instance_index"])
        instance = plan["instance"]
        team_code = int(plan["team_code"])
        current_xwot = int(instance.get("XWot", -1))
        if team_code == 255:
            if current_xwot in occupied_xwot:
                raise RuntimeError(f"Free-agent roster slot collision at XWot {current_xwot}.")
            planned_xwot[instance_index] = current_xwot
            occupied_xwot.add(current_xwot)
            continue
        block_start = team_code * MAX_TEAM_INSTANCES
        preferred_valid = block_start <= current_xwot < block_start + MAX_TEAM_INSTANCES
        if preferred_valid and current_xwot not in occupied_xwot:
            roster_slot = current_xwot
        else:
            roster_slot = next(
                (
                    value
                    for value in range(block_start, block_start + MAX_TEAM_INSTANCES)
                    if value not in occupied_xwot
                ),
                None,
            )
            if roster_slot is None:
                raise RuntimeError(f"No safe roster slot remains for team code {team_code}.")
        planned_xwot[instance_index] = roster_slot
        occupied_xwot.add(roster_slot)

    access = TdbAccess()
    instance_specs = access.list_fields_by_index(db_path, "ulGe")
    lineup_fields = {field.name for field in instance_specs if field.size == 1}
    lineup_fields.add("lcCm")
    table_indexes = {table.name: index for index, table in enumerate(access.list_tables(db_path))}
    with access.open_database(db_path) as db_index:
        instance_table = access.get_table_properties(db_index, table_indexes["ulGe"])
        instance_fields = {
            access.get_field_properties(db_index, "ulGe", index).name:
            access.get_field_properties(db_index, "ulGe", index)
            for index in range(instance_table.field_count)
        }
        bio_table = access.get_table_properties(db_index, table_indexes["cPbu"])
        bio_fields = {
            access.get_field_properties(db_index, "cPbu", index).name:
            access.get_field_properties(db_index, "cPbu", index)
            for index in range(bio_table.field_count)
        }
        for plan in plans:
            instance_index = int(plan["instance_index"])
            team_code = int(plan["team_code"])
            access.set_field_value(db_index, "ulGe", instance_fields["BSXd"], instance_index, team_code)
            access.set_field_value(
                db_index,
                "ulGe",
                instance_fields["XWot"],
                instance_index,
                planned_xwot[instance_index],
            )
            for field_name in lineup_fields:
                field = instance_fields.get(field_name)
                if field is not None:
                    access.set_field_value(db_index, "ulGe", field, instance_index, 0)
            access.set_field_value(
                db_index,
                "cPbu",
                bio_fields["BSXd"],
                int(plan["bio_index"]),
                int(plan["ownership_code"]),
            )
        for relation_index in sorted(relation_indexes_to_remove, reverse=True):
            access.remove_record(db_index, "caBZ", relation_index)
        for instance_index in sorted(instance_indexes_to_remove, reverse=True):
            access.remove_record(db_index, "ulGe", instance_index)
        access.save_database(db_index)


def apply_draft_class(
    db_path: Path,
    prospects: list[DraftProspect],
    *,
    undrafted_teams: set[str] | None = None,
    free_agent_teams: set[str] | None = None,
) -> list[dict[str, object]]:
    if not prospects:
        return []
    access = TdbAccess()
    undrafted_teams = {team.upper() for team in (undrafted_teams or set())}
    free_agent_teams = {team.upper() for team in (free_agent_teams or set())}
    cache = build_player_snapshot_cache(db_path)
    teams = load_teams(db_path)
    prospect_teams = [team for team in teams if team.abbrev.upper() in {"P261", "P262", "P263"}]
    if not prospect_teams:
        raise RuntimeError("No 2026 Prospects holding team was found.")

    bio_by_name: dict[str, list[tuple[int, dict[str, object]]]] = {}
    blank_bios: dict[str, list[tuple[int, dict[str, object]]]] = {"skater": [], "goalie": []}
    for index, bio in enumerate(cache.bio_rows):
        name = f"{bio.get('PedH') or ''} {bio.get('RMbQ') or ''}".strip()
        if name:
            bio_by_name.setdefault(_draft_name_key(name), []).append((index, bio))
        elif int(bio.get("zIBw") or -1) >= 16000:
            kind = "goalie" if int(bio.get("aljv") or -1) == POSITION_CODES["G"] else "skater"
            blank_bios[kind].append((index, bio))
    blank_bios["skater"].sort(key=lambda item: int(item[1].get("zIBw") or 0))
    blank_bios["goalie"].sort(key=lambda item: int(item[1].get("zIBw") or 0))

    rating_index = {int(row.get("zIBw") or -1): index for index, row in enumerate(cache.ratings_rows)}
    goalie_rating_index = {int(row.get("zIBw") or -1): index for index, row in enumerate(cache.goalie_ratings_rows)}
    flag_index = {int(row.get("zIBw") or -1): index for index, row in enumerate(cache.flags_rows)}
    placeholder_relation_index = {
        int(row.get("qFky") or -1): index
        for index, row in enumerate(cache.relation_rows)
        if int(row.get("BERR") or 0) == 1
        and int(row.get("qFky") or -1) == int(row.get("qEfv") or -2)
    }
    # Relation rows can retain reserved/stale instance IDs that are absent from
    # ulGe. NHLView still indexes qEfv as a unique key, so never recycle them.
    used_instance_ids = {
        int(row.get("TWSX") or -1) for row in cache.instance_rows
    } | {
        int(row.get("qEfv") or -1) for row in cache.relation_rows
    }
    available_instance_ids = iter(value for value in range(1, 6000) if value not in used_instance_ids)
    team_instance_load: dict[int, int] = {}
    used_roster_slots: dict[int, set[int]] = {}
    for row in cache.instance_rows:
        team_code = int(row["BSXd"]) if row.get("BSXd") is not None else -1
        team_instance_load[team_code] = team_instance_load.get(team_code, 0) + 1
        roster_slot_value = int(row["XWot"]) if row.get("XWot") is not None else -1
        roster_slot = roster_slot_value - team_code * MAX_TEAM_INSTANCES
        if 0 <= roster_slot < MAX_TEAM_INSTANCES:
            used_roster_slots.setdefault(team_code, set()).add(roster_slot)

    instance_rows_by_id: dict[int, list[dict[str, object]]] = {}
    for row in cache.instance_rows:
        instance_rows_by_id.setdefault(int(row.get("TWSX") or -1), []).append(row)
    relation_ids_by_player: dict[int, set[int]] = {}
    for row in cache.relation_rows:
        relation_ids_by_player.setdefault(int(row.get("qFky") or -1), set()).add(int(row.get("qEfv") or -1))

    instance_field_specs = access.list_fields_by_index(db_path, "ulGe")
    lineup_fields = {field.name for field in instance_field_specs if field.size == 1}
    lineup_fields.add("lcCm")

    def least_loaded_prospect_team() -> TeamRecord:
        return min(prospect_teams, key=lambda team: (team_instance_load.get(team.code, 0), team.code))

    templates = {False: _find_template(cache, False), True: _find_template(cache, True)}
    table_indexes = {table.name: index for index, table in enumerate(access.list_tables(db_path))}
    changes: list[dict[str, object]] = []
    stale_relation_indexes: set[int] = set()

    with access.open_database(db_path) as db_index:
        def write_fields(table_name: str, record_index: int, updates: dict[str, object]) -> None:
            table = access.get_table_properties(db_index, table_indexes[table_name])
            fields = {
                access.get_field_properties(db_index, table_name, field_index).name:
                access.get_field_properties(db_index, table_name, field_index)
                for field_index in range(table.field_count)
            }
            for field_name, value in updates.items():
                field = fields.get(field_name)
                if field is not None:
                    access.set_field_value(db_index, table_name, field, record_index, value)

        def add_instance(
            source_snapshot,
            *,
            player_id: int,
            team: TeamRecord,
            style_code: int,
            reserved_relation_index: int | None = None,
        ) -> int:
            if team_instance_load.get(team.code, 0) >= MAX_TEAM_INSTANCES:
                raise RuntimeError(
                    f"{team.abbrev} already has {MAX_TEAM_INSTANCES} player instances; "
                    "no safe roster slot remains."
                )
            available_slots = [
                slot
                for slot in range(MAX_TEAM_INSTANCES)
                if slot not in used_roster_slots.setdefault(team.code, set())
            ]
            if not available_slots:
                raise RuntimeError(f"No safe roster slot remains on {team.abbrev}.")
            roster_slot = available_slots[0]
            used_roster_slots[team.code].add(roster_slot)
            instance_id = next(available_instance_ids)
            instance_record_index = access.add_record(db_index, "ulGe")
            instance_overrides = {
                "TWSX": instance_id,
                "BSXd": team.code,
                "XWot": team.code * MAX_TEAM_INSTANCES + roster_slot,
                "sFgQ": style_code,
                "tRVs": 0,
                **{field: 0 for field in lineup_fields},
            }
            access.copy_record_fields(
                db_index,
                "ulGe",
                source_snapshot.instance_rows[0],
                instance_record_index,
                overrides=instance_overrides,
            )
            relation_record_index = (
                reserved_relation_index
                if reserved_relation_index is not None
                else access.add_record(db_index, "caBZ")
            )
            access.copy_record_fields(
                db_index,
                "caBZ",
                source_snapshot.relation_rows[0],
                relation_record_index,
                overrides={"BERR": 0, "qFky": player_id, "qEfv": instance_id},
            )
            team_instance_load[team.code] = team_instance_load.get(team.code, 0) + 1
            return instance_id

        for prospect in sorted(prospects, key=lambda item: item.pick):
            target_team = resolve_playable_nhl_team(prospect.team, teams)
            if target_team is None:
                raise RuntimeError(f"Roster team not found for {prospect.team} ({prospect.name}).")
            existing = bio_by_name.get(_draft_name_key(prospect.name), [])
            is_undrafted = prospect.team.upper() in undrafted_teams
            force_free_agent = prospect.team.upper() in free_agent_teams
            franchise_team = _franchise_prospect_team(target_team, teams)
            native_rights_code = _franchise_rights_code(target_team, teams)
            rights_updates = {
                "WBbd": 0 if is_undrafted else native_rights_code,
                "uWgv": 0 if is_undrafted else native_rights_code,
                **_draft_metadata_updates(prospect, is_undrafted=is_undrafted),
            }
            canonical_first_name, _, canonical_last_name = prospect.name.partition(" ")
            bio_draft_updates = {
                **rights_updates,
                **_draft_contract_updates(
                    is_undrafted=is_undrafted,
                    force_free_agent=force_free_agent,
                ),
                **({"BSXd": target_team.code + 1} if not is_undrafted else {}),
                "PedH": canonical_first_name,
                "RMbQ": canonical_last_name,
            }
            if existing:
                bio_index, bio = max(
                    existing,
                    key=lambda item: _draft_match_priority(item[1], prospect.name),
                )
                write_fields("cPbu", bio_index, bio_draft_updates)
                player_id = int(bio.get("zIBw") or -1)
                goalie = int(bio.get("aljv") or -1) == POSITION_CODES["G"]
                snapshot = cache.get_player_snapshot(
                    str(bio.get("PedH") or ""),
                    str(bio.get("RMbQ") or ""),
                    player_id,
                )
                ratings = (
                    snapshot.goalie_ratings_row if goalie else snapshot.ratings_row
                ) if snapshot is not None else None
                style_code = int((ratings or {}).get("sFgQ") or 1)
                instance_source = snapshot if snapshot is not None and snapshot.instance_rows else templates[goalie]
                related_instance_ids = relation_ids_by_player.get(player_id, set())
                instance_teams = {
                    int(row["BSXd"]) if row.get("BSXd") is not None else -1
                    for instance_id in related_instance_ids
                    for row in instance_rows_by_id.get(instance_id, [])
                }
                if player_id >= 16000 and snapshot is not None and snapshot.instance_rows:
                    stale_index = placeholder_relation_index.get(player_id)
                    if stale_index is not None:
                        stale_relation_indexes.add(stale_index)
                if not instance_teams:
                    amateur_team = _team_for_amateur_club(prospect, teams) or least_loaded_prospect_team()
                    placement_team = amateur_team if is_undrafted else franchise_team
                    add_instance(
                        instance_source,
                        player_id=player_id,
                        team=placement_team,
                        style_code=style_code,
                    )
                    instance_teams.add(placement_team.code)
                if not is_undrafted and franchise_team.code not in instance_teams:
                    add_instance(
                        instance_source,
                        player_id=player_id,
                        team=franchise_team,
                        style_code=style_code,
                    )
                changes.append(
                    {
                        "player": prospect.name,
                        "player_id": int(bio.get("zIBw") or -1),
                        "action": "rights-updated",
                        "team": prospect.team,
                        "pick": prospect.pick,
                        "changes": [{"section": "draft", "field": key, "before": bio.get(key), "after": value} for key, value in bio_draft_updates.items() if bio.get(key) != value],
                    }
                )
                continue

            goalie = prospect.position == "G"
            kind = "goalie" if goalie else "skater"
            if not blank_bios[kind]:
                raise RuntimeError(f"No inactive {kind} create-player slots remain.")
            bio_index, blank_bio = blank_bios[kind].pop(0)
            player_id = int(blank_bio["zIBw"])
            first_name, _, last_name = prospect.name.partition(" ")
            amateur_team = _team_for_amateur_club(prospect, teams) or least_loaded_prospect_team()
            placement_team = amateur_team if is_undrafted else franchise_team
            style_code = STYLE_CODES[prospect.archetype]
            template = templates[goalie]
            bio_updates = {
                "PedH": first_name,
                "RMbQ": last_name,
                "DaPp": int(blank_bio.get("DaPp") or 60000),
                "aljv": POSITION_CODES[prospect.position],
                "zKKG": 126,
                "BSXd": 0 if is_undrafted else native_rights_code,
                "QwoG": 0,
                "dhKk": 0,
                "GDhI": 0,
                "IzRv": 0,
                "IrlK": 0,
                "tRVs": 0,
                **rights_updates,
                **_draft_contract_updates(
                    is_undrafted=is_undrafted,
                    force_free_agent=force_free_agent,
                ),
            }
            access.copy_record_fields(
                db_index,
                "cPbu",
                template.bio,
                bio_index,
                overrides={"zIBw": player_id, **bio_updates},
            )

            ratings_table = "yuHm" if goalie else "yvSd"
            ratings_indexes = goalie_rating_index if goalie else rating_index
            ratings_row_index = ratings_indexes.get(player_id)
            if ratings_row_index is None:
                raise RuntimeError(f"Inactive ratings slot missing for player ID {player_id}.")
            ratings_updates = _goalie_raw_updates(prospect) if goalie else _skater_raw_updates(prospect)
            ratings_updates.update(
                {
                    "sFgQ": style_code,
                    "YqJH": 0,
                    "AMoQ": POTENTIAL_STAR_CODES[prospect.potential_stars],
                    "feBm": POTENTIAL_COLOR_CODES[prospect.potential_color],
                }
            )
            template_ratings = template.goalie_ratings_row if goalie else template.ratings_row
            if template_ratings is None:
                raise RuntimeError(f"Safe ratings template missing for {prospect.name}.")
            access.copy_record_fields(
                db_index,
                ratings_table,
                template_ratings,
                ratings_row_index,
                overrides={"zIBw": player_id, **ratings_updates},
            )

            flags_row_index = flag_index.get(player_id)
            if flags_row_index is not None and template.flags_row is not None:
                access.copy_record_fields(
                    db_index,
                    "ajmx",
                    template.flags_row,
                    flags_row_index,
                    overrides={"zIBw": player_id},
                )

            add_instance(
                template,
                player_id=player_id,
                team=placement_team,
                style_code=style_code,
                reserved_relation_index=placeholder_relation_index.get(player_id),
            )
            changes.append(
                {
                    "player": prospect.name,
                    "player_id": player_id,
                    "action": "created",
                    "team": prospect.team,
                    "current_team": placement_team.abbrev,
                    "pick": prospect.pick,
                    "overall": prospect.projected_overall,
                    "archetype": prospect.archetype,
                    "potential": f"{prospect.potential_stars:.1f} {prospect.potential_color}",
                    "changes": [{"section": "draft", "field": "created", "before": None, "after": f"{prospect.team} pick {prospect.pick}"}],
                }
            )
        # Old draft builds left the inactive self-link beside the real team link.
        # Remove in descending order so record shifts cannot invalidate later indexes.
        for relation_index in sorted(stale_relation_indexes, reverse=True):
            access.remove_record(db_index, "caBZ", relation_index)
        access.save_database(db_index)
    _consolidate_draft_player_instances(
        db_path,
        prospects,
        free_agent_teams=free_agent_teams,
    )
    return changes


def apply_elite_prospects_scouting(
    db_path: Path,
    prospects: list[DraftProspect] | None = None,
) -> list[dict[str, object]]:
    prospects = [row for row in (prospects or load_2026_draft_class()) if row.scouting_source]
    if not prospects:
        return []
    access = TdbAccess()
    cache = build_player_snapshot_cache(db_path)
    table_indexes = {table.name: index for index, table in enumerate(access.list_tables(db_path))}
    bio_matches: dict[str, list[dict[str, object]]] = {}
    for bio in cache.bio_rows:
        name = f"{bio.get('PedH') or ''} {bio.get('RMbQ') or ''}".strip()
        if name:
            bio_matches.setdefault(_draft_name_key(name), []).append(bio)
    skater_indexes = {int(row.get("zIBw") or -1): index for index, row in enumerate(cache.ratings_rows)}
    goalie_indexes = {int(row.get("zIBw") or -1): index for index, row in enumerate(cache.goalie_ratings_rows)}
    instance_indexes = {int(row.get("TWSX") or -1): index for index, row in enumerate(cache.instance_rows)}
    relation_ids: dict[int, list[int]] = {}
    for relation in cache.relation_rows:
        relation_ids.setdefault(int(relation.get("qFky") or -1), []).append(int(relation.get("qEfv") or -1))
    semantic_labels = {
        "speed": "Speed", "body_checking": "Body Checking", "endurance": "Endurance",
        "puck_control": "Puck Control", "passing": "Passing", "slap_shot_power": "Slap Shot Power",
        "slap_shot_accuracy": "Slap Shot Accuracy", "wrist_shot_power": "Wrist Shot Power",
        "wrist_shot_accuracy": "Wrist Shot Accuracy", "agility": "Agility", "strength": "Strength",
        "acceleration": "Acceleration", "balance": "Balance", "faceoffs": "Face-offs",
        "durability": "Durability", "deking": "Deking", "aggressiveness": "Aggressiveness",
        "poise": "Poise", "hand_eye": "Hand-Eye", "shot_blocking": "Shot Blocking",
        "offensive_awareness": "Off. Awareness", "defensive_awareness": "Def. Awareness",
        "discipline": "Discipline", "fighting_skill": "Fighting Skill", "stick_checking": "Stick Checking",
        "consistency": "Consistency", "breakaway": "Breakaway", "rebound_control": "Rebound Control",
        "angles": "Angles", "vision": "Vision", "poke_check": "Poke Check", "shot_recovery": "Shot Recovery",
    }
    results: list[dict[str, object]] = []
    with access.open_database(db_path) as db_index:
        field_cache: dict[str, dict[str, object]] = {}

        def fields_for(table_name: str):
            if table_name not in field_cache:
                table = access.get_table_properties(db_index, table_indexes[table_name])
                field_cache[table_name] = {
                    field.name: field
                    for field in (
                        access.get_field_properties(db_index, table_name, field_index)
                        for field_index in range(table.field_count)
                    )
                }
            return field_cache[table_name]

        def write(table_name: str, record_index: int, updates: dict[str, int]) -> None:
            fields = fields_for(table_name)
            for field_name, value in updates.items():
                field = fields.get(field_name)
                if field is not None:
                    access.set_field_value(db_index, table_name, field, record_index, value)

        for prospect in prospects:
            matches = bio_matches.get(_draft_name_key(prospect.name), [])
            if not matches:
                continue
            bio = max(matches, key=lambda row: _draft_match_priority(row, prospect.name))
            player_id = int(bio.get("zIBw") or -1)
            goalie = prospect.position == "G"
            table_name = "yuHm" if goalie else "yvSd"
            row_index = (goalie_indexes if goalie else skater_indexes).get(player_id)
            current_row = cache.goalie_ratings_by_player_id.get(player_id) if goalie else cache.ratings_by_player_id.get(player_id)
            if row_index is None or current_row is None:
                continue
            generated_player = player_id >= 16000
            generated_updates = _goalie_raw_updates(prospect) if goalie else _skater_raw_updates(prospect)
            if generated_player:
                updates = generated_updates
            else:
                specs = GOALIE_ATTRIBUTE_SPECS if goalie else SKATER_ATTRIBUTE_SPECS
                spec_by_label = {spec.label: spec for spec in specs}
                updates = {}
                for semantic in prospect.scouting_modifiers:
                    label = semantic_labels.get(semantic)
                    spec = spec_by_label.get(label or "")
                    if spec is None:
                        continue
                    updates[spec.field] = generated_updates[spec.field]
            style_code = STYLE_CODES[prospect.archetype]
            updates.update(
                {
                    "sFgQ": style_code,
                    "AMoQ": POTENTIAL_STAR_CODES[prospect.potential_stars],
                    "feBm": POTENTIAL_COLOR_CODES[prospect.potential_color],
                }
            )
            before = dict(current_row)
            write(table_name, row_index, updates)
            for instance_id in relation_ids.get(player_id, []):
                instance_index = instance_indexes.get(instance_id)
                if instance_index is not None:
                    write("ulGe", instance_index, {"sFgQ": style_code})
            results.append(
                {
                    "player": prospect.name,
                    "player_id": player_id,
                    "action": "elite-prospects-scouting",
                    "projected_overall": prospect.projected_overall,
                    "archetype": prospect.archetype,
                    "potential": f"{prospect.potential_stars:.1f} {prospect.potential_color}",
                    "source": ELITE_PROSPECTS_GRADES,
                    "changes": [
                        {"section": "ratings", "field": field, "before": before.get(field), "after": value}
                        for field, value in updates.items()
                        if before.get(field) != value
                    ],
                }
            )
        access.save_database(db_index)
    return results


def validate_draft_players(
    db_path: Path,
    prospects: list[DraftProspect],
    *,
    free_agent_teams: set[str] | None = None,
) -> list[str]:
    free_agent_teams = {team.upper() for team in (free_agent_teams or set())}
    cache = build_player_snapshot_cache(db_path)
    valid_instance_ids = {int(row.get("TWSX") or -1) for row in cache.instance_rows}
    instance_rows_by_id: dict[int, list[dict[str, object]]] = {}
    for row in cache.instance_rows:
        instance_rows_by_id.setdefault(int(row.get("TWSX") or -1), []).append(row)
    relation_ids_by_player: dict[int, list[int]] = {}
    for row in cache.relation_rows:
        relation_ids_by_player.setdefault(int(row.get("qFky") or -1), []).append(int(row.get("qEfv") or -1))
    teams = load_teams(db_path)
    roster_slots = [
        int(row["XWot"]) if row.get("XWot") is not None else -1
        for row in cache.instance_rows
    ]
    errors = []
    if len(roster_slots) != len(set(roster_slots)):
        errors.append("Roster contains duplicate XWot player slots")
    for prospect in prospects:
        matches = [
            bio
            for bio in cache.bio_rows
            if _draft_name_key(f"{bio.get('PedH') or ''} {bio.get('RMbQ') or ''}")
            == _draft_name_key(prospect.name)
        ]
        if not matches:
            errors.append(f"{prospect.name}: bio missing")
            continue
        bio = max(matches, key=lambda row: _draft_match_priority(row, prospect.name))
        player_id = int(bio.get("zIBw") or -1)
        snapshot = cache.get_player_snapshot(str(bio.get("PedH") or ""), str(bio.get("RMbQ") or ""), player_id)
        player_instance_rows = [
            row
            for instance_id in relation_ids_by_player.get(player_id, [])
            for row in instance_rows_by_id.get(instance_id, [])
        ]
        if snapshot is None or not player_instance_rows:
            errors.append(f"{prospect.name}: team instance missing")
            continue
        if len(player_instance_rows) != 1:
            errors.append(
                f"{prospect.name}: expected one active roster instance, found {len(player_instance_rows)}"
            )
        target_team = resolve_playable_nhl_team(prospect.team, teams)
        franchise_team = None if target_team is None else _franchise_prospect_team(target_team, teams)
        target_organization = None if target_team is None else organization_for_abbrev(target_team.abbrev)
        expected_team_codes = (
            {255}
            if prospect.team.upper() in free_agent_teams
            else {
                team.code for team in teams
                if target_organization is not None
                and organization_for_abbrev(team.abbrev) == target_organization
                and league_name_for_team(team) in {"NHL", "AHL", "Organization"}
            }
        )
        if not expected_team_codes.intersection({
            int(row["BSXd"]) if row.get("BSXd") is not None else -1
            for row in player_instance_rows
        }):
            destination = "free agency" if expected_team_codes == {255} else prospect.team
            errors.append(f"{prospect.name}: roster destination missing ({destination})")
        if target_team is not None:
            expected_ownership = 0 if expected_team_codes == {255} else target_team.code + 1
            if int(bio.get("BSXd") or 0) != expected_ownership:
                errors.append(
                    f"{prospect.name}: franchise ownership is {bio.get('BSXd')}, "
                    f"expected {expected_ownership} ({prospect.team})"
                )
            expected_rights = _franchise_rights_code(target_team, teams)
            if (
                int(bio.get("WBbd") or 0) != expected_rights
                or int(bio.get("uWgv") or 0) != expected_rights
            ):
                errors.append(
                    f"{prospect.name}: franchise rights do not match {prospect.team}"
                )
        expected_draft_metadata = _draft_metadata_updates(prospect, is_undrafted=False)
        draft_mismatches = {
            field: (bio.get(field), expected)
            for field, expected in expected_draft_metadata.items()
            if int(bio.get(field) if bio.get(field) is not None else -1) != expected
        }
        if draft_mismatches:
            errors.append(
                f"{prospect.name}: draft history is invalid {draft_mismatches}"
            )
        expected_contract = _draft_contract_updates(
            is_undrafted=False,
            force_free_agent=prospect.team.upper() in free_agent_teams,
        )
        contract_mismatches = {
            field: (bio.get(field), expected)
            for field, expected in expected_contract.items()
            if int(bio.get(field) or 0) != expected
        }
        if contract_mismatches:
            errors.append(
                f"{prospect.name}: franchise contract state is invalid {contract_mismatches}"
            )
        for row in player_instance_rows:
            team_code = int(row["BSXd"]) if row.get("BSXd") is not None else -1
            roster_slot = int(row["XWot"]) if row.get("XWot") is not None else -1
            if team_code != 255 and roster_slot // MAX_TEAM_INSTANCES != team_code:
                errors.append(
                    f"{prospect.name}: roster slot {roster_slot} does not belong to team {team_code}"
                )
                break
        if player_id >= 16000:
            invalid_relations = [
                row
                for row in snapshot.relation_rows
                if int(row.get("BERR") or 0) != 0
                or int(row.get("qEfv") or -1) not in valid_instance_ids
            ]
            relation_instance_ids = [int(row.get("qEfv") or -1) for row in snapshot.relation_rows]
            if invalid_relations:
                errors.append(f"{prospect.name}: inactive or orphan team relationship remains")
            elif len(relation_instance_ids) != len(set(relation_instance_ids)):
                errors.append(f"{prospect.name}: duplicate team relationship")
        if prospect.position == "G" and snapshot.goalie_ratings_row is None:
            errors.append(f"{prospect.name}: goalie ratings missing")
        elif prospect.position != "G" and snapshot.ratings_row is None:
            errors.append(f"{prospect.name}: skater ratings missing")
    return errors
