import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
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
        transport = lambda req, *_: {'status': 'ok', 'data': response(req, ['raid'])}
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
