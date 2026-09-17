"""Offline contracts for explicit defaults; never source a production .env."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'opencode-go:union-alpha,openrouter:stealth/union-alpha,'


class ExplicitChainConfigTests(unittest.TestCase):
    def config(self, assignments=''):
        script = assignments + '\nsource core/config.sh\n'
        keys = ('AI_COMMON_AGENTS', 'MODEL_IMPROVE_LIST', 'PEAK_HOURS_AGENT_PREFERENCE',
                'RADIO_AGENTS', 'COMMENT_AGENTS', 'MODEL_IMPROVE_PEAK_LIST',
                'IMPROVE_PEAK_CHAIN_ENABLED', 'COMMENT_CLASSIFIER_AI_ENABLED',
                'COMMENT_CLASSIFIER_AGENTS', 'COMMENT_CLASSIFIER_EDIT_AGENTS',
                'RADIO_FACT_CHECK_AGENTS', 'RADIO_JIJI_RESEARCH_AGENTS',
                'COMMENT_TRANSLATION_MAX_ATTEMPTS')
        for key in keys:
            script += f'printf "%s=%s\\n" {key} "${{{key}}}"\n'
        result = subprocess.run([shutil.which('bash'), '-c', script], cwd=ROOT,
                                env={'PATH': os.defpath, 'HOME': str(ROOT)},
                                capture_output=True, text=True, check=True)
        return dict(line.split('=', 1) for line in result.stdout.splitlines())

    def test_defaults_and_inheritance(self):
        values = self.config()
        for key in ('AI_COMMON_AGENTS', 'MODEL_IMPROVE_LIST', 'PEAK_HOURS_AGENT_PREFERENCE',
                    'RADIO_AGENTS', 'COMMENT_AGENTS', 'COMMENT_CLASSIFIER_AGENTS',
                    'COMMENT_CLASSIFIER_EDIT_AGENTS', 'RADIO_FACT_CHECK_AGENTS',
                    'RADIO_JIJI_RESEARCH_AGENTS'):
            with self.subTest(key=key):
                self.assertTrue(values[key].startswith(PREFIX), values[key])
                self.assertEqual(values[key].count('opencode-go:union-alpha'), 1)
                self.assertEqual(values[key].count('openrouter:stealth/union-alpha'), 1)

    def test_explicit_overrides_are_not_rewritten(self):
        values = self.config('AI_COMMON_AGENTS=local\nMODEL_IMPROVE_LIST=opencode:custom\n'
                             'RADIO_AGENTS=opencode:radio\nPEAK_HOURS_AGENT_PREFERENCE=local')
        self.assertEqual(values['AI_COMMON_AGENTS'], 'local')
        self.assertEqual(values['COMMENT_AGENTS'], 'local')
        self.assertEqual(values['RADIO_AGENTS'], 'opencode:radio')
        self.assertEqual(values['MODEL_IMPROVE_LIST'], 'opencode:custom')
        self.assertEqual(values['PEAK_HOURS_AGENT_PREFERENCE'], 'local')

    def test_translation_cap_config_default_and_override(self):
        self.assertEqual(self.config()['COMMENT_TRANSLATION_MAX_ATTEMPTS'], '4')
        self.assertEqual(self.config('COMMENT_TRANSLATION_MAX_ATTEMPTS=2')
                         ['COMMENT_TRANSLATION_MAX_ATTEMPTS'], '2')
        self.assertEqual(self.config('COMMENT_TRANSLATION_MAX_ATTEMPTS=')
                         ['COMMENT_TRANSLATION_MAX_ATTEMPTS'], '4')

    def test_empty_auxiliary_chains_keep_legacy_parent_fallback(self):
        keys = ('RADIO_FACT_CHECK_AGENTS', 'RADIO_JIJI_RESEARCH_AGENTS',
                'COMMENT_CLASSIFIER_AGENTS', 'COMMENT_CLASSIFIER_EDIT_AGENTS')
        values = self.config('\n'.join(f'{key}=' for key in keys))
        for key in keys:
            self.assertEqual(values[key], '')

    def test_disabled_features_stay_disabled(self):
        values = self.config()
        self.assertEqual(values['MODEL_IMPROVE_PEAK_LIST'], '')
        self.assertEqual(values['IMPROVE_PEAK_CHAIN_ENABLED'], '0')
        self.assertEqual(values['COMMENT_CLASSIFIER_AI_ENABLED'], '0')

    def test_date_does_not_change_explicit_configuration(self):
        self.assertEqual(self.config('AI_PRIORITY_NOW_EPOCH=0'),
                         self.config('AI_PRIORITY_NOW_EPOCH=2000000000'))


if __name__ == '__main__':
    unittest.main()
