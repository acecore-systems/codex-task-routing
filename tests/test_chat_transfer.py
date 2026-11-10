from __future__ import annotations

import importlib.util
import html
import io
import json
import os
import shutil
import socket
import sys
import unittest
import uuid
import threading
import time
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
ROUTE_SCRIPT = ROOT / "plugins" / "codex-task-routing" / "scripts" / "chatgpt_route.py"
TRANSFER_SCRIPT = ROOT / "plugins" / "codex-task-routing" / "scripts" / "chat_transfer.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


chatgpt_route = load_module("chatgpt_route_for_transfer_test", ROUTE_SCRIPT)
chat_transfer = load_module("chat_transfer_under_test", TRANSFER_SCRIPT)


class ChatTransferTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # Protocol/deadline unit tests must also run in socket-disabled clouds.
        # Real loopback/Browser availability is a separate integration check.
        self.enterContext(patch.object(socket, "socket", side_effect=PermissionError("socket disabled")))
        self.enterContext(patch.object(socket, "socketpair", side_effect=PermissionError("socket disabled")))
        fixture_root = Path(__file__).resolve().parent / ".tmp-runtime-tests"
        fixture_root.mkdir(exist_ok=True)
        self.base = fixture_root / f"chat-transfer-{uuid.uuid4().hex}"
        self.base.mkdir()
        self.bundle_path = self.base / "bundle.json"
        self.reply_path = self.base / "reply.json"
        request = {
            "request_id": str(uuid.uuid4()),
            "task": "Assess the supplied bounded evidence.",
            "materials": "Supplied content only.",
            "acceptance_criteria": ["Return a concise finding."],
        }
        self.bundle = chatgpt_route.prepare_payload(request)
        self.bundle_path.write_text(json.dumps(self.bundle), encoding="utf-8")
        self.state = chat_transfer.TransferState.from_paths(self.bundle_path, self.reply_path)
        self.state.token = "test-capability-token"

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def response(self, *, result: str = "bounded finding", request_id: str | None = None) -> dict[str, object]:
        return {
            "request_id": request_id or self.bundle["request_id"],
            "input_sha256": self.bundle["input_sha256"],
            "status": "completed",
            "result": result,
            "evidence": ["supplied evidence"],
        }

    def test_page_has_stable_dom_fields_without_submitting_the_prompt(self) -> None:
        document = chat_transfer._page(
            self.state, status=chat_transfer._response_status_message(None)
        ).decode("utf-8")
        self.assertIn('<label for="request_prompt">Request prompt</label>', document)
        self.assertIn('id="request_prompt" readonly', document)
        self.assertNotIn('name="request_prompt"', document)
        self.assertIn('<label for="response_json">Response JSON</label>', document)
        self.assertIn('Save verified response', document)
        self.assertIn('Transfer status', document)
        self.assertIn('name="referrer" content="same-origin"', document)
        self.assertIn('&lt;canonical UUID&gt;', document)
        self.assertIn("default-src 'none'", chat_transfer.CONTENT_SECURITY_POLICY)
        self.assertIn("form-action 'self'", chat_transfer.CONTENT_SECURITY_POLICY)

    def test_workflow_preflight_and_save_over_http(self) -> None:
        facts = dict(substantial=True, parent_has_independent_work=True, materials_approved=True,
                     handoff_proportionate=True, requires_repeated_local_access=False, needs=[])
        base = dict(is_root=True, route_enabled=True, transport="browser-temporary",
                    actions_authorized=True, browser_checks={op: "permission_checked" for op in chat_transfer.chat_plan.BROWSER_OPERATIONS})
        workflow = {"facts": facts, "live_base": base}
        state = chat_transfer.TransferState(bundle=self.bundle, reply_path=self.reply_path, workflow=workflow)
        state.token = "test-capability-token"
        server = SimpleNamespace(state=state, server_port=32123, completed=False)
        observation = dict(surface="chat-temporary", model="Pro", quota_state="no_limit_notice",
                           observed_at=datetime.now(timezone.utc).isoformat())

        def post(fields):
            body = urlencode(fields).encode("utf-8")
            frame = (f"POST /transfer/{state.token} HTTP/1.1\r\nHost: 127.0.0.1:32123\r\n"
                     f"Origin: http://127.0.0.1:32123\r\nContent-Type: application/x-www-form-urlencoded\r\n"
                     f"Content-Length: {len(body)}\r\n\r\n").encode("ascii") + body
            received = bytearray()
            connection = SimpleNamespace(makefile=lambda *_: io.BytesIO(frame), sendall=received.extend)
            chat_transfer.TransferHandler(connection, ("127.0.0.1", 0), server)
            header, _, page = bytes(received).partition(b"\r\n\r\n")
            return int(header.split()[1]), page.decode("utf-8")

        code, page = post({"response_json": json.dumps(self.response())})
        self.assertEqual(code, 422)
        self.assertFalse(self.reply_path.exists())
        self.assertNotIn('id="saved_result"', page)
        code, _ = post({"phase": "dispatch", "live_observation": json.dumps({**observation, "is_root": True})})
        self.assertEqual(code, 422)
        code, page = post({"phase": "dispatch", "live_observation": json.dumps(observation)})
        self.assertEqual(code, 200)
        self.assertIn('id="preflight_result" readonly', page)
        self.assertEqual(state.preflight_result["plan"]["next_action"], "send_once")
        self.assertEqual(state.preflight_result["input_sha256"], self.bundle["input_sha256"])
        claim = self.base / f'.chat-dispatch-{self.bundle["request_id"]}.json'
        self.assertEqual(json.loads(claim.read_text(encoding="utf-8")), {
            "request_id": self.bundle["request_id"], "input_sha256": self.bundle["input_sha256"]})
        second = chat_transfer.TransferState(bundle=self.bundle, reply_path=self.reply_path, workflow=workflow)
        retry = second.check_preflight("dispatch", json.dumps(observation))
        self.assertEqual(retry["plan"]["reason"], "dispatch_already_reserved")
        self.assertNotEqual(retry["plan"]["next_action"], "send_once")
        receive_base = {**base, "browser_checks": {**base["browser_checks"], "send": "blocked"}}
        recovery = chat_transfer.TransferState(bundle=self.bundle, reply_path=self.reply_path,
                                               workflow={"facts": facts, "live_base": receive_base})
        self.assertEqual(recovery.check_preflight("receive", json.dumps(observation))["plan"]["next_action"], "collect_reply")
        self.assertEqual(post({"phase": "dispatch", "live_observation": json.dumps(observation)})[0], 422)
        code, _ = post({"phase": "receive", "live_observation": json.dumps({**observation, "model": "other"})})
        self.assertEqual(code, 200)
        self.assertFalse(state.receive_ready)
        self.assertEqual(post({"response_json": json.dumps(self.response())})[0], 422)
        code, _ = post({"phase": "receive", "live_observation": json.dumps(observation)})
        self.assertEqual(code, 200)
        self.assertTrue(state.receive_ready)
        code, page = post({"response_json": json.dumps(self.response())})
        self.assertEqual(code, 200)
        self.assertIn('id="saved_result" readonly', page)
        saved_text = page.split('id="saved_result" readonly aria-readonly="true">', 1)[1].split('</textarea>', 1)[0]
        self.assertEqual(json.loads(html.unescape(saved_text)), chat_transfer._saved_metadata(state))
        self.assertEqual(json.loads(self.reply_path.read_text(encoding="utf-8")), self.response())
        self.assertTrue(server.completed)
        # A reply saved by the legacy helper may exist without a reservation.
        claim.unlink()
        completed = chat_transfer.TransferState(bundle=self.bundle, reply_path=self.reply_path, workflow=workflow)
        duplicate = completed.check_preflight("dispatch", json.dumps(observation))
        self.assertEqual(duplicate["plan"]["reason"], "reply_already_present")
        self.assertFalse(claim.exists())

    def test_workflow_listening_metadata_has_only_tool_presence(self) -> None:
        workflow = {"facts": {"needs": [{"tool": "private-name"}]},
                    "live_base": {"is_root": True, "route_enabled": True, "transport": "browser-temporary",
                                  "browser_checks": {"inspect": "permission_checked"}}}
        state = chat_transfer.TransferState(bundle=self.bundle, reply_path=self.reply_path, workflow=workflow)
        state.token = "test-capability-token"
        server = SimpleNamespace(origin="http://127.0.0.1:32123", completed=False,
                                 handle_request=Mock(), server_close=Mock())
        with (patch.object(chat_transfer, "create_server", return_value=server),
              patch.object(chat_transfer.time, "monotonic", side_effect=[0.0, 1.01]),
              redirect_stdout(io.StringIO()) as stdout):
            self.assertEqual(chat_transfer.serve_transfer(state, timeout_seconds=1), 3)
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertIs(events[0]["workflow"], True)
        self.assertIs(events[0]["needs_tools"], True)
        self.assertNotIn("private-name", stdout.getvalue())
        self.assertNotIn(self.bundle["prompt"], stdout.getvalue())

    def test_workflow_requires_inspection_before_server_start(self) -> None:
        for checks in ({}, {"inspect": "blocked"}, {"inspect": "unknown"}):
            with self.subTest(checks=checks), self.assertRaisesRegex(chat_transfer.ChatTransferError, "inspection"):
                chat_transfer.TransferState(bundle=self.bundle, reply_path=self.reply_path,
                    workflow={"facts": {"needs": []}, "live_base": {
                        "is_root": True, "route_enabled": True, "transport": "browser-temporary",
                        "browser_checks": checks}})

    def test_unauthenticated_paths_do_not_receive_prompt_or_token(self) -> None:
        error_document = chat_transfer._minimal_error_page().decode("utf-8")
        self.assertNotIn(self.bundle["prompt"], error_document)
        self.assertNotIn(self.state.token, error_document)
        self.assertNotIn("Response JSON", error_document)
        self.assertFalse(chat_transfer._path_is_valid("/transfer/not-the-token", self.state.token))
        self.assertFalse(chat_transfer._path_is_valid(f"/transfer/{self.state.token}?x=1", self.state.token))

    def test_wrong_host_and_origin_are_rejected(self) -> None:
        port = 32123
        self.assertFalse(chat_transfer._host_is_valid("localhost:32123", port))
        self.assertFalse(chat_transfer._origin_is_valid("http://127.0.0.1:1", port))
        self.assertTrue(chat_transfer._host_is_valid("127.0.0.1:32123", port))
        self.assertTrue(chat_transfer._origin_is_valid("http://127.0.0.1:32123", port))

    def test_oversize_post_is_rejected_without_writing_a_reply(self) -> None:
        self.assertGreater(chat_transfer.MAX_POST_BYTES, chatgpt_route.MAX_JSON_BYTES)
        with self.assertRaisesRegex(chat_transfer.ChatTransferError, "size limit"):
            chat_transfer._parse_post_length(str(chat_transfer.MAX_POST_BYTES + 1))
        body = urlencode({"response_json": "x" * (chatgpt_route.MAX_JSON_BYTES + 1)}).encode("utf-8")
        # The request length is checked before this parser in the handler; this
        # checks the decoded response size independently.
        with self.assertRaisesRegex(chat_transfer.ChatTransferError, "size limit"):
            chat_transfer._response_text_from_form(body)
        self.assertFalse(self.reply_path.exists())

    def test_wrong_request_id_is_not_saved(self) -> None:
        with self.assertRaisesRegex(chat_transfer.ChatTransferError, "request_id"):
            self.state.save_response(self.response(request_id=str(uuid.uuid4())))
        self.assertFalse(self.reply_path.exists())

    def test_json_parse_status_reports_only_location_metadata(self) -> None:
        invalid = '{"request_id": }'
        with self.assertRaises(chat_transfer.ChatTransferError):
            self.state.save_response_text(invalid)
        status = self.state.transfer_status
        self.assertIn("syntax line=1", status)
        self.assertIn("column=16", status)
        self.assertIn("character=U+007D", status)
        self.assertIn("decoded_chars=16", status)
        self.assertNotIn(invalid, status)

    def test_http_boundary_and_verified_save(self) -> None:
        # Feed wire bytes to the real HTTP parser and handler in memory.
        # This keeps local HTTP filters and OS socket EOF behavior out of the
        # protocol assertions; Browser transport is verified separately.
        server = SimpleNamespace(state=self.state, server_port=32123, completed=False)
        path = f'/transfer/{self.state.token}'

        def request(method, target, body=b'', headers=None, *, close=True):
            request_headers = {'Host': f'127.0.0.1:{server.server_port}'}
            request_headers.update(headers or {})
            if close:
                request_headers['Connection'] = 'close'
            if method == 'POST':
                request_headers.setdefault('Content-Length', str(len(body)))
            frame = [f'{method} {target} HTTP/1.1']
            frame.extend(f'{name}: {value}' for name, value in request_headers.items())
            raw_request = ('\r\n'.join(frame) + '\r\n\r\n').encode('ascii') + body
            received = bytearray()
            connection = SimpleNamespace(
                makefile=lambda *_args: io.BytesIO(raw_request),
                sendall=received.extend,
            )
            chat_transfer.TransferHandler(connection, ('127.0.0.1', 0), server)
            head, separator, body_bytes = bytes(received).partition(b'\r\n\r\n')
            self.assertTrue(separator, 'server must return a complete HTTP response')
            lengths = [line.split(b':', 1)[1].strip() for line in head.split(b'\r\n')
                       if line.lower().startswith(b'content-length:')]
            self.assertEqual(len(lengths), 1)
            self.assertEqual(len(body_bytes), int(lengths[0]))
            status_line = head.split(b'\r\n', 1)[0].decode('ascii')
            self.assertRegex(status_line, r'^HTTP/1\.0 [1-5]\d\d ')
            self.assertIn(b'\r\nConnection: close\r\n', b'\r\n' + head + b'\r\n')
            self.assertIn(b'\r\nReferrer-Policy: same-origin\r\n', b'\r\n' + head + b'\r\n')
            return int(status_line.split()[1]), body_bytes.decode('utf-8')

        for method, target, headers in [
            ('GET', '/wrong-token', {}),
            ('GET', path, {'Host': 'attacker.invalid'}),
            ('POST', path, {'Origin': 'https://attacker.invalid'}),
        ]:
            with self.subTest(method=method, headers=headers):
                status, body = request(method, target, headers=headers)
                self.assertGreaterEqual(status, 400)
                self.assertNotIn('Supplied content only.', body)
                self.assertNotIn(self.state.token, body)
        headers = {'Origin': 'http://127.0.0.1:32123', 'Content-Type': 'application/x-www-form-urlencoded'}
        invalid = self.response(request_id=str(uuid.uuid4()))
        status, _ = request('POST', path, urlencode({'response_json': json.dumps(invalid)}).encode('utf-8'), headers)
        self.assertEqual(status, 422)
        self.assertFalse(self.reply_path.exists())
        # The Browser form percent-encodes UTF-8; keep this near the live
        # response size while exercising a request without Connection: close.
        valid = self.response(result="確認" * 1700)
        status, body = request(
            'POST',
            path,
            urlencode({'response_json': json.dumps(valid, ensure_ascii=False)}).encode('utf-8'),
            headers,
            close=False,
        )
        self.assertEqual(status, 200)
        self.assertIn('Parent semantic acceptance is still required', body)
        self.assertEqual(json.loads(self.reply_path.read_text(encoding='utf-8')), valid)

    def test_form_rejects_invalid_utf8_and_duplicate_field(self) -> None:
        for body in (b'response_json=%FF', b'response_json=a&response_json=b'):
            with self.assertRaises(chat_transfer.ChatTransferError):
                chat_transfer._response_text_from_form(body)

    def test_same_response_retry_is_idempotent(self) -> None:
        response = self.response()
        validated, created = self.state.save_response(response)
        self.assertTrue(created)
        self.assertEqual(validated["status"], "completed")
        first = self.reply_path.read_bytes()
        validated, created = self.state.save_response(response)
        self.assertFalse(created)
        self.assertEqual(validated["status"], "completed")
        self.assertEqual(self.reply_path.read_bytes(), first)
        self.assertEqual(
            chatgpt_route.validate_exchange(
                self.bundle, json.loads(self.reply_path.read_text(encoding="utf-8"))
            )["status"],
            "completed",
        )

    def test_existing_different_response_is_never_overwritten(self) -> None:
        original = self.response(result="first result")
        self.reply_path.write_text(json.dumps(original), encoding="utf-8")
        before = self.reply_path.read_bytes()
        with self.assertRaisesRegex(chat_transfer.ChatTransferError, "already exists with a different response"):
            self.state.save_response(self.response(result="second result"))
        self.assertEqual(self.reply_path.read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows no-replace rename")
    def test_windows_creates_reply_without_hard_links_or_overwrite(self) -> None:
        with patch.object(chat_transfer.os, "link", side_effect=PermissionError):
            self.assertTrue(chat_transfer._atomic_create_reply(self.reply_path, b"first"))
            self.assertFalse(chat_transfer._atomic_create_reply(self.reply_path, b"second"))
        self.assertEqual(self.reply_path.read_bytes(), b"first")
        self.assertEqual(list(self.base.glob(".chat-transfer-*")), [])

    def test_completed_status_is_not_semantic_acceptance(self) -> None:
        status = chat_transfer._response_status_message("completed", created=True)
        self.assertIn("Response status is completed", status)
        self.assertIn("Parent semantic acceptance is still required", status)

    def test_factory_binds_only_loopback_without_opening_a_socket(self) -> None:
        previous_token = self.state.token
        with patch.object(chat_transfer.HTTPServer, "__init__", return_value=None) as initialize:
            server = chat_transfer.create_server(self.state)
        initialize.assert_called_once_with(("127.0.0.1", 0), chat_transfer.TransferHandler)
        self.assertIs(server.state, self.state)
        self.assertFalse(server.completed)
        self.assertIsNone(server.deadline)
        self.assertNotEqual(self.state.token, previous_token)

    def test_timeout_stops_without_echoing_prompt_or_response(self) -> None:
        server = SimpleNamespace(origin="http://127.0.0.1:32123", completed=False,
                                 handle_request=Mock(), server_close=Mock())
        with (patch.object(chat_transfer, "create_server", return_value=server) as create,
              patch.object(chat_transfer.time, "monotonic", side_effect=[0.0, 0.25, 1.01]),
              redirect_stdout(io.StringIO()) as stdout):
            self.assertEqual(chat_transfer.serve_transfer(self.state, timeout_seconds=1), 3)
        create.assert_called_once_with(self.state)
        server.handle_request.assert_called_once_with()
        server.server_close.assert_called_once_with()
        self.assertEqual(server.deadline, 1.0)
        self.assertEqual(server.timeout, 0.75)
        self.assertEqual(server.client_timeout_seconds, 0.75)
        metadata = stdout.getvalue()
        self.assertIn('"event": "listening"', metadata)
        self.assertIn('"event": "timeout"', metadata)
        self.assertNotIn('"event": "saved"', metadata)
        self.assertNotIn(self.bundle["prompt"], metadata)
        self.assertNotIn("bounded finding", metadata)
        self.assertFalse(self.reply_path.exists())

    def test_saved_event_identifies_input_and_preserves_exit_status(self) -> None:
        for status, exit_code in (("completed", 0), ("blocked", 3), ("missing_input", 3)):
            with self.subTest(status=status):
                state = chat_transfer.TransferState.from_paths(self.bundle_path, self.reply_path)
                state.token = "test-capability-token"
                response = self.response()
                response["status"] = status
                server = SimpleNamespace(origin="http://127.0.0.1:32123", completed=False,
                                         server_close=Mock())

                def save():
                    state.save_response(response)
                    server.completed = True

                server.handle_request = Mock(side_effect=save)
                with (patch.object(chat_transfer, "create_server", return_value=server),
                      redirect_stdout(io.StringIO()) as stdout):
                    self.assertEqual(chat_transfer.serve_transfer(state, timeout_seconds=1), exit_code)
                events = [json.loads(line) for line in stdout.getvalue().splitlines()]
                self.assertEqual(events[-1], {
                    "event": "saved", "request_id": self.bundle["request_id"],
                    "input_sha256": self.bundle["input_sha256"], "status": status,
                })
                self.assertEqual(json.loads(self.reply_path.read_text(encoding="utf-8")), response)
                server.server_close.assert_called_once_with()
                self.reply_path.unlink()

    def test_absolute_deadline_interrupts_continuous_partial_request(self) -> None:
        class PendingInput(io.RawIOBase):
            """A blocking stream; shutdown ends reads while traffic is active."""
            def __init__(self):
                super().__init__()
                self.condition = threading.Condition()
                self.pending = bytearray()
                self.ended = False

            def readable(self):
                return True

            def feed(self, data):
                with self.condition:
                    if self.ended:
                        raise OSError("connection ended")
                    self.pending.extend(data)
                    self.condition.notify_all()

            def end(self):
                with self.condition:
                    self.ended = True
                    self.condition.notify_all()

            def readinto(self, target):
                with self.condition:
                    while not self.pending and not self.ended:
                        self.condition.wait()
                    count = min(len(target), len(self.pending))
                    target[:count] = self.pending[:count]
                    del self.pending[:count]
                    return count

        # Exercise the real finish_request, Timer and HTTP parser without the
        # TCP listener/socketpair. Only connection shutdown/EOF is a fixture.
        server = object.__new__(chat_transfer.TransferServer)
        server.state = self.state
        server.server_port = 32123
        server.completed = False
        server.RequestHandlerClass = chat_transfer.TransferHandler
        source = PendingInput()
        shutdown_calls, worker_errors = [], []

        def shutdown(how):
            shutdown_calls.append(how)
            source.end()

        connection = SimpleNamespace(makefile=lambda *_: io.BufferedReader(source),
                                     sendall=lambda _data: None, shutdown=shutdown)
        server.deadline = time.monotonic() + 0.15

        def finish_request():
            try:
                server.finish_request(connection, ("127.0.0.1", 0))
            except Exception as error:
                worker_errors.append(error)

        worker = threading.Thread(target=finish_request, daemon=True)
        worker.start()
        chunks_sent = 0
        try:
            source.feed(b"GET /transfer/")
            until = time.monotonic() + 1
            while worker.is_alive() and time.monotonic() < until:
                try:
                    source.feed(b"x")
                    chunks_sent += 1
                except OSError:
                    break
                time.sleep(0.01)
            worker.join(timeout=0.3)
            self.assertFalse(worker.is_alive(), "traffic must not extend the absolute deadline")
            self.assertGreater(chunks_sent, 1, "fixture must supply continuous partial traffic")
            self.assertEqual(shutdown_calls, [socket.SHUT_RDWR])
            self.assertEqual(worker_errors, [])
            self.assertFalse(self.reply_path.exists())
        finally:
            source.end()
            worker.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
