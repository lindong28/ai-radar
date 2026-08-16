from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ..sources.loader import SourceConfig
from .content import clean_content
from .dedup import FetchedItem
from .http_client import FeedResponse, fetch_document
from .rss import utc_now


@dataclass(frozen=True)
class WebSourceSpec:
    final_url: str
    allowed_fetch_hosts: frozenset[str]
    allowed_item: Callable[[str], bool]
    parser: str = "cards"
    minimum_items: int = 1
    example_paths: tuple[str, ...] = ()


def _path(prefix: str, *, excluded: tuple[str, ...] = ()) -> Callable[[str], bool]:
    def allowed(url: str) -> bool:
        path = urlparse(url).path.rstrip("/")
        return path.startswith(prefix) and path != prefix.rstrip("/") and not any(path.startswith(value) for value in excluded)
    return allowed


def _host_path(hosts: set[str], prefixes: tuple[str, ...]) -> Callable[[str], bool]:
    return lambda url: (urlparse(url).hostname or "").casefold() in hosts and any(urlparse(url).path.startswith(prefix) for prefix in prefixes)


def _deepseek_update_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "api-docs.deepseek.com"
        and parsed.path.rstrip("/") == "/zh-cn/updates"
        and re.fullmatch(r"时间-?20\d{2}-\d{2}-\d{2}", unquote(parsed.fragment)) is not None
    )


WEB_SOURCE_REGISTRY: dict[str, WebSourceSpec] = {
    "anthropic_news": WebSourceSpec("https://www.anthropic.com/news", frozenset({"www.anthropic.com"}), _path("/news/"), example_paths=("/news/example",)),
    "anthropic_research": WebSourceSpec("https://www.anthropic.com/research", frozenset({"www.anthropic.com"}), _path("/research/", excluded=("/research/team",)), example_paths=("/research/example",)),
    "claude_platform_releases": WebSourceSpec("https://platform.claude.com/docs/en/release-notes/overview", frozenset({"platform.claude.com"}), lambda url: url.startswith("https://platform.claude.com/docs/en/release-notes/overview#"), parser="releases"),
    "claude_blog": WebSourceSpec("https://claude.com/blog", frozenset({"claude.com"}), _path("/blog/"), example_paths=("/blog/example",)),
    "cursor_blog": WebSourceSpec("https://cursor.com/blog", frozenset({"cursor.com"}), _path("/blog/", excluded=("/blog/topic",)), example_paths=("/blog/example",)),
    "every_latest": WebSourceSpec("https://every.to/", frozenset({"every.to"}), _host_path({"every.to"}, ("/chain-of-thought/", "/napkin-math/", "/working-overtime/", "/mindset/")), example_paths=("/chain-of-thought/example",)),
    "google_research": WebSourceSpec("https://research.google/blog/", frozenset({"research.google"}), _path("/blog/", excluded=("/blog/rss", "/blog/javascript")), example_paths=("/blog/example",)),
    "hf_daily_papers": WebSourceSpec("https://huggingface.co/papers", frozenset({"huggingface.co"}), _host_path({"arxiv.org"}, ("/abs/",)), parser="papers"),
    "lmsys_blog": WebSourceSpec("https://www.lmsys.org/blog/", frozenset({"www.lmsys.org"}), _path("/blog/"), parser="lmsys", example_paths=("/blog/2026-example",)),
    "langchain_blog": WebSourceSpec("https://www.langchain.com/blog", frozenset({"www.langchain.com"}), _path("/blog/"), example_paths=("/blog/example",)),
    "microsoft_ai": WebSourceSpec("https://microsoft.ai/blog/", frozenset({"microsoft.ai"}), lambda url: (urlparse(url).hostname or "").casefold() == "microsoft.ai" and urlparse(url).path.rstrip("/") not in {"/news", "/blog"} and urlparse(url).path.startswith(("/news/", "/blog/")), example_paths=("/blog/example",)),
    "mistral_news": WebSourceSpec("https://mistral.ai/news", frozenset({"mistral.ai"}), _path("/news/", excluded=("/news/rss",)), example_paths=("/news/example",)),
    "runway_news": WebSourceSpec("https://runway.com/news", frozenset({"runwayml.com", "runway.com"}), _host_path({"runway.com", "runwayml.com"}, ("/news/", "/customer-stories/")), example_paths=("/news/example",)),
    "sierra_blog": WebSourceSpec("https://sierra.ai/blog", frozenset({"sierra.ai"}), _path("/blog/"), example_paths=("/blog/example",)),
    "suno_blog": WebSourceSpec("https://suno.com/blog", frozenset({"suno.com"}), _path("/blog/"), example_paths=("/blog/example",)),
    "xai_news": WebSourceSpec("https://x.ai/news", frozenset({"x.ai"}), _path("/news/"), example_paths=("/news/example",)),
    "inclusionai_models": WebSourceSpec("https://huggingface.co/api/models?author=inclusionAI&sort=lastModified&direction=-1&limit=50", frozenset({"huggingface.co"}), _host_path({"huggingface.co"}, ("/inclusionAI/",)), parser="models"),
    "deepseek_api_updates": WebSourceSpec(
        "https://api-docs.deepseek.com/zh-cn/updates",
        frozenset({"api-docs.deepseek.com"}),
        _deepseek_update_url,
        parser="deepseek_updates",
    ),
}


