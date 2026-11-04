"""Polite, metadata-only scraper for Hindi Anime Zone.

The scraper deliberately does not extract, return, or follow streaming or
file-download URLs. A quality is represented only by its visible label
(e.g. "720p"). The site's public DMCA page says it indexes material from
non-affiliated third parties; that is not proof of distribution permission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html import unescape
import re
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config


@dataclass
class EpisodeInfo:
    number: int
    season: int | None = None
    audio_languages: list[str] = field(default_factory=list)
    quality_labels: list[str] = field(default_factory=list)


@dataclass
class AnimeResult:
    title: str
    page_url: str
    seasons: list[int] = field(default_factory=list)
    seasons_declared: int | None = None
    episode_count_declared: int | None = None
    episodes: list[EpisodeInfo] = field(default_factory=list)
    audio_languages: list[str] = field(default_factory=list)
    quality_labels: list[str] = field(default_factory=list)
    # Kept explicit for callers, but always empty. Authorisation cannot be
    # established from the site's pages, so outbound media links are omitted.
    authorised_download_links: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    title: str
    url: str
    score: float


_LANGUAGE_ALIASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Hindi", re.compile(r"\b hindi \b", re.I | re.X)),
    ("English", re.compile(r"\b(?:english|eng)\b", re.I)),
    ("Japanese", re.compile(r"\b(?:japanese|jpn|jap)\b", re.I)),
    ("Tamil", re.compile(r"\b(?:tamil|tam)\b", re.I)),
    ("Telugu", re.compile(r"\b(?:telugu|tel)\b", re.I)),
    ("Malayalam", re.compile(r"\b(?:malayalam|mal)\b", re.I)),
    ("Kannada", re.compile(r"\b(?:kannada|kan)\b", re.I)),
    ("Bengali", re.compile(r"\b(?:bengali|beng)\b", re.I)),
    ("Korean", re.compile(r"\b(?:korean|kor)\b", re.I)),
    ("Chinese", re.compile(r"\b(?:chinese|chi)\b", re.I)),
    ("French", re.compile(r"\b(?:french|fre)\b", re.I)),
    ("German", re.compile(r"\b(?:german|ger)\b", re.I)),
    ("Spanish", re.compile(r"\b(?:spanish|spa)\b", re.I)),
    ("Russian", re.compile(r"\b(?:russian|rus)\b", re.I)),
    ("Arabic", re.compile(r"\b(?:arabic|ara)\b", re.I)),
)

_QUALITY_RE = re.compile(r"(?<!\d)(480|720|1080)\s*p\b", re.I)
_EPISODE_RE = re.compile(r"^\s*Episode\s*(\d+)\s*(?:[:\-–—]\s*(.*))?$", re.I)
_SEASON_RE = re.compile(r"\bSeason\s*(\d+)\b", re.I)


def normalise_text(value: str) -> str:
    """Return a comparison-friendly form of a title or query."""
    value = unicodedata.normalize("NFKC", unescape(value)).casefold()
    return " ".join(re.findall(r"[\w]+", value, flags=re.UNICODE))


def is_adult_labelled(value: str) -> bool:
    """Return True for titles/labels explicitly marked as adult content."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    return any(term in folded for term in config.ADULT_CONTENT_TERMS)


def extract_qualities(value: str) -> list[str]:
    found = {f"{match.group(1)}p" for match in _QUALITY_RE.finditer(value or "")}
    return [label for label in config.QUALITY_LABELS if label in found]


def extract_languages(value: str) -> list[str]:
    """Extract only languages named in audio/language text; preserve order."""
    # The site uses forms such as Hindi_Fandub and Eng-Jap; normalise separators
    # so the language token is not hidden inside a larger underscore/hyphen token.
    searchable = re.sub(r"[_-]+", " ", value or "")
    found: list[str] = []
    for language, pattern in _LANGUAGE_ALIASES:
        if pattern.search(searchable):
            found.append(language)
    return found


