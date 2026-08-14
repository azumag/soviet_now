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
        self.assertTrue(comment_bilingual.batch_has_english(["viewer: GG!"]))
        self.assertTrue(comment_bilingual.batch_has_english(["viewer: hi"]))

    def test_does_not_treat_urls_or_mixed_japanese_as_english(self):
        self.assertFalse(
            comment_bilingual.batch_has_english(
                ["viewer: https://example.com", "viewer: Sorenはgoodです"]
            )
        )


class TestBilingualResponseParser(unittest.TestCase):
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

    def test_english_tts_uses_flite_and_normalizes_wav(self):
        with tempfile.TemporaryDirectory() as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            fixture = temp_dir / "fixture.wav"
            source = temp_dir / "english.txt"
            output = temp_dir / "english.wav"
            fake_flite = temp_dir / "flite"
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
            env = os.environ.copy()
            env["FAKE_WAV"] = str(fixture)
            env["ENGLISH_TTS_FLITE_BIN"] = str(fake_flite)
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

            env = os.environ.copy()
            env["FAKE_WAV"] = str(fixture)
            env["BILINGUAL_TTS_ENGLISH_SCRIPT"] = str(fake_english)
            env["BILINGUAL_TTS_SAY_SCRIPT"] = str(fake_say)
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

    def test_generation_requires_markers_and_stores_speech_metadata(self):
        script = (REPO_ROOT / "broadcast/comment.sh").read_text(encoding="utf-8")
        for marker in (
            comment_bilingual.ENGLISH_MARKER,
            comment_bilingual.JAPANESE_MARKER,
            comment_bilingual.END_MARKER,
        ):
            self.assertIn(marker, script)
        self.assertIn('comment_bilingual.py" detect', script)
        self.assertIn('comment_bilingual.py" parse --metadata', script)
        self.assertIn('--expected-pairs "$comment_bilingual_expected_pairs"', script)
        self.assertIn('payload["speech_segments"] = speech_segments', script)

    def test_playback_uses_bilingual_renderer_before_normal_voicevox_path(self):
        script = (REPO_ROOT / "broadcast/comment_lib.sh").read_text(encoding="utf-8")
        bilingual_index = script.index('_comment_has_bilingual_speech "$playing_file"')
        voicevox_index = script.index(
            'SAY_VOICEVOX_SPEAKER_OVERRIDE="${_cw_vo_speaker:-}"', bilingual_index
        )
        self.assertLess(bilingual_index, voicevox_index)
        self.assertIn("_comment_play_bilingual_speech", script)
        self.assertIn("英語返答 → 日本語訳の順で再生", script)
        self.assertIn('_comment_declares_bilingual_speech "$playing_file"', script)
        self.assertIn("通常VOICEVOX経路を抑止", script)

    def test_comment_prompts_allow_english_then_japanese_translation(self):
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
                self.assertTrue("English" in text or "英語" in text)
                self.assertTrue(
                    "日本語訳" in text or "Japanese translation" in text
                )


if __name__ == "__main__":
    unittest.main()
