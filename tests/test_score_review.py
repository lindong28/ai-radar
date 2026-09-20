"""Archived identity, inert text and explicit score-review ballots."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from evals._shared import assets, score_review
from evals._shared.metrics import score

SCRIPT = Path(__file__).resolve().parents[1] / "evals/visible-score/score-review.js"


def make_run(path, *, title="示例新闻", mode="five", first_score=82):
    cases = [
        {"case_id": "case-a", "split": "dev", "input": {"title": title, "content_text": "原文 A",
         "source_name": "来源 A", "url": "https://example.org/a"}, "reference": {"score": 50}},
        {"case_id": "case-b", "split": "dev", "input": {"title": "另一条新闻", "content_text": "原文 B",
         "source_name": "来源 B", "url": "javascript:alert(1)"}, "reference": {"score": 65}},
    ]
    prompts = {case["case_id"]: {"system": "实际系统 prompt", "user": case["input"]["title"]} for case in cases}
    predictions = []
    for case, value in zip(cases, [first_score, 41], strict=True):
        response = {"reason": "模型当时的总理由", "impact": 7, "novelty": 6,
                    "substance": 5, "authority": 8, "relevance": 9}
        prediction = {"case_id": case["case_id"], "status": "ok", "output": {"score": value},
                      "prompt": prompts[case["case_id"]], "reason": response["reason"],
                      "response_json": json.dumps(response, ensure_ascii=False)}
        if mode == "five-separate":
            prediction["reason_origin"] = "aggregated-dimension-calls"
            prediction["reason"] = "代码聚合的理由"
            prediction["dimension_calls"] = [
                {"dimension": name, "prompt": {"system": f"只评 {name}", "user": case["input"]["title"]},
                 "reason": f"{name} 原始理由", "response_json": json.dumps({"reason": f"{name} 原始理由", name: response[name]}),
                 "status": "ok"}
                for name in ("impact", "novelty", "substance", "authority", "relevance")
            ]
        predictions.append(prediction)
    assets.write_json(path / "started.json", {
        "target": "visible-score", "benchmark": "aihot-score-pointwise", "version": "v1",
        "label": f"fixture-{mode}", "mode": mode, "split": "dev", "case_identity": assets.digest(cases),
        "case_ids": [case["case_id"] for case in cases], "rendered_prompts_sha256": assets.digest(prompts),
    })
    assets.write_jsonl(path / "cases.jsonl", cases)
    assets.write_jsonl(path / "predictions.jsonl", predictions)
    assets.write_jsonl(path / "prompts.jsonl", [{"case_id": key, "prompt": value} for key, value in prompts.items()])
    assets.write_json(path / "scores.json", score("O2", cases, predictions))
    return path


def make_analysis(path):
    assets.write_json(path, [
        {"case_id": "case-b", "category": "输入缺口", "analysis": "应由用户判断。", "counterargument": "原文可能足够。"},
        {"case_id": "case-a", "category": "疑似参考分", "analysis": "仅是一条待复核的分析。"},
    ])
    return path


def overwrite_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def overwrite_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_generation_preserves_source_and_analysis_order(tmp_path):
    run = make_run(tmp_path / "source")
    analysis = make_analysis(tmp_path / "analysis.json")
    before = {path.name: path.read_bytes() for path in run.iterdir()}
    payload = score_review.build_payload(run, analysis)
    assert [row["case_id"] for row in payload["cases"]] == ["case-b", "case-a"]
    assert payload["cases"][0]["case"] == assets.read_jsonl(run / "cases.jsonl")[1]
    assert payload["cases"][0]["prediction"] == assets.read_jsonl(run / "predictions.jsonl")[1]
    assert payload["cases"][1]["counterargument"] is None
    assert "decision" not in payload["cases"][0]
    assert payload["metadata"]["source_cases_sha256"] == assets.file_digest(run / "cases.jsonl")
    assert payload["metadata"]["source_predictions_sha256"] == assets.file_digest(run / "predictions.jsonl")
    generated = score_review.generate(run, analysis, tmp_path / "page")
    assert generated["cases"] == 2
    assert Path(generated["output"]).is_file()
    assert before == {path.name: path.read_bytes() for path in run.iterdir()}
    assert not (tmp_path / "human-evals").exists()
    with pytest.raises(FileExistsError):
        score_review.generate(run, analysis, tmp_path / "page")


@pytest.mark.parametrize("artifact", ["cases", "predictions", "prompts", "prediction_prompt", "target"])
def test_changed_source_identity_is_rejected_before_writing(tmp_path, artifact):
    run = make_run(tmp_path / "source")
    analysis = make_analysis(tmp_path / "analysis.json")
    if artifact == "target":
        metadata = assets.read_json(run / "started.json")
        metadata["target"] = "news-admission"
        overwrite_json(run / "started.json", metadata)
    else:
        filename = "predictions" if artifact == "prediction_prompt" else artifact
        path = run / f"{filename}.jsonl"
        rows = assets.read_jsonl(path)
        if artifact == "cases":
            rows[0]["input"]["title"] = "被改写的输入"
        elif artifact == "predictions":
            rows[0]["output"]["score"] = 20
        else:
            rows[0]["prompt"]["user"] = "并非原 prompt"
        overwrite_jsonl(path, rows)
    with pytest.raises(ValueError):
        score_review.generate(run, analysis, tmp_path / "page")
    assert not (tmp_path / "page").exists()


@pytest.mark.parametrize("rows", [
    [],
    [{"case_id": "absent", "category": "待判断", "analysis": "分析"}],
    [{"case_id": "case-a", "category": "断言参考有错", "analysis": "分析"}],
    [{"case_id": "case-a", "category": "待判断", "analysis": ""}],
    [{"case_id": "case-a", "category": "待判断", "analysis": "分析", "counterargument": []}],
    [{"case_id": "case-a", "category": "待判断", "analysis": "分析"}] * 2,
])
def test_invalid_analysis_is_rejected(tmp_path, rows):
    run = make_run(tmp_path / "source")
    path = tmp_path / "analysis.json"
    assets.write_json(path, rows)
    with pytest.raises(ValueError):
        score_review.build_payload(run, path)


def test_embedded_text_cannot_close_script_and_is_lossless(tmp_path):
    hostile = '</script><script>alert("not HTML")</script> & \u2028 新闻'
    run = make_run(tmp_path / "source", title=hostile)
    analysis = make_analysis(tmp_path / "analysis.json")
    payload = score_review.build_payload(run, analysis)
    html = score_review.render_html(payload)
    start = '<script id="review-data" type="application/json">'
    embedded = html.split(start, 1)[1].split("</script>", 1)[0]
    assert json.loads(embedded) == payload
    assert "<" not in embedded and "&" not in embedded and "\u2028" not in embedded
    assert hostile not in html
    assert "innerHTML" not in SCRIPT.read_text()
    assert "__REVIEW_DATA__" not in html and "/* SCORE_REVIEW_SCRIPT */" not in html


def test_separate_dimension_calls_are_preserved_without_inventing_old_reasons(tmp_path):
    run = make_run(tmp_path / "source")
    comparison = make_run(tmp_path / "candidate", mode="five-separate", first_score=61)
    analysis = make_analysis(tmp_path / "analysis.json")
    payload = score_review.build_payload(run, analysis, comparison_runs=(comparison,))
    old = payload["cases"][0]["prediction"]
    new = payload["cases"][0]["comparisons"][0]["prediction"]
    assert "dimension_calls" not in old
    assert old["reason"] == "模型当时的总理由"
    assert new["reason_origin"] == "aggregated-dimension-calls"
    assert len(new["dimension_calls"]) == 5
    assert new["dimension_calls"] == assets.read_jsonl(comparison / "predictions.jsonl")[1]["dimension_calls"]
    assert payload["source"]["label"] == "fixture-five"
    assert payload["comparisons"][0]["label"] == "fixture-five-separate"


@pytest.mark.parametrize("changed", ["input", "version"])
def test_comparison_identity_mismatch_is_rejected(tmp_path, changed):
    run = make_run(tmp_path / "source")
    comparison = make_run(tmp_path / "candidate", title="另一个输入" if changed == "input" else "示例新闻")
    if changed == "version":
        metadata = assets.read_json(comparison / "started.json")
        metadata["version"] = "v2"
        overwrite_json(comparison / "started.json", metadata)
    with pytest.raises(ValueError, match="comparison"):
        score_review.build_payload(run, make_analysis(tmp_path / "analysis.json"), comparison_runs=(comparison,))


def test_missing_comparison_prediction_stays_missing(tmp_path):
    run = make_run(tmp_path / "source")
    comparison = make_run(tmp_path / "candidate")
    rows = assets.read_jsonl(comparison / "predictions.jsonl")[:1]
    overwrite_jsonl(comparison / "predictions.jsonl", rows)
    overwrite_json(comparison / "scores.json", score("O2", assets.read_jsonl(comparison / "cases.jsonl"), rows))
    payload = score_review.build_payload(run, make_analysis(tmp_path / "analysis.json"), comparison_runs=(comparison,))
    assert payload["cases"][0]["comparisons"][0]["prediction"] is None


def test_browser_ballot_contract_persistence_and_clipboard_failure(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to exercise the page's JavaScript without a browser")
    payload = score_review.build_payload(make_run(tmp_path / "source"), make_analysis(tmp_path / "analysis.json"))
    code = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ui = require(process.argv[1]);
const page = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const pending = ui.initialState(page);
  assert.deepEqual(Object.values(pending), [{decision:'pending', reason:''}, {decision:'pending', reason:''}]);
  pending['case-a'] = {decision:'retain', reason:'只是暂保留，不确认 50 分。'};
  pending['case-b'] = {decision:'exclude', reason:'输入不足以评判该分数。'};
  const ballot = ui.exportBallot(page, pending, '2026-09-21T01:02:03Z');
  assert.equal(ballot.format, 'ai-radar-score-human-review-v1');
  assert.equal(ballot.metadata.target, 'visible-score');
  assert.equal(ballot.metadata.benchmark, 'aihot-score-pointwise');
  assert.equal(ballot.metadata.version, 'v1');
  assert.equal(ballot.metadata.exported_at, '2026-09-21T01:02:03Z');
  assert.equal(ballot.metadata.source_cases_sha256.length, 64);
  assert.equal(ballot.metadata.source_predictions_sha256.length, 64);
  assert.equal(ballot.metadata.batch_sha256.length, 64);
  assert.ok(ballot.metadata.batch_id.startsWith('score-review-'));
  assert.deepEqual(ballot.judgments.map(row => row.case_id), ['case-b', 'case-a']);
  assert.deepEqual(Object.keys(ballot.judgments[0]), ['case_id', 'decision', 'reason']);
  assert.deepEqual(ui.initialState(page, JSON.parse(JSON.stringify(ballot))), pending);
  const wrong = structuredClone(ballot);
  wrong.metadata.source_predictions_sha256 = 'other predictions';
  assert.equal(ui.initialState(page, wrong)['case-a'].decision, 'pending');
  const legacy = structuredClone(ballot);
  legacy.judgments[1].decision = 'keep';
  assert.equal(ui.initialState(page, legacy)['case-a'].decision, 'pending');
  const uncertain = structuredClone(ballot);
  uncertain.judgments[1].decision = 'uncertain';
  assert.equal(ui.initialState(page, uncertain)['case-a'].decision, 'uncertain');
  const changed = structuredClone(page);
  changed.metadata.source_cases_sha256 = 'different cases';
  assert.notEqual(ui.storageKey(page), ui.storageKey(changed));
  assert.ok(ui.storageKey(page).includes(page.metadata.source_predictions_sha256));
  const revisedBatch = structuredClone(page);
  revisedBatch.metadata.batch_sha256 = 'different analysis or comparison';
  assert.notEqual(ui.storageKey(page), ui.storageKey(revisedBatch));
  assert.equal(ui.initialState(revisedBatch, ballot)['case-b'].decision, 'pending');
  const oldBatch = structuredClone(ballot);
  delete oldBatch.metadata.batch_sha256;
  assert.equal(ui.initialState(page, oldBatch)['case-b'].decision, 'pending');
  assert.equal(ui.safeURL('https://example.org/article'), 'https://example.org/article');
  assert.equal(ui.safeURL('http://example.org/article'), 'http://example.org/article');
  for (const url of ['javascript:alert(1)', 'data:text/html,<script>x</script>', '//example.org', 'https://user:secret@example.org']) assert.equal(ui.safeURL(url), null);
  let copied = null;
  assert.equal((await ui.copyBallot(page, pending, {writeText: async text => {copied = text;}})).copied, true);
  assert.equal(JSON.parse(copied).judgments.length, 2);
  const failed = await ui.copyBallot(page, pending, {writeText: async () => {throw new Error('denied');}});
  assert.equal(failed.copied, false);
  assert.equal(JSON.parse(failed.text).judgments.length, 2);
  assert.equal((await ui.copyBallot(page, pending, null)).copied, false);
  assert.equal(ui.dimensionCalls(page.cases[0].prediction).length, 0);
  assert.equal(ui.responseObject(page.cases[0].prediction).reason, '模型当时的总理由');
  process.stdout.write('ballot, restore, URL and clipboard branches checked\n');
})().catch(error => {console.error(error); process.exit(1);});
"""
    result = subprocess.run([node, "-e", code, str(SCRIPT)], input=json.dumps(payload),
                            text=True, capture_output=True, timeout=15, check=True)
    assert "branches checked" in result.stdout


def test_cli_generates_page(tmp_path, capsys):
    run = make_run(tmp_path / "source")
    analysis = make_analysis(tmp_path / "analysis.json")
    assert score_review.main(["--source-run", str(run), "--analysis-json", str(analysis),
                              "--output-dir", str(tmp_path / "page")]) == 0
    assert "尚未写入人评标签" in capsys.readouterr().out