def _score_title(query: str, title: str) -> float:
    query_norm = normalise_text(query)
    title_norm = normalise_text(title)
    if not query_norm or not title_norm:
        return 0.0
    if query_norm in title_norm or title_norm in query_norm:
        return 1.0
    query_tokens = set(query_norm.split())
    title_tokens = set(title_norm.split())
    token_coverage = len(query_tokens & title_tokens) / max(1, len(query_tokens))
    sequence_score = SequenceMatcher(None, query_norm, title_norm).ratio()
    return max(token_coverage * 0.9, sequence_score)


def _site_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    return host in {"hindianimeszone.com", "www.hindianimeszone.com"}


def _canonical_page_url(url: str) -> str:
    parts = urlsplit(url)
    # Remove fragments and common tracking parameters; preserve meaningful query data.
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in {"fbclid", "gclid", "ref"}
    ]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") + "/"
    return urlunsplit(("https", "hindianimeszone.com", path, urlencode(kept_query), ""))


def _is_anime_page_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not _site_host(url):
        return False
    path = parts.path.casefold()
    if path in {"", "/"}:
        return False
    if any(path.startswith(prefix) for prefix in config.EXCLUDED_PATH_PREFIXES):
        return False
    segments = [segment for segment in path.split("/") if segment]
    # The observed catalogue uses one-slug post URLs, rather than nested paths.
    return len(segments) == 1


def _clean_lines(node: Tag) -> list[str]:
    lines: list[str] = []
    for raw in node.get_text("\n", strip=True).splitlines():
        text = " ".join(raw.split()).strip()
        if text and (not lines or text != lines[-1]):
            lines.append(text)
    return lines


def _truncate_at_related_content(lines: list[str]) -> list[str]:
    stop_labels = {
        "more like this",
        "discussion",
        "write a comment",
        "previous post",
        "next post",
    }
    for index, line in enumerate(lines):
        if line.strip().casefold() in stop_labels:
            return lines[:index]
    return lines


def _find_declared_count(lines: list[str], label: str) -> int | None:
    label_lower = label.casefold()
    for index, line in enumerate(lines):
        match = re.fullmatch(rf"{re.escape(label)}\s*:?\s*(\d+)", line, flags=re.I)
        if match:
            return int(match.group(1))
        if line.strip().casefold().rstrip(":") == label_lower and index + 1 < len(lines):
            next_match = re.search(r"\b(\d+)\b", lines[index + 1])
            if next_match:
                return int(next_match.group(1))
    return None


def _language_lines(lines: list[str]) -> list[str]:
    snippets: list[str] = []
    for index, line in enumerate(lines):
        label_match = re.match(r"^(?:Dub|Audio|Language)\s*:\s*(.*)$", line, re.I)
        if label_match and label_match.group(1).strip():
            snippets.append(label_match.group(1).strip())
        elif line.strip().casefold().rstrip(":") in {"language", "audio", "dub"}:
            if index + 1 < len(lines):
                snippets.append(lines[index + 1])
    return snippets


