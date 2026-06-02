#!/usr/bin/env python3
"""strategy_runner.py - 内側ループ: strategy.py を使って1試合を自律プレイ

インフラコード（AI非改変）。
- importlib で strategy.py を動的ロード
- game_state.json を読み、analyze_board で解析
- strategy.decide() でドロップX決定
- commands.txt に書き込み → 消化待ち
- JSONL 履歴記録
- GAMEOVER で最終スコア+ターン数を JSON で stdout 出力

Usage: python3 strategy_runner.py
"""

import hashlib
import importlib
import importlib.util
import json
import math
import os
import signal
import subprocess
import sys
import time

# --- 定数 ---
GAME_STATE = "game_state.json"
COMMANDS = "commands.txt"
HISTORY_DIR = "game_history"
HISTORY_FILE = os.path.join(HISTORY_DIR, "latest.jsonl")
RUSSIA_WORKER_PID_FILE = "tmp/state/.russia_celebration_worker.pid"
RUSSIA_CELEBRATION_ENABLED = os.environ.get("RUSSIA_CELEBRATION_ENABLED", "0") != "0"
# Runtime の game_state / history 上では type15 までしか観測されていない。
# ロシアは type15 の新規出現、ソ連は makeSorenCount の増加で検知する。
RUSSIA_TYPE = 15

# 座標変換
GAME_X_MIN = -3.0
GAME_X_MAX = 3.0
CANVAS_X_MIN = 410
CANVAS_X_MAX = 830

# タイミング
POLL_INTERVAL = 0.15      # ポーリング間隔(秒)
SETTLE_REQUIRED = 1       # 静止確認回数
COMMAND_TIMEOUT = 20      # commands.txt 消化待ちタイムアウト(秒)
MOVE_TIMEOUT = 120        # MOVE状態待ちタイムアウト(秒)
DROP_WAIT = 0.3           # ドロップ後の待ち時間(秒)
# commands未消化がこの回数連続したら bridge 非同期と判定しゲーム中断
# (外側 eloop.sh が bridge 再起動して自己回復。0で無効)
BRIDGE_DESYNC_LIMIT = int(os.environ.get("SOREN_BRIDGE_DESYNC_LIMIT", "3") or "3")


_received_signal = None
_fire_and_forget_processes = []

STOP_FILE = "tmp/stop"

def _handle_stop_signal(signum, _frame):
    """SIGINT/SIGTERMをKeyboardInterruptとして扱い、シグナル再送出で確実にbashに伝搬。"""
    global _received_signal
    _received_signal = signum
    raise KeyboardInterrupt(f"signal {signum}")


signal.signal(signal.SIGINT, _handle_stop_signal)
signal.signal(signal.SIGTERM, _handle_stop_signal)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _deadline_crossing_overlay_payload(turn, score, decision, analysis):
    results = analysis.get("results", []) if isinstance(analysis, dict) else []
    if not results or not isinstance(decision, dict):
        return None

    try:
        decision_x = float(decision.get("x", 0.0) or 0.0)
    except Exception:
        decision_x = 0.0
    chosen = min(results, key=lambda r: abs(float(r.get("x", 0.0) or 0.0) - decision_x))
    if not _candidate_has_confident_deadline_contact(chosen, analysis):
        return None

    safe = [r for r in results if not r.get("crosses_deadline", False)]
    landing_safe = [r for r in results if _candidate_has_non_crossing_landing(r)]
    legal = [
        r
        for r in results
        if not r.get("crosses_deadline", False)
        or _candidate_has_non_crossing_landing(r)
        or r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
    ]
    merge_grade = str(chosen.get("merge_grade") or "NO")
    reason = str(decision.get("reason") or "")
    body = (
        f"turn={turn} score={score} x={decision_x:+.2f} "
        f"merge={merge_grade} safe={len(safe)}/{len(results)} "
        f"landing_safe={len(landing_safe)}/{len(results)} "
        f"legal={len(legal)}/{len(results)} "
        f"reason={reason[:180]}"
    )
    if safe or landing_safe:
        return {
            "title": "デッドライン超過: 安全候補あり",
            "body": body,
            "level": "warn",
        }
    if legal:
        return {
            "title": "デッドライン超過: 非超過なし・併合候補あり",
            "body": body,
            "level": "warn",
        }
    return {
        "title": "デッドライン超過: 合法候補なし",
        "body": body,
        "level": "info",
    }


