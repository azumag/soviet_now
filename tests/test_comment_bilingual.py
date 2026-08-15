import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import comment_bilingual  # noqa: E402


class TestEnglishCommentDetection(unittest.TestCase):
    def test_detects_english_body_without_counting_viewer_name(self):
        self.assertTrue(
            comment_bilingual.batch_has_english(
                ["EnglishViewer: Hello, how is the game going?"]
            )
        )
        self.assertFalse(
            comment_bilingual.batch_has_english(["EnglishViewer: こんにちは！"])
        )
        self.assertFalse(comment_bilingual.batch_has_english(["viewer: GG!"]))
        self.assertTrue(comment_bilingual.batch_has_english(["viewer: hi"]))
        self.assertTrue(comment_bilingual.batch_has_english(["viewer: Hello there!"]))
        self.assertTrue(comment_bilingual.batch_has_english(["viewer: Amazing stream!"]))
        self.assertTrue(comment_bilingual.batch_has_english(["viewer: Well played!"]))
        self.assertFalse(
            comment_bilingual.batch_has_english(["viewer: LUL PogChamp LUL PogChamp"])
        )
        self.assertFalse(
            comment_bilingual.batch_has_english(
                ["tonkararin: dociaiDoci dociaiDoci dociaiDoci"]
            )
        )

    def test_does_not_treat_urls_or_mixed_japanese_as_english(self):
        self.assertFalse(
            comment_bilingual.batch_has_english(
                ["viewer: https://example.com", "viewer: Sorenはgoodです"]
            )
        )


