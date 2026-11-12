#!/usr/bin/env python3
"""Plan or validate a single opt-in Local Auth Muse pilot; never send a request."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from routing_core.common import RoutingError, _load_json

TOOL='local_auth_muse_ember_pilot'
HASH=re.compile(r'[a-f0-9]{64}\Z')
RESULT_FIELDS={'state','inputSha256','model','effort','inputTokens','outputTokens','seconds','text'}

def _hash(value):
    if not isinstance(value,str) or not HASH.fullmatch(value): raise ValueError('invalid hash')
    return value

def _text(value,limit):
    if not isinstance(value,str) or not value.strip(): raise ValueError('invalid text')
    raw=value.encode('utf-8','strict')
    if len(raw)>limit: raise ValueError('text too large')
    return raw

def prepare(data):
    required={'schema_version','actor','explicit_opt_in','host_authorized','target_authorized','materials_authorized','data_class','prompt','prompt_sha256','status'}
    if not isinstance(data,dict) or set(data)!=required or type(data['schema_version']) is not int or data['schema_version']!=1: raise ValueError('invalid input')
    if data['actor']!='root' or data['data_class']!='public_or_synthetic': raise ValueError('scope')
    flags=('explicit_opt_in','host_authorized','target_authorized','materials_authorized')
    if any(type(data[k]) is not bool for k in flags): raise ValueError('authorization')
    raw=_text(data['prompt'],4096);digest=_hash(data['prompt_sha256'])
    if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('hash mismatch')
    status=data['status'];gaps=[k for k in flags if not data[k]]
    if not isinstance(status,dict) or set(status)!=RESULT_FIELDS or status.get('state')!='not_started' or any(status.get(k) is not None for k in RESULT_FIELDS-{'state'}): gaps.append('pilot_status_not_ready')
    if gaps:return {'ready':False,'next_action':'resolve_gaps','gaps':gaps,'sent':False}
    return {'ready':True,'next_action':'manual_tool_call','tool':TOOL,'arguments':{'operation':'run','dataClass':'public_or_synthetic','prompt':data['prompt'],'promptSha256':digest},'sent':False,'parent_acceptance_required':True}

def verify(data):
    if not isinstance(data,dict) or set(data)!={'schema_version','prompt_sha256','result'} or type(data['schema_version']) is not int or data['schema_version']!=1: raise ValueError('invalid input')
    digest=_hash(data['prompt_sha256']);r=data['result']
    if not isinstance(r,dict) or set(r)!=RESULT_FIELDS or r['state']!='completed' or r['inputSha256']!=digest or r['model']!='muse-ember-1.0': raise ValueError('result unverified')
    if r['effort'] not in (None,'max','high','medium','low','minimal'): raise ValueError('effort')
    for key in ('inputTokens','outputTokens'):
        if r[key] is not None and (type(r[key]) is not int or not 0<=r[key]<=10000000): raise ValueError('usage')
    if type(r['seconds']) not in (int,float) or not 0<=r['seconds']<=90: raise ValueError('duration')
    _text(r['text'],16000)
    return {'verified':True,'input_sha256':digest,'observed_model':r['model'],'observed_effort':r['effort'],'parent_acceptance_required':True,'accepted':False}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('prepare','verify'));parser.add_argument('--facts',type=Path,required=True);args=parser.parse_args(argv)
    try:
        data=_load_json(args.facts,'pilot planning input',limit=65536)
        result=prepare(data) if args.action=='prepare' else verify(data)
    except (ValueError,TypeError,UnicodeError,RecursionError,OSError,RoutingError):
        print(json.dumps({'error':'invalid_muse_pilot_input'}),file=sys.stderr);return 2
    print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
if __name__=='__main__':sys.exit(main())
