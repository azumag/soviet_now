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

import importlib
import importlib.util
import json
import math
import os
import sys
import time

# --- 定数 ---
GAME_STATE = "game_state.json"
COMMANDS = "commands.txt"
HISTORY_DIR = "game_history"
HISTORY_FILE = os.path.join(HISTORY_DIR, "latest.jsonl")

# 座標変換
GAME_X_MIN = -3.0
GAME_X_MAX = 3.0
CANVAS_X_MIN = 410
CANVAS_X_MAX = 830

# タイミング
POLL_INTERVAL = 0.5       # ポーリング間隔(秒)
SETTLE_REQUIRED = 2       # 静止確認回数
COMMAND_TIMEOUT = 20      # commands.txt 消化待ちタイムアウト(秒)
MOVE_TIMEOUT = 120        # MOVE状態待ちタイムアウト(秒)
DROP_WAIT = 2.0           # ドロップ後の待ち時間(秒)


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
    log(f"DROP game_x={game_x:.3f} → canvas={cx},350")
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

        return {
            "results": results,
            "same_type": [
                {"id": p["id"], "type": p["type"], "x": p["x"], "y": p["y"], "r": p["r"]}
                for p in same_type
            ],
            "reactor": reactor,
        }
    except Exception as e:
        log(f"WARNING: analyze_board failed: {e}")
        return {"results": [], "same_type": [], "reactor": {}, "error": str(e)}


def record_turn(history_f, turn, game_state, decision, analysis):
    """1ターン分の履歴をJSONLに記録"""
    pieces = game_state.get("pieces", [])
    score = game_state.get("score", 0)
    max_y = max((p["y"] for p in pieces), default=-5.0)
    nxt = game_state.get("next", {})

    results = analysis.get("results", [])
    best_grade = results[0].get("merge_grade", "NO") if results else "NO"
    has_merge = results[0].get("has_merge", False) if results else False

    reactor = analysis.get("reactor", {})
    reactive_pairs = len(reactor.get("reactive_pairs", []))

    # ピースのスナップショット（軽量化: 位置とtypeのみ）
    piece_snapshot = [
        {"id": p["id"], "type": p["type"], "x": round(p["x"], 2), "y": round(p["y"], 2)}
        for p in pieces
    ]

    record = {
        "turn": turn,
        "score": score,
        "score_delta": 0,  # 前ターンとの差分は呼び出し側で更新
        "piece_count": len(pieces),
        "max_y": round(max_y, 2),
        "next_type": nxt.get("type", 0),
        "decision_x": round(decision.get("x", 0), 3),
        "decision_reason": decision.get("reason", ""),
        "merge_available": has_merge,
        "best_merge_grade": best_grade,
        "reactor_reactive_pairs": reactive_pairs,
        "state_snapshot": {"pieces": piece_snapshot},
    }

    history_f.write(json.dumps(record, ensure_ascii=False) + "\n")
    history_f.flush()


def wait_for_move_state():
    """MOVE状態になるまで待つ。GAMEOVER/STOPならFalseを返す。"""
    settle_count = 0
    start = time.time()

    while time.time() - start < MOVE_TIMEOUT:
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

    with open(HISTORY_FILE, "w") as history_f:
        while True:
            # MOVE状態待ち
            gs, is_move = wait_for_move_state()

            if not is_move:
                # GAMEOVER or TIMEOUT
                final_state = get_state_field(gs) if gs else "UNKNOWN"
                final_score = gs.get("score", 0) if gs else 0
                log(f"=== 試合終了: state={final_state}, score={final_score}, turns={turn} ===")
                return {
                    "score": final_score,
                    "turns": turn,
                    "state": final_state,
                    "pieces": len(gs.get("pieces", [])) if gs else 0,
                }

            turn += 1
            score = gs.get("score", 0)
            pieces = gs.get("pieces", [])
            max_y = max((p["y"] for p in pieces), default=-5.0)
            log(f"Turn {turn}: score={score}, pieces={len(pieces)}, maxY={max_y:.2f}")

            # 盤面解析
            analysis = build_analysis(gs)

            # strategy.decide() でドロップ決定
            try:
                decision = strategy.decide(gs, analysis)
                if not isinstance(decision, dict) or "x" not in decision:
                    log(f"WARNING: decide() returned invalid: {decision}")
                    decision = {"x": 0.0, "reason": "invalid decide() return → center fallback"}
            except Exception as e:
                log(f"ERROR: strategy.decide() failed: {e}")
                decision = {"x": 0.0, "reason": f"decide() exception: {e}"}

            # ドロップX をクランプ
            drop_x = max(GAME_X_MIN, min(GAME_X_MAX, decision["x"]))
            decision["x"] = drop_x

            log(f"  Decision: DROP:{drop_x:.3f} ({decision.get('reason', '')})")

            # 履歴記録
            record_turn(history_f, turn, gs, decision, analysis)

            # score_delta を更新 (前ターンとの差分)
            # (JSONLは追記済みなのでログ出力のみ)
            delta = score - prev_score
            if delta > 0:
                log(f"  Score +{delta} (total: {score})")
            prev_score = score

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
    result = run_game()
    # 最終結果を JSON で stdout に出力
    print("---RESULT---")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
