from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.request import Request, urlopen


PUCKPEDIA_DEPTH_BASE = "https://depth-charts.puckpedia.com"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
PUCKPEDIA_TEAM_CODES = {"NSH": "NAS", "WSH": "WAS"}

_SECTION_STATUS = {
    "Forwards": "nhl",
    "Defense": "nhl",
    "Goalies": "nhl",
    "Press Box Skaters": "nhl",
    "Minor-League Skaters": "minors",
    "Minor-League Goalies": "minors",
    "Loaned Skaters": "loaned",
    "Prospect Skaters": "prospects",
    "Prospect Goalies": "prospects",
    "Reserve List Skaters": "reserve",
}


@dataclass(frozen=True, slots=True)
class PuckPediaDepthPlayer:
    puckpedia_id: int
    team_abbrev: str
    full_name: str
    status: str
    section: str


@dataclass(frozen=True, slots=True)
class PuckPediaDepthChart:
    team_abbrev: str
    players: tuple[PuckPediaDepthPlayer, ...]
    lines_season: str | None
    contracts_season: str | None
    last_updated: str | None


class _DepthChartParser(HTMLParser):
    def __init__(self, team_abbrev: str) -> None:
        super().__init__(convert_charrefs=True)
        self.team_abbrev = team_abbrev
        self.players: list[PuckPediaDepthPlayer] = []
        self.document_text: list[str] = []
        self._in_th = False
        self._th_parts: list[str] = []
        self._section: str | None = None
        self._player_id: int | None = None
        self._player_parts: list[str] = []
        self._skip_mobile_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "th":
            self._in_th = True
            self._th_parts = []
        if self._player_id is not None and tag == "span":
            classes = set(str(values.get("class") or "").split())
            if "sm:hidden" in classes and "max-sm:hidden" not in classes:
                self._skip_mobile_depth += 1
        if tag == "a":
            match = re.search(r"/player/i/(\d+)", str(values.get("href") or ""))
            if match and self._section in _SECTION_STATUS:
                self._player_id = int(match.group(1))
                self._player_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "th" and self._in_th:
            label = " ".join("".join(self._th_parts).split())
            if label in _SECTION_STATUS:
                self._section = label
            self._in_th = False
            self._th_parts = []
        if tag == "span" and self._skip_mobile_depth:
            self._skip_mobile_depth -= 1
        if tag == "a" and self._player_id is not None:
            display_name = " ".join("".join(self._player_parts).split())
            if "," in display_name:
                last_name, first_name = (part.strip() for part in display_name.split(",", 1))
                full_name = f"{first_name} {last_name}".strip()
            else:
                full_name = display_name
            if full_name and self._section is not None:
                item = PuckPediaDepthPlayer(
                    puckpedia_id=self._player_id,
                    team_abbrev=self.team_abbrev,
                    full_name=full_name,
                    status=_SECTION_STATUS[self._section],
                    section=self._section,
                )
                if item not in self.players:
                    self.players.append(item)
            self._player_id = None
            self._player_parts = []
            self._skip_mobile_depth = 0

    def handle_data(self, data: str) -> None:
        self.document_text.append(data)
        if self._in_th:
            self._th_parts.append(data)
        if self._player_id is not None and not self._skip_mobile_depth:
            self._player_parts.append(data)


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str | None] | None = None, parent=None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.parent = parent
        self.children: list[_Node | str] = []


class _StreamingDomParser(HTMLParser):
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self._VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _walk(node: _Node):
    yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield from _walk(child)


def _node_text(node: _Node, *, include_mobile_abbreviation: bool = False) -> str:
    parts: list[str] = []

    def visit(value: _Node | str) -> None:
        if isinstance(value, str):
            parts.append(value)
            return
        classes = set(str(value.attrs.get("class") or "").split())
        if not include_mobile_abbreviation and "sm:hidden" in classes and "max-sm:hidden" not in classes:
            return
        for child in value.children:
            visit(child)

    visit(node)
    return " ".join("".join(parts).split())


def _restore_react_streamed_nodes(root: _Node) -> None:
    by_id = {
        str(node.attrs["id"]): node
        for node in _walk(root)
        if node.attrs.get("id") is not None
    }
    replacements: list[tuple[str, str]] = []
    for node in _walk(root):
        if node.tag != "script":
            continue
        script = _node_text(node, include_mobile_abbreviation=True)
        replacements.extend(re.findall(r'\$RS\("([^"]+)","([^"]+)"\)', script))
    for source_id, placeholder_id in replacements:
        source = by_id.get(source_id)
        placeholder = by_id.get(placeholder_id)
        if source is None or placeholder is None or placeholder.parent is None:
            continue
        parent = placeholder.parent
        try:
            index = parent.children.index(placeholder)
        except ValueError:
            continue
        moved = list(source.children)
        for child in moved:
            if isinstance(child, _Node):
                child.parent = parent
        parent.children[index:index + 1] = moved
        source.children = []


