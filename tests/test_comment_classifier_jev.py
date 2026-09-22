import copy
import io
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

from lib import comment_classifier_jev as jev
from lib import comment_classifier_jev_report as report

ROOT = Path(__file__).resolve().parents[1]


def row(text='BGM聞こえない？', category='chitchat', index=1, user='viewer'):
    return {'index': index, 'user': user, 'comment': text, 'category': category, 'is_english': False}


def response(request, choices=None, confidence=.9):
    choices = choices or ['stream_bug_report'] * len(request['questions'])
    return {'model': request['model'], 'usage': {'input_tokens': 1000, 'output_tokens': 0},
            'answers': {key: {'type': 'choice', 'choice': choice, 'confidence': confidence,
                              'probabilities': {c: .95 if c == choice else .05 / 13 for c in jev.CRITERIA}}
                        for key, choice in zip(request['questions'], choices)}}


class JevTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.rows = [row()]
        self.request = jev.build_request(self.rows, jev.Config().model)
        self.transport = Mock(side_effect=lambda req, key, timeout: {'status': 'ok', 'data': response(req)})

    def classify(self, rows=None, key='test-key', config=None, transport=None):
        return jev.classify(self.rows if rows is None else rows, config or jev.Config(), key,
                            self.directory, transport=transport or self.transport)

    def test_input_is_projection_not_context(self):
        source = row('nextNext: ソ連ゲームについて？', user='PRIVATE_NAME')
        source.update(persona='PRIVATE_PERSONA', game='PRIVATE_GAME', history='PRIVATE_HISTORY',
                      category='sing_request', is_english=True)
        request = jev.build_request([source], jev.Config().model)
        self.assertEqual(request, jev.build_request([{'comment': source['comment']}], jev.Config().model))
        self.assertEqual(request['state'], {'comments': [{'index': 1, 'text': source['comment']}]})
        self.assertNotIn('PRIVATE', jev.dumps(request))
        self.assertIn('index=1', request['questions']['c1']['instructions'])

    def test_criteria_match_canonical_prompt(self):
        prompt = (ROOT / 'prompts/comment_classifier.md').read_text(encoding='utf-8')
        categories = re.findall(r'^- ([a-z_]+):', prompt, re.MULTILINE)
        self.assertTrue(categories)
        self.assertEqual(len(categories), len(set(categories)))
        self.assertEqual(set(categories), set(jev.CRITERIA))
        self.assertEqual(jev.NOTIFICATIONS,
                         {'card_gacha', 'raid', 'subscription', 'stream_goal', 'bits'})
        self.assertLessEqual(jev.NOTIFICATIONS, set(categories))
        workflow = (ROOT / '.github/workflows/comment-classifier-jev.yml').read_text()
        for event in ('pull_request', 'push'):
            paths = workflow.split('  ' + event + ':', 1)[1].split('\npermissions:', 1)[0]
            paths = paths.split('\n  push:', 1)[0]
            self.assertIn("      - 'prompts/comment_classifier.md'", paths)

    def test_success_only_changes_category(self):
        output, event = self.classify()
        self.assertEqual(output, [{**self.rows[0], 'category': 'stream_bug_report'}])
        self.assertEqual(self.rows[0]['category'], 'chitchat')
        self.assertEqual(event['rows'][0]['status'], 'jev')
        self.assertAlmostEqual(event['estimated_usd'], .000042)
        self.assertNotIn('BGM', jev.dumps(event))
        self.assertNotIn('test-key', jev.dumps(event))

    def test_per_row_confidence_fallback(self):
        rows = [row(index=1), row('右に置いた方がよくない？', index=2)]
        def transport(req, *_):
            value = response(req, ['stream_bug_report', 'strategy_advice'])
            value['answers']['c2']['confidence'] = .2
            return {'status': 'ok', 'data': value}
        output, event = self.classify(rows, transport=transport)
        self.assertEqual(output[0]['category'], 'stream_bug_report')
        self.assertEqual(output[1], rows[1])
        self.assertEqual(event['rows'][1]['status'], 'low_confidence')

    def test_local_notifications_not_sent_or_overridden(self):
        rows = [row(category='card_gacha', user='PRIVATE_BOT'),
                row(index=2, user='Nightbot'), row(index=3)]
        output, event = self.classify(rows)
        self.assertEqual(output[:2], rows[:2])
        self.assertEqual(output[2]['category'], 'stream_bug_report')
        sent = self.transport.call_args.args[0]
        self.assertEqual(len(sent['state']['comments']), 1)
        self.assertNotIn('PRIVATE_BOT', jev.dumps(sent))
        self.assertEqual(event['rows'][0]['status'], 'local_notification')

    def test_model_cannot_create_platform_notification(self):
        for category in jev.NOTIFICATIONS:
            with self.subTest(category=category):
                transport = lambda req, *_: {'status': 'ok', 'data': response(req, [category])}
                output, event = self.classify(transport=transport)
                self.assertEqual(output, self.rows)
                self.assertEqual(event['rows'][0]['status'], 'unconfirmed_notification')

    def test_missing_key_no_network(self):
        for key in ('', 'bad\nkey', 'nonasciiあ'):
            output, event = self.classify(key=key)
            self.assertEqual(output, self.rows)
            self.assertFalse(event['attempted'])
        self.transport.assert_not_called()

    def test_failures_direct_fallback_and_unknown_usage(self):
        for reason in jev.COOLDOWNS:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                output, event = jev.classify(self.rows, jev.Config(), 'test-key', Path(temp),
                                             transport=lambda *_: {'status': reason})
                self.assertEqual(output, self.rows)
                self.assertEqual(event['status'], reason)
                self.assertTrue(event['attempted'])
                self.assertIsNone(event['usage'])
                self.assertIsNone(event['estimated_usd'])

    def test_bad_mapping_falls_back_entire_batch(self):
        def transport(req, *_):
            value = response(req)
            value['answers']['wrong'] = value['answers'].pop('c1')
            return {'status': 'ok', 'data': value}
        output, event = self.classify(transport=transport)
        self.assertEqual(output, self.rows)
        self.assertEqual(event['status'], 'invalid_response')

    def test_strict_json_rejects_duplicate_and_nonfinite(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                jev.strict_json(raw)

    def test_response_schema_rejects_invalid_answers(self):
        original = response(self.request)
        mutations = [lambda d: d['answers']['c1'].update(type='score'),
                     lambda d: d['answers']['c1'].update(choice='unknown'),
                     lambda d: d['answers']['c1'].update(confidence=float('nan')),
                     lambda d: d['answers']['c1'].update(confidence=True),
                     lambda d: d['answers']['c1'].update(confidence=2),
                     lambda d: d['answers']['c1']['probabilities'].update(chitchat=-.1),
                     lambda d: d['answers']['c1']['probabilities'].update(chitchat=.99),
                     lambda d: d['answers']['c1']['probabilities'].pop('other'),
                     lambda d: d['answers']['c1'].update(choice='chitchat'),
                     lambda d: d.update(model='SECRET_MODEL_NAME'),
                     lambda d: d['usage'].update(input_tokens=True),
                     lambda d: d['answers'].update(extra=d['answers']['c1'])]
        for mutate in mutations:
            value = copy.deepcopy(original)
            mutate(value)
            with self.assertRaises(ValueError):
                jev.validate_response(value, self.request)

    def test_bad_configuration_rejected(self):
        for values in ({'timeout_ms': 0}, {'timeout_ms': 5001}, {'timeout_ms': True},
                       {'min_confidence': float('nan')}, {'min_confidence': -1},
                       {'model': 'https://elsewhere.example'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                jev.Config(**values)

    def test_limits_keep_excess_on_heuristic(self):
        rows = [row(index=i) for i in range(1, 12)]
        rows[0]['comment'] = 'あ' * 5000
        output, event = self.classify(rows)
        self.assertEqual(output[0], rows[0])
        self.assertEqual(event['eligible_count'], 8)
        self.assertEqual(output[-1], rows[-1])
        self.assertLessEqual(len(jev.dumps(self.transport.call_args.args[0]).encode()), jev.MAX_REQUEST_BYTES)

    def test_cooldown_survives_new_call(self):
        first = Mock(return_value={'status': 'rate_limited', 'retry_after': 120})
        self.classify(transport=first)
        output, event = self.classify()
        self.assertEqual(output, self.rows)
        self.assertEqual(event['status'], 'cooldown')
        self.assertFalse(event['attempted'])
        self.transport.assert_not_called()
        self.assertGreater(json.loads((self.directory / 'gate.json').read_text())['until'], time.time() + 100)

    def test_busy_is_nonblocking_and_no_network(self):
        with jev.locked_file(self.directory / 'gate.json'):
            _, event = self.classify()
        self.assertEqual(event['status'], 'busy')
        self.transport.assert_not_called()

    def test_state_symlink_is_not_followed(self):
        target = self.directory / 'target'
        target.write_text('untouched')
        (self.directory / 'gate.json').symlink_to(target)
        _, event = self.classify()
        self.assertEqual(event['status'], 'state_unavailable')
        self.assertEqual(target.read_text(), 'untouched')
        self.transport.assert_not_called()

    def test_timeout_reaps_the_actual_process(self):
        processes, real_popen = [], subprocess.Popen
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        started = time.monotonic()
        with patch.object(jev.subprocess, 'Popen', side_effect=spawn), self.assertRaises(subprocess.TimeoutExpired):
            jev.bounded_process([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=.1)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNotNone(processes[0].returncode)
        with self.assertRaises(ChildProcessError):
            os.waitpid(processes[0].pid, os.WNOHANG)

    @unittest.skipUnless(sys.platform.startswith('linux') or sys.platform == 'darwin',
                         'requires Linux/macOS POSIX process groups and selectable pipes')
    def test_parent_signals_reap_detached_child_even_during_creation(self):
        code = textwrap.dedent('''
            import os, subprocess, sys
            from lib import comment_classifier_jev as jev
            real_popen, children = subprocess.Popen, []
            def barrier(process):
                print(process.pid, os.getpgid(process.pid), flush=True)
                assert sys.stdin.buffer.read(1) == b'x'
            def spawn(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                children.append(process)
                if sys.argv[1] == 'spawn':
                    barrier(process)
                else:
                    real_communicate = process.communicate
                    def communicate(*args, **kwargs):
                        process.communicate = real_communicate
                        barrier(process)
                        return real_communicate(*args, **kwargs)
                    process.communicate = communicate
                return process
            jev.subprocess.Popen = spawn
            try:
                jev.bounded_process([sys.executable, '-c',
                                     'import time; time.sleep(30)'], timeout=20)
            finally:
                child = children[0]
                assert child.returncode is not None, 'child was not reaped'
                try:
                    os.waitpid(child.pid, os.WNOHANG)
                except ChildProcessError:
                    print('reaped', flush=True)
                else:
                    raise AssertionError('child still waitable')
        ''')
        for signum in (signal.SIGTERM, signal.SIGINT):
            for phase in ('spawn', 'communicate'):
                with self.subTest(signum=signum, phase=phase):
                    parent = subprocess.Popen([sys.executable, '-c', code, phase], cwd=ROOT,
                                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE, start_new_session=True)
                    child_pid = None
                    try:
                        ready, _, _ = select.select([parent.stdout], [], [], 5)
                        self.assertTrue(ready, 'parent did not reach child-created barrier')
                        child_pid, group = map(int, parent.stdout.readline().split())
                        self.assertEqual(child_pid, group)
                        self.assertNotEqual(group, parent.pid)
                        parent.send_signal(signum)
                        out, err = parent.communicate(b'x', timeout=5)
                        self.assertEqual(parent.returncode, 128 + signum, err.decode())
                        self.assertEqual(out.strip(), b'reaped')
                        with self.assertRaises(ProcessLookupError):
                            os.kill(child_pid, 0)
                        with self.assertRaises(ProcessLookupError):
                            os.killpg(group, 0)
                    finally:
                        # A failing regression must not leave its sleeper behind.
                        if child_pid is not None:
                            try:
                                os.killpg(child_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        if parent.poll() is None:
                            parent.kill()
                        parent.communicate(timeout=5)

    def test_handlers_restored_after_success_spawn_failure_and_cancellation(self):
        originals = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        custom = lambda *_: None
        try:
            for signum in originals:
                signal.signal(signum, custom)
            self.assertEqual(jev.bounded_process([sys.executable, '-c', 'print("ok")']), b'ok\n')
            for signum in originals:
                self.assertIs(signal.getsignal(signum), custom)
            with patch.object(jev.subprocess, 'Popen', side_effect=OSError('spawn failed')):
                with self.assertRaises(OSError):
                    jev.bounded_process(['unused'])
            for signum in originals:
                self.assertIs(signal.getsignal(signum), custom)
            for error in (KeyboardInterrupt(), SystemExit(7), subprocess.TimeoutExpired('test', .1)):
                process = Mock(returncode=None)
                process.communicate.side_effect = [error, (b'', b'')]
                with patch.object(jev.subprocess, 'Popen', return_value=process), \
                        patch.object(jev.os, 'killpg') as killpg, \
                        patch.object(jev.time, 'monotonic', side_effect=[0, 0, 1]):
                    with self.assertRaises(type(error)):
                        jev.bounded_process(['unused'], timeout=.1)
                    killpg.assert_called_once_with(process.pid, signal.SIGKILL)
                    self.assertEqual(process.communicate.call_count, 2)
                for signum in originals:
                    self.assertIs(signal.getsignal(signum), custom)
        finally:
            for signum, handler in originals.items():
                signal.signal(signum, handler)

    def test_non_main_thread_fails_before_spawning(self):
        errors = []
        def run():
            try:
                jev.bounded_process(['unused'])
            except ValueError as exc:
                errors.append(exc)
        with patch.object(jev.subprocess, 'Popen') as spawn:
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            spawn.assert_not_called()

    def test_http_worker_transport_and_fixed_endpoint(self):
        mock_response = Mock()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_response.status = 200
        mock_response.read.return_value = jev.dumps(response(self.request)).encode()
        opener = Mock()
        opener.open.return_value = mock_response
        with patch.object(jev.urllib.request, 'build_opener', return_value=opener):
            result = jev.http_worker(self.request, 'test-key', .1)
        self.assertEqual(result['status'], 'ok')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, jev.ENDPOINT)
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
        self.assertEqual(json.loads(request.data), self.request)

    def test_http_failures_never_leak_body(self):
        for code, expected in ((401, 'auth_error'), (403, 'auth_error'), (429, 'rate_limited'),
                               (529, 'overloaded'), (503, 'server_error'), (302, 'http_error')):
            error = urllib.error.HTTPError(jev.ENDPOINT, code, 'SECRET_DETAIL', {'Retry-After': '45'},
                                            io.BytesIO(b'SECRET_BODY'))
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(jev.urllib.request, 'build_opener', return_value=opener):
                result = jev.http_worker(self.request, 'SECRET_KEY', .1)
            self.assertEqual(result['status'], expected)
            self.assertNotIn('SECRET', jev.dumps(result))

    def test_network_timeout_and_malformed_response(self):
        for error, expected in ((socket.timeout('SECRET'), 'timeout'),
                                (urllib.error.URLError('SECRET'), 'network_error')):
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(jev.urllib.request, 'build_opener', return_value=opener):
                self.assertEqual(jev.http_worker(self.request, 'k', .1)['status'], expected)

    def test_metrics_rotation_failure_and_redaction(self):
        _, event = self.classify([row('PRIVATE_TEXT', user='PRIVATE_USER')])
        directory = self.directory / 'logs'
        with patch.object(jev, 'LOG_BYTES', len(jev.dumps(event).encode()) + 2):
            for _ in range(3):
                jev.append_metrics(event, directory)
        self.assertEqual(len(list(directory.glob('metrics-*.jsonl.1'))), 1)
        self.assertNotIn('PRIVATE', next(directory.glob('metrics-*.jsonl')).read_text())
        with patch.object(jev, 'locked_file', side_effect=OSError('SECRET')):
            jev.append_metrics(event, directory)

    def test_report_denominators_and_abstentions(self):
        _, success = self.classify()
        _, missing = self.classify(key='')
        success.update(heuristic_ms=1, classification_ms=20)
        missing.update(heuristic_ms=2, classification_ms=2)
        result = report.summarize([success, missing])
        self.assertEqual(result['comments'], 2)
        self.assertEqual(result['requests_attempted'], 1)
        self.assertEqual(result['jev_coverage'], .5)
        self.assertEqual(result['agreement_sample_count'], 1)
        self.assertEqual(result['latency_ms']['classification_ms']['p95'], 20)
        scored = report.score_predictions([('chitchat', 'chitchat'), ('stream_bug_report', None)])
        self.assertEqual(scored['coverage'], .5)
        self.assertEqual(scored['correct_fraction_all'], .5)
        self.assertEqual(scored['accuracy_on_available'], 1)

    def test_request_process_key_not_in_argv_or_payload(self):
        with patch.object(jev, 'bounded_process', side_effect=subprocess.TimeoutExpired('hidden', .1)) as process:
            result = jev.request_once(self.request, 'PRIVATE_KEY', .1)
        self.assertEqual(result['status'], 'timeout')
        args, kwargs = process.call_args
        self.assertNotIn('PRIVATE_KEY', str(args))
        self.assertNotIn(b'PRIVATE_KEY', kwargs['data'])
        self.assertEqual(set(kwargs['env']), {'TYPESAFE_API_KEY', 'LANG'})
        self.assertEqual(kwargs['env']['TYPESAFE_API_KEY'], 'PRIVATE_KEY')

    # -- docich semantic-decision delegation (azumag/docich#882) -----------

    def test_docich_transport_delegates_direct_route_and_rounded_timeout(self):
        core = Mock()
        core.request_once.return_value = {'status': 'ok', 'meta': {'route': 'direct'},
                                          'data': response(self.request)}
        with patch.dict(sys.modules, {'docich.semantic_decision.transport': core}):
            result = jev.docich_transport(self.request, 'PRIVATE_KEY', .1)
        core.request_once.assert_called_once_with(
            self.request, route='direct', env={'TYPESAFE_API_KEY': 'PRIVATE_KEY'}, timeout_ms=100)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['data'], response(self.request))

    def test_docich_transport_passes_through_known_failure_statuses(self):
        for status in jev.COOLDOWNS:
            core = Mock()
            core.request_once.return_value = {'status': status, 'retry_after': 12}
            with self.subTest(status=status), patch.dict(sys.modules, {'docich.semantic_decision.transport': core}):
                result = jev.docich_transport(self.request, 'k', .1)
            self.assertEqual(result, {'status': status, 'retry_after': 12})

    def test_docich_transport_rejects_unknown_status_shape(self):
        core = Mock()
        core.request_once.return_value = {'status': 'made_up_status'}
        with patch.dict(sys.modules, {'docich.semantic_decision.transport': core}):
            result = jev.docich_transport(self.request, 'k', .1)
        self.assertEqual(result, {'status': 'invalid_response'})

    def test_docich_transport_core_exception_never_leaks_and_is_invalid_response(self):
        core = Mock()
        core.request_once.side_effect = RuntimeError('SECRET_DETAIL')
        with patch.dict(sys.modules, {'docich.semantic_decision.transport': core}):
            result = jev.docich_transport(self.request, 'PRIVATE_KEY', .1)
        self.assertEqual(result, {'status': 'invalid_response'})
        self.assertNotIn('SECRET', jev.dumps(result))
        self.assertNotIn('PRIVATE_KEY', jev.dumps(result))

    def test_docich_transport_without_nested_docich_is_invalid_response_not_legacy_http(self):
        # This standalone checkout has no docich sibling tree; the adapter
        # must fail closed, never fall through to this file's own
        # request_once/http_worker/ENDPOINT.
        sys.modules.pop('docich.semantic_decision.transport', None)
        with patch.object(jev, 'bounded_process', side_effect=AssertionError('must not spawn legacy HTTP')):
            result = jev.docich_transport(self.request, 'k', .1)
        self.assertEqual(result, {'status': 'invalid_response'})

    def test_resolve_transport_env_flag_is_exact_and_unset_keeps_legacy(self):
        self.assertIs(jev._resolve_transport({'DOCICH_SEMANTIC_BACKEND': 'jev'}), jev.docich_transport)
        self.assertIs(jev._resolve_transport({}), jev.request_once)
        self.assertIs(jev._resolve_transport({'DOCICH_SEMANTIC_BACKEND': 'legacy'}), jev.request_once)
        self.assertIs(jev._resolve_transport({'DOCICH_SEMANTIC_BACKEND': 'Jev'}), jev.request_once)

    def test_run_default_unaffected_and_explicit_transport_still_wins(self):
        env = {'TYPESAFE_API_KEY': 'k', 'COMMENT_CLASSIFIER_JEV_LOG_ENABLED': '0',
               'COMMENT_CLASSIFIER_JEV_STATE_DIR': str(self.directory)}
        sentinel = Mock(return_value={'status': 'ok', 'data': response(self.request)})
        # No DOCICH_SEMANTIC_BACKEND: run() must still resolve this file's own
        # request_once via _resolve_transport, matching pre-#882 production.
        with patch.object(jev, 'baseline_from_file', return_value=self.rows), \
                patch.object(jev, '_resolve_transport', return_value=sentinel) as resolve:
            jev.run(self.directory / 'unused', env=env)
        resolve.assert_called_once_with(env)
        sentinel.assert_called_once()
        # An explicit transport= caller bypasses env resolution entirely.
        with patch.object(jev, 'baseline_from_file', return_value=self.rows), \
                patch.object(jev, '_resolve_transport') as resolve:
            jev.run(self.directory / 'unused', env=env, transport=sentinel)
        resolve.assert_not_called()

    def test_run_uses_docich_backend_only_when_flagged(self):
        core = Mock()
        core.request_once.return_value = {'status': 'ok', 'data': response(self.request)}
        env = {'TYPESAFE_API_KEY': 'k', 'DOCICH_SEMANTIC_BACKEND': 'jev',
               'COMMENT_CLASSIFIER_JEV_LOG_ENABLED': '0',
               'COMMENT_CLASSIFIER_JEV_STATE_DIR': str(self.directory)}
        with patch.object(jev, 'baseline_from_file', return_value=self.rows), \
                patch.dict(sys.modules, {'docich.semantic_decision.transport': core}), \
                patch.object(jev, 'bounded_process', side_effect=AssertionError('legacy HTTP must not run')):
            output, event = jev.run(self.directory / 'unused', env=env)
        self.assertEqual(output[0]['category'], 'stream_bug_report')
        self.assertEqual(event['status'], 'ok')
        core.request_once.assert_called_once()
        self.assertEqual(core.request_once.call_args.kwargs['route'], 'direct')
        self.assertEqual(core.request_once.call_args.kwargs['env'], {'TYPESAFE_API_KEY': 'k'})

    def test_body_stall_and_dns_stall_obey_wall_deadline(self):
        scripts = [
            'jev.socket.getaddrinfo=lambda *a, **k: time.sleep(30)',
            'class Response:\n status=200\n def __enter__(self): return self\n'
            ' def __exit__(self,*a): pass\n def read(self,n): time.sleep(30)\n'
            'class Opener:\n def open(self,*a,**k): return Response()\n'
            'jev.urllib.request.build_opener=lambda *a: Opener()',
        ]
        for setup in scripts:
            code = ('import sys,time; sys.path.insert(0, sys.argv[1]); '
                    'from lib import comment_classifier_jev as jev\n' + setup + '\n'
                    "jev.http_worker({'state':'test','questions':{},'model':'jev-1.13.0'}, 'test-key', 5)")
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                jev.bounded_process([sys.executable, '-c', code, str(ROOT)], timeout=.2)
            self.assertLess(time.monotonic() - started, 1.2)

    def test_metrics_date_retention_not_refreshable_mtime(self):
        directory = self.directory / 'logs'
        directory.mkdir()
        old = directory / 'metrics-2000-01-01.jsonl'
        old.write_text('{}\n')
        _, event = self.classify()
        jev.append_metrics(event, directory)
        self.assertFalse(old.exists())

    def test_cooldown_write_failure_does_not_hide_an_attempt(self):
        stream = io.StringIO('')
        original_write = stream.write
        stream.write = Mock(side_effect=OSError('SECRET'))
        from contextlib import contextmanager
        @contextmanager
        def locked(_):
            yield stream
        with patch.object(jev, 'locked_file', locked):
            output, event = self.classify()
        self.assertTrue(event['attempted'])
        self.assertEqual(event['status'], 'ok')
        self.assertIsNotNone(event['usage'])

    def test_run_records_baseline_and_never_exposes_invalid_setting(self):
        env = {'COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE': 'PRIVATE_INVALID',
               'COMMENT_CLASSIFIER_JEV_LOG_ENABLED': '0'}
        with patch.object(jev, 'baseline_from_file', return_value=self.rows):
            output, event = jev.run(self.directory / 'unused', env=env)
        self.assertEqual(output, self.rows)
        self.assertEqual(event['status'], 'invalid_config')
        self.assertIn('heuristic_ms', event)
        self.assertIn('classification_ms', event)
        self.assertNotIn('PRIVATE_INVALID', jev.dumps(event))

    def test_eval_requires_explicit_api_permission(self):
        result = subprocess.run([sys.executable, str(ROOT / 'lib/comment_classifier_jev_report.py'),
                                 '--evaluate', 'not-read.jsonl'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'requires --allow-api', result.stderr)


class ShellTests(unittest.TestCase):
    def shell(self, code, *args, env=None):
        return subprocess.run(['bash', '-c', code, 'jev-test', str(ROOT), *args],
                              text=True, capture_output=True, timeout=5,
                              env={**os.environ, 'TYPESAFE_API_KEY': '', **(env or {})})

    def test_disabled_legacy_exit_status_and_double_source(self):
        result = self.shell('''
            unset COMMENT_CLASSIFIER_BACKEND
            _classify_comments() { printf 'legacy'; return 17; }
            source "$1/broadcast/comment_classifier_jev.sh"
            source "$1/broadcast/comment_classifier_jev.sh"
            _classify_comments anything
        ''')
        self.assertEqual(result.stdout, 'legacy')
        self.assertEqual(result.returncode, 17)

    def test_rollback_explicit_empty_backend_and_restart_contract(self):
        doc = (ROOT / 'docs/comment_classifier_jev.md').read_text(encoding='utf-8')
        self.assertIn('`COMMENT_CLASSIFIER_BACKEND=`', doc)
        self.assertIn('対象workerを完全再起動', doc)
        self.assertIn('APIキー除去には再起動が必要', doc)
        with tempfile.TemporaryDirectory() as temp:
            removed, disabled = Path(temp) / 'removed.env', Path(temp) / 'disabled.env'
            removed.write_text('# Jev settings removed\n')
            disabled.write_text('COMMENT_CLASSIFIER_BACKEND=\n')
            result = self.shell('''
                export COMMENT_CLASSIFIER_BACKEND=jev TYPESAFE_API_KEY=dummy
                source "$2"
                printf '%s:%s\\n' "$COMMENT_CLASSIFIER_BACKEND" "$TYPESAFE_API_KEY"
                source "$3"
                printf '%s:%s\\n' "$COMMENT_CLASSIFIER_BACKEND" "$TYPESAFE_API_KEY"
                _classify_comments() { printf 'legacy'; }
                source "$1/broadcast/comment_classifier_jev.sh"
                _classify_comments unused
            ''', str(removed), str(disabled))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'jev:dummy\n:dummy\nlegacy')

    def test_full_reload_captures_fresh_base(self):
        result = self.shell('''
            unset COMMENT_CLASSIFIER_BACKEND
            _classify_comments() { printf 'old'; }
            source "$1/broadcast/comment_classifier_jev.sh"
            _classify_comments() { printf 'new'; }
            source "$1/broadcast/comment_classifier_jev.sh"
            _classify_comments
        ''')
        self.assertEqual(result.stdout, 'new')
        self.assertEqual(result.returncode, 0)

    def test_broken_adapter_goes_to_heuristic_even_with_old_ai_enabled(self):
        with tempfile.NamedTemporaryFile(mode='w') as source:
            source.write('viewer: hello\n')
            source.flush()
            result = self.shell('''
                export COMMENT_CLASSIFIER_BACKEND=jev COMMENT_CLASSIFIER_AI_ENABLED=1
                ELOOP_LIB_DIR=/nonexistent/jev-test
                _classify_comments() { printf 'MUST_NOT_ENTER_LEGACY_AI'; return 9; }
                _classify_comments_heuristic() { printf '[{"category":"chitchat"}]'; }
                _comment_normalize_classification_for_comments() { printf '%s' "$1"; }
                source "$1/broadcast/comment_classifier_jev.sh"
                _classify_comments "$2"
            ''', source.name)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [{'category': 'chitchat'}])
        self.assertNotIn('MUST_NOT', result.stdout + result.stderr)

    def test_loader_order(self):
        source = (ROOT / 'eloop_lib.sh').read_text()
        self.assertLess(source.index('broadcast/comment.sh'), source.index('broadcast/comment_classifier_jev.sh'))
        self.assertLess(source.index('broadcast/comment_classifier_jev.sh'), source.index('broadcast/comment_lib.sh'))

    @unittest.skipUnless((ROOT / 'broadcast/comment.sh').is_file(), 'canonical repository file needed (CI checkout)')
    def test_real_canonical_baseline_and_keyless_adapter_match(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'comments.txt'
            source.write_text('viewer: BGM聞こえない？\n\nviewer: Amazing stream!\n'
                              'viewer: nextNext: 右に置いた方がよくない？\n'
                              'Nightbot: raid incoming\n', encoding='utf-8')
            expected = jev.baseline_from_file(source)
            output, event = jev.run(source, env={'TYPESAFE_API_KEY': '', 'COMMENT_CLASSIFIER_JEV_LOG_ENABLED': '0'})
            self.assertEqual(output, expected)
            self.assertFalse(event['attempted'])
            result = self.shell('''
                export ELOOP_LIB_DIR="$1" COMMENT_CLASSIFIER_BACKEND=jev
                export COMMENT_CLASSIFIER_AI_ENABLED=1 COMMENT_CLASSIFIER_JEV_LOG_ENABLED=0
                source "$1/broadcast/comment.sh"
                source "$1/broadcast/comment_classifier_jev.sh"
                _classify_comments "$2"
            ''', str(source))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), expected)


if __name__ == '__main__':
    unittest.main()
