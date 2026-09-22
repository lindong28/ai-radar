from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from airadar.interpret.engine.summarizer.core import SummarizerConfig, summarize
from airadar.interpret.engine.summarizer.llm import supported_model_aliases

_DEFAULT_BATCH_ROOT = Path("tmp/summary_agent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate structured article summaries for Summary Agent.")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--input", help="Single article URL or local markdown/text file")
    inputs.add_argument("--input-list", help="File containing one URL/path per line")
    parser.add_argument("--list-models", action="store_true", help="Print supported model aliases and exit")
    parser.add_argument(
        "--user", default="default", help="User directory under data/summary_agent (default: default)"
    )
    parser.add_argument(
        "--model",
        help="Supported model alias or raw OpenAI-compatible model name (default: SUMMARIZER_MODEL or deepseek-v4-pro)",
    )
    parser.add_argument("--base-url", help="Local llm-gateway base URL")
    parser.add_argument("--api-key", help="Removed: provider keys are managed by llm-gateway")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for scratch output files (default: tmp/summary_agent/<YYYYMMDD_HHMMSS>)",
    )
    parser.add_argument("--concurrency", type=int, default=10, help="Batch concurrency (default: 10)")
    parser.add_argument("--project-root", type=Path, help="Project root for context files")
    parser.add_argument("--max-input-tokens", type=int, default=55_000, help="Approximate input token budget")
    parser.add_argument("--temperature", type=float, default=0.3, help="LLM temperature")
    parser.add_argument("--max-tokens", type=int, default=8000, help="Max output tokens")
    parser.add_argument(
        "--skip-dedup",
        action="store_true",
        help="Skip the URL-vs-index dedup pre-check (force re-analysis of known URLs)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_models:
        print(json.dumps(supported_model_aliases(), ensure_ascii=False, indent=2))
        return
    if not args.input and not args.input_list:
        parser.error("one of the arguments --input --input-list --list-models is required")

    output_dir = args.output_dir or _auto_batch_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    config = SummarizerConfig(
        user=args.user,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        output_dir=output_dir,
        project_root=args.project_root,
        concurrency=args.concurrency,
        max_input_tokens=args.max_input_tokens,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    try:
        if args.input:
            payload = asyncio.run(
                _process_single_async(args.input, args.user, config, output_dir, skip_dedup=args.skip_dedup)
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        inputs = _read_input_list(Path(args.input_list))
        results = asyncio.run(_process_batch_async(inputs, args.user, config, output_dir, skip_dedup=args.skip_dedup))
        has_error = any(not item.get("ok", False) for item in results)
        print(json.dumps({"batch_dir": str(output_dir), "results": results}, ensure_ascii=False, indent=2))
        if has_error:
            sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


async def _process_single_async(
    article_input: str,
    user: str,
    config: SummarizerConfig,
    output_dir: Path,
    *,
    skip_dedup: bool,
) -> dict[str, Any]:
    if not skip_dedup and _looks_like_url(article_input):
        existing = _check_url(article_input, user)
        if existing is not None:
            return {
                "ok": True,
                "skipped": True,
                "input": article_input,
                "batch_dir": str(output_dir),
                "dedup": existing,
            }

    result = await summarize(article_input, config=config)
    return {
        "ok": True,
        "skipped": False,
        "input": article_input,
        "batch_dir": str(output_dir),
        "result": result.to_meta_dict(),
    }


async def _process_batch_async(
    inputs: list[str],
    user: str,
    config: SummarizerConfig,
    output_dir: Path,
    *,
    skip_dedup: bool,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, config.concurrency))

    async def _limited(article_input: str) -> dict[str, Any]:
        async with semaphore:
            try:
                return await _process_single_async(
                    article_input,
                    user,
                    config,
                    output_dir,
                    skip_dedup=skip_dedup,
                )
            except Exception as exc:
                return {"ok": False, "input": article_input, "error": str(exc)}

    return await asyncio.gather(*[_limited(item) for item in inputs])


def _check_url(url: str, user: str) -> dict | None:
    """Lazily import embedding to look up the URL in the user's index."""
    embedding = _load_embedding_module()
    if embedding is None:
        return None
    return embedding.check_url_in_index(url, user)


def _load_embedding_module():
    from airadar.interpret.engine import embedding

    return embedding


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _auto_batch_dir() -> Path:
    return _DEFAULT_BATCH_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_input_list(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Input list not found: {path}")
    inputs = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not inputs:
        raise ValueError(f"Input list is empty: {path}")
    return inputs
