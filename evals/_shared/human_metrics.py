"""Append human-priority rescoring receipts; expose rebuildable current rows.

No inference is performed. Frozen cases, predictions and scores remain intact.
"""
from __future__ import annotations

from pathlib import Path

from .assets import digest, file_digest, read_json, read_jsonl, write_json
from .human_labels import POLICY, apply_labels
from .human_store import read_reviews
from .metrics import score
from .relocations import resolve_asset_path


def review_snapshot(root: Path) -> dict | None:
    path = root / 'human-evals/news-admission/reviews.json'
    if not path.exists():
        return None
    book = read_reviews(path)
    if book['metadata']['target'] != 'news-admission':
        raise ValueError('human review target mismatch')
    return book


def current_rows(root: Path, experiment: Path, rows: list[dict], book: dict | None) -> list[dict]:
    """Verify original observations, then score the explicit current human view."""
    metadata = read_json(experiment / 'metadata.json')
    if metadata['target'] != 'news-admission':
        return rows
    if book is None:
        if any((experiment / 'metrics').glob('human-priority-*.json')):
            raise ValueError('human review bank missing; refusing fallback to observed labels')
        return rows
    run = resolve_asset_path(Path('runs') / rows[0]['run_id'], root=root)
    cases_path, predictions_path = run / 'cases.jsonl', run / 'predictions.jsonl'
    cases, predictions = read_jsonl(cases_path), read_jsonl(predictions_path)
    if digest(cases) != metadata['case_identity']:
        raise ValueError(f'frozen case identity mismatch: {run}')
    original = read_json(run / 'scores.json')
    if score('O1', cases, predictions) != original:
        raise ValueError(f'frozen predictions/scorer differ from original scores: {run}')
    annotations = [a for batch in book['batches'] for a in batch['data']['annotations']]
    effective, application = apply_labels(cases, annotations, 'news-admission')
    views = {'archived': (predictions_path, predictions)}
    policy_path = run / 'policy-predictions.jsonl'
    if policy_path.exists():
        sidecar = read_json(run / 'human-feedback-scores.json')
        if (sidecar['cases_sha256'] != file_digest(cases_path)
                or sidecar['predictions_sha256'] != file_digest(predictions_path)):
            raise ValueError(f'policy source identity mismatch: {run}')
        policy = read_jsonl(policy_path)
        if score('O1', cases, policy) != sidecar['views']['with_existing_policy']['observed']:
            raise ValueError(f'policy predictions differ from original scores: {run}')
        if (sidecar.get('policy_predictions_sha256') is not None
                and sidecar['policy_predictions_sha256'] != file_digest(policy_path)):
            raise ValueError(f'policy predictions hash mismatch: {run}')
        views['with_existing_policy'] = (policy_path, policy)
    module = Path(__file__).parent
    # Include the actual executing implementation, not another checkout's files.
    implementation = {name: file_digest(module / name) for name in (
        'human_metrics.py', 'human_labels.py', 'human_store.py', 'identity.py', 'metrics.py', 'assets.py')}
    identity = {
        'cases_sha256': file_digest(cases_path), 'predictions_sha256': file_digest(predictions_path),
        'effective_cases_digest': digest(effective), 'metadata_sha256': file_digest(experiment / 'metadata.json'),
        'original_scores_sha256': file_digest(run / 'scores.json'),
        'batches': [{'batch_id': b['metadata']['batch_id'], 'sha256': b['sha256']} for b in book['batches']],
        'prediction_views': {name: {'path': str(path.relative_to(root)), 'sha256': file_digest(path)}
                             for name, (path, _) in views.items()},
        'implementation_sha256': implementation,
    }
    human_ids = {c['case_id'] for c in effective if c.get('provenance', {}).get('human_reference')}
    receipt = {'label_policy': POLICY, 'identity': identity, 'application': application,
               'original_scores': str((run / 'scores.json').relative_to(root)),
               'reviews': 'human-evals/news-admission/reviews.json', 'views': {}}
    for view, (_, predictions) in views.items():
        receipt['views'][view] = {
            'scores': score('O1', effective, predictions),
            'human_only': score('O1', [c for c in effective if c['case_id'] in human_ids],
                                [p for p in predictions if p['case_id'] in human_ids]),
        }
    path = experiment / 'metrics' / f'human-priority-{digest(identity)}.json'
    if path.exists():
        if read_json(path) != receipt:
            raise ValueError(f'human-priority receipt identity collision: {path}')
    else:
        write_json(path, receipt)
    projected = []
    for view in views:
        for row in rows:
            name = row['metric_name']
            value = receipt['views'][view]['scores']['metrics'][name]
            projected.append({**row, 'metric_value': value['value'], 'status': value['status'],
                              'reason': value.get('reason'), 'source': str(path.relative_to(root)),
                              'pointer': ['views', view, 'scores', 'metrics', name, 'value'],
                              'observed_source': (row['source'] if view == 'archived' else
                                                  str((run / 'human-feedback-scores.json').relative_to(root))),
                              'observed_pointer': (row['pointer'] if view == 'archived' else
                                                   ['views', view, 'observed', 'metrics', name, 'value']),
                              'label_policy': POLICY, 'prediction_view': view,
                              'human_case_count': application['human_case_count'],
                              'changed_field_count': application['changed_field_count'],
                              'effective_cases_digest': identity['effective_cases_digest']})
    return projected
