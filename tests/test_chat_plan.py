import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import chat_plan as cp


class ChatPlanTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 14, tzinfo=timezone.utc)
        self.need = {'tool': 'GitHub', 'operation': 'fetch_file', 'scope': 'example/project'}
        self.facts = dict(substantial=True, parent_has_independent_work=True,
                          materials_approved=True, handoff_proportionate=True,
                          requires_repeated_local_access=False, needs=[self.need])
        self.inventory = dict(schema_version=1, surface='chat-temporary', model='6 Pro',
                              installed_plugins=['GitHub'], capabilities=[dict(
                                  **self.need, state='verified', observed_at='2026-09-13T00:00:00Z',
                                  expires_at='2026-09-15T00:00:00Z')])

    def run_plan(self):
        return cp.plan(self.facts, self.inventory, now=self.now)

    def test_matching_verified_capability_is_only_a_candidate(self):
        self.assertEqual(self.run_plan()['route'], 'chat_candidate')

    def test_unknown_advertised_and_expired_require_preflight(self):
        for state in ('advertised', 'verified'):
            self.inventory['capabilities'][0]['state'] = state
            if state == 'verified':
                self.inventory['capabilities'][0]['expires_at'] = self.now.isoformat()
            self.assertEqual(self.run_plan()['route'], 'preflight_needed')
        self.inventory['capabilities'] = []
        self.assertEqual(self.run_plan()['checks'][0]['state'], 'unknown')

    def test_scope_and_operation_not_generalized(self):
        for key in ('scope', 'operation'):
            changed = copy.deepcopy(self.facts)
            changed['needs'][0][key] = 'different'
            self.assertEqual(cp.plan(changed, self.inventory, now=self.now)['route'], 'preflight_needed')

    def test_no_required_tools_needs_no_inventory(self):
        self.facts['needs'] = []
        self.assertEqual(cp.plan(self.facts)['route'], 'chat_candidate')

    def test_gates_and_blocked(self):
        for key in ('substantial', 'parent_has_independent_work', 'handoff_proportionate'):
            changed = {**self.facts, key: False}
            self.assertEqual(cp.plan(changed)['route'], 'codex')
        self.facts['requires_repeated_local_access'] = True
        self.assertEqual(self.run_plan()['route'], 'codex')
        self.facts['requires_repeated_local_access'] = False
        self.facts['materials_approved'] = False
        self.assertEqual(self.run_plan()['route'], 'not_ready')
        self.facts['materials_approved'] = True
        self.inventory['capabilities'][0]['state'] = 'blocked'
        self.assertEqual(self.run_plan()['route'], 'not_ready')

    def test_malformed_inputs_fail_closed(self):
        for update in ({'model': 'Work'}, {'schema_version': True}, {'unknown': 1}):
            with self.assertRaises(cp.ChatRouteError):
                cp.validate_inventory({**self.inventory, **update})
        for key in ('state', 'observed_at', 'expires_at'):
            broken = copy.deepcopy(self.inventory)
            broken['capabilities'][0][key] = 'invalid'
            with self.assertRaises(cp.ChatRouteError):
                cp.validate_inventory(broken)
        with self.assertRaises(cp.ChatRouteError):
            cp.plan({**self.facts, 'substantial': 'true'})

    def test_future_observation_and_long_lifetime(self):
        self.inventory['capabilities'][0].update(observed_at='2026-09-15T00:00:00Z', expires_at='2026-09-16T00:00:00Z')
        self.assertEqual(self.run_plan()['checks'][0]['state'], 'stale')
        self.inventory['capabilities'][0]['expires_at'] = '2026-10-01T00:00:00Z'
        with self.assertRaises(cp.ChatRouteError):
            self.run_plan()


if __name__ == '__main__':
    unittest.main()
