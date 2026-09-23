"""Build a local, read-only experiment atlas from archived category runs. No LLM calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from catalogue import NODES, NOTES
from evals._shared.category_metrics import score_categories

HERE = Path(__file__).resolve().parent
REL = Path('runs/content-enrichment/aihot-category-navigation/v1')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def keyed(records):
    result = {r['case_id']: r for r in records}
    if len(result) != len(records):
        raise ValueError('duplicate case_id in archive')
    return result


def project_prediction(p):
    if p is None:
        return dict(status='missing', category=None, reason='未记录预测')
    return dict(status=p['status'], category=p.get('output', {}).get('category'),
                reason=p.get('reason') or '原档未记录 reason')


def load_run(root, run_id):
    path = root / REL / run_id
    cases = rows(path / 'cases.jsonl')
    predictions = rows(path / 'predictions.jsonl')
    by_id = keyed(cases)
    pred = keyed(predictions)
    prompts = keyed(rows(path / 'prompts.jsonl'))
    if not set(pred) <= set(by_id) or not set(prompts) <= set(by_id):
        raise ValueError(f'{run_id}: archive contains unknown IDs')
    scores = score_categories(cases, predictions)
    stored = json.loads((path / 'scores.json').read_text())
    for metric, value in scores['metrics'].items():
        if metric in stored['metrics'] and value['value'] != stored['metrics'][metric]['value']:
            raise ValueError(f'{run_id}: canonical score drift: {metric}')
    first_path = path / 'first-pass-predictions.jsonl'
    first = keyed(rows(first_path)) if first_path.exists() else {}
    systems = []

    def prompt_view(prompt):
        if not prompt:
            return None
        system = prompt.get('system', '')
        if system not in systems:
            systems.append(system)
        return dict(system=systems.index(system), user=prompt.get('user', ''))

    projected = []
    for case in cases:
        id = case['case_id']
        review = path / 'review-prompts' / f'{id}.json'
        projected.append(dict(id=id, split=case['split'], input=case['input'],
            gold=case['reference']['category'], prediction=project_prediction(pred.get(id)),
            prompt=prompt_view(prompts.get(id, {}).get('prompt')),
            first=project_prediction(first.get(id)) if first else None,
            review_prompt=prompt_view(json.loads(review.read_text())) if review.exists() else None))
    names = ['cases.jsonl','predictions.jsonl','prompts.jsonl','scores.json','config.json']
    if first:
        names.append('first-pass-predictions.jsonl')
    hashes = {name: digest(path / name) for name in names}
    for review in sorted((path / 'review-prompts').glob('*.json')):
        hashes[f'review-prompts/{review.name}'] = digest(review)
    config = json.loads((path / 'config.json').read_text())
    return dict(id=run_id, model=config.get('models', {}).get('category'),
        cases=projected, systems=systems, metrics=scores['metrics'], hashes=hashes,
        newly_computed_metrics=sorted(set(scores['metrics'])-set(stored['metrics'])),
        source=str(REL / run_id), conclusion=(path / 'conclusion.md').read_text() if (path / 'conclusion.md').exists() else '')


def join(data, ids):
    combined = {}
    for run in ids:
        for case in data[run]['cases']:
            if case['id'] in combined:
                raise ValueError(f'overlapping run partitions: {run}/{case["id"]}')
            combined[case['id']] = (case, run)
    return combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output must be a new directory; existing reports are never overwritten')
    data = {}
    for node in NODES:
        for a in node['assessments']:
            for id in a['runs'] + a['baseline']:
                if id not in data:
                    data[id] = load_run(args.archive_root, id)
            target = join(data, a['runs'])
            control = join(data, a['baseline'])
            common = set(target) & set(control)
            if control and not common:
                raise ValueError('comparison has no shared cases')
            for id in common:
                c, b = target[id][0], control[id][0]
                if c['gold'] != b['gold'] or c['input'] != b['input']:
                    raise ValueError(f'case/gold drift in comparison {node["id"]}/{id}')
            a['n'] = len(target)
            a['correct'] = sum(c['prediction']['status']=='ok' and c['prediction']['category']==c['gold'] for c,_ in target.values())
            a['failures'] = sum(c['prediction']['status']!='ok' for c,_ in target.values())
            a['paired_n'] = len(common)
            a['missing_seeds'] = [s for s in a['seeds'] if not any(id.startswith(s) for id in target)]
            for s in a['seeds']:
                if sum(id.startswith(s) for id in target) > 1:
                    raise ValueError(f'ambiguous seed prefix: {s}')
    manifest = dict(format='ai-radar-category-atlas-v1', generated_at=datetime.now(timezone.utc).isoformat(),
        benchmark='content-enrichment/aihot-category-navigation/v1', nodes=NODES, notes=NOTES,
        provenance=['docs/evaluations/content-enrichment/status.md','docs/evaluations/experiments/hypotheses.md'],
        runs={id:dict(file=f'data/{id.replace("/","_")}.json',model=r['model'],hashes=r['hashes']) for id,r in data.items()})
    manifest['editorial_source_hashes'] = {name: digest(args.archive_root / name) for name in manifest['provenance']}
    manifest['report_code_hashes'] = {name: digest(HERE / name) for name in ['catalogue.py','build.py','app.js','index.html','style.css']}
    manifest['snapshot_sha256'] = hashlib.sha256(json.dumps(manifest,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    args.output.mkdir(parents=True)
    (args.output / 'data').mkdir()
    for name in ['index.html','style.css','app.js']:
        shutil.copyfile(HERE / name, args.output / name)
    for id, run in data.items():
        (args.output / manifest['runs'][id]['file']).write_text(json.dumps(run,ensure_ascii=False))
    (args.output / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(dict(output=str(args.output), candidates=len(NODES), runs=len(data),
                         comparisons=sum(len(n['assessments']) for n in NODES),
                         validation='canonical metrics and paired input/gold checked',
                         snapshot_sha256=manifest['snapshot_sha256']),ensure_ascii=False))


if __name__ == '__main__':
    main()
