"""Observation-only hook: store enrolled counters, never prompts or transcripts."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

import routing
from routing_core.observation_capture import record_event, verify_stopped_task, fire_arm, discard_unreadable_arm
from usage_snapshot import read_snapshot


def main() -> int:
    prompt_hook = len(sys.argv) > 1 and sys.argv[1] == 'prompt'
    try:
        raw = sys.stdin.buffer.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            if prompt_hook:
                home = routing._default_codex_home()
                discard_unreadable_arm(codex_home=home, policy=routing.load_policy(codex_home=home))
            return 0
        try:
            event = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=routing._reject_duplicate_pairs)
        except (ValueError, UnicodeError):
            if prompt_hook:
                home = routing._default_codex_home()
                discard_unreadable_arm(codex_home=home, policy=routing.load_policy(codex_home=home))
            return 0
        if not isinstance(event, dict):
            if prompt_hook:
                home = routing._default_codex_home()
                discard_unreadable_arm(codex_home=home, policy=routing.load_policy(codex_home=home))
            return 0
        if prompt_hook and event.get('hook_event_name') != 'UserPromptSubmit':
            home = routing._default_codex_home()
            discard_unreadable_arm(codex_home=home, policy=routing.load_policy(codex_home=home))
            return 0
        # No prompt, transcript, error, cwd or assistant-message value is retained.
        filtered = {key: event[key] for key in ('hook_event_name', 'session_id', 'turn_id',
                    'agent_id', 'status', 'token_usage') if key in event}
        del event, raw
        home = routing._default_codex_home()
        policy = routing.load_policy(codex_home=home)
        def read_child(enrollment, agent_id):
            return read_snapshot(home / 'state_5.sqlite', enrollment, agent_id)
        if filtered.get('hook_event_name') == 'UserPromptSubmit':
            fire_arm(codex_home=home, policy=policy, event=filtered, snapshot_reader=read_child)
            return 0
        outcome = record_event(codex_home=home, policy=policy, event=filtered, child_reader=read_child)
        if outcome == 'recorded' and filtered.get('hook_event_name') == 'Stop':
            verify_stopped_task(codex_home=home, policy=policy, session_id=filtered.get('session_id'),
                                turn_id=filtered.get('turn_id'), snapshot_reader=read_child, sleep=time.sleep)
    except (Exception,):
        # Hook failure must not interrupt or change model execution; never echo input.
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
