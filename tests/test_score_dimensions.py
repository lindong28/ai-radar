"""Exercise independent calls through the same runner and durable HTTP boundary."""
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_score_eval import config, dataset, gateway_companion  # noqa: F401 — shared real-runner fixture

from evals._shared import assets, score_eval
from evals._shared.score_dimensions import combine_calls, dimension_prompts
from evals._shared.transport import DurableChat

PROMPT = {"system": "One independent decision.", "user_template": "{{ item.title }}|{{ item.content_text }}",
          "dimension_rubrics": {name: f"RUBRIC_{name}" for name in score_eval.FIVE_WEIGHTS}}


def factory(captured, failure=None):
    def for_directory(attempts):
        def for_case(key):
            def client_factory(**kwargs):
                def create(**kwargs):
                    text = kwargs["messages"][0]["content"]
                    name = next(n for n in score_eval.FIVE_WEIGHTS if f"RUBRIC_{n}" in text)
                    captured.append((key, name, kwargs))
                    payload = {"reason": f"evidence {key} {name}", name: int(key)}
                    if key == "2" and name == "novelty" and failure:
                        if failure == "timeout":
                            raise TimeoutError("fixture timeout")
                        payload = {name: 2, "reason": "late"}
                    content = json.dumps(payload)
                    raw = {"model": "fixture-model", "choices": [{"message": {"content": content}}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                           "llm_gateway": gateway_companion(kwargs)}
                    return SimpleNamespace(model="fixture-model", usage=raw["usage"],
                        model_extra={"llm_gateway": raw["llm_gateway"]},
                        choices=[SimpleNamespace(message=SimpleNamespace(content=content))], model_dump=lambda **kw: raw)
                return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                       close=kwargs["http_client"].close)
            return DurableChat(attempts, **config()["transport_identity"],
                               case_id=key, client_factory=client_factory)
        return for_case
    return for_directory


@pytest.mark.parametrize("failure", [None, "timeout", "late"])
def test_five_real_attempts_per_case_and_no_partial_success(dataset, tmp_path, failure, monkeypatch):  # noqa: F811 - imported pytest fixture
    counter = iter(range(10))
    monkeypatch.setattr(score_eval, "create_run", lambda *args, **kwargs: assets.create_run(
        *args, **kwargs, created_at=datetime(2026, 9, 21, tzinfo=UTC) + timedelta(seconds=next(counter))))
    captured = []
    result = score_eval.evaluate(dataset, config=config(), prompt=PROMPT, split="dev", limit=None,
        seed="fixture", chat_factory=factory(captured, failure), label="independent", mode="five-separate",
        workers=2, root=tmp_path)
    assert result["complete"] is (failure is None)
    assert result["metrics"]["mae"]["value"] == (0 if failure is None else None)
    assert len(captured) == 30
    for key, dimension, request in captured:
        system, user = [message["content"] for message in request["messages"]]
        assert system.count("RUBRIC_") == 1
        assert f"RUBRIC_{dimension}" in system
        assert user == f"Title {key}|content {key}"
        assert "SECRET" not in system + user
    run = Path(result["run"])
    assert len(list((run / "attempts").glob("*.json"))) == 30
    predictions = assets.read_jsonl(run / "predictions.jsonl")
    for row in predictions:
        assert len(row["dimension_calls"]) == len(row["attempt_ids"]) == 5
        if row["status"] == "ok":
            assert json.loads(row["response_json"]) == combine_calls(row["dimension_calls"])
            assert row["usage"]["total_tokens"] == 75
        else:
            assert row["case_id"] == "2" and row["output"] is None
    if failure is None:
        cached = score_eval.evaluate(dataset, config=config(), prompt=PROMPT, split="dev", limit=None,
            seed="fixture", chat_factory=factory(captured), label="reuse", mode="five-separate",
            workers=2, root=tmp_path, reuse=run)
        assert cached["complete"] and len(captured) == 30
        row = assets.read_json(run / "items/2.json")
        row["dimension_calls"][0]["prompt"]["system"] = "tampered"
        # Deliberately corrupt a fixture, not a real immutable run.
        (run / "items/2.json").write_text(json.dumps(row))
        with pytest.raises(ValueError, match="dimension calls mismatch"):
            score_eval.evaluate(dataset, config=config(), prompt=PROMPT, split="dev", limit=None,
                seed="fixture", chat_factory=factory(captured), label="bad", mode="five-separate",
                workers=2, root=tmp_path, reuse=run)


def test_exact_a2_rubrics_and_raw_template_are_preserved():
    base = assets.read_json(assets.ROOT / "evals/visible-score/prompts/five-news-value-v2.json")
    independent = assets.read_json(assets.ROOT / "evals/visible-score/prompts/five-independent-v2.json")
    assert independent["user_template"] == base["user_template"]
    assert all(rubric in base["system"] for rubric in independent["dimension_rubrics"].values())
    prompts = dimension_prompts(independent, "raw body")
    assert {p["user"] for p in prompts.values()} == {"raw body"}


@pytest.mark.parametrize("mutation", ["missing", "extra", "order", "empty"])
def test_invalid_prompt_fails_before_calls(dataset, tmp_path, mutation):  # noqa: F811 - imported pytest fixture
    prompt = copy.deepcopy(PROMPT)
    if mutation == "missing":
        del prompt["dimension_rubrics"]["impact"]
    elif mutation == "extra":
        prompt["dimension_rubrics"]["score"] = "extra"
    elif mutation == "order":
        prompt["dimension_rubrics"] = dict(reversed(list(prompt["dimension_rubrics"].items())))
    else:
        prompt["dimension_rubrics"]["impact"] = ""
    captured = []
    with pytest.raises(ValueError, match="dimension_rubrics"):
        score_eval.evaluate(dataset, config=config(), prompt=prompt, split="dev", limit=1,
            seed="fixture", chat_factory=factory(captured), label="bad", mode="five-separate", root=tmp_path)
    assert not captured
