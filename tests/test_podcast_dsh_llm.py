"""podcast_build の dsh バックエンド（NDJSON パース・起動契約）の単体テスト。

実プロセスやネットワークは使わない。dsh の起動・タイムアウト kill・
schema 指定時の JSON 抽出だけをモックで確認する。
"""
from __future__ import annotations

import importlib.util
import json
import signal
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "podcast_build_dsh_test", ROOT / "tools" / "podcast_build.py"
)
podcast = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(podcast)


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _final(text: str, kind: str = "completed") -> str:
    return _line(
        {
            "type": "final",
            "reason": {"kind": kind},
            "text": text,
        }
    )


class ParseDshEventsTest(unittest.TestCase):
    def test_final_and_reason_are_extracted(self) -> None:
        stdout = "\n".join(
            [
                _line({"type": "tool_call", "tool": "web_fetch"}),
                _final("最終回答です"),
            ]
        )
        text, reason = podcast._parse_dsh_events(stdout)
        self.assertEqual(text, "最終回答です")
        self.assertEqual(reason, "completed")

    def test_last_final_wins(self) -> None:
        text, reason = podcast._parse_dsh_events(
            "\n".join([_final("最初"), _final("最後")])
        )
        self.assertEqual(text, "最後")
        self.assertEqual(reason, "completed")

    def test_invalid_lines_and_non_dicts_are_skipped(self) -> None:
        stdout = "\n".join(
            [
                "not json {{{",
                "[]",
                _line({"type": "other", "text": "無視"}),
                _final("回答"),
                "",
            ]
        )
        text, reason = podcast._parse_dsh_events(stdout)
        self.assertEqual(text, "回答")
        self.assertEqual(reason, "completed")

    def test_missing_final_returns_none_reason(self) -> None:
        text, reason = podcast._parse_dsh_events(
            _line({"type": "tool_call", "tool": "web_fetch"})
        )
        self.assertEqual(text, "")
        self.assertIsNone(reason)

    def test_non_completed_reason_is_returned(self) -> None:
        text, reason = podcast._parse_dsh_events(_final("途中", kind="aborted"))
        self.assertEqual(text, "途中")
        self.assertEqual(reason, "aborted")

    def test_final_without_reason_has_none_kind(self) -> None:
        text, reason = podcast._parse_dsh_events(
            _line({"type": "final", "text": "回答"})
        )
        self.assertEqual(text, "回答")
        self.assertIsNone(reason)


class DshCommandTest(unittest.TestCase):
    def test_uses_absolute_node_and_bin_js(self) -> None:
        with (
            mock.patch.object(podcast, "DSH_NODE", "/opt/node/bin/node"),
            mock.patch.object(podcast, "DSH_JS", "/opt/dsh/lib/bin.js"),
            mock.patch.object(podcast.os.path, "isfile", return_value=True),
        ):
            cmd = podcast._dsh_command()

        self.assertEqual(cmd[0], "/opt/node/bin/node")
        self.assertEqual(cmd[1], "/opt/dsh/lib/bin.js")
        self.assertEqual(
            cmd[2:],
            [
                "--profile",
                "headless",
                "--approval-policy",
                "never",
                "--permission-mode",
                "read-only",
                "--json",
                "-",
            ],
        )

    def test_falls_back_to_path_node_when_default_is_missing(self) -> None:
        with (
            mock.patch.object(podcast, "DSH_NODE", "/missing/node"),
            mock.patch.object(podcast, "DSH_JS", "/opt/dsh/lib/bin.js"),
            mock.patch.object(
                podcast.os.path,
                "isfile",
                side_effect=lambda path: path == "/opt/dsh/lib/bin.js",
            ),
            mock.patch.object(podcast.shutil, "which", return_value="/usr/bin/node"),
        ):
            cmd = podcast._dsh_command()

        self.assertEqual(cmd[0], "/usr/bin/node")

    def test_missing_bin_js_raises_clearly(self) -> None:
        with (
            mock.patch.object(podcast, "DSH_NODE", "/opt/node/bin/node"),
            mock.patch.object(podcast, "DSH_JS", "/missing/bin.js"),
            mock.patch.object(podcast.os.path, "isfile", return_value=False),
            mock.patch.object(podcast.shutil, "which", return_value="/usr/bin/node"),
        ):
            with self.assertRaisesRegex(RuntimeError, "bin.js が見つかりません"):
                podcast._dsh_command()


