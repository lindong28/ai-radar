from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from airadar import db
from airadar.interpret.engine.paths import ASSETS, kb_root, tags_path
from airadar.interpret.engine.summarizer.fetcher import fetch_url
from airadar.interpret.engine.summarizer.llm import LLMConfig, complete_text, render_prompts
from airadar.interpret.engine.summarizer.schema import ArticleDocument, SummaryResult, parse_summary_output
from airadar.interpret.engine.summarizer.slug import slugify_title
from airadar.provider.llm_gateway import gateway_error


@dataclass(frozen=True)
class SummarizerConfig:
    user: str = "default"
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    output_dir: Path = Path("tmp/summary_agent")
    project_root: Path | None = None
    concurrency: int = 10
    max_input_tokens: int = 55_000
    temperature: float = 0.3
    max_tokens: int = 8000

    def resolved_project_root(self) -> Path:
        if self.project_root is not None:
            return self.project_root.resolve()
        return db.PROJECT_ROOT


def find_project_root(start: Path) -> Path:
    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "src/airadar").exists():
            return candidate
    raise FileNotFoundError(f"Could not find project root above {start}")


async def summarize(
    article_input: str,
    *,
    config: SummarizerConfig | None = None,
    client_factory: Callable[..., Any] | None = None,
    keyword_loader: Callable[[Path, str], list[str]] | None = None,
) -> SummaryResult:
    config = config or SummarizerConfig()
    project_root = config.resolved_project_root()
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    article = await _load_article(article_input)
    slug = slugify_title(article.title)
    prompt_context = _build_prompt_context(
        article=article,
        config=config,
        project_root=project_root,
        keyword_loader=keyword_loader or load_existing_keywords,
    )
    template_dir = Path(__file__).parent / "prompts"
    system_prompt, user_prompt = render_prompts(template_dir, prompt_context)
    llm_config = LLMConfig.from_values(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    llm_response = await complete_text(
        system_prompt,
        user_prompt,
        llm_config,
        client_factory=client_factory,
        temp_dir=project_root / "tmp",
    )
    llm_output = llm_response.content
    # Preserve a paid response before local schema parsing can reject it.
    (output_dir / f"{slug}_gateway.json").write_text(
        json.dumps({
            "request_id": llm_response.metadata["gateway_request_id"],
            "response": llm_response.raw.model_dump(mode="json"),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    known_tags = load_known_tags(project_root)
    try:
        parsed = parse_summary_output(llm_output, known_tags=known_tags)
    except Exception as exc:
        raise gateway_error(
            exc, llm_response.metadata["gateway_request_id"], completion=llm_response.raw,
        ) from exc

    result = SummaryResult(
        slug=slug,
        title=article.title,
        source=article.source,
        url=article.url,
        publish_date=article.publish_date,
        saved_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        summary_md=parsed.summary_md,
        recommendation=parsed.recommendation,
        criteria_reason=parsed.criteria_reason,
        save_decision=parsed.save_decision,
        save_reason=parsed.save_reason,
        tags=parsed.tags,
        keywords=parsed.keywords,
        projects=parsed.projects,
        model_name=llm_response.metadata["backend_model"],
        llm_metadata={
            **llm_response.metadata,
            "criteria_reason_source": parsed.criteria_reason_source,
        },
        validation=parsed.validation,
    )
    _write_outputs(output_dir, article, result)
    return result


async def summarize_batch(
    inputs: list[str],
    *,
    config: SummarizerConfig | None = None,
    client_factory: Callable[..., Any] | None = None,
    keyword_loader: Callable[[Path, str], list[str]] | None = None,
) -> list[SummaryResult | Exception]:
    config = config or SummarizerConfig()
    semaphore = asyncio.Semaphore(max(1, config.concurrency))

    async def _limited(article_input: str) -> SummaryResult | Exception:
        async with semaphore:
            try:
                return await summarize(
                    article_input,
                    config=config,
                    client_factory=client_factory,
                    keyword_loader=keyword_loader,
                )
            except Exception as exc:
                return exc

    return await asyncio.gather(*[_limited(item) for item in inputs])


async def _load_article(article_input: str) -> ArticleDocument:
    if _is_url(article_input):
        return await fetch_url(article_input)

    path = Path(article_input).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Article input file not found: {path}")
    text = path.read_text(encoding="utf-8")
    title = _title_from_markdown(text) or path.stem
    return ArticleDocument(title=title, content=text, source="local_file", url=str(path.resolve()))


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _title_from_markdown(text: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+)", line.strip())
        if match:
            return match.group(1).strip()
    return ""


def _build_prompt_context(
    *,
    article: ArticleDocument,
    config: SummarizerConfig,
    project_root: Path,
    keyword_loader: Callable[[Path, str], list[str]],
) -> dict[str, Any]:
    user_root = kb_root() / config.user
    design_doc = _read_required(ASSETS / "summary_agent_design.md")
    return {
        "article": article,
        "article_content": _truncate_article(article.content, config.max_input_tokens),
        "design_doc": _extract_design_sections(design_doc),
        "persona": _read_required(user_root / "persona.md"),
        "tags_doc": _read_required(tags_path()),
        "reference_outputs": [
            _read_required(user_root / "article_summaries/building_effective_agents_output.md"),
            _read_required(user_root / "article_summaries/软件工程师头衔要没了ClaudeCode之父YC访谈_output.md"),
        ],
        "existing_keywords": keyword_loader(project_root, config.user),
        "max_input_tokens": config.max_input_tokens,
    }


def _read_required(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Required summarizer context file not found: {path}")
    return path.read_text(encoding="utf-8")


def _extract_design_sections(design_doc: str) -> str:
    start = design_doc.find("## 2. 输出内容选择原则")
    end = design_doc.find("## 4.", start)
    if start == -1:
        return design_doc
    return design_doc[start : end if end != -1 else None].strip()


def _truncate_article(content: str, max_input_tokens: int) -> str:
    # Conservative approximation that avoids adding tokenizer dependencies to the hot path.
    max_chars = max(4_000, max_input_tokens * 3)
    if len(content) <= max_chars:
        return content
    return f"{content[:max_chars].rstrip()}\n\n[文章已截断，共 {len(content)} 字，保留前 {max_chars} 字]"


def load_existing_keywords(project_root: Path, user: str) -> list[str]:
    from airadar.interpret.engine.embedding import _load_index

    index = _load_index(user)
    return sorted({str(keyword) for entry in index for keyword in entry.get("metadata", {}).get("keywords", [])})


def load_known_tags(project_root: Path) -> set[str]:
    """Parse the controlled tag vocabulary from agents/summary-agent/docs/tags.md."""
    tags_doc = tags_path()
    if not tags_doc.exists():
        return set()
    pattern = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)
    return {match.group(1) for match in pattern.finditer(tags_doc.read_text(encoding="utf-8"))}


def _write_outputs(output_dir: Path, article: ArticleDocument, result: SummaryResult) -> None:
    (output_dir / f"{result.slug}_article.md").write_text(article.content.rstrip() + "\n", encoding="utf-8")
    (output_dir / f"{result.slug}_summary.md").write_text(result.summary_md.rstrip() + "\n", encoding="utf-8")
    (output_dir / f"{result.slug}_meta.json").write_text(
        json.dumps(result.to_meta_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
