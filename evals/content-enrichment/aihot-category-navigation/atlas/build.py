"""Build a local, read-only experiment atlas from archived category runs. No LLM calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import importlib.util
import shutil
from datetime import datetime, timezone
from pathlib import Path

from catalogue import CONTROL_RUN_OWNERS, NODES, NOTES
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


def annotate_comparisons(nodes, controls):
    """Resolve comparison identity from run ownership, never display labels."""
    owners = dict(controls)
    known = {node['id'] for node in nodes}
    for node in nodes:
        for assessment in node['assessments']:
            for run in assessment['runs']:
                if run in owners and owners[run] != node['id']:
                    raise ValueError(f'conflicting run ownership: {run}')
                owners[run] = node['id']
    if not set(owners.values()) <= known:
        raise ValueError('unknown control candidate')
    for node in nodes:
        for assessment in node['assessments']:
            try:
                identities = sorted({owners[run] for run in assessment['baseline']})
            except KeyError as error:
                raise ValueError(f'unknown baseline run ownership: {error}') from error
            assessment['baseline_candidates'] = identities
            assessment['comparison_role'] = (
                'unpaired' if not identities else
                'parent' if identities == [node['parent']] else 'auxiliary')


def descendants(nodes, anchor, inspiration=True):
    found = set()
    pending = [anchor]
    while pending:
        parent = pending.pop()
        for node in nodes:
            links = [node['parent']] + (node.get('inspiration', []) if inspiration else [])
            if parent in links and node['id'] not in found:
                found.add(node['id'])
                pending.append(node['id'])
    found.discard(anchor)
    return found


def representative(node, data):
    """Largest coverage, then latest run timestamp; never pick by metric."""
    if not node['assessments']:
        return []
    return max(node['assessments'], key=lambda a: (len(join(data, a['runs'])), max(a['runs'], default='')))['runs']


def projected_scores(cases, ids):
    originals = [dict(case_id=id, input=cases[id][0]['input'], reference={'category': cases[id][0]['gold']}) for id in ids]
    predictions = [dict(case_id=id, status=cases[id][0]['prediction']['status'], output={'category': cases[id][0]['prediction']['category']}) for id in ids]
    return score_categories(originals, predictions)['metrics']


def downstream_tables(nodes, data):
    chosen = {node['id']: representative(node, data) for node in nodes}
    tables = {}
    labels = [('category_accuracy', '准确率')]
    for slug, title in [('model','模型'),('product','产品'),('industry','行业'),('paper','论文'),('tutorial','教程'),('opinion','观点')]:
        labels.extend((f'category_{slug}_{metric}', title + ' ' + metric.title()) for metric in ['precision', 'recall'])
    fmt = lambda value: '—' if value is None else f'{100*value:.2f}%'
    for node in nodes:
        anchor = node['id']
        downstream = descendants(nodes, anchor)
        direct = descendants(nodes, anchor, inspiration=False)
        baseline = join(data, chosen[anchor])
        rows_out = []
        for candidate in nodes:
            if candidate['id'] not in downstream:
                continue
            candidate_runs = chosen[candidate['id']]
            target = join(data, candidate_runs)
            common = sorted(set(baseline) & set(target))
            drift = [id for id in common if baseline[id][0]['input'] != target[id][0]['input'] or baseline[id][0]['gold'] != target[id][0]['gold']]
            row = dict(id=candidate['id'], title=candidate['title'], relation='实现后代' if candidate['id'] in direct else '含借鉴路径的后代',
                baseline_runs=chosen[anchor], candidate_runs=candidate_runs, baseline_n=len(baseline), candidate_n=len(target), paired_n=len(common), metrics=[])
            if not common or drift:
                row.update(status='unavailable', scope='无法建立同输入、同参考的配对', note=f'{len(drift)} 题 input/gold 漂移；不计算差值' if drift else '无共同题目或缺运行')
            else:
                before, after = projected_scores(baseline, common), projected_scores(target, common)
                for key, title in labels:
                    b, c = before[key]['value'], after[key]['value']
                    row['metrics'].append(dict(label=title, baseline=fmt(b), candidate=fmt(c), delta=None if b is None or c is None else f'{100*(c-b):+.2f} pp', direction='higher'))
                correct = lambda case: case['prediction']['status'] == 'ok' and case['prediction']['category'] == case['gold']
                fixes = sum(not correct(baseline[id][0]) and correct(target[id][0]) for id in common)
                regressions = sum(correct(baseline[id][0]) and not correct(target[id][0]) for id in common)
                row.update(status='paired', scope=f'共同题目 {len(common)} 题',
                    note=f'修复 {fixes} / 回退 {regressions}；所选排除 {len(baseline)-len(common)} 题，下游排除 {len(target)-len(common)} 题。')
            rows_out.append(row)
        tables[anchor] = dict(anchor=anchor, rows=rows_out)
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--atlas-skill', type=Path, default=Path.home() / '.claude/skills/eval-workflows')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output must be a new directory; existing reports are never overwritten')
    annotate_comparisons(NODES, CONTROL_RUN_OWNERS)
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
    manifest['downstream'] = downstream_tables(NODES, data)
    manifest['report_code_hashes'] = {name: digest(HERE / name) for name in ['catalogue.py','build.py','app.js','presentation.json']}
    bundler_path = args.atlas_skill / 'scripts/bundle_experiment_atlas.py'
    spec = importlib.util.spec_from_file_location('atlas_bundle', bundler_path)
    bundler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundler)
    manifest['shared_assets_sha256'] = bundler.bundle(args.output, json.loads((HERE / 'presentation.json').read_text()))
    manifest['bundler_sha256'] = digest(bundler_path)
    manifest['snapshot_sha256'] = hashlib.sha256(json.dumps(manifest,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    (args.output / 'data').mkdir()
    shutil.copyfile(HERE / 'app.js', args.output / 'app.js')
    for id, run in data.items():
        (args.output / manifest['runs'][id]['file']).write_text(json.dumps(run,ensure_ascii=False))
    (args.output / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(dict(output=str(args.output), candidates=len(NODES), runs=len(data),
                         comparisons=sum(len(n['assessments']) for n in NODES),
                         validation='canonical metrics and paired input/gold checked',
                         snapshot_sha256=manifest['snapshot_sha256']),ensure_ascii=False))


if __name__ == '__main__':
    main()
