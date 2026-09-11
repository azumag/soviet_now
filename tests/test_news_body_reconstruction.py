#!/usr/bin/env python3
"""ニュース本文が取得できない場合の再構成・メタ発言抑止の回帰テスト。

RSS の Google News ブロックは見出ししか持たないことが多く、事前調査(prepass)が
「本文が確認できていない」と報告すると、生成AIがそのまま読み上げてしまう。
本文が無い場合は検索結果から内容を再構成し、素材不足のメタ説明は出さない。
"""
import os
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RADIO_ENGINE = ROOT / "broadcast" / "radio_engine.sh"
RADIO_CORNERS = ROOT / "broadcast" / "radio_corners.sh"
JIJI_PROMPT = ROOT / "prompts" / "radio_jiji.md"


def _extract_shell_function(source: str, name: str) -> str:
    lines = source.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{name}()"):
            start = i
            break
    if start is None:
        raise AssertionError(f"function {name} not found")
    for j in range(start + 1, len(lines)):
        if lines[j] == "}":
            return "\n".join(lines[start : j + 1])
    raise AssertionError(f"function {name} has no closing brace")


def _run_shell_function(name: str, stdin_text: str) -> str:
    func = _extract_shell_function(RADIO_ENGINE.read_text(encoding="utf-8"), name)
    script = func + "\n" + f'printf "%s" "$__TEST_INPUT__" | {name}\n'
    env = dict(os.environ)
    env["__TEST_INPUT__"] = stdin_text
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"{name} failed: {result.stderr}")
    return result.stdout


class NewsBodyReconstructionTest(unittest.TestCase):
    def test_prepass_prompt_reconstructs_from_search_instead_of_flagging_missing_body(self):
        engine = RADIO_ENGINE.read_text(encoding="utf-8")
        self.assertIn("記事本文や一次情報を直接取得できなかった場合は", engine)
        self.assertIn("複数ソースの報道や公式発表から内容を再構成", engine)
        self.assertIn("本文が無いことを理由に調査を止めない", engine)
        self.assertIn("素材が取れなかったことを示すメタ説明は書かない", engine)
        # 失敗文を出力しない従来ルールは維持する
        self.assertIn("WebFetchが失敗した場合、その失敗文を出力しない", engine)

    def test_news_corner_prompt_forbids_body_unavailable_meta(self):
        corners = RADIO_CORNERS.read_text(encoding="utf-8")
        self.assertIn("事前調査メモや検索で裏付けられた範囲から内容を再構成", corners)
        self.assertIn("素材の不足を説明するメタ発言はしないこと", corners)
        # 見出しだけでも再構成することを導入文でも明示する
        self.assertIn("本文が短い・見出しだけ・未取得の場合は", corners)

    def test_jiji_prompt_forbids_body_unavailable_meta(self):
        jiji = JIJI_PROMPT.read_text(encoding="utf-8")
        self.assertIn("本文が確認できない", jiji)
        self.assertIn("検索結果から内容を再構成", jiji)

    def test_research_memo_sanitizer_drops_body_failure_and_work_notes(self):
        raw = "\n".join(
            [
                "- 公開日時: 2026-09-10T09:39:00Z",
                "- 対象・発表時刻の詳細は記事本文が確認できていない",
                "記事自体（朝日・連載）の本文は取得できず",
                "未確認点:",
                "## 未完了タスク",
                "- 朝日新聞記事本文の直接取得（本文の詳細な主張の把握）",
                "## 次の推奨手順",
                "朝日新聞の記事URLを再取得し、記事本文を確認してから、本文生成者が使う事前調査メモを完成させること。",
                "- 米中間選挙投開票日: 2026年11月3日",
            ]
        )
        out = _run_shell_function("_sanitize_radio_research_memo", raw)
        self.assertNotIn("確認できていない", out)
        self.assertNotIn("取得できず", out)
        self.assertNotIn("未確認", out)
        self.assertNotIn("未完了タスク", out)
        self.assertNotIn("本文の直接取得", out)
        self.assertNotIn("推奨手順", out)
        self.assertNotIn("記事本文を確認してから", out)
        self.assertIn("2026年11月3日", out)

    def test_onair_sanitizer_drops_body_unavailable_sentence(self):
        raw = "米中間選挙の話です。本文が確認できませんでした。それでも支持率の動向は注目です。（本文未確認）"
        out = _run_shell_function("_sanitize_onair_text", raw)
        self.assertNotIn("本文が確認できませんでした", out)
        self.assertNotIn("本文未確認", out)
        self.assertIn("支持率の動向は注目です", out)


if __name__ == "__main__":
    unittest.main()