class TestBilingualResponseParser(unittest.TestCase):
    def test_parses_plain_english_reply_then_japanese_translation(self):
        parsed = comment_bilingual.parse_response(
            "Comrade Alex, the game is going well.\n\n"
            "同志Alex、ゲームは順調です。盤面にはまだ危険があります。",
            expected_pairs=1,
        )

        self.assertTrue(parsed["bilingual"])
        self.assertEqual(
            [segment["language"] for segment in parsed["speech_segments"]],
            ["en", "ja"],
        )
        self.assertIn("日本語訳：", parsed["display_text"])

    def test_parses_plain_language_transition_after_blank_lines_are_removed(self):
        parsed = comment_bilingual.parse_response(
            "Comrade Alex, the game is going well.\n"
            "同志Alex、ゲームは順調です。盤面にはまだ危険があります。",
            expected_pairs=1,
        )

        self.assertTrue(parsed["bilingual"])
        self.assertEqual(
            [segment["language"] for segment in parsed["speech_segments"]],
            ["en", "ja"],
        )

    def test_output_detector_accepts_uncommon_english_words(self):
        self.assertTrue(
            comment_bilingual.looks_like_english_output(
                "Absolutely, comrade. Victory awaits."
            )
        )
        self.assertTrue(
            comment_bilingual.looks_like_english_output(
                "Comrade あずまぐ, thank you for watching the stream."
            )
        )
        self.assertTrue(
            comment_bilingual.parse_response(
                "Comrade あずまぐ, thank you for watching the stream.\n"
                "あずまぐさん、見てくださってありがとうございます。",
                expected_pairs=1,
            )["bilingual"]
        )

    def test_markerless_japanese_response_is_accepted_as_normal_reply(self):
        parsed = comment_bilingual.parse_response(
            "同志のみなさん、コメントありがとうございます。\n"
            "この流れは落ち着いて続けます。"
        )

        self.assertFalse(parsed["bilingual"])
        self.assertEqual(parsed["speech_segments"], [])
        self.assertIn("コメントありがとうございます", parsed["display_text"])

    def test_unpaired_english_fails_open_instead_of_blocking_queue(self):
        parsed = comment_bilingual.parse_response(
            "Thanks for watching.\n\n"
            "See you in the next round.",
            expected_pairs=2,
        )

        self.assertFalse(parsed["bilingual"])
        self.assertEqual(parsed["speech_segments"], [])
        self.assertIn("Thanks for watching.", parsed["display_text"])

    def test_classifier_order_merges_only_flagged_rows_without_guessing_boundaries(self):
        rows = [
            {"index": 1, "is_english": False},
            {"index": 2, "is_english": True},
            {"index": 3, "is_english": False},
        ]
        parsed = comment_bilingual.build_ordered_speech_segments(
            rows,
            "返信Aです。\n\n返信Bです。\n\n返信Cです。",
            "Reply B in English.",
        )
        self.assertEqual(
            [(segment["language"], segment["role"], segment["text"]) for segment in parsed["speech_segments"]],
            [
                ("ja", "reply", "返信Aです。"),
                ("en", "translation", "Reply B in English."),
                ("ja", "reply", "返信Bです。"),
                ("ja", "reply", "返信Cです。"),
            ],
        )
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            metadata = Path(temp_dir_raw) / "speech.json"
            metadata.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
            loaded = comment_bilingual.load_speech_segments(metadata)
            self.assertEqual(loaded[1]["role"], "translation")
            self.assertEqual(loaded[2]["role"], "reply")

    def test_classifier_order_rejects_missing_japanese_paragraph_instead_of_merging(self):
        rows = [
            {"index": 1, "is_english": True},
            {"index": 2, "is_english": False},
        ]
        with self.assertRaises(comment_bilingual.BilingualFormatError):
            comment_bilingual.build_ordered_speech_segments(
                rows, "返信Aです。", "Reply A in English.",
            )

    def test_classifier_order_rejects_misordered_rows(self):
        rows = [
            {"index": 2, "is_english": True},
            {"index": 1, "is_english": False},
        ]
        with self.assertRaises(comment_bilingual.BilingualFormatError):
            comment_bilingual.build_ordered_speech_segments(
                rows, "返信Aです。\n\n返信Bです。", "Reply B in English."
            )

    def test_classifier_order_rejects_translation_labels(self):
        rows = [{"index": 1, "is_english": True}]
        with self.assertRaises(comment_bilingual.BilingualFormatError):
            comment_bilingual.build_ordered_speech_segments(
                rows, "返信Aです。", "Target 1\nReply A in English."
            )

    def test_parses_english_reply_then_japanese_translation(self):
        parsed = comment_bilingual.parse_bilingual_response(
            """===ENGLISH===
Comrade Alex, the game is going well. The board is still dangerous.
===JAPANESE===
同志Alex、ゲームは順調です。盤面にはまだ危険があります。
===END_BILINGUAL==="""
        )

        self.assertEqual(
            [segment["language"] for segment in parsed["speech_segments"]],
            ["en", "ja"],
        )
        self.assertTrue(
            parsed["display_text"].startswith("Comrade Alex, the game is going well.")
        )
        self.assertIn("日本語訳：", parsed["display_text"])

    def test_parse_response_keeps_legacy_marker_compatibility(self):
        parsed = comment_bilingual.parse_response(
            "===ENGLISH===\nComrade Alex, welcome to the stream.\n"
            "===JAPANESE===\n同志Alex、配信へようこそ。\n===END_BILINGUAL===",
            expected_pairs=1,
        )
        self.assertTrue(parsed["bilingual"])
        self.assertEqual(parsed["english_reply_count"], 1)

    def test_preserves_mixed_batch_order(self):
        parsed = comment_bilingual.parse_bilingual_response(
            """同志A、ありがとうございます。

===ENGLISH===
Comrade B, thank you for joining us.
===JAPANESE===
同志B、参加してくれてありがとうございます。
===END_BILINGUAL===

同志C、その見方は面白いです。"""
        )

        self.assertEqual(
            [segment["language"] for segment in parsed["speech_segments"]],
            ["ja", "en", "ja", "ja"],
        )
        display = parsed["display_text"]
        self.assertLess(display.index("同志A"), display.index("Comrade B"))
        self.assertLess(display.index("Comrade B"), display.index("同志B"))
        self.assertLess(display.index("同志B"), display.index("同志C"))

    def test_requires_one_marker_pair_for_each_english_comment(self):
        response = """===ENGLISH===
Reply to the first English comment.
===JAPANESE===
最初の英語コメントへの返答です。
===END_BILINGUAL==="""
        with self.assertRaises(comment_bilingual.BilingualFormatError):
            comment_bilingual.parse_bilingual_response(response, expected_pairs=2)

    def test_rejects_missing_translation_or_unmarked_english(self):
        invalid = [
            "===ENGLISH===\nHello there.\n===END_BILINGUAL===",
            "Hello there.\n===ENGLISH===\nHow are you?\n===JAPANESE===\n元気ですか。\n===END_BILINGUAL===",
            "Hello there.\n同志A、こんにちは。\n===ENGLISH===\nHow are you?\n===JAPANESE===\n元気ですか。\n===END_BILINGUAL===",
        ]
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(comment_bilingual.BilingualFormatError):
                    comment_bilingual.parse_bilingual_response(text)

    def test_accepts_exact_english_duplicate_immediately_before_marker(self):
        reply = (
            "You are right, actors need silence, and I should have stopped sooner."
        )
        parsed = comment_bilingual.parse_bilingual_response(
            f"""{reply}

===ENGLISH===
{reply}
===JAPANESE===
その通りです。役者には沈黙が必要で、私はもっと早く止めるべきでした。
===END_BILINGUAL===""",
            expected_pairs=1,
        )

        self.assertEqual(parsed["display_text"].count(reply), 1)
        self.assertEqual(
            [segment["language"] for segment in parsed["speech_segments"]],
            ["en", "ja"],
        )


