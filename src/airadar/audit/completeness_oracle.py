from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

WEB_FACTS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "anthropic_news": ("https://www.anthropic.com/news", ("/news/",), "cards"),
    "anthropic_research": ("https://www.anthropic.com/research", ("/research/",), "cards"),
    "claude_platform_releases": ("https://platform.claude.com/docs/en/release-notes/overview", (), "releases"),
    "claude_blog": ("https://claude.com/blog", ("/blog/",), "cards"),
    "cursor_blog": ("https://cursor.com/blog", ("/blog/",), "cards"),
    "every_latest": ("https://every.to/", ("/chain-of-thought/", "/napkin-math/", "/working-overtime/", "/mindset/"), "cards"),
    "google_research": ("https://research.google/blog/", ("/blog/",), "cards"),
    "hf_daily_papers": ("https://huggingface.co/papers", ("/papers/",), "papers"),
    "lmsys_blog": ("https://www.lmsys.org/blog/", ("/blog/",), "lmsys"),
    "langchain_blog": ("https://www.langchain.com/blog", ("/blog/",), "cards"),
    "microsoft_ai": ("https://microsoft.ai/blog/", ("/news/", "/blog/"), "cards"),
    "mistral_news": ("https://mistral.ai/news", ("/news/",), "cards"),
    "runway_news": ("https://runway.com/news", ("/news/", "/customer-stories/"), "cards"),
    "sierra_blog": ("https://sierra.ai/blog", ("/blog/",), "cards"),
    "suno_blog": ("https://suno.com/blog", ("/blog/",), "cards"),
    "xai_news": ("https://x.ai/news", ("/news/",), "cards"),
    "inclusionai_models": ("https://huggingface.co/api/models", ("/inclusionAI/",), "models"),
    "deepseek_api_updates": ("https://api-docs.deepseek.com/zh-cn/updates", (), "deepseek_updates"),
}


def _text(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def _url(value: str, base: str) -> str:
    parts = urlsplit(urljoin(base, value.strip()))
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if not key.startswith("utm_")]
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/") or "/", urlencode(query), ""))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def enumerate_feed(slug: str, fetch_url: str, body: bytes) -> set[tuple[str, str]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError("oracle cannot parse feed XML") from exc
    result: dict[str, tuple[str, str]] = {}
    for node in root.iter():
        if _local(node.tag) not in {"item", "entry"}:
            continue
        title = ""
        link = ""
        categories: set[str] = set()
        for child in node:
            local = _local(child.tag)
            if local == "title" and not title:
                title = _text("".join(child.itertext()))
            elif local == "link" and not link:
                link = str(child.attrib.get("href") or child.text or "").strip()
            elif local == "category":
                categories.add(str(child.attrib.get("term") or child.text or "").strip().casefold())
        if not title or not link:
            continue
        canonical = _url(link, fetch_url)
        if slug == "google_cloud_databases" and "databases" not in categories and not urlsplit(canonical).path.casefold().startswith("/blog/products/databases/"):
            continue
        result[canonical.rstrip("/").casefold()] = (canonical, title)
    if not result:
        raise ValueError("oracle found zero feed items")
    return set(result.values())


def _card_candidate(anchor: Tag) -> tuple[int, str]:
    own = anchor.select_one("h1,h2,h3,h4,[data-title]")
    if own:
        return 5, _text(own.get_text(" ", strip=True))
    text = _text(anchor.get_text(" ", strip=True))
    if text and text.casefold() not in {"read more", "learn more", "view article"}:
        card = anchor.find_parent(["article", "li", "div"])
        return (3 if isinstance(card, Tag) else 2), text
    card = anchor.find_parent(["article", "li", "div"])
    heading = card.select_one("h1,h2,h3,h4,[data-title]") if isinstance(card, Tag) else None
    if isinstance(heading, Tag):
        return 4, _text(heading.get_text(" ", strip=True))
    return 1, str(anchor.get("aria-label") or anchor.get("title") or "").strip()


