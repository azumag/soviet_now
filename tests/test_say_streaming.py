import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestSayStreaming(unittest.TestCase):
    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run(
        self,
        content_text: str,
        *,
        fail_first_chunk: str | None = None,
        partial_fail_after_sleep: str | None = None,
        retry_max: int = 1,
        fail_caption_index: int | None = None,
        synth_sleep: str = "0.25",
        play_sleep: str = "0.80",
        ffprobe_duration: str = "0",
        truncate_min: str | None = None,
        country_names: bool = False,
        copy_country_names: bool = True,
    ):
        raw_dir = tempfile.TemporaryDirectory()
        root = Path(raw_dir.name)
        (root / "tmp").mkdir()
        (root / "config").mkdir()
        (root / "bin").mkdir()
        (root / "lib").mkdir()
        shutil.copy2(REPO_ROOT / "say_enqueue.sh", root / "say_enqueue.sh")
        shutil.copy2(
            REPO_ROOT / "lib" / "normalize_speech_text.py",
            root / "lib" / "normalize_speech_text.py",
        )
        if copy_country_names:
            shutil.copy2(
                REPO_ROOT / "lib" / "country_names.py",
                root / "lib" / "country_names.py",
            )

        event_log = root / "events.log"
        caption_log = root / "captions.log"
        (root / "lib" / "closed_captions.sh").write_text(
            """DOCICH_CC_PLAN_READY=0
DOCICH_CC_DIRTY=0
docich_cc_init() { DOCICH_CC_PLAN_READY=0; DOCICH_CC_DIRTY=0; }
docich_cc_is_enabled() { return 0; }
docich_cc_start_plan() { DOCICH_CC_PLAN_READY=1; printf 'start:%s\\n' "$#" >> "$TEST_CAPTION_LOG"; return 0; }
docich_cc_wait_plan() { return 0; }
docich_cc_prepare() { DOCICH_CC_DIRTY=1; printf 'prepare:%s\\n' "$1" >> "$TEST_CAPTION_LOG"; [ "${TEST_CAPTION_FAIL_PREPARE:-}" = "$1" ] && return 1; return 0; }
docich_cc_commit() { printf 'commit:%s\\n' "$1" >> "$TEST_CAPTION_LOG"; return 0; }
docich_cc_clear() { if [ "$DOCICH_CC_DIRTY" = "1" ]; then printf 'clear\\n' >> "$TEST_CAPTION_LOG"; fi; DOCICH_CC_DIRTY=0; return 0; }
docich_cc_cleanup() { :; }
""",
            encoding="utf-8",
        )
        (root / "tmp" / "voicevox_voice.txt").write_text("1\n", encoding="utf-8")
        content = root / "comment.txt"
        content.write_text(content_text, encoding="utf-8")
        fail_state = root / "fail_once.state"
        partial_fail_state = root / "partial_fail_once.state"

        self._write_executable(
            root / "voicevox_tts.sh",
            """#!/bin/sh
set -eu
out=''
input=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -f) input="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'synth-start:%s\\n' "$(basename "$out")" >> "$TEST_EVENT_LOG"
if [ -n "$input" ]; then
  normalized=$(tr '\\n' ' ' < "$input")
  printf 'synth-text:%s\\n' "$normalized" >> "$TEST_EVENT_LOG"
fi
sleep SYNC_SLEEP_SEC
printf 'wav' > "$out"
printf 'synth-end:%s\\n' "$(basename "$out")" >> "$TEST_EVENT_LOG"
""".replace("SYNC_SLEEP_SEC", synth_sleep),
        )
        fail_name = fail_first_chunk or ""
        partial_fail_name = partial_fail_after_sleep or ""
        self._write_executable(
            root / "bin" / "pactl",
            "#!/bin/sh\nprintf '1\\tsoren_null\\tmodule-null-sink\\n'\n",
        )
        self._write_executable(
            root / "bin" / "ffprobe",
            f"#!/bin/sh\nprintf '{ffprobe_duration}\\n'\n",
        )
        self._write_executable(
            root / "bin" / "paplay",
            f"""#!/bin/sh
set -eu
name=$(basename "$3")
if [ -n "${{SAY_STREAM_PLAYER_READY_FILE:-}}" ]; then
  : > "$SAY_STREAM_PLAYER_READY_FILE"
fi
printf 'play-start:%s\\n' "$name" >> "$TEST_EVENT_LOG"
if [ "$name" = "{fail_name}" ] && [ ! -e "{fail_state}" ]; then
  : > "{fail_state}"
  exit 1
fi
if [ "$name" = "{partial_fail_name}" ] && [ ! -e "{partial_fail_state}" ]; then
  : > "{partial_fail_state}"
  sleep SYNC_SLEEP_SEC
  exit 1
fi
sleep SYNC_SLEEP_SEC
printf 'play-end:%s\\n' "$name" >> "$TEST_EVENT_LOG"
""".replace("SYNC_SLEEP_SEC", play_sleep),
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{root / 'bin'}:{env['PATH']}",
                "SOREN_OBS_PLATFORM": "linux",
                "SAY_AUDIO_DEVICE": "soren_null",
                "SAY_CONTEXT_LABEL": "comment",
                "SAY_CHUNK_GAP_SEC": "0",
                "SAY_RETRY_MAX": str(retry_max),
                "SAY_RETRY_SLEEP_SEC": "0",
                "SAY_RETRY_MAX_SLEEP_SEC": "1",
                "SAY_STREAM_PLAY_START_SETTLE_SEC": "0.1",
                "SAY_STREAM_PLAYER_READY_DIR": str(root / "ready"),
                "TWITCH_SNOOZE_POLL_SEC": "0",
                "SPEAKING_GRACE_SEC": "0",
                "VOICEVOX_COMMENT_SYNTH_TIMEOUT_SEC": "5",
                "VOICEVOX_SYNTH_LOCK_WAIT_COMMENT_SEC": "5",
                "TEST_EVENT_LOG": str(event_log),
                "TEST_CAPTION_LOG": str(caption_log),
            }
        )
        if fail_caption_index is not None:
            env["TEST_CAPTION_FAIL_PREPARE"] = str(fail_caption_index)
        if truncate_min is not None:
            env["SAY_TRUNCATE_MIN_EXPECTED_SEC"] = truncate_min
        if country_names:
            env["SAY_REPLACE_COUNTRY_REFERENCES"] = "1"
        result = subprocess.run(
            ["bash", "say_enqueue.sh", "--no-preempt", str(content), "150", "0"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        events = (
            event_log.read_text(encoding="utf-8").splitlines()
            if event_log.exists()
            else []
        )
        caption_ops = (
            caption_log.read_text(encoding="utf-8").splitlines()
            if caption_log.exists()
            else []
        )
        raw_dir.cleanup()
        return result, events, caption_ops

    def test_generic_audio_does_not_treat_external_type_terms_as_countries(self):
        result, events, _ = self._run(
            "ソ連のT-34戦車、T-72戦車、Type 2 diabetesです。",
            synth_sleep="0.01",
            play_sleep="0.01",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "synth-text:ソ連のt-34戦車、t-72戦車、type 2 diabetesです。",
            events,
        )
        self.assertFalse(
            any("モルドバ" in line or "不明な国" in line for line in events),
            events,
        )

    def test_game_audio_explicitly_replaces_all_country_stage_numbers(self):
        source = " ".join(f"T{piece_type}" for piece_type in range(1, 17))
        expected = (
            "アルメニア モルドバ エストニア ラトビア リトアニア "
            "ジョージア アゼルバイジャン タジキスタン キルギス "
            "ベラルーシ ウズベキスタン トルクメニスタン ウクライナ "
            "カザフスタン ロシア ソ連"
        )

        result, events, _ = self._run(
            source,
            synth_sleep="0.01",
            play_sleep="0.01",
            country_names=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"synth-text:{expected}", events)

    def test_game_audio_fails_closed_when_country_normalizer_is_missing(self):
        result, events, _ = self._run(
            "T14を読み上げます。",
            synth_sleep="0.01",
            play_sleep="0.01",
            country_names=True,
            copy_country_names=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ゲーム国名の正規化に失敗", result.stderr)
        self.assertFalse(any(line.startswith("synth-") for line in events), events)

    def test_playback_starts_while_next_chunk_is_synthesized(self):
        text = "".join(f"第{i}文です。" for i in range(1, 41))
        result, events, caption_ops = self._run(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        play_start_0 = events.index("play-start:chunk_0.wav")
        synth_start_1 = events.index("synth-start:chunk_1.wav")
        synth_end_1 = events.index("synth-end:chunk_1.wav")
        play_end_0 = events.index("play-end:chunk_0.wav")
        self.assertLess(play_start_0, synth_start_1, events)
        self.assertLess(synth_start_1, play_end_0, events)
        self.assertLess(synth_end_1, play_end_0, events)
        chunk_count = len([line for line in events if line.startswith("synth-start:chunk_")])
        self.assertEqual(
            [line for line in caption_ops if line.startswith("prepare:")],
            [f"prepare:{i}" for i in range(chunk_count)],
            caption_ops,
        )
        self.assertEqual(
            [line for line in caption_ops if line.startswith("commit:")],
            [f"commit:{i}" for i in range(chunk_count)],
            caption_ops,
        )

    def test_unpunctuated_long_text_is_hard_split_without_truncation(self):
        result, events, _ = self._run("a" * 350)
        self.assertEqual(result.returncode, 0, result.stderr)
        synth = [line for line in events if line.startswith("synth-start:")]
        plays = [line for line in events if line.startswith("play-start:")]
        self.assertGreaterEqual(len(synth), 4, events)
        self.assertEqual(len(synth), len(plays), events)

    def test_short_comment_keeps_the_existing_single_wav_path(self):
        result, events, _ = self._run("短いコメントです。")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(line.startswith("synth-start:") and "_pre.wav" in line for line in events), events)
        self.assertFalse(any("chunk_" in line for line in events), events)

    def test_failed_chunk_playback_retries_only_that_chunk(self):
        text = "".join(f"第{i}文です。" for i in range(1, 41))
        result, events, _ = self._run(text, fail_first_chunk="chunk_1.wav")
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [line.split(":", 1)[1] for line in events if line.startswith("play-start:")]
        self.assertGreaterEqual(starts.count("chunk_1.wav"), 2, events)
        first = starts.index("chunk_0.wav")
        second = starts.index("chunk_1.wav")
        third = starts.index("chunk_2.wav")
        self.assertLess(first, second, starts)
        self.assertLess(second, third, starts)
        self.assertEqual(starts.count("chunk_0.wav"), 1, starts)
        self.assertEqual(starts.count("chunk_2.wav"), 1, starts)

    def test_retry_limit_zero_does_not_relaunch_failed_chunk(self):
        text = "".join(f"第{i}文です。" for i in range(1, 41))
        result, events, _ = self._run(text, fail_first_chunk="chunk_1.wav", retry_max=0)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        starts = [line.split(":", 1)[1] for line in events if line.startswith("play-start:")]
        self.assertEqual(starts.count("chunk_0.wav"), 1, starts)
        self.assertEqual(starts.count("chunk_1.wav"), 1, starts)

    def test_caption_prepare_failure_clears_previous_page_but_audio_continues(self):
        text = "".join(f"第{i}文です。" for i in range(1, 41))
        result, events, caption_ops = self._run(text, fail_caption_index=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("prepare:0", caption_ops)
        self.assertIn("commit:0", caption_ops)
        self.assertIn("clear", caption_ops)
        self.assertNotIn("commit:1", caption_ops)
        self.assertGreaterEqual(sum(line.startswith("play-start:") for line in events), 3, events)

    def test_synthesis_slower_than_playback_does_not_duplicate_chunks(self):
        # VM では次チャンク合成（数十秒）が再生より長く、待機開始時点で既に
        # 再生が終わっている。この場合も各チャンクは一度だけ再生されるべき。
        text = "".join(f"第{i}文です。" for i in range(1, 41))
        result, events, _ = self._run(
            text,
            synth_sleep="0.60",
            play_sleep="0.30",
            ffprobe_duration="3.5",
            truncate_min="2",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [line.split(":", 1)[1] for line in events if line.startswith("play-start:")]
        chunk_names = sorted({name for name in starts if name.startswith("chunk_")})
        for name in chunk_names:
            self.assertEqual(starts.count(name), 1, f"{name} duplicated: {starts}")
        self.assertIn("合成待ち中に完了", result.stderr)

    def test_partially_played_chunk_is_not_retried_from_the_beginning(self):
        # プレイヤーが数秒再生してから異常終了した場合、同じ WAV を先頭から
        # 再試行すると、既に聞こえた部分が二重になる。
        text = "".join(f"第{i}文です。" for i in range(1, 41))
        result, events, _ = self._run(
            text,
            partial_fail_after_sleep="chunk_1.wav",
            retry_max=1,
            play_sleep="2.2",
            ffprobe_duration="10",
            truncate_min="2",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        starts = [line.split(":", 1)[1] for line in events if line.startswith("play-start:")]
        self.assertEqual(starts.count("chunk_1.wav"), 1, events)
        self.assertIn("再試行せず完了扱い", result.stderr)


if __name__ == "__main__":
    unittest.main()
