#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class AiGenerateBackoffTests(unittest.TestCase):
    def test_rate_limit_status_reports_main_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            state_dir = Path(temp_dir)
            now = int(time.time())
            (state_dir / "codex_deepseek-v4-flash").write_text(
                f"{now + 3600}\n", encoding="utf-8"
            )
            (state_dir / "codex_minimax-m3").write_text(
                f"{now + 7200}\n", encoding="utf-8"
            )
            env = os.environ.copy()
            env.update(
                {
                    "AI_BACKOFF_DIR": str(state_dir),
                    "COMMENT_AGENTS": "codex:deepseek-v4-flash,codex:minimax-m3",
                }
            )
            result = subprocess.run(
                ["python3", "lib/ai_backoff_status.py", "--lines"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("main=deepseek-v4-flash", result.stdout)
        self.assertIn("fb=minimax-m3", result.stdout)

    def test_rate_limit_writer_and_status_share_override_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            state_dir = root / "backoff"
            script = textwrap.dedent(
                f"""
                set -u
                ELOOP_LIB_DIR={root!s}
                AI_BACKOFF_DIR={state_dir!s}
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                _ai_backoff_set codex:deepseek-v4-flash 3600
                test -f {state_dir / 'codex_deepseek-v4-flash'!s}
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            env = os.environ.copy()
            env.update(
                {
                    "AI_BACKOFF_DIR": str(state_dir),
                    "COMMENT_AGENTS": "codex:deepseek-v4-flash,codex:minimax-m3",
                }
            )
            status = subprocess.run(
                ["python3", "lib/ai_backoff_status.py", "--lines"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("main=deepseek-v4-flash", status.stdout)

    def test_status_dashboard_header_shows_both_rate_limited_roles(self) -> None:
        import status_dashboard

        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            state_dir = Path(temp_dir)
            now = int(time.time())
            (state_dir / "codex_deepseek-v4-flash").write_text(
                f"{now + 3600}\n", encoding="utf-8"
            )
            (state_dir / "codex_minimax-m3").write_text(
                f"{now + 7200}\n", encoding="utf-8"
            )
            old_env = os.environ.copy()
            try:
                os.environ["AI_BACKOFF_DIR"] = str(state_dir)
                os.environ["COMMENT_AGENTS"] = "codex:deepseek-v4-flash,codex:minimax-m3"
                lines = status_dashboard.render_header(
                    [],
                    {"state": "STOP", "score": 0, "pieces": []},
                    "",
                    "?",
                    "?",
                    0,
                    0,
                    0,
                    {},
                    {},
                )
            finally:
                os.environ.clear()
                os.environ.update(old_env)
        rendered = "\n".join(lines)
        self.assertIn("AI 429", rendered)
        self.assertIn("main=deepseek-v4-flash", rendered)
        self.assertIn("fb=minimax-m3", rendered)

    def test_radio_quality_failure_does_not_set_model_backoff(self) -> None:
        source = (REPO_ROOT / "broadcast/radio_engine.sh").read_text(encoding="utf-8")
        start = source.index("品質チェック失敗")
        end = source.index("done # attempt loop end", start)
        quality_retry = source[start:end]
        self.assertNotIn("_ai_backoff_set", quality_retry)
        self.assertIn("モデルbackoffなし", quality_retry)

    def test_comment_global_backoff_is_only_set_for_rate_limit_outcome(self) -> None:
        source = (REPO_ROOT / "broadcast/comment.sh").read_text(encoding="utf-8")
        start = source.index("_comment_handle_generation_failure()")
        end = source.index("\n}", start) + len("\n}")
        policy = source[start:end]
        self.assertIn("generation_rate_limited", policy)
        self.assertIn("_comment_failure_backoff_set", policy)
        self.assertIn("_comment_failure_backoff_clear", policy)
        self.assertIn("形式/通常失敗、バックオフなし", policy)
        self.assertIn('_comment_handle_generation_failure "$generation_rate_limited"', source)

    def test_comment_global_backoff_runtime_only_tracks_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            backoff = root / "comment_generation_backoff_until"
            script = textwrap.dedent(
                f"""
                set -u
                source {REPO_ROOT / 'core/helpers.sh'!s}
                source {REPO_ROOT / 'broadcast/comment.sh'!s}
                log() {{ :; }}
                COMMENT_FAILURE_BACKOFF_FILE={backoff!s}
                COMMENT_FAILURE_BACKOFF_SEC=3600
                _comment_handle_generation_failure false
                [ ! -f {backoff!s} ] || exit 11
                _comment_handle_generation_failure true
                [ -f {backoff!s} ] || exit 12
                [ "$(cat {backoff!s})" -gt "$(date +%s)" ] || exit 13
                _comment_handle_generation_failure false
                [ ! -f {backoff!s} ] || exit 14
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def _run_list(self, mode: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            prompt = root / "prompt.txt"
            prompt.write_text("test prompt\n", encoding="utf-8")
            script = textwrap.dedent(
                f"""
                set -u
                ELOOP_LIB_DIR={root!s}
                AI_GENERATION_QUEUE_ENABLED=0
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                log() {{ :; }}
                validator() {{ [ "$1" = "VALID" ]; }}
                _ai_dispatch() {{
                    case "{mode}" in
                    invalid) printf 'malformed output'; return 0 ;;
                    invalid_rate_text) printf 'The rate limit exceeded in this example'; return 0 ;;
                    failed) return 1 ;;
                    rate_limited)
                        if [ "$2" = "codex:deepseek-v4-flash" ]; then
                            printf 'provider rejected request'
                            return "$AI_RATE_LIMIT_RC"
                        fi
                        printf 'VALID'
                        return 0
                        ;;
                    esac
                }}
                if ai_generate_list COMMENT {prompt!s} 'codex:deepseek-v4-flash,codex:minimax-m3' '' validator; then
                    printf '\\nRESULT=success\\n'
                else
                    printf '\\nRESULT=failure\\n'
                fi
                if [ -f "$(_ai_backoff_dir)/codex_deepseek-v4-flash" ]; then
                    echo DEEPSEEK_EXISTS=1
                else
                    echo DEEPSEEK_EXISTS=0
                fi
                if [ -f "$(_ai_backoff_dir)/codex_minimax-m3" ]; then
                    echo MINIMAX_EXISTS=1
                else
                    echo MINIMAX_EXISTS=0
                fi
                """
            )
            return subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )

    def test_invalid_output_does_not_backoff_model(self) -> None:
        result = self._run_list("invalid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT=failure", result.stdout)
        self.assertIn("DEEPSEEK_EXISTS=0", result.stdout)
        self.assertIn("MINIMAX_EXISTS=0", result.stdout)

    def test_regular_failure_does_not_backoff_model(self) -> None:
        result = self._run_list("failed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT=failure", result.stdout)
        # プロバイダ/CLI 失敗 (rc!=0) はモデル別バックオフを設定する。
        # 両エージェントとも失敗するため、両方にバックオフが付く。
        self.assertIn("DEEPSEEK_EXISTS=1", result.stdout)
        self.assertIn("MINIMAX_EXISTS=1", result.stdout)

    def test_rate_limit_words_in_model_output_alone_do_not_backoff(self) -> None:
        result = self._run_list("invalid_rate_text")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT=failure", result.stdout)
        self.assertIn("DEEPSEEK_EXISTS=0", result.stdout)
        self.assertIn("MINIMAX_EXISTS=0", result.stdout)

    def test_explicit_rate_limit_backoffs_only_that_model(self) -> None:
        result = self._run_list("rate_limited")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT=success", result.stdout)
        self.assertIn("DEEPSEEK_EXISTS=1", result.stdout)
        self.assertIn("MINIMAX_EXISTS=0", result.stdout)

    def test_active_rate_limit_backoff_is_not_force_retried(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            prompt = root / "prompt.txt"
            calls = root / "calls.txt"
            prompt.write_text("test prompt\n", encoding="utf-8")
            script = textwrap.dedent(
                f"""
                set -u
                ELOOP_LIB_DIR={root!s}
                AI_GENERATION_QUEUE_ENABLED=0
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                log() {{ :; }}
                validator() {{ [ "$1" = "VALID" ]; }}
                mkdir -p "$(_ai_backoff_dir)"
                printf '%s\\n' "$(( $(date +%s) + 3600 ))" >"$(_ai_backoff_dir)/codex_minimax-m3"
                _ai_dispatch() {{ echo called >>{calls!s}; printf VALID; return 0; }}
                if ai_generate_list COMMENT {prompt!s} 'codex:minimax-m3' '' validator; then
                    echo RESULT=success
                else
                    echo RESULT=failure
                fi
                if [ -f {calls!s} ]; then
                    echo CALLS=$(wc -l <{calls!s} | tr -d ' ')
                else
                    echo CALLS=0
                fi
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT=failure", result.stdout)
        self.assertIn("CALLS=0", result.stdout)

    def test_codex_backend_returns_rate_limit_code_only_for_explicit_signal(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            prompt = root / "prompt.txt"
            prompt.write_text("test prompt\n", encoding="utf-8")
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/bin/sh\n"
                "out=''\n"
                "while [ $# -gt 0 ]; do\n"
                "  if [ \"$1\" = '-o' ]; then out=\"$2\"; shift 2; else shift; fi\n"
                "done\n"
                "printf '%s\\n' '429 Too Many Requests' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            script = textwrap.dedent(
                f"""
                set -u
                ELOOP_LIB_DIR={root!s}
                source {REPO_ROOT / 'core/helpers.sh'!s}
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                CODEX_BIN={fake_codex!s}
                _ai_call_codex_unqueued RADIO codex:minimax-m3 {prompt!s} 3
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 79, result.stderr)

    def test_codex_model_text_does_not_turn_a_regular_failure_into_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            prompt = root / "prompt.txt"
            prompt.write_text("test prompt\n", encoding="utf-8")
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/bin/sh\n"
                "out=''\n"
                "while [ $# -gt 0 ]; do\n"
                "  if [ \"$1\" = '-o' ]; then out=\"$2\"; shift 2; else shift; fi\n"
                "done\n"
                "printf '%s\\n' 'The rate limit exceeded in stdout narrative'\n"
                "printf '%s\\n' 'The rate limit exceeded in this narrative' > \"$out\"\n"
                "printf '%s\\n' 'ordinary provider failure' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            script = textwrap.dedent(
                f"""
                set -u
                ELOOP_LIB_DIR={root!s}
                source {REPO_ROOT / 'core/helpers.sh'!s}
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                CODEX_BIN={fake_codex!s}
                _ai_call_codex_unqueued RADIO codex:minimax-m3 {prompt!s} 3
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_per_model_backoff_seconds_resolve_from_items(self) -> None:
        script = textwrap.dedent(
            f"""
            set -u
            ELOOP_LIB_DIR={REPO_ROOT!s}
            source {REPO_ROOT / 'core/config.sh'!s}
            source {REPO_ROOT / 'lib/ai_generate.sh'!s}
            printf '%s\\n' "$(_ai_backoff_sec_for_agent codex:deepseek-v4-flash-free RADIO)"
            printf '%s\\n' "$(_ai_backoff_sec_for_agent codex:amd-token-factory-deepseek-v4-flash RADIO)"
            printf '%s\\n' "$(_ai_backoff_sec_for_agent codex:openrouter/free RADIO)"
            printf '%s\\n' "$(_ai_backoff_sec_for_agent vercel:minimax/minimax-m3-free RADIO)"
            printf '%s\\n' "$(_ai_backoff_sec_for_agent vercel:poolside/laguna-s-2.1-free RADIO)"
            printf '%s\\n' "$(_ai_backoff_sec_for_agent local RADIO)"
            printf '%s\\n' "$(_ai_backoff_sec_for_agent codex:deepseek-v4-flash RADIO)"
            printf '%s\\n' "$(_ai_backoff_sec_for_agent codex:minimax-m3 RADIO)"
            printf '%s\\n' "$(_ai_backoff_sec_for_agent codex:deepseek-v4-pro RADIO)"
            """
        )
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line]
        self.assertEqual(
            lines,
            [
                "86400",
                "86400",
                "86400",
                "300",
                "300",
                "1800",
                "18000",
                "18000",
                "18000",
            ],
        )

    def test_vercel_category_b_agent_resolves_dedicated_backoff(self) -> None:
        # 回帰: AI_BACKOFF_SEC_ITEMS の GLM 5.3 キーは slash 形式
        # (vercel/zai/glm-5.3-flash:300) でなければ name 解決が
        # "vercel" になって 300 秒が当たらない (#162 レビュー指摘)。
        script = textwrap.dedent(
            f"""
            set -u
            ELOOP_LIB_DIR={REPO_ROOT!s}
            source {REPO_ROOT / 'core/config.sh'!s}
            source {REPO_ROOT / 'lib/ai_generate.sh'!s}
            printf '%s\\n' "$(_ai_backoff_sec_for_agent vercel:zai/glm-5.3-flash COMMENT)"
            """
        )
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line]
        self.assertEqual(lines, ["300"])

    def test_ai_stats_record_writes_jsonl_without_stdout(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            stats_dir = Path(temp_dir) / "ai_stats"
            script = textwrap.dedent(
                f"""
                set -u
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                AI_STATS_DIR={stats_dir!s}
                _ai_stats_record attempt RADIO codex:deepseek-v4-flash ""
                _ai_stats_record winner RADIO codex:minimax-m3 0
                _ai_stats_record all_failed RADIO "" ""
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "", "stats must not leak to stdout")
            files = list(stats_dir.glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            lines = files[0].read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertIn('"event":"winner"', lines[1])
            self.assertIn('"agent":"codex:minimax-m3"', lines[1])

    def test_rate_limit_uses_long_backoff_but_generic_failure_uses_short(self) -> None:
        # _ai_backoff_set に渡る秒数を記録するモックで、種別による差を検証する。
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            state = root / "backoff"
            calls = root / "backoff_calls.txt"
            prompt = root / "prompt.txt"
            prompt.write_text("test\n", encoding="utf-8")
            script = textwrap.dedent(
                f"""
                set -u
                ELOOP_LIB_DIR={root!s}
                AI_BACKOFF_DIR={state!s}
                AI_GENERATION_QUEUE_ENABLED=0
                AI_BACKOFF_FAILURE_SEC=300
                source {REPO_ROOT / 'core/config.sh'!s}
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                log() {{ :; }}
                _ai_backoff_set() {{
                    printf '%s\\n' "$2" >>{calls!s}
                }}
                validator() {{ [ "$1" = "VALID" ]; }}
                _ai_dispatch() {{
                    if [ "$2" = "codex:deepseek-v4-flash" ]; then
                        return "$AI_RATE_LIMIT_RC"
                    fi
                    return 1
                }}
                ai_generate_list COMMENT {prompt!s} 'codex:deepseek-v4-flash,codex:minimax-m3' '' validator || true
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            values = [int(line) for line in calls.read_text(encoding="utf-8").split() if line]
            # deepseek-v4-flash（rate limit）はモデル別の 5h=18000、minimax（一般失敗）は短い 300。
            self.assertIn(18000, values)
            self.assertIn(300, values)

    def test_invalid_agent_spec_is_skipped_without_dispatch_or_stats(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            prompt = root / "prompt.txt"
            calls = root / "calls.txt"
            stats = root / "stats"
            prompt.write_text("test\n", encoding="utf-8")
            script = textwrap.dedent(
                f"""
                set -u
                ELOOP_LIB_DIR={root!s}
                AI_STATS_DIR={stats!s}
                AI_GENERATION_QUEUE_ENABLED=0
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                log() {{ :; }}
                validator() {{ [ "$1" = "VALID" ]; }}
                _ai_dispatch() {{
                    printf '%s\\n' "$2" >>{calls!s}
                    printf 'VALID'
                    return 0
                }}
                ai_generate_list RADIO:batch_commentary {prompt!s} '__invalid_agent__,codex:usable' '' validator
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls.read_text(encoding="utf-8").splitlines(), ["codex:usable"])
            stat_text = "\n".join(
                path.read_text(encoding="utf-8") for path in stats.glob("*.jsonl")
            )
            self.assertNotIn("__invalid_agent__", stat_text)
            self.assertIn('"agent":"codex:usable"', stat_text)

    def test_dispatch_rejects_unknown_agent_before_recording_attempt(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            prompt = root / "prompt.txt"
            stats = root / "stats"
            prompt.write_text("test\n", encoding="utf-8")
            script = textwrap.dedent(
                f"""
                set -u
                AI_STATS_DIR={stats!s}
                source {REPO_ROOT / 'lib/ai_generate.sh'!s}
                log() {{ :; }}
                _ai_dispatch RADIO __invalid_agent__ {prompt!s}
                rc=$?
                test "$rc" -eq 2
                test ! -e {stats!s}
                """
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
