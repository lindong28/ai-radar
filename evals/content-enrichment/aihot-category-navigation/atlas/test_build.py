"""Run with unittest discover -s <atlas directory>; no model or archive access."""
from copy import deepcopy
import unittest

from build import annotate_comparisons
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


if __name__ == '__main__':
    unittest.main()
