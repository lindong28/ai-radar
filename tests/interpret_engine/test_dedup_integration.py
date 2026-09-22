"""Tests for dedup + auto batch_dir integration in summarizer.cli.

The orchestrating agent should not have to call --check-url N times. Instead
the summarizer CLI handles dedup internally: for each URL input it consults
the index, and for already-known URLs it emits a `dedup` skip record without
spending an LLM call.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from airadar.interpret.engine import embedding
from airadar.interpret.engine.summarizer import cli as summarizer_cli  # noqa: E402


@pytest.fixture()
def kb_with_known_url(tmp_path, monkeypatch):
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    user_dir = tmp_path / "test_user"
    user_dir.mkdir()
    (user_dir / "index.json").write_text(
        json.dumps(
            [
                {
                    "title": "Known Article",
                    "input": {"article_file_path": "data/summary_agent/articles/known.md"},
                    "output": {"summary_file_path": "data/summary_agent/test_user/article_summaries/known_output.md"},
                    "metadata": {"url": "https://example.com/known", "saved_at": "2026-01-01 00:00"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_check_url_in_index_returns_dict_for_known_url(kb_with_known_url):
    result = embedding.check_url_in_index("https://example.com/known", "test_user")
    assert result is not None
    assert result["found"] is True
    assert result["slug"] == "known"
    assert result["article_file_path"] == "data/summary_agent/articles/known.md"


def test_canonicalize_url_drops_wechat_tracking_query():
    canonical = embedding.canonicalize_url("https://mp.weixin.qq.com/s/article")

    assert embedding.canonicalize_url("https://mp.weixin.qq.com/s/article?scene=334#wechat_redirect") == canonical


def test_canonicalize_url_preserves_complete_legacy_wechat_identity():
    first = embedding.canonicalize_url(
        "https://mp.weixin.qq.com/s?mid=100&__biz=MzA1&idx=1&sn=aaa&scene=334#wechat_redirect"
    )
    second = embedding.canonicalize_url(
        "https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=2&sn=bbb&mpshare=1"
    )

    assert first == "https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=1&sn=aaa"
    assert second == "https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=2&sn=bbb"
    assert first != second


def test_canonicalize_url_drops_default_https_port_from_legacy_wechat_identity():
    without_port = embedding.canonicalize_url(
        "https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=1&sn=aaa"
    )
    with_default_port = embedding.canonicalize_url(
        "https://mp.weixin.qq.com:443/s?__biz=MzA1&mid=100&idx=1&sn=aaa"
    )

    assert with_default_port == without_port


def test_canonicalize_url_does_not_collapse_incomplete_legacy_wechat_identity():
    first = embedding.canonicalize_url("https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&scene=334")
    second = embedding.canonicalize_url("https://mp.weixin.qq.com/s?__biz=MzA2&mid=200&scene=334")

    assert first == "https://mp.weixin.qq.com/s?__biz=MzA1&mid=100"
    assert second == "https://mp.weixin.qq.com/s?__biz=MzA2&mid=200"
    assert first != second


def test_canonicalize_url_strips_tracking_params_but_preserves_identity_params():
    canonical = embedding.canonicalize_url(
        "https://Example.com/watch?id=42&b=2&utm_source=newsletter&fbclid=abc&scene=334&a=1#section"
    )

    assert canonical == "https://example.com/watch?a=1&b=2&id=42"


def test_check_url_in_index_matches_canonical_url(kb_with_known_url):
    result = embedding.check_url_in_index("https://example.com/known?utm_source=newsletter&scene=334", "test_user")

    assert result is not None
    assert result["found"] is True
    assert result["slug"] == "known"


def test_check_url_in_index_matches_legacy_wechat_url_with_default_https_port(
    kb_with_known_url: Path,
) -> None:
    index_path = kb_with_known_url / "test_user" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[0]["metadata"]["url"] = "https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=1&sn=aaa"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    result = embedding.check_url_in_index(
        "https://mp.weixin.qq.com:443/s?__biz=MzA1&mid=100&idx=1&sn=aaa",
        "test_user",
    )

    assert result is not None
    assert result["found"] is True


def test_verify_url_vector_matches_legacy_wechat_url_with_default_https_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_vector_kb(
        tmp_path,
        manifest_slugs=["known"],
        vectors=np.ones((1, embedding.EMBEDDING_DIM), dtype=np.float32),
    )
    index_path = tmp_path / "test_user" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[0]["metadata"]["url"] = "https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=1&sn=aaa"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    embedding.cmd_verify_url_vector(
        "https://mp.weixin.qq.com:443/s?__biz=MzA1&mid=100&idx=1&sn=aaa",
        "test_user",
    )

    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_check_url_in_index_returns_none_for_unknown_url(kb_with_known_url):
    result = embedding.check_url_in_index("https://example.com/unseen", "test_user")
    assert result is None


def _write_vector_kb(
    root: Path,
    *,
    manifest_slugs: list[str] | None,
    vectors: np.ndarray | None,
) -> None:
    user_dir = root / "test_user"
    user_dir.mkdir(exist_ok=True)
    (user_dir / "index.json").write_text(
        json.dumps(
            [
                {
                    "title": "Known Article",
                    "output": {"summary_file_path": "data/summary_agent/test_user/article_summaries/known_output.md"},
                    "metadata": {"url": "https://example.com/known"},
                }
            ]
        ),
        encoding="utf-8",
    )
    embeddings_dir = user_dir / "embeddings"
    embeddings_dir.mkdir(exist_ok=True)
    if vectors is not None:
        np.save(embeddings_dir / "vectors.npy", vectors)
    if manifest_slugs is not None:
        (embeddings_dir / "vectors_manifest.json").write_text(
            json.dumps({"slugs": manifest_slugs}),
            encoding="utf-8",
        )


@pytest.mark.parametrize(
    ("vector", "expected_status"),
    [
        (np.ones((1, embedding.EMBEDDING_DIM), dtype=np.float32), "ok"),
        (np.zeros((1, embedding.EMBEDDING_DIM), dtype=np.float32), "zero_or_nonfinite"),
    ],
)
def test_verify_url_vector_reports_item_vector_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    vector: np.ndarray,
    expected_status: str,
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_vector_kb(tmp_path, manifest_slugs=["known"], vectors=vector)

    embedding.cmd_verify_url_vector("https://example.com/known", "test_user")

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": expected_status,
        "kb_slug": "known",
        "index_rows": 1,
        "manifest_rows": 1,
        "vector_rows": 1,
        "vector_ndim": 2,
        "vector_dim": embedding.EMBEDDING_DIM,
        "expected_vector_dim": embedding.EMBEDDING_DIM,
    }


def test_verify_url_vector_reports_url_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_vector_kb(
        tmp_path,
        manifest_slugs=["known"],
        vectors=np.ones((1, embedding.EMBEDDING_DIM), dtype=np.float32),
    )

    embedding.cmd_verify_url_vector("https://example.com/missing", "test_user")

    assert json.loads(capsys.readouterr().out) == {
        "status": "url_not_found",
        "kb_slug": None,
        "index_rows": 1,
        "manifest_rows": 1,
        "vector_rows": 1,
        "vector_ndim": 2,
        "vector_dim": embedding.EMBEDDING_DIM,
        "expected_vector_dim": embedding.EMBEDDING_DIM,
    }


def test_verify_url_vector_distinguishes_malformed_index_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_vector_kb(
        tmp_path,
        manifest_slugs=[""],
        vectors=np.ones((1, embedding.EMBEDDING_DIM), dtype=np.float32),
    )
    index_path = tmp_path / "test_user" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[0]["output"]["summary_file_path"] = ""
    index_path.write_text(json.dumps(index), encoding="utf-8")

    embedding.cmd_verify_url_vector("https://example.com/known", "test_user")

    assert json.loads(capsys.readouterr().out) == {
        "status": "index_entry_malformed",
        "kb_slug": None,
        "index_rows": 1,
        "manifest_rows": 1,
        "vector_rows": 1,
        "vector_ndim": 2,
        "vector_dim": embedding.EMBEDDING_DIM,
        "expected_vector_dim": embedding.EMBEDDING_DIM,
    }


def test_verify_url_vector_reports_manifest_alignment_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_vector_kb(
        tmp_path,
        manifest_slugs=["different"],
        vectors=np.ones((1, embedding.EMBEDDING_DIM), dtype=np.float32),
    )

    embedding.cmd_verify_url_vector("https://example.com/known", "test_user")

    assert json.loads(capsys.readouterr().out) == {
        "status": "alignment_mismatch",
        "kb_slug": "known",
        "index_rows": 1,
        "manifest_rows": 1,
        "vector_rows": 1,
        "vector_ndim": 2,
        "vector_dim": embedding.EMBEDDING_DIM,
        "expected_vector_dim": embedding.EMBEDDING_DIM,
    }


def test_verify_url_vector_reports_unavailable_manifest_and_vectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_vector_kb(tmp_path, manifest_slugs=None, vectors=None)

    embedding.cmd_verify_url_vector("https://example.com/known", "test_user")

    assert json.loads(capsys.readouterr().out) == {
        "status": "alignment_mismatch",
        "kb_slug": "known",
        "index_rows": 1,
        "manifest_rows": None,
        "vector_rows": None,
        "vector_ndim": None,
        "vector_dim": None,
        "expected_vector_dim": embedding.EMBEDDING_DIM,
    }


def test_verify_url_vector_reports_vector_shape_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_vector_kb(
        tmp_path,
        manifest_slugs=["known"],
        vectors=np.ones((1, embedding.EMBEDDING_DIM - 1), dtype=np.float32),
    )

    embedding.cmd_verify_url_vector("https://example.com/known", "test_user")

    assert json.loads(capsys.readouterr().out) == {
        "status": "vector_shape_mismatch",
        "kb_slug": "known",
        "index_rows": 1,
        "manifest_rows": 1,
        "vector_rows": 1,
        "vector_ndim": 2,
        "vector_dim": embedding.EMBEDDING_DIM - 1,
        "expected_vector_dim": embedding.EMBEDDING_DIM,
    }


def test_verify_url_vector_does_not_label_one_dimensional_length_as_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_vector_kb(
        tmp_path,
        manifest_slugs=["known"],
        vectors=np.ones((embedding.EMBEDDING_DIM,), dtype=np.float32),
    )

    embedding.cmd_verify_url_vector("https://example.com/known", "test_user")

    assert json.loads(capsys.readouterr().out) == {
        "status": "vector_shape_mismatch",
        "kb_slug": "known",
        "index_rows": 1,
        "manifest_rows": 1,
        "vector_rows": None,
        "vector_ndim": 1,
        "vector_dim": None,
        "expected_vector_dim": embedding.EMBEDDING_DIM,
    }


def test_summarizer_cli_skips_known_url_and_emits_dedup_record(kb_with_known_url, capsys):
    """When a URL is already in the index, summarizer prints a dedup payload and never calls the LLM."""
    with patch.object(summarizer_cli, "summarize") as mock_summarize:
        summarizer_cli.main(
            [
                "--input",
                "https://example.com/known",
                "--user",
                "test_user",
                "--output-dir",
                str(kb_with_known_url / "scratch"),
            ]
        )

    mock_summarize.assert_not_called()
    output = json.loads(capsys.readouterr().out)
    assert output["dedup"]["found"] is True
    assert output["dedup"]["slug"] == "known"
    assert output["skipped"] is True


def test_summarizer_cli_auto_creates_timestamped_batch_dir(tmp_path, monkeypatch, capsys):
    """If no --output-dir given, summarizer auto-creates tmp/summary_agent/<TS>/ and prints batch_dir."""
    monkeypatch.chdir(tmp_path)

    with patch.object(summarizer_cli, "summarize") as mock_summarize:
        mock_summarize.return_value = _make_fake_summary_result()
        summarizer_cli.main(["--input", "/tmp/nonexistent.md", "--user", "test_user"])

    out = capsys.readouterr().out
    payload = json.loads(out)
    batch_dir = payload["batch_dir"]
    assert re.search(r"tmp/summary_agent/\d{8}_\d{6}/?$", batch_dir)
    assert (tmp_path / batch_dir).is_dir()


def test_summarizer_cli_input_list_honors_concurrency(tmp_path, capsys):
    """Batch mode should process up to --concurrency items at a time."""
    input_list = tmp_path / "articles.txt"
    input_list.write_text("article-a.md\narticle-b.md\narticle-c.md\n", encoding="utf-8")
    scratch_dir = tmp_path / "scratch"
    running = 0
    max_running = 0

    async def fake_summarize(article_input, *, config):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        running -= 1
        result = _make_fake_summary_result()
        return replace(result, slug=Path(article_input).stem)

    with patch.object(summarizer_cli, "summarize", side_effect=fake_summarize) as mock_summarize:
        summarizer_cli.main(
            [
                "--input-list",
                str(input_list),
                "--user",
                "test_user",
                "--concurrency",
                "2",
                "--output-dir",
                str(scratch_dir),
            ]
        )

    output = json.loads(capsys.readouterr().out)
    assert [item["input"] for item in output["results"]] == ["article-a.md", "article-b.md", "article-c.md"]
    assert mock_summarize.call_count == 3
    assert max_running == 2


def _make_fake_summary_result():
    """Return a SummaryResult-shaped object whose to_meta_dict() returns a minimal payload."""
    from airadar.interpret.engine.summarizer.schema import SummaryResult

    return SummaryResult(
        slug="fake",
        title="fake",
        source="local_file",
        url="/tmp/nonexistent.md",
        publish_date=None,
        saved_at="2026-01-01 00:00",
        summary_md="### 📋 文章概况\nx\n",
        recommendation="可跳过",
        criteria_reason="摘要已覆盖全部信息。",
        save_decision=False,
        save_reason="无 actionable",
        tags=[],
        keywords=[],
        projects=[],
        model_name="fake",
    )
