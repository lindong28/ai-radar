"""Pointwise score runner through durable transport, without external model calls."""
import copy
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals._shared import assets, cli, score_eval
from evals._shared.transport import DurableChat


def cases():
    return [{"case_id": str(i), "split": "dev" if i < 6 else "regression",
             "input": {"item_id": f"item-{i}", "title": f"Title {i}", "source_id": "example",
                       "url": f"https://example.org/{i}", "tier": "T1" if i % 2 else "T2",
                       "published_at": "2026-09-16T12:00:00Z", "content_text": f"content {i}",
                       "reference": "SECRET_GOLD", "expected": "SECRET_EXPECTED"},
             "reference": {"score": i * 10}} for i in range(10)]


def config():
    return {"models": {"score": "fixture-model"},
            "transport_identity": {"provider": "ark", "base_url": "https://example.org/v1"}}


PROMPT = {"system": "Emit reason first, then score.",
          "user_template": "{{ item.title }}|{{ item.content_text }}|{{ reference|default('absent') }}"}


def fixture_root(root):
    path = root / f"evals/{score_eval.TARGET}/{score_eval.BENCHMARK}/metrics.json"
    path.parent.mkdir(parents=True)
    path.write_bytes((assets.ROOT / f"evals/{score_eval.TARGET}/{score_eval.BENCHMARK}/metrics.json").read_bytes())
    return root


@pytest.fixture
def dataset(tmp_path):
    fixture_root(tmp_path)
    leaf = tmp_path / "data/visible-score/aihot-score-pointwise/v1"
    assets.write_jsonl(leaf / "cases.jsonl", cases())
    assets.write_json(leaf / "manifest.json", {
        "schema_version": 2, "target": "visible-score", "benchmark": "aihot-score-pointwise",
        "version": "v1", "evaluation_mode": "pointwise", "case_count": 10,
        "files": {"cases.jsonl": assets.file_digest(leaf / "cases.jsonl")},
        "shared_evidence": ".", "evidence_files": {},
    })
    return leaf


