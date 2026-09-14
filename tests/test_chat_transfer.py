from __future__ import annotations

import importlib.util
import io
import json
import shutil
import socket
import sys
import unittest
import uuid
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode


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

    def test_completed_status_is_not_semantic_acceptance(self) -> None:
        status = chat_transfer._response_status_message("completed", created=True)
        self.assertIn("Response status is completed", status)
        self.assertIn("Parent semantic acceptance is still required", status)

    def test_timeout_stops_without_echoing_prompt_or_response(self) -> None:
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(chat_transfer.serve_transfer(self.state, timeout_seconds=1), 3)
        metadata = stdout.getvalue()
        self.assertIn('"event": "listening"', metadata)
        self.assertIn('"event": "timeout"', metadata)
        self.assertNotIn(self.bundle["prompt"], metadata)
        self.assertNotIn("bounded finding", metadata)

    def test_absolute_deadline_interrupts_continuous_partial_request(self) -> None:
        server = chat_transfer.create_server(self.state)
        application, client = socket.socketpair()
        application.settimeout(5)
        server.deadline = time.monotonic() + 0.15
        worker = threading.Thread(target=server.finish_request,
                                  args=(application, ('127.0.0.1', 0)), daemon=True)
        worker.start()
        try:
            client.sendall(b'GET /transfer/')
            until = time.monotonic() + 1
            while worker.is_alive() and time.monotonic() < until:
                try:
                    client.sendall(b'x')
                except OSError:
                    break
                time.sleep(0.01)
            worker.join(timeout=0.3)
            self.assertFalse(worker.is_alive(), 'traffic must not extend the absolute deadline')
            self.assertFalse(self.reply_path.exists())
        finally:
            client.close()
            application.close()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