def _date(value: str | None, *, required: bool) -> str:
    if not value:
        if required:
            raise ValueError("missing web item date")
        return utc_now()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid web item date: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"invalid web item date: {value}")
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _item(source: SourceConfig, spec: WebSourceSpec, url: str, title: str, published_at: str | None, *, required_date: bool) -> FetchedItem:
    absolute = urljoin(spec.final_url, url)
    if not urlparse(absolute).scheme or not spec.allowed_item(absolute):
        raise ValueError(f"out-of-scope web item URL: {absolute}")
    clean_title = clean_content(title)
    if not clean_title:
        raise ValueError("missing web item title")
    return FetchedItem(source.slug, absolute, clean_title, None, _date(published_at, required=required_date), utc_now(), clean_title, None, {"adapter": "web"})


def _parse_cards(source: SourceConfig, spec: WebSourceSpec, body: bytes) -> list[FetchedItem]:
    soup = BeautifulSoup(body, "html.parser")
    candidates: dict[str, list[tuple[int, str, str | None, bool]]] = {}
    explicit_urls: list[str] = []
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        absolute = urljoin(spec.final_url, href)
        if not spec.allowed_item(absolute):
            continue
        if anchor.has_attr("data-ai-radar-item"):
            explicit_urls.append(absolute.rstrip("/").casefold())
        card = anchor.find_parent(["article", "li", "div"])
        title_node = anchor.select_one("h1, h2, h3, h4, [data-title]")
        if title_node is not None:
            rank = 5
            title = title_node.get_text(" ", strip=True)
        else:
            title = anchor.get_text(" ", strip=True)
            rank = 3 if isinstance(card, Tag) else 2
        if (not title or title.casefold() in {"read more", "learn more", "view article"}) and isinstance(card, Tag):
            title_node = card.select_one("h1, h2, h3, h4, [data-title]")
            title = title_node.get_text(" ", strip=True) if title_node else ""
            rank = 4
        if not title or title.casefold() in {"read more", "learn more", "view article"}:
            title = str(anchor.get("aria-label") or anchor.get("title") or "").strip()
            rank = 1
        time = anchor.select_one("time")
        if time is None and isinstance(card, Tag):
            time = card.select_one("time")
        candidates.setdefault(absolute.rstrip("/"), []).append((rank, title, str(time.get("datetime")) if isinstance(time, Tag) and time.get("datetime") else None, isinstance(time, Tag) and bool(time.get("datetime"))))
    if len(explicit_urls) != len(set(explicit_urls)):
        raise ValueError(f"duplicate canonical web item URL for {source.slug}")
    items: list[FetchedItem] = []
    for url, versions in candidates.items():
        _rank, title, date, required = max(versions, key=lambda candidate: (candidate[0], len(clean_content(candidate[1]))))
        if not title:
            continue
        items.append(_item(source, spec, url, title, date, required_date=required))
    return items


def _parse_releases(source: SourceConfig, spec: WebSourceSpec, body: bytes) -> list[FetchedItem]:
    soup = BeautifulSoup(body, "html.parser")
    result = []
    for heading in soup.select("[data-ai-radar-release], h2, h3"):
        time = heading.select_one("time")
        title = heading.get_text(" ", strip=True)
        date_match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}", title)
        if not date_match:
            continue
        date_text = date_match.group(0)
        date_value = datetime.strptime(date_text, "%B %d, %Y").replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        fragment = str(heading.get("id") or date_text.casefold().replace(",", "").replace(" ", "-"))
        result.append(_item(source, spec, f"#{fragment}", title, str(time.get("datetime")) if isinstance(time, Tag) else date_value, required_date=True))
    return result


def _parse_lmsys(source: SourceConfig, spec: WebSourceSpec, body: bytes) -> list[FetchedItem]:
    text = body.decode("utf-8")
    pattern = re.compile(r'\\"slug\\":\\"(?P<slug>[^\\"]+)\\",\\"title\\":\\"(?P<title>[^\\"]+)\\".*?\\"date\\":\\"(?P<date>[^\\"]+)\\"')
    result = []
    for match in pattern.finditer(text):
        raw_date = match.group("date")
        date_match = re.search(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2}", raw_date)
        if date_match is None:
            raise ValueError(f"invalid web item date: {raw_date}")
        date_text = date_match.group(0).replace("Sept ", "Sep ")
        parsed = None
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                parsed = datetime.strptime(date_text, fmt).replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(f"invalid web item date: {date_text}")
        result.append(_item(source, spec, f"/blog/{match.group('slug')}", match.group("title"), parsed.isoformat().replace("+00:00", "Z"), required_date=True))
    return result


