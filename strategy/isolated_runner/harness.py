#!/usr/bin/env python3
"""strategy/isolated_runner/harness.py - issue #35 rootless isolated runner

このスクリプトは host からは一切execされない。host 側 (strategy/sandbox.sh /
run_isolated.sh) が組み立てた OS レベルサンドボックス (bubblewrap / unshare など、
非特権UID・read-only root・tmpfs workdir・networkなし・env -i) の "内側" で
python3 から起動される、信頼済み (host-authored, リポジトリにコミット済み) の
harness コードである。

このプロセスの内部でのみ、未信頼の AI生成 strategy 候補 (strategy_candidate.py)
を実際に exec() し、decide() を固定corpusの game_state/analysis fixture に対して
呼び出す。これは意図的な唯一の exec() 実行箇所であり、OS サンドボックスの外へは
一切影響しない前提で設計されている (ネットワーク無し・read-onlyな入力以外書込み
不可・非特権UID・resource limitで擁護)。

引数:
    harness.py <input_dir> <output_path>

<input_dir> に期待する内容 (すべて read-only):
    strategy_candidate.py   - 評価対象の候補コード
    strategy_helpers/       - 候補が import する可能性のあるhelperディレクトリ (任意)
    analyze_board.py        - 候補が import する可能性のある参照コード (任意)
    fixtures/*.json         - {"fixture_id", "game_state", "analysis"} の配列 corpus
    config.json             - resource limit 等の非秘密設定 (任意、無ければ既定値)

<output_path> はサンドボックス内で書込み可能な唯一の場所 (/output 配下) に置く
こと。書き込む内容は構造化 evaluation JSON のみで、秘密情報は一切含まない
(env は起動時点で空、host credential path はそもそも mount されていない)。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback

HARNESS_VERSION = "isolated-runner-harness/1"

# 候補コードの exec 中、一つの fixture が無限ループした場合に個別に打ち切るための
# ソフトタイムアウト。全体の壁時計上限は "外側" (run_isolated.sh が timeout(1) +
# OSサンドボックスのkillで強制する)。BaseException 由来にして、候補コードの
# 雑な `except Exception:` に飲み込まれにくくしている (完全な保証ではない —
# 最終防衛線は外側の wall-clock timeout による SIGKILL)。


class _FixtureTimeout(BaseException):
    pass


def _install_alarm():
    try:
        import signal
    except Exception:
        return None

    if not hasattr(signal, "SIGALRM"):
        return None

    def _handler(signum, frame):
        raise _FixtureTimeout("per-fixture timeout")

    try:
        signal.signal(signal.SIGALRM, _handler)
        return signal
    except Exception:
        return None


def _apply_internal_rlimits(cfg):
    """外側 (bwrap --rlimit-* / ulimit) と重ねて、内側からも自分自身の limit を
    さらに絞る (多層防御)。既に外側で締められているため、ここでの失敗は無視する
    (下げる方向の setrlimit は基本的に非特権でも常に成功するはずだが、念のため)。
    """
    try:
        import resource
    except Exception:
        return

    def _set(name, value):
        if value is None:
            return
        limit = getattr(resource, name, None)
        if limit is None:
            return
        try:
            soft, hard = resource.getrlimit(limit)
            new_soft = min(value, hard) if hard not in (resource.RLIM_INFINITY, -1) else value
            resource.setrlimit(limit, (new_soft, hard))
        except Exception:
            pass

    _set("RLIMIT_CPU", cfg.get("cpu_seconds"))
    _set("RLIMIT_NOFILE", cfg.get("nofile"))
    _set("RLIMIT_FSIZE", cfg.get("fsize_kb", 0) * 1024 if cfg.get("fsize_kb") else None)
    if cfg.get("nproc") is not None:
        _set("RLIMIT_NPROC", cfg.get("nproc"))
    mem_mb = cfg.get("mem_mb")
    if mem_mb is not None:
        # RLIMIT_AS はプラットフォームによって扱いが異なる (Linuxでは仮想アドレス
        # 空間の総量)。失敗しても致命的にしない — 外側 ulimit -v が主防衛線。
        _set("RLIMIT_AS", mem_mb * 1024 * 1024)


def _sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _load_config(input_dir):
    default = {
        "cpu_seconds": 10,
        "mem_mb": 512,
        "nproc": 16,
        "fsize_kb": 512,
        "nofile": 64,
        "per_fixture_timeout_seconds": 5,
        "max_reason_len": 500,
        "max_fixtures": 64,
    }
    cfg_path = os.path.join(input_dir, "config.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                default.update({k: v for k, v in loaded.items() if k in default})
        except Exception:
            pass
    return default


def _load_fixtures(input_dir, max_fixtures):
    fixtures_dir = os.path.join(input_dir, "fixtures")
    out = []
    if not os.path.isdir(fixtures_dir):
        return out
    for name in sorted(os.listdir(fixtures_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(fixtures_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                fx = json.load(f)
            fixture_id = fx.get("fixture_id", name)
            game_state = fx["game_state"]
            analysis = fx["analysis"]
            out.append((fixture_id, game_state, analysis))
        except Exception as e:
            out.append((name, None, None))
            out[-1] = (name, None, None, f"fixture_load_error: {e}")
        if len(out) >= max_fixtures:
            break
    return out


def _contract_check(result):
    """旧 (host exec時代の) assert_decision と同じ出力契約: dict, x/reason 必須,
    x は bool でない数値で -3.2..3.2, reason は非空文字列。
    """
    if not isinstance(result, dict):
        return False, f"result is not dict: {type(result).__name__}"
    if "x" not in result:
        return False, f"missing x: {result!r}"
    if "reason" not in result:
        return False, f"missing reason: {result!r}"
    x = result["x"]
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False, f"x is not numeric: {x!r}"
    if not (-3.2 <= float(x) <= 3.2):
        return False, f"x out of range: {x!r}"
    if not isinstance(result["reason"], str) or not result["reason"].strip():
        return False, f"reason is not non-empty string: {result!r}"
    return True, ""


def run(input_dir, output_path):
    started_at = time.time()
    cfg = _load_config(input_dir)
    _apply_internal_rlimits(cfg)
    sig = _install_alarm()

    candidate_path = os.path.join(input_dir, "strategy_candidate.py")
    candidate_sha256 = _sha256_file(candidate_path)

    out = {
        "harness_version": HARNESS_VERSION,
        "python_version": sys.version,
        "started_at": started_at,
        "candidate_input_sha256": candidate_sha256,
        "decide_params": None,
        "load_error": None,
        "decisions": [],
    }

    # sandbox 内でのみ import できるよう、input_dir を最優先で sys.path に置く。
    # (strategy_helpers / analyze_board を候補が import する場合に解決させるため)
    if input_dir not in sys.path:
        sys.path.insert(0, input_dir)

    try:
        with open(candidate_path, encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        out["load_error"] = f"read_error: {e}"
        _write_output(output_path, out)
        return 1

    module_ns = {"__name__": "strategy_candidate", "__file__": candidate_path}
    try:
        code = compile(source, candidate_path, "exec")
        exec(code, module_ns)  # noqa: S102 - 意図的な唯一のexec (OSサンドボックス内)
    except BaseException as e:  # 候補コードのロード自体が壊れているケース
        out["load_error"] = f"{type(e).__name__}: {e}"
        _write_output(output_path, out)
        return 1

    decide_fn = module_ns.get("decide")
    if not callable(decide_fn):
        out["load_error"] = "decide() not found or not callable"
        _write_output(output_path, out)
        return 1

    try:
        import inspect
        sig_params = list(inspect.signature(decide_fn).parameters.keys())
        out["decide_params"] = sig_params
    except Exception:
        pass

    fixtures = _load_fixtures(input_dir, cfg.get("max_fixtures", 64))
    per_fixture_timeout = cfg.get("per_fixture_timeout_seconds", 5)
    max_reason_len = cfg.get("max_reason_len", 500)

    for entry in fixtures:
        if len(entry) == 4:
            fixture_id, _gs, _an, load_err = entry
            out["decisions"].append({
                "fixture_id": fixture_id,
                "ok": False,
                "error_type": "fixture_load_error",
                "error_message": load_err,
            })
            continue
        fixture_id, game_state, analysis = entry
        t0 = time.perf_counter()
        if sig is not None and hasattr(sig, "alarm"):
            sig.alarm(int(max(1, per_fixture_timeout)))
        try:
            result = decide_fn(game_state, analysis)
            ok, reason_err = _contract_check(result)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
            if ok:
                reason = str(result["reason"])
                if len(reason) > max_reason_len:
                    reason = reason[:max_reason_len] + "...(truncated)"
                out["decisions"].append({
                    "fixture_id": fixture_id,
                    "ok": True,
                    "x": float(result["x"]),
                    "reason": reason,
                    "elapsed_ms": elapsed_ms,
                })
            else:
                out["decisions"].append({
                    "fixture_id": fixture_id,
                    "ok": False,
                    "error_type": "contract_violation",
                    "error_message": reason_err[:max_reason_len],
                    "elapsed_ms": elapsed_ms,
                })
        except _FixtureTimeout:
            out["decisions"].append({
                "fixture_id": fixture_id,
                "ok": False,
                "error_type": "fixture_timeout",
                "error_message": f"decide() exceeded {per_fixture_timeout}s on this fixture",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 3),
            })
        except BaseException as e:
            tb_last = traceback.format_exception_only(type(e), e)
            out["decisions"].append({
                "fixture_id": fixture_id,
                "ok": False,
                "error_type": type(e).__name__,
                "error_message": ("".join(tb_last)).strip()[:max_reason_len],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 3),
            })
        finally:
            if sig is not None and hasattr(sig, "alarm"):
                sig.alarm(0)

    out["finished_at"] = time.time()
    _write_output(output_path, out)
    return 0


def _write_output(output_path, payload):
    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, output_path)


def main():
    if len(sys.argv) != 3:
        print("usage: harness.py <input_dir> <output_path>", file=sys.stderr)
        return 2
    input_dir, output_path = sys.argv[1], sys.argv[2]
    return run(input_dir, output_path)


if __name__ == "__main__":
    sys.exit(main())
