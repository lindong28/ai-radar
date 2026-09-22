"""Editorial calls exercise the real runner and durable attempt writer."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_score_eval import config, dataset, gateway_companion  # noqa: F401

from evals._shared import assets, score_eval
from evals._shared.score_editorial import adjusted_dimensions, diagnosis
from evals._shared.transport import DurableChat

PROMPT = {"system": "SCORE", "user_template": "{{ item.title }}|{{ item.content_text }}",
          "editorial_system": "CLASSIFY"}


def factory(captured, failure=None):
    def directory(attempts):
        def case(key):
            def client_factory(**kwargs):
                def create(**kwargs):
                    captured.append((key, kwargs))
                    first = kwargs["messages"][0]["content"] == "CLASSIFY"
                    if failure == "timeout" and key == "2" and not first:
                        raise TimeoutError("fixture")
                    payload = ({"reason": "test input evidence", "news_value_type": "analysis"} if first else
                               {"reason": "test scoring", **dict.fromkeys(score_eval.FIVE_WEIGHTS, int(key))})
                    if failure == "late" and key == "2" and first:
                        payload = {"news_value_type": "analysis", "reason": "late"}
                    content = json.dumps(payload)
                    raw = {"model": "fixture-model", "usage": {"total_tokens": 15},
                           "choices": [{"message": {"content": content}}], "llm_gateway": gateway_companion(kwargs)}
                    return SimpleNamespace(model="fixture-model", usage=raw["usage"],
                        model_extra={"llm_gateway": raw["llm_gateway"]},
                        choices=[SimpleNamespace(message=SimpleNamespace(content=content))], model_dump=lambda **kw: raw)
                return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                       close=kwargs["http_client"].close)
            return DurableChat(attempts, **config()["transport_identity"],
                               case_id=key, client_factory=client_factory)
        return case
    return directory


@pytest.mark.parametrize("failure", [None, "late", "timeout"])
def test_two_calls_actual_prompts_gold_isolation_and_failures(dataset, tmp_path, failure):  # noqa: F811
    captured = []
    result = score_eval.evaluate(dataset, config=config(), prompt=PROMPT, split="dev", limit=None,
        seed="fixture", chat_factory=factory(captured, failure), label="editorial", mode="five-editorial",
        workers=2, root=tmp_path)
    assert result["complete"] is (failure is None)
    assert result["metrics"]["mae"]["value"] == (0 if failure is None else None)
    assert len(captured) == (11 if failure == "late" else 12)
    for key, request in captured:
        system, user = [m["content"] for m in request["messages"]]
        assert "SECRET" not in system + user
        assert user.startswith(f"Title {key}|content {key}")
        assert ("news_value_type" in user) is (system == "SCORE")
    run = Path(result["run"])
    assert len(list((run / "attempts").glob("*.json"))) == len(captured)
    for row in assets.read_jsonl(run / "predictions.jsonl"):
        assert "editorial_call" in row
        if row["status"] == "ok":
            assert row["usage"]["total_tokens"] == 30
            assert len(row["attempt_ids"]) == 2
        else:
            assert row["case_id"] == "2" and row["output"] is None


@pytest.mark.parametrize("payload", [{"news_value_type": "analysis", "reason": "late"},
    {"reason": "why", "news_value_type": "unregistered"}, {"reason": "why", "news_value_type": "analysis", "score": 50}])
def test_bad_diagnosis_rejected(payload):
    with pytest.raises(ValueError):
        diagnosis(payload)


@pytest.mark.parametrize("news_type,source,cap,floor", [
    ("promotion", "anthropic_news", 3, 6), ("substantive_release", "anthropic_news", 3, 6),
    ("routine_release", "anthropic_news", 3, 6), ("substantive_release", "unverified", 3, 6),
    ("analysis", "x_emollick", 4, 5), ("unknown", "anthropic_news", 3, 6)])
def test_rules_are_dimension_specific_and_do_not_mutate(news_type, source, cap, floor):
    original = {"reason": "evidence", "impact": 4, "novelty": 6, "substance": 7, "authority": 8, "relevance": 10}
    result = adjusted_dimensions(original, news_type, source, promotion_cap=cap, official_impact_floor=floor)
    assert original["impact"] == 4 and original["novelty"] == 6
    assert result["authority"] == 8 and result["relevance"] == 10
    if news_type == "promotion":
        assert [result[k] for k in ("impact", "novelty", "substance")] == [3, 3, 3]
    elif news_type == "substantive_release" and source == "anthropic_news":
        assert result["impact"] == 6 and result["substance"] == 7
    else:
        assert result == original


def test_candidate_boundary_and_examples_are_isolated():
    base = assets.read_json(assets.ROOT / "evals/visible-score/prompts/five-evidence-boundary-v3.json")
    p1 = assets.read_json(assets.ROOT / "evals/visible-score/prompts/five-editorial-boundary-v1.json")
    p2 = assets.read_json(assets.ROOT / "evals/visible-score/prompts/five-editorial-examples-v1.json")
    p3 = assets.read_json(assets.ROOT / "evals/visible-score/prompts/five-editorial-workflow-v1.json")
    assert p1["system"].startswith(base["system"])
    assert p2["system"].startswith(p1["system"])
    assert p3["system"] == p1["system"]
    assert {p["user_template"] for p in (base, p1, p2, p3)} == {base["user_template"]}


def test_editorial_reuse_rejects_changed_auxiliary_prompt(dataset, tmp_path):  # noqa: F811
    captured = []
    kwargs = dict(config=config(), prompt=PROMPT, split="dev", limit=None, seed="fixture",
                  chat_factory=factory(captured), label="reuse", mode="five-editorial", workers=2, root=tmp_path)
    result = score_eval.evaluate(dataset, **kwargs)
    reuse_kwargs = {**kwargs, "root": tmp_path / "reused"}
    metric_path = Path("evals/visible-score/aihot-score-pointwise/metrics.json")
    assets.write_json(tmp_path / "reused" / metric_path, assets.read_json(tmp_path / metric_path))
    reused = score_eval.evaluate(dataset, reuse=Path(result["run"]), **reuse_kwargs)
    assert reused["complete"] and len(captured) == 12
    item = Path(result["run"]) / "items/2.json"
    row = assets.read_json(item)
    row["editorial_call"]["prompt"]["user"] = "changed input"
    assets.replace_json(item, row)
    with pytest.raises(ValueError, match="editorial calls mismatch"):
        score_eval.evaluate(dataset, reuse=Path(result["run"]), **kwargs)


def test_rule_values_use_input_and_predictions_not_gold():
    from evals._shared.score_editorial_rules import values, views
    from evals._shared.score_context_analysis import train_case
    cases = [{"case_id": str(i), "input": {"source_id": source}, "reference": {"score": gold}}
             for i, (source, gold) in enumerate((("anthropic_news", 20), ("x_emollick", 80)))]
    dims = [{"reason": "input evidence", **dict.fromkeys(score_eval.FIVE_WEIGHTS, 5)} for _ in cases]
    scores = values(cases, dims, ["substantive_release", "promotion"],
                    {"promotion_cap": 3, "official_impact_floor": 6})
    assert scores == [54, 34]
    measured = views(cases, scores)
    assert measured["all"]["metrics"]["mae"]["value"] == 40
    assert sum(len([c for c in cases if train_case(c) == flag]) for flag in (True, False)) == 2
    cases[0]["reference"]["score"] = 99
    assert values(cases, dims, ["substantive_release", "promotion"],
                  {"promotion_cap": 3, "official_impact_floor": 6}) == scores


def test_fit_ignores_heldout_gold_and_replay_rejects_other_scorer(tmp_path, monkeypatch):
    from copy import deepcopy
    from evals._shared import score_editorial_rules as rules
    from evals._shared.score_context_analysis import train_case
    sources = [f"source-{i}" for i in range(20)]
    train = next(s for s in sources if train_case({"input": {"source_id": s}}))
    held = next(s for s in sources if not train_case({"input": {"source_id": s}}))
    cases = [{"case_id": str(i), "input": {"source_id": s}, "reference": {"score": g}}
             for i, (s, g) in enumerate(((train, 30), (train, 60), (held, 20), (held, 80)))]
    dims = [{"reason": "input", **dict.fromkeys(score_eval.FIVE_WEIGHTS, i + 4)} for i in range(4)]
    labels = ["promotion", "analysis", "promotion", "analysis"]
    metadata = {"split": "dev", "object_identity": {"scorer": "A11"}}
    provenance = {"baseline_run": "baseline", "editorial_run": "editorial", "classifier_identity": "fixed"}
    def inputs(baseline, editorial, root):
        meta = deepcopy(metadata)
        if str(baseline) == "other":
            meta["object_identity"] = {"scorer": "different"}
        return meta, deepcopy(cases), dims, labels, provenance
    monkeypatch.setattr(rules, "paired_inputs", inputs)  # I/O fixture; real selection and replay checks.
    mapping = tmp_path / "mapping.json"
    first = rules.fit(Path("baseline"), Path("editorial"), mapping, tmp_path)
    cases[2]["reference"]["score"], cases[3]["reference"]["score"] = 100, 0
    second = rules.fit(Path("baseline"), Path("editorial"), tmp_path / "second.json", tmp_path)
    assert {k: v["parameters"] for k, v in first["selected"].items()} == {
        k: v["parameters"] for k, v in second["selected"].items()}
    assert first["fit_case_ids"] == ["0", "1"]
    with pytest.raises(ValueError, match="baseline scoring object differs"):
        rules.replay(Path("other"), Path("editorial"), mapping, "combined", tmp_path)


@pytest.mark.parametrize("failure", [None, "late"])
def test_classifier_only_retains_real_attempts_and_rejects_invalid(dataset, tmp_path, monkeypatch, failure):  # noqa: F811
    from test_score_eval import fixture_root
    from evals._shared import score_editorial_classify as classifier
    from evals._shared.score_editorial_rules import paired_inputs
    captured = []
    kwargs = dict(config=config(), split="dev", limit=None, seed="fixture",
                  chat_factory=factory(captured), label="fixture", workers=2)
    base = score_eval.evaluate(dataset, prompt={k: PROMPT[k] for k in ("system", "user_template")},
                              mode="five", root=tmp_path, **kwargs)
    candidate = score_eval.evaluate(dataset, prompt=PROMPT, mode="five-editorial",
                                   root=fixture_root(tmp_path / "candidate"), **kwargs)
    captured.clear()
    monkeypatch.setattr(classifier, "transport_factory", lambda *a: factory(captured, failure))
    monkeypatch.setattr(classifier, "check_identity", lambda dataset, config, prompt, *a:
                        score_eval.object_identity(config, prompt, "five-editorial"))
    output = tmp_path / "classify"
    args = (Path(base["run"]), Path(candidate["run"]), output, tmp_path / "unused.env", tmp_path, 2)
    if failure:
        with pytest.raises(ValueError, match="incomplete"):
            classifier.classify(*args)
        with pytest.raises(ValueError, match="incomplete"):
            paired_inputs(Path(base["run"]), output, tmp_path)
    else:
        assert classifier.classify(*args)["complete"]
        _, cases, _, labels, _ = paired_inputs(Path(base["run"]), output, tmp_path)
        assert len(cases) == 6 and labels == ["analysis"] * 6
    assert len(captured) == 6
    assert len(list((output / "attempts").glob("*.json"))) == 6
    assert all(request["messages"][0]["content"] == "CLASSIFY" for _, request in captured)