def durable_fixture(answer, captured):
    """Replace only the HTTP client; execute real request and attempt persistence."""
    def factory(attempts):
        def for_case(key):
            def create(**kwargs):
                captured.append((key, kwargs))
                content = answer(key)
                if not isinstance(content, str):
                    content = json.dumps(content)
                raw = {"model": "fixture-model", "choices": [{"message": {"content": content}}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                return SimpleNamespace(
                    model=raw["model"], usage=raw["usage"],
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    model_dump=lambda **kw: raw,
                )
            def client_factory(**kwargs):
                assert kwargs["max_retries"] == 0
                return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None)
            return DurableChat(attempts, provider="ark", base_url="https://example.org/v1",
                               api_key="fixture-key", case_id=key, client_factory=client_factory)
        return for_case
    return factory


def execute(dataset, root, answer, *, captured=None, **kwargs):
    captured = [] if captured is None else captured
    return score_eval.evaluate(dataset, config=config(), prompt=PROMPT, split="dev", limit=None,
                               seed="fixture", chat_factory=durable_fixture(answer, captured),
                               label="fixture", mode="direct", workers=2, root=root, **kwargs)


@pytest.mark.parametrize("mode", ["dimensions", "direct", "semantic", "five"])
def test_real_runner_persists_exact_prompts_responses_identity_and_mae(dataset, tmp_path, mode):
    captured = []
    def answer(key):
        if mode == "five":
            return {"reason": f"source evidence {key}", **dict.fromkeys(score_eval.FIVE_WEIGHTS, int(key))}
        if mode == "semantic":
            return {"reason": f"source evidence {key}", **{
                field: {"reason": f"{field} evidence", "score": int(key)}
                for field in score_eval.SEMANTIC_WEIGHTS}}
        return {"reason": f"source evidence {key}", **(
            {field: int(key) for field in score_eval.DIMENSIONS} if mode == "dimensions" else {"score": int(key) * 10})}
    result = score_eval.evaluate(dataset, config=config(), prompt=PROMPT, split="dev", limit=None,
                                 seed="fixture", chat_factory=durable_fixture(answer, captured),
                                 label="fixture", mode=mode, workers=2, root=tmp_path)
    assert result["complete"]
    assert result["metrics"]["mae"]["value"] == 0
    assert len(captured) == 6
    run = Path(result["run"])
    rows = assets.read_jsonl(run / "predictions.jsonl")
    assert len(rows) == len(list((run / "items").glob("*.json"))) == 6
    for row in rows:
        assert row["reason"] == f"source evidence {row['case_id']}"
        assert next(iter(json.loads(row["response_json"]))) == "reason"
        assert row["raw"]["choices"][0]["message"]["content"] == json.dumps(answer(row["case_id"]))
        assert "SECRET" not in json.dumps(row["prompt"])
        assert row["prompt"]["user"].endswith("|absent")
    attempts = [assets.read_json(path) for path in (run / "attempts").glob("*.json")]
    assert len(attempts) == 6
    assert all(row["status"] == "ok" and row["usage"]["completion_tokens"] == 5 for row in attempts)
    assert {row["attempt_id"] for row in rows} == {row["attempt_id"] for row in attempts}
    metadata = assets.read_json(run / "started.json")
    identity = metadata["object_identity"]
    assert identity["prompt"] == PROMPT
    assert identity["transport"] == config()["transport_identity"]
    assert identity["request"] == {"model": "fixture-model", "temperature": 0.0, "max_tokens": 600}
    assert identity["surface"] == "ordinary-nonfeatured-visible-score"
    assert identity["source_sha256"]["src/airadar/curator/score.py"] == assets.file_digest(assets.ROOT / "src/airadar/curator/score.py")
    assert identity["mapping"]["ranking_pool"] is False
    if mode == "dimensions":
        assert identity["mapping"]["weights"] == score_eval.DEFAULT_WEIGHTS.as_record()
    if mode == "semantic":
        assert identity["mapping"]["coefficients"] == {"impact": 5, "information_gain": 3, "evidence": 2}
        assert identity["source_sha256"]["src/airadar/scorer/semantic.py"] == assets.file_digest(
            assets.ROOT / "src/airadar/scorer/semantic.py")
    assert metadata["dataset_cases_sha256"] == assets.file_digest(dataset / "cases.jsonl")
    selected = assets.read_jsonl(run / "cases.jsonl")
    assert metadata["case_identity"] == assets.digest(selected)
    assert assets.read_json(run / "config.json") == config()
    archived = assets.read_jsonl(run / "prompts.jsonl")
    assert {row["case_id"]: row["prompt"] for row in archived} == {row["case_id"]: row["prompt"] for row in rows}
    assert assets.rebuild_index(tmp_path)[0]["metric_value"] == 0


@pytest.mark.parametrize("payload", [
    {"score": 50, "reason": "late"}, {"score": 50}, {"reason": " ", "score": 50},
    {"reason": "evidence", "score": float("nan")}, {"reason": "evidence", "score": float("inf")},
    {"reason": "evidence", "score": True}, {"reason": "evidence", "score": "50"},
    {"reason": "evidence", "score": 50.5}, {"reason": "evidence", "score": 101},
    {"reason": "evidence", "score": -1}, {"reason": "evidence"},
])
def test_direct_rejects_missing_late_reason_and_invalid_scores(payload):
    with pytest.raises(ValueError):
        score_eval.project_score(payload, "direct", "T2")


@pytest.mark.parametrize("field,value", [
    ("relevance", None), ("density", float("nan")), ("recency", float("inf")),
    ("authority", "7"), ("engineering", True), ("significance", 10.1),
])
def test_six_dimensions_are_required_and_strict_even_when_weight_is_zero(field, value):
    payload = {"reason": "evidence", **dict.fromkeys(score_eval.DIMENSIONS, 5)}
    payload[field] = value
    with pytest.raises(ValueError):
        score_eval.project_score(payload, "dimensions", "T2")
    del payload[field]
    with pytest.raises(ValueError):
        score_eval.project_score(payload, "dimensions", "T2")


@pytest.mark.parametrize("value,expected", [(0, 0), (2.45, 25), (6.25, 63), (9.95, 100), (10, 100)])
def test_weighted_mapping_uses_javascript_rounding_without_tier_multiplier(value, expected):
    payload = {"reason": "evidence", **dict.fromkeys(score_eval.DIMENSIONS, value)}
    assert score_eval.project_score(payload, "dimensions", "T1") == {"score": expected}
    assert score_eval.project_score(payload, "dimensions", "T2") == {"score": expected}


@pytest.mark.parametrize("failure", ["timeout", "missing", "late_reason", "nonfinite", "bad_json"])
def test_one_failure_keeps_exact_cases_raw_and_attempt_and_invalidates_mae(dataset, tmp_path, failure):
    def answer(key):
        if key != "2":
            return {"reason": "valid", "score": int(key) * 10}
        if failure == "timeout":
            raise TimeoutError("fixture timeout")
        return {"missing": {"reason": "missing score"}, "late_reason": {"score": 20, "reason": "late"},
                "nonfinite": {"reason": "nonfinite", "score": float("nan")}, "bad_json": "not JSON"}[failure]
    captured = []
    result = execute(dataset, tmp_path, answer, captured=captured)
    assert not result["complete"]
    assert result["metrics"]["mae"]["value"] is None
    assert result["metrics"]["mae"]["denominator"] == 6
    assert len(captured) == 6
    run = Path(result["run"])
    rows = assets.read_jsonl(run / "predictions.jsonl")
    failed = next(row for row in rows if row["case_id"] == "2")
    assert failed["status"] == "error" and failed["output"] is None
    assert len(rows) == 6
    attempt = assets.read_json(run / "attempts" / f"{failed['attempt_id']}.json")
    assert attempt["case_id"] == "2"
    if failure in {"nonfinite", "late_reason", "missing"}:
        assert failed["raw"]["choices"][0]["message"]["content"] == json.dumps(answer("2"))
        assert failed["reason"] == answer("2")["reason"]
    if failure == "bad_json":
        assert attempt["raw"]["choices"][0]["message"]["content"] == "not JSON"
        assert failed["raw"] == attempt["raw"]
        assert failed["usage"] == attempt["usage"]


def test_selection_is_label_blind_exact_same_cases_and_exclusions():
    original = cases()
    changed = copy.deepcopy(original)
    for row in changed:
        row["reference"]["score"] = 100 - row["reference"]["score"]
    selected = score_eval.select_cases(original, "dev", 3, "seed")
    assert [c["case_id"] for c in selected] == [c["case_id"] for c in score_eval.select_cases(changed[::-1], "dev", 3, "seed")]
    assert not {c["case_id"] for c in selected} & {c["case_id"] for c in score_eval.select_cases(original, "regression", 3, "seed")}
    ordered = score_eval.select_cases(original, "dev", None, "seed")
    excluded = frozenset(c["case_id"] for c in ordered[:2])
    assert score_eval.select_cases(original, "dev", None, "seed", excluded) == ordered[2:]


def test_reuse_requires_same_identity_and_exact_cases_and_skips_successes(dataset, tmp_path):
    first = execute(dataset, tmp_path, lambda key: {"reason": "valid", "score": int(key) * 10})
    captured = []
    second_root = fixture_root(tmp_path / "second")
    second = execute(dataset, second_root, lambda key: pytest.fail("must not call reused cases"),
                     captured=captured, reuse=Path(first["run"]))
    assert not captured and second["complete"]
    rows = assets.read_jsonl(Path(second["run"]) / "predictions.jsonl")
    assert all(row["reused_from"] == first["run"] for row in rows)
    arguments = dict(config=config(), prompt=PROMPT, split="dev", seed="fixture", chat_factory=None,
                     label="mismatch", mode="direct", root=second_root, reuse=Path(first["run"]))
    with pytest.raises(ValueError, match="exact same cases"):
        score_eval.evaluate(dataset, limit=2, **arguments)
    arguments["prompt"] = {**PROMPT, "system": "different"}
    with pytest.raises(ValueError, match="object identity"):
        score_eval.evaluate(dataset, limit=None, **arguments)


def test_runner_exclude_run_and_render_failure_are_archived(dataset, tmp_path):
    prior = tmp_path / "prior"
    assets.write_jsonl(prior / "cases.jsonl", cases()[:2])
    captured = []
    result = execute(dataset, tmp_path, lambda key: {"reason": "valid", "score": int(key) * 10},
                     captured=captured, exclude_runs=(prior,))
    assert {key for key, _ in captured} == {"2", "3", "4", "5"}
    metadata = assets.read_json(Path(result["run"]) / "started.json")
    assert metadata["selection"]["exclusions"][0]["cases_sha256"] == assets.file_digest(prior / "cases.jsonl")
    second_root = fixture_root(tmp_path / "second")
    result = score_eval.evaluate(dataset, config=config(), prompt={**PROMPT, "user_template": "{{ reference.score }}"},
                                 split="dev", limit=2, seed="fixture", label="no-gold", root=second_root,
                                 chat_factory=durable_fixture(lambda key: pytest.fail("must not call"), []))
    assert not result["complete"]
    assert result["metrics"]["mae"]["value"] is None
    assert len(assets.read_jsonl(Path(result["run"]) / "predictions.jsonl")) == 2


def test_canonical_cli_validate_and_run(dataset, tmp_path, monkeypatch):
    entry = assets.ROOT / "evals/visible-score/aihot-score-pointwise/evaluate.py"
    monkeypatch.setattr(sys, "argv", [str(entry), "validate", "--dataset", str(dataset)])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(entry), run_name="__main__")
    assert result.value.code == 0
    cfg, prompt = tmp_path / "config.json", tmp_path / "prompt.json"
    assets.write_json(cfg, config())
    assets.write_json(prompt, PROMPT)
    captured = []
    monkeypatch.setattr(cli, "transport_factory", lambda config, env: durable_fixture(
        lambda key: {"reason": "valid", "score": int(key) * 10}, captured))
    evaluate = score_eval.evaluate
    monkeypatch.setattr(score_eval, "evaluate", lambda *args, **kw: evaluate(*args, **kw, root=tmp_path))
    monkeypatch.setattr(sys, "argv", [str(entry), "run", "--dataset", str(dataset), "--config", str(cfg),
                                     "--prompt", str(prompt), "--split", "dev", "--mode", "direct",
                                     "--limit", "2", "--label", "cli-smoke", "--smoke"])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(entry), run_name="__main__")
    assert result.value.code == 0 and len(captured) == 2
    started = list((tmp_path / "runs").rglob("started.json"))
    assert len(started) == 1 and assets.read_json(started[0])["smoke"] is True


@pytest.mark.parametrize("workers", [0, 33])
def test_worker_bound_is_checked_before_calls(dataset, tmp_path, workers):
    with pytest.raises(ValueError, match="workers"):
        score_eval.evaluate(dataset, config=config(), prompt=PROMPT, split="dev", limit=2,
                             seed="fixture", chat_factory=None, label="invalid", workers=workers, root=tmp_path)
