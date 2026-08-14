#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

from model_output_guard import extract_final_text  # noqa: E402


class ModelOutputGuardTests(unittest.TestCase):
    def test_clean_radio_script_is_unchanged(self) -> None:
        source = "こんにちは。今日のニュースです。\n\n===SUMMARY===\n科学,宇宙"
        self.assertEqual(extract_final_text(source), source)

    def test_untagged_web_research_notes_before_divider_are_not_spoken(self) -> None:
        source = """WebFetchが使えない環境なので、自分の知識で候補を選びます。
確実性が高いのは以下のあたりです。
- 候補A

---

こんばんは。ここからが今日の本題です。

===SUMMARY===
本題,要約
"""
        self.assertEqual(
            extract_final_text(source),
            "こんばんは。ここからが今日の本題です。\n\n===SUMMARY===\n本題,要約",
        )

    def test_tool_only_output_is_discarded(self) -> None:
        source = """<function_calls>
<invoke name="exec_command">
<parameter name="cmd">curl https://example.invalid</parameter>
</invoke>
</tool_call>"""
        self.assertEqual(extract_final_text(source), "")

    def test_unmarked_work_note_is_discarded(self) -> None:
        self.assertEqual(extract_final_text("WebFetchをもう少し試してみます。"), "")

    def test_explicit_final_container_wins_over_analysis(self) -> None:
        source = "<analysis>検索します。</analysis><final>完成した本文です。</final>"
        self.assertEqual(extract_final_text(source), "完成した本文です。")

    def test_work_note_mislabeled_as_final_is_discarded(self) -> None:
        source = "<final>WebFetchをもう少し試してみます。</final>"
        self.assertEqual(extract_final_text(source), "")

    def test_fact_check_envelope_drops_preamble_but_keeps_issues(self) -> None:
        source = """材料を確認します。
===SAFE_SCRIPT===
放送する完成原稿です。
===ISSUES===
なし
"""
        self.assertEqual(
            extract_final_text(source),
            "===SAFE_SCRIPT===\n放送する完成原稿です。\n===ISSUES===\nなし",
        )

    def test_malformed_tool_protocol_does_not_leak_trailing_text(self) -> None:
        source = "<tool_call>\n検索コマンド\n完成したように見える文です。"
        self.assertEqual(extract_final_text(source), "")


if __name__ == "__main__":
    unittest.main()
