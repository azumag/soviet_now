"""comment_template.md が参照する変数は envsubst 許可リストに列挙されていること。

実発生 (2026-09-05 review): `_comment_length_policy` を export しテンプレートも
`${_comment_length_policy}` で参照していたが、generate_comment_response の
envsubst SHELL-FORMAT 一覧に無かった。GNU envsubst は未列挙変数を置換しないため、
生成プロンプトにリテラル `${_comment_length_policy}` が残り、質問抑制ポリシーが
AI へ届いていなかった。
"""
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "broadcast/comment.sh"
TEMPLATE = REPO_ROOT / "prompts/comment_template.md"

VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z_0-9]*)\}")


def _template_envsubst_lines():
    """comment_template.md を消費する envsubst 行 (次行が "$_comment_template") を返す。"""
    lines = SRC.read_text(encoding="utf-8").split("\n")
    out = []
    for i, ln in enumerate(lines):
        if "envsubst '" in ln and i + 1 < len(lines) and "_comment_template" in lines[i + 1]:
            out.append((i + 1, ln))
    return out


class TestCommentTemplateEnvsubst(unittest.TestCase):
    def test_template_envsubst_lists_exist(self):
        self.assertGreaterEqual(
            len(_template_envsubst_lines()), 2,
            "comment_template.md を処理する envsubst が見つかりません",
        )

    def test_every_template_variable_is_allowed(self):
        template_vars = set(VAR_RE.findall(TEMPLATE.read_text(encoding="utf-8")))
        self.assertTrue(template_vars, "comment_template.md に変数参照がありません")
        for lineno, ln in _template_envsubst_lines():
            allowed = set(VAR_RE.findall(ln))
            missing = sorted(template_vars - allowed)
            self.assertEqual(
                missing, [],
                f"comment.sh:{lineno} の envsubst 許可リストに無い変数: {missing}"
                " (GNU envsubst は未列挙変数を置換しないため、テンプレートにリテラルが残ります)",
            )

    def test_length_policy_is_in_allowed_lists(self):
        """2026-09-05 review 回帰: 質問抑制ポリシー (_comment_length_policy) が
        許可リストに無いと export しても AI へ届かない。"""
        self.assertTrue(_template_envsubst_lines(), "envsubst 行がありません")
        for lineno, ln in _template_envsubst_lines():
            self.assertIn(
                "${_comment_length_policy}", ln,
                f"comment.sh:{lineno} の envsubst 許可リストに _comment_length_policy がありません",
            )

    def test_category_prompt_list_also_allows_length_policy(self):
        """カテゴリ別プロンプトの envsubst (comment.sh の gacha_completion_note 行)
        も _comment_length_policy を許可していること。カテゴリテンプレートは現状
        この変数を参照しないため no-op だが、参照側と許可側の整合を固定する。"""
        lines = SRC.read_text(encoding="utf-8").split("\n")
        category_lists = [
            (i + 1, ln) for i, ln in enumerate(lines)
            if "envsubst '" in ln and "${gacha_completion_note}" in ln
        ]
        self.assertTrue(category_lists, "カテゴリ別プロンプトの envsubst 行が見つかりません")
        for lineno, ln in category_lists:
            self.assertIn(
                "${_comment_length_policy}", ln,
                f"comment.sh:{lineno} の envsubst 許可リストに _comment_length_policy がありません",
            )

    @unittest.skipIf(shutil.which("envsubst") is None, "envsubst not available")
    def test_generated_prompt_expands_policy_and_leaves_no_literal(self):
        """実際に envsubst を実行し、ポリシー本文が展開されリテラルが残らないこと。"""
        marker = "QSUPPRESS-MARKER-質問抑制テスト"
        template_text = TEMPLATE.read_text(encoding="utf-8")
        lineno, ln = _template_envsubst_lines()[0]
        varlist = ln.split("envsubst '")[1].split("'")[0]
        env = dict(os.environ)
        env["_comment_length_policy"] = marker
        result = subprocess.run(
            ["envsubst", varlist],
            input=template_text, capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(marker, result.stdout, "ポリシー本文が展開されていません")
        self.assertNotIn("${_comment_length_policy}", result.stdout, "リテラルが残っています")


if __name__ == "__main__":
    unittest.main()
