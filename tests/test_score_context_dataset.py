import pytest

from evals._shared.score_context_dataset import candidates, instant
from evals._shared.score_context_run import validate_match
from evals._shared.score_eval import prompt_context


def test_candidates_cut_future_old_disallowed_and_duplicate_versions():
    focal = {"title": "New model alpha", "url": "https://a/1", "content_text": "model alpha release"}
    def row(key, observed, published="2026-09-17T10:00:00Z", source="ok", url="https://a/2"):
        return {"id": key, "observed_at": observed, "raw": {**focal, "source_id": source,
            "url": url, "published_at": published}}
    rows = [row("old-version", "2026-09-17T10:10:00Z"), row("new-version", "2026-09-17T10:30:00Z"),
            row("future", "2026-09-17T12:00:00Z", url="https://a/3"),
            row("old-news", "2026-09-17T10:00:00Z", published="2026-09-01T10:00:00Z", url="https://a/4"),
            row("wrong-source", "2026-09-17T10:00:00Z", source="wechat-only", url="https://a/5"),
            row("self", "2026-09-17T10:00:00Z", url="https://a/1")]
    result = candidates(focal, instant("2026-09-17T11:00:00Z"), rows, {"ok"})
    assert [r["id"] for r in result] == ["new-version"]


@pytest.mark.parametrize("value", ("2026-09-17", "2026-09-17T11:00:00"))
def test_clock_requires_timezone_and_time(value):
    with pytest.raises(ValueError):
        instant(value)


@pytest.mark.parametrize("indices", ([True], [-1], [3], [0, 0], "0"))
def test_match_rejects_invalid_members(indices):
    with pytest.raises(ValueError):
        validate_match({"reason": "test", "related_indices": indices}, 3)


def test_match_accepts_empty_and_subset():
    assert validate_match({"reason": "none", "related_indices": []}, 3) == []
    assert validate_match({"reason": "one", "related_indices": [1]}, 3) == [1]


def test_context_does_not_pass_reference_or_retrieval_score():
    raw = {"item_id": "i", "title": "t", "url": "https://x/a", "tier": "T1",
        "published_at": "2026-09-17T09:00:00Z", "content_text": "original",
        "source_id": "s", "score_context": {"archive_first_observed_at": "2026-09-17T10:00:00Z",
        "age_hours": 1, "reference": "SECRET", "neighbors": [{"id": "j", "source_id": "s",
        "title": "n", "url": "https://x/b", "content_text": "body", "score": "SECRET"}]}}
    result = prompt_context(raw, contextual=True)
    assert "SECRET" not in repr(result)
    assert "clock" not in prompt_context(raw)
    assert result["neighbors"][0]["content_text"] == "body"


def test_offline_deadline_terminates_queued_workers():
    import os
    import subprocess
    import sys
    import time
    from evals._shared import assets
    code = ("from evals._shared.score_context_study import arm_deadline; "
            "from concurrent.futures import ThreadPoolExecutor; import time; "
            "arm_deadline(1); e=ThreadPoolExecutor(max_workers=2); "
            "list(e.map(lambda _:time.sleep(3),range(8)))")
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-c", code], cwd=assets.ROOT,
                            env={**os.environ, "PYTHONPATH": "src:."}, timeout=4)
    assert result.returncode == 124
    assert time.monotonic() - started < 3
