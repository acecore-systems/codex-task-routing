from __future__ import annotations
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from test_observation import completed_child_run, missing_completion, partial_completion

SCRIPT = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts/compare_observations.py'
spec = importlib.util.spec_from_file_location('compare_observations', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def evidence(value):
    return {'value': value, 'evidence_ref': 'evidence', 'reason': None}

def flag():
    return evidence(True)

def side(reference, parent_input, child_input):
    parent, child = completed_child_run(), completed_child_run()
    parent.update(run_key='parent', relationship='parent', run_id=reference + '-parent')
    child['run_id'] = reference + '-child'
    parent['observed'] = {'model': 'gpt-6-astra', 'effort': 'high', 'evidence_ref': 'parent-settings', 'reason': None}
    for run, value in ((parent, parent_input), (child, child_input)):
        run['usage'].update(turn_scope='full_turn', input_tokens=value, cache_input_tokens=0, output_tokens=0, reasoning_tokens=0)
    return {'completion': {'state': 'completed', 'scope': 'complete work', 'measurement': {'state': 'measured', 'reason': None},
            'verification': {'reference': 'tests', 'reason': None}, 'rework': 'none', 'runs': [parent, child]},
            'completion_ref': reference,
            'conditions': {key: evidence(value) for key, value in {'work':'same task', 'acceptance':'same checks', 'environment':'same environment', 'parent_model':'gpt-6-astra', 'parent_effort':'high'}.items()},
            'coverage': flag(), 'independent': flag(), 'quality': flag(), 'chat_messages': {'value': None, 'evidence_ref': None, 'reason': 'unavailable'}}

def inputs():
    return {'baseline': side('before', 100, 30), 'candidate': side('after', 60, 100), 'independent_comparisons': flag()}

class ComparisonTests(unittest.TestCase):
    def test_parent_reduction_is_not_total_reduction(self):
        result = module.compare(inputs())
        self.assertEqual(result['parent_tokens']['reduction_percent'], 40)
        self.assertEqual(result['total_tokens']['reduction_percent'], -30 / 130 * 100)
        self.assertIsNone(result['candidate_chat_messages']['value'])
        self.assertEqual(result['candidate_rework'], 'none')

    def test_missing_and_conflicting_evidence_never_produces_a_percentage(self):
        for edit in ('partial', 'overlap', 'coverage', 'quality', 'condition', 'unknown', 'same_ref', 'parent_setting', 'cross_overlap', 'duplicate_run', 'duplicate_within', 'partial_measurement'):
            data = inputs()
            with self.subTest(edit=edit):
                if edit == 'partial': data['candidate']['completion'] = missing_completion()
                elif edit in ('overlap', 'coverage', 'quality'):
                    key = {'overlap':'independent', 'coverage':'coverage', 'quality':'quality'}[edit]
                    data['candidate'][key] = {'value': None, 'evidence_ref': None, 'reason': 'unknown'}
                elif edit == 'condition': data['candidate']['conditions']['acceptance'] = evidence('different')
                elif edit == 'unknown': data['candidate']['conditions']['environment'] = {'value': None, 'evidence_ref': None, 'reason': 'unknown'}
                elif edit == 'same_ref': data['candidate']['completion_ref'] = 'before'
                elif edit == 'cross_overlap': data['independent_comparisons'] = {'value': None, 'evidence_ref': None, 'reason': 'unknown'}
                elif edit == 'duplicate_run': data['candidate']['completion']['runs'][0]['run_id'] = 'before-parent'
                elif edit == 'duplicate_within': data['candidate']['completion']['runs'][1]['run_id'] = 'after-parent'
                elif edit == 'partial_measurement': data['candidate']['completion'] = partial_completion()
                else: data['candidate']['completion']['runs'][0]['observed'].update(model=None, effort=None, evidence_ref=None, reason='unknown')
                result = module.compare(data)
                for key in ('parent_tokens', 'total_tokens'):
                    self.assertIsNone(result[key]['reduction_percent'])
                    self.assertTrue(result[key]['reason'])

    def test_cache_and_reasoning_are_not_added_again(self):
        data = inputs()
        usage = data['baseline']['completion']['runs'][0]['usage']
        usage.update(input_tokens=100, cache_input_tokens=40, output_tokens=50, reasoning_tokens=20)
        result = module.compare(data)
        self.assertEqual(result['parent_tokens']['baseline'], 150)
        self.assertEqual(result['total_tokens']['baseline'], 180)

    def test_zero_baseline_does_not_divide(self):
        data = inputs()
        data['baseline'] = side('before', 0, 0)
        result = module.compare(data)
        self.assertIsNone(result['total_tokens']['reduction_percent'])
        self.assertEqual(result['total_tokens']['reason'], 'zero baseline')

    def test_partial_turn_is_not_measured_and_bad_subset_is_rejected(self):
        data = inputs()
        data['candidate']['completion']['runs'][0]['usage']['turn_scope'] = 'partial_turn'
        with self.assertRaises(module.RoutingError): module.compare(data)
        data = inputs()
        data['candidate']['completion']['runs'][0]['usage']['cache_input_tokens'] = 61
        with self.assertRaises(module.RoutingError): module.compare(data)

    def test_cli_reads_only_and_handles_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'input.json'
            text = json.dumps(inputs())
            path.write_text(text, encoding='utf-8')
            proc = subprocess.run([sys.executable, str(SCRIPT), '--input', str(path)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(path.read_text(encoding='utf-8'), text)
            self.assertEqual(json.loads(proc.stdout)['parent_tokens']['reduction_percent'], 40)
            path.write_text('{}', encoding='utf-8')
            proc = subprocess.run([sys.executable, str(SCRIPT), '--input', str(path)], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(proc.returncode, 0)

if __name__ == '__main__': unittest.main()
