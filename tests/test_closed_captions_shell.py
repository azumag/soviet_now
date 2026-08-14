#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class ClosedCaptionShellTests(unittest.TestCase):
    def test_say_enqueue_preloads_before_boundary_and_translates_in_parallel(self) -> None:
        source = (REPO_ROOT / "say_enqueue.sh").read_text(encoding="utf-8")
        playback_start = source.index("_play_prerendered_voicevox_chunks()")
        playback_end = source.index("_concat_prerendered_voicevox_chunks()")
        playback = source[playback_start:playback_end]
        self.assertLess(playback.index('docich_cc_commit "$i"'), playback.index('_launch_stream_wav "$chunk_wav"'))
        self.assertLess(
            playback.index('docich_cc_prepare "$((i + 1))"'),
            playback.index('_wait_for_player_pid "$play_pid"'),
        )
        self.assertIn("docich_cc_clear || true", playback)

        planning = source[source.index("_pre_chunks=()"):]
        self.assertLess(
            planning.index('docich_cc_start_plan "${_pre_chunks[@]}"'),
            planning.index("./voicevox_tts.sh"),
        )
        self.assertLess(
            planning.index('docich_cc_start_plan "${_pre_chunks[@]:0:PRE_MAX_CHUNKS}"'),
            planning.index('_synthesize_chunk "${_pre_chunks[$_pc_i]}"'),
        )
        self.assertIn("Partial/rollback deployments", source)
        self.assertIn("docich_cc_is_enabled() { return 1; }", source)

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        controller = root / "controller.py"
        call_log = root / "calls.jsonl"
        debug_log = root / "debug.log"
        controller.write_text(
            textwrap.dedent(
                """
                import json
                import os
                from pathlib import Path
                import sys

                args = sys.argv[1:]
                record = {"args": args}
                if args and args[0] == "plan":
                    chunks_path = Path(args[args.index("--chunks-file") + 1])
                    record["chunks"] = chunks_path.read_text(encoding="utf-8").splitlines()
                with Path(os.environ["DOCICH_TEST_CALL_LOG"]).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\\n")
                if args and args[0] == "plan":
                    if os.environ.get("DOCICH_TEST_FAIL_PLAN") == "1":
                        raise SystemExit(2)
                    output = Path(args[args.index("--output") + 1])
                    output.write_text('{"v":1}\\n', encoding="utf-8")
                if (
                    len(args) > 1
                    and args[0] == "send"
                    and args[1] == "prepare"
                    and os.environ.get("DOCICH_TEST_FAIL_PREPARE") == "1"
                ):
                    raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        return controller, call_log, debug_log

    def _run_shell(
        self,
        script: str,
        *,
        root: Path,
        controller: Path,
        call_log: Path,
        debug_log: Path,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        socket_path = root / "cc.sock"
        if not socket_path.exists():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
        env = os.environ.copy()
        env.update(
            {
                "DOCICH_CC_ENABLED": "1",
                "DOCICH_CC_CONTROLLER": str(controller),
                "DOCICH_CC_PYTHON": sys.executable,
                "DOCICH_CC_SOCKET": str(socket_path),
                "DOCICH_TEST_CALL_LOG": str(call_log),
                "DEBUG_LOG_FILE": str(debug_log),
            }
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", "-c", script],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_plan_and_chunk_boundary_control_order(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            controller, call_log, debug_log = self._fixture(root)
            content = root / "content.txt"
            content.write_text("日本語", encoding="utf-8")
            result = self._run_shell(
                f"""
                set -uo pipefail
                source lib/closed_captions.sh
                IS_LINUX=1 USE_VOICEVOX=1 WAV_MODE=false RENDER_ONLY=false
                docich_cc_init token_1 {content!s}
                docich_cc_start_plan '一つ目。' '二つ目。'
                docich_cc_wait_plan
                docich_cc_prepare 0 0
                docich_cc_commit 0
                docich_cc_prepare 1 1
                docich_cc_commit 1
                docich_cc_clear
                printf '%s %s\\n' "$DOCICH_CC_DIRTY" "$DOCICH_CC_ACTIVE"
                docich_cc_cleanup
                """,
                root=root,
                controller=controller,
                call_log=call_log,
                debug_log=debug_log,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("0 0", result.stdout)
            calls = [json.loads(line) for line in call_log.read_text().splitlines()]
            self.assertEqual(calls[0]["args"][0], "plan")
            self.assertEqual(calls[0]["chunks"], ["一つ目。", "二つ目。"])
            operations = [
                call["args"][1]
                for call in calls[1:]
                if call["args"][0] == "send"
            ]
            self.assertEqual(
                operations,
                ["prepare", "commit", "prepare", "commit", "clear"],
            )
            second_prepare = calls[3]["args"]
            self.assertEqual(
                second_prepare[second_prepare.index("--sequence") + 1],
                "1",
            )

    def test_translation_failure_is_fail_open(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            controller, call_log, debug_log = self._fixture(root)
            content = root / "content.txt"
            content.write_text("日本語", encoding="utf-8")
            result = self._run_shell(
                f"""
                set -uo pipefail
                source lib/closed_captions.sh
                IS_LINUX=1 USE_VOICEVOX=1 WAV_MODE=false RENDER_ONLY=false
                docich_cc_init token_2 {content!s}
                docich_cc_start_plan '日本語'
                if docich_cc_wait_plan; then exit 9; fi
                printf 'audio-continues\\n'
                docich_cc_cleanup
                """,
                root=root,
                controller=controller,
                call_log=call_log,
                debug_log=debug_log,
                extra_env={"DOCICH_TEST_FAIL_PLAN": "1"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("audio-continues", result.stdout)
            calls = [json.loads(line) for line in call_log.read_text().splitlines()]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["args"][0], "plan")

    def test_prepare_timeout_still_attempts_execution_scoped_clear(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            controller, call_log, debug_log = self._fixture(root)
            content = root / "content.txt"
            content.write_text("日本語", encoding="utf-8")
            result = self._run_shell(
                f"""
                set -uo pipefail
                source lib/closed_captions.sh
                IS_LINUX=1 USE_VOICEVOX=1 WAV_MODE=false RENDER_ONLY=false
                docich_cc_init token_timeout {content!s}
                docich_cc_start_plan '日本語'
                if docich_cc_prepare 0 0; then exit 9; fi
                printf 'audio-continues dirty=%s\\n' "$DOCICH_CC_DIRTY"
                docich_cc_cleanup
                """,
                root=root,
                controller=controller,
                call_log=call_log,
                debug_log=debug_log,
                extra_env={"DOCICH_TEST_FAIL_PREPARE": "1"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("audio-continues dirty=1", result.stdout)
            calls = [json.loads(line) for line in call_log.read_text().splitlines()]
            operations = [
                call["args"][1]
                for call in calls
                if call["args"][0] == "send"
            ]
            self.assertEqual(operations, ["prepare", "clear"])

    def test_render_only_never_enables_caption_work(self) -> None:
        result = subprocess.run(
            [
                "bash",
                "-c",
                "source lib/closed_captions.sh; "
                "IS_LINUX=1 USE_VOICEVOX=1 WAV_MODE=false RENDER_ONLY=true; "
                "if docich_cc_is_enabled; then exit 7; fi",
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "DOCICH_CC_ENABLED": "1"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
