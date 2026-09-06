"""issue #35: rootless隔離runner (strategy/isolated_runner/) の回帰テスト。

このファイルには2種類のテストが混在する:

  1. OS非依存の部分 (常に実行される): harness.py の評価ロジック・
     run_isolated.py のhost側hash/schema再検証ロジック・
     _strategy_isolated_runner_available() の実測fail-closed挙動・
     validate_strategy_with_helpers() の配線。macOS でも Linux でも
     同じ結果になるべき部分。
  2. Linux + OS隔離バックエンド (bwrap または unshare+setpriv+chroot) が
     実際に機能する環境でのみ意味を持つ部分: 悪性fixture (env読取・
     host file読書き・外部接続・subprocess・fork bomb) がサンドボックス内で
     完結しhost副作用0であることの実測。macOS (このリポジトリの開発機) では
     `_strategy_isolated_runner_available()` が実測でfalseになるため、
     これらのテストは `unittest.skipUnless` で明示的にskipされる —
     「合格した」という嘘の報告を避けるための意図的な設計。
     Linuxで実際に検証する手順は strategy/isolated_runner/verify_on_linux.sh
     を参照。

実行:
    python3 -m pytest tests/test_isolated_runner_rootless.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ISOLATED_RUNNER_DIR = REPO_ROOT / "strategy" / "isolated_runner"
sys.path.insert(0, str(REPO_ROOT))


def _load_run_isolated_module():
    spec = importlib.util.spec_from_file_location(
        "isolated_runner_run_isolated", str(ISOLATED_RUNNER_DIR / "run_isolated.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_isolated = _load_run_isolated_module()

_BASH_PRELUDE = r'''
log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }
source "__REPO__/strategy/sandbox.sh"
'''


def _run_bash(script_tail: str, cwd: str, env: dict | None = None) -> subprocess.CompletedProcess:
    script = _BASH_PRELUDE.replace("__REPO__", str(REPO_ROOT)) + script_tail
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env=env if env is not None else os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _isolation_actually_works() -> bool:
    """本物のOS隔離が使える環境かどうかを実測する (probeを実際に叩く)。"""
    try:
        proc = subprocess.run(
            [sys.executable, str(ISOLATED_RUNNER_DIR / "run_isolated.py"), "probe"],
            capture_output=True, text=True, timeout=60,
        )
        return proc.returncode == 0
    except Exception:
        return False


_ISOLATION_WORKS_HERE = _isolation_actually_works()


# ---------------------------------------------------------------------------
# 1. _strategy_isolated_runner_available() の実測 fail-closed 挙動
# ---------------------------------------------------------------------------

class TestAvailabilityRealDetection(unittest.TestCase):
    def test_probe_and_bash_function_agree(self):
        """run_isolated.py probe の終了コードと、sandbox.sh の
        _strategy_isolated_runner_available() の戻り値が一致すること
        (どちらも同じ判定ロジックを指しているはずなので)。"""
        probe = subprocess.run(
            [sys.executable, str(ISOLATED_RUNNER_DIR / "run_isolated.py"), "probe"],
            capture_output=True, text=True, timeout=60,
        )
        with tempfile.TemporaryDirectory() as cwd:
            result = _run_bash(
                '_strategy_isolated_runner_available\n'
                'echo "RC=$?"\n',
                cwd=str(REPO_ROOT),  # strategy/isolated_runner/run_isolated.py への相対パス解決のためrepo rootで実行
            )
        bash_rc = 0 if "RC=0" in result.stdout else 1
        probe_rc = 0 if probe.returncode == 0 else 1
        self.assertEqual(
            bash_rc, probe_rc,
            msg=f"probe.returncode={probe.returncode} probe.stdout={probe.stdout!r} "
                f"bash result.stdout={result.stdout!r}",
        )

    @unittest.skipIf(_ISOLATION_WORKS_HERE, "この環境ではOS隔離が実際に機能する (Linux+bwrap/unshare)")
    def test_unavailable_on_this_host_with_explicit_reason(self):
        """このリポジトリの開発機 (macOS 等、隔離バックエンドが無い環境) では
        _strategy_isolated_runner_available() が実測でfalseになり、
        理由が読み取れることを確認する。"""
        probe = subprocess.run(
            [sys.executable, str(ISOLATED_RUNNER_DIR / "run_isolated.py"), "probe"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertNotEqual(probe.returncode, 0)
        payload = json.loads(probe.stdout)
        self.assertFalse(payload["available"])
        self.assertTrue(payload["reason"], msg="reason が空: fail-closedの根拠が記録されていない")


class TestValidateStrategyWithHelpersWiring(unittest.TestCase):
    """validate_strategy_with_helpers が実際に _strategy_isolated_runner_available /
    _strategy_isolated_runner_evaluate を経由すること、かつ #34 で固定した
    fail-closedメッセージが (隔離runner不在の環境では) 今も変わらず出ることを
    確認する。"""

    @unittest.skipIf(_ISOLATION_WORKS_HERE, "この環境ではOS隔離が実際に機能する")
    def test_fails_closed_with_original_34_message_when_unavailable(self):
        with tempfile.TemporaryDirectory() as cwd:
            helpers_dst = Path(cwd) / "strategy_helpers"
            helpers_dst.mkdir()
            for src in (REPO_ROOT / "strategy_helpers").glob("*.py"):
                (helpers_dst / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            candidate = Path(cwd) / "strategy.py"
            candidate.write_text((REPO_ROOT / "strategy.py").read_text(encoding="utf-8"), encoding="utf-8")

            result = _run_bash(
                'validate_strategy_with_helpers "strategy.py" "strategy_helpers"\n'
                'echo "RC=$?"\n'
                'echo "VALIDATE_ERROR=$VALIDATE_ERROR"\n',
                cwd=cwd,
            )
            self.assertIn("RC=1", result.stdout, msg=f"stdout={result.stdout}\nstderr={result.stderr}")
            self.assertIn("OS隔離runner未導入", result.stdout)
            self.assertIn("fail-closed", result.stdout)


# ---------------------------------------------------------------------------
# 2. harness.py のコアロジック (OS隔離の"内側"で動く部分を、隔離なしで直接
#    実行してロジック自体を検証する。信頼済みharnessコード自身のテストであり、
#    「隔離が機能しているか」のテストではない — それは上のprobeテストの役目)。
# ---------------------------------------------------------------------------

def _stage_harness_input(input_dir: Path, candidate_source: str, config: dict | None = None,
                          include_helpers: bool = True, include_fixtures: bool = True):
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "strategy_candidate.py").write_text(candidate_source, encoding="utf-8")
    if include_helpers:
        dst = input_dir / "strategy_helpers"
        dst.mkdir()
        for src in (REPO_ROOT / "strategy_helpers").glob("*.py"):
            (dst / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    ab_src = REPO_ROOT / "analyze_board.py"
    if ab_src.is_file():
        (input_dir / "analyze_board.py").write_text(ab_src.read_text(encoding="utf-8"), encoding="utf-8")
    if include_fixtures:
        dst = input_dir / "fixtures"
        dst.mkdir()
        for src in (ISOLATED_RUNNER_DIR / "fixtures").glob("*.json"):
            (dst / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (input_dir / "harness.py").write_text(
        (ISOLATED_RUNNER_DIR / "harness.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    cfg = {
        "cpu_seconds": 10, "mem_mb": 512, "nproc": 16, "fsize_kb": 512,
        "nofile": 64, "per_fixture_timeout_seconds": 3, "max_reason_len": 500, "max_fixtures": 64,
    }
    if config:
        cfg.update(config)
    (input_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


def _run_harness(input_dir: Path, output_path: Path):
    return subprocess.run(
        [sys.executable, str(input_dir / "harness.py"), str(input_dir), str(output_path)],
        capture_output=True, text=True, timeout=60,
    )


class TestHarnessMatchesDirectDecideCalls(unittest.TestCase):
    """受入条件: 正常strategy corpusを評価でき、旧runner(strategy.decide()の
    直接呼び出し)とのscore/decision差分を説明できる。ここでは実測で差分ゼロ
    であることを示す (harness.py はOS隔離の内側で動く前提のコードだが、
    ロジック自体は隔離の有無に依存しないので、非隔離環境でも直接検証できる)。
    """

    def test_root_strategy_decisions_match_direct_calls_exactly(self):
        sys.path.insert(0, str(REPO_ROOT))
        import strategy as root_strategy  # noqa: E402  (信頼済みhostコードを直接import、これは検証のためだけの比較オラクル)

        with tempfile.TemporaryDirectory() as td:
            input_dir = Path(td) / "input"
            output_path = Path(td) / "evaluation.json"
            _stage_harness_input(input_dir, (REPO_ROOT / "strategy.py").read_text(encoding="utf-8"))

            proc = _run_harness(input_dir, output_path)
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            evaluation = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIsNone(evaluation["load_error"])
            self.assertGreater(len(evaluation["decisions"]), 0)

            fixtures_dir = ISOLATED_RUNNER_DIR / "fixtures"
            diffs = []
            for entry in evaluation["decisions"]:
                self.assertTrue(entry["ok"], msg=f"fixture failed unexpectedly: {entry}")
                fx = json.loads((fixtures_dir / f"{entry['fixture_id']}.json").read_text(encoding="utf-8"))
                oracle = root_strategy.decide(fx["game_state"], fx["analysis"])
                if oracle["x"] != entry["x"] or oracle["reason"] != entry["reason"]:
                    diffs.append({
                        "fixture_id": entry["fixture_id"],
                        "harness": {"x": entry["x"], "reason": entry["reason"]},
                        "oracle_direct_call": {"x": oracle["x"], "reason": oracle["reason"]},
                    })
            self.assertEqual(
                diffs, [],
                msg=f"harness と旧runner相当(decide()直接呼び出し)のあいだにscore/decision差分がある: {diffs}",
            )

    def test_all_output_decisions_satisfy_old_runtime_contract(self):
        """旧 (host exec時代の) assert_decision と同じ契約: x∈[-3.2,3.2] かつ非bool数値、
        reasonは非空文字列。"""
        with tempfile.TemporaryDirectory() as td:
            input_dir = Path(td) / "input"
            output_path = Path(td) / "evaluation.json"
            _stage_harness_input(input_dir, (REPO_ROOT / "strategy.py").read_text(encoding="utf-8"))
            proc = _run_harness(input_dir, output_path)
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            evaluation = json.loads(output_path.read_text(encoding="utf-8"))
            for entry in evaluation["decisions"]:
                self.assertTrue(entry["ok"], msg=entry)
                x = entry["x"]
                self.assertIsInstance(x, (int, float))
                self.assertNotIsInstance(x, bool)
                self.assertTrue(-3.2 <= x <= 3.2, msg=f"x out of range: {entry}")
                self.assertIsInstance(entry["reason"], str)
                self.assertTrue(entry["reason"].strip())


class TestHarnessHandlesBrokenCandidates(unittest.TestCase):
    def test_syntax_error_candidate_reports_load_error_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            input_dir = Path(td) / "input"
            output_path = Path(td) / "evaluation.json"
            _stage_harness_input(input_dir, "def decide(game_state, analysis\n    return {}\n")
            proc = _run_harness(input_dir, output_path)
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            evaluation = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(evaluation["load_error"])
            self.assertIn("SyntaxError", evaluation["load_error"])
            self.assertEqual(evaluation["decisions"], [])

    def test_missing_decide_reports_load_error(self):
        with tempfile.TemporaryDirectory() as td:
            input_dir = Path(td) / "input"
            output_path = Path(td) / "evaluation.json"
            _stage_harness_input(input_dir, "x = 1\n")
            proc = _run_harness(input_dir, output_path)
            self.assertEqual(proc.returncode, 1)
            evaluation = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn("decide() not found", evaluation["load_error"])

    def test_decide_raising_is_recorded_per_fixture_not_fatal(self):
        candidate = textwrap.dedent('''
            def decide(game_state, analysis):
                raise ValueError("boom")
        ''')
        with tempfile.TemporaryDirectory() as td:
            input_dir = Path(td) / "input"
            output_path = Path(td) / "evaluation.json"
            _stage_harness_input(input_dir, candidate)
            proc = _run_harness(input_dir, output_path)
            self.assertEqual(proc.returncode, 0)
            evaluation = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertGreater(len(evaluation["decisions"]), 0)
            for entry in evaluation["decisions"]:
                self.assertFalse(entry["ok"])
                self.assertEqual(entry["error_type"], "ValueError")

    def test_out_of_range_x_is_contract_violation(self):
        candidate = textwrap.dedent('''
            def decide(game_state, analysis):
                return {"x": 999.0, "reason": "out of range on purpose"}
        ''')
        with tempfile.TemporaryDirectory() as td:
            input_dir = Path(td) / "input"
            output_path = Path(td) / "evaluation.json"
            _stage_harness_input(input_dir, candidate)
            proc = _run_harness(input_dir, output_path)
            self.assertEqual(proc.returncode, 0)
            evaluation = json.loads(output_path.read_text(encoding="utf-8"))
            for entry in evaluation["decisions"]:
                self.assertFalse(entry["ok"])
                self.assertEqual(entry["error_type"], "contract_violation")

    def test_hanging_decide_is_cut_off_by_per_fixture_timeout(self):
        """decide() が無限ループしても、fixture単位のSIGALRMタイムアウトで
        個別に打ち切られ、harness全体は壁時計内に終了すること
        (外側のwall-clock timeoutが唯一の防衛線ではないことの確認)。"""
        candidate = textwrap.dedent('''
            def decide(game_state, analysis):
                while True:
                    pass
        ''')
        with tempfile.TemporaryDirectory() as td:
            input_dir = Path(td) / "input"
            output_path = Path(td) / "evaluation.json"
            _stage_harness_input(input_dir, candidate, config={"per_fixture_timeout_seconds": 1})
            proc = _run_harness(input_dir, output_path)
            self.assertEqual(proc.returncode, 0, msg=f"harness自体は正常終了するはず: {proc.stdout} {proc.stderr}")
            evaluation = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertGreater(len(evaluation["decisions"]), 0)
            for entry in evaluation["decisions"]:
                self.assertFalse(entry["ok"])
                self.assertEqual(entry["error_type"], "fixture_timeout")


# ---------------------------------------------------------------------------
# 3. run_isolated.py のhost側 hash/schema 再検証ロジック (OS隔離なしで
#    直接呼べる純粋関数部分)
# ---------------------------------------------------------------------------

class TestRunIsolatedHostSideVerification(unittest.TestCase):
    def test_contract_check_accepts_valid_entry(self):
        ok, err = run_isolated._contract_check({"ok": True, "x": 1.0, "reason": "fine"})
        self.assertTrue(ok, msg=err)

    def test_contract_check_rejects_out_of_range(self):
        ok, err = run_isolated._contract_check({"ok": True, "x": 5.0, "reason": "fine"})
        self.assertFalse(ok)
        self.assertIn("out of range", err)

    def test_contract_check_rejects_missing_reason(self):
        ok, err = run_isolated._contract_check({"ok": True, "x": 1.0, "reason": ""})
        self.assertFalse(ok)

    def test_contract_check_rejects_bool_x(self):
        ok, err = run_isolated._contract_check({"ok": True, "x": True, "reason": "fine"})
        self.assertFalse(ok)

    def test_contract_check_passthrough_for_already_failed_entries(self):
        ok, err = run_isolated._contract_check({"ok": False, "error_type": "ValueError"})
        self.assertTrue(ok)

    def test_sha256_tree_changes_with_content(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "a.py").write_text("x = 1\n", encoding="utf-8")
            h1 = run_isolated._sha256_tree(str(d))
            (d / "a.py").write_text("x = 2\n", encoding="utf-8")
            h2 = run_isolated._sha256_tree(str(d))
            self.assertNotEqual(h1, h2)

    def test_has_symlinks_detects_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            real = d / "real.py"
            real.write_text("x = 1\n", encoding="utf-8")
            self.assertFalse(run_isolated._has_symlinks(str(d)))
            (d / "link.py").symlink_to(real)
            self.assertTrue(run_isolated._has_symlinks(str(d)))

    def test_detect_backend_reason_is_never_empty_when_unavailable(self):
        backend, reason = run_isolated.detect_backend()
        if backend is None:
            self.assertTrue(reason)

    def test_bwrap_argv_disables_nested_user_namespaces(self):
        argv = run_isolated._build_bwrap_argv(
            "/tmp/input", "/tmp/output", dict(run_isolated.DEFAULT_LIMITS),
            "/usr/bin/python3", ["/input/harness.py"],
        )
        self.assertIn("--unshare-user", argv)
        self.assertLess(argv.index("--unshare-user"), argv.index("--disable-userns"))
        self.assertIn("--disable-userns", argv)
        self.assertIn("--assert-userns-disabled", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)

    def test_deploy_has_scoped_bwrap_apparmor_userns_profile(self):
        profile = REPO_ROOT / "deploy" / "soren-runtime" / "apparmor" / "usr.bin.bwrap"
        self.assertTrue(profile.is_file(), msg=f"missing tracked AppArmor profile: {profile}")
        text = profile.read_text(encoding="utf-8")
        self.assertIn("profile bwrap /usr/bin/bwrap flags=(unconfined)", text)
        self.assertIn("userns,", text)
        self.assertNotIn("/usr/local/bin/bwrap", text)

    def test_bwrap_argv_remounts_root_readonly(self):
        argv = run_isolated._build_bwrap_argv(
            "/tmp/input", "/tmp/output", dict(run_isolated.DEFAULT_LIMITS),
            "/usr/bin/python3", ["/input/harness.py"],
        )
        idx = argv.index("--remount-ro")
        self.assertEqual(argv[idx + 1], "/")
        self.assertLess(idx, argv.index("--"))

    def test_selftest_probe_persists_output_write_success(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "selftest.json"
            proc = subprocess.run(
                [sys.executable, str(ISOLATED_RUNNER_DIR / "selftest_probe.py"), str(out)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIs(payload.get("output_write_ok"), True)

    def test_linux_verify_falls_back_to_unittest_without_pytest(self):
        script = (ISOLATED_RUNNER_DIR / "verify_on_linux.sh").read_text(encoding="utf-8")
        self.assertIn("_run_test_file()", script)
        self.assertIn("import pytest", script)
        self.assertIn('python3 "$test_file" -v', script)

    def test_linux_verify_distinguishes_receipt_pass_from_shadow_apply_gate(self):
        script = (ISOLATED_RUNNER_DIR / "verify_on_linux.sh").read_text(encoding="utf-8")
        self.assertIn("loop_passes=0", script)
        self.assertNotIn("loop_failures=0", script)
        self.assertIn('_strategy_isolated_runner_evaluate "$LOOP_DIR/strategy.py"', script)
        self.assertIn("shadow mode rejects automatic apply", script)

    def test_linux_verify_does_not_treat_contained_forkbomb_receipt_pass_as_escape(self):
        script = (ISOLATED_RUNNER_DIR / "verify_on_linux.sh").read_text(encoding="utf-8")
        self.assertNotIn("fork bomb candidate が pass 判定になった", script)
        self.assertIn("fork bomb containment", script)

    @unittest.skipIf(_ISOLATION_WORKS_HERE, "この環境ではOS隔離が実際に機能する")
    def test_evaluate_fails_closed_and_receipt_has_no_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            receipt_out = Path(td) / "receipt.json"
            proc = subprocess.run(
                [sys.executable, str(ISOLATED_RUNNER_DIR / "run_isolated.py"), "evaluate",
                 "--target", str(REPO_ROOT / "strategy.py"),
                 "--helpers", str(REPO_ROOT / "strategy_helpers"),
                 "--receipt-out", str(receipt_out),
                 "--mode", "shadow"],
                capture_output=True, text=True, timeout=60,
                env={"PATH": os.environ.get("PATH", ""), "HOST_SECRET_PROBE": "must-not-leak-into-receipt"},
            )
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(receipt_out.is_file())
            receipt_text = receipt_out.read_text(encoding="utf-8")
            receipt = json.loads(receipt_text)
            self.assertEqual(receipt["gate"], "fail")
            self.assertIn("runner_version", receipt)
            self.assertNotIn("must-not-leak-into-receipt", receipt_text)
            self.assertNotIn("HOST_SECRET_PROBE", receipt_text)


# ---------------------------------------------------------------------------
# 4. Linux + 実際に機能するOS隔離バックエンドが無いと意味を成さないテスト。
#    macOS 開発機ではここから先は skip される (嘘の合格を避けるため)。
#    Linux本番相当環境での実行手順は strategy/isolated_runner/verify_on_linux.sh
#    を参照。
# ---------------------------------------------------------------------------

_MALICIOUS_HELPER_TEMPLATE = '''"""issue #35 Linux検証用の悪性helper。strategy_helpers 経由でimportされる想定。
候補strategy.py自体はAST deny gateを通過する必要があるため、危険な処理は
すべてこのhelper側に置く (helpersは構文チェックのみでAST deny gate対象外)。

