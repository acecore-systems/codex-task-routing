#!/usr/bin/env python3
"""Prepare and verify opt-in public-web research; never transmit a request."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from routing_core.common import RoutingError, _load_json
from muse_pilot_plan import _hash, _text


def prepare(data):
    fields = {'schema_version', 'actor', 'explicit_opt_in', 'host_authorized',
              'target_authorized', 'materials_authorized', 'cost_authorized',
              'data_class', 'purpose', 'prompt', 'prompt_sha256', 'status'}
    if not isinstance(data, dict) or set(data) != fields or type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('input')
    flags = ('explicit_opt_in', 'host_authorized', 'target_authorized', 'materials_authorized', 'cost_authorized')
    if any(type(data[k]) is not bool for k in flags):
        raise ValueError('authorization')
    raw = _text(data['prompt'], 4096)
    digest = _hash(data['prompt_sha256'])
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('hash')
    status = data['status']
    if isinstance(status, dict) and status.get('inputSha256') == digest:
        if status.get('state') in ('intent', 'unverified'):
            return {'ready': False, 'next_action': 'reconcile_existing_request', 'sent': 'unknown'}
        if status.get('state') == 'completed':
            return {'ready': False, 'next_action': 'review_existing_result', 'sent': True}
    gaps = [k for k in flags if not data[k]]
    if status != {'state': 'not_started', 'inputSha256': digest}:
        return {'ready': False, 'next_action': 'check_request_status', 'sent': 'unknown'}
    if data['actor'] != 'root' or data['data_class'] != 'public' or data['purpose'] != 'public_web_research':
        return {'ready': False, 'next_action': 'use_existing_policy', 'sent': False}
    if gaps:
        return {'ready': False, 'next_action': 'use_existing_policy', 'gaps': gaps, 'sent': False}
    return {'ready': True, 'next_action': 'manual_tool_call', 'tool': 'local_auth_muse_web_research',
            'arguments': {'operation': 'run', 'dataClass': 'public', 'explicitApproval': True,
                          'prompt': data['prompt'], 'promptSha256': digest},
            'sent': False, 'parent_acceptance_required': True}


def verify(data):
    if not isinstance(data, dict) or set(data) != {'schema_version', 'prompt_sha256', 'result'} or type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('input')
    digest = _hash(data['prompt_sha256'])
    r = data['result']
    if not isinstance(r, dict) or set(r) != {'state', 'inputSha256', 'model', 'effort', 'inputTokens', 'outputTokens', 'output'} or r['state'] != 'completed' or r['inputSha256'] != digest or r['model'] != 'muse-ember-1.0' or r['effort'] not in (None, 'max'):
        raise ValueError('result')
    for name in ('inputTokens', 'outputTokens'):
        if r[name] is not None and (type(r[name]) is not int or not 0 <= r[name] <= 10000000):
            raise ValueError('usage')
    output = r['output']
    if not isinstance(output, list) or not 1 <= len(output) <= 30:
        raise ValueError('output')
    searches = 0
    citations = []
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ValueError('item')
        if item.get('type') == 'web_search_call' and set(item) == {'type', 'status'} and item['status'] == 'completed':
            searches += 1
        elif item.get('type') == 'output_text' and set(item) == {'type', 'text', 'annotations'}:
            text = item['text']
            _text(text, 16000)
            texts.append(text)
            if not isinstance(item['annotations'], list) or len(item['annotations']) > 100:
                raise ValueError('annotations')
            for ann in item['annotations']:
                if not isinstance(ann, dict) or set(ann) != {'type', 'url', 'title', 'start_index', 'end_index'} or ann['type'] != 'url_citation':
                    raise ValueError('citation')
                url = ann['url']
                if (not isinstance(url, str) or not 1 <= len(url) <= 2048 or '\\' in url
                        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)):
                    raise ValueError('url')
                u = urlsplit(url)
                # Parsing alone does not validate the port or reject empty userinfo.
                port = u.port
                if (u.scheme not in ('http', 'https') or not u.hostname
                        or u.username is not None or u.password is not None
                        or (port is not None and not 1 <= port <= 65535)):
                    raise ValueError('url')
                if not isinstance(ann['title'], str) or len(ann['title']) > 512 or any(type(ann[k]) is not int for k in ('start_index', 'end_index')) or not 0 <= ann['start_index'] < ann['end_index'] <= len(text):
                    raise ValueError('citation span')
                citations.append(ann)
        else:
            raise ValueError('item')
    if not 1 <= searches <= 3 or not texts or not citations or sum(len(t.encode('utf-8')) for t in texts) > 16000:
        raise ValueError('ungrounded')
    return {'verified': True, 'input_sha256': digest, 'requested_effort': 'max', 'observed_effort': r['effort'], 'input_tokens': r['inputTokens'], 'output_tokens': r['outputTokens'], 'searches': searches, 'output': output,
            'parent_acceptance_required': True, 'accepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify'))
    parser.add_argument('--facts', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = _load_json(args.facts, 'public web research', limit=131072)
        value = prepare(data) if args.action == 'prepare' else verify(data)
    except (ValueError, TypeError, UnicodeError, RecursionError, OSError, RoutingError):
        print(json.dumps({'error': 'invalid_muse_web_research_input'}), file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