def _parse_papers(source: SourceConfig, spec: WebSourceSpec, body: bytes) -> list[FetchedItem]:
    soup = BeautifulSoup(body, "html.parser")
    candidates: dict[str, str] = {}
    for node in soup.select("[data-ai-radar-paper], a[href^='/papers/']"):
        paper_id = str(node.get("data-ai-radar-paper") or urlparse(str(node.get("href") or "")).path.removeprefix("/papers/")).strip("/")
        if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", paper_id) is None:
            continue
        article = node.find_parent("article")
        if article is None and not node.has_attr("data-ai-radar-paper"):
            continue
        card = article or node.find_parent("div")
        heading = node.select_one("h1, h2, h3, h4")
        if heading is None and isinstance(card, Tag):
            heading = card.select_one("h1, h2, h3, h4")
        title = heading.get_text(" ", strip=True) if isinstance(heading, Tag) else node.get_text(" ", strip=True)
        if not title:
            continue
        candidates.setdefault(paper_id, title)
    return [_item(source, spec, f"https://arxiv.org/abs/{paper_id}", title, None, required_date=False) for paper_id, title in candidates.items()]


def _parse_models(source: SourceConfig, spec: WebSourceSpec, body: bytes) -> list[FetchedItem]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("malformed inclusionAI model JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("malformed inclusionAI model JSON")
    result = []
    for model in payload:
        if not isinstance(model, dict) or not str(model.get("id") or "").startswith("inclusionAI/"):
            raise ValueError("out-of-scope inclusionAI model")
        model_id = str(model["id"])
        result.append(_item(source, spec, f"https://huggingface.co/{model_id}", model_id, str(model.get("lastModified") or ""), required_date=True))
    return result


def _parse_deepseek_updates(source: SourceConfig, spec: WebSourceSpec, body: bytes) -> list[FetchedItem]:
    soup = BeautifulSoup(body.replace(b"\0", b""), "html.parser")
    result: list[FetchedItem] = []
    for heading in soup.select("article h2[id]"):
        heading_text = heading.get_text(" ", strip=True).replace("\u200b", "").strip()
        date_match = re.fullmatch(r"时间\s*[:：]?\s*(20\d{2}-\d{2}-\d{2})", heading_text)
        if date_match is None:
            continue
        release_title = ""
        for sibling in heading.find_next_siblings():
            if sibling.name == "h2":
                break
            if sibling.name == "h3":
                release_title = sibling.get_text(" ", strip=True).replace("\u200b", "").strip()
                break
        if not release_title:
            raise ValueError(f"missing DeepSeek release title for {date_match.group(1)}")
        fragment = quote(str(heading.get("id")), safe="")
        result.append(
            _item(
                source,
                spec,
                f"#{fragment}",
                release_title,
                f"{date_match.group(1)}T00:00:00Z",
                required_date=True,
            )
        )
    return result


def parse_web_source(source: SourceConfig, response: FeedResponse) -> list[FetchedItem]:
    spec = WEB_SOURCE_REGISTRY.get(source.slug)
    if spec is None:
        raise ValueError(f"unregistered web source: {source.slug}")
    final_host = (urlparse(response.final_url or source.url).hostname or "").casefold()
    if final_host not in spec.allowed_fetch_hosts:
        raise ValueError(f"invalid final response host for {source.slug}: {final_host}")
    parser = {
        "cards": _parse_cards,
        "releases": _parse_releases,
        "papers": _parse_papers,
        "models": _parse_models,
        "lmsys": _parse_lmsys,
        "deepseek_updates": _parse_deepseek_updates,
    }[spec.parser]
    items = parser(source, spec, response.body)
    urls = [item.url.rstrip("/").casefold() for item in items]
    if len(urls) != len(set(urls)):
        raise ValueError(f"duplicate canonical web item URL for {source.slug}")
    if len(items) < spec.minimum_items:
        raise ValueError(f"zero accepted web items for {source.slug}")
    return items


def fetch_web_source(source: SourceConfig, conn: object, timeout: float = 30.0) -> tuple[FeedResponse, list[FetchedItem]]:
    response = fetch_document(source, conn, accept="text/html, application/json;q=0.9, */*;q=0.1", timeout=timeout)  # type: ignore[arg-type]
    return response, [] if response.not_modified else parse_web_source(source, response)
