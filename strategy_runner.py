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


_received_signal = None

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


def is_board_settled(gs):
    """盤面が静止しているか (全ピースの速度が閾値以下)
    vy=-5000 等の極端な速度はドロップ待機中のnextピースなので除外する。"""
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
    spec.loader.exec_module(mod)
    return mod


def get_strategy_hash():
    """strategy.py のMD5ハッシュ（先頭8文字）を返す"""
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
        reactor = calc_reactor_state(pieces)
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


def record_turn(history_f, turn, game_state, decision, analysis, russia_created=False, soviet_created=False, strategy_hash=None, score_delta=0):
    """1ターン分の履歴をJSONLに記録"""
    pieces = game_state.get("pieces", [])
    score = game_state.get("score", 0)
    max_y = max((p["y"] for p in pieces), default=-5.0)
    top_edge_y = max((p["y"] + p.get("r", 0.0) for p in pieces), default=-5.0)
    nxt = game_state.get("next", {})

    results = analysis.get("results", [])
    deadline = analysis.get("deadline", {})

    # chosen_x に最も近い result を参照（results[0]は最左端なので不正確）
    chosen_x = decision.get("x", 0.0)
    chosen_result = None
    if results:
        chosen_result = min(results, key=lambda r: abs(r["x"] - chosen_x))
    best_grade = chosen_result.get("merge_grade", "NO") if chosen_result else "NO"
    has_merge = chosen_result.get("has_merge", False) if chosen_result else False

    reactor = analysis.get("reactor", {})
    reactive_pairs = len(reactor.get("reactive_pairs", []))
    danger_piece_count = int(deadline.get("danger_piece_count", 0) or 0)
    min_redline_time = float(deadline.get("min_redline_time", 0.0) or 0.0)

    # ピースのスナップショット（軽量化: 位置とtypeのみ）
    piece_snapshot = [
        {"id": p["id"], "type": p["type"], "x": round(p["x"], 2), "y": round(p["y"], 2)}
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
        "next_type": nxt.get("type", 0),
        "decision_x": round(decision.get("x", 0), 3),
        "decision_reason": decision.get("reason", ""),
        "merge_available": has_merge,
        "best_merge_grade": best_grade,
        "reactor_reactive_pairs": reactive_pairs,
        "decision_crosses_deadline": bool(chosen_result.get("crosses_deadline", False)) if chosen_result else False,
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


def wait_for_move_state():
    """MOVE状態になるまで待つ。GAMEOVER/STOPならFalseを返す。"""
    settle_count = 0
    start = time.time()

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
            time.sleep(POLL_INTERVAL)
            continue

        # MOVE状態 → 静止確認
        if is_board_settled(gs):
            settle_count += 1
            if settle_count >= SETTLE_REQUIRED:
                return gs, True
        else:
            settle_count = 0

        time.sleep(POLL_INTERVAL)

    log("TIMEOUT: MOVE状態待ちタイムアウト")
    gs = load_game_state()
    return gs, False


def run_game():
    """1試合を自律プレイ"""
    log("=== Strategy Runner: 試合開始 ===")

    # strategy.py ロード
    try:
        strategy = load_strategy_module()
        strategy_hash = get_strategy_hash()
        log(f"Strategy hash: {strategy_hash}")
        if not hasattr(strategy, "decide"):
            log("ERROR: strategy.py に decide() がありません")
            return {"error": "no decide function", "score": 0, "turns": 0}
    except Exception as e:
        log(f"ERROR: strategy.py ロード失敗: {e}")
        return {"error": str(e), "score": 0, "turns": 0}

    # 履歴ディレクトリ準備
    os.makedirs(HISTORY_DIR, exist_ok=True)

    turn = 0
    prev_score = 0
    russia_created = False
    russia_announced = False
    soviet_created = False
    prev_russia_count = 0

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
            gs, is_move = wait_for_move_state()

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
            prev_score = score
            prev_russia_count = current_russia_count

            # コマンド書き込み
            if not commands_empty():
                log("WARNING: commands.txt not empty, waiting...")
                wait_commands_done()

            write_drop_command(drop_x)

            # コマンド消化待ち
            wait_commands_done()

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