def _players_from_restored_dom(root: _Node, team_abbrev: str) -> list[PuckPediaDepthPlayer]:
    players: list[PuckPediaDepthPlayer] = []
    seen: set[tuple[int, str]] = set()
    for table in (node for node in _walk(root) if node.tag == "table" and "hidden" not in node.attrs):
        section: str | None = None
        for node in _walk(table):
            if node.tag == "th":
                label = _node_text(node)
                if label in _SECTION_STATUS:
                    section = label
            if node.tag != "a" or section is None:
                continue
            match = re.search(r"/player/i/(\d+)", str(node.attrs.get("href") or ""))
            if not match:
                continue
            display_name = _node_text(node)
            if "," not in display_name:
                continue
            last_name, first_name = (part.strip() for part in display_name.split(",", 1))
            full_name = f"{first_name} {last_name}".strip()
            key = (int(match.group(1)), section)
            if not full_name or key in seen:
                continue
            seen.add(key)
            players.append(
                PuckPediaDepthPlayer(
                    puckpedia_id=key[0],
                    team_abbrev=team_abbrev.upper(),
                    full_name=full_name,
                    status=_SECTION_STATUS[section],
                    section=section,
                )
            )
    return players


_DEPTH_CACHE: dict[str, PuckPediaDepthChart] = {}


def parse_puckpedia_depth_chart(
    html: str,
    team_abbrev: str,
    *,
    metadata_html: str | None = None,
) -> PuckPediaDepthChart:
    parser = _StreamingDomParser()
    parser.feed(html)
    _restore_react_streamed_nodes(parser.root)
    players = _players_from_restored_dom(parser.root, team_abbrev)
    metadata_parser = _StreamingDomParser()
    metadata_parser.feed(metadata_html if metadata_html is not None else html)
    metadata_text = _node_text(metadata_parser.root, include_mobile_abbreviation=True)
    season_match = re.search(
        r"Lines as of\s+(\d{4}-\d{2})\s*\|\s*Contracts as of\s+(\d{4}-\d{2})",
        metadata_text,
        flags=re.IGNORECASE,
    )
    updated_match = re.search(
        r"Last Updated:\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{1,2}:\d{2}\s+(?:AM|PM)\s+(?:ET|CT|MT|PT))",
        metadata_text,
        flags=re.IGNORECASE,
    )
    return PuckPediaDepthChart(
        team_abbrev=team_abbrev.upper(),
        players=tuple(players),
        lines_season=None if season_match is None else season_match.group(1).strip(),
        contracts_season=None if season_match is None else season_match.group(2).strip(),
        last_updated=None if updated_match is None else updated_match.group(1).strip(),
    )


def fetch_puckpedia_depth_chart(
    team_abbrev: str,
    *,
    use_cache: bool = True,
    timeout: float = 25.0,
) -> PuckPediaDepthChart:
    abbrev = team_abbrev.upper().strip()
    if use_cache and abbrev in _DEPTH_CACHE:
        return _DEPTH_CACHE[abbrev]
    source_code = PUCKPEDIA_TEAM_CODES.get(abbrev, abbrev)
    url = f"{PUCKPEDIA_DEPTH_BASE}/{source_code}/chart?ads=true&hideFooter=true&showHeader=false"
    request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html"})
    with urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", errors="replace")
    metadata_url = f"{PUCKPEDIA_DEPTH_BASE}/{source_code}?ads=true&hideFooter=true&showHeader=false"
    metadata_request = Request(metadata_url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html"})
    with urlopen(metadata_request, timeout=timeout) as response:
        metadata_html = response.read().decode("utf-8", errors="replace")
    chart = parse_puckpedia_depth_chart(html, abbrev, metadata_html=metadata_html)
    if not chart.players:
        raise RuntimeError(f"PuckPedia returned no depth-chart players for {abbrev}.")
    _DEPTH_CACHE[abbrev] = chart
    return chart


def fetch_puckpedia_league_depth_charts(
    team_abbrevs,
    *,
    use_cache: bool = True,
    max_workers: int = 8,
) -> tuple[list[PuckPediaDepthChart], list[dict[str, str]]]:
    charts: list[PuckPediaDepthChart] = []
    errors: list[dict[str, str]] = []
    abbreviations = tuple(dict.fromkeys(str(value).upper().strip() for value in team_abbrevs if value))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {
            executor.submit(fetch_puckpedia_depth_chart, abbrev, use_cache=use_cache): abbrev
            for abbrev in abbreviations
        }
        for future in as_completed(pending):
            abbrev = pending[future]
            try:
                charts.append(future.result())
            except Exception as exc:
                errors.append({"team": abbrev, "error": str(exc), "source": "PuckPedia Depth Charts"})
    charts.sort(key=lambda chart: chart.team_abbrev)
    errors.sort(key=lambda row: row["team"])
    return charts, errors
