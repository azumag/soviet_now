#!/usr/bin/env python3
"""strategy/isolated_runner/fixtures/generate_fixtures.py

issue #35 (rootless isolated runner) 用の評価corpusを生成する開発時ツール。

このスクリプトは host 上でしか動かさない (信頼済みコード = analyze_board.py /
strategy_runner.py / 既存 tests/fixtures/*.json の実盤面のみを読む。AI生成候補は
一切読み書きしない)。生成物である `f*.json` はリポジトリにコミットされた
決定論的な入力corpusであり、isolated runner (harness.py) はこのスクリプトを
実行時に呼ばない — 起動時に既にディスク上にある `f*.json` を read-only input
として読むだけ。

再生成が必要な場合のみ、リポジトリルートで実行する:
    python3 strategy/isolated_runner/fixtures/generate_fixtures.py

出力: strategy/isolated_runner/fixtures/f##_<name>.json
  {"fixture_id": ..., "source": ..., "game_state": {...}, "analysis": {...},
   "provenance": {"generated_from": "tests/fixtures/<file>", "root_strategy_decide_hash": "..."}}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, REPO_ROOT)

# 選定基準: 既存 tests/fixtures/*.json のうち、序盤/中盤/デッドライン近傍/多ピースなど
# 局面の多様性を確保できるものを選ぶ (テストで実績のある実盤面データを再利用する)。
SOURCE_FILES = [
    "anchor_lane_t9_beside_turn14.json",
    "anchor_lane_t11_beside_turn50.json",
    "lookahead_t6_nnt10_turn31.json",
    "low_drop_cover_failsafe_turn74.json",
    "first_russia_uzbekistan_turn67.json",
]

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(REPO_ROOT, "tests", "fixtures")


def _empty_board_fixture():
    """盤面が空の最小ケース (旧 assert_decision の empty-analysis 相当)。"""
    game_state = {
        "state": "MOVE",
        "score": 0,
        "pieces": [],
        "shapes": {},
        "next": {"type": 1, "r": 0.207},
        "nextNext": {"type": 1, "r": 0.207},
    }
    analysis = {"results": [], "same_type": [], "reactor": {}, "deadline": {}}
    return "empty_board", game_state, analysis


def main():
    import analyze_board as ab
    import strategy_runner as sr

    root_strategy_path = os.path.join(REPO_ROOT, "strategy.py")
    try:
        decide_hash = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "extract_decide_hash.py"), root_strategy_path],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:
        decide_hash = ""

    generated = []

    # fixture 0: 空盤面 (合成)
    name, gs, an = _empty_board_fixture()
    generated.append((name, "synthetic:empty_board", gs, an))

    for src_name in SOURCE_FILES:
        src_path = os.path.join(SRC_DIR, src_name)
        if not os.path.isfile(src_path):
            print(f"WARN: source fixture missing, skipping: {src_path}", file=sys.stderr)
            continue
        with open(src_path, encoding="utf-8") as f:
            fx = json.load(f)
        if "next_type" in fx:
            # anchor_lane / lookahead 系: next_type/next_next_type からゲーム状態を組み立てる
            nt = fx["next_type"]
            nnt = fx.get("next_next_type", 5)
            gs = {
                "state": "MOVE",
                "score": fx.get("score", 0),
                "pieces": [dict(p) for p in fx["pieces"]],
                "shapes": fx["shapes"],
                "next": {"type": nt, "r": ab.TYPE_RADII.get(nt, 0.5)},
                "nextNext": {"type": nnt, "r": ab.TYPE_RADII.get(nnt, 0.5)},
            }
        else:
            # first_russia 系: 既に game_state そのものの形で保存されている
            gs = {
                "state": fx.get("state", "MOVE"),
                "score": fx.get("score", 0),
                "pieces": [dict(p) for p in fx["pieces"]],
                "shapes": fx["shapes"],
                "next": dict(fx["next"]),
                "nextNext": dict(fx["nextNext"]),
            }
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        generated.append((os.path.splitext(src_name)[0], f"tests/fixtures/{src_name}", gs, an))

    for idx, (name, source, gs, an) in enumerate(generated):
        fixture_id = f"f{idx:02d}_{name}"
        out = {
            "fixture_id": fixture_id,
            "source": source,
            "game_state": gs,
            "analysis": an,
            "provenance": {
                "generated_by": "strategy/isolated_runner/fixtures/generate_fixtures.py",
                "root_strategy_decide_hash_at_generation": decide_hash,
            },
        }
        out_path = os.path.join(OUT_DIR, f"{fixture_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
