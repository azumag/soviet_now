#!/usr/bin/env python3
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "lib" / "radio_parser.py"
ENGINE = ROOT / "broadcast" / "radio_engine.sh"


class RadioParserTests(unittest.TestCase):
    def run_parser(self, raw, *, strict=True):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            body = tmp_path / "body.txt"
            summary = tmp_path / "summary.txt"
            selected = tmp_path / "selected.txt"
            cmd = ["python3", str(PARSER)]
            if strict:
                cmd.append("--require-on-air-script")
            cmd.extend([str(body), str(summary), str(selected)])
            result = subprocess.run(cmd, input=raw, text=True, capture_output=True)
            return (
                result,
                body.read_text(encoding="utf-8") if body.exists() else "",
                summary.read_text(encoding="utf-8") if summary.exists() else "",
                selected.read_text(encoding="utf-8") if selected.exists() else "",
            )

    def test_strict_boundary_discards_search_thinking(self):
        raw = """The previous tool calls did not return useful results.
Let me think about a safe topic and search again.
Now let me write the script carefully.
ON_AIR_SCRIPT_START
お晩です。ここからは確認できた内容だけをお伝えします。
ニュースの背景を落ち着いて見ていきます。
===SUMMARY===
ニュース, 背景
確認済み情報の紹介
===SELECTED_NEWS===
確認済みのニュース見出し
"""
        result, body, summary, selected = self.run_parser(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("tool calls", body)
        self.assertNotIn("Let me", body)
        self.assertTrue(body.startswith("お晩です。"))
        self.assertEqual(summary, "ニュース, 背景 / 確認済み情報の紹介")
        self.assertEqual(selected, "確認済みのニュース見出し")

    def test_last_boundary_wins(self):
        raw = """Planning notes.
ON_AIR_SCRIPT_START
This was only a format rehearsal.
ON_AIR_SCRIPT_START
こんばんは。こちらだけが読み上げ対象です。
===SUMMARY===
読み上げ
境界確認
"""
        result, body, _, _ = self.run_parser(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body, "こんばんは。こちらだけが読み上げ対象です。")

    def test_strict_mode_rejects_missing_boundary(self):
        raw = """Let me search for the latest information.
こんばんは。これは境界のない原稿です。
===SUMMARY===
原稿
境界なし
"""
        result, body, _, _ = self.run_parser(raw)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(body, "")
        self.assertIn("missing ON_AIR_SCRIPT_START", result.stderr)

    def test_strict_mode_rejects_missing_summary_end_boundary(self):
        raw = """ON_AIR_SCRIPT_START
こんばんは。終端境界のない原稿です。
"""
        result, body, _, _ = self.run_parser(raw)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(body, "")
        self.assertIn("missing ===SUMMARY===", result.stderr)

    def test_delimited_boundary_alias_remains_supported(self):
        raw = """===ON_AIR_SCRIPT===
こんばんは。旧境界表記でも本文だけを抽出します。
===SUMMARY===
旧境界
互換性確認
"""
        result, body, _, _ = self.run_parser(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body, "こんばんは。旧境界表記でも本文だけを抽出します。")

    def test_provider_mangled_boundary_alias_is_exactly_supported(self):
        raw = """ON_AIR_SCRIPT===
こんばんは。実モデルで観測した境界表記にも対応します。
===SUMMARY===
実モデル
境界互換
"""
        result, body, _, _ = self.run_parser(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body, "こんばんは。実モデルで観測した境界表記にも対応します。")

    def test_legacy_mode_remains_available_for_offline_consumers(self):
        raw = """こんばんは。従来形式の原稿です。
===SUMMARY===
従来形式
互換性確認
"""
        result, body, summary, _ = self.run_parser(raw, strict=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body, "こんばんは。従来形式の原稿です。")
        self.assertEqual(summary, "従来形式 / 互換性確認")

    def test_runtime_wrapper_requires_boundary(self):
        shell = f'''\
set -o pipefail
ELOOP_LIB_DIR={ROOT!s}
log() {{ :; }}
source {ENGINE!s}
work=$(mktemp -d)
printf '%s' '境界のない原稿です。===SUMMARY===要約' | \
  _radio_parse_output_to_files "$work/body" "$work/summary" "$work/selected"
'''
        result = subprocess.run(["bash", "-c", shell], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
