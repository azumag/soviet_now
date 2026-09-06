#!/usr/bin/env python3
"""Bound one improvement CLI; never infer quota from generated text.

OpenCode 1.18.27 run --format=json omits session.status=retry. Its native
--print-logs stderr does include session/model-bound stream errors. Only that
private pipe's structured events may end a call early; shared logs are not read.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

RATE_LIMITED = 79
DEADLINE = 80
BUFFER_LIMIT = 262144
OUTPUT_LIMIT = 8 * 1024 * 1024
FIELD = re.compile(r'([A-Za-z_][A-Za-z0-9_.]*)=("(?:\\.|[^"\\])*"|[^\s]+)')
RATE = re.compile(r'\brate[ _-]?limit(?:ed|ing|_error|error)?\b|\btoo many requests\b|\b429\b', re.I)


def monotonic():
    # clock_gettime is boot-relative across processes even on macOS/Python 3.9,
    # whose time.monotonic() epoch can otherwise be process-relative.
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def remaining(call_end, job_end, stage_end, *, now=None):
    """Never round a subsecond or exhausted budget up to a new call."""
    current = monotonic() if now is None else now
    limits = [x for x in (call_end, job_end, stage_end) if x is not None]
    return max(0.0, min(limits)-current) if limits else math.inf


def log_fields(line):
    if not line.startswith('timestamp=') or len(line) > BUFFER_LIMIT:
        return None
    result = {}; pos = 0
    while pos < len(line):
        match = FIELD.match(line, pos)
        if match is None or match[1] in result:
            return None
        value = match[2]
        if value.startswith('"'):
            try: value = json.loads(value)
            except ValueError: return None
        result[match[1]] = value
        pos = match.end()
        if pos == len(line): break
        if line[pos] != ' ': return None
        pos += 1
    return result


class NativeEvents:
    def __init__(self, directory, model):
        self.directory = str(Path(directory).resolve())
        self.provider, _, self.model = model.partition('/')
        self.sessions = set()

    def feed(self, line):
        d = log_fields(line)
        if not d: return None
        run = d.get('run'); session = d.get('id')
        if (d.get('level') == 'INFO' and d.get('message') == 'created'
                and d.get('directory') == self.directory
                and d.get('parentID') in (None, '', 'undefined', 'null')
                and isinstance(run, str) and isinstance(session, str)
                and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', run)
                and re.fullmatch(r'ses_[A-Za-z0-9_-]{1,128}', session)):
            if len(self.sessions) < 64: self.sessions.add((run, session))
            return None
        if (d.get('level') == 'ERROR' and d.get('message') == 'stream error'
                and (run, d.get('session.id')) in self.sessions
                and d.get('providerID') == self.provider and d.get('modelID') == self.model
                and RATE.search(d.get('error.error', ''))):
            return 'rate_limited'
        return None

    def public_error(self, line):
        try: event = json.loads(line)
        except ValueError: return None
        if not isinstance(event, dict) or event.get('type') != 'error': return None
        if not any(sid == event.get('sessionID') for _, sid in self.sessions): return None
        err = event.get('error')
        if not isinstance(err, dict): return None
        data = err.get('data', {})
        if not isinstance(data, dict): return None
        if data.get('statusCode') == 429 or RATE.search(str(data.get('message', ''))):
            return 'rate_limited'
        return None


def write_receipt(path, data):
    path = Path(path)
    if path.is_symlink(): raise ValueError('receipt must not be a symlink')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=True, sort_keys=True); f.write('\n')
            f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0: raise ValueError('positive finite budget required')
    return number


def finite_deadline(value):
    if value in (None, '', 'none'): return None
    number = float(value)
    if not math.isfinite(number) or number < 0: raise ValueError('invalid deadline')
    return number


def deadline_reason(job, stage, now=None):
    now = monotonic() if now is None else now
    if job is not None and now >= job: return 'job_deadline_exhausted'
    if stage is not None and now >= stage: return 'stage_deadline_exhausted'
    return ''


def stop_owned(proc):
    # start_new_session makes this process group exclusive to this invocation.
    try: os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: proc.wait(timeout=.25)
    except subprocess.TimeoutExpired: pass
    # A finished leader can still have children holding pipes / running tools.
    try: os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError: pass
    try: proc.wait(timeout=1)
    except subprocess.TimeoutExpired: pass


def execute(args):
    start = monotonic()
    job = finite_deadline(args.job_deadline)
    stage = finite_deadline(args.stage_deadline)
    call = start + positive(args.timeout)
    parser = NativeEvents(os.getcwd(), args.model)
    proc = None; interrupted = [0]; rc = 1; reason = 'cli_failure'
    prior = {}; selector = selectors.DefaultSelector(); buffers = {}; discarded = {}; output = 0
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            prior[sig] = signal.signal(sig, lambda signum, frame: interrupted.__setitem__(0, signum))
        if remaining(call, job, stage) <= 0:
            reason = 'job_deadline_exhausted' if job is not None and job <= monotonic() else 'stage_deadline_exhausted'
            rc = DEADLINE
        else:
            command = args.command[1:] if args.command[:1] == ['--'] else args.command
            if not command: raise ValueError('command is required')
            proc = subprocess.Popen(command, stdin=sys.stdin, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
            for channel, stream in (('stdout', proc.stdout), ('stderr', proc.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, channel)
                buffers[channel] = b''; discarded[channel] = False
            while True:
                now = monotonic()
                if interrupted[0]:
                    rc = 128+interrupted[0]; reason = 'interrupted'; break
                if remaining(call, job, stage, now=now) <= 0:
                    if job is not None and now >= job:
                        rc = DEADLINE; reason = 'job_deadline_exhausted'
                    elif stage is not None and now >= stage:
                        rc = DEADLINE; reason = 'stage_deadline_exhausted'
                    else:
                        rc = 124; reason = 'call_timeout'
                    break
                detected = None
                for key, _ in selector.select(timeout=min(.05, remaining(call, job, stage))):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    channel = key.data
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    # Preserve generated output. Native diagnostics are metadata only;
                    # do not copy error bodies, request URLs, headers or prompts.
                    if channel == 'stdout' and output < OUTPUT_LIMIT:
                        part = chunk[:OUTPUT_LIMIT-output]
                        try: os.write(sys.stdout.fileno(), part)
                        except BrokenPipeError: pass
                        output += len(part)
                    if not args.opencode_events: continue
                    combined = buffers[channel] + chunk
                    lines = combined.split(b'\n'); buffers[channel] = lines.pop()
                    for raw in lines:
                        if discarded[channel]: discarded[channel] = False; continue
                        if len(raw) > BUFFER_LIMIT: continue
                        text = raw.decode('utf-8', errors='replace').rstrip('\r')
                        found = parser.feed(text) if channel == 'stderr' else parser.public_error(text)
                        if found: detected = found
                    if len(buffers[channel]) > BUFFER_LIMIT:
                        buffers[channel] = b''; discarded[channel] = True
                if detected:
                    rc = RATE_LIMITED; reason = detected; break
                ended = proc.poll()
                if ended is not None and not selector.get_map():
                    # A file written late must not turn a deadline into success.
                    if remaining(call, job, stage) <= 0: continue
                    rc = ended if ended >= 0 else 128-ended
                    reason = 'completed' if rc == 0 else 'cli_failure'
                    break
    finally:
        if proc is not None:
            stop_owned(proc)
            if proc.stdout: proc.stdout.close()
            if proc.stderr: proc.stderr.close()
        selector.close()
        for sig, handler in prior.items(): signal.signal(sig, handler)
        write_receipt(args.receipt, {
            'schema': 'improve-command/v1', 'reason': reason, 'exit_code': rc,
            'model': args.model, 'pid': proc.pid if proc else None,
            'elapsed_seconds': round(monotonic()-start, 4),
            'configured_call_timeout': float(args.timeout),
            'label': args.label[:100], 'configured_primary_attempts': args.primary_limit[:8],
            'initial_job_remaining': None if job is None else max(0, round(job-start, 4)),
            'initial_stage_remaining': None if stage is None else max(0, round(stage-start, 4)),
            'native_protocol': 'opencode-1.18.27-logfmt/v1' if args.opencode_events else None,
            'session_bindings': len(parser.sessions),
        })
        print(f'\n[IMPROVE_GUARD] reason={reason} rc={rc}', file=sys.stderr)
    return rc


def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='action',required=True)
    clock=sub.add_parser('deadline');clock.add_argument('--seconds',required=True)
    init=sub.add_parser('init');init.add_argument('--seconds',required=True);init.add_argument('--analysis-seconds');init.add_argument('--max-seconds')
    check=sub.add_parser('check');check.add_argument('--job-deadline');check.add_argument('--stage-deadline')
    queue=sub.add_parser('queue-limit');queue.add_argument('--job-deadline');queue.add_argument('--stage-deadline');queue.add_argument('--limit',default='180')
    run=sub.add_parser('run');run.add_argument('--timeout',required=True);run.add_argument('--receipt',required=True)
    run.add_argument('--model',required=True);run.add_argument('--job-deadline');run.add_argument('--stage-deadline')
    run.add_argument('--label',default='');run.add_argument('--primary-limit',default='1')
    run.add_argument('--opencode-events',action='store_true');run.add_argument('command',nargs=argparse.REMAINDER)
    args=ap.parse_args()
    try:
        if args.action=='deadline': print(monotonic()+positive(args.seconds));return 0
        if args.action=='init':
            total=positive(args.seconds);analysis=positive(args.analysis_seconds) if args.analysis_seconds else total/2
            if args.max_seconds and total > positive(args.max_seconds): raise ValueError('one-job budget cannot increase configured ceiling')
            if analysis >= total: raise ValueError('analysis must leave time for implementation')
            now=monotonic();print(now+total,now+analysis);return 0
        if args.action=='queue-limit':
            job=finite_deadline(args.job_deadline);stage=finite_deadline(args.stage_deadline)
            rem=remaining(None,job,stage);limit=float(args.limit)
            if not math.isfinite(limit) or limit < 0: raise ValueError('invalid queue limit')
            if rem < 1: print(deadline_reason(job,stage) or 'queue_budget_exhausted');return DEADLINE
            cap=min(rem,limit if limit > 0 else 180)
            print(int(cap));return 0
        if args.action=='check':
            reason=deadline_reason(finite_deadline(args.job_deadline),finite_deadline(args.stage_deadline))
            if reason: print(reason);return DEADLINE
            return 0
        return execute(args)
    except (OSError,ValueError) as exc:
        print(f'[IMPROVE_GUARD] refused: {type(exc).__name__}',file=sys.stderr);return 81

if __name__=='__main__':raise SystemExit(main())
