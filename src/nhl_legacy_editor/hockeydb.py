from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from urllib.parse import quote_plus
from urllib.parse import urljoin
from urllib.request import Request, urlopen


HOCKEYDB_BASE = "https://www.hockeydb.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


@dataclass(slots=True)
class HockeyDbSearchResult:
    name: str
    url: str
    pid: str


@dataclass(slots=True)
class HockeyDbProfile:
    name: str
    url: str
    position: str | None
    shoots: str | None
    born: str | None
    birthplace: str | None
    height: str | None
    weight: str | None
    draft_info: str | None


def _fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def search_hockeydb_player(query: str) -> list[HockeyDbSearchResult]:
    if not query.strip():
        return []
    url = f"{HOCKEYDB_BASE}/ihdb/stats/find_player.php?full_name={quote_plus(query)}"
    html = _fetch_text(url)
    name_match = re.search(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", html, re.IGNORECASE | re.DOTALL)
    pid_match = re.search(r"carddisplay\.php\?pid=(\d+)", html)
    if not name_match:
        return []
    return [
        HockeyDbSearchResult(
            name=unescape(name_match.group(1).strip()),
            url=url,
            pid=pid_match.group(1) if pid_match else "",
        )
    ]


def fetch_hockeydb_profile(url: str) -> HockeyDbProfile:
    html = _fetch_text(url)

    name_match = re.search(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", html, re.IGNORECASE | re.DOTALL)
    position_match = re.search(r"\n([A-Za-z /-]+)\s+--\s+shoots\s+([LR])", html)
    born_match = re.search(
        r"Born\s+(.+?)\s+--\s+</span><span[^>]*>(.+?)</span>",
        html,
        re.IGNORECASE,
    )
    hw_match = re.search(r"Height\s+([0-9.]+)\s+--\s+Weight\s+([0-9]+)", html, re.IGNORECASE)
    draft_match = re.search(r"Drafted by\s+<a [^>]+>([^<]+)</a>", html, re.IGNORECASE)

    return HockeyDbProfile(
        name=unescape(name_match.group(1).strip()) if name_match else "Unknown",
        url=url,
        position=position_match.group(1).strip() if position_match else None,
        shoots=position_match.group(2).strip() if position_match else None,
        born=born_match.group(1).strip() if born_match else None,
        birthplace=born_match.group(2).strip() if born_match else None,
        height=hw_match.group(1).strip() if hw_match else None,
        weight=hw_match.group(2).strip() if hw_match else None,
        draft_info=draft_match.group(1).strip() if draft_match else None,
    )


def fetch_hockeydb_profile_by_name(query: str) -> HockeyDbProfile | None:
    results = search_hockeydb_player(query)
    if not results:
        return None
    return fetch_hockeydb_profile(results[0].url)