sentinel_dir は "実在するhost上のディレクトリの絶対パス文字列" をテスト生成時に
直接埋め込む (env経由では渡さない — env自体が inaccessible なサンドボックスの
実効性を試すのが目的なので)。サンドボックスが機能していれば、このパスは
サンドボックス内の mount namespace には存在しない/見えないため、file書込みは
そもそも到達できず失敗するはずである。
"""
import os as _os

SENTINEL_DIR = {sentinel_dir!r}


def hit_host():
    results = {{}}
    try:
        with open(_os.path.join(SENTINEL_DIR, "file.hit"), "w") as f:
            f.write("EXECUTED")
        results["file"] = True
    except Exception:
        results["file"] = False
    try:
        results["env_secret"] = _os.environ.get("HOST_SECRET_PROBE", "absent")
        if results["env_secret"] not in ("absent", None):
            with open(_os.path.join(SENTINEL_DIR, "env.hit"), "w") as f:
                f.write(str(results["env_secret"]))
    except Exception:
        results["env_secret"] = "error"
    try:
        import subprocess as _sp
        import sys as _sys
        _sp.run([_sys.executable, "-c",
                 "open(%r, 'w').write('x')" % _os.path.join(SENTINEL_DIR, "subprocess.hit")],
                timeout=5)
        results["subprocess"] = True
    except Exception:
        results["subprocess"] = False
    try:
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("1.1.1.1", 80))
        s.close()
        with open(_os.path.join(SENTINEL_DIR, "network.hit"), "w") as f:
            f.write("EXECUTED")
        results["network"] = True
    except Exception:
        results["network"] = False
    return results
'''

_BENIGN_CANDIDATE_USING_HELPER = textwrap.dedent('''
    import math
    from strategy_helpers import malicious_probe as _mp


    def decide(game_state, analysis):
        _mp.hit_host()
        pieces = game_state.get("pieces", []) if isinstance(game_state, dict) else []
        return {"x": round(math.log(len(pieces) + 1), 4), "reason": "linux-only security probe fixture"}
''')


@unittest.skipUnless(_ISOLATION_WORKS_HERE, "OS隔離 (bwrap/unshare) がこの環境では機能しない: macOSでは未検証。Linuxで verify_on_linux.sh を使うこと")
class TestLinuxIsolationContainsMaliciousHelper(unittest.TestCase):
    """Linux + bwrap/unshare が実際に機能する環境でのみ実行される。
    悪性helper (env読取・host file書込み・subprocess生成・外部接続) が
    候補経由でimportされても、host側に副作用が一切出ないことを実測する。
    decide() は全fixtureで無条件に呼ばれるため、корpus中の1件でも通れば
    攻撃は実行されている — sentinel_dir は host 上の実在パスを直接埋め込む
    ことで、env伝播に頼らずに「サンドボックス内から host の既知パスへ到達
    できるか」を検証する。
    """

    def test_malicious_helper_produces_zero_host_side_effects(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as sentinel_dir:
            candidate_path = Path(td) / "strategy.py"
            candidate_path.write_text(_BENIGN_CANDIDATE_USING_HELPER, encoding="utf-8")
            helpers_dir = Path(td) / "strategy_helpers"
            helpers_dir.mkdir()
            (helpers_dir / "__init__.py").write_text("", encoding="utf-8")
            helper_source = _MALICIOUS_HELPER_TEMPLATE.format(sentinel_dir=str(sentinel_dir))
            (helpers_dir / "malicious_probe.py").write_text(helper_source, encoding="utf-8")

            receipt_out = Path(td) / "receipt.json"
            proc = subprocess.run(
                [sys.executable, str(ISOLATED_RUNNER_DIR / "run_isolated.py"), "evaluate",
                 "--target", str(candidate_path), "--helpers", str(helpers_dir),
                 "--receipt-out", str(receipt_out), "--mode", "shadow"],
                capture_output=True, text=True, timeout=90,
                env={"PATH": os.environ.get("PATH", ""), "HOST_SECRET_PROBE": "top-secret-must-not-leak"},
            )
            sentinel_hits = sorted(os.listdir(sentinel_dir))
            self.assertEqual(sentinel_hits, [], msg=f"host副作用を検出: {sentinel_hits}; stdout={proc.stdout}")
            receipt_text = receipt_out.read_text(encoding="utf-8") if receipt_out.is_file() else ""
            self.assertNotIn("top-secret-must-not-leak", receipt_text)


if __name__ == "__main__":
    unittest.main()
