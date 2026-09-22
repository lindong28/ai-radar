from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from airadar.interpret.engine import tag_normalizer


def _write_tags_doc(project_root: Path) -> Path:
    tags_doc = project_root / "agents/summary-agent/docs/tags.md"
    tags_doc.parent.mkdir(parents=True)
    tags_doc.write_text(
        """# 分类标签词汇表

## 当前标签

### 构建（怎么造 Agent）

| 标签 | 覆盖范围 | 示例文章主题 |
|------|---------|-------------|
| `Agent架构` | Agent 的整体设计模式 | workflow 选型 |

### 评测（怎么评 Agent）

| 标签 | 覆盖范围 | 示例文章主题 |
|------|---------|-------------|
| `评测方法` | 评测的执行方法 | Agent-as-a-Judge |

### 进化（Agent 怎么变强）

| 标签 | 覆盖范围 | 示例文章主题 |
|------|---------|-------------|
| `Skill迭代` | Skill/Prompt 的迭代优化方法 | Skill 质量提升 |

### 应用方向（做什么应用）

| 标签 | 覆盖范围 | 示例文章主题 |
|------|---------|-------------|
| `视频生成` | AI 视频生成 | Seedance 实战 |
""",
        encoding="utf-8",
    )
    return tags_doc


def _fake_embeddings(texts: list[str]) -> np.ndarray:
    vectors = {
        "Agent架构": [1.0, 0.0, 0.0],
        "Agent 架构": [0.99, 0.01, 0.0],
        "评测方法": [0.0, 1.0, 0.0],
        "Skill迭代": [0.0, 0.0, 1.0],
        "视频生成": [0.0, -1.0, 0.0],
        "AI乐器演奏": [-1.0, 0.0, 0.0],
        "失效tag": [0.5, 0.5, 0.0],
    }
    return np.array([vectors[text] for text in texts], dtype=np.float32)


def test_normalized_tags_subset_of_tags_md(tmp_path, monkeypatch):
    _write_tags_doc(tmp_path)
    monkeypatch.setattr(tag_normalizer, "get_embeddings", lambda _client, texts: _fake_embeddings(texts))
    monkeypatch.setattr(
        tag_normalizer,
        "_classify_unknown",
        lambda *args, **kwargs: {"dimension": "应用方向", "coverage": "AI 乐器演奏应用"},
    )

    meta = {
        "title": "Demo",
        "tags": ["Agent架构", "Agent 架构", "AI乐器演奏"],
        "projects": [{"name": "Project", "tags": ["Agent 架构", "AI乐器演奏"]}],
    }
    report = tag_normalizer.normalize_meta_tags(
        meta,
        project_root=tmp_path,
        article_title="Demo",
        article_summary_excerpt="Summary",
        threshold=0.85,
        embedding_client_factory=lambda: object(),
    )

    known_tags = tag_normalizer.load_known_tags(tmp_path)
    assert set(meta["tags"]).issubset(known_tags)
    assert set(meta["projects"][0]["tags"]).issubset(known_tags)
    assert meta["tags"] == ["Agent架构", "AI乐器演奏"]
    assert meta["projects"][0]["tags"] == ["Agent架构", "AI乐器演奏"]
    assert report.normalization_failed is False


def test_new_tag_appended_to_correct_dimension(tmp_path, monkeypatch):
    tags_doc = _write_tags_doc(tmp_path)
    monkeypatch.setattr(tag_normalizer, "get_embeddings", lambda _client, texts: _fake_embeddings(texts))
    monkeypatch.setattr(
        tag_normalizer,
        "_classify_unknown",
        lambda *args, **kwargs: {"dimension": "应用方向", "coverage": "AI 乐器演奏应用"},
    )

    meta = {"title": "Demo Title", "tags": ["AI乐器演奏"], "projects": []}
    report = tag_normalizer.normalize_meta_tags(
        meta,
        project_root=tmp_path,
        article_title="Demo Title",
        article_summary_excerpt="Summary",
        threshold=0.85,
        embedding_client_factory=lambda: object(),
    )

    content = tags_doc.read_text(encoding="utf-8")
    assert "| `AI乐器演奏` | AI 乐器演奏应用 | Demo Title |" in content
    assert report.tags_md_written is True
    assert report.decisions[0].dimension == "应用方向"


def test_high_similarity_tag_merged(tmp_path, monkeypatch):
    tags_doc = _write_tags_doc(tmp_path)
    monkeypatch.setattr(tag_normalizer, "get_embeddings", lambda _client, texts: _fake_embeddings(texts))
    classify_calls = []
    monkeypatch.setattr(tag_normalizer, "_classify_unknown", lambda *args, **kwargs: classify_calls.append(args))

    meta = {"title": "Demo", "tags": ["Agent 架构"], "projects": []}
    report = tag_normalizer.normalize_meta_tags(
        meta,
        project_root=tmp_path,
        article_title="Demo",
        article_summary_excerpt="Summary",
        threshold=0.85,
        embedding_client_factory=lambda: object(),
    )

    assert meta["tags"] == ["Agent架构"]
    assert classify_calls == []
    assert "`Agent 架构`" not in tags_doc.read_text(encoding="utf-8")
    assert report.decisions[0].action == "merged"
    assert report.decisions[0].similarity is not None
    assert report.decisions[0].similarity >= 0.85


def test_embedding_failure_fallback(tmp_path, monkeypatch, capsys):
    _write_tags_doc(tmp_path)

    def fail_embeddings(_client, _texts):
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(tag_normalizer, "get_embeddings", fail_embeddings)
    meta = {"title": "Demo", "tags": ["失效tag"], "projects": []}

    report = tag_normalizer.normalize_meta_tags(
        meta,
        project_root=tmp_path,
        article_title="Demo",
        article_summary_excerpt="Summary",
        threshold=0.85,
        embedding_client_factory=lambda: object(),
    )

    assert meta["tags"] == ["失效tag"]
    assert report.normalization_failed is True
    assert "embedding unavailable" in (report.failure_reason or "")
    assert "Warning: Tag normalization failed" in capsys.readouterr().err


def test_report_serializes_to_stdout_shape():
    report = tag_normalizer.NormalizationReport(
        decisions=[
            tag_normalizer.TagDecision(
                original="评测方法论",
                normalized="评测方法",
                action="merged",
                similarity=0.91,
                dimension=None,
            )
        ],
        normalization_failed=False,
        failure_reason=None,
        tags_md_written=False,
    )

    payload = json.loads(json.dumps(tag_normalizer.report_to_json(report), ensure_ascii=False))
    assert payload == {
        "decisions": [
            {
                "original": "评测方法论",
                "normalized": "评测方法",
                "action": "merged",
                "similarity": 0.91,
            }
        ],
        "normalization_failed": False,
        "tags_md_written": False,
    }
