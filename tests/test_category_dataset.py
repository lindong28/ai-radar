import json
from pathlib import Path

import pytest

from evals._shared import assets
from evals._shared.category_dataset import build, observations, page_items


def source(tmp_path):
    path = tmp_path / 'inputs/content-enrichment/aihot-enrichment-fields/v1'
    cases = [{'case_id': k, 'split': split, 'input': {'source_id': 'x_author',
        'url': f'https://x.com/author/status/{i}', 'title': k, 'content_text': f'body-{k}'},
        'reference': {'category': 'tip'}, 'provenance': {'aihot': [{'item_id': k}]}}
        for i, (k, split) in enumerate([('a', 'dev'), ('b', 'regression')], 1)]
    assets.write_jsonl(path / 'cases.jsonl', cases)
    assets.write_json(path / 'manifest.json', {'schema_version': 2, 'target': 'content-enrichment',
        'benchmark': 'aihot-enrichment-fields', 'version': 'v1', 'case_count': 2,
        'evaluation_mode': 'pointwise', 'shared_evidence': '.', 'evidence_files': {},
        'files': {'cases.jsonl': assets.file_digest(path / 'cases.jsonl')}})
    return path


def response(path, name, category, ids):
    assets.write_json(path / f'{name}.body', {'items': [{'id': k, 'url': f'https://x.com/i/web/status/{ord(k) - 96}'} for k in ids]})
    assets.write_json(path / f'{name}.response.json', {'url': f'https://aihot.news/api/public/feed?mode=all&category={category}',
        'status': 200, 'finished_at': '2026-09-21T00:00:00Z',
        'sha256': assets.file_digest(path / f'{name}.body')})


def test_page_parser_reads_news_not_navigation_and_rejects_empty_shell():
    item = {'id': 'a', 'title': 'original', 'url': 'https://example.org/a', 'aiTags': []}
    rsc = '7:' + json.dumps({'news': [item]}) + '\n'
    html = '<script>self.__next_f.push(' + json.dumps([1, rsc]) + ')</script>'
    assert page_items(html.encode()) == [item]
    with pytest.raises(ValueError, match='no news'):
        page_items(b'<a href="/all?category=opinion">opinion</a>')


def test_build_merge_conflict_and_tamper(tmp_path):
    inputs, capture = source(tmp_path), tmp_path / 'capture'
    response(capture, 'one', 'opinion', ['a'])
    response(capture, 'two', 'tip', ['b'])
    root = tmp_path / 'benchmarks'
    first = Path(build([inputs], [capture], [], 'v1', root)['dataset'])
    _, cases = assets.load_dataset(first)
    assert [(c['case_id'], c['reference']['category'], c['split']) for c in cases] == [
        ('a', 'opinion', 'dev'), ('b', 'tip', 'regression')]
    assert all(c['input']['content_text'] == 'body-' + c['case_id'] for c in cases)
    second = Path(build([inputs], [capture], [first], 'v2', root)['dataset'])
    assert assets.load_dataset(second)[0]['case_count'] == 2
    conflict = tmp_path / 'conflict'
    response(conflict, 'changed', 'paper', ['a'])
    third = Path(build([], [conflict], [second], 'v3', root)['dataset'])
    assert [c['case_id'] for c in assets.load_dataset(third)[1]] == ['b']
    assert assets.read_jsonl(third / 'excluded.jsonl') == [{'case_id': 'a', 'reason': 'conflicting_navigation'}]
    with pytest.raises(FileExistsError):
        build([], [], [first], 'v1', root)
    mismatched = tmp_path / 'mismatched'
    response(mismatched, 'wrong', 'opinion', ['a'])
    body = mismatched / 'wrong.body'
    assets.replace_json(body, {'items': [{'id': 'a', 'url': 'https://x.com/i/web/status/999'}]})
    meta = assets.read_json(mismatched / 'wrong.response.json')
    assets.replace_json(mismatched / 'wrong.response.json', {**meta, 'sha256': assets.file_digest(body)})
    fourth = Path(build([], [mismatched], [second], 'v4', root)['dataset'])
    assert assets.read_jsonl(fourth / 'excluded.jsonl') == [{'case_id': 'a', 'reason': 'source_url_mismatch'}]
    (capture / 'one.body').write_text('{}')
    with pytest.raises(ValueError, match='hash differs'):
        observations(capture)
