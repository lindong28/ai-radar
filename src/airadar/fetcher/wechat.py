from __future__ import annotations

import json
import logging
import time
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import (
    Browser,
    BrowserContext,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from .content import clean_content

logger = logging.getLogger(__name__)

ArticleResult = dict[str, Any]


def _text_or_default(node: Tag | None, fallback: str) -> str:
    if node is None:
        return fallback
    return node.get_text(strip=True) or fallback


def parse_article_html(html: str, url: str) -> ArticleResult:
    soup = BeautifulSoup(html, "html.parser")
    title = _text_or_default(soup.find("h1", {"id": "activity-name"}), "未找到标题")
    author = _text_or_default(
        soup.find("span", {"id": "js_author_name"}) or soup.find("a", {"id": "js_name"}),
        "未知作者",
    )
    publish_time = _text_or_default(soup.find("em", {"id": "publish_time"}), "未知时间")

    content_node = soup.find("div", {"id": "js_content"})
    content_html = ""
    if isinstance(content_node, Tag):
        for tag in content_node.find_all(["script", "style"]):
            tag.decompose()
        content_html = str(content_node)

    return {
        "success": True,
        "url": url,
        "title": title,
        "author": author,
        "publish_time": publish_time,
        "content_html": content_html,
        "content_text": clean_content(content_html, fallback=title),
        "error": None,
    }


class WeChatScraper:
    NAVIGATION_TIMEOUT_MS = 45000
    CONTENT_TIMEOUT_MS = 20000
    NETWORK_IDLE_TIMEOUT_MS = 5000
    MAX_FETCH_ATTEMPTS = 3
    BASE_RETRY_DELAY_SECONDS = 0.5
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self) -> None:
        self.playwright: Any | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    def _log_event(self, event: str, **fields: Any) -> None:
        logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True))

    def _retry_delay_seconds(self, attempt: int) -> float:
        return self.BASE_RETRY_DELAY_SECONDS * (2 ** max(attempt - 1, 0))

    def _new_context(self) -> BrowserContext:
        if self.browser is None:
            raise RuntimeError("browser is not initialized")
        return self.browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=self.USER_AGENT)

    def initialize(self) -> None:
        if self.browser is not None:
            return
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context = self._new_context()

    def fetch_article(self, url: str) -> ArticleResult:
        try:
            self.initialize()
            last_error: Exception | None = None
            self._log_event("fetch_start", url=url, max_attempts=self.MAX_FETCH_ATTEMPTS)

            for attempt in range(1, self.MAX_FETCH_ATTEMPTS + 1):
                if attempt == self.MAX_FETCH_ATTEMPTS and attempt > 1:
                    self._log_event("context_rebuild", url=url, attempt=attempt)
                    try:
                        if self.context is not None:
                            self.context.close()
                    except PlaywrightError:
                        pass
                    self.context = self._new_context()

                if self.context is None:
                    raise RuntimeError("browser context is not initialized")

                page = self.context.new_page()
                page.set_default_timeout(self.CONTENT_TIMEOUT_MS)
                page.set_default_navigation_timeout(self.NAVIGATION_TIMEOUT_MS)
                attempt_started_at = time.monotonic()

                try:
                    self._log_event("attempt_start", url=url, attempt=attempt)
                    page.goto(url, wait_until="domcontentloaded", timeout=self.NAVIGATION_TIMEOUT_MS)
                    page.wait_for_selector("#js_content", state="attached", timeout=self.CONTENT_TIMEOUT_MS)
                    try:
                        page.wait_for_load_state("networkidle", timeout=self.NETWORK_IDLE_TIMEOUT_MS)
                    except PlaywrightTimeoutError:
                        pass

                    result = parse_article_html(page.content(), url)
                    elapsed_ms = round((time.monotonic() - attempt_started_at) * 1000)
                    self._log_event(
                        "attempt_success",
                        url=url,
                        attempt=attempt,
                        elapsed_ms=elapsed_ms,
                        title=result.get("title"),
                    )
                    return result
                except Exception as exc:
                    last_error = exc
                    elapsed_ms = round((time.monotonic() - attempt_started_at) * 1000)
                    retrying = attempt < self.MAX_FETCH_ATTEMPTS
                    self._log_event(
                        "attempt_failure",
                        url=url,
                        attempt=attempt,
                        elapsed_ms=elapsed_ms,
                        retrying=retrying,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    if retrying:
                        delay_seconds = self._retry_delay_seconds(attempt)
                        self._log_event(
                            "retry_scheduled",
                            url=url,
                            attempt=attempt,
                            next_attempt=attempt + 1,
                            delay_seconds=delay_seconds,
                        )
                        time.sleep(delay_seconds)
                finally:
                    page.close()

            if last_error is None:
                raise RuntimeError("Exhausted retries without capturing an error")
            raise last_error
        except Exception as exc:
            self._log_event("fetch_failed", url=url, error_type=type(exc).__name__, error=str(exc))
            return {"success": False, "url": url, "error": f"Failed to fetch article: {exc}"}

    def cleanup(self) -> None:
        if self.context is not None:
            self.context.close()
            self.context = None
        if self.browser is not None:
            self.browser.close()
            self.browser = None
        if self.playwright is not None:
            self.playwright.stop()
            self.playwright = None


def scrape_article(url: str) -> ArticleResult:
    scraper = WeChatScraper()
    try:
        return scraper.fetch_article(url)
    finally:
        scraper.cleanup()
