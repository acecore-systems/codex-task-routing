"""Compare supplied observation completions; never collect or write usage data."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from routing_core.common import RoutingError, _load_json
from routing_core.observation import _validate_observation_completion

CONDITIONS = ('work', 'acceptance', 'environment', 'parent_model', 'parent_effort')


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096


def _evidenced(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {'value', 'evidence_ref', 'reason'}:
        return False
    if value['value'] is None:
        return value['evidence_ref'] is None and _text(value['reason'])
    return _text(value['value']) and _text(value['evidence_ref']) and value['reason'] is None


def _flag(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {'value', 'evidence_ref', 'reason'}:
        return False
    if value['value'] is True:
        return _text(value['evidence_ref']) and value['reason'] is None
    return (value['value'] is False or value['value'] is None) and (
        value['evidence_ref'] is None and _text(value['reason'])
    )


def _validate_side(side: Any) -> None:
    fields = {'completion', 'completion_ref', 'conditions', 'coverage',
              'independent', 'quality', 'chat_messages'}
    if not isinstance(side, dict) or set(side) != fields:
        raise RoutingError('comparison side has invalid fields')
    _validate_observation_completion(side['completion'])
    conditions = side['conditions']
    if (not _text(side['completion_ref']) or not isinstance(conditions, dict)
            or set(conditions) != set(CONDITIONS)
            or not all(_evidenced(value) for value in conditions.values())):
        raise RoutingError('comparison conditions or reference are invalid')
    if not all(_flag(side[name]) for name in ('coverage', 'independent', 'quality')):
        raise RoutingError('comparison evidence flags are invalid')
    chat = side['chat_messages']
    if not isinstance(chat, dict) or set(chat) != {'value', 'evidence_ref', 'reason'}:
        raise RoutingError('chat messages are invalid')
    if chat['value'] is None:
        valid = chat['evidence_ref'] is None and _text(chat['reason'])
    else:
        valid = (type(chat['value']) is int and chat['value'] >= 0
                 and _text(chat['evidence_ref']) and chat['reason'] is None)
    if not valid:
        raise RoutingError('chat messages are invalid')


def compare(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {'baseline', 'candidate', 'independent_comparisons'}:
        raise RoutingError('comparison must contain baseline and candidate')
    a, b = data['baseline'], data['candidate']
    _validate_side(a)
    _validate_side(b)
    if not _flag(data['independent_comparisons']):
        raise RoutingError('comparison independence evidence is invalid')
    reasons: list[str] = []
    if data['independent_comparisons']['value'] is not True:
        reasons.append('comparison ranges: ' + data['independent_comparisons']['reason'])
    if a['completion_ref'] == b['completion_ref']:
        reasons.append('baseline and candidate evidence references must differ')
    for field in CONDITIONS:
        av, bv = a['conditions'][field]['value'], b['conditions'][field]['value']
        if av is None or bv is None:
            reasons.append(f'{field} is unknown')
        elif av != bv:
            reasons.append(f'{field} differs')

    bases = {run['usage'].get('basis', 'separate_counters')
             for side in (a, b) for run in side['completion']['runs']}
    if len(bases) > 1:
        reasons.append('usage bases differ')
    basis = next(iter(bases)) if len(bases) == 1 else None

    seen_run_ids: set[str] = set()
    for name, side in (('baseline', a), ('candidate', b)):
        completion = side['completion']
        for run in completion['runs']:
            if run['run_id'] is not None:
                if run['run_id'] in seen_run_ids:
                    reasons.append('duplicate run identity; independent ranges cannot be confirmed')
                seen_run_ids.add(run['run_id'])
        if completion['measurement']['state'] != 'measured' or completion['state'] != 'completed':
            reasons.append(f'{name} measurement is incomplete')
        if completion['verification']['reference'] is None:
            reasons.append(f'{name} verification is missing')
        for flag in ('coverage', 'independent', 'quality'):
            if side[flag]['value'] is not True:
                reasons.append(f'{name} {flag}: {side[flag]["reason"]}')
        for run in completion['runs']:
            if basis == 'separate_counters' and run['relationship'] == 'parent' and (
                run['observed']['model'] != side['conditions']['parent_model']['value']
                or run['observed']['effort'] != side['conditions']['parent_effort']['value']
            ):
                reasons.append(f'{name} observed parent settings are unknown or differ')
                break

    def amount(side: dict[str, Any], parent_only: bool) -> int:
        return sum(
            (run['usage']['total_tokens'] if basis == 'aggregate_total_tokens' else
             run['usage']['input_tokens'] + run['usage']['output_tokens'])
            for run in side['completion']['runs']
            if not parent_only or run['relationship'] == 'parent'
        )

    def metric(parent_only: bool) -> dict[str, Any]:
        if reasons:
            return {'baseline': None, 'candidate': None, 'difference': None,
                    'reduction_percent': None, 'reason': '; '.join(reasons)}
        baseline, candidate = amount(a, parent_only), amount(b, parent_only)
        if baseline == 0:
            return {'baseline': baseline, 'candidate': candidate, 'difference': candidate - baseline,
                    'reduction_percent': None, 'reason': 'zero baseline'}
        return {'baseline': baseline, 'candidate': candidate, 'difference': candidate - baseline,
                'reduction_percent': (baseline - candidate) * 100 / baseline, 'reason': None}

    return {
        'basis': basis, 'runtime_model_verified': False if basis in {'aggregate_total_tokens', 'hook_request_counters'} else None,
        'parent_tokens': metric(True), 'total_tokens': metric(False),
        'baseline_chat_messages': a['chat_messages'], 'candidate_chat_messages': b['chat_messages'],
        'baseline_rework': a['completion']['rework'], 'candidate_rework': b['completion']['rework'],
        'baseline_quality': a['quality'], 'candidate_quality': b['quality'],
        'interpretation': 'aggregate total or input + output tokens; cache and reasoning are subsets; not price or account usage',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = _load_json(args.input, 'comparison input', 256 * 1024)
        print(json.dumps(compare(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (RoutingError, OSError, ValueError, TypeError):
        print('Invalid or inaccessible comparison input.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