def notify_deadline_crossing_overlay(turn, score, decision, analysis):
    payload = _deadline_crossing_overlay_payload(turn, score, decision, analysis)
    if not payload:
        return
    if not os.path.exists("./overlay_notify.sh"):
        return
    _reap_fire_and_forget_processes()
    env = dict(os.environ)
    env["OVERLAY_NOTIFY_OBS_SHOW"] = "1"
    try:
        proc = subprocess.Popen(
            [
                "./overlay_notify.sh",
                "deadline",
                payload["title"],
                payload["body"],
                payload["level"],
            ],
            cwd=".",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _fire_and_forget_processes.append(proc)
    except Exception as err:
        log(f"WARN: deadline overlay notify failed: {err}")


def _actual_deadline_contact_overlay_payload(turn, score, decision, before_analysis, after_game_state):
    if not isinstance(after_game_state, dict) or not has_deadline_contact(after_game_state):
        return None
    try:
        from analyze_board import calc_reactor_state

        after_reactor = calc_reactor_state(
            after_game_state.get("pieces", []),
            after_game_state.get("shapes", {}),
        )
    except Exception:
        after_reactor = {}

    before_deadline = (
        before_analysis.get("deadline", {}) if isinstance(before_analysis, dict) else {}
    )
    try:
        decision_x = float(decision.get("x", 0.0) or 0.0)
    except Exception:
        decision_x = 0.0
    reason = str(decision.get("reason") or "")
    before_top = _float_or_none(before_deadline.get("top_edge_y"))
    before_text = f"before_top={before_top:.2f} " if before_top is not None else ""
    body = (
        f"turn={turn} score={score} x={decision_x:+.2f} "
        f"{before_text}"
        f"actual_top={float(after_reactor.get('top_edge_y', 0.0) or 0.0):.2f} "
        f"danger={int(after_reactor.get('danger_piece_count', 0) or 0)} "
        f"reason={reason[:180]}"
    )
    return {
        "title": "デッドライン超過: 実画面接触",
        "body": body,
        "level": "warn" if int(after_reactor.get("danger_piece_count", 0) or 0) else "info",
    }


def notify_actual_deadline_contact_overlay(turn, score, decision, before_analysis, after_game_state):
    payload = _actual_deadline_contact_overlay_payload(
        turn, score, decision, before_analysis, after_game_state
    )
    if not payload:
        return
    if not os.path.exists("./overlay_notify.sh"):
        return
    _reap_fire_and_forget_processes()
    env = dict(os.environ)
    env["OVERLAY_NOTIFY_OBS_SHOW"] = "1"
    try:
        proc = subprocess.Popen(
            [
                "./overlay_notify.sh",
                "deadline",
                payload["title"],
                payload["body"],
                payload["level"],
            ],
            cwd=".",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _fire_and_forget_processes.append(proc)
    except Exception as err:
        log(f"WARN: actual deadline overlay notify failed: {err}")


def _reap_fire_and_forget_processes():
    """Non-blocking cleanup for best-effort side-effect subprocesses."""
    if not _fire_and_forget_processes:
        return
    alive = []
    for proc in _fire_and_forget_processes:
        try:
            if proc.poll() is None:
                alive.append(proc)
        except Exception:
            continue
    _fire_and_forget_processes[:] = alive[-16:]


def _float_or_none(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_has_non_crossing_landing(candidate):
    """True when the visible landing pose itself remains below the deadline."""
    if not isinstance(candidate, dict):
        return False
    deadline_y = _float_or_none(candidate.get("deadline_y"))
    if deadline_y is None:
        deadline_y = 3.38
    top_y = _float_or_none(candidate.get("top_y_after_drop"))
    if top_y is None:
        landing_y = _float_or_none(candidate.get("landing_y"))
        if landing_y is None:
            return False
        top_y = landing_y
    if top_y >= deadline_y:
        return False
    edge_top_y = _float_or_none(candidate.get("edge_vertical_top_y"))
    if candidate.get("wall_rotation_risk") and edge_top_y is not None and edge_top_y >= deadline_y:
        return False
    merge_top_y = _float_or_none(candidate.get("merge_result_top_y"))
    if candidate.get("merge_grade") in ("DIRECT", "NEAR") and merge_top_y is not None and merge_top_y >= deadline_y:
        return False
    return True


def _candidate_has_confident_deadline_contact(candidate, analysis):
    """True when a crossing flag is likely to be visible on the real board.

    `crosses_deadline` is intentionally conservative and is also used as a
    strategy penalty. The OBS alert should be stricter: far-below boards have
    many polygon/AABB landing false positives after pieces settle or merge.
    """
    if not isinstance(candidate, dict) or not candidate.get("crosses_deadline", False):
        return False
    if _candidate_has_non_crossing_landing(candidate):
        return False

    deadline = analysis.get("deadline", {}) if isinstance(analysis, dict) else {}
    deadline_y = _float_or_none(candidate.get("deadline_y"))
    if deadline_y is None:
        deadline_y = _float_or_none(deadline.get("deadline_y"))
    if deadline_y is None:
        return True

    top_edge_y = _float_or_none(deadline.get("top_edge_y"))
    if top_edge_y is None and "deadline_crossed" not in deadline and "danger_piece_count" not in deadline:
        return True
    danger_count = int(deadline.get("danger_piece_count", 0) or 0)
    if (
        bool(deadline.get("deadline_crossed", False))
        or danger_count > 0
        or (top_edge_y is not None and top_edge_y >= deadline_y - 0.75)
    ):
        return True

    risk_top = _float_or_none(candidate.get("risk_top_y_after_drop"))
    if risk_top is None:
        risk_top = _float_or_none(candidate.get("top_y_after_drop"))
    if candidate.get("wall_rotation_risk") and risk_top is not None and risk_top >= deadline_y:
        return True

    return (
        candidate.get("merge_grade", "NO") == "NO"
        and risk_top is not None
        and risk_top >= deadline_y + 1.25
    )


def _candidate_has_strategy_deadline_risk(candidate, analysis):
    """True when deadline prediction should affect strategy choice.

    Keep this stricter than raw `crosses_deadline`: live boards can mark every
    candidate crossing even though the settled screen remains below the line.
    DIRECT/NEAR merges are allowed because they can remove the risky contact.
    """
    if not isinstance(candidate, dict) or not candidate.get("crosses_deadline", False):
        return False
    if candidate.get("merge_grade", "NO") in ("DIRECT", "NEAR"):
        return False

    deadline = analysis.get("deadline", {}) if isinstance(analysis, dict) else {}
    deadline_y = _float_or_none(candidate.get("deadline_y"))
    if deadline_y is None:
        deadline_y = _float_or_none(deadline.get("deadline_y"))
    if deadline_y is None:
        deadline_y = 3.38
    top_edge_y = _float_or_none(deadline.get("top_edge_y"))
    danger_count = int(deadline.get("danger_piece_count", 0) or 0)
    if bool(deadline.get("deadline_crossed", False)) or danger_count > 0:
        return True
    results = analysis.get("results", []) if isinstance(analysis, dict) else []
    has_non_crossing = any(
        isinstance(r, dict) and not r.get("crosses_deadline", False)
        for r in results
    )
    if has_non_crossing:
        return True
    return top_edge_y is not None and top_edge_y >= deadline_y - 0.10


def load_game_state():
    """game_state.json を読み込む"""
    try:
        with open(GAME_STATE) as f:
            return json.load(f)
    except Exception:
        return None


def get_state_field(gs):
    """ゲーム状態文字列を取得"""
    if gs is None:
        return ""
    return gs.get("state", "")


SETTLE_FORCE_TIMEOUT = 30.0  # この秒数経過後は速度に関わらず settled 扱い
DEFAULT_FAST_DROP_DEADLINE_CONTACT = True


def strategy_fast_drop_deadline_contact_enabled(strategy):
    """strategy.py のAI調整パラメータで deadline 接触時の即時DROPを切り替える。"""
    raw = getattr(strategy, "FAST_DROP_DEADLINE_CONTACT", DEFAULT_FAST_DROP_DEADLINE_CONTACT)
    if isinstance(raw, str):
        return raw.strip().lower() not in ("0", "false", "no", "off")
    return bool(raw)

def has_deadline_contact(gs):
    """赤線に触れている/赤線タイマー中のピースがあれば即ドロップへ進む。"""
    if gs is None:
        return False
    for p in gs.get("pieces", []):
        try:
            if float(p.get("redLineTime", 0) or 0) > 0:
                return True
        except Exception:
            continue
    try:
        from analyze_board import calc_reactor_state

        reactor = calc_reactor_state(gs.get("pieces", []), gs.get("shapes", {}))
        return bool(reactor.get("deadline_crossed", False))
    except Exception:
        return False
    return False

def is_board_settled(gs, force_after: float = 0.0):
    """盤面が静止しているか (全ピースの速度が閾値以下)
    vy=-5000 等の極端な速度はドロップ待機中のnextピースなので除外する。
    force_after > 0 の場合、その時刻(time.time())を過ぎたら強制 settled 扱い。"""
    if force_after > 0 and time.time() >= force_after:
        return True
    if gs is None:
        return False
    pieces = gs.get("pieces", [])
    if not pieces:
        return True
    # ドロップ待機ピース(vy=-5000等)を除外: |vy|>100 は物理的にありえない
    settled_pieces = [p for p in pieces if abs(p.get("vy", 0)) < 100 and abs(p.get("vx", 0)) < 100]
    if not settled_pieces:
        return True
    max_v = max(abs(p.get("vx", 0))**2 + abs(p.get("vy", 0))**2 for p in settled_pieces)
    return max_v < 0.1


def commands_empty():
    """commands.txt が空かチェック"""
    try:
        with open(COMMANDS) as f:
            return f.read().strip() == ""
    except FileNotFoundError:
        return True


def game_x_to_canvas(game_x):
    """ゲームX座標 → キャンバスX座標"""
    x = max(GAME_X_MIN, min(GAME_X_MAX, game_x))
    cx = int((x - GAME_X_MIN) / (GAME_X_MAX - GAME_X_MIN) * (CANVAS_X_MAX - CANVAS_X_MIN) + CANVAS_X_MIN)
    return cx


def write_drop_command(game_x):
    """ドロップコマンドをcommands.txtに書き込み"""
    cx = game_x_to_canvas(game_x)
    log(f"DROP {game_x:+.2f} → {cx}")
    with open(COMMANDS, "w") as f:
        f.write(f"{cx},350\n")


def wait_commands_done():
    """commands.txt が消化されるまで待つ"""
    for _ in range(int(COMMAND_TIMEOUT / POLL_INTERVAL)):
        if commands_empty():
            return True
        time.sleep(POLL_INTERVAL)
    log("TIMEOUT: commands未消化 → クリア")
    with open(COMMANDS, "w") as f:
        f.write("")
    return False


def load_strategy_module():
    """strategy.py を動的ロード (毎試合最新版を使用)"""
    spec = importlib.util.spec_from_file_location("strategy", "strategy.py")
    if spec is None or spec.loader is None:
        raise ImportError("strategy.py not found")
    mod = importlib.util.module_from_spec(spec)
    # Match normal import semantics so strategy.py can safely use __name__ and
    # sys.modules["strategy"] during runtime reloads.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def get_strategy_hash():
    """strategy.py の decide() ASTハッシュを返す（extract_decide_hash.py と同一方式）"""
    try:
        from extract_decide_hash import compute_hash
        h = compute_hash("strategy.py")
        return h if h else hashlib.md5(open("strategy.py", "rb").read()).hexdigest()[:8]
    except Exception:
        with open("strategy.py", "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:8]


def get_strategy_file_hash():
    """strategy.py 全体のハッシュ。AI調整パラメータだけの変更でも再ロードするために使う。"""
    with open("strategy.py", "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def count_piece_type(game_state, piece_type):
    """盤面上の指定type個数を返す。"""
    if game_state is None:
        return 0
    return sum(1 for p in game_state.get("pieces", []) if p.get("type", 0) == piece_type)


def trigger_russia_celebration_now(score, turn):
    """ロシア建国時の即時処理を別プロセスで発火する。

    handle_russia_celebration() 側で、祝賀読み上げの有無と関係なく
    クリップ作成は先に実行する。ここでは worker 起動を常に試みる。
    """
    try:
        with open("game_count.txt") as f:
            game_num = int((f.read() or "0").strip() or "0") + 1
    except Exception:
        game_num = 0

    cmd = (
        "cd . && "
        f"mkdir -p tmp && echo $$ > {RUSSIA_WORKER_PID_FILE} && "
        f"trap 'rm -f {RUSSIA_WORKER_PID_FILE}' EXIT && "
        "source ./eloop_lib.sh && source ./eloop.sh && "
        f"handle_russia_celebration '{score}' '{turn}' '{game_num}'"
    )
    try:
        subprocess.Popen(
            ["/bin/bash", "-lc", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as e:
        log(f"WARNING: failed to trigger russia celebration: {e}")
        return False


def trigger_soviet_clip_now(score, turn):
    """ソ連建国クリップを検知直後に別プロセスで発火する。"""
    try:
        with open("game_count.txt") as f:
            game_num = int((f.read() or "0").strip() or "0") + 1
    except Exception:
        game_num = 0

    cmd = (
        "cd . && "
        "source ./eloop_lib.sh && "
        f"_create_twitch_clip '☭ ソ連建国! score={score} (Game #{game_num})' '{game_num}'"
    )
    try:
        subprocess.Popen(
            ["/bin/bash", "-lc", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as e:
        log(f"WARNING: failed to trigger soviet clip: {e}")
        return False


def build_analysis(game_state):
    """analyze_board の関数を呼んで analysis dict を構築"""
    try:
        from analyze_board import analyze_drops, calc_reactor_state

        pieces = game_state.get("pieces", [])
        shapes = game_state.get("shapes", {})
        nxt = game_state.get("next", {})
        nt = nxt.get("type", 0)
        nr = nxt.get("r", 0.5)

        results, same_type = analyze_drops(pieces, nt, nr, shapes)
        reactor = calc_reactor_state(pieces, shapes)
        deadline = {
            "deadline_y": reactor.get("deadline_y", 0.0),
            "top_center_y": reactor.get("top_center_y", -5.0),
            "top_edge_y": reactor.get("top_edge_y", -5.0),
            "deadline_margin": reactor.get("deadline_margin", 0.0),
            "deadline_crossed": reactor.get("deadline_crossed", False),
            "danger_piece_count": reactor.get("danger_piece_count", 0),
            "min_redline_time": reactor.get("min_redline_time", 0.0),
        }

        return {
            "results": results,
            "same_type": [
                {"id": p["id"], "type": p["type"], "x": p["x"], "y": p["y"], "r": p["r"]}
                for p in same_type
            ],
            "reactor": reactor,
            "deadline": deadline,
        }
    except Exception as e:
        log(f"WARNING: analyze_board failed: {e}")
        return {"results": [], "same_type": [], "reactor": {}, "error": str(e)}


def enrich_game_state_deadline_fields(game_state, analysis):
    """Expose analyzer deadline fields through game_state for legacy strategies."""
    if not isinstance(game_state, dict):
        return game_state
    if not isinstance(analysis, dict):
        analysis = {}
    reactor = analysis.get("reactor") if isinstance(analysis.get("reactor"), dict) else {}
    deadline = analysis.get("deadline") if isinstance(analysis.get("deadline"), dict) else {}
    if "deadline_crossed" not in game_state:
        game_state["deadline_crossed"] = bool(
            reactor.get("deadline_crossed", deadline.get("deadline_crossed", False))
        )
    if "deadline_margin" not in game_state:
        margin = reactor.get("deadline_margin", deadline.get("deadline_margin", None))
        if margin is not None:
            game_state["deadline_margin"] = margin
    return game_state


def record_turn(history_f, turn, game_state, decision, analysis, russia_created=False, soviet_created=False, strategy_hash=None, score_delta=0):
    """1ターン分の履歴をJSONLに記録"""
    pieces = game_state.get("pieces", [])
    score = game_state.get("score", 0)
    max_y = max((p["y"] for p in pieces), default=-5.0)
    nxt = game_state.get("next", {})
    nxt2 = game_state.get("nextNext", {})

    results = analysis.get("results", [])
    deadline = analysis.get("deadline", {})
    top_edge_y = float(
        deadline.get(
            "top_edge_y",
            max((p["y"] + p.get("r", 0.0) for p in pieces), default=-5.0),
        )
        or -5.0
    )

    # chosen_x に最も近い result を参照（results[0]は最左端なので不正確）
    chosen_x = decision.get("x", 0.0)
    chosen_result = None
    if results:
        chosen_result = min(results, key=lambda r: abs(r["x"] - chosen_x))
    best_grade = chosen_result.get("merge_grade", "NO") if chosen_result else "NO"
    has_merge = chosen_result.get("has_merge", False) if chosen_result else False
    next_type = int(nxt.get("type", 0) or 0)
    same_type_pieces = [
        p for p in pieces if int(p.get("type", 0) or 0) == next_type
    ]
    highest_same_type = None
    closest_same_type_dx = None
    if same_type_pieces:
        highest_same_type = max(
            same_type_pieces,
            key=lambda p: (
                float(p.get("y", -99.0) or -99.0),
                -abs(float(p.get("x", 0.0) or 0.0) - float(chosen_x or 0.0)),
            ),
        )
        closest_same_type_dx = min(
            abs(float(p.get("x", 0.0) or 0.0) - float(chosen_x or 0.0))
            for p in same_type_pieces
        )

    reactor = analysis.get("reactor", {})
    reactive_pairs = len(reactor.get("reactive_pairs", []))
    danger_piece_count = int(deadline.get("danger_piece_count", 0) or 0)
    min_redline_time = float(deadline.get("min_redline_time", 0.0) or 0.0)
    deadline_clean_candidate_count = len([
        r for r in results
        if not r.get("crosses_deadline", False)
        and not r.get("merge_result_crosses_deadline", False)
    ])

    # ピースのスナップショット（軽量化: 位置とtypeのみ）
    piece_snapshot = [
        {
            "id": p["id"],
            "type": p["type"],
            "x": round(p["x"], 2),
            "y": round(p["y"], 2),
            "r": round(float(p.get("r", 0.0) or 0.0), 3),
            **(
                {"rx": round(float(p.get("rx")), 3)}
                if p.get("rx") is not None
                else {}
            ),
            **(
                {"ry": round(float(p.get("ry")), 3)}
                if p.get("ry") is not None
                else {}
            ),
            **(
                {"angle": round(float(p.get("angle")), 1)}
                if p.get("angle") is not None
                else {}
            ),
        }
        for p in pieces
    ]

    record = {
        "turn": turn,
        "score": score,
        "score_delta": score_delta,
        "piece_count": len(pieces),
        "max_y": round(max_y, 2),
        "top_edge_y": round(top_edge_y, 2),
        "deadline_y": round(float(deadline.get("deadline_y", 0.0) or 0.0), 2),
        "deadline_margin": round(float(deadline.get("deadline_margin", 0.0) or 0.0), 2),
        "deadline_crossed": bool(deadline.get("deadline_crossed", False)),
        "danger_piece_count": danger_piece_count,
        "min_redline_time": round(min_redline_time, 2),
        "next_type": next_type,
        "next_next_type": int(nxt2.get("type", 0) or 0),
        "decision_x": round(decision.get("x", 0), 3),
        "decision_reason": decision.get("reason", ""),
        "merge_available": has_merge,
        "best_merge_grade": best_grade,
        "visual_same_type_count": len(same_type_pieces),
        "visual_same_type_highest_y": round(float(highest_same_type.get("y", -5.0)), 2) if highest_same_type else None,
        "visual_same_type_highest_x": round(float(highest_same_type.get("x", 0.0)), 2) if highest_same_type else None,
        "visual_same_type_closest_dx": round(float(closest_same_type_dx), 2) if closest_same_type_dx is not None else None,
        "reactor_reactive_pairs": reactive_pairs,
        "decision_crosses_deadline": bool(chosen_result.get("crosses_deadline", False)) if chosen_result else False,
        "decision_strategy_deadline_risk": _candidate_has_strategy_deadline_risk(chosen_result, analysis) if chosen_result else False,
        "decision_top_y_after_drop": round(float(chosen_result.get("top_y_after_drop", 0.0) or 0.0), 2) if chosen_result else None,
        "decision_risk_top_y_after_drop": round(float(chosen_result.get("risk_top_y_after_drop", 0.0) or 0.0), 2) if chosen_result else None,
        "decision_merge_result_crosses_deadline": bool(chosen_result.get("merge_result_crosses_deadline", False)) if chosen_result else False,
        "decision_merge_result_top_y": round(float(chosen_result.get("merge_result_top_y", 0.0) or 0.0), 2) if chosen_result else None,
        "deadline_safe_candidate_count": len([r for r in results if not r.get("crosses_deadline", False)]),
        "deadline_clean_candidate_count": deadline_clean_candidate_count,
        "deadline_candidate_count": len(results),
        "danger_merge_available": bool(chosen_result.get("danger_merge_available", False)) if chosen_result else False,
        "danger_direct_merge_available": bool(chosen_result.get("danger_direct_merge_available", False)) if chosen_result else False,
        "strategy_hash": strategy_hash,
        "state_snapshot": {"pieces": piece_snapshot},
    }

    if russia_created:
        record["russia_created"] = True
    if soviet_created:
        record["soviet_created"] = True

    history_f.write(json.dumps(record, ensure_ascii=False) + "\n")
    history_f.flush()
    # Overlay deadline notifications are emitted only after the real post-drop
    # screen confirms contact. The conservative per-candidate crossing flag is
    # still recorded for strategy/audit use, but is too noisy for OBS alerts.


def enforce_deadline_safety(decision, analysis, game_state=None):
    """送信直前の最終安全弁: deadline越えと余白消費を差し替える。"""
    results = analysis.get("results", []) if isinstance(analysis, dict) else []
    if not results or not isinstance(decision, dict):
        return decision

    chosen_x = float(decision.get("x", 0.0) or 0.0)
    chosen = min(results, key=lambda r: abs(float(r.get("x", 0.0) or 0.0) - chosen_x))
    deadline_y = float(chosen.get("deadline_y", 3.38) or 3.38)
    deadline_buffer_y = deadline_y - 0.75
    deadline = analysis.get("deadline", {}) if isinstance(analysis, dict) else {}
    current_top_edge_y = float(deadline.get("top_edge_y", -5.0) or -5.0)
    deadline_crossed = bool(deadline.get("deadline_crossed", False))
    danger_piece_count = int(deadline.get("danger_piece_count", 0) or 0)

    grade_rank = {"DIRECT": 0, "NEAR": 1, "FAR": 2, "NO": 3}

    def risk_top(r):
        return float(
            r.get(
                "risk_top_y_after_drop",
                r.get("top_y_after_drop", r.get("landing_y", 999.0)),
            )
            or 999.0
        )

    def rank_candidate(r):
        grade = r.get("merge_grade", "NO")
        return (
            grade_rank.get(grade, 9),
            risk_top(r),
            abs(float(r.get("x", 0.0) or 0.0)),
        )

    next_type = 0
    try:
        next_type = int(((game_state or {}).get("next") or {}).get("type", 0) or 0)
    except Exception:
        next_type = 0

    geometry_radii = {
        1: 0.207,
        2: 0.259,
        3: 0.316,
        4: 0.380,
        5: 0.414,
        6: 0.470,
        7: 0.559,
        8: 0.660,
        9: 0.746,
        10: 0.846,
        11: 0.982,
        12: 1.068,
        13: 1.207,
        14: 1.385,
        15: 1.600,
    }

    def _geom_num(value, default=0.0):
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    geometry_pieces = (game_state or {}).get("pieces") or []
    geometry_floor_y = -5.0
    geometry_top_cache = {}
    try:
        next_r_from_state = _geom_num(((game_state or {}).get("next") or {}).get("r"), 0.0)
    except Exception:
        next_r_from_state = 0.0
    geometry_next_r = next_r_from_state if next_r_from_state > 0 else geometry_radii.get(next_type, 0.5)

    def geometry_top_at_x(x):
        if not geometry_pieces or geometry_next_r <= 0:
            return None
        try:
            x_val = float(x)
        except Exception:
            return None
        x_key = round(x_val, 3)
        if x_key in geometry_top_cache:
            return geometry_top_cache[x_key]
        landing_y = geometry_floor_y + geometry_next_r
        for p in geometry_pieces:
            pr = _geom_num(p.get("r"), 0.0)
            if pr <= 0:
                pr = geometry_radii.get(int(p.get("type", 0) or 0), 0.5)
            px = _geom_num(p.get("x"))
            py = _geom_num(p.get("y"), geometry_floor_y)
            dx = abs(x_val - px)
            contact = geometry_next_r + pr
            if dx >= contact:
                continue
            y = py + math.sqrt(max(contact * contact - dx * dx, 0.0))
            if y > landing_y:
                landing_y = y
        top_y = landing_y + geometry_next_r
        geometry_top_cache[x_key] = top_y
        return top_y

    def risky_merge_result_deadline(r):
        return (
            isinstance(r, dict)
            and r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
            and bool(r.get("merge_result_crosses_deadline", False))
            and has_deadline_clean_candidate
        )

    non_crossing_safe = [r for r in results if not r.get("crosses_deadline", False)]
    landing_safe = (
        []
        if non_crossing_safe
        else [r for r in results if _candidate_has_non_crossing_landing(r)]
    )
    safe = non_crossing_safe or landing_safe
    deadline_clean_candidates = [
        r for r in results
        if isinstance(r, dict)
        and not r.get("crosses_deadline", False)
        and not r.get("merge_result_crosses_deadline", False)
    ]
    has_deadline_clean_candidate = bool(deadline_clean_candidates)
    merge_allowed = [
        r for r in results
        if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        and not risky_merge_result_deadline(r)
    ]
    deadline_legal = list(safe)
    for r in merge_allowed:
        if r not in deadline_legal:
            deadline_legal.append(r)
    safe_direct = [r for r in deadline_legal if r.get("merge_grade", "NO") == "DIRECT"]
    safe_merge = [
        r for r in deadline_legal if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
    ]
    buffered = [r for r in safe if risk_top(r) <= deadline_buffer_y]
    chosen_top = risk_top(chosen)
    min_risk_candidate = min(
        results,
        key=lambda r: (
            risk_top(r),
            abs(float(r.get("x", 0.0) or 0.0)),
        ),
    )
    min_risk_top = risk_top(min_risk_candidate)
    large_deadline_pressure = (
        current_top_edge_y >= deadline_y - 0.75
            and next_type >= 9
            and (safe or current_top_edge_y >= deadline_y - 0.10)
    )
    piece_count = len((game_state or {}).get("pieces") or [])
    reactor = analysis.get("reactor", {}) if isinstance(analysis, dict) else {}
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0
    late_pressure = (
        piece_count >= 35
        or current_top_edge_y >= deadline_y - 1.0
        or (
            chosen.get("crosses_deadline", False)
            and current_top_edge_y >= deadline_y - 0.75
        )
    )
    deadline_contact_pressure = (
        deadline_crossed
        or current_top_edge_y >= deadline_y - 0.20
        or danger_piece_count > 0
    )
    deadline_precontact_pressure = (
        deadline_contact_pressure
        or current_top_edge_y >= deadline_y - 0.75
        or (
            piece_count >= 33
            and reactive_pair_count >= 3
            and current_top_edge_y >= deadline_y - 1.5
        )
    )
    all_crossing_measurement_noise = (
        not deadline_contact_pressure
        and current_top_edge_y < deadline_y - 0.75
        and results
        and all(r.get("crosses_deadline", False) for r in results)
        and all("deadline_y" in r for r in results)
    )
    all_crossing_deadline_pressure = (
        deadline_precontact_pressure
        and not all_crossing_measurement_noise
    )
    urgent_direct_pressure = (
        safe_direct
        and (
            piece_count >= 30
            or current_top_edge_y >= deadline_y - 1.5
            or reactive_pair_count >= 3
        )
    )
    urgent_merge_pressure = (
        safe_merge
        and (
            piece_count >= 35
            or current_top_edge_y >= deadline_y - 1.0
            or reactive_pair_count >= 3
            or (
                chosen.get("crosses_deadline", False)
                and current_top_edge_y >= deadline_y - 0.75
            )
        )
    )

    def deadline_headroom_replacement_for(candidate):
        """Prefer the lowest-risk safe landing near the deadline.

        Visual same-country fallbacks are useful, but when the board is already
        near the red line they must not keep stacking onto a much taller safe
        column while a clearly lower safe slot exists.
        """
        if not deadline_precontact_pressure or not safe or not isinstance(candidate, dict):
            return None
        if candidate.get("merge_grade", "NO") in ("DIRECT", "NEAR"):
            return None
        best_safe = min(
            safe,
            key=lambda r: (
                risk_top(r),
                abs(float(r.get("x", 0.0) or 0.0)),
            ),
        )
        candidate_geom_top = geometry_top_at_x(candidate.get("x"))
        if candidate_geom_top is not None:
            safe_geom_pairs = [
                (geometry_top_at_x(r.get("x")), r)
                for r in safe
            ]
            safe_geom_pairs = [
                (geom_top, r)
                for geom_top, r in safe_geom_pairs
                if geom_top is not None
            ]
            if safe_geom_pairs:
                best_safe_geom_top, best_safe_geom = min(
                    safe_geom_pairs,
                    key=lambda item: (
                        item[0],
                        risk_top(item[1]),
                        abs(float(item[1].get("x", 0.0) or 0.0)),
                    ),
                )
                if candidate_geom_top > best_safe_geom_top + 0.20:
                    return best_safe_geom
        if risk_top(candidate) <= risk_top(best_safe) + 0.35:
            return None
        return best_safe

    def geometry_underestimate_replacement_for(candidate):
        """Catch analyzer-safe choices that geometry says are already over deadline."""
        if not isinstance(candidate, dict) or not safe:
            return None
        if candidate.get("crosses_deadline", False):
            return None
        candidate_geom_top = geometry_top_at_x(candidate.get("x"))
        if candidate_geom_top is None or candidate_geom_top <= deadline_y + 0.02:
            return None
        if piece_count < 18 and current_top_edge_y < deadline_y - 1.80:
            return None
        safe_geom_pairs = []
        for option in safe:
            if option is candidate:
                continue
            option_geom_top = geometry_top_at_x(option.get("x"))
            if option_geom_top is None:
                continue
            safe_geom_pairs.append((option_geom_top, option))
        if not safe_geom_pairs:
            return None
        best_geom_top, best_geom = min(
            safe_geom_pairs,
            key=lambda item: (
                item[0],
                risk_top(item[1]),
                grade_rank.get(item[1].get("merge_grade", "NO"), 9),
                abs(float(item[1].get("x", 0.0) or 0.0)),
            ),
        )
        if best_geom_top > candidate_geom_top - 0.25:
            return None
        if (
            candidate.get("merge_grade", "NO") in ("DIRECT", "NEAR")
            and best_geom.get("merge_grade", "NO") == "NO"
            and current_top_edge_y >= deadline_y - 0.75
        ):
            return None
        if (
            best_geom_top > deadline_y + 0.15
            and risk_top(best_geom) > risk_top(candidate) + 0.40
        ):
            return None
        return best_geom

    def visual_same_country_replacement():
        """Fallback for visually obvious same-country merges missed by analysis.

        The Unity screen often shows a drop-reachable same-country piece while
        reactor labels every candidate as NO. Under endgame pressure, aim at the
        highest matching country instead of preserving a generic low-risk slot.
        """
        if not late_pressure or not next_type:
            return None
        if not safe and chosen.get("crosses_deadline", False) and not all_crossing_deadline_pressure:
            return None
        if chosen.get("merge_grade", "NO") in ("DIRECT", "NEAR"):
            return None
        pieces = (game_state or {}).get("pieces") or []
        same_pieces = [
            p for p in pieces if int(p.get("type", 0) or 0) == next_type
        ]
        if not same_pieces:
            return None
        target_piece = max(
            same_pieces,
            key=lambda p: (
                float(p.get("y", -99.0) or -99.0),
                -abs(float(p.get("x", 0.0) or 0.0)),
            ),
        )
        target_x = float(target_piece.get("x", 0.0) or 0.0)
        pool = safe or results
        min_pool_risk = min((risk_top(r) for r in pool), default=min_risk_top)
        # When a same-country target is only visually inferred, avoid trading a
        # safer central slot for an edge-ish target unless it clearly improves
        # contact with that country. These visual fallbacks are meant to catch
        # skipped merges, not to manufacture a new tower at the wall.
        chosen_target_dx = abs(chosen_x - target_x)
        target_band = [
            r
            for r in pool
            if abs(float(r.get("x", 0.0) or 0.0) - target_x) <= 0.85
            and risk_top(r) <= min_pool_risk + 1.35
            and abs(float(r.get("x", 0.0) or 0.0) - target_x)
            <= chosen_target_dx + 0.05
            and abs(float(r.get("x", 0.0) or 0.0)) <= 2.55
        ]
        if not target_band:
            target_band = [
                r
                for r in pool
                if abs(float(r.get("x", 0.0) or 0.0) - target_x) <= 1.2
                and risk_top(r) <= min_pool_risk + 1.9
                and abs(float(r.get("x", 0.0) or 0.0) - target_x)
                <= chosen_target_dx + 0.15
            ]
        if not target_band:
            return None
        return min(
            target_band,
            key=lambda r: (
                abs(float(r.get("x", 0.0) or 0.0) - target_x),
                bool(r.get("crosses_deadline", False)),
                risk_top(r),
            ),
        )

    def visual_deadline_same_country_replacement():
        """Hard invariant for deadline boards with visually reachable merges.

        In the death spiral, the old visual fallback still rejected edge-side
        same-country targets because they looked riskier than a generic safe
        slot. The user-visible failure is worse: placing elsewhere while an
        apparent merge target is sitting on screen. When the board is already
        touching the deadline, aim at the highest same-country target even if
        analysis labels the candidate as NO.
        """
        if not deadline_precontact_pressure or not next_type:
            return None
        if not safe and not all_crossing_deadline_pressure:
            return None
        if chosen.get("merge_grade", "NO") in ("DIRECT", "NEAR"):
            return None
        pieces = (game_state or {}).get("pieces") or []
        same_pieces = [
            p for p in pieces if int(p.get("type", 0) or 0) == next_type
        ]
        if not same_pieces:
            return None
        target_piece = max(
            same_pieces,
            key=lambda p: (
                float(p.get("y", -99.0) or -99.0),
                -abs(float(p.get("x", 0.0) or 0.0)),
            ),
        )
        target_x = float(target_piece.get("x", 0.0) or 0.0)
        target_y = float(target_piece.get("y", -99.0) or -99.0)
        chosen_dx = abs(chosen_x - target_x)
        # Only take over when the current choice is visibly elsewhere. This
        # keeps normal low-risk moves intact, while forcing the broken endgame
        # case that repeatedly dies on screen.
        if chosen_dx <= 0.65:
            return None
        pool = results or safe
        if not pool:
            return None
        target_band = [
            r
            for r in pool
            if abs(float(r.get("x", 0.0) or 0.0) - target_x) <= 1.10
            and abs(float(r.get("x", 0.0) or 0.0) - target_x) < chosen_dx
        ]
        if not target_band:
            target_band = [
                r
                for r in pool
                if abs(float(r.get("x", 0.0) or 0.0) - target_x) < chosen_dx
            ]
        if not target_band:
            return None
        return min(
            target_band,
            key=lambda r: (
                abs(float(r.get("x", 0.0) or 0.0) - target_x),
                grade_rank.get(r.get("merge_grade", "NO"), 9),
                risk_top(r) if target_y < deadline_y - 2.0 else 0.0,
            ),
        )

    reason_text = str(decision.get("reason", "") or "")

    def pre_russia_t12_lane_replacement_for(candidate):
        """Keep deadline overrides near the T12/T11 rebuild lane.

        When the strategy layer is explicitly trying to consolidate T12/T11
        material, a tiny risk-top improvement at the edge can strand the only
        route to the first T13. Keep this bounded to no-Russia, max-T12 boards
        and only accept a lane candidate in a near-risk band.
        """
        if not isinstance(candidate, dict):
            return None
        if not any(
            marker in reason_text
            for marker in (
                "PRE_RUSSIA_T12_CONSOLIDATE",
                "PRE_RUSSIA_T11_DENSITY_LATCH",
                "PRE_RUSSIA_SINGLE_T12_ANCHOR_LADDER",
                "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR",
                "PRE_RUSSIA_NEXT_UP_LATCH",
            )
        ):
            return None
        pieces = (game_state or {}).get("pieces") or []
        if not pieces or next_type not in (10, 11, 12):
            return None
        high_counts = {}
        for piece in pieces:
            try:
                piece_type = int(piece.get("type", 0) or 0)
            except Exception:
                continue
            if piece_type >= 10:
                high_counts[piece_type] = high_counts.get(piece_type, 0) + 1
        if (
            high_counts.get(13, 0) > 0
            or high_counts.get(14, 0) > 0
            or high_counts.get(15, 0) > 0
            or high_counts.get(12, 0) < 1
            or (
                high_counts.get(12, 0) < 2
                and high_counts.get(11, 0) < 2
            )
            or piece_count < 30
        ):
            return None
        single_t12_anchor_reason = (
            "PRE_RUSSIA_SINGLE_T12_ANCHOR_LADDER" in reason_text
            or "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR" in reason_text
        )
        t12_targets = [
            p for p in pieces if int(p.get("type", 0) or 0) == 12
        ]
        t11_targets = [
            p for p in pieces if int(p.get("type", 0) or 0) == 11
        ]
        t10_targets = [
            p for p in pieces if int(p.get("type", 0) or 0) == 10
        ]
        lane_x = None
        if len(t12_targets) >= 2:
            best_pair = None
            best_pair_key = (999.0, 999.0)
            for idx, left in enumerate(t12_targets):
                for right in t12_targets[idx + 1:]:
                    ax = _geom_num(left.get("x"))
                    ay = _geom_num(left.get("y"), -10.0)
                    bx = _geom_num(right.get("x"))
                    by = _geom_num(right.get("y"), -10.0)
                    pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                    pair_top = max(ay, by)
                    pair_key = (
                        pair_dist + max(0.0, pair_top - 1.3) * 0.65,
                        pair_top,
                    )
                    if pair_key < best_pair_key:
                        best_pair_key = pair_key
                        best_pair = (left, right)
            if best_pair is not None:
                lane_x = (
                    _geom_num(best_pair[0].get("x"))
                    + _geom_num(best_pair[1].get("x"))
                ) / 2.0
        elif t12_targets:
            lane_x = sum(_geom_num(p.get("x")) for p in t12_targets) / len(t12_targets)

        target_x = lane_x
        if next_type == 11 and t11_targets:
            if single_t12_anchor_reason and lane_x is not None:
                target_x = lane_x
            else:
                def t11_key(piece):
                    px = _geom_num(piece.get("x"))
                    py = _geom_num(piece.get("y"), -10.0)
                    lane_dist = abs(px - lane_x) if lane_x is not None else 0.0
                    return (lane_dist * 0.4 + max(0.0, py - 1.4) * 1.0, py)
                target_x = _geom_num(min(t11_targets, key=t11_key).get("x"))
        elif next_type == 10 and t10_targets:
            up_targets = t11_targets + t12_targets
            def t10_key(piece):
                px = _geom_num(piece.get("x"))
                py = _geom_num(piece.get("y"), -10.0)
                up_dist = min(
                    ((_geom_num(up.get("x")) - px) ** 2 + (_geom_num(up.get("y"), -10.0) - py) ** 2) ** 0.5
                    for up in up_targets
                ) if up_targets else 999.0
                lane_dist = abs(px - lane_x) if lane_x is not None else 999.0
                return (min(up_dist, lane_dist) + max(0.0, py - 1.0) * 0.7, py)
            target_x = _geom_num(min(t10_targets, key=t10_key).get("x"))
        if target_x is None:
            return None

        current_dx = abs(_geom_num(candidate.get("x")) - target_x)
        pool = results or safe
        if not pool or current_dx <= 1.25:
            return None
        lane_band = [
            r for r in pool
            if abs(_geom_num(r.get("x")) - target_x) < current_dx
            and abs(_geom_num(r.get("x")) - target_x) <= 1.25
            and not r.get("crosses_deadline", False)
            and risk_top(r) <= max(risk_top(candidate) + 0.85, min_risk_top + 1.8)
        ]
        if not lane_band:
            return None
        return min(
            lane_band,
            key=lambda r: (
                abs(_geom_num(r.get("x")) - target_x),
                bool(r.get("crosses_deadline", False)),
                risk_top(r),
            ),
        )

    def second_russia_t12_pair_replacement_for(candidate):
        """Keep one-T14 / two-T12 boards on the second-Russia pair lane."""
        if not isinstance(candidate, dict):
            return None
        if not any(
            marker in reason_text
            for marker in (
                "SECOND_RUSSIA_T12_PAIR_LOCK",
                "DEADLINE_GUARD_SECOND_RUSSIA_T12_PAIR_LOCK",
            )
        ):
            return None
        pieces = (game_state or {}).get("pieces") or []
        if not pieces or next_type not in (10, 11, 12, 13):
            return None
        high_counts = {}
        for piece in pieces:
            try:
                piece_type = int(piece.get("type", 0) or 0)
            except Exception:
                continue
            if piece_type >= 10:
                high_counts[piece_type] = high_counts.get(piece_type, 0) + 1
        if (
            high_counts.get(15, 0) > 0
            or high_counts.get(14, 0) < 1
            or high_counts.get(13, 0) > 0
            or high_counts.get(12, 0) < 2
            or piece_count < 34
        ):
            return None

        t12_targets = [
            p for p in pieces if int(p.get("type", 0) or 0) == 12
        ]
        best_pair = None
        best_pair_key = (999.0, 999.0)
        for idx, left in enumerate(t12_targets):
            for right in t12_targets[idx + 1:]:
                ax = _geom_num(left.get("x"))
                ay = _geom_num(left.get("y"), -10.0)
                bx = _geom_num(right.get("x"))
                by = _geom_num(right.get("y"), -10.0)
                pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                pair_top = max(ay, by)
                pair_center = (ax + bx) / 2.0
                pair_key = (
                    pair_dist
                    + max(0.0, pair_top - 1.15) * 1.15
                    + abs(pair_center) * 0.18,
                    pair_top,
                )
                if pair_key < best_pair_key:
                    best_pair_key = pair_key
                    best_pair = (left, right)
        if best_pair is None:
            return None
        target_x = (
            _geom_num(best_pair[0].get("x"))
            + _geom_num(best_pair[1].get("x"))
        ) / 2.0

        current_dx = abs(_geom_num(candidate.get("x")) - target_x)
        pool = results or safe
        if not pool or current_dx <= 1.15:
            return None
        lane_band = [
            r for r in pool
            if abs(_geom_num(r.get("x")) - target_x) < current_dx
            and abs(_geom_num(r.get("x")) - target_x) <= 1.2
            and risk_top(r) <= max(risk_top(candidate) + 0.9, min_risk_top + 1.8)
        ]
        if not lane_band:
            return None
        return min(
            lane_band,
            key=lambda r: (
                abs(_geom_num(r.get("x")) - target_x),
                bool(r.get("crosses_deadline", False)),
                risk_top(r),
            ),
        )

    def second_russia_t12_ladder_replacement_for(candidate):
        """Keep one-T14 / single-T12 rebuild boards on the second-Russia lane."""
        if not isinstance(candidate, dict):
            return None
        if not any(
            marker in reason_text
            for marker in (
                "SECOND_RUSSIA_T12_LADDER",
                "DEADLINE_GUARD_SECOND_RUSSIA_T12_LADDER",
            )
        ):
            return None
        pieces = (game_state or {}).get("pieces") or []
        if not pieces or next_type not in (10, 11, 12, 13):
            return None
        high_counts = {}
        for piece in pieces:
            try:
                piece_type = int(piece.get("type", 0) or 0)
            except Exception:
                continue
            if piece_type >= 10:
                high_counts[piece_type] = high_counts.get(piece_type, 0) + 1
        if (
            high_counts.get(15, 0) > 0
            or high_counts.get(14, 0) < 1
            or high_counts.get(13, 0) > 0
            or high_counts.get(12, 0) < 1
            or piece_count < 30
        ):
            return None

        t12_targets = [
            p for p in pieces if int(p.get("type", 0) or 0) == 12
        ]
        t11_targets = [
            p for p in pieces if int(p.get("type", 0) or 0) == 11
        ]
        t10_targets = [
            p for p in pieces if int(p.get("type", 0) or 0) == 10
        ]
        if not t12_targets:
            return None

        if len(t12_targets) >= 2:
            best_pair = None
            best_pair_key = (999.0, 999.0)
            for idx, left in enumerate(t12_targets):
                for right in t12_targets[idx + 1:]:
                    ax = _geom_num(left.get("x"))
                    ay = _geom_num(left.get("y"), -10.0)
                    bx = _geom_num(right.get("x"))
                    by = _geom_num(right.get("y"), -10.0)
                    pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                    pair_top = max(ay, by)
                    pair_key = (
                        pair_dist + max(0.0, pair_top - 1.3) * 0.65,
                        pair_top,
                    )
                    if pair_key < best_pair_key:
                        best_pair_key = pair_key
                        best_pair = (left, right)
            target_x = (
                (_geom_num(best_pair[0].get("x")) + _geom_num(best_pair[1].get("x"))) / 2.0
                if best_pair is not None
                else sum(_geom_num(p.get("x")) for p in t12_targets) / len(t12_targets)
            )
        else:
            target_x = _geom_num(t12_targets[0].get("x"))

        if next_type == 11 and t11_targets:
            def t11_key(piece):
                px = _geom_num(piece.get("x"))
                py = _geom_num(piece.get("y"), -10.0)
                return (
                    abs(px - target_x) * 0.45
                    + max(0.0, py - 1.15) * 1.15,
                    py,
                )
            target_x = _geom_num(min(t11_targets, key=t11_key).get("x"))
        elif next_type == 10 and t10_targets:
            up_targets = t11_targets + t12_targets
            def t10_key(piece):
                px = _geom_num(piece.get("x"))
                py = _geom_num(piece.get("y"), -10.0)
                up_dist = min(
                    ((_geom_num(up.get("x")) - px) ** 2 + (_geom_num(up.get("y"), -10.0) - py) ** 2) ** 0.5
                    for up in up_targets
                ) if up_targets else 999.0
                return (
                    min(up_dist, abs(px - target_x))
                    + max(0.0, py - 1.0) * 0.9
                    + max(0.0, abs(px) - 2.1) * 0.25,
                    py,
                )
            target_x = _geom_num(min(t10_targets, key=t10_key).get("x"))

        current_dx = abs(_geom_num(candidate.get("x")) - target_x)
        pool = results or safe
        if not pool or current_dx <= 1.05:
            return None
        lane_band = [
            r for r in pool
            if abs(_geom_num(r.get("x")) - target_x) < current_dx
            and abs(_geom_num(r.get("x")) - target_x) <= 1.30
            and risk_top(r) <= max(risk_top(candidate) + 1.15, min_risk_top + 2.05)
        ]
        if not lane_band:
            lane_band = [
                r for r in pool
                if abs(_geom_num(r.get("x")) - target_x) < current_dx
                and abs(_geom_num(r.get("x")) - target_x) <= 1.60
                and risk_top(r) <= max(risk_top(candidate) + 1.35, min_risk_top + 2.30)
            ]
        if not lane_band:
            return None
        return min(
            lane_band,
            key=lambda r: (
                abs(_geom_num(r.get("x")) - target_x),
                bool(r.get("crosses_deadline", False)),
                risk_top(r),
            ),
        )

    def first_russia_t13_pair_replacement_for(candidate):
        """Keep one-T14 / two-T13 boards on the second-T14 lane."""
        if not isinstance(candidate, dict):
            return None
        if not any(
            marker in reason_text
            for marker in (
                "FIRST_RUSSIA_T13_PAIR_LIFT",
                "DEADLINE_GUARD_FIRST_RUSSIA_T13_PAIR_LIFT",
            )
        ):
            return None
        pieces = (game_state or {}).get("pieces") or []
        if not pieces or next_type not in (10, 11, 12, 13):
            return None
        high_counts = {}
        for piece in pieces:
            try:
                piece_type = int(piece.get("type", 0) or 0)
            except Exception:
                continue
            if piece_type >= 10:
                high_counts[piece_type] = high_counts.get(piece_type, 0) + 1
        if (
            high_counts.get(15, 0) > 0
            or high_counts.get(14, 0) != 1
            or high_counts.get(13, 0) < 2
            or piece_count < 32
        ):
            return None

        t13_targets = [
            p for p in pieces if int(p.get("type", 0) or 0) == 13
        ]
        best_pair = None
        best_pair_key = (999.0, 999.0)
        for idx, left in enumerate(t13_targets):
            for right in t13_targets[idx + 1:]:
                ax = _geom_num(left.get("x"))
                ay = _geom_num(left.get("y"), -10.0)
                bx = _geom_num(right.get("x"))
                by = _geom_num(right.get("y"), -10.0)
                pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                pair_top = max(ay, by)
                pair_key = (
                    pair_dist + max(0.0, pair_top - 1.1) * 0.55,
                    pair_top,
                )
                if pair_key < best_pair_key:
                    best_pair_key = pair_key
                    best_pair = (left, right)
        if best_pair is None:
            return None
        target_x = (
            _geom_num(best_pair[0].get("x"))
            + _geom_num(best_pair[1].get("x"))
        ) / 2.0

        current_dx = abs(_geom_num(candidate.get("x")) - target_x)
        pool = results or safe
        if not pool or current_dx <= 1.15:
            return None
        lane_band = [
            r for r in pool
            if abs(_geom_num(r.get("x")) - target_x) < current_dx
            and abs(_geom_num(r.get("x")) - target_x) <= 1.3
            and risk_top(r) <= max(risk_top(candidate) + 1.05, min_risk_top + 1.9)
        ]
        if not lane_band:
            return None
        return min(
            lane_band,
            key=lambda r: (
                abs(_geom_num(r.get("x")) - target_x),
                bool(r.get("crosses_deadline", False)),
                risk_top(r),
            ),
        )

    country_route_reason = (
        10 <= next_type <= 12
        and (
            "SAME_COUNTRY" in reason_text
            or "RUSSIA_RESOURCE" in reason_text
            or "UKRAINE_PAIR" in reason_text
        )
    )
    if (
        country_route_reason
        and not urgent_direct_pressure
        and not urgent_merge_pressure
        and not risky_merge_result_deadline(chosen)
        and deadline_headroom_replacement_for(chosen) is None
        and not chosen.get("crosses_deadline", False)
        and chosen_top <= min_risk_top + 2.0
        and abs(chosen_x) <= 2.65
    ):
        # The strategy layer already chose a country-building placement. Do not
        # flatten that into a generic min-risk edge drop unless the placement is
        # actually crossing the deadline or wildly unsafe.
        return decision

    replacement_source = "generic"
    chosen_headroom_replacement = deadline_headroom_replacement_for(chosen)
    chosen_geometry_replacement = geometry_underestimate_replacement_for(chosen)

    if (
        chosen.get("merge_grade", "NO") == "DIRECT"
        and chosen.get("crosses_deadline", False)
        and not risky_merge_result_deadline(chosen)
    ):
        # A DIRECT merge is the one deadline-crossing move that can remove the
        # risky contact immediately. Older runtime safety could downgrade this
        # to a generic NO safe landing whenever such a landing existed, which
        # helped survival but starved the high-type merge route needed for type16.
        return decision

    if chosen_headroom_replacement is not None:
        replacement = chosen_headroom_replacement
        replacement_source = "deadline_headroom"

    elif risky_merge_result_deadline(chosen):
        replacement = min(deadline_clean_candidates, key=rank_candidate)
        replacement_source = "avoid_merge_result_deadline"

    elif urgent_direct_pressure:
        if chosen in safe_direct:
            return decision
        replacement = min(safe_direct, key=rank_candidate)
        replacement_source = "urgent_direct"

    elif urgent_merge_pressure:
        if chosen in safe_merge:
            return decision
        replacement = min(safe_merge, key=rank_candidate)
        replacement_source = "urgent_merge"

    elif (visual_deadline_replacement := visual_deadline_same_country_replacement()) is not None:
        replacement = visual_deadline_replacement
        replacement_source = "visual_deadline_same_country"

    elif (visual_replacement := visual_same_country_replacement()) is not None:
        replacement = visual_replacement
        replacement_source = "visual_same_country"

    elif deadline_precontact_pressure and buffered:
        if chosen in buffered:
            return decision
        chosen_grade_val = grade_rank.get(chosen.get("merge_grade", "NO"), 9)
        chosen_crosses = bool(chosen.get("crosses_deadline", False))
        # Never downgrade merge grade for headroom: if chosen has a merge
        # (DIRECT/NEAR) and no buffered alternative preserves that grade,
        # keep the chosen merge — unless chosen crosses the deadline (then
        # we MUST swap; any safe drop beats game over).
        merge_preserving_buffered = [
            r for r in buffered
            if grade_rank.get(r.get("merge_grade", "NO"), 9) <= chosen_grade_val
        ]
        if not merge_preserving_buffered and chosen_grade_val <= 1 and not chosen_crosses:
            return decision
        pool = merge_preserving_buffered or buffered
        replacement = min(
            pool,
            key=lambda r: (
                grade_rank.get(r.get("merge_grade", "NO"), 9),
                risk_top(r),
                abs(float(r.get("x", 0.0) or 0.0)),
            ),
        )
        replacement_source = "buffered"

    elif deadline_precontact_pressure and safe:
        if chosen in safe:
            return decision
        chosen_grade_val = grade_rank.get(chosen.get("merge_grade", "NO"), 9)
        merge_preserving_safe = [
            r for r in safe
            if grade_rank.get(r.get("merge_grade", "NO"), 9) <= chosen_grade_val
        ]
        if not merge_preserving_safe and chosen_grade_val <= 1 and not chosen.get("crosses_deadline", False):
            return decision
        pool = merge_preserving_safe or safe
        replacement = min(pool, key=rank_candidate)
        replacement_source = "safe"

    elif large_deadline_pressure and chosen_top > min_risk_top + 0.05:
        all_merges = [
            r for r in results if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        ]
        if chosen.get("crosses_deadline", False) and chosen.get("merge_grade", "NO") == "NO" and all_merges:
            replacement = min(all_merges, key=rank_candidate)
            replacement_source = "large_deadline_merge_candidate"
        else:
            replacement = min_risk_candidate
            replacement_source = "large_deadline_minrisk"
    elif (
        not chosen.get("crosses_deadline", False)
        and chosen.get("merge_grade", "NO") == "NO"
        and safe
        and "MEDIUM_TOWER" in str(decision.get("reason", "") or "")
        and chosen_top >= deadline_y - 0.35
        and current_top_edge_y >= deadline_y - 1.40
    ):
        no_safe_pool = [r for r in safe if r.get("merge_grade", "NO") == "NO"] or list(safe)
        alt_pool = [r for r in no_safe_pool if r is not chosen]
        if not alt_pool:
            return decision
        best_safe = min(
            alt_pool,
            key=lambda r: (
                risk_top(r),
                abs(float(r.get("x", 0.0) or 0.0)),
            ),
        )
        chosen_geom_top = geometry_top_at_x(chosen.get("x"))
        best_geom_top = geometry_top_at_x(best_safe.get("x"))
        best_is_edge = abs(float(best_safe.get("x", 0.0) or 0.0)) > 2.2
        risk_improvement = chosen_top - risk_top(best_safe)
        geom_improvement = (
            (chosen_geom_top - best_geom_top)
            if chosen_geom_top is not None and best_geom_top is not None
            else None
        )
        risk_threshold = 0.14 if best_is_edge else 0.10
        geom_threshold = 0.28 if best_is_edge else 0.20
        if risk_improvement >= risk_threshold or (
            geom_improvement is not None and geom_improvement >= geom_threshold
        ):
            replacement = best_safe
            replacement_source = "safe_medium_tower_underestimate_postcondition"
        else:
            return decision
    elif not chosen.get("crosses_deadline", False):
        if chosen_geometry_replacement is None:
            return decision
        replacement = chosen_geometry_replacement
        replacement_source = "geometry_underestimate_postcondition"
    elif not deadline_precontact_pressure:
        # Even if the board-wide deadline pressure looks low, do not keep a
        # NO-merge crossing choice when a non-crossing alternative already
        # exists in the same analysis set.
        if safe and chosen.get("merge_grade", "NO") == "NO":
            replacement = min(
                safe,
                key=lambda r: (
                    grade_rank.get(r.get("merge_grade", "NO"), 9),
                    risk_top(r),
                    abs(float(r.get("x", 0.0) or 0.0)),
                ),
            )
            replacement_source = "safe_far_below_crossing"
        else:
            return decision
    else:
        if not safe and not all_crossing_deadline_pressure:
            return decision
        risk_band = [
            r for r in results if risk_top(r) <= min_risk_top + 0.05
        ] or [min_risk_candidate]
        non_no_band = [
            r for r in risk_band if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        ]
        all_merges = [
            r for r in results if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        ]
        replacement_pool = non_no_band or all_merges or risk_band
        replacement = min(
            replacement_pool,
            key=lambda r: (
                risk_top(r),
                grade_rank.get(r.get("merge_grade", "NO"), 9),
                abs(float(r.get("x", 0.0) or 0.0)),
            ),
        )
        if replacement.get("crosses_deadline", False) and replacement.get("merge_grade", "NO") == "NO":
            replacement = min(risk_band, key=lambda r: (risk_top(r), abs(float(r.get("x", 0.0) or 0.0))))
        replacement_source = (
            "risk_band_merge_candidate"
            if not non_no_band and all_merges
            else "risk_band"
        )

    # If the analysis still says NO but the live board has a high same-country
    # target far from the chosen drop, prefer a candidate that actually moves
    # toward that target. This is the visual invariant the logs alone have been
    # missing: in a dying board, do not place elsewhere while an apparent
    # same-country contact is reachable.
    def _replacement_x():
        v = replacement.get("x", chosen_x)
        return float(v if v is not None else chosen_x)

    if (
        late_pressure
        and replacement.get("merge_grade", "NO") == "NO"
        and next_type
        and abs(_replacement_x()) <= 3.05
    ):
        pieces = (game_state or {}).get("pieces") or []
        same_pieces = [
            p for p in pieces if int(p.get("type", 0) or 0) == next_type
        ]
        if same_pieces:
            target_piece = max(
                same_pieces,
                key=lambda p: (
                    float(p.get("y", -99.0) or -99.0),
                    -abs(float(p.get("x", 0.0) or 0.0)),
                ),
            )
            target_x = float(target_piece.get("x", 0.0) or 0.0)
            target_y = float(target_piece.get("y", -99.0) or -99.0)
            current_dx = abs(_replacement_x() - target_x)
            if current_dx > 1.25 and (target_y >= deadline_y - 2.25 or piece_count >= 38):
                pool = safe or results
                visual_band = [
                    r
                    for r in pool
                    if abs(float(r.get("x", 0.0) or 0.0) - target_x) <= 1.05
                    and abs(float(r.get("x", 0.0) or 0.0) - target_x) < current_dx
                    and risk_top(r) <= max(risk_top(replacement) + 0.8, min_risk_top + 1.7)
                ]
                if visual_band:
                    replacement = min(
                        visual_band,
                        key=lambda r: (
                            abs(float(r.get("x", 0.0) or 0.0) - target_x),
                            bool(r.get("crosses_deadline", False)),
                            risk_top(r),
                        ),
                    )
                    replacement_source = "visual_same_country_hard"

    if (pre_russia_lane_replacement := pre_russia_t12_lane_replacement_for(replacement)) is not None:
        replacement = pre_russia_lane_replacement
        replacement_source = f"{replacement_source}_pre_russia_t12_lane"

    if (first_russia_lane_replacement := first_russia_t13_pair_replacement_for(replacement)) is not None:
        replacement = first_russia_lane_replacement
        replacement_source = f"{replacement_source}_first_russia_t13_pair_lane"

    if (second_russia_ladder_replacement := second_russia_t12_ladder_replacement_for(replacement)) is not None:
        replacement = second_russia_ladder_replacement
        replacement_source = f"{replacement_source}_second_russia_t12_ladder_lane"

    if (second_russia_lane_replacement := second_russia_t12_pair_replacement_for(replacement)) is not None:
        replacement = second_russia_lane_replacement
        replacement_source = f"{replacement_source}_second_russia_t12_pair_lane"

    # Absolute postcondition: when a non-crossing candidate exists, the runtime
    # safety layer must never finish on a deadline-crossing NO-merge candidate.
    # Crossing DIRECT/NEAR candidates are allowed by the mandatory rule because
    # the contact can immediately collapse the risky piece instead of adding a
    # dead placement.
    if (
        safe
        and replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
    ):
        # Prefer any safe merge across the whole safe pool first — don't
        # restrict to a narrow risk band, which can hide the only safe merges
        # available and force a NEAR→NO downgrade.
        safe_merges = [
            r for r in safe
            if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        ]
        if safe_merges:
            replacement = min(safe_merges, key=rank_candidate)
        else:
            min_safe_top = min(risk_top(s) for s in safe)
            safe_band = [
                r for r in safe if risk_top(r) <= min_safe_top + 0.05
            ] or safe
            replacement = min(
                safe_band,
                key=lambda r: (
                    risk_top(r),
                    grade_rank.get(r.get("merge_grade", "NO"), 9),
                    abs(float(r.get("x", 0.0) or 0.0)),
                ),
            )
        replacement_source = f"{replacement_source}_safe_postcondition"

    if (
        replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
    ):
        all_merges = [
            r for r in results if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        ]
        if all_merges:
            replacement = min(all_merges, key=rank_candidate)
            replacement_source = f"{replacement_source}_merge_candidate_postcondition"

    # When every candidate crosses the deadline, a visually inferred same-country
    # contact is often the only escape route. Do not flatten that attempt back to
    # generic min-risk just because the analyzer still grades it as NO.
    preserve_visual_same_country = (
        replacement_source.startswith("visual_deadline_same_country")
    )
    # Keep visual same-country routing only while it stays near the min-risk band.
    # If it drifts too high, or the board is already over the deadline, prefer
    # the lower all-crossing fallback instead of preserving a visibly worse stack
    # path.
    if (
        preserve_visual_same_country
        and replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
        and not safe
        and (
            risk_top(replacement) > min_risk_top + 0.70
            or current_top_edge_y >= deadline_y + 0.20
        )
    ):
        preserve_visual_same_country = False
    if (
        preserve_visual_same_country
        and replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
        and not safe
        and risk_top(replacement) <= min_risk_top + 0.35
    ):
        replacement_geom_top = geometry_top_at_x(replacement.get("x"))
        minrisk_geom_top = geometry_top_at_x(min_risk_candidate.get("x"))
        if (
            replacement_geom_top is not None
            and minrisk_geom_top is not None
            and replacement_geom_top > minrisk_geom_top + 0.20
        ):
            preserve_visual_same_country = False
    if (
        preserve_visual_same_country
        and replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
        and not safe
    ):
        replacement_geom_top = geometry_top_at_x(replacement.get("x"))
        crossing_geom_candidates = []
        for candidate in results:
            if (
                candidate.get("merge_grade", "NO") != "NO"
                or not candidate.get("crosses_deadline", False)
            ):
                continue
            candidate_geom_top = geometry_top_at_x(candidate.get("x"))
            if candidate_geom_top is None or candidate_geom_top <= deadline_y + 0.02:
                continue
            crossing_geom_candidates.append((candidate_geom_top, candidate))
        if replacement_geom_top is not None and crossing_geom_candidates:
            best_geom_top, best_geom_candidate = min(
                crossing_geom_candidates,
                key=lambda item: (
                    item[0],
                    risk_top(item[1]),
                    abs(float(item[1].get("x", 0.0) or 0.0)),
                ),
            )
            if (
                replacement_geom_top > best_geom_top + 0.20
                and risk_top(replacement) > risk_top(best_geom_candidate) + 0.35
            ):
                replacement = best_geom_candidate
                preserve_visual_same_country = False
                replacement_source = (
                    f"{replacement_source}_geometry_min_top_postcondition"
                )
    if (
        replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
        and not safe
        and not preserve_visual_same_country
        and min_risk_top + 0.05 < risk_top(replacement)
    ):
        replacement = min_risk_candidate
        replacement_source = f"{replacement_source}_minrisk_postcondition"

    if (
        replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
        and not safe
        and not preserve_visual_same_country
    ):
        # If the strategy already chose a non-edge NO crossing and every
        # analyzer candidate crosses, do not make the lateral placement worse
        # just for a tiny risk-top gain. Recent failures showed NO->NO
        # overrides drifting from center-ish choices to walls in all-crossing
        # endgames, which adds instability without preserving a merge.
        if (
            chosen.get("crosses_deadline", False)
            and chosen.get("merge_grade", "NO") == "NO"
            and abs(chosen_x) <= 2.2
            and abs(float(replacement.get("x", 0.0) or 0.0)) > 2.2
            and risk_top(chosen) <= min_risk_top + 0.35
        ):
            replacement = chosen
            replacement_source = f"{replacement_source}_preserve_non_edge_no_postcondition"

        # When every analyzer candidate crosses the deadline, the final guard
        # can still choose a NO-merge wall drop just because its measured risk
        # is slightly lower. Keep the min-risk policy, but prefer a center-ish
        # NO candidate when it is in the same risk band.
        non_edge_band = [
            r
            for r in results
            if r.get("merge_grade", "NO") == "NO"
            and r.get("crosses_deadline", False)
            and abs(float(r.get("x", 0.0) or 0.0)) <= 2.2
            and risk_top(r) <= min_risk_top + 0.35
        ]
        if non_edge_band and abs(float(replacement.get("x", 0.0) or 0.0)) > 2.2:
            non_edge_replacement = min(
                non_edge_band,
                key=lambda r: (
                    risk_top(r),
                    abs(float(r.get("x", 0.0) or 0.0)),
                ),
            )
            replacement_geom_top = geometry_top_at_x(replacement.get("x"))
            non_edge_geom_top = geometry_top_at_x(non_edge_replacement.get("x"))
            if (
                replacement_geom_top is None
                or non_edge_geom_top is None
                or non_edge_geom_top <= replacement_geom_top + 0.20
            ):
                replacement = non_edge_replacement
                replacement_source = f"{replacement_source}_non_edge_postcondition"

    if (
        replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
        and not safe
        and not preserve_visual_same_country
    ):
        replacement_geom_top = geometry_top_at_x(replacement.get("x"))
        crossing_geom_candidates = []
        for candidate in results:
            if (
                candidate.get("merge_grade", "NO") != "NO"
                or not candidate.get("crosses_deadline", False)
            ):
                continue
            candidate_geom_top = geometry_top_at_x(candidate.get("x"))
            if candidate_geom_top is None:
                continue
            crossing_geom_candidates.append((candidate_geom_top, candidate))
        if replacement_geom_top is not None and crossing_geom_candidates:
            best_geom_top, best_geom_candidate = min(
                crossing_geom_candidates,
                key=lambda item: (
                    item[0],
                    risk_top(item[1]),
                    abs(float(item[1].get("x", 0.0) or 0.0)),
                ),
            )
            candidate_x = float(best_geom_candidate.get("x", 0.0) or 0.0)
            edge_candidate = abs(candidate_x) > 2.2
            improvement_needed = 0.85 if edge_candidate else 0.35
            if replacement_geom_top > best_geom_top + improvement_needed:
                replacement = best_geom_candidate
                replacement_source = f"{replacement_source}_geometry_lower_postcondition"

    if (
        replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
        and safe
    ):
        risk_band = [
            r for r in results if risk_top(r) <= min_risk_top + 0.05
        ] or [min_risk_candidate]
        non_no_band = [
            r for r in risk_band if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        ]
        if non_no_band:
            replacement = min(non_no_band, key=rank_candidate)
        elif (all_merges := [
            r for r in results if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        ]):
            replacement = min(all_merges, key=rank_candidate)
        elif safe:
            replacement = min(
                safe,
                key=lambda r: (
                    risk_top(r),
                    abs(float(r.get("x", 0.0) or 0.0)),
                ),
            )
        else:
            non_edge_band = [
                r
                for r in results
                if risk_top(r) <= min_risk_top + 0.35
                and abs(float(r.get("x", 0.0) or 0.0)) <= 2.2
            ]
            replacement = min(
                non_edge_band or risk_band,
                key=lambda r: (
                    risk_top(r),
                    abs(float(r.get("x", 0.0) or 0.0)),
                ),
            )

    if (headroom_replacement := deadline_headroom_replacement_for(replacement)) is not None:
        replacement = headroom_replacement
        replacement_source = f"{replacement_source}_deadline_headroom"
        if (pre_russia_lane_replacement := pre_russia_t12_lane_replacement_for(replacement)) is not None:
            replacement = pre_russia_lane_replacement
            replacement_source = f"{replacement_source}_pre_russia_t12_lane"
        if (first_russia_lane_replacement := first_russia_t13_pair_replacement_for(replacement)) is not None:
            replacement = first_russia_lane_replacement
            replacement_source = f"{replacement_source}_first_russia_t13_pair_lane"
        if (second_russia_ladder_replacement := second_russia_t12_ladder_replacement_for(replacement)) is not None:
            replacement = second_russia_ladder_replacement
            replacement_source = f"{replacement_source}_second_russia_t12_ladder_lane"
        if (second_russia_lane_replacement := second_russia_t12_pair_replacement_for(replacement)) is not None:
            replacement = second_russia_lane_replacement
            replacement_source = f"{replacement_source}_second_russia_t12_pair_lane"

    if risky_merge_result_deadline(replacement):
        safer_merges = [
            r for r in results
            if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
            and not risky_merge_result_deadline(r)
        ]
        if safer_merges:
            replacement = min(safer_merges, key=rank_candidate)
            replacement_source = f"{replacement_source}_avoid_merge_result_deadline"
        elif deadline_clean_candidates:
            replacement = min(deadline_clean_candidates, key=rank_candidate)
            replacement_source = f"{replacement_source}_avoid_merge_result_deadline_safe"
        else:
            return decision

    if (
        chosen.get("merge_grade", "NO") in ("DIRECT", "NEAR")
        and chosen.get("crosses_deadline", False)
        and replacement.get("crosses_deadline", False)
        and replacement.get("merge_grade", "NO") == "NO"
    ):
        merge_candidates = [
            r for r in results
            if r.get("merge_grade", "NO") in ("DIRECT", "NEAR")
            and not risky_merge_result_deadline(r)
        ]
        if merge_candidates:
            replacement = min(merge_candidates, key=rank_candidate)
            replacement_source = f"{replacement_source}_preserve_crossing_merge_postcondition"
        elif not risky_merge_result_deadline(chosen):
            replacement = chosen
            replacement_source = f"{replacement_source}_preserve_crossing_merge_postcondition"

    if (pre_russia_lane_replacement := pre_russia_t12_lane_replacement_for(replacement)) is not None:
        replacement = pre_russia_lane_replacement
        replacement_source = f"{replacement_source}_pre_russia_t12_lane"

    new_decision = dict(decision)
    old_grade = chosen.get("merge_grade", "NO")
    new_grade = replacement.get("merge_grade", "NO")
    # Note: `r.get("x", default) or default` is wrong because x=0.0 is falsy.
    _new_x_raw = replacement.get("x", chosen_x)
    new_x = float(_new_x_raw if _new_x_raw is not None else chosen_x)
    new_decision["x"] = max(GAME_X_MIN, min(GAME_X_MAX, new_x))
    reason = str(new_decision.get("reason", "") or "").strip()
    visual_suffix = (
        "_VISUAL_SAME_COUNTRY"
        if replacement_source.startswith("visual_same_country")
        else ""
    )
    suffix = f"RUNTIME_DEADLINE_SAFETY_OVERRIDE_{old_grade}_TO_{new_grade}_{replacement_source}{visual_suffix}"
    new_decision["reason"] = f"{reason}_{suffix}" if reason else suffix
    log(
        "RUNTIME_DEADLINE_SAFETY_OVERRIDE: "
        f"x={chosen_x:.2f}/{old_grade}/cross -> x={new_decision['x']:.2f}/{new_grade}"
    )
    return new_decision


def wait_for_move_state(deadline_fast_drop_enabled=DEFAULT_FAST_DROP_DEADLINE_CONTACT):
    """MOVE状態になるまで待つ。GAMEOVER/STOPならFalseを返す。"""
    settle_count = 0
    start = time.time()
    settle_force_at = 0.0  # MOVE確認後に初めてセット

    while time.time() - start < MOVE_TIMEOUT:
        if os.path.exists(STOP_FILE):
            raise KeyboardInterrupt("stop file")

        gs = load_game_state()
        if gs is None:
            time.sleep(POLL_INTERVAL)
            continue

        state = get_state_field(gs)

        if state in ("GAMEOVER", "STOP"):
            return gs, False

        if state != "MOVE":
            settle_count = 0
            settle_force_at = 0.0
            time.sleep(POLL_INTERVAL)
            continue

        # MOVE状態に入った瞬間にタイムアウト時刻をセット
        if settle_force_at == 0.0:
            settle_force_at = time.time() + SETTLE_FORCE_TIMEOUT

        if deadline_fast_drop_enabled and has_deadline_contact(gs):
            log("FAST_DROP_DEADLINE_CONTACT: skipping settle wait")
            return gs, True

        # 静止確認（force_after を渡してグリッチ時も突破できるようにする）
        if is_board_settled(gs, force_after=settle_force_at):
            if time.time() >= settle_force_at:
                log(f"WARN: board not settled after {SETTLE_FORCE_TIMEOUT}s, forcing drop")
            settle_count += 1
            if settle_count >= SETTLE_REQUIRED:
                return gs, True
        else:
            settle_count = 0

        time.sleep(POLL_INTERVAL)

    log("TIMEOUT: MOVE状態待ちタイムアウト — 強制 settled 扱いで続行")
    gs = load_game_state()
    return gs, True


def run_game():
    """1試合を自律プレイ"""
    log("=== Strategy Runner: 試合開始 ===")

    # strategy.py ロード
    try:
        strategy = load_strategy_module()
        strategy_hash = get_strategy_hash()
        strategy_file_hash = get_strategy_file_hash()
        log(f"Strategy hash: {strategy_hash}")
        deadline_fast_drop_enabled = strategy_fast_drop_deadline_contact_enabled(strategy)
        log(f"FAST_DROP_DEADLINE_CONTACT enabled={deadline_fast_drop_enabled}")
        if not hasattr(strategy, "decide"):
            log("ERROR: strategy.py に decide() がありません")
            return {"error": "no decide function", "score": 0, "turns": 0}
    except Exception as e:
        log(f"ERROR: strategy.py ロード失敗: {e}")
        return {"error": str(e), "score": 0, "turns": 0}

    # 履歴ディレクトリ準備
    os.makedirs(HISTORY_DIR, exist_ok=True)

    turn = 0
    cmd_desync_streak = 0
    prev_score = 0
    russia_created = False
    russia_announced = False
    soviet_created = False
    prev_russia_count = 0
    prev_actual_deadline_contact = False
    last_decision = {}

    # 前回の建国フラグをクリア（ゲーム開始時に毎回リセット）
    try:
        os.remove("tmp/markers/.russia_created")
    except FileNotFoundError:
        pass
    try:
        os.remove("tmp/markers/.soviet_created")
    except FileNotFoundError:
        pass

    with open(HISTORY_FILE, "w") as history_f:
        while True:
            # stop-file チェック
            if os.path.exists(STOP_FILE):
                raise KeyboardInterrupt("stop file")

            # MOVE状態待ち
            gs, is_move = wait_for_move_state(deadline_fast_drop_enabled)

            if not is_move:
                # GAMEOVER or TIMEOUT
                final_state = get_state_field(gs) if gs else "UNKNOWN"
                final_score = gs.get("score", 0) if gs else 0
                log(f"END s={final_score} t={turn} ({final_state})")
                return {
                    "score": final_score,
                    "turns": turn,
                    "state": final_state,
                    "pieces": len(gs.get("pieces", [])) if gs else 0,
                    "russia_created": russia_created,
                    "russia_announced": russia_announced,
                    "soviet_created": soviet_created,
                    "final_types": [p.get("type", 0) for p in gs.get("pieces", [])] if gs else [],
                }

            current_deadline_contact = has_deadline_contact(gs)
            if current_deadline_contact and not prev_actual_deadline_contact:
                notify_actual_deadline_contact_overlay(
                    turn + 1,
                    gs.get("score", 0),
                    last_decision,
                    {"deadline": {"top_edge_y": None}},
                    gs,
                )
            prev_actual_deadline_contact = current_deadline_contact

            turn += 1
            score = gs.get("score", 0)
            pieces = gs.get("pieces", [])
            max_y = max((p["y"] for p in pieces), default=-5.0)
            current_russia_count = count_piece_type(gs, RUSSIA_TYPE)
            log(f"T{turn} s={score} p={len(pieces)} y={max_y:.1f}")

            # ロシア建国検知（リアルタイム・1試合1回限り）
            # Runtime 上では type15 がロシアとして現れる。
            # 盤面に「存在する」だけではなく、このターンでロシアが新規生成された時だけ発火する。
            if (
                not russia_created
                and current_russia_count > prev_russia_count
                and gs.get("makeSorenCount", 0) <= 0
            ):
                russia_created = True
                log(
                    f"!!! RUSSIA CREATED !!! ロシア建国達成！ score={score} "
                    f"russia_count={prev_russia_count}->{current_russia_count}"
                )
                os.makedirs("tmp/markers", exist_ok=True)
                with open("tmp/markers/.russia_created", "w") as flag_f:
                    flag_f.write(f"{turn}|{score}\n")
                log("ロシア建国フラグ記録完了")
                russia_announced = trigger_russia_celebration_now(score, turn)

            # ソ連建国検知（リアルタイム・1試合1回限り）
            # Runtime 上ではソ連は piece type ではなく makeSorenCount で確実に検知する。
            if not soviet_created:
                if gs.get("makeSorenCount", 0) > 0:
                    soviet_created = True
                    log(
                        f"!!! SOVIET UNION CREATED !!! ソ連建国達成！ score={score} "
                        f"makeSorenCount={gs.get('makeSorenCount', 0)}"
                    )
                    trigger_soviet_clip_now(score, turn)
                    # フラグファイル作成（eloop.shが参照）
                    os.makedirs("tmp/markers", exist_ok=True)
                    with open("tmp/markers/.soviet_created", "w") as flag_f:
                        flag_f.write(f"{turn}\n")
                    log("ソ連建国フラグ記録完了（読み上げはキュー順で継続）")
                    # 建国後は戦略実行を停止し、これ以上コマンド送信しない
                    try:
                        with open(COMMANDS, "w") as f:
                            f.write("")
                    except Exception:
                        pass
                    decision = {"x": 0.0, "reason": "soviet created -> strategy halted"}
                    analysis = {"results": [], "same_type": [], "reactor": {}}
                    delta = score - prev_score
                    record_turn(
                        history_f,
                        turn,
                        gs,
                        decision,
                        analysis,
                        russia_created=russia_created,
                        soviet_created=True,
                        strategy_hash=strategy_hash,
                        score_delta=delta,
                    )
                    log("HALT: 建国達成により strategy_runner を停止（操作なし）")
                    return {
                        "score": score,
                        "turns": turn,
                        "state": get_state_field(gs),
                        "pieces": len(pieces),
                        "russia_created": russia_created,
                        "russia_announced": russia_announced,
                        "soviet_created": True,
                        "final_types": [p.get("type", 0) for p in pieces],
                    }

            # 盤面解析
            analysis = build_analysis(gs)
            enrich_game_state_deadline_fields(gs, analysis)

            # strategy.py は手番ごとに再ロードする。これにより、ライブ改善で
            # strategy.py だけを書き換えた場合もプロセス再起動なしで次手から反映する。
            try:
                current_strategy_hash = get_strategy_hash()
                current_strategy_file_hash = get_strategy_file_hash()
                if current_strategy_file_hash != strategy_file_hash:
                    strategy = load_strategy_module()
                    strategy_hash = current_strategy_hash
                    strategy_file_hash = current_strategy_file_hash
                    deadline_fast_drop_enabled = strategy_fast_drop_deadline_contact_enabled(strategy)
                    log(f"Strategy reloaded: {strategy_hash}")
                    log(f"FAST_DROP_DEADLINE_CONTACT enabled={deadline_fast_drop_enabled}")
            except Exception as err:
                log(f"WARN: strategy.py reload failed, keeping previous module: {err}")

            # strategy.decide() でドロップ決定
            try:
                decision = strategy.decide(gs, analysis)
                if not isinstance(decision, dict) or "x" not in decision:
                    log(f"WARNING: decide() returned invalid: {decision}")
                    decision = {"x": 0.0, "reason": "invalid decide() return → center fallback"}
            except Exception as e:
                err = str(e)
                log(f"ERROR: strategy.decide() failed: {err}")
                # decide例外は戦略破損の可能性が高いため即時終了して外側でロールバックさせる
                return {
                    "error": "decide_exception",
                    "error_message": err,
                    "score": score,
                    "turns": turn,
                    "state": get_state_field(gs),
                    "pieces": len(pieces),
                    "russia_created": russia_created,
                    "russia_announced": russia_announced,
                    "soviet_created": soviet_created,
                    "strategy_hash": strategy_hash,
                    "final_types": [p.get("type", 0) for p in pieces],
                }

            # ドロップX をクランプ
            drop_x = max(GAME_X_MIN, min(GAME_X_MAX, decision["x"]))
            decision["x"] = drop_x
            decision = enforce_deadline_safety(decision, analysis, gs)
            drop_x = max(GAME_X_MIN, min(GAME_X_MAX, decision["x"]))
            decision["x"] = drop_x

            reason = decision.get("reason", "")
            # reason を短縮表示（30文字まで）
            short_reason = reason[:30] if len(reason) > 30 else reason
            print(f"  x={drop_x:+.2f}\n  {short_reason}", flush=True)

            # score_delta を計算 (前ターンとの差分) — record_turn の前に計算
            delta = score - prev_score

            # 履歴記録
            record_turn(
                history_f,
                turn,
                gs,
                decision,
                analysis,
                russia_created=russia_created,
                soviet_created=soviet_created,
                strategy_hash=strategy_hash,
                score_delta=delta,
            )

            if delta > 0:
                print(f"  +{delta} → {score}", flush=True)
                cmd_desync_streak = 0  # スコア進行=bridge疎通正常
            prev_score = score
            prev_russia_count = current_russia_count

            # コマンド書き込み
            if not commands_empty():
                log("WARNING: commands.txt not empty, waiting...")
                wait_commands_done()

            write_drop_command(drop_x)
            last_decision = dict(decision)

            # コマンド消化待ち + bridge非同期 自己回復ウォッチドッグ
            if wait_commands_done():
                cmd_desync_streak = 0
            else:
                cmd_desync_streak += 1
                if BRIDGE_DESYNC_LIMIT > 0 and cmd_desync_streak >= BRIDGE_DESYNC_LIMIT:
                    log(
                        f"BRIDGE DESYNC: commands未消化 {cmd_desync_streak}連続 "
                        f"→ bridge非同期と判定・ゲーム中断 (eloop側で自己回復)"
                    )
                    return {
                        "error": "bridge_desync",
                        "score": score,
                        "turns": turn,
                        "state": get_state_field(gs),
                        "pieces": len(pieces),
                        "russia_created": russia_created,
                        "russia_announced": russia_announced,
                        "soviet_created": soviet_created,
                        "strategy_hash": strategy_hash,
                        "final_types": [p.get("type", 0) for p in pieces],
                    }

            # ドロップ後の待ち
            time.sleep(DROP_WAIT)


def main():
    try:
        result = run_game()
    except KeyboardInterrupt:
        log("Interrupted: strategy_runnerを終了します")
        try:
            with open(COMMANDS, "w") as f:
                f.write("")
        except Exception:
            pass
        # シグナル再送出: bash WCE が「シグナル死」と認識し trap を発火する
        sig = _received_signal or signal.SIGINT
        signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)
    # 最終結果を JSON で stdout に出力
    print("---RESULT---")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
