#!/usr/bin/env python3
"""Serve one local-only Browser handoff for an approved Chat route bundle.

The server deliberately exposes a prepared prompt only through a loopback
HTML document.  It never opens ChatGPT, calls a model or network API, reads
authentication or conversation data, or evaluates browser-side script.  A
supported Browser surface can read the readonly prompt field and paste it
into an already-approved temporary Chat.  It can later put that Chat's final
JSON response into this form for local schema/correlation validation.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import socket
import stat
import sys
import tempfile
import time
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit


# The helper is intentionally loaded from the same installed plugin directory.
# It is the protocol authority for prepared-bundle and response validation.
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))
import chatgpt_route  # noqa: E402


DEFAULT_TIMEOUT_SECONDS = 10 * 60
MAX_TIMEOUT_SECONDS = 60 * 60
# A URL-encoded form can expand a valid 256 KiB reply substantially.  The
# decoded canonical reply is separately kept within chatgpt_route.MAX_JSON_BYTES.
MAX_POST_BYTES = chatgpt_route.MAX_JSON_BYTES * 3
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


class ChatTransferError(ValueError):
    """A local transfer error that does not include submitted material."""


def _origin_for(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _host_is_valid(host: str | None, port: int) -> bool:
    return host == f"127.0.0.1:{port}"


def _origin_is_valid(origin: str | None, port: int) -> bool:
    return origin == _origin_for(port)


def _path_is_valid(path: str, token: str) -> bool:
    parsed = urlsplit(path)
    return not parsed.query and parsed.path == f"/transfer/{token}"


def _minimal_error_page() -> bytes:
    """Return an unauthenticated error document with no transfer material."""

    return b"<!doctype html><meta charset=utf-8><title>Unavailable</title><p>Local transfer unavailable.</p>"


def _parse_post_length(value: str | None) -> int:
    try:
        length = int(value or "")
    except ValueError as exc:
        raise ChatTransferError("invalid response length") from exc
    if length < 0 or length > MAX_POST_BYTES:
        raise ChatTransferError("response exceeds the size limit")
    return length


def _response_text_from_form(body: bytes) -> str:
    try:
        form = parse_qs(
            body.decode("utf-8"), strict_parsing=True, keep_blank_values=True, max_num_fields=2, errors="strict"
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChatTransferError("invalid form data") from exc
    values = form.get("response_json")
    if set(form) != {"response_json"} or values is None or len(values) != 1:
        raise ChatTransferError("exactly one response JSON field is required")
    response_text = values[0]
    if len(response_text.encode("utf-8")) > chatgpt_route.MAX_JSON_BYTES:
        raise ChatTransferError("response exceeds the size limit")
    return response_text


def _response_parse_status(response_text: str, error: chatgpt_route.ChatRouteError) -> str:
    """Describe a JSON rejection without exposing any response characters."""

    cause = error.__cause__
    if isinstance(cause, json.JSONDecodeError):
        if cause.pos < len(response_text):
            character = f"U+{ord(response_text[cause.pos]):04X}"
        else:
            character = "EOF"
        return (
            "Response JSON parse rejected "
            f"(syntax line={cause.lineno}, column={cause.colno}, character={character}, "
            f"decoded_chars={len(response_text)})."
        )
    if isinstance(cause, chatgpt_route.DuplicateKeyError):
        return f"Response JSON parse rejected (duplicate object key, decoded_chars={len(response_text)})."
    if isinstance(cause, ValueError):
        return f"Response JSON parse rejected (unsupported JSON value, decoded_chars={len(response_text)})."
    if "must be a JSON object" in str(error):
        return f"Response JSON parse rejected (top-level object required, decoded_chars={len(response_text)})."
    return f"Response JSON parse rejected (unclassified parser failure, decoded_chars={len(response_text)})."


def _safe_reply_path(path: Path | str) -> Path:
    """Return one explicit reply target after link/reparse and parent checks."""

    safe_path = chatgpt_route._assert_safe_ancestors(path)
    parent = safe_path.parent
    try:
        metadata = os.lstat(parent)
    except OSError as exc:
        raise ChatTransferError("reply directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or chatgpt_route._is_link_or_reparse(parent):
        raise ChatTransferError("reply directory is unavailable")
    if os.path.lexists(safe_path) and chatgpt_route._is_link_or_reparse(safe_path):
        raise ChatTransferError("refusing a symlink or reparse-point reply path")
    if os.path.lexists(safe_path):
        try:
            target_metadata = os.lstat(safe_path)
        except OSError as exc:
            raise ChatTransferError("reply path is unavailable") from exc
        if not stat.S_ISREG(target_metadata.st_mode):
            raise ChatTransferError("reply path is not a regular file")
    return safe_path


def _canonical_response_bytes(response: Mapping[str, Any]) -> bytes:
    encoded = (
        json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    if len(encoded) > chatgpt_route.MAX_JSON_BYTES:
        raise ChatTransferError("response exceeds the supported reply size limit")
    return encoded


def _load_existing_reply(path: Path) -> dict[str, Any] | None:
    if not os.path.lexists(path):
        return None
    try:
        return chatgpt_route._load_json_object(path, purpose="existing response")
    except chatgpt_route.ChatRouteError as exc:
        raise ChatTransferError("existing reply is unavailable") from exc


def _atomic_create_reply(path: Path, content: bytes) -> bool:
    """Create *path* atomically without replacing an independently made file.

    ``os.replace`` would make the write atomic but could overwrite a different
    reply created after the initial check.  Linking a fully-fsynced temporary
    file creates the final name only when it did not already exist.
    """

    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".chat-transfer-", dir=path.parent
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary = Path(temporary_name)
        if chatgpt_route._is_link_or_reparse(temporary):
            raise ChatTransferError("temporary reply path is unsafe")
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    except ChatTransferError:
        raise
    except OSError as exc:
        raise ChatTransferError("cannot save the reply") from exc
    finally:
        if temporary_name and os.path.lexists(temporary_name):
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


class TransferState:
    """The bounded, in-memory state for exactly one bundle and reply target."""

    def __init__(self, *, bundle: Mapping[str, Any], reply_path: Path) -> None:
        request_id, input_sha256 = chatgpt_route._expected_request(bundle)
        prompt = bundle.get("prompt")
        if not isinstance(prompt, str):
            raise ChatTransferError("prepared bundle prompt is unavailable")
        self.bundle = dict(bundle)
        self.prompt = prompt
        self.request_id = request_id
        self.input_sha256 = input_sha256
        self.reply_path = _safe_reply_path(reply_path)
        self.response_status: str | None = None
        # Exposed only on the authorized local page.  These fixed strings
        # identify transport progress without retaining response material.
        self.transfer_status = "Waiting for one response JSON submission."

    @classmethod
    def from_paths(cls, bundle_path: Path | str, reply_path: Path | str) -> "TransferState":
        bundle = chatgpt_route._load_json_object(Path(bundle_path), purpose="prepared bundle")
        return cls(bundle=bundle, reply_path=Path(reply_path))

    def save_response_text(self, response_text: str) -> tuple[dict[str, Any], bool]:
        """Validate and save one response, accepting only an identical retry."""

        try:
            response = chatgpt_route._parse_json_object(response_text, purpose="response")
        except chatgpt_route.ChatRouteError as exc:
            self.transfer_status = _response_parse_status(response_text, exc)
            raise ChatTransferError(str(exc)) from exc
        return self.save_response(response)

    def save_response(self, response: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Return validation metadata and whether this call created the reply."""

        try:
            _safe_reply_path(self.reply_path)
        except chatgpt_route.ChatRouteError as exc:
            raise ChatTransferError("reply path is unsafe") from exc
        try:
            validated = chatgpt_route.validate_exchange(self.bundle, response)
        except chatgpt_route.ChatRouteError as exc:
            raise ChatTransferError(str(exc)) from exc
        content = _canonical_response_bytes(response)
        existing = _load_existing_reply(self.reply_path)
        if existing is not None:
            try:
                chatgpt_route.validate_exchange(self.bundle, existing)
            except chatgpt_route.ChatRouteError as exc:
                raise ChatTransferError("existing reply is not a valid response for this bundle") from exc
            if existing != dict(response):
                raise ChatTransferError("reply already exists with a different response")
            self.response_status = validated["status"]
            return validated, False

        created = _atomic_create_reply(self.reply_path, content)
        if not created:
            # Another process won the race.  It is idempotent only when its
            # parsed protocol object is exactly the response just submitted.
            existing = _load_existing_reply(self.reply_path)
            if existing is None:
                raise ChatTransferError("reply could not be saved")
            try:
                chatgpt_route.validate_exchange(self.bundle, existing)
            except chatgpt_route.ChatRouteError as exc:
                raise ChatTransferError("existing reply is not a valid response for this bundle") from exc
            if existing != dict(response):
                raise ChatTransferError("reply already exists with a different response")
        self.response_status = validated["status"]
        return validated, created


