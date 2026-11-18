"""Offline regressions: non-Web plans must not bypass existing Ember gates."""
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/codex-task-routing'
REFERENCES = PLUGIN / 'skills/task-routing/references'
sys.path.insert(0, str(PLUGIN / 'scripts'))
import muse_pilot_plan as pilot
import muse_web_research as web


class EmberRouteBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.prompt = '合成資料: りんごが二個、みかんが三個。品目を抽出する。'
        self.digest = hashlib.sha256(self.prompt.encode('utf-8')).hexdigest()
        self.web_facts = dict(
            schema_version=1, actor='root', explicit_opt_in=True,
            host_authorized=True, target_authorized=True,
            materials_authorized=True, cost_authorized=True,
            data_class='public', purpose='public_web_research',
            prompt=self.prompt, prompt_sha256=self.digest,
            status={'state': 'not_started', 'inputSha256': self.digest},
        )
        self.pilot_status = {key: None for key in pilot.RESULT_FIELDS}
        self.pilot_status['state'] = 'not_started'
        self.pilot_facts = {
            key: value for key, value in self.web_facts.items()
            if key not in ('cost_authorized', 'purpose')
        }
        self.pilot_facts.update(
            data_class='public_or_synthetic', status=self.pilot_status,
        )

    def test_non_web_purposes_do_not_get_web_arguments(self):
        for purpose in ('summary', 'extract', 'compare', 'format'):
            with self.subTest(purpose=purpose):
                result = web.prepare({**self.web_facts, 'purpose': purpose})
                self.assertFalse(result['ready'])
                self.assertIs(result['sent'], False)
                self.assertNotIn('arguments', result)
                self.assertNotIn('tool', result)

    def test_web_scope_stays_public_only(self):
        for data_class in ('private', 'synthetic', 'public_or_synthetic'):
            with self.subTest(data_class=data_class):
                result = web.prepare({**self.web_facts, 'data_class': data_class})
                self.assertFalse(result['ready'])
                self.assertNotIn('arguments', result)

    def test_text_only_result_does_not_satisfy_web_verification(self):
        result = dict(
            state='completed', inputSha256=self.digest,
            model='muse-ember-1.0', effort='max',
            inputTokens=1, outputTokens=1,
            output=[{'type': 'output_text', 'text': 'りんご、みかん',
                     'annotations': []}],
        )
        with self.assertRaises(ValueError):
            web.verify(dict(schema_version=1, prompt_sha256=self.digest,
                            result=result))

    def test_spent_pilot_is_not_reopened_by_a_different_input(self):
        previous_digest = hashlib.sha256(b'previous synthetic pilot').hexdigest()
        self.assertNotEqual(previous_digest, self.digest)
        for state in ('intent', 'unverified', 'completed'):
            with self.subTest(state=state):
                status = {**self.pilot_status, 'state': state,
                          'inputSha256': previous_digest}
                result = pilot.prepare({**self.pilot_facts, 'status': status})
                self.assertFalse(result['ready'])
                self.assertNotIn('arguments', result)

    def test_pilot_and_web_status_are_not_interchangeable(self):
        result = web.prepare({**self.web_facts, 'status': self.pilot_status})
        self.assertFalse(result['ready'])
        self.assertEqual(result['next_action'], 'check_request_status')
        result = pilot.prepare({**self.pilot_facts,
                                'status': self.web_facts['status']})
        self.assertFalse(result['ready'])
        self.assertNotIn('arguments', result)

    def test_changing_purpose_does_not_turn_uncertain_send_into_fallback(self):
        for state in ('intent', 'unverified'):
            with self.subTest(state=state):
                result = web.prepare({
                    **self.web_facts, 'purpose': 'summary',
                    'status': {'state': state, 'inputSha256': self.digest},
                })
                self.assertFalse(result['ready'])
                self.assertEqual(result['sent'], 'unknown')
                self.assertEqual(result['next_action'], 'reconcile_existing_request')
                self.assertNotIn('arguments', result)

    def test_current_guidance_does_not_restore_routine_terra(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        guide = (REFERENCES / 'operating-guide.md').read_text(encoding='utf-8')
        self.assertNotIn('通常実装はTerra', readme)
        self.assertNotIn('evaluate a complete Terra unit', guide)
        self.assertIn('通常実装はSol/low', readme)
        self.assertIn('complete routine Sol unit', guide)

    def test_non_web_contract_is_linked_and_explicitly_not_operational(self):
        contract = REFERENCES / 'muse-text-tasks.md'
        self.assertTrue(contract.is_file())
        content = contract.read_text(encoding='utf-8')
        self.assertIn('常用の実行経路未提供', content)
        self.assertIn('実装・稼働は未確認', content)
        for name in ('Liquid', 'LFM3-VL', 'Pro', 'Cloud'):
            self.assertIn(name, content)
        for path in (ROOT / 'README.md', PLUGIN / 'skills/task-routing/SKILL.md',
                     REFERENCES / 'muse-web-research.md',
                     REFERENCES / 'muse-pilot.md', REFERENCES / 'operating-guide.md'):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertIn('muse-text-tasks.md', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
