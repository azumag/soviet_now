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

    # --- <think> ブロックの回帰テスト (issue: MiniMax-M3 が素の <think> を出す) ---

    def test_think_block_is_removed_and_body_survives(self) -> None:
        source = "<think>\nこれは考え中\n</think>\n本当の答えはこれです。"
        self.assertEqual(extract_final_text(source), "本当の答えはこれです。")

    def test_orphan_closing_think_tag_drops_only_the_prefix(self) -> None:
        # 実害ケースの本命: 開始タグが別チャネルへ出て、閉じタグだけが本文側に残る。
        source = "</think>\n本当の答えはこれです。"
        self.assertEqual(extract_final_text(source), "本当の答えはこれです。")

    def test_unterminated_think_tag_drops_the_remainder(self) -> None:
        source = "<think>\n考え中で切れた"
        self.assertEqual(extract_final_text(source), "")

    def test_body_before_unterminated_think_survives(self) -> None:
        source = "本文です。<think>途中で切れた"
        self.assertEqual(extract_final_text(source), "本文です。")

    def test_thinking_block_is_removed(self) -> None:
        source = "<thinking>考え</thinking>本文。"
        self.assertEqual(extract_final_text(source), "本文。")

    def test_analysis_block_is_removed(self) -> None:
        source = "<analysis>検索します。</analysis>本文。"
        self.assertEqual(extract_final_text(source), "本文。")

    def test_multiple_think_blocks_are_removed(self) -> None:
        source = "<think>a</think>本文1。<think>b</think>本文2。"
        self.assertEqual(extract_final_text(source), "本文1。本文2。")

    def test_nested_think_blocks_are_removed(self) -> None:
        source = "<think>外<think>内</think>まだ考え中</think>本文。"
        self.assertEqual(extract_final_text(source), "本文。")

    def test_think_tag_with_attributes_is_removed(self) -> None:
        source = '<think type="x">考え</think>本文。'
        self.assertEqual(extract_final_text(source), "本文。")

    def test_multiline_japanese_think_block_is_removed(self) -> None:
        source = (
            "<think>\n"
            "改行を含む考え中のテキストです。\n"
            "「引用」や — ダッシュ、#記号も混ざります。\n"
            "</think>\n"
            "本文です。"
        )
        self.assertEqual(extract_final_text(source), "本文です。")

    def test_think_only_output_is_empty(self) -> None:
        source = "<think>考えだけ</think>"
        self.assertEqual(extract_final_text(source), "")

    def test_tool_protocol_after_think_still_discards_whole_output(self) -> None:
        source = '<think>考え</think><invoke name="x">y</invoke>本文。'
        self.assertEqual(extract_final_text(source), "")

    def test_think_inside_explicit_final_container(self) -> None:
        source = "<final><think>考え</think>完成本文。</final>"
        self.assertEqual(extract_final_text(source), "完成本文。")

    def test_think_before_divider_still_recovers_body(self) -> None:
        source = "<think>\n考え中\n</think>\n\n---\n\n本文です。\n"
        self.assertEqual(extract_final_text(source), "本文です。")

    def test_safe_script_envelope_survives_think_in_script(self) -> None:
        source = (
            "===SAFE_SCRIPT===\n"
            "<think>考え</think>\n"
            "放送原稿です。\n"
            "===ISSUES===\n"
            "なし"
        )
        result = extract_final_text(source)
        self.assertTrue(result.startswith("===SAFE_SCRIPT==="))
        self.assertIn("放送原稿です。", result)
        self.assertIn("===ISSUES===\nなし", result)
        self.assertNotIn("<think>", result)
        self.assertNotIn("考え", result)

    def test_cli_noise_and_think_block_combined(self) -> None:
        source = "> echoed\n<think>考え\n中</think>\n本文です。\n/Users/foo"
        self.assertEqual(extract_final_text(source), "本文です。")

    def test_work_note_after_think_is_still_discarded(self) -> None:
        source = "<think>x</think>WebFetchをもう少し試してみます。"
        self.assertEqual(extract_final_text(source), "")

    def test_cli_echoed_unterminated_think_does_not_swallow_body(self) -> None:
        # trim を先に行うことで、CLIエコー行 (">"始まり) に乗った未対応の
        # 開始タグが以降の本文まで巻き添えで捨てるのを防ぐ回帰テスト。
        source = "> 指示: <think>タグを使うな\n本文です。"
        self.assertEqual(extract_final_text(source), "本文です。")

    def test_opening_think_on_echoed_line_still_recovers_body(self) -> None:
        source = "> <think>\n考え中\n</think>\n本文です。"
        self.assertEqual(extract_final_text(source), "本文です。")

    def test_body_before_orphan_closing_think_is_still_discarded(self) -> None:
        # 本文が先・閉じタグが末尾に漏れる形は救済しない（安全側の既知の限界）。
        source = "本文です。\n</think>"
        self.assertEqual(extract_final_text(source), "")

    def test_think_only_output_with_trailing_whitespace_is_empty(self) -> None:
        source = "<think>考えだけ</think>\n\n   \n"
        self.assertEqual(extract_final_text(source), "")


if __name__ == "__main__":
    unittest.main()