def _response_status_message(status: str | None, *, created: bool | None = None) -> str:
    if status is None:
        return "Waiting for one response JSON submission."
    prefix = "Response saved" if created else "Matching response already saved"
    if status == "completed":
        return (
            f"{prefix} after schema and request-correlation validation. "
            "Response status is completed. Parent semantic acceptance is still required."
        )
    return (
        f"{prefix} after schema and request-correlation validation. "
        f"Response status is {status}; this is not a completed outcome and requires parent attention."
    )


def _page(state: TransferState, *, status: str, error: bool = False) -> bytes:
    status_class = "error" if error else "status"
    escaped_prompt = html.escape(state.prompt, quote=False)
    escaped_status = html.escape(status, quote=False)
    action = html.escape(f"/transfer/{state.token}", quote=True)
    document = f"""<!doctype html>
<html lang=\"en\">
<meta charset=\"utf-8\">
<!-- Keep the form's same-origin Origin header.  no-referrer makes an HTML
     form submission opaque (Origin: null) even when the target is local. -->
<meta name=\"referrer\" content=\"same-origin\">
<title>Local Chat transfer</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 58rem; padding: 0 1rem; }}
label {{ display: block; font-weight: 650; margin-top: 1rem; }}
textarea {{ box-sizing: border-box; min-height: 12rem; width: 100%; font: 0.9rem ui-monospace, monospace; }}
.status {{ border-left: .3rem solid #286; padding-left: .75rem; }}
.error {{ border-left: .3rem solid #a22; padding-left: .75rem; }}
</style>
<h1>Local Chat transfer</h1>
<p>Use supported Browser DOM controls to move this approved prompt and the final response. This page does not contact Chat or any external service.</p>
<form method=\"post\" action=\"{action}\" accept-charset=\"utf-8\">
  <label for=\"request_prompt\">Request prompt</label>
  <textarea id=\"request_prompt\" readonly aria-readonly=\"true\">{escaped_prompt}</textarea>
  <label for=\"response_json\">Response JSON</label>
  <textarea id=\"response_json\" name=\"response_json\" required></textarea>
  <button type=\"submit\">Save verified response</button>
</form>
<h2>Transfer status</h2>
<p class=\"{status_class}\">{escaped_status}</p>
</html>
"""
    return document.encode("utf-8")


