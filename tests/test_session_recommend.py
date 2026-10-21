"""Candidate-free context recommendations; no browser, model or network calls."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest

from runtime_fixtures import runtime_directory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/codex-task-routing/scripts'))
import session_plan as sp


def repository_unit():
    return dict(schema_version=1,
        work=dict(purpose='別repoの回帰修正を実装・検証する', kind='repository',
                  independent=True, tightly_coupled=False, handoff_benefit=True,
                  reasons=['separate_repo_or_pr', 'independent_deliverable']),
        owner=dict(status='new'),
        execution=dict(local_required=False, chat_can_complete=False,
                       cloud=dict(available=True, materials_ready=True, tests_ready=True)),
        host=dict(subagent=True, new_session=True, codex_cloud=True,
                  chat_pro=False, materials_transfer=True))


class SessionRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.facts = repository_unit()

    def recommend(self):
        return sp.recommend_session(self.facts)

    def alternative(self, route):
        return next(item for item in self.recommend()['alternatives'] if item['route'] == route)

    def test_new_repository_work_compares_cloud_without_a_preselected_candidate(self):
        result = self.recommend()
        self.assertEqual(result['route'], 'codex_cloud')
        self.assertEqual(result['next_action'], 'prepare_cloud_handoff')
        self.assertIn('cloud_for_independent_repository_unit', result['why'])
        self.assertFalse(result['gaps'])
        self.assertIn('required_tests_and_evidence', result['handoff_required'])
        self.assertIn('durable_claim_and_submission_state', result['handoff_required'])

    def test_continuing_owner_is_reused_even_when_new_dispatch_capability_is_missing(self):
        for route in sp.ROUTES:
            with self.subTest(route=route):
                self.facts['owner'] = dict(status='continuing', owner_id='existing-owner',
                                           route=route, suitable=True)
                self.facts['host'] = {}
                self.facts['execution']['cloud'] = dict(available=False)
                result = self.recommend()
                self.assertEqual(result['route'], route)
                self.assertEqual(result['owner_id'], 'existing-owner')
                self.assertEqual(result['next_action'], 'continue_current' if route == 'continue_current'
                                 else 'reuse_existing_owner')
                self.assertFalse(result['gaps'])
                self.assertFalse(result['execution_authorized'])

    def test_unknown_owner_requires_reuse_check_before_acting_on_cloud_proposal(self):
        for owner in (dict(status='unknown'), dict(status='continuing', owner_id='current',
                      route='continue_current', suitable=None)):
            with self.subTest(owner=owner):
                self.facts['owner'] = owner
                result = self.recommend()
                self.assertEqual(result['route'], 'codex_cloud')
                self.assertEqual(result['next_action'], 'check_owner_reuse')
                self.assertIn('owner_reuse:unknown', result['gaps'])

    def test_tightly_coupled_independent_output_and_costly_handoff_stay_current(self):
        for key, value in (('tightly_coupled', True), ('handoff_benefit', False)):
            with self.subTest(key=key):
                facts = repository_unit()
                facts['work'][key] = value
                self.assertEqual(sp.recommend_session(facts)['route'], 'continue_current')

    def test_duration_context_quota_and_same_model_alone_do_not_create_a_session(self):
        for reason in ('long_running', 'long_context', 'quota_avoidance', 'same_model_transfer'):
            with self.subTest(reason=reason):
                self.facts['work']['reasons'] = [reason]
                result = self.recommend()
                self.assertEqual(result['route'], 'continue_current')
                self.assertIn('no_independent_purpose', result['why'])
        self.facts['work']['reasons'] += ['independent_deliverable']
        self.assertEqual(self.recommend()['route'], 'codex_cloud')

    def test_bounded_same_goal_support_uses_child_not_an_independent_session(self):
        self.facts['work'].update(kind='support', independent=False, reasons=[])
        self.assertEqual(self.recommend()['route'], 'subagent')
        self.facts['host']['subagent'] = False
        self.assertEqual(self.recommend()['route'], 'continue_current')

    def test_unknown_separation_facts_remain_gaps_not_permission(self):
        for key in ('independent', 'tightly_coupled', 'handoff_benefit'):
            with self.subTest(key=key):
                facts = repository_unit()
                facts['work'][key] = None
                result = sp.recommend_session(facts)
                self.assertEqual(result['route'], 'codex_cloud')
                self.assertIn('work.' + key + ':unknown', result['gaps'])
                self.assertEqual(result['next_action'], 'assess_work_unit')
                self.assertFalse(result['execution_authorized'])

    def test_unknown_cloud_environment_and_permissions_preserve_conditional_proposal(self):
        self.facts['execution']['cloud'] = {}
        self.facts['host'] = {}
        result = self.recommend()
        self.assertEqual(result['route'], 'codex_cloud')
        self.assertEqual(result['next_action'], 'resolve_candidate_gaps')
        self.assertEqual(set(result['gaps']), {'cloud.available:unknown', 'cloud.materials_ready:unknown',
            'cloud.tests_ready:unknown', 'host.codex_cloud:unknown', 'host.materials_transfer:unknown'})
        self.assertTrue(result['proposal']['conditional'])
        self.assertFalse(result['execution_authorized'])

    def test_unavailable_cloud_is_reported_when_local_is_recommended(self):
        self.facts['execution']['cloud']['available'] = False
        result = self.recommend()
        self.assertEqual(result['route'], 'new_local_session')
        cloud = self.alternative('codex_cloud')
        self.assertEqual(cloud['suitability'], 'unavailable')
        self.assertIn('cloud.available:false', cloud['why'])
        self.assertFalse(result['execution_authorized'])

    def test_missing_materials_tests_and_transfer_permission_do_not_silently_pass(self):
        for field in ('materials_ready', 'tests_ready'):
            with self.subTest(field=field):
                self.facts = repository_unit()
                self.facts['execution']['cloud'][field] = False
                self.assertEqual(self.recommend()['route'], 'new_local_session')
                self.assertIn('cloud.' + field + ':false', self.alternative('codex_cloud')['why'])
        for field in ('codex_cloud', 'materials_transfer'):
            with self.subTest(field=field):
                self.facts = repository_unit()
                self.facts['host'][field] = False
                self.assertEqual(self.recommend()['route'], 'new_local_session')
                self.assertEqual(self.alternative('codex_cloud')['suitability'], 'blocked')

    def test_local_dependencies_select_local_and_unknown_dependence_needs_assessment(self):
        self.facts['execution']['local_required'] = True
        self.assertEqual(self.recommend()['route'], 'new_local_session')
        self.assertIn('local_execution_required', self.alternative('codex_cloud')['why'])
        self.facts['execution']['local_required'] = None
        self.assertIn('execution.local_required:unknown', self.recommend()['gaps'])
        self.assertEqual(self.recommend()['next_action'], 'assess_work_unit')

    def test_pro_policy_is_preserved_for_complete_analysis_and_capable_repo_work(self):
        for kind in ('analysis', 'repository'):
            with self.subTest(kind=kind):
                self.facts['work']['kind'] = kind
                self.facts['execution']['chat_can_complete'] = True
                self.facts['host']['chat_pro'] = True
                result = self.recommend()
                self.assertEqual(result['route'], 'chat_pro')
                self.assertEqual(result['next_action'], 'evaluate_existing_chat_policy')
                self.assertFalse(result['execution_authorized'])
        self.facts['host']['chat_pro'] = False
        self.assertEqual(self.recommend()['route'], 'codex_cloud')

    def test_unknown_chat_capability_does_not_hide_a_suitable_repository_cloud_unit(self):
        self.facts['execution']['chat_can_complete'] = None
        self.facts['host']['chat_pro'] = True
        self.assertEqual(self.recommend()['route'], 'codex_cloud')
        self.assertIn('execution.chat_can_complete:unknown', self.alternative('chat_pro')['gaps'])

    def test_explicit_host_rejection_cannot_be_bypassed_with_another_session(self):
        self.facts['host'].update(codex_cloud=False, new_session=False, chat_pro=False)
        result = self.recommend()
        self.assertEqual(result['route'], 'continue_current')
        self.assertEqual(result['next_action'], 'report_context_limitations')

    def test_recommendation_never_mutates_input_or_sets_model_effort_execution(self):
        before = copy.deepcopy(self.facts)
        result = self.recommend()
        self.assertEqual(self.facts, before)
        self.assertTrue(result['proposal_only'])
        self.assertFalse(result['execution_authorized'])
        self.assertFalse({'model', 'effort', 'thinking', 'task_id'} & set(result))

    def test_schema_rejects_coercion_candidate_and_owner_status_ambiguity(self):
        invalid = []
        for path, value in [(('schema_version',), True), (('candidate',), 'codex_cloud'),
            (('work', 'independent'), 1), (('work', 'reasons'), ['long_running'] * 2),
            (('execution', 'local_required'), 'false'), (('host', 'codex_cloud'), 'true'),
            (('owner',), dict(status='new', suitable=True)),
            (('owner',), dict(status='continuing', owner_id='a', route='codex_cloud'))]:
            facts = repository_unit()
            target = facts
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            invalid.append(facts)
        for facts in invalid:
            with self.subTest(facts=facts), self.assertRaises(sp.SessionPlanError):
                sp.recommend_session(facts)

    def test_cli_exposes_recommend_separately_and_redacts_invalid_input(self):
        with runtime_directory() as root:
            path = root / 'facts.json'
            path.write_text(json.dumps(self.facts), encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = sp.main(['recommend', '--facts', str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())['route'], 'codex_cloud')
            path.write_text('{"synthetic-private-material": true}', encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = sp.main(['recommend', '--facts', str(path)])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(err.getvalue()), {'error': 'invalid_session_plan_input'})
            self.assertNotIn('synthetic-private-material', err.getvalue())


if __name__ == '__main__':
    unittest.main()