class TimeoutContractTest(unittest.TestCase):
    def test_zero_timeout_is_clamped_to_one_second(self) -> None:
        self.assertEqual(podcast._resolve_llm_timeout(0), 1.0)
        self.assertEqual(podcast._resolve_llm_timeout(-5), 1.0)

    def test_run_dsh_timeout_kills_process_group(self) -> None:
        class FakeProc:
            pid = 4321
            returncode = -signal.SIGKILL

            def __init__(self) -> None:
                self.communicate_calls = 0

            def communicate(self, *_args, **_kwargs):
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    raise subprocess.TimeoutExpired(cmd="dsh", timeout=1.0)
                return "", ""

            def poll(self):
                return None

        proc = FakeProc()
        with (
            mock.patch.object(podcast, "_dsh_command", return_value=["node", "bin.js"]),
            mock.patch.object(podcast.subprocess, "Popen", return_value=proc),
            mock.patch.object(podcast, "_kill_process_group") as kill_mock,
        ):
            result = podcast._run_dsh("prompt", 1)

        self.assertIsNone(result)
        kill_mock.assert_called_once_with(proc)

    def test_kill_process_group_uses_killpg_on_posix(self) -> None:
        class FakeProc:
            pid = 99

            def poll(self):
                return None

        proc = FakeProc()
        with (
            mock.patch.object(podcast.os, "name", "posix"),
            mock.patch.object(podcast.os, "getpgid", return_value=77),
            mock.patch.object(podcast.os, "killpg") as killpg_mock,
        ):
            podcast._kill_process_group(proc)  # type: ignore[arg-type]

        killpg_mock.assert_called_once_with(77, signal.SIGKILL)


class LlmGenerateTest(unittest.TestCase):
    def test_prompt_is_sent_to_stdin_with_empty_cwd(self) -> None:
        captured: dict[str, object] = {}

        class FakeProc:
            returncode = 0
            pid = 1

            def communicate(self, prompt, timeout=None):
                captured["prompt"] = prompt
                captured["timeout"] = timeout
                return _final("生成結果"), ""

            def poll(self):
                return 0

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["cwd"] = kwargs.get("cwd")
            return FakeProc()

        with (
            mock.patch.object(podcast, "_dsh_command", return_value=["node", "bin.js"]),
            mock.patch.object(podcast.subprocess, "Popen", side_effect=fake_popen),
        ):
            result = podcast._run_dsh("テストプロンプト", 30)

        self.assertEqual(result, "生成結果")
        # 先頭にツール不使用の実行ルールが付き、本文(元のプロンプト)は末尾にそのまま残る。
        self.assertTrue(captured["prompt"].startswith("【実行ルール】"))
        self.assertTrue(captured["prompt"].endswith("\n\nテストプロンプト"))
        self.assertEqual(captured["timeout"], 30.0)
        self.assertTrue(str(captured["cwd"]).startswith("/"))

    def test_schema_adds_json_instruction_and_returns_dict(self) -> None:
        captured: dict[str, str] = {}

        def fake_run(prompt: str, timeout=None):
            captured["prompt"] = prompt
            return '```json\n{"text": "本文"}\n```'

        with mock.patch.object(podcast, "_run_dsh", side_effect=fake_run):
            result = podcast.llm_generate("プロンプト", schema=podcast.WRITE_SCHEMA)

        self.assertEqual(result, {"text": "本文"})
        self.assertIn("JSON Schema", captured["prompt"])
        self.assertIn("コードフェンス", captured["prompt"])

    def test_schema_rejects_invalid_json(self) -> None:
        with mock.patch.object(podcast, "_run_dsh", return_value="JSONではない"):
            self.assertIsNone(
                podcast.llm_generate("プロンプト", schema=podcast.WRITE_SCHEMA)
            )

    def test_schema_rejects_missing_required_key(self) -> None:
        with mock.patch.object(podcast, "_run_dsh", return_value='{"other": "x"}'):
            self.assertIsNone(
                podcast.llm_generate("プロンプト", schema=podcast.WRITE_SCHEMA)
            )

    def test_without_schema_returns_raw_text(self) -> None:
        with mock.patch.object(podcast, "_run_dsh", return_value="素の本文"):
            self.assertEqual(podcast.llm_generate("プロンプト"), "素の本文")


if __name__ == "__main__":
    unittest.main()