class TransferServer(HTTPServer):
    """A loopback-only HTTP server carrying one TransferState."""

    def __init__(self, state: TransferState) -> None:
        super().__init__(("127.0.0.1", 0), TransferHandler)
        self.state = state
        self.completed = False
        self.client_timeout_seconds = 1.0
        self.deadline: float | None = None

    @property
    def origin(self) -> str:
        return _origin_for(self.server_port)

    def get_request(self) -> tuple[Any, Any]:
        request, address = super().get_request()
        request.settimeout(self.client_timeout_seconds)
        return request, address

    def finish_request(self, request: Any, client_address: Any) -> None:
        # Socket timeouts bound inactivity, not a client continuously sending
        # bytes. Interrupt the active connection at the absolute CLI deadline.
        def expire() -> None:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = None
        if self.deadline is not None:
            timer = threading.Timer(max(0.0, self.deadline - time.monotonic()), expire)
            timer.daemon = True
            timer.start()
        try:
            super().finish_request(request, client_address)
        except OSError:
            # A disconnected Browser or expired socket carries no useful
            # traceback; preserve the one-shot server until its deadline.
            self.state.transfer_status = "Browser connection ended before transfer completion."
        finally:
            if timer is not None:
                timer.cancel()


class TransferHandler(BaseHTTPRequestHandler):
    server: TransferServer
    # Explicit close framing keeps Browser form responses unambiguous across
    # the loopback environments exercised by this helper.
    protocol_version = "HTTP/1.0"

    def log_message(self, _format: str, *args: object) -> None:
        # Do not emit the capability URL, submitted response, or request paths.
        return

    def _host_is_valid(self) -> bool:
        return _host_is_valid(self.headers.get("Host"), self.server.server_port)

    def _path_is_valid(self) -> bool:
        return _path_is_valid(self.path, self.server.state.token)

    def _set_transfer_status(self, status: str) -> None:
        self.server.state.transfer_status = status

    def _send(self, code: HTTPStatus, content: bytes, *, content_type: str = "text/html; charset=utf-8") -> None:
        # The response is complete before this connection is closed.  Each
        # local capability URL has one request per connection, avoiding a
        # keep-alive parser retaining form data after submission.
        self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header(
            "Content-Security-Policy",
            CONTENT_SECURITY_POLICY,
        )
        # Match the document policy so same-origin Browser form submissions
        # retain their Origin header while cross-origin navigation leaks none.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _send_error_page(self, code: HTTPStatus, status: str) -> None:
        self._send(code, _page(self.server.state, status=status, error=True))

    def _send_unavailable(self, code: HTTPStatus) -> None:
        self._send(code, _minimal_error_page())

    def do_GET(self) -> None:
        if not self._host_is_valid():
            self._send_unavailable(HTTPStatus.BAD_REQUEST)
            return
        if not self._path_is_valid():
            self._send_unavailable(HTTPStatus.NOT_FOUND)
            return
        self._send(
            HTTPStatus.OK,
            _page(
                self.server.state,
                status=(
                    _response_status_message(self.server.state.response_status)
                    if self.server.state.response_status is not None
                    else self.server.state.transfer_status
                ),
            ),
        )

    def do_POST(self) -> None:
        if not self._host_is_valid():
            self._send_unavailable(HTTPStatus.BAD_REQUEST)
            return
        if not self._path_is_valid():
            self._send_unavailable(HTTPStatus.NOT_FOUND)
            return
        origin_valid = _origin_is_valid(self.headers.get("Origin"), self.server.server_port)
        self._set_transfer_status(
            f"POST received (host_valid=true, path_valid=true, origin_valid={'true' if origin_valid else 'false'})."
        )
        if not origin_valid:
            self._send_unavailable(HTTPStatus.FORBIDDEN)
            return
        self._set_transfer_status("POST origin accepted; checking form encoding.")
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            self._set_transfer_status("Response form rejected before body processing.")
            self._send_error_page(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Transfer status: response must use the local form.")
            return
        try:
            length = _parse_post_length(self.headers.get("Content-Length"))
        except ChatTransferError as exc:
            code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if "size limit" in str(exc) else HTTPStatus.BAD_REQUEST
            self._set_transfer_status("Response form rejected before body processing.")
            self._send_error_page(code, f"Transfer status: {exc}.")
            return
        self._set_transfer_status(f"POST body reading (content_length={length} bytes).")
        try:
            body = self.rfile.read(length)
        except (OSError, TimeoutError):
            # The client socket is bounded by serve_transfer's remaining
            # deadline.  A partial upload must not keep the CLI alive.
            self._set_transfer_status("Response body did not arrive before the transfer deadline.")
            self.close_connection = True
            return
        if len(body) != length:
            self._set_transfer_status("Response body was incomplete.")
            self._send_error_page(HTTPStatus.BAD_REQUEST, "Transfer status: incomplete response body.")
            return
        self._set_transfer_status("Checking response JSON format.")
        try:
            response_text = _response_text_from_form(body)
        except ChatTransferError as exc:
            code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if "size limit" in str(exc) else HTTPStatus.BAD_REQUEST
            self._set_transfer_status("Response JSON was rejected before correlation validation.")
            self._send_error_page(code, f"Transfer status: {exc}.")
            return
        self._set_transfer_status("Validating response correlation.")
        try:
            validated, created = self.server.state.save_response_text(response_text)
        except ChatTransferError as exc:
            if not self.server.state.transfer_status.startswith("Response JSON parse rejected"):
                self._set_transfer_status("Response JSON was rejected by correlation or save checks.")
            self._send_error_page(HTTPStatus.UNPROCESSABLE_ENTITY, f"Transfer status: response rejected ({exc}).")
            return
        self.server.completed = True
        self._send(
            HTTPStatus.OK,
            _page(
                self.server.state,
                status=_response_status_message(validated["status"], created=created),
            ),
        )


def create_server(state: TransferState) -> TransferServer:
    """Create a random-port, random-capability loopback server for tests or CLI."""

    state.token = secrets.token_urlsafe(32)
    return TransferServer(state)


def serve_transfer(state: TransferState, *, timeout_seconds: int) -> int:
    server = create_server(state)
    deadline = time.monotonic() + timeout_seconds
    server.deadline = deadline
    metadata = {
        "event": "listening",
        "input_sha256": state.input_sha256,
        "request_id": state.request_id,
        "timeout_seconds": timeout_seconds,
        "url": f"{server.origin}/transfer/{state.token}",
    }
    print(json.dumps(metadata, ensure_ascii=True, sort_keys=True), flush=True)
    try:
        while not server.completed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(
                    json.dumps(
                        {"event": "timeout", "request_id": state.request_id},
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return 3
            server.timeout = min(remaining, 1.0)
            # A submitted form may take more than a second to arrive from the
            # Browser.  Bound the read by the transfer deadline itself, rather
            # than closing a valid in-flight response after one second.
            server.client_timeout_seconds = max(0.01, remaining)
            server.handle_request()
    except KeyboardInterrupt:
        print(
            json.dumps(
                {"event": "cancelled", "request_id": state.request_id},
                ensure_ascii=True,
                sort_keys=True,
            ),
            flush=True,
        )
        return 130
    finally:
        server.server_close()
    final_status = state.response_status
    print(
        json.dumps(
            {"event": "saved", "request_id": state.request_id, "status": final_status},
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if final_status == "completed" else 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one local Browser-to-Chat response transfer")
    parser.add_argument("--bundle", type=Path, required=True, help="approved bundle.json path")
    parser.add_argument("--reply", type=Path, required=True, help="the single reply.json path to create")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"seconds to wait before stopping (1-{MAX_TIMEOUT_SECONDS}; default {DEFAULT_TIMEOUT_SECONDS})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.timeout <= MAX_TIMEOUT_SECONDS:
        print("error: timeout must be within the supported range", file=sys.stderr)
        return 2
    try:
        state = TransferState.from_paths(args.bundle, args.reply)
    except (ChatTransferError, chatgpt_route.ChatRouteError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return serve_transfer(state, timeout_seconds=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
