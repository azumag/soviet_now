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

    # --- 壊れた偽tool_callの回帰テスト (MiniMax-M3 の "]<]minimax[>[" 形式) ---

    def test_malformed_minimax_toolcall_tail_is_stripped(self) -> None:
        # 本番実測データ (2026-08-18 17:44 RADIO:news) を単純化した回帰ケース。
        source = (
            "ON_AIR_SCRIPT_START\n"
            "こんばんは。テストです。\n\n"
            "===SUMMARY===\n"
            "テスト,サマリー]<]minimax[>[</invoke>\n"
            "]<]minimax[>[</tool_call>"
        )
        self.assertEqual(
            extract_final_text(source),
            "ON_AIR_SCRIPT_START\nこんばんは。テストです。\n\n===SUMMARY===\nテスト,サマリー",
        )

    def test_malformed_toolcall_with_nested_fake_tags_is_stripped(self) -> None:
        # 過去実測: <invoke name="..."> 等の入れ子もどきが末尾に続くケース。
        source = (
            "本文です。"
            ']<]minimax[>[</invoke name="write_stdin">]<]minimax[>[<chars>]<]minimax[>[</chars>'
            "]<]minimax[>[</invoke>\n]<]minimax[>[</tool_call>"
        )
        self.assertEqual(extract_final_text(source), "本文です。")

    def test_stray_reasoning_tag_inside_toolcall_garbage_does_not_eat_body(
        self,
    ) -> None:
        # 偽tool_callの断片内にたまたま孤立した </think> 等が混ざっている場合、
        # 先に _drop_reasoning を当てると REASONING_CLOSE_HEAD_RE の貪欲マッチが
        # 手前の本文ごと飲み込んでしまう。偽tool_call除去を _drop_reasoning の
        # 前にも当てることで防いでいる回帰テスト。
        source = "本文です。]<]minimax[>[</think>\n]<]minimax[>[</tool_call>"
        self.assertEqual(extract_final_text(source), "本文です。")

    def test_safe_script_envelope_strips_toolcall_tail_after_script(self) -> None:
        # ===SAFE_SCRIPT=== envelope内で、本文の直後に偽tool_call末尾ゴミが
        # 付着するケース。マーカー行と本文は残り、ゴミに巻き込まれた
        # ===ISSUES=== 以降は消える（軽微: ログ/メタ用途、本文は script 判定を
        # 独立して通過済み）。
        source = (
            "===SAFE_SCRIPT===\n"
            "放送原稿です。]<]minimax[>[</invoke>\n"
            "]<]minimax[>[</tool_call>\n"
            "===ISSUES===\nなし"
        )
        self.assertEqual(
            extract_final_text(source),
            "===SAFE_SCRIPT===\n放送原稿です。",
        )

    def test_toolcall_tail_strip_can_leave_a_short_command_fragment(self) -> None:
        # 末尾除去した結果、シェルコマンド断片のような短い残骸が残ることがある
        # (このモジュール単体では「意味のある本文か」までは判定しない)。実際の
        # ラジオ生成パイプラインでは broadcast/radio_engine.sh の
        # _is_valid_radio_talk() が RADIO_FACT_CHECK_MIN_CHARS(既定100字、空白
        # 除去後)未満を別途弾くため、"ls -la" のような短い断片は最終的に
        # 無効判定される。ここでは guard 単体の実挙動のみ固定する。
        source = 'ls -la]<]minimax[>[</cmd>]<]minimax[>[</invoke>\n]<]minimax[>[</tool_call>'
        self.assertEqual(extract_final_text(source), "ls -la")

    def test_malformed_toolcall_at_string_start_leaves_nothing_before_it(self) -> None:
        # MALFORMED_TOOLCALL_TAIL_RE は「最初の出現位置から末尾まで」を無条件に
        # 除去する（PROTOCOL_REJECT_RE による二段防御ではない）。マーカーが
        # 文字列の先頭付近にあれば、手前に残るものが無いため結果的に空になる。
        source = ']<]minimax[>[</invoke>\n本文っぽいが実は続きがある。'
        self.assertEqual(extract_final_text(source), "")

    def test_malformed_toolcall_mid_output_silently_drops_trailing_real_text(
        self,
    ) -> None:
        # 既知の制約: マーカーの後に本物の本文が続いていても、その部分は
        # 除去され復元されない（実測7サンプルでは常に末尾まで続く形だったため
        # この設計を採用したが、未知の亜種でマーカー後に本文が戻るケースが
        # あれば、ここで無言のまま切り捨てられる）。
        source = "前半の本文です。]<]minimax[>[</invoke>\n後半の本文はここにあるが失われる。"
        self.assertEqual(extract_final_text(source), "前半の本文です。")

    # --- WORK_NOTE_RE: 英語の作業メモが文中に埋め込まれるケース ---

    def test_work_note_at_line_start_still_discarded(self) -> None:
        source = "Let's search for the latest information first.\n本文です。"
        self.assertEqual(extract_final_text(source), "")

    def test_work_note_mid_sentence_toolcall_vocabulary_is_discarded(self) -> None:
        # 実測: "...consistently. Let me try a different approach..." のように
        # 文中に作業メモ表現が出現するケース(2026-08-16 RADIO:music_knowledge)。
        # "Let me"は行頭アンカー付きのため文中では未検出だが、この実例特有の
        # ツール用語("exec_command"/"returning empty output")は行頭アンカー無しで
        # 検出する。正当な原稿にこれらの語が自然に登場することは無いため、
        # アンカーを外しても "Let's go!" 等の正当な英語混じり本文には影響しない
        # （test_legitimate_english_phrase_mid_sentence_is_not_falsely_discarded
        # で別途確認）。
        # 参考: 万一この語彙にも一致しない未知の作業メモが本文と混在した場合、
        # 英語のみなら broadcast/radio_quality.sh の _radio_quality_check
        # （RADIO_QUALITY_MIN_JAPANESE_RATIO）が拾うが、日本語本文と混在する
        # 場合はそちらでは拾えないため、この語彙リストが最後の防波堤になる。
        source = (
            "The exec_command tool seems to be returning empty output consistently. "
            "Let me try a different approach and write from my own knowledge."
        )
        self.assertEqual(extract_final_text(source), "")

    def test_legitimate_english_phrase_mid_sentence_is_not_falsely_discarded(
        self,
    ) -> None:
        # 上のケースとのトレードオフの裏付け: 行頭アンカーがあることで、
        # 英語コメントへの返信等に自然に登場する "Let's"/"I will" が
        # 誤って全体棄却されないことを固定する。
        source = "こんにちは！Let's go! 一緒に頑張りましょう。" + "あ" * 100
        self.assertEqual(extract_final_text(source), source)
        source2 = "Hello! I will try my best next game." + "あ" * 100
        self.assertEqual(extract_final_text(source2), source2)

    def test_toolcall_vocabulary_terms_do_not_appear_in_legitimate_output(
        self,
    ) -> None:
        # C-2で追加した無アンカーの語彙("exec_command"/"returning empty output")
        # は正当な原稿・コメント返しに自然には登場しない前提。念のため、
        # それらの語を含まない現実的な英語混じり本文には影響しないことを確認。
        source = "The output was great and the command performed well today." + "あ" * 100
        self.assertEqual(extract_final_text(source), source)


if __name__ == "__main__":
    unittest.main()
