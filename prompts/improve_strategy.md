あなたはパズルゲーム「ソ連パズル」の戦略改善AI。`strategy.py` の `decide()` 関数を改善せよ。

## ゲーム概要
- ソ連パズル（スイカゲーム風）: 同type2個が接触→合体進化 (type_N + type_N → type_{N+1})
- プレイヤーはドロップX座標(-3.0〜+3.0)のみ指定可能
- ゲームオーバー: ピースがデッドライン(y≒2.5)を超える
- 目標: スコア最大化

## strategy.py のインターフェース契約（変更禁止）

```python
def decide(game_state: dict, analysis: dict) -> dict:
    """
    Args:
        game_state: {"state", "score", "next": {"type", "r"}, "nextNext": {"type", "r"},
                     "pieces": [{"id", "type", "x", "y", "r", "vx", "vy", ...}], "shapes": {...}}
        analysis: {"results": [{x, landing_y, merge_grade, score, has_merge, merges, ...}],
                   "same_type": [{id, type, x, y, r}],
                   "reactor": {type_count, reactive_pairs, near_pairs, pipeline, ...}}
    Returns:
        {"x": float, "reason": str}  # x は -3.0〜+3.0
    """
```

## 変更ルール
- `decide()` の**シグネチャ** (`def decide(game_state, analysis) -> dict`) は変更禁止
- `if __name__ == "__main__"` ブロックは変更禁止
- `decide()` 内部、ヘルパー関数、定数、import、モジュールレベル変数は自由に変更可能
- strategy.py のみ変更可能。他のファイルは変更しない
- 変更内容をファイル冒頭のコメントに追記せよ

## 参照データの読み方

### strategy.py (現在版)
現在の decide() ロジック。改善対象。

### game_history/latest.jsonl (ターン履歴)
各行がJSON: `{"turn", "score", "score_delta", "piece_count", "max_y", "next_type", "decision_x", "decision_reason", "merge_available", "best_merge_grade", "reactor_reactive_pairs", ...}`

履歴から分析すべきポイント:
- **マージ見逃し**: `merge_available=true` なのに score_delta=0 のターン
- **スコア停滞**: 連続してscore_deltaが0のターン列
- **高さ危機**: max_yが1.5を超えた後の推移
- **大型ピース散在**: reactor_reactive_pairsが常に0

### best_strategy.py (ベスト版)
最高スコアを記録した strategy.py。参考にせよ。

### game_state.json (最終盤面)
ゲームオーバー時の盤面。散在・到達不能ピースの分析に使用。

## 分析→改善の手順

1. **履歴分析**: latest.jsonl を全ターン分析
   - マージ成功率 (merge_available かつ score 増加のターン / 全ターン)
   - スコア停滞パターン (3ターン以上 score_delta=0)
   - max_y の推移 (危機的状況の検出と対処)
   - decision_reason の傾向

2. **最終盤面分析**: game_state.json
   - 大型ピース(type8+)の配置: 旗側集約できていたか
   - 到達不能ペア: 同type同士が離れすぎていないか
   - ピース分布: 左右バランス

3. **改善実装**: strategy.py の decide() を改善
   - 分析で発見した弱点を修正するロジックを追加
   - 例: マージ見逃し→マージ判定の閾値調整
   - 例: 高さ危機→危機回避ロジック追加
   - 例: 大型ピース散在→旗側集約ロジック追加

## 改善のヒント

### 使える情報
- `game_state["pieces"]`: 全ピースの位置・type・速度
- `game_state["next"]`/`game_state["nextNext"]`: 次と次の次のピース
- `analysis["results"]`: 全サンプルX座標の期待値ランキング（analyze_board.py計算済み）
- `analysis["reactor"]`: 反応器状態（同typeペア距離、パイプライン健全性）

### 有効なパターン
- **旗側集約**: type9+ の最初の配置側を記憶し、以後同じ側に配置
- **nextNext保護**: 次の次のピースのマージ経路を塞がない配置
- **高さ制限**: max_y > 1.5 で壁ドロップ禁止
- **シェイク戦略**: マージ不可が続く時、小ピースで下層を揺らす
- **連鎖設計**: typeN-1ペアをtypeNの近くに配置
- **モジュールレベル変数**: 試合内の状態保持（旗側、連続無マージ数、前回ドロップX等）

## 出力

strategy.py を**上書き**で出力せよ。Write ツールで `strategy.py` に書き込むこと。

注意:
- `if __name__ == "__main__"` ブロックは元のまま維持すること
- 冒頭コメントに変更履歴を追記すること
- decide() は必ず `{"x": float, "reason": str}` を返すこと
