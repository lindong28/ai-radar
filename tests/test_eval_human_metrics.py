"""Current human-priority query consumers must not rewrite model history."""
import json
from datetime import UTC, datetime

import pytest

from evals._shared import assets, runner
from evals._shared.human_labels import input_identity
from evals._shared.human_store import append_batch
from evals._shared.metrics import score


def fixture_run(root, second=1, predictions=(True, True), failed=False):
    benchmark = assets.OBSERVED_ADMISSION
    definition = root / 'evals/news-admission' / benchmark / 'metrics.json'
    if not definition.exists():
        assets.write_json(definition, assets.read_json(assets.ROOT / 'evals/news-admission' / benchmark / 'metrics.json'))
    run, experiment = assets.create_run(root, 'news-admission', 'v1', benchmark=benchmark,
                                       created_at=datetime(2026, 9, 20, 1, 2, second, tzinfo=UTC))
    cases = [{'case_id': key, 'split': 'dev', 'input': {'title': key, 'content_text': key,
              'source_id': 'lab', 'url': 'https://example.com/' + key},
              'reference': {'member': value}} for key, value in [('a', True), ('b', False)]]
    rows = [{'case_id': case['case_id'], 'status': 'ok', 'output': {'member': value}}
            for case, value in zip(cases, predictions)]
    if failed:
        rows[0] = {'case_id': 'a', 'status': 'error', 'output': {}, 'error': 'timeout'}
    assets.write_jsonl(run / 'cases.jsonl', cases)
    assets.write_jsonl(run / 'predictions.jsonl', rows)
    metadata = {'target': 'news-admission', 'benchmark': benchmark, 'version': 'v1',
                'case_identity': assets.digest(cases), 'split': 'dev', 'scorer_identity': {},
                'smoke': None, 'status': 'failed' if failed else 'complete'}
    assets.archive_metrics(root, run, experiment, score('O1', cases, rows), metadata)
    return run, experiment, cases, rows


def vote(root, case, value, batch='one'):
    annotation = {'target': 'news-admission', 'case_id': case['case_id'], 'field': 'member',
                  'value': value, 'input_identity': input_identity(case, 'news-admission'),
                  'provenance': 'user', 'reason': 'Explicit user judgment', 'ballot_sha256': batch}
    return append_batch(root / 'human-evals/news-admission/reviews.json', 'news-admission',
                        {'metadata': {'batch_id': batch}, 'data': {'annotations': [annotation]}})


def test_current_queries_rescore_preserve_history_and_are_idempotent(tmp_path):
    run, experiment, cases, _ = fixture_run(tmp_path)
    vote(tmp_path, cases[1], True)
    originals = {p: p.read_bytes() for p in [run / 'cases.jsonl', run / 'predictions.jsonl',
                                            run / 'scores.json', experiment / 'metrics/summary.json']}
    rows = assets.rebuild_index(tmp_path)
    assert [r['metric_value'] for r in rows] == [1, 1]
    assert all(r['human_case_count'] == 1 and r['changed_field_count'] == 1 for r in rows)
    assert assets.read_json(experiment / 'metrics/current.json') == rows
    for row in rows:
        value = assets.read_json(tmp_path / row['source'])
        for key in row['pointer']:
            value = value[key]
        assert value == row['metric_value']
    before = {p: p.read_bytes() for p in experiment.glob('metrics/human-priority-*.json')}
    assert len(before) == 1
    assert assets.rebuild_index(tmp_path) == rows
    assert all(p.read_bytes() == content for p, content in (originals | before).items())
    vote(tmp_path, cases[0], True, 'two')
    updated = assets.rebuild_index(tmp_path)
    assert updated[0]['human_case_count'] == 2
    assert updated[0]['source'] != rows[0]['source']
    assert all(p.read_bytes() == content for p, content in before.items())


def test_compare_uses_human_priority_not_observed_scores(tmp_path):
    _, a, cases, _ = fixture_run(tmp_path, 1, (True, False))
    _, b, _, _ = fixture_run(tmp_path, 2, (True, True))
    assert runner.compare(a, b, root=tmp_path)['accepted'] is False
    vote(tmp_path, cases[1], True)
    result = runner.compare(a, b, root=tmp_path)
    assert result['accepted'] is True
    assert result['label_policy'] == 'human-reference-priority-v1'


@pytest.mark.parametrize('failure', ['input', 'conflict', 'predictions', 'missing_bank'])
def test_invalid_rebuild_keeps_last_published_index(tmp_path, failure):
    run, _, cases, _ = fixture_run(tmp_path)
    vote(tmp_path, cases[1], True)
    assets.rebuild_index(tmp_path)
    index = tmp_path / 'experiments/metrics/summary.json'
    before = index.read_bytes()
    if failure == 'input':
        cases[1]['input']['content_text'] = 'different'
        (run / 'cases.jsonl').write_text('\n'.join(json.dumps(c) for c in cases) + '\n')
    elif failure == 'conflict':
        vote(tmp_path, cases[1], False, 'two')
    elif failure == 'predictions':
        (run / 'predictions.jsonl').write_text('{"case_id":"a","status":"ok","output":{"member":false}}\n')
    else:
        (tmp_path / 'human-evals/news-admission/reviews.json').unlink()
    with pytest.raises(ValueError):
        assets.rebuild_index(tmp_path)
    assert index.read_bytes() == before


def test_failed_run_stays_unscored_and_zero_overlap_is_explicit(tmp_path):
    _, _, cases, _ = fixture_run(tmp_path, failed=True)
    vote(tmp_path, {**cases[1], 'case_id': 'absent'}, True)
    rows = assets.rebuild_index(tmp_path)
    assert all(r['human_case_count'] == 0 for r in rows)
    assert all(r['metric_value'] is None and r['status'] == 'not_computed' for r in rows)


def test_policy_predictions_are_a_separate_view(tmp_path):
    run, _, cases, predictions = fixture_run(tmp_path)
    vote(tmp_path, cases[1], True)
    policy = [predictions[0], {**predictions[1], 'output': {'member': False}}]
    assets.write_jsonl(run / 'policy-predictions.jsonl', policy)
    assets.write_json(run / 'human-feedback-scores.json', {
        'cases_sha256': assets.file_digest(run / 'cases.jsonl'),
        'predictions_sha256': assets.file_digest(run / 'predictions.jsonl'),
        'views': {'with_existing_policy': {'observed': score('O1', cases, policy)}},
    })
    rows = assets.rebuild_index(tmp_path)
    assert len(rows) == 4
    assert {(r['prediction_view'], r['metric_value']) for r in rows if r['metric_name'] == 'recall'} == {
        ('archived', 1), ('with_existing_policy', .5)}
    for row in rows:
        value = assets.read_json(tmp_path / row['observed_source'])
        for key in row['observed_pointer']:
            value = value[key]
        expected = 0.5 if row['metric_name'] == 'precision' and row['prediction_view'] == 'archived' else 1.0
        assert value == expected