class TestBilingualSpeechScripts(unittest.TestCase):
    @staticmethod
    def _write_silence(path: Path, frames: int = 1200) -> None:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x00" * frames)

    @staticmethod
    def _make_executable(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    @classmethod
    def _write_fake_ffmpeg(cls, path: Path) -> None:
        cls._make_executable(
            path,
            """#!/usr/bin/env python3
import shutil
import sys
import wave

args = sys.argv[1:]
source = args[args.index('-i') + 1]
target = args[-1]
if '-f' in args and 'concat' in args:
    files = []
    with open(source, encoding='utf-8') as manifest:
        for line in manifest:
            if line.startswith("file '") and line.rstrip().endswith("'"):
                files.append(line.strip()[6:-1])
    params = None
    frames = []
    for item in files:
        with wave.open(item, 'rb') as wav:
            params = params or wav.getparams()
            frames.append(wav.readframes(wav.getnframes()))
    with wave.open(target, 'wb') as wav:
        wav.setparams(params)
        for chunk in frames:
            wav.writeframes(chunk)
else:
    shutil.copyfile(source, target)
""",
        )

    def test_english_tts_uses_flite_and_normalizes_wav(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            fixture = temp_dir / "fixture.wav"
            source = temp_dir / "english.txt"
            output = temp_dir / "english.wav"
            fake_flite = temp_dir / "flite"
            fake_ffmpeg = temp_dir / "ffmpeg"
            self._write_silence(fixture)
            source.write_text("Hello from the stream.\n", encoding="utf-8")
            self._make_executable(
                fake_flite,
                """#!/bin/bash
if [ "${1:-}" = "-lv" ]; then echo "Voices available: slt"; exit 0; fi
out=""
while [ "$#" -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac
done
cp "$FAKE_WAV" "$out"
""",
            )
            self._write_fake_ffmpeg(fake_ffmpeg)
            env = os.environ.copy()
            env["FAKE_WAV"] = str(fixture)
            env["ENGLISH_TTS_FLITE_BIN"] = str(fake_flite)
            env["ENGLISH_TTS_FFMPEG_BIN"] = str(fake_ffmpeg)
            subprocess.run(
                [
                    str(REPO_ROOT / "english_tts.sh"),
                    "-o",
                    str(output),
                    "-f",
                    str(source),
                ],
                cwd=REPO_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            with wave.open(str(output), "rb") as wav:
                self.assertEqual(wav.getframerate(), 24000)
                self.assertEqual(wav.getnchannels(), 1)
                self.assertGreater(wav.getnframes(), 0)

    def test_bilingual_renderer_concatenates_segments_in_one_wav(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            fixture = temp_dir / "fixture.wav"
            metadata = temp_dir / "meta.json"
            output = temp_dir / "combined.wav"
            fake_english = temp_dir / "english_tts.sh"
            fake_say = temp_dir / "say_enqueue.sh"
            fake_ffmpeg = temp_dir / "ffmpeg"
            self._write_silence(fixture)
            parsed = comment_bilingual.parse_bilingual_response(
                """===ENGLISH===
Thank you for the English comment.
===JAPANESE===
英語のコメントをありがとうございます。
===END_BILINGUAL==="""
            )
            metadata.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
            fake_body = """#!/bin/bash
out=""
# Simulate Linux audio tools that probe their inherited stdin. The renderer
# must not let a child consume the next line of the segment manifest.
IFS= read -r -n 1 _discarded || true
while [ "$#" -gt 0 ]; do
  case "$1" in -o|--render-only) out="$2"; shift 2 ;; *) shift ;; esac
done
cp "$FAKE_WAV" "$out"
"""
            self._make_executable(fake_english, fake_body)
            self._make_executable(fake_say, fake_body)
            self._write_fake_ffmpeg(fake_ffmpeg)

            env = os.environ.copy()
            env["FAKE_WAV"] = str(fixture)
            env["BILINGUAL_TTS_ENGLISH_SCRIPT"] = str(fake_english)
            env["BILINGUAL_TTS_SAY_SCRIPT"] = str(fake_say)
            env["BILINGUAL_TTS_FFMPEG_BIN"] = str(fake_ffmpeg)
            subprocess.run(
                [
                    str(REPO_ROOT / "bilingual_comment_tts.sh"),
                    "-o",
                    str(output),
                    str(metadata),
                    "120",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            with wave.open(str(output), "rb") as wav:
                self.assertEqual(wav.getframerate(), 24000)
                self.assertGreaterEqual(wav.getnframes(), 2400)


class TestBilingualPipelineWiring(unittest.TestCase):
    def test_comment_cleaner_can_preserve_natural_paragraph_boundaries(self):
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source broadcast/radio_engine.sh; _clean_comment_talk "$1" 1',
                "comment-cleaner",
                "返信Aです。\n\n返信Bです。\n\n返信Cです。",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout, "返信Aです。\n\n返信Bです。\n\n返信Cです。")

    def test_render_only_cleanup_removes_intermediate_voicevox_wav(self):
        script = (REPO_ROOT / "say_enqueue.sh").read_text(encoding="utf-8")
        self.assertIn('rm -f "${MY_CONTENT%.txt}_pre.wav"', script)

    def test_generation_sidecar_embeds_ordered_speech_segments(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            queue_file = temp_dir / "comment.txt"
            history_file = temp_dir / "history.jsonl"
            speech_file = temp_dir / "speech.json"
            queue_file.write_text("English reply.\n\n日本語訳：\n日本語訳です。\n", encoding="utf-8")
            speech_file.write_text(
                json.dumps(
                    comment_bilingual.parse_bilingual_response(
                        """===ENGLISH===
English reply.
===JAPANESE===
日本語訳です。
===END_BILINGUAL==="""
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            shell = r'''
source broadcast/comment.sh
COMMENT_GENERATION_HISTORY_FILE="$1"
COMMENT_GENERATION_HISTORY_KEEP=10
_comment_store_generation_meta "$2" main test-model batch 1 30 primary secondary "" "$3"
'''
            subprocess.run(
                [
                    "bash",
                    "-c",
                    shell,
                    "test-comment-meta",
                    str(history_file),
                    str(queue_file),
                    str(speech_file),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            sidecar = temp_dir / "comment.meta.json"
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertTrue(payload["bilingual"])
            self.assertEqual(
                [segment["language"] for segment in payload["speech_segments"]],
                ["en", "ja"],
            )

    def test_generation_uses_classifier_translation_contract_and_stores_speech_metadata(self):
        script = (REPO_ROOT / "broadcast/comment.sh").read_text(encoding="utf-8")
        self.assertNotIn('comment_bilingual.py" detect', script)
        self.assertNotIn('comment_bilingual.py" parse-response', script)
        self.assertIn('"is_english"', script)
        self.assertIn("COMMENT_TRANSLATION", script)
        self.assertIn("build-segments", script)
        self.assertIn("_comment_build_translation_prompt", script)
        self.assertIn('payload["speech_segments"] = speech_segments', script)
        self.assertIn("日本語返信のみ継続", script)

    def test_translation_prompt_reads_japanese_paragraphs_from_pipeline(self):
        classification = json.dumps(
            [
                {"index": 1, "is_english": False, "category": "chitchat"},
                {"index": 2, "is_english": True, "category": "chitchat"},
                {"index": 3, "is_english": False, "category": "chitchat"},
            ],
            ensure_ascii=False,
        )
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source broadcast/comment.sh; printf "%s" "$2" | _comment_build_translation_prompt "$1"',
                "translation-prompt",
                classification,
                "返信Aです。\n\n返信Bです。\n\n返信Cです。",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("Target 2", result.stdout)
        self.assertIn("返信Bです。", result.stdout)
        self.assertNotIn("Target 1", result.stdout)
        self.assertNotIn("返信Aです。", result.stdout)

    def test_translation_failure_does_not_use_shared_ai_backoff(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            prompt = temp_dir / "prompt.txt"
            agent_file = temp_dir / "agent.txt"
            prompt.write_text("translate", encoding="utf-8")
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    r'''
source broadcast/comment.sh
_contains_provider_error_text() { return 1; }
_ai_dispatch() { return 1; }
_ai_backoff_set() { echo "shared backoff must not be called" >&2; return 99; }
_comment_generate_translation "$1" "codex:deepseek-v4-flash,codex:minimax-m3" 1 "$2"
''',
                    "translation-backoff",
                    str(prompt),
                    str(agent_file),
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("shared backoff must not be called", result.stderr)

    def test_translation_skips_existing_provider_backoff_without_force_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            prompt = temp_dir / "prompt.txt"
            agent_file = temp_dir / "agent.txt"
            calls = temp_dir / "calls.txt"
            prompt.write_text("translate", encoding="utf-8")
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    r'''
source broadcast/comment.sh
_contains_provider_error_text() { return 1; }
_ai_backoff_check() { [ "$1" = "codex:deepseek-v4-flash" ] && return 1; return 0; }
_ai_dispatch() { printf '%s\n' "$2" >> "$CALLS"; printf 'Reply in English.'; }
_comment_generate_translation "$1" "codex:deepseek-v4-flash,codex:minimax-m3" 1 "$2" >/dev/null
cat "$CALLS"
''',
                    "translation-backoff-skip",
                    str(prompt),
                    str(agent_file),
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "CALLS": str(calls)},
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout.strip(), "codex:minimax-m3")

    def test_translation_caps_dispatch_attempts_at_two_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            prompt = temp_dir / "prompt.txt"
            calls = temp_dir / "calls.txt"
            prompt.write_text("translate", encoding="utf-8")
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    r'''
source broadcast/comment.sh
_contains_provider_error_text() { return 1; }
_ai_dispatch() { printf '%s\n' "$2" >> "$CALLS"; return 1; }
_comment_generate_translation "$1" "codex:one,codex:two,codex:three" 1 >/dev/null
cat "$CALLS"
''',
                    "translation-cap",
                    str(prompt),
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "CALLS": str(calls)},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.stdout.splitlines(),
                ["codex:one", "codex:two"],
            )

    def test_classification_safety_failure_does_not_return_raw_ai_result(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            comments = temp_dir / "comments.txt"
            comments.write_text(
                "natural: Hello from the stream!\n"
                "viewer: dociaiDoci dociaiDoci dociaiDoci\n",
                encoding="utf-8",
            )
            invalid = json.dumps(
                [
                    {
                        "index": 2,
                        "user": "viewer",
                        "comment": "dociaiDoci dociaiDoci dociaiDoci",
                        "category": "chitchat",
                        "is_english": True,
                    },
                    {
                        "index": 1,
                        "user": "natural",
                        "comment": "Hello from the stream!",
                        "category": "chitchat",
                        "is_english": False,
                    },
                ],
                ensure_ascii=False,
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    r'''
source broadcast/comment.sh
COMMENT_CLASSIFIER_AI_ENABLED=1
_classify_comments_with_edit_contract() { printf 'fake-agent\n%s' "$INVALID"; return 0; }
ai_generate() { return 1; }
_classify_comments "$1"
''',
                    "classification-live-safety",
                    str(comments),
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "INVALID": invalid},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('"is_english":true', result.stdout)

    def test_classifier_language_safety_clears_emote_chain_even_if_model_flags_it(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            comments = temp_dir / "comments.txt"
            comments.write_text("viewer: dociaiDoci dociaiDoci dociaiDoci\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    r'''
source broadcast/comment.sh
_comment_enforce_english_safety "$1" "$2"
''',
                    "classifier-safety",
                    '[{"index":1,"comment":"dociaiDoci dociaiDoci dociaiDoci","category":"chitchat","is_english":true}]',
                    str(comments),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn('"is_english":false', result.stdout)

    def test_classifier_language_safety_keeps_english_with_url_context(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            comments = temp_dir / "comments.txt"
            comments.write_text(
                "viewer: Amazing stream! https://example.com\n", encoding="utf-8"
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    r'''
source broadcast/comment.sh
_comment_enforce_english_safety "$1" "$2"
''',
                    "classifier-url",
                    '[{"index":1,"category":"chitchat","is_english":true}]',
                    str(comments),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn('"is_english":true', result.stdout)

    def test_normal_comment_validation_rejects_english_only_partial_output(self):
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source broadcast/radio_engine.sh; _is_valid_comment_talk "$1"',
                "comment-validation",
                "Thanks for watching the stream today.",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)

        unpunctuated = subprocess.run(
            [
                "bash",
                "-c",
                'source broadcast/radio_engine.sh; _is_valid_comment_talk "$1"',
                "comment-validation",
                "Thanks for watching the stream today",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(unpunctuated.returncode, 0, unpunctuated.stderr)

        normal_ascii_junk = subprocess.run(
            [
                "bash",
                "-c",
                'source broadcast/radio_engine.sh; _is_valid_comment_talk "$1"',
                "comment-validation",
                "this is a long ascii-only response without japanese punctuation",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(normal_ascii_junk.returncode, 0)

    def test_playback_uses_bilingual_renderer_before_normal_voicevox_path(self):
        script = (REPO_ROOT / "broadcast/comment_lib.sh").read_text(encoding="utf-8")
        bilingual_index = script.index('_comment_has_bilingual_speech "$playing_file"')
        voicevox_index = script.index(
            'SAY_VOICEVOX_SPEAKER_OVERRIDE="${_cw_vo_speaker:-}"', bilingual_index
        )
        self.assertLess(bilingual_index, voicevox_index)
        self.assertIn("_comment_play_bilingual_speech", script)
        self.assertIn("英語翻訳 → 日本語返信の順で再生", script)
        self.assertIn('_comment_declares_bilingual_speech "$playing_file"', script)
        self.assertIn("通常VOICEVOX経路を抑止", script)

    def test_comment_prompts_generate_japanese_before_separate_translation(self):
        prompt_paths = [
            "prompts/comment_response.md",
            "prompts/comment_response_default.md",
            "prompts/comment_response_chitchat.md",
            "prompts/comment_response_game.md",
            "prompts/comment_response_raid.md",
            "prompts/comment_response_card_gacha.md",
            "prompts/comment_response_sing_request.md",
            "prompts/comment_persona_soren91.md",
        ]
        for relative_path in prompt_paths:
            text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertTrue("英語" in text or "English" in text)
                self.assertTrue(
                    "後段" in text or "separately" in text or "separate" in text or "別" in text
                )


if __name__ == "__main__":
    unittest.main()