def enumerate_web(slug: str, body: bytes, final_url: str) -> set[tuple[str, str]]:
    if slug not in WEB_FACTS:
        raise ValueError(f"oracle has no web facts for {slug}")
    base, prefixes, kind = WEB_FACTS[slug]
    if kind == "models":
        payload = json.loads(body)
        result = {(f"https://huggingface.co/{row['id']}", str(row["id"])) for row in payload if isinstance(row, dict) and str(row.get("id") or "").startswith("inclusionAI/")}
    elif kind == "deepseek_updates":
        soup = BeautifulSoup(body.replace(b"\0", b""), "html.parser")
        result = set()
        for heading in soup.select("article h2[id]"):
            date_match = re.fullmatch(
                r"时间\s*[:：]?\s*(20\d{2}-\d{2}-\d{2})",
                _text(heading.get_text(" ", strip=True)).replace("\u200b", "").strip(),
            )
            if date_match is None:
                continue
            title = ""
            for sibling in heading.find_next_siblings():
                if sibling.name == "h2":
                    break
                if sibling.name == "h3":
                    title = _text(sibling.get_text(" ", strip=True)).replace("\u200b", "").strip()
                    break
            if not title:
                raise ValueError(f"oracle missing DeepSeek release title for {date_match.group(1)}")
            result.add((f"{base}#{quote(str(heading.get('id')), safe='')}", title))
    elif kind == "lmsys":
        text = body.decode("utf-8")
        matches = re.finditer(r'\\"slug\\":\\"(?P<slug>[^\\"]+)\\",\\"title\\":\\"(?P<title>[^\\"]+)\\".*?\\"date\\":\\"[^\\"]+\\"', text)
        result = {(_url(f"/blog/{match.group('slug')}", base), _text(match.group("title"))) for match in matches}
    elif kind == "releases":
        soup = BeautifulSoup(body, "html.parser")
        result = set()
        for heading in soup.select("h2,h3,[data-ai-radar-release]"):
            title = _text(heading.get_text(" ", strip=True))
            match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}", title)
            if match:
                fragment = str(heading.get("id") or match.group(0).casefold().replace(",", "").replace(" ", "-"))
                result.add((f"{base}#{fragment}", title))
    elif kind == "papers":
        soup = BeautifulSoup(body, "html.parser")
        paper_candidates: dict[str, str] = {}
        for anchor in soup.select("[data-ai-radar-paper],a[href^='/papers/']"):
            paper_id = str(anchor.get("data-ai-radar-paper") or urlsplit(str(anchor.get("href") or "")).path.removeprefix("/papers/")).strip("/")
            if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", paper_id) is None:
                continue
            article = anchor.find_parent("article")
            if article is None and not anchor.has_attr("data-ai-radar-paper"):
                continue
            heading = article.select_one("h1,h2,h3,h4") if isinstance(article, Tag) else None
            title = _text(heading.get_text(" ", strip=True) if isinstance(heading, Tag) else anchor.get_text(" ", strip=True))
            if title:
                paper_candidates.setdefault(f"https://arxiv.org/abs/{paper_id}", title)
        result = set(paper_candidates.items())
    else:
        soup = BeautifulSoup(body, "html.parser")
        candidates: dict[str, list[tuple[int, str]]] = {}
        for anchor in soup.select("a[href]"):
            absolute = _url(str(anchor.get("href") or ""), final_url or base)
            path = urlsplit(absolute).path
            if not any(path.startswith(prefix) and path.rstrip("/") != prefix.rstrip("/") for prefix in prefixes):
                continue
            if slug == "google_research" and path.startswith(("/blog/rss", "/blog/javascript")):
                continue
            if slug == "anthropic_research" and path.startswith("/research/team"):
                continue
            if slug == "cursor_blog" and path.startswith("/blog/topic"):
                continue
            if slug == "mistral_news" and path.startswith("/news/rss"):
                continue
            rank, title = _card_candidate(anchor)
            if title:
                candidates.setdefault(absolute, []).append((rank, title))
        result = {
            (url, max(versions, key=lambda candidate: (candidate[0], len(_text(candidate[1]))))[1])
            for url, versions in candidates.items()
        }
    if not result:
        raise ValueError(f"oracle found zero web items for {slug}")
    return result
