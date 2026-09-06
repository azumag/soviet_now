"""issue #34: AI生成strategyのhost execと権限迂回の停止 — 回帰テスト。

検証対象:
  1. 悪性fixtureのtop-level codeが一度もhost execされない
     (file/env/network/subprocess/dynamic-import/dunder探索の各sentinelに到達しない)
  2. syntax error fixtureは安全に拒否される
  3. 正常な候補 (strategy.py 本体 / strategy_versions/*.py) はAST deny gateを通過する
  4. OS隔離runner未導入時は正常候補でも自動適用されず、理由がVALIDATE_ERRORに出る
     (validate_strategy_with_helpers が fail-closed で reject する)
  5. strategy.py 自体は本テストで一切変更していない (decide hashは不変)

実行:
  python3 -m pytest tests/test_strategy_sandbox_no_host_exec.py -v
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# validate_strategy{,_with_helpers} 呼び出しに必要な最小の bash 環境。
# core/helpers.sh の log() 実体は使わず、テスト内で軽量スタブに差し替える
# (副作用のある broadcast/radio 等を一切 source しないため)。
_BASH_PRELUDE = r'''
log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }
source "__REPO__/strategy/sandbox.sh"
'''


def _run_validate(script_tail: str, cwd: str, env: dict) -> subprocess.CompletedProcess:
    script = _BASH_PRELUDE.replace("__REPO__", str(REPO_ROOT)) + script_tail
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# --- 悪性fixture: top-level (モジュール直下) で複数ベクタを一度に踏む ---
# decide() 自体は無害だが、モジュールがimport/execされた瞬間に走る
# top-levelコードがhostのfile/env/network/subprocessに到達しようとする。
_MALICIOUS_FIXTURE = '''"""malicious fixture for issue #34 regression test."""
import os as _os

_SENTINEL_DIR = _os.environ.get("SENTINEL_DIR", "")


def _hit(name, content="EXECUTED"):
    if not _SENTINEL_DIR:
        return
    with open(_os.path.join(_SENTINEL_DIR, name), "w") as _f:
        _f.write(content)


# 実行された証拠 (どのベクタが成功したかに関わらず一番最初に立つ)
_hit("executed.marker")

# vector: file write
try:
    _hit("file.hit")
except Exception:
    pass

# vector: env exfiltration (host env should never be readable by this file)
try:
    _hit("env.hit", _os.environ.get("HOST_SECRET_PROBE", "absent"))
except Exception:
    pass

# vector: subprocess spawn
try:
    import subprocess as _subprocess
    import sys as _sys
    _subprocess.run([_sys.executable, "-c",
                     "open(%r, 'w').write('x')" % _os.path.join(_SENTINEL_DIR, "subprocess.hit")])
except Exception:
    pass

# vector: network socket construction
try:
    import socket as _socket
    _sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    _sock.close()
    _hit("network.hit")
except Exception:
    pass

# vector: dynamic import via __import__
try:
    __import__("os")
    _hit("dynamic_import.hit")
except Exception:
    pass

# vector: eval-based dynamic import
try:
    eval("__import__('os').getcwd()")
    _hit("eval.hit")
except Exception:
    pass

# vector: dunder introspection walk (classic sandbox escape)
try:
    _base = ().__class__.__bases__[0]
    _subs = _base.__subclasses__()
    _hit("dunder_walk.hit")
except Exception:
    pass

# vector: getattr-based dunder fetch
try:
    _g = getattr(int, "__class__")
    _hit("getattr_dunder.hit")
except Exception:
    pass


def decide(game_state, analysis):
    return {"x": 0.0, "reason": "malicious-fixture-placeholder"}
'''

_SYNTAX_ERROR_FIXTURE = '''"""syntax error fixture for issue #34 regression test."""
import math


