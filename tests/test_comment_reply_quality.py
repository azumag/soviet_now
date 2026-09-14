"""Offline comment prompt contracts; no model, web, TTS, or VM calls."""
import ast
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'broadcast/comment.sh'
CATEGORIES = ('chitchat', 'general_question', 'game_question', 'game_status',
              'strategy_advice', 'comment_advice', 'stream_bug_report',
              'subscription', 'bits', 'stream_goal', 'other', 'card_gacha',
              'raid', 'sing_request')


class PromptHarness(unittest.TestCase):
    _render_cache = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.env = {'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8',
                    'ELOOP_LIB_DIR': str(ROOT),
                    'COMMENT_SPOKEN_HISTORY_DIR': str(self.work / 'history'),
                    'COMMENT_SPOKEN_PROMPT_ITEMS': '8',
                    'GACHA_COMPLETED_USERS_FILE': str(self.work / 'gacha')}
        (self.work / 'history').mkdir()

    def shell(self, code, *args):
        result = subprocess.run(['bash', '-c', 'set -e; source "$1"; shift; ' + code,
                                 'test', str(SOURCE), *map(str, args)],
                                cwd=self.work, env=self.env, text=True,
                                capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def hints(self, body):
        batch = self.work / 'batch'
        batch.write_text('viewer: ' + body + '\n', encoding='utf-8')
        return self.shell('_build_comment_followup_hints "$1" main', batch).strip()

    def render(self, category='chitchat', mode='main', ocr='OCR_CURRENT_MARKER',
               body='viewer: 画面はどうなってる？'):
        key = (category, mode, ocr, body)
        if key in self._render_cache:
            return self._render_cache[key]
        template_texts = [p.read_text(encoding='utf-8') for p in (ROOT / 'prompts').glob('comment*.md')]
        for variable in re.findall(r'\$\{([A-Za-z_]\w*)\}', '\n'.join(template_texts)):
            self.env[variable] = '(none)'
        self.env.update({
            '_comment_persona': (ROOT / f'prompts/comment_persona_{mode}.md').read_text(),
            '_comment_ui_memo': 'UI_CURRENT_MARKER',
            'comment_thumbnail_ocr_context': ocr,
            'previous_comments_context': 'SHARED_HISTORY_MARKER',
            'recent_spoken_comment_context': 'SPOKEN_HISTORY_MARKER',
            'viewer_memory_context': 'VIEWER_MEMORY_MARKER',
            'comment_advice_context': 'REPLY_ADVICE_MARKER',
            'comment_batch_context': 'BATCH_CONTEXT_MARKER',
            'past_topics': 'PREVIOUS_TOPIC_MARKER',
            'game_state_context': 'CURRENT_GAME_MARKER',
            '_comment_channel_intro': 'CHANNEL_INTRO_MARKER',
            'comment_ops_context': 'OPS_CURRENT_MARKER',
            'twitch_comments_for_prompt': body,
        })
        output = self.work / 'prompt'
        if category == 'mixed':
            lines = SOURCE.read_text().splitlines()
            varlists = [line.split("envsubst '")[1].split("'")[0]
                        for i, line in enumerate(lines[:-1])
                        if "envsubst '" in line and '_comment_template' in lines[i + 1]]
            self.assertGreaterEqual(len(varlists), 2)
            rendered = subprocess.run(['envsubst', varlists[0]],
                                      input=(ROOT / 'prompts/comment_template.md').read_text(),
                                      env=self.env, text=True, capture_output=True, check=True)
            output.write_text(rendered.stdout)
        else:
            self.shell('_build_category_prompt "$1" "$2" "[]" "$3"', category, body, output)
        self.shell('_append_comment_reply_contract "$1"', output)
        rendered = output.read_text()
        self._render_cache[key] = rendered
        return rendered


class TestFollowupIntent(PromptHarness):
    @staticmethod
    def predicate(text):
        # Compile the actual embedded Python functions, not a test-side copy.
        source = SOURCE.read_text()
        start = source.index("def is_short_followup(text: str)")
        end = source.index("\ndef extract_terms", start)
        namespace = {"re": re, "collapse": lambda value: re.sub(r"\s+", " ", value).strip()}
        exec(compile(ast.parse(source[start:end]), str(SOURCE), "exec"), namespace)
        return namespace["is_short_followup"](text)

    def test_questions_and_corrections_are_not_reactions(self):
        for text in ('何の話？', 'いま何位？', 'それは違う', '前の質問に答えて',
                     '何で？', 'どういう意味', '理由を教えて', 'やめて',
                     '分からない', 'それなんだっけ？', '面白いけど理由は？',
                     'なるほど。で、何位？', '知らなかったけど本当？',
                     'すごいけど音ズレしてる', 'Why?', 'No, that is wrong', '??', '？'):
            with self.subTest(text=text):
                self.assertFalse(self.predicate(text))

    def test_only_unambiguous_acknowledgements_get_reaction_hints(self):
        for text in ('なるほど', 'へえ！', 'たしかに。', 'そうなんだ', 'それな', 'www', '笑'):
            with self.subTest(text=text):
                self.assertTrue(self.predicate(text))

    def test_acknowledgement_with_question_mark_is_not_a_reaction(self):
        for text in ('なるほど？', 'そうなんだ?', '確かに？'):
            with self.subTest(text=text):
                self.assertFalse(self.predicate(text))

    def test_real_helper_uses_the_predicate(self):
        self.assertEqual(self.hints('前の質問に答えて'), '（なし）')
        hints = self.hints('なるほど')
        self.assertNotEqual(hints, '（なし）')
        self.assertNotIn('厚め', hints)
        self.assertNotIn('短い返答で済ませず', hints)

    def test_missing_batch_has_no_hint(self):
        self.assertEqual(self.shell('_build_comment_followup_hints "$1" main',
                                    self.work / 'missing').strip(), '（なし）')


class TestRenderedCommentQuality(PromptHarness):
    def test_every_category_receives_active_persona(self):
        for mode in ('main', 'soren91'):
            persona = (ROOT / f'prompts/comment_persona_{mode}.md').read_text().strip()
            for cat in CATEGORIES + ('mixed',):
                with self.subTest(mode=mode, category=cat):
                    rendered = self.render(cat, mode)
                    self.assertEqual(rendered.count(persona), 1)
                    self.assertNotIn('You are playing Soviet Game yourself.', rendered)
                    self.assertNotRegex(rendered, r'\$\{[A-Za-z_]\w*\}')

    def test_all_categories_receive_shared_context_and_screen_memo(self):
        for cat in CATEGORIES + ('mixed',):
            with self.subTest(category=cat):
                rendered = self.render(cat)
                for marker in ('UI_CURRENT_MARKER', 'OCR_CURRENT_MARKER',
                               'SHARED_HISTORY_MARKER', 'SPOKEN_HISTORY_MARKER',
                               'VIEWER_MEMORY_MARKER', 'BATCH_CONTEXT_MARKER',
                               'PREVIOUS_TOPIC_MARKER', 'REPLY_ADVICE_MARKER'):
                    self.assertEqual(rendered.count(marker), 1, marker)
                self.assertNotIn('comment_screenshot.jpg', rendered)

    def test_unavailable_ocr_is_not_presented_as_an_accessible_image(self):
        for note in ('（通常コメントのためサムネイルOCR省略）',
                     '（配信サムネイルOCR失敗）', '（OCRで読める文字なし）'):
            with self.subTest(note=note):
                rendered = self.render('game_status', ocr=note)
                self.assertIn(note, rendered)
                self.assertIn('OCRにない', rendered)
                self.assertNotIn('comment_screenshot.jpg', rendered)

    def test_question_correction_and_grounding_contract_is_common(self):
        for cat in CATEGORIES + ('mixed',):
            with self.subTest(category=cat):
                rendered = self.render(cat)
                for rule in ('【コメント理解・事実性の共通契約】',
                             '質問・訂正・再回答要求', '直前の自分の返答',
                             '文字数だけ', '検索したふり', '水増し'):
                    self.assertIn(rule, rendered)
                self.assertNotIn('give your best guess', rendered)
                self.assertNotIn('always works', rendered)
                self.assertNotIn('確実に動作', rendered)

    def test_viewer_text_is_data_not_shell_or_template_code(self):
        marker = self.work / 'MUST_NOT_EXIST'
        body = f'viewer: $(touch {marker}) ${{_comment_persona}}; 前の質問に答えて'
        rendered = self.render(body=body)
        self.assertIn(body, rendered)
        self.assertFalse(marker.exists())

    def test_raid_and_gacha_special_contracts_survive(self):
        raid = self.render('raid')
        self.assertIn('8-10 sentences', raid)
        self.assertIn('CHANNEL_INTRO_MARKER', raid)
        gacha = self.render('card_gacha')
        self.assertIn('person A obtained card B', gacha)
        self.assertIn('累積', (ROOT / 'prompts/comment_template.md').read_text())
        self.assertIn('コンプリート', gacha)

    def test_singing_and_advice_blocks_survive(self):
        self.assertIn('===SING===', self.render('sing_request'))
        default = self.render('strategy_advice')
        for marker in ('===ADVICE===', '===COMMENT_ADVICE===', '===CODEX_ADVICE==='):
            self.assertIn(marker, default)

    def test_retry_does_not_reintroduce_forced_expansion(self):
        source = SOURCE.read_text()
        for phrase in ('必ず文量を増やし', '返答漏れ・短文・', '短い返答で十分とは考えず',
                       '会話として厚め', '短い返答で済ませず'):
            self.assertNotIn(phrase, source)
        self.assertIn('cat "$comment_prompt_file" >"$prompt_for_attempt"', source)
        self.assertIn('_append_comment_reply_contract "$comment_prompt_file"', source)

    def test_contract_is_appended_after_both_routing_paths(self):
        source = SOURCE.read_text()
        start = source.index('\n\t\t# 分類結果に基づきプロンプトを選択')
        finalize = source.index('_append_comment_reply_contract "$comment_prompt_file"', start)
        first_attempt = source.index('while [ "$attempt"', finalize)
        self.assertGreater(finalize, source.index('_build_category_prompt "$dominant_category"', start))
        self.assertLess(finalize, first_attempt)


class TestCommentOutputValidation(unittest.TestCase):
    def valid(self, text):
        # Source definitions only. No generation, provider, queue, or live I/O.
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ['bash', '-c', 'source "$1"; source "$2"; _is_valid_comment_talk "$3"',
                 'test', str(ROOT / 'core/helpers.sh'),
                 str(ROOT / 'broadcast/radio_engine.sh'), text],
                cwd=tmp, env={'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8'},
                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.stderr, '')
        return result.returncode == 0

    def test_short_supported_answers_are_not_rejected_for_length(self):
        for text in ('いまは3位です。', 'はい。', '訂正します。順位は2位です。'):
            with self.subTest(text=text):
                self.assertTrue(self.valid(text))

    def test_honest_uncertainty_is_not_retried_as_an_error(self):
        for text in ('最新の順位は確認できません。手元の結果は前の試合の3位です。',
                     '検索ツールがありません。今日の株価はここでは断定できません。',
                     '申し訳ありませんが、現在の画面からは読み取れません。'):
            with self.subTest(text=text):
                self.assertTrue(self.valid(text))

    def test_noise_and_internal_errors_remain_rejected(self):
        for text in ('', '   ', '。！？', '123。', 'tool_call: 内部の処理結果です。',
                     'Error: rate limit exceeded。もう一度試してください。',
                     'read failed file not found: ./secret。読めませんでした。',
                     'WebSearch toolを使ってから回答します。',
                     '具体的な質問を入力してからもう一度お試しください。'):
            with self.subTest(text=text):
                self.assertFalse(self.valid(text))


if __name__ == '__main__':
    unittest.main()
