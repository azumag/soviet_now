#!/usr/bin/env python3
"""Host-verified analysis prerequisites, not a proof of causal/strategic quality."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

MAX_BYTES=16*1024*1024
ALLOWED_INPUT_TYPES=set(range(1,12))

def encode(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()

def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON key')
        result[key]=value
    return result

def decode(raw):
    return json.loads(raw,object_pairs_hook=unique_object,parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON')))

def scopes(row):
    yield row
    for name in ('state_snapshot','state','game_state'):
        if isinstance(row.get(name),dict):yield row[name]

def counter(row):
    values=[v['makeSorenCount'] for v in scopes(row) if 'makeSorenCount' in v]
    if not values:return None,False
    if any(type(v) is not int or v<0 for v in values) or len(set(values))!=1:return None,True
    return values[0],False

def terminal(row):
    return (row.get('event') in ('game_end','game_over','game_complete') or
            any(v.get(k) is True for v in scopes(row) for k in ('game_over','gameOver','isGameOver')))

def game_evidence(name,raw):
    rows=[decode(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    if not rows or any(not isinstance(row,dict) for row in rows):raise ValueError('empty or invalid history')
    observations=[counter(row) for row in rows]
    values=[v for v,bad in observations if v is not None]
    first,bad=observations[0]
    baseline=(first==0 and not bad and rows[0].get('turn')==1 and type(rows[0].get('turn')) is int)
    coherent=not any(b for _,b in observations) and all(a<=b for a,b in zip(values,values[1:]))
    status='unknown'
    if baseline and coherent:
        if any(v>0 for v in values):status='founded'
        elif observations[-1][0]==0 and terminal(rows[-1]):status='not_founded'
    types=sorted({row['next_type'] for row in rows if type(row.get('next_type')) is int})
    turns=sorted({row['turn'] for row in rows if type(row.get('turn')) is int and row['turn']>=1})
    return {'file':name,'sha256':hashlib.sha256(raw).hexdigest(),'turns':turns,'next_types':types,
            'counter_status':status,'counter_baseline_verified':baseline,'counter_coherent':coherent}

def build_evidence(root,names):
    root=Path(root).resolve()
    if not names or len(names)>256 or len(set(names))!=len(names):raise ValueError('invalid input set')
    games=[]
    for name in names:
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='game_history':raise ValueError('history outside allowlist')
        path=root/rel
        if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file() or path.stat().st_size>MAX_BYTES:raise ValueError('invalid history path')
        games.append(game_evidence(rel.as_posix(),path.read_bytes()))
    unknown=sum(g['counter_status']=='unknown' for g in games)
    founded=sum(g['counter_status']=='founded' for g in games)
    observed=sorted({v for g in games for v in g['next_types']})
    return {'version':1,'game_count':len(games),'founded_games':None if unknown else founded,
            'known_founded_games':founded,'unknown_counter_games':unknown,'observed_next_types':observed,
            'allowed_next_types':sorted(ALLOWED_INPUT_TYPES),'games':games,
            'meaning':'unknown is not zero; piece type is not a founding counter; turn1 zero baseline required'}

def nonempty(value):
    return isinstance(value,str) and bool(value.strip()) and len(value)<=2000

def validate(text,evidence):
    errors=[];decision='reject';doc={}
    blocks=re.findall(r'^```analysis_contract\s*\n(.*?)\n```\s*$',text,re.M|re.S)
    if len(blocks)!=1:return {'ok':False,'decision':'reject','errors':['one_contract_required']}
    try:
        doc=decode(blocks[0])
        if not isinstance(doc,dict):raise ValueError('not object')
    except (ValueError,TypeError):return {'ok':False,'decision':'reject','errors':['invalid_contract_json']}
    if type(doc.get('version')) is not int or doc['version']!=1:errors.append('invalid_version')
    if doc.get('decision') not in ('implement','hold'):errors.append('invalid_decision')
    if doc.get('evidence_sha256')!=hashlib.sha256(encode(evidence)).hexdigest():errors.append('evidence_digest_mismatch')
    if type(doc.get('game_count')) is not int or doc['game_count']!=evidence['game_count']:errors.append('game_count_mismatch')
    expected=evidence['founded_games'];supplied=doc.get('founded_games','missing')
    if (supplied is not None if expected is None else type(supplied) is not int or supplied!=expected):errors.append('founding_count_mismatch')
    hypotheses=doc.get('hypotheses');changes=doc.get('changes')
    if not isinstance(hypotheses,list):hypotheses=[];errors.append('invalid_hypotheses')
    if not isinstance(changes,list):changes=[];errors.append('invalid_changes')
    if doc.get('decision')=='hold':
        if changes or not nonempty(doc.get('reason')):errors.append('invalid_hold')
        decision='hold'
    else:
        decision='implement'
        if len(hypotheses)!=1:errors.append('one_hypothesis_required')
        if len(changes)!=1:errors.append('one_change_required')
    allowed_files={g['file']:set(g['turns']) for g in evidence['games']};ids=set()
    for hyp in hypotheses:
        if not isinstance(hyp,dict) or not nonempty(hyp.get('id')) or not nonempty(hyp.get('claim')):
            errors.append('invalid_hypothesis');continue
        if hyp['id'] in ids:errors.append('duplicate_hypothesis_id')
        ids.add(hyp['id']);refs=hyp.get('evidence')
        if not isinstance(refs,list) or not refs:errors.append('evidence_reference_required');continue
        for ref in refs:
            if (not isinstance(ref,dict) or type(ref.get('turn')) is not int or
                    ref.get('turn') not in allowed_files.get(ref.get('file'),set())):
                errors.append('unverified_reference')
    for change in changes:
        if not isinstance(change,dict):errors.append('invalid_change');continue
        target=change.get('target','')
        if not isinstance(target,str) or not (target=='strategy.py.staging' or re.fullmatch(r'strategy_helpers/[A-Za-z_][A-Za-z0-9_]*\.py',target)):
            errors.append('unapproved_target')
        if change.get('hypothesis_id') not in ids:errors.append('unknown_hypothesis')
        if not nonempty(change.get('mechanism')):errors.append('mechanism_required')
        values=change.get('required_next_types')
        if (not isinstance(values,list) or any(type(t) is not int or t not in ALLOWED_INPUT_TYPES or
                t not in evidence['observed_next_types'] for t in values)):
            errors.append('unsupported_next_type')
    # Narrow check of the observed literal mistake, limited to the proposed plan.
    # This deliberately does not purport to understand arbitrary prose or code.
    plan=text.partition('## Implementation Plan')[2].split('\n## ',1)[0]
    for op,raw in re.findall(r'\bnext_type\s*(==|>=|>)\s*(\d+)',plan):
        num=int(raw)
        if (op in ('==','>=') and num>11) or (op=='>' and num>=11):errors.append('unreachable_plan_condition')
    return {'ok':not errors and decision=='implement','decision':decision if not errors else 'reject',
            'errors':sorted(set(errors)),'evidence_sha256':hashlib.sha256(encode(evidence)).hexdigest(),
            'analysis_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'limitation':'Checks evidence/shape/reachable declared inputs, not causal correctness of free text or candidate code.'}

def save(path,raw):
    path=Path(path)
    if path.is_symlink():raise ValueError('symlink output')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix='.analysis-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    ev=sub.add_parser('evidence');ev.add_argument('--root',required=True);ev.add_argument('--output',required=True);ev.add_argument('files',nargs='+')
    check=sub.add_parser('validate');check.add_argument('--evidence',required=True);check.add_argument('--analysis',required=True);check.add_argument('--result',required=True)
    args=parser.parse_args()
    try:
        if args.action=='evidence':
            raw=encode(build_evidence(args.root,args.files));save(args.output,raw);print(hashlib.sha256(raw).hexdigest());return 0
        path=Path(args.analysis)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BYTES:raise ValueError('invalid analysis file')
        evidence=decode(Path(args.evidence).read_bytes());text=path.read_text(encoding='utf-8')
        result=validate(text,evidence)
        save(args.result,encode(result));save(str(Path(args.result).with_suffix('.md')),text.encode())
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['ok'] else 83 if result['decision']=='hold' else 82
    except (OSError,ValueError,TypeError,KeyError):
        if args.action=='validate':save(args.result,encode({'ok':False,'decision':'reject','errors':['invalid_evidence_or_output']}))
        return 81

if __name__=='__main__':raise SystemExit(main())