def decide(game_state, analysis
    return {"x": 0.0, "reason": "broken"}
'''

_BENIGN_FIXTURE = '''"""benign fixture for issue #34 regression test."""
import math


def decide(game_state, analysis):
    pieces = game_state.get("pieces", [])
    score = math.log(len(pieces) + 1)
    return {"x": round(score, 4), "reason": "benign-fixture"}
'''


class TestMaliciousCandidateNeverExecutedOnHost(unittest.TestCase):
    """受入条件: 悪性fixtureのtop-level codeが一度も実行されない。"""

    def _write_and_validate(self, source: str):
        with tempfile.TemporaryDirectory() as sentinel_dir, tempfile.TemporaryDirectory() as cwd:
            candidate = Path(cwd) / "strategy.py.staging"
            candidate.write_text(source, encoding="utf-8")

            env = os.environ.copy()
            env["SENTINEL_DIR"] = sentinel_dir
            env["HOST_SECRET_PROBE"] = "top-secret-value-must-not-leak"
            env["GAME_STATE"] = "game_state.json"  # 存在しないので旧ランタイムsmokeテスト分岐は素通り

            result = _run_validate(
                'validate_strategy "strategy.py.staging" "nonexistent_helpers"\n'
                'echo "RC=$?"\n'
                'echo "VALIDATE_ERROR=$VALIDATE_ERROR"\n',
                cwd=cwd,
                env=env,
            )
            sentinel_hits = sorted(os.listdir(sentinel_dir))
            return result, sentinel_hits

    def test_malicious_fixture_top_level_code_never_runs(self):
        result, hits = self._write_and_validate(_MALICIOUS_FIXTURE)
        self.assertEqual(
            hits, [],
            msg=f"host sentinel files were created (=host exec happened!): {hits}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("RC=1", result.stdout, msg=f"expected rejection. stdout={result.stdout}")

    def test_malicious_fixture_rejected_by_ast_deny_gate(self):
        result, hits = self._write_and_validate(_MALICIOUS_FIXTURE)
        self.assertEqual(hits, [])
        self.assertIn("ast-deny-gate rejected candidate", result.stdout)
        # 具体的な denylist 検出も出ていること (import allowlist違反 or denied call)
        self.assertTrue(
            "import not allowlisted" in result.stdout
            or "denied call" in result.stdout
            or "denied identifier" in result.stdout,
            msg=f"stdout={result.stdout}",
        )

    def test_host_file_env_network_subprocess_sentinels_never_touched(self):
        """4系統 (file/env/network/subprocess) いずれのsentinelにも到達しないことを個別に確認。"""
        _, hits = self._write_and_validate(_MALICIOUS_FIXTURE)
        for forbidden in ("file.hit", "env.hit", "network.hit", "subprocess.hit",
                          "dynamic_import.hit", "eval.hit", "dunder_walk.hit",
                          "getattr_dunder.hit", "executed.marker"):
            self.assertNotIn(forbidden, hits, msg=f"host sentinel reached: {forbidden}")


class TestSyntaxErrorFixtureRejectedSafely(unittest.TestCase):
    def test_syntax_error_is_rejected_without_crash(self):
        with tempfile.TemporaryDirectory() as cwd:
            candidate = Path(cwd) / "strategy.py.staging"
            candidate.write_text(_SYNTAX_ERROR_FIXTURE, encoding="utf-8")
            env = os.environ.copy()
            env["GAME_STATE"] = "game_state.json"
            result = _run_validate(
                'validate_strategy "strategy.py.staging" "nonexistent_helpers"\n'
                'echo "RC=$?"\n'
                'echo "VALIDATE_ERROR=$VALIDATE_ERROR"\n',
                cwd=cwd,
                env=env,
            )
            self.assertIn("RC=1", result.stdout)
            self.assertIn("SyntaxError", result.stdout,
                          msg=f"stdout={result.stdout}\nstderr={result.stderr}")


class TestNormalCandidatesPassAstGate(unittest.TestCase):
    """受入条件: 既存の strategy.py および strategy_versions/ の正常な候補が
    AST deny gate (静的検証) を通過することを確認する。
    """

    def _validate_file(self, src_path: Path):
        with tempfile.TemporaryDirectory() as cwd:
            candidate = Path(cwd) / "strategy.py"
            candidate.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")
            env = os.environ.copy()
            env["GAME_STATE"] = "game_state.json"
            result = _run_validate(
                'validate_strategy "strategy.py" "nonexistent_helpers"\n'
                'echo "RC=$?"\n'
                'echo "VALIDATE_ERROR=$VALIDATE_ERROR"\n',
                cwd=cwd,
                env=env,
            )
            return result

    def test_benign_fixture_passes(self):
        with tempfile.TemporaryDirectory() as cwd:
            candidate = Path(cwd) / "strategy.py.staging"
            candidate.write_text(_BENIGN_FIXTURE, encoding="utf-8")
            env = os.environ.copy()
            env["GAME_STATE"] = "game_state.json"
            result = _run_validate(
                'validate_strategy "strategy.py.staging" "nonexistent_helpers"\n'
                'echo "RC=$?"\n',
                cwd=cwd,
                env=env,
            )
            self.assertIn("RC=0", result.stdout, msg=f"stdout={result.stdout}\nstderr={result.stderr}")

    def test_active_root_strategy_py_passes(self):
        result = self._validate_file(REPO_ROOT / "strategy.py")
        self.assertIn("RC=0", result.stdout, msg=f"stdout={result.stdout}\nstderr={result.stderr}")

    def test_strategy_versions_candidates_pass_or_have_preexisting_unrelated_failure(self):
        """strategy_versions/*.py を全件チェックする。AST deny gate由来の拒否
        (import not allowlisted / denied call / denied identifier / dunder) が
        一件も無いことを確認する。既存の decide() load-before-assign チェック等、
        本issueと無関係な既存バリデーションの失敗は許容する (pre-existing)。
        """
        version_files = sorted(glob.glob(str(REPO_ROOT / "strategy_versions" / "*.py")))
        self.assertGreater(len(version_files), 0, "no strategy_versions fixtures found")
        ast_gate_failures = []
        for vf in version_files:
            result = self._validate_file(Path(vf))
            if "ast-deny-gate rejected candidate" in result.stdout:
                ast_gate_failures.append((vf, result.stdout))
        self.assertEqual(
            ast_gate_failures, [],
            msg=f"AST deny gate false-positive on legitimate historical candidates: {ast_gate_failures}",
        )


class TestReachableHelperAstGate(unittest.TestCase):
    def _validate(self, candidate_source: str, helper_sources: dict[str, str]):
        with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as sentinel_dir:
            root = Path(cwd)
            (root / "strategy.py").write_text(candidate_source, encoding="utf-8")
            helpers = root / "strategy_helpers"
            helpers.mkdir()
            (helpers / "__init__.py").write_text("", encoding="utf-8")
            for name, source in helper_sources.items():
                (helpers / f"{name}.py").write_text(source, encoding="utf-8")
            env = os.environ.copy()
            env["SENTINEL_DIR"] = sentinel_dir
            env["GAME_STATE"] = "game_state.json"
            result = _run_validate(
                'validate_strategy "strategy.py" "strategy_helpers"\n'
                'echo "RC=$?"\n'
                'echo "VALIDATE_ERROR=$VALIDATE_ERROR"\n',
                cwd=cwd, env=env,
            )
            return result, sorted(os.listdir(sentinel_dir))

    def test_reachable_malicious_helper_is_rejected_without_host_execution(self):
        candidate = '''import math
from strategy_helpers import evil

def decide(game_state, analysis):
    return evil.choose(game_state)
'''
        helper = '''import os

marker = os.environ.get("SENTINEL_DIR", "")
if marker:
    open(os.path.join(marker, "helper-executed"), "w").write("x")

def choose(game_state):
    return {"x": 0.0, "reason": "evil"}
'''
        result, hits = self._validate(candidate, {"evil": helper})
        self.assertEqual(hits, [], msg=f"helper executed on host: {hits}")
        self.assertIn("RC=1", result.stdout)
        self.assertIn("ast-deny-gate rejected helper", result.stdout)
        self.assertIn("strategy_helpers/evil.py", result.stdout)

    def test_unreachable_malicious_helper_does_not_poison_candidate(self):
        candidate = '''import math
from strategy_helpers import safe

def decide(game_state, analysis):
    return safe.choose(game_state)
'''
        safe = '''import math

def choose(game_state):
    return {"x": math.sin(0.0), "reason": "safe"}
'''
        evil = '''import os

def choose(game_state):
    return {"x": 0.0, "reason": os.getcwd()}
'''
        result, hits = self._validate(candidate, {"safe": safe, "evil": evil})
        self.assertEqual(hits, [])
        self.assertIn("RC=0", result.stdout, msg=f"stdout={result.stdout} stderr={result.stderr}")


class TestIsolatedRunnerFailClosed(unittest.TestCase):
    """受入条件: 正常候補もOS隔離runner未導入時は自動applyされず、理由がstatusに出る。"""

    def test_validate_strategy_with_helpers_fails_closed_for_valid_candidate(self):
        with tempfile.TemporaryDirectory() as cwd:
            strategy_helpers_dst = Path(cwd) / "strategy_helpers"
            strategy_helpers_dst.mkdir()
            for src in (REPO_ROOT / "strategy_helpers").glob("*.py"):
                (strategy_helpers_dst / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            candidate = Path(cwd) / "strategy.py"
            candidate.write_text((REPO_ROOT / "strategy.py").read_text(encoding="utf-8"), encoding="utf-8")

            env = os.environ.copy()
            env["GAME_STATE"] = "game_state.json"
            result = _run_validate(
                'validate_strategy_with_helpers "strategy.py" "strategy_helpers"\n'
                'echo "RC=$?"\n'
                'echo "VALIDATE_ERROR=$VALIDATE_ERROR"\n',
                cwd=cwd,
                env=env,
            )
            self.assertIn("RC=1", result.stdout, msg=f"stdout={result.stdout}\nstderr={result.stderr}")
            self.assertIn("OS隔離runner未導入", result.stdout)
            self.assertIn("fail-closed", result.stdout)
            # gate自体 (deny-list) で落ちたのではなく、隔離runner可否で落ちたことを区別する
            self.assertNotIn("ast-deny-gate rejected candidate", result.stdout)
            self.assertNotIn("SyntaxError", result.stdout)

    def test_isolated_runner_available_helper_has_no_env_override(self):
        """再有効化が環境変数で戻せる形になっていないことを確認する
        (issue #34: 権限迂回optionと同様、runtime toggleを提供しない)。
        """
        source = (REPO_ROOT / "strategy/sandbox.sh").read_text(encoding="utf-8")
        start = source.index("_strategy_isolated_runner_available()")
        end = source.index("\n}\n", start)
        body = source[start:end]
        self.assertNotIn("${", body, msg="isolated runner gate must not read any env var override")


class TestApprovalBypassRemoved(unittest.TestCase):
    """受入条件: 生成agentの権限迂回optionが削除され、環境変数で戻せる形になっていない。"""

    def test_codex_args_do_not_contain_dangerous_bypass_flag(self):
        source = (REPO_ROOT / "strategy/ai.sh").read_text(encoding="utf-8")
        start = source.index("local -a codex_args=(")
        end = source.index(")", start)
        codex_args_block = source[start:end]
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex_args_block)

    def test_no_env_var_reintroduces_bypass(self):
        source = (REPO_ROOT / "strategy/ai.sh").read_text(encoding="utf-8")
        start = source.index("local -a codex_args=(")
        end = source.index(")", start)
        codex_args_block = source[start:end]
        self.assertNotIn("bypass", codex_args_block.lower())
        self.assertNotIn("${", codex_args_block)


class TestDecideHashUnaffected(unittest.TestCase):
    """受入条件: active strategy hash と game loop は維持される。"""

    def test_strategy_py_not_modified_by_this_change(self):
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD", "--", "strategy.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "", msg=f"strategy.py was modified: {result.stdout}")

    def test_decide_hash_stable_and_game_loop_validate_passes(self):
        # extract_decide_hash.py は変更していないので、同一 strategy.py に対して
        # 決定的に同じ hash を返すことを確認する (回帰指標)。
        h1 = subprocess.run(
            [sys.executable, "extract_decide_hash.py", "strategy.py"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout.strip()
        h2 = subprocess.run(
            [sys.executable, "extract_decide_hash.py", "strategy.py"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout.strip()
        self.assertTrue(h1, "extract_decide_hash.py returned empty hash")
        self.assertEqual(h1, h2)

        # soren_loop.sh / eloop.sh の起動時ゲートと同じ呼び出し形 (bare validate_strategy)
        # が、現行の active strategy.py に対して今も成功することを確認する。
        with tempfile.TemporaryDirectory() as cwd:
            candidate = Path(cwd) / "strategy.py"
            candidate.write_text((REPO_ROOT / "strategy.py").read_text(encoding="utf-8"), encoding="utf-8")
            env = os.environ.copy()
            env["GAME_STATE"] = "game_state.json"  # ファイルは存在しない = 旧ランタイムsmokeテスト分岐は無効化済み
            result = _run_validate(
                'validate_strategy\n'
                'echo "RC=$?"\n',
                cwd=cwd,
                env=env,
            )
            self.assertIn("RC=0", result.stdout, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


if __name__ == "__main__":
    unittest.main()
