"""Run with unittest discover -s <atlas directory>; no model or archive access."""
from copy import deepcopy
import unittest

from build import annotate_comparisons, descendants, downstream_tables, representative
from catalogue import CONTROL_RUN_OWNERS, NODES


class ComparisonIdentityTest(unittest.TestCase):
    def setUp(self):
        self.nodes = deepcopy(NODES)

    def test_existing_catalogue_has_explicit_comparison_roles(self):
        annotate_comparisons(self.nodes, CONTROL_RUN_OWNERS)
        c5 = next(n for n in self.nodes if n['id'] == 'C5')
        self.assertEqual(c5['assessments'][0]['comparison_role'], 'parent')
        self.assertEqual(c5['assessments'][0]['baseline_candidates'], ['C1'])
        self.assertEqual(c5['assessments'][1]['comparison_role'], 'auxiliary')
        self.assertEqual(c5['assessments'][1]['baseline_candidates'], ['B1'])
        self.assertTrue(all(a['comparison_role'] == 'unpaired' for a in c5['assessments'][2:]))

    def test_display_label_cannot_change_comparison_identity(self):
        for node in self.nodes:
            for assessment in node['assessments']:
                assessment['baseline_label'] = 'arbitrary presentation text'
        annotate_comparisons(self.nodes, CONTROL_RUN_OWNERS)
        c5 = next(n for n in self.nodes if n['id'] == 'C5')
        self.assertEqual(c5['assessments'][0]['comparison_role'], 'parent')
        self.assertEqual(c5['assessments'][1]['comparison_role'], 'auxiliary')

    def test_unknown_control_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown baseline run ownership'):
            annotate_comparisons(self.nodes, {})

    def test_conflicting_ownership_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'conflicting run ownership'):
            annotate_comparisons(self.nodes, {'2026-09-22/02-50-59': 'B1'})


class DownstreamTest(unittest.TestCase):
    def setUp(self):
        def case(id, category='paper', status='ok'):
            return dict(id=id, input={'title':id}, gold='paper', prediction={'status':status, 'category':category})
        self.data = {
            '01': {'cases':[case('one'),case('two','opinion')]},
            '02': {'cases':[case('one','opinion'),case('two','opinion')]},
            '03': {'cases':[case('one'),case('two')]},
            '04': {'cases':[case('one',status='error')]},
        }
        self.nodes = [dict(id='A',title='A',parent=None,inspiration=[],assessments=[{'runs':['01']},{'runs':['02']}]),
                      dict(id='B',title='B',parent='A',inspiration=[],assessments=[{'runs':['03']}]),
                      dict(id='C',title='C',parent='B',inspiration=[],assessments=[{'runs':['04']}])]

    def test_representative_is_latest_not_best_and_coverage_first(self):
        self.assertEqual(representative(self.nodes[0],self.data),['02'])
        self.nodes[0]['assessments'].append({'runs':['04']})
        self.assertEqual(representative(self.nodes[0],self.data),['02'])

    def test_transitive_descendants_and_failure_denominator(self):
        self.assertEqual(descendants(self.nodes,'A'),{'B','C'})
        rows = downstream_tables(self.nodes,self.data)['A']['rows']
        self.assertEqual(rows[0]['metrics'][0]['baseline'],'0.00%')
        self.assertEqual(rows[0]['metrics'][0]['candidate'],'100.00%')
        self.assertEqual(rows[0]['metrics'][0]['delta'],'+100.00 pp')
        self.assertEqual(rows[1]['paired_n'],1)
        self.assertEqual(rows[1]['metrics'][0]['candidate'],'0.00%')
        self.assertEqual(downstream_tables(self.nodes,self.data)['C']['rows'],[])

    def test_input_or_gold_drift_is_not_silently_filtered(self):
        for field, value in [('input',{'title':'changed'}),('gold','opinion')]:
            data = deepcopy(self.data)
            data['03']['cases'][0][field] = value
            row = downstream_tables(self.nodes,data)['A']['rows'][0]
            self.assertEqual(row['status'],'unavailable')
            self.assertEqual(row['metrics'],[])

    def test_no_common_ids_and_inspiration_are_explicit(self):
        self.nodes[2]['parent'] = None
        self.nodes[2]['inspiration'] = ['B']
        self.data['04']['cases'][0]['id'] = 'other'
        row = downstream_tables(self.nodes,self.data)['A']['rows'][1]
        self.assertEqual(row['relation'],'含借鉴路径的后代')
        self.assertEqual(row['status'],'unavailable')
        self.assertEqual(row['paired_n'],0)


if __name__ == '__main__':
    unittest.main()