def parse_anime_page(html: str, page_url: str) -> AnimeResult:
    """Parse metadata from one anime detail page (never returns media URLs)."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True).split(" - Hindi Anime Zone")[0]
    title = " ".join(title.split()) or "Untitled anime page"

    if is_adult_labelled(title):
        raise ValueError("Adult-labelled page skipped by the scraper's safety filter.")

    # Prefer the individual post container, then <main>, then <body>.
    content: Tag | None = h1.find_parent("article") if h1 else None
    if content is None:
        content = soup.find("article") or soup.find("main") or soup.body
    if content is None:
        content = soup

    # Remove navigation, sidebars, comments and related-post sections before parsing.
    for unwanted in content.select(
        "script, style, nav, footer, aside, form, .related, .related-posts, "
        ".post-navigation, .comments, #comments"
    ):
        unwanted.decompose()

    lines = _truncate_at_related_content(_clean_lines(content))
    seasons = sorted({int(number) for number in _SEASON_RE.findall(title)})
    seasons_declared = _find_declared_count(lines, "Seasons")
    episode_count_declared = _find_declared_count(lines, "Episodes")
    if not seasons and seasons_declared == 1:
        seasons = [1]

    # Build episode groups using the visible "Episode N" labels.
    episode_starts: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = _EPISODE_RE.match(line)
        if match:
            episode_starts.append((index, int(match.group(1)), match.group(2) or ""))

    episodes: list[EpisodeInfo] = []
    for position, (start, number, suffix) in enumerate(episode_starts):
        end = episode_starts[position + 1][0] if position + 1 < len(episode_starts) else len(lines)
        block_lines = lines[start + 1 : end]
        block_text = " ".join([suffix, *block_lines])
        episode_languages = extract_languages(block_text)
        episodes.append(
            EpisodeInfo(
                number=number,
                season=seasons[0] if len(seasons) == 1 else None,
                audio_languages=episode_languages,
                quality_labels=extract_qualities(block_text),
            )
        )

    global_language_snippets = _language_lines(lines)
    global_languages: list[str] = []
    for snippet in global_language_snippets:
        for language in extract_languages(snippet):
            if language not in global_languages:
                global_languages.append(language)

    # If an episode card has no separate audio label, retain the page-level label.
    for episode in episodes:
        if not episode.audio_languages:
            episode.audio_languages = list(global_languages)

    all_quality_text = " ".join([title, *lines])
    all_qualities = extract_qualities(all_quality_text)
    for episode in episodes:
        for label in episode.quality_labels:
            if label not in all_qualities:
                all_qualities.append(label)
    all_qualities = [label for label in config.QUALITY_LABELS if label in all_qualities]

    return AnimeResult(
        title=title,
        page_url=_canonical_page_url(page_url),
        seasons=seasons,
        seasons_declared=seasons_declared,
        episode_count_declared=episode_count_declared,
        episodes=episodes,
        audio_languages=global_languages,
        quality_labels=all_qualities,
        authorised_download_links=[],
    )


class HindiAnimeZoneScraper:
    """Searches the site's search form first, then its paginated catalogue."""

    def __init__(
        self,
        timeout: int = config.REQUEST_TIMEOUT_SECONDS,
        request_interval: float = config.REQUEST_INTERVAL_SECONDS,
        max_catalogue_pages: int = config.MAX_CATALOGUE_PAGES,
    ) -> None:
        self.timeout = timeout
        self.request_interval = max(0.0, request_interval)
        self.max_catalogue_pages = max(1, max_catalogue_pages)
        self.last_errors: list[str] = []
        self._last_request_at = 0.0
        self._robots_checked = False
        self._robots_allowed = False
        self._robots_parser: RobotFileParser | None = None

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})
        retry = Retry(
            total=2,
            connect=2,
            read=1,
            status=2,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HindiAnimeZoneScraper":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _wait_for_polite_interval(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        delay = self.request_interval - elapsed
        if delay > 0:
            time.sleep(delay)
        self._last_request_at = time.monotonic()

    def _check_robots(self, target_url: str) -> bool:
        if not self._robots_checked:
            self._robots_checked = True
            robots_url = urljoin(config.BASE_URL, "/robots.txt")
            self._wait_for_polite_interval()
            try:
                response = self.session.get(robots_url, timeout=self.timeout, allow_redirects=False)
                if response.status_code == 404:
                    self._robots_allowed = True
                elif response.status_code != 200:
                    self.last_errors.append(
                        f"Could not verify robots.txt access rules (HTTP {response.status_code}); "
                        "stopping rather than guessing."
                    )
                    self._robots_allowed = False
                else:
                    parser = RobotFileParser()
                    parser.set_url(robots_url)
                    parser.parse(response.text.splitlines())
                    self._robots_parser = parser
                    self._robots_allowed = True
            except requests.RequestException as exc:
                self.last_errors.append(f"Could not read robots.txt: {exc}")
                self._robots_allowed = False

        if not self._robots_allowed:
            return False
        if self._robots_parser is not None and not self._robots_parser.can_fetch(config.USER_AGENT, target_url):
            self.last_errors.append(f"robots.txt does not allow crawling this path: {urlsplit(target_url).path}")
            return False
        return True

    def _fetch(self, url: str, params: dict[str, str] | None = None) -> requests.Response | None:
        """Fetch an HTML page, following only same-site redirects."""
        current_url = url
        current_params = params
        for _redirect_count in range(4):
            if not _site_host(current_url):
                self.last_errors.append(f"Blocked a non-site URL: {current_url}")
                return None
            if not self._check_robots(current_url):
                return None
            self._wait_for_polite_interval()
            try:
                response = self.session.get(
                    current_url,
                    params=current_params,
                    timeout=self.timeout,
                    allow_redirects=False,
                )
                current_params = None
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        self.last_errors.append(f"Site returned a redirect without a Location header: {current_url}")
                        return None
                    next_url = urljoin(current_url, location)
                    if not _site_host(next_url):
                        self.last_errors.append("A redirect to a different host was blocked.")
                        return None
                    current_url = next_url
                    continue
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if "html" not in content_type.casefold():
                    self.last_errors.append(
                        f"Expected an HTML page but received {content_type or 'unknown content type'}."
                    )
                    return None
                return response
            except requests.RequestException as exc:
                self.last_errors.append(f"Request failed for {current_url}: {exc}")
                return None
        self.last_errors.append(f"Too many same-site redirects while opening {url}")
        return None

    def _find_search_request(self, soup: BeautifulSoup, query: str) -> tuple[str, dict[str, str]] | None:
        for form in soup.find_all("form"):
            search_input = None
            for field in form.find_all(["input", "textarea"]):
                descriptor = " ".join(
                    str(field.get(key, ""))
                    for key in ("type", "name", "id", "placeholder", "aria-label")
                ).casefold()
                if "search" in descriptor or "anime" in descriptor:
                    search_input = field
                    break
            if search_input is None:
                continue
            method = str(form.get("method", "get")).casefold()
            if method != "get":
                continue  # Never submit POST forms.
            field_name = search_input.get("name") or search_input.get("id")
            if not field_name:
                continue
            action = urljoin(config.HOME_URL, str(form.get("action") or config.HOME_URL))
            if not _site_host(action):
                continue
            params: dict[str, str] = {}
            for field in form.find_all("input"):
                name = field.get("name")
                if not name:
                    continue
                field_type = str(field.get("type", "text")).casefold()
                if field_type in {"submit", "button", "image", "file"}:
                    continue
                if field_type in {"checkbox", "radio"} and not field.has_attr("checked"):
                    continue
                params[str(name)] = str(field.get("value", ""))
            params[str(field_name)] = query
            return action, params
        return None

    def _extract_candidates(self, soup: BeautifulSoup, query: str) -> list[Candidate]:
        best_by_url: dict[str, Candidate] = {}
        for anchor in soup.find_all("a", href=True):
            title = " ".join(anchor.get_text(" ", strip=True).split())
            url = urljoin(config.HOME_URL, str(anchor.get("href", "")))
            if not title or is_adult_labelled(title) or not _is_anime_page_url(url):
                continue
            score = _score_title(query, title)
            if score < 0.45:
                continue
            canonical = _canonical_page_url(url)
            current = best_by_url.get(canonical)
            if current is None or score > current.score:
                best_by_url[canonical] = Candidate(title=title, url=canonical, score=score)
        return sorted(best_by_url.values(), key=lambda item: (-item.score, item.title.casefold()))

    def _next_catalogue_url(self, soup: BeautifulSoup, current_url: str, page_number: int) -> str | None:
        for anchor in soup.find_all("a", href=True):
            text = anchor.get_text(" ", strip=True).casefold()
            rel = " ".join(anchor.get("rel", [])).casefold()
            if text == "next" or "next" in rel:
                candidate_url = urljoin(current_url, str(anchor["href"]))
                if _site_host(candidate_url):
                    return candidate_url
        # The public catalogue pagination uses this site-specific parameter.
        if page_number == 1:
            return urljoin(config.CATALOGUE_URL, "?haz_catalog_page=2")
        return None

    def _search_catalogue(self, query: str) -> list[Candidate]:
        found: dict[str, Candidate] = {}
        current_url = config.CATALOGUE_URL
        seen_pages: set[str] = set()
        for page_number in range(1, self.max_catalogue_pages + 1):
            # Preserve catalogue pagination parameters in the loop identity.
            if current_url in seen_pages:
                break
            seen_pages.add(current_url)
            response = self._fetch(current_url)
            if response is None:
                break
            soup = BeautifulSoup(response.text, "html.parser")
            for candidate in self._extract_candidates(soup, query):
                existing = found.get(candidate.url)
                if existing is None or candidate.score > existing.score:
                    found[candidate.url] = candidate
            next_url = self._next_catalogue_url(soup, response.url, page_number)
            if not next_url:
                break
            current_url = next_url
        return sorted(found.values(), key=lambda item: (-item.score, item.title.casefold()))

    def search(self, query: str, max_results: int = config.DEFAULT_MAX_RESULTS) -> list[AnimeResult]:
        query = query.strip()
        self.last_errors = []
        if not query:
            self.last_errors.append("Enter an anime title to search for.")
            return []

        home_response = self._fetch(config.HOME_URL)
        if home_response is None:
            return []
        home_soup = BeautifulSoup(home_response.text, "html.parser")
        candidates: dict[str, Candidate] = {}

        # Use the live search form if it is present and can be submitted as GET.
        search_request = self._find_search_request(home_soup, query)
        if search_request:
            action, params = search_request
            search_response = self._fetch(action, params=params)
            if search_response is not None:
                search_soup = BeautifulSoup(search_response.text, "html.parser")
                for candidate in self._extract_candidates(search_soup, query):
                    candidates[candidate.url] = candidate

        # The homepage can itself contain a matching title.
        for candidate in self._extract_candidates(home_soup, query):
            current = candidates.get(candidate.url)
            if current is None or candidate.score > current.score:
                candidates[candidate.url] = candidate

        # Site-specific catalogue pagination is the fallback for partial searches
        # or a search form implemented only by client-side JavaScript.
        catalogue_candidates = self._search_catalogue(query)
        for candidate in catalogue_candidates:
            current = candidates.get(candidate.url)
            if current is None or candidate.score > current.score:
                candidates[candidate.url] = candidate

        ordered = sorted(candidates.values(), key=lambda item: (-item.score, item.title.casefold()))
        results: list[AnimeResult] = []
        seen_identity: set[tuple[str, tuple[int, ...]]] = set()
        for candidate in ordered:
            if len(results) >= max(1, max_results):
                break
            page_response = self._fetch(candidate.url)
            if page_response is None:
                continue
            try:
                result = parse_anime_page(page_response.text, page_response.url)
            except ValueError:
                continue
            # A page title is a better identity than its catalogue-card label.
            if _score_title(query, result.title) < 0.40:
                continue
            identity = (normalise_text(result.title), tuple(result.seasons))
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
            results.append(result)
        return results


def format_result(result: AnimeResult) -> str:
    lines = [f"ANIME: {result.title}", f"Page: {result.page_url}"]

    if result.seasons:
        lines.append("Seasons: " + ", ".join(str(number) for number in result.seasons))
    elif result.seasons_declared is not None:
        lines.append(f"Seasons declared by site: {result.seasons_declared}")
    else:
        lines.append("Seasons: Not detected")

    if result.episode_count_declared is not None:
        lines.append(f"Episode count stated by site: {result.episode_count_declared}")
    lines.append("Available Episodes:")
    if result.episodes:
        for episode in result.episodes:
            season_label = f"Season {episode.season} " if episode.season is not None else ""
            detail = f"{season_label}Episode {episode.number}"
            if episode.audio_languages:
                detail += " (Audio: " + ", ".join(episode.audio_languages) + ")"
            if episode.quality_labels:
                detail += " [" + ", ".join(episode.quality_labels) + "]"
            lines.append(f"- {detail}")
    else:
        lines.append("- No episode headings detected on this page")

    lines.append("Available Qualities:")
    if result.quality_labels:
        lines.extend(f"- {label}" for label in result.quality_labels)
    else:
        lines.append("- No supported quality labels detected")

    if result.audio_languages:
        lines.append("Audio Languages: " + ", ".join(result.audio_languages))
    else:
        lines.append("Audio Languages: Not detected")

    # Do not show any outbound file/watch links. The site does not establish
    # distribution permissions for third-party material.
    lines.append("Authorised download links: Not displayed (authorisation not verified)")
    return "\n".join(lines)
