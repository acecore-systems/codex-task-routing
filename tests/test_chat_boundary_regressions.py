"""Regressions for untrusted JSON and capability timestamp boundaries."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from runtime_fixtures import runtime_directory
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/codex-task-routing/scripts'
sys.path.insert(0, str(SCRIPTS))
import chat_plan
import chat_transfer
import chatgpt_route


class ChatBoundaryRegressions(unittest.TestCase):
    def setUp(self):
        self.request = dict(task='Review supplied text.', materials='Fixture only.',
                            acceptance_criteria=['Return evidence.'])
        self.bundle = chatgpt_route.prepare_payload(self.request)
        self.response = dict(request_id=self.bundle['request_id'],
                             input_sha256=self.bundle['input_sha256'],
                             status='completed', result='Valid result.', evidence=[])

    def test_lone_surrogates_rejected_before_preparing_or_saving(self):
        for surrogate in ('\ud800', '\udfff'):
            for field in ('task', 'materials', 'acceptance_criteria'):
                invalid = copy.deepcopy(self.request)
                invalid[field] = [surrogate] if field == 'acceptance_criteria' else surrogate
                with self.subTest(surrogate=repr(surrogate), field=field):
                    with self.assertRaisesRegex(chatgpt_route.ChatRouteError, 'valid Unicode'):
                        chatgpt_route.prepare_payload(invalid)
            for field in ('result', 'evidence'):
                invalid = dict(self.response)
                invalid[field] = [surrogate] if field == 'evidence' else surrogate
                with self.subTest(surrogate=repr(surrogate), field=field):
                    with runtime_directory() as directory:
                        reply = Path(directory) / 'reply.json'
                        state = chat_transfer.TransferState(bundle=self.bundle, reply_path=reply)
                        with self.assertRaisesRegex(chat_transfer.ChatTransferError, 'valid Unicode'):
                            state.save_response_text(json.dumps(invalid))
                        self.assertFalse(reply.exists())
                        self.assertIsNone(state.response_status)
                        # The same request can still accept a corrected response.
                        self.assertTrue(state.save_response(self.response)[1])

    def test_valid_supplementary_unicode_roundtrips(self):
        response = dict(self.response, result='Supplementary: \U0001f680')
        parsed = chatgpt_route._parse_json_object(json.dumps(response), purpose='response')
        self.assertEqual(chatgpt_route.validate_exchange(self.bundle, parsed)['status'], 'completed')
        self.assertEqual(json.loads(chat_transfer._canonical_response_bytes(parsed)), response)

    def test_inventory_upper_date_boundary_has_no_overflow(self):
        inventory = dict(schema_version=1, surface='chat-temporary', model='6 Pro',
                         installed_plugins=['Example'], capabilities=[dict(
                             tool='Example', operation='read', scope='fixture', state='verified',
                             observed_at='9999-12-30T00:00:00Z', expires_at='9999-12-31T00:00:00Z')])
        self.assertEqual(chat_plan.validate_inventory(inventory), inventory)
        for timestamp in ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00'):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(chatgpt_route.ChatRouteError):
                    chat_plan._time(timestamp)


if __name__ == '__main__':
    unittest.main()
