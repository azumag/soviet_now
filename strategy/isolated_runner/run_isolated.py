#!/usr/bin/env python3
"""strategy/isolated_runner/run_isolated.py - issue #35 rootless isolated runner

host側 (strategy/sandbox.sh から呼ばれる) のオーケストレータ。責務:

  1. OS隔離バックエンドの検出 (bubblewrap 優先、無ければ unshare+setpriv、
     どちらも無ければ利用不可=fail-closedを維持)。
  2. `probe` サブコマンド: 実際にサンドボックスを一往復させ、非特権UID・
     read-only root・network無し・出力チャンネルのみ書込み可を実測確認する
     (バイナリの存在チェックだけで「使える」と判定しない)。
  3. `evaluate` サブコマンド: 候補strategyファイルとhelpersを read-only input
     としてのみ渡し、tmpfs workdir・非特権UID・networkなし・env空で
     harness.py を実行し、CPU/メモリ/PID/ファイルサイズ/壁時計/出力サイズの
     上限を強制する。出力は allowlist (evaluation.json 一つ) のみ回収し、
     host側でhash/schemaを再検証してreceiptを組み立てる。
  4. receipt には runner version・input/output hash・resource usage・
     gate結果を **秘密なしで** 記録する (env は起動時点で空、credential path は
     そもそも mount していない)。

このスクリプト自身は AI生成候補を一切 exec/import しない。候補を実際に
execするのは、常にOS隔離の内側で動く harness.py だけである。

Linux依存部分 (bwrap/unshare実行そのもの) は macOS では動かないため、この
モジュールの `detect_backend()` は macOS 等では常に (None, reason) を返し、
`probe` は必ず失敗する。これは意図した fail-closed の挙動であり、バグではない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

RUNNER_VERSION = "isolated-runner/1"
HERE = os.path.dirname(os.path.abspath(__file__))
STRATEGY_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(STRATEGY_DIR)

SANDBOX_UID = 65534
SANDBOX_GID = 65534

# 非秘密の既定 resource limit。evaluate/probe 双方で使う。harness.py 側の
# _load_config の既定値と意図的に同じキー・同じ値にしている (config.json 経由で
# 一致させる。ズレると「外側は許可したが内側で弾かれた」等の紛らわしい失敗になる)。
DEFAULT_LIMITS = {
    "wall_seconds": 30,
    "cpu_seconds": 10,
    "mem_mb": 512,
    "nproc": 16,
    "fsize_kb": 512,
    "nofile": 64,
    "per_fixture_timeout_seconds": 5,
    "max_output_bytes": 262144,  # 256 KiB
    "max_fixtures": 64,
    "max_reason_len": 500,
}

# python3 実体がこの配下に無い場合は「安全に bind できる範囲」を超えるので
# バックエンド自体を使用不可とする (親checkout/credential path 等、意図しない
# ものを bind してしまう事故を避けるための allowlist)。
_SAFE_LIB_ROOTS = ("/usr", "/bin", "/lib", "/lib64", "/sbin", "/opt")


class SetupError(Exception):
    """サンドボックス構築段階の失敗 (バックエンド不在・python3解決不可等)。"""


def _which(name):
    return shutil.which(name)


def detect_backend():
    """(backend_name_or_None, reason) を返す。backend が None なら fail-closed。"""
    if sys.platform != "linux":
        return None, f"unsupported platform for OS isolation: {sys.platform} (Linux only; fail-closed maintained by design)"
    if _which("bwrap"):
        return "bwrap", ""
    if _which("unshare") and _which("setpriv") and _which("chroot"):
        return "unshare", ""
    return None, "neither bubblewrap (bwrap) nor unshare+setpriv+chroot found on PATH"


def _resolve_python3():
    path = _which("python3")
    if not path:
        raise SetupError("python3 not found on PATH")
    real = os.path.realpath(path)
    if not any(real == root or real.startswith(root + "/") for root in _SAFE_LIB_ROOTS):
        raise SetupError(
            f"python3 resolves outside the allowlisted OS library roots ({real!r}); "
            "refusing to bind an unexpected path (would risk exposing more than the OS runtime)"
        )
    return real


def _host_lib_binds():
    """('ro-bind'|'symlink', src, dest) のリスト。/usr /bin /lib /lib64 /sbin の
    うち host に存在するものだけを対象にする。親checkout/credentialは対象外。
    """
    binds = []
    for top in ("usr", "bin", "lib", "lib64", "sbin"):
        host_path = "/" + top
        if not os.path.exists(host_path) and not os.path.islink(host_path):
            continue
        if os.path.islink(host_path):
            target = os.readlink(host_path)
            binds.append(("symlink", target, "/" + top))
        else:
            binds.append(("ro-bind", host_path, "/" + top))
    for etc_path in ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d"):
        if os.path.exists(etc_path):
            kind = "ro-bind"
            binds.append((kind, etc_path, etc_path))
    return binds


def _ulimit_prefix(limits):
    return (
        f"ulimit -t {int(limits['cpu_seconds'])} 2>/dev/null; "
        f"ulimit -v {int(limits['mem_mb']) * 1024} 2>/dev/null; "
        f"ulimit -u {int(limits['nproc'])} 2>/dev/null; "
        f"ulimit -f {int(limits['fsize_kb'])} 2>/dev/null; "
        f"ulimit -n {int(limits['nofile'])} 2>/dev/null; "
    )


def _build_bwrap_argv(input_dir, output_dir, limits, python3_path, harness_rel_argv):
    argv = [
        "bwrap",
        "--unshare-all",
        # bubblewrap 0.9.0 は --disable-userns 利用時に明示的な
        # --unshare-user を要求する (--unshare-all だけでは引数検証を通らない)。
        "--unshare-user",
        # AppArmorでbwrap自身にuserns作成を限定許可する環境でも、
        # 未信頼candidateへnested user namespace作成権限を継承させない。
        "--disable-userns",
        "--assert-userns-disabled",
        "--cap-drop", "ALL",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "LANG", "C",
        "--setenv", "HOME", "/tmp",
        "--hostname", "isolated-runner",
        "--uid", str(SANDBOX_UID),
        "--gid", str(SANDBOX_GID),
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--chdir", "/tmp",
    ]
    for kind, a, b in _host_lib_binds():
        if kind == "ro-bind":
            argv += ["--ro-bind", a, b]
        else:
            argv += ["--symlink", a, b]
    argv += ["--ro-bind", input_dir, "/input"]
    argv += ["--bind", output_dir, "/output"]
    # bwrapの空root自体もread-onlyにする。/tmpと/outputは別mountなので
    # writableのまま維持される。
    argv += ["--remount-ro", "/"]
    argv += ["--"]
    shell_cmd = _ulimit_prefix(limits) + "exec " + " ".join(
        _sh_quote(a) for a in ([python3_path] + harness_rel_argv)
    )
    argv += ["/bin/sh", "-c", shell_cmd]
    return argv


def _sh_quote(s):
    if s == "" or any(c in s for c in " \t\n\"'\\$`"):
        return "'" + s.replace("'", "'\\''") + "'"
    return s


def _build_unshare_script(input_dir, output_dir, limits, python3_path, harness_rel_argv, newroot):
    """unshare フォールバック用のセットアップスクリプトを生成する。

    重要な安全設計:
      - unshare --user --map-root-user は「名前空間の中だけのfake-root」
        (host上では非特権uidのまま) を与える。mount/chroot にはこのfake-rootの
        capabilityが必要なので、先にmountとchrootを終わらせる。
      - chroot 後、**未信頼コードを実行する直前に** setpriv で非特権UID
        (65534) へ実 uid/gid を落とし、no-new-privsも立てる。これにより
        untrusted harness/candidate 実行時点では fake-root 権限は残らない。
      - bwrap と異なり、このパスは pivot_root を使わない単純 chroot なので、
        理論上の chroot 脱出耐性は bwrap 実装より弱い (issue報告書に明記)。
        本番では bubblewrap のインストールを第一選択にすること。
    """
    lines = ["#!/bin/sh", "set -e"]
    lines.append(f"NEWROOT={_sh_quote(newroot)}")
    lines.append('mkdir -p "$NEWROOT"/tmp "$NEWROOT"/proc "$NEWROOT"/input "$NEWROOT"/output "$NEWROOT"/dev')
    for top in ("usr", "bin", "lib", "lib64", "sbin"):
        host_path = "/" + top
        lines.append(f"if [ -L {host_path} ]; then")
        lines.append(f"  ln -s \"$(readlink -f {host_path})\" \"$NEWROOT/{top}\"")
        lines.append(f"elif [ -d {host_path} ]; then")
        lines.append(f"  mkdir -p \"$NEWROOT/{top}\"")
        lines.append(f"  mount --bind -o ro {host_path} \"$NEWROOT/{top}\"")
        lines.append("fi")
    lines.append(f"mount --bind -o ro {_sh_quote(input_dir)} \"$NEWROOT/input\"")
    lines.append(f"mount --bind {_sh_quote(output_dir)} \"$NEWROOT/output\"")
    lines.append('mount -t tmpfs -o size=32m,mode=1777,nosuid,nodev tmpfs "$NEWROOT/tmp"')
    lines.append('mount -t proc -o nosuid,nodev,noexec proc "$NEWROOT/proc"')
    inner_cmd = _ulimit_prefix(limits) + "exec " + " ".join(
        _sh_quote(a) for a in ([python3_path] + harness_rel_argv)
    )
    dropped = (
        f"exec setpriv --reuid={SANDBOX_UID} --regid={SANDBOX_GID} --clear-groups --no-new-privs "
        f"/bin/sh -c {_sh_quote('cd /tmp && ' + inner_cmd)}"
    )
    lines.append(f'exec chroot "$NEWROOT" /bin/sh -c {_sh_quote(dropped)}')
    return "\n".join(lines) + "\n"


class RunResult:
    def __init__(self):
        self.returncode = None
        self.timed_out = False
        self.stdout = ""
        self.stderr = ""
        self.wall_seconds = 0.0
        self.rusage_cpu_seconds = None
        self.rusage_maxrss_kb = None


def _run_sandboxed(argv, wall_seconds):
    """argv (bwrap/unshareの起動列) をenv空・独立プロセスグループで実行し、
    壁時計超過時はプロセスグループ全体をSIGKILLする。RUSAGE_CHILDRENの差分で
    resource usageを取得する (自己申告ではなくカーネルの計測値)。
    """
    result = RunResult()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            start_new_session=True,
        )
    except FileNotFoundError as e:
        raise SetupError(f"failed to launch sandbox backend: {e}")

    try:
        stdout, stderr = proc.communicate(timeout=wall_seconds)
        result.returncode = proc.returncode
        result.stdout = stdout.decode("utf-8", "replace")
        result.stderr = stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        result.timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            stdout, stderr = proc.communicate(timeout=5)
            result.stdout = stdout.decode("utf-8", "replace")
            result.stderr = stderr.decode("utf-8", "replace")
        except Exception:
            pass
        result.returncode = -signal.SIGKILL

    result.wall_seconds = round(time.monotonic() - t0, 3)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    result.rusage_cpu_seconds = round(
        (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime), 3
    )
    result.rusage_maxrss_kb = after.ru_maxrss - before.ru_maxrss if after.ru_maxrss >= before.ru_maxrss else after.ru_maxrss
    return result


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_tree(root):
    """ディレクトリ配下の (相対パス, 内容hash) を安定ソートして集約hashを作る。
    シンボリックリンクは辿らない (存在自体を拒否対象として別途チェックする)。
    """
    h = hashlib.sha256()
    if not os.path.isdir(root):
        return None
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            entries.append(rel)
    for rel in sorted(entries):
        full = os.path.join(root, rel)
        if os.path.islink(full):
            continue
        h.update(rel.encode("utf-8"))
        h.update(_sha256_file(full).encode("utf-8"))
    return h.hexdigest()


def _has_symlinks(root):
    if not os.path.isdir(root):
        return False
    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames) + list(filenames):
            if os.path.islink(os.path.join(dirpath, name)):
                return True
    return False


def _mktemp_base():
    base = os.environ.get("ISOLATED_RUNNER_TMP_BASE")
    if not base:
        eloop_lib_dir = os.environ.get("ELOOP_LIB_DIR")
        if eloop_lib_dir:
            base = os.path.join(eloop_lib_dir, "tmp")
    if not base:
        base = tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    return base


def _new_staging_dirs(prefix):
    base = _mktemp_base()
    root = tempfile.mkdtemp(prefix=f".{prefix}_", dir=base)
    input_dir = os.path.join(root, "input")
    output_dir = os.path.join(root, "output")
    os.makedirs(input_dir)
    os.makedirs(output_dir)
    return root, input_dir, output_dir


def cmd_probe(args):
    backend, reason = detect_backend()
    receipt = {
        "runner_version": RUNNER_VERSION,
        "checked_at": time.time(),
        "backend": backend,
        "available": False,
        "reason": reason,
    }
    if backend is None:
        print(json.dumps(receipt))
        return 1

    root = None
    try:
        python3_path = _resolve_python3()
        root, input_dir, output_dir = _new_staging_dirs("iso_probe")
        shutil.copy2(os.path.join(HERE, "selftest_probe.py"), os.path.join(input_dir, "selftest_probe.py"))
        limits = dict(DEFAULT_LIMITS)
        harness_argv = ["/input/selftest_probe.py", "/output/selftest.json"]

        if backend == "bwrap":
            argv = _build_bwrap_argv(input_dir, output_dir, limits, python3_path, harness_argv)
        else:
            newroot = os.path.join(root, "newroot")
            os.makedirs(newroot, exist_ok=True)
            script = _build_unshare_script(input_dir, output_dir, limits, python3_path, harness_argv, newroot)
            script_path = os.path.join(root, "unshare_setup.sh")
            with open(script_path, "w") as f:
                f.write(script)
            os.chmod(script_path, 0o700)
            argv = ["unshare", "--mount", "--uts", "--ipc", "--pid", "--net", "--user",
                    "--map-root-user", "--fork", "--mount-proc", "/bin/sh", script_path]

        run_result = _run_sandboxed(argv, limits["wall_seconds"])
        receipt["exit_code"] = run_result.returncode
        receipt["timed_out"] = run_result.timed_out
        receipt["wall_seconds"] = run_result.wall_seconds
        # stderr は診断用に短く残すが、host pathやシークレットを含みうる自由形式
        # なので長さを切り詰める (現実装では秘密は元々環境にないが、防御的に)。
        receipt["backend_stderr_tail"] = run_result.stderr[-2000:]

        selftest_path = os.path.join(output_dir, "selftest.json")
        if not os.path.isfile(selftest_path):
            receipt["reason"] = "self-test produced no output (sandbox launch failed or was killed)"
            print(json.dumps(receipt))
            return 1
        with open(selftest_path, encoding="utf-8") as f:
            selftest = json.load(f)
        receipt["selftest"] = selftest

        problems = []
        if selftest.get("uid") in (0, None):
            problems.append(f"sandbox uid not dropped: {selftest.get('uid')!r}")
        if selftest.get("network_blocked") is not True:
            problems.append("network was NOT blocked inside sandbox")
        if selftest.get("root_write_blocked") is not True:
            problems.append("write to sandbox root filesystem was NOT blocked")
        if selftest.get("input_write_blocked") is not True and "input_write_blocked" in selftest:
            problems.append("write to /input (read-only) was NOT blocked")
        if not selftest.get("output_write_ok"):
            problems.append("write to /output (writable channel) failed unexpectedly")

        if problems:
            receipt["available"] = False
            receipt["reason"] = "self-test ran but isolation properties failed: " + "; ".join(problems)
            print(json.dumps(receipt))
            return 1

        receipt["available"] = True
        receipt["reason"] = ""
        print(json.dumps(receipt))
        return 0
    except SetupError as e:
        receipt["reason"] = f"setup error: {e}"
        print(json.dumps(receipt))
        return 1
    finally:
        if root and os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)


def _contract_check(entry):
    if not entry.get("ok"):
        return True, ""  # 失敗として記録済みのエントリはそのまま許容 (evaluate側でgate判定)
    x = entry.get("x")
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False, f"x is not numeric: {x!r}"
    if not (-3.2 <= float(x) <= 3.2):
        return False, f"x out of range: {x!r}"
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False, f"reason is not non-empty string: {reason!r}"
    return True, ""


def cmd_evaluate(args):
    limits = dict(DEFAULT_LIMITS)
    receipt = {
        "runner_version": RUNNER_VERSION,
        "started_at": time.time(),
        "mode": args.mode,
        "gate": "fail",
        "gate_reason": "",
        "resource_usage": {},
    }

    backend, reason = detect_backend()
    receipt["backend"] = backend
    if backend is None:
        receipt["gate_reason"] = f"isolated runner backend unavailable: {reason}"
        _write_receipt(args.receipt_out, receipt)
        print(json.dumps(receipt))
        return 1

    target_file = args.target
    helpers_dir = args.helpers
    if not os.path.isfile(target_file):
        receipt["gate_reason"] = f"target_file not found: {target_file}"
        _write_receipt(args.receipt_out, receipt)
        print(json.dumps(receipt))
        return 1

    if os.path.islink(target_file):
        receipt["gate_reason"] = "target_file is a symlink; refused"
        _write_receipt(args.receipt_out, receipt)
        print(json.dumps(receipt))
        return 1

    if helpers_dir and os.path.isdir(helpers_dir) and _has_symlinks(helpers_dir):
        receipt["gate_reason"] = "helpers_dir contains symlinks; refused"
        _write_receipt(args.receipt_out, receipt)
        print(json.dumps(receipt))
        return 1

    root = None
    try:
        python3_path = _resolve_python3()
        root, input_dir, output_dir = _new_staging_dirs("iso_eval")

        # --- 入力を組み立てる (read-only にする対象。すべてコピーであり、
        #     parent checkout や credential path そのものを mount しない) ---
        shutil.copy2(target_file, os.path.join(input_dir, "strategy_candidate.py"))
        candidate_pre_hash = _sha256_file(target_file)

        helpers_pre_hash = None
        if helpers_dir and os.path.isdir(helpers_dir):
            dst_helpers = os.path.join(input_dir, "strategy_helpers")
            shutil.copytree(
                helpers_dir, dst_helpers,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            helpers_pre_hash = _sha256_tree(dst_helpers)

        analyze_board_src = os.path.join(REPO_ROOT, "analyze_board.py")
        if os.path.isfile(analyze_board_src):
            shutil.copy2(analyze_board_src, os.path.join(input_dir, "analyze_board.py"))

        fixtures_src = os.path.join(HERE, "fixtures")
        dst_fixtures = os.path.join(input_dir, "fixtures")
        os.makedirs(dst_fixtures, exist_ok=True)
        fixture_count = 0
        for name in sorted(os.listdir(fixtures_src)):
            if name.endswith(".json"):
                shutil.copy2(os.path.join(fixtures_src, name), os.path.join(dst_fixtures, name))
                fixture_count += 1
        fixtures_hash = _sha256_tree(dst_fixtures)

        shutil.copy2(os.path.join(HERE, "harness.py"), os.path.join(input_dir, "harness.py"))
        harness_hash = _sha256_file(os.path.join(input_dir, "harness.py"))

        with open(os.path.join(input_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(limits, f, sort_keys=True)

        receipt["input_hashes"] = {
            "candidate_sha256": candidate_pre_hash,
            "helpers_tree_sha256": helpers_pre_hash,
            "fixtures_tree_sha256": fixtures_hash,
            "fixture_count": fixture_count,
            "harness_sha256": harness_hash,
        }
        receipt["limits"] = limits

        harness_argv = ["/input/harness.py", "/input", "/output/evaluation.json"]
        if backend == "bwrap":
            argv = _build_bwrap_argv(input_dir, output_dir, limits, python3_path, harness_argv)
        else:
            newroot = os.path.join(root, "newroot")
            os.makedirs(newroot, exist_ok=True)
            script = _build_unshare_script(input_dir, output_dir, limits, python3_path, harness_argv, newroot)
            script_path = os.path.join(root, "unshare_setup.sh")
            with open(script_path, "w") as f:
                f.write(script)
            os.chmod(script_path, 0o700)
            argv = ["unshare", "--mount", "--uts", "--ipc", "--pid", "--net", "--user",
                    "--map-root-user", "--fork", "--mount-proc", "/bin/sh", script_path]

        run_result = _run_sandboxed(argv, limits["wall_seconds"] + 5)
        receipt["resource_usage"] = {
            "wall_seconds": run_result.wall_seconds,
            "cpu_seconds": run_result.rusage_cpu_seconds,
            "maxrss_kb": run_result.rusage_maxrss_kb,
            "exit_code": run_result.returncode,
            "timed_out": run_result.timed_out,
        }
        receipt["backend_stderr_tail"] = run_result.stderr[-2000:]

        # --- 再検証を厳格に host 側で行う。候補が何を主張していても信用しない。 ---
        if run_result.timed_out:
            receipt["gate_reason"] = f"wall-clock timeout exceeded ({limits['wall_seconds']}s); artifact not harvested"
            _write_receipt(args.receipt_out, receipt)
            print(json.dumps(receipt))
            return 1

        if run_result.returncode != 0:
            receipt["gate_reason"] = f"sandboxed harness exited non-zero or was killed (code={run_result.returncode}); artifact not harvested (likely OOM/crash/signal)"
            _write_receipt(args.receipt_out, receipt)
            print(json.dumps(receipt))
            return 1

        eval_path = os.path.join(output_dir, "evaluation.json")
        if not os.path.isfile(eval_path):
            receipt["gate_reason"] = "no evaluation.json produced; artifact not harvested"
            _write_receipt(args.receipt_out, receipt)
            print(json.dumps(receipt))
            return 1

        out_size = os.path.getsize(eval_path)
        receipt["output_size_bytes"] = out_size
        if out_size > limits["max_output_bytes"]:
            receipt["gate_reason"] = f"evaluation.json exceeds max_output_bytes ({out_size} > {limits['max_output_bytes']}); artifact not harvested"
            _write_receipt(args.receipt_out, receipt)
            print(json.dumps(receipt))
            return 1

        with open(eval_path, encoding="utf-8") as f:
            evaluation = json.load(f)
        receipt["output_sha256"] = _sha256_file(eval_path)

        reported_candidate_hash = evaluation.get("candidate_input_sha256")
        if reported_candidate_hash != candidate_pre_hash:
            receipt["gate_reason"] = (
                "candidate hash mismatch between host pre-image and what the sandbox actually read "
                f"(host={candidate_pre_hash!r} sandbox={reported_candidate_hash!r}); integrity failure, artifact not harvested"
            )
            _write_receipt(args.receipt_out, receipt)
            print(json.dumps(receipt))
            return 1

        if evaluation.get("load_error"):
            receipt["gate_reason"] = f"candidate failed to load inside sandbox: {evaluation['load_error']}"
            receipt["evaluation_summary"] = {"load_error": evaluation["load_error"]}
            _write_receipt(args.receipt_out, receipt)
            print(json.dumps(receipt))
            return 1

        decisions = evaluation.get("decisions", [])
        schema_violations = []
        ok_count = 0
        for entry in decisions:
            ok, err = _contract_check(entry)
            if not ok:
                schema_violations.append({"fixture_id": entry.get("fixture_id"), "error": err})
            elif entry.get("ok"):
                ok_count += 1

        receipt["evaluation_summary"] = {
            "harness_version": evaluation.get("harness_version"),
            "decide_params": evaluation.get("decide_params"),
            "fixtures_total": len(decisions),
            "fixtures_ok": ok_count,
            "fixtures_failed": len(decisions) - ok_count,
            "decisions": [
                {k: v for k, v in d.items() if k in ("fixture_id", "ok", "x", "reason", "error_type", "elapsed_ms")}
                for d in decisions
            ],
        }

        if schema_violations:
            receipt["gate_reason"] = "output schema/contract re-validation failed on host: " + json.dumps(schema_violations)[:1000]
            _write_receipt(args.receipt_out, receipt)
            print(json.dumps(receipt))
            return 1

        if ok_count == 0:
            receipt["gate_reason"] = "no fixture produced a valid decision (all failed/timed out/erred)"
            _write_receipt(args.receipt_out, receipt)
            print(json.dumps(receipt))
            return 1

        receipt["gate"] = "pass"
        receipt["gate_reason"] = f"{ok_count}/{len(decisions)} fixtures produced a contract-valid decision"
        _write_receipt(args.receipt_out, receipt)
        print(json.dumps(receipt))
        return 0

    except SetupError as e:
        receipt["gate_reason"] = f"setup error: {e}"
        _write_receipt(args.receipt_out, receipt)
        print(json.dumps(receipt))
        return 1
    finally:
        if root and os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)


def _write_receipt(path, receipt):
    receipt["finished_at"] = time.time()
    if not path:
        return
    tmp = f"{path}.tmp.{uuid.uuid4().hex}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="OS隔離が実際に機能するか自己診断する")
    p_probe.set_defaults(func=cmd_probe)

    p_eval = sub.add_parser("evaluate", help="候補strategyを隔離runnerで評価する")
    p_eval.add_argument("--target", required=True)
    p_eval.add_argument("--helpers", default="strategy_helpers")
    p_eval.add_argument("--receipt-out", required=True)
    p_eval.add_argument("--mode", choices=["shadow", "enforce"], default="shadow")
    p_eval.set_defaults(func=cmd_evaluate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
