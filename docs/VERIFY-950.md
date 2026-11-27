# VERIFY-950: recognition-hold-fallback の固定 x=0 検証

- 検証日: 2026-11-27
- 対象Issue: azumag/soviet_now#950
- 対象HEAD: `e4c47b9` (`fix(soren91): 浮遊した低信頼の盤面上方観測を危険判定に使わない (#949)`)
- 作業範囲: read-only のコード・記録照合。コード変更・修正PR・本番操作なし。

## 結論

**(b) 設計どおり**

`recognition-hold-fallback` が盤面の最低レーンを見ずに固定 `x=0` へ投下しているという指摘は、コード上そのとおり確認できる。ただしこれは実装漏れや条件分岐の欠陥ではなく、bounded escape 導入時に文書化・テスト化された意図的な固定位置である。したがって現行仕様に対する実欠陥ではない。

一方で、Issue が示す観測表どおり中央が高い盤面では非最適になりうる。固定位置を改めるかどうかは実装欠陥の修正ではなく設計変更であり、オーナー判断と新旧比較が必要である。

## コード照合

確認したコードは以下のとおり。

1. `soren91/main.mjs` は `const HOLD_ESCAPE_X = 0;` を定義し、「bounded recognition-hold escape, never instead of a strategy decision」と明記している。
2. 非MOVE分岐で `holdReason === 'unknown-current'`、現フレームの校正が健全、かつ policy 上発動可能なときだけ、以下を実行する。
   - `executeDrop(page, HOLD_ESCAPE_X, calibration, observation)`
   - 履歴へ `decision: { x: HOLD_ESCAPE_X, reason: 'recognition-hold-fallback', hold: false }` を記録
3. この経路には盤面の高さ・最低レーン・着地面を計算する処理も、`x` を盤面状態から選ぶ処理もない。投下位置は常に `HOLD_ESCAPE_X`、すなわち `0` である。
4. `executeDrop` は受け取った `gameX` を `dropXToPixel(gameX, calibration)` で画面座標へ変換してクリックするだけで、fallback 時に限った追加の位置補正はない。

したがって、「固定 `x=0` へ盲目投下し、最低レーンを無視する」という動作記述はコードと一致する。

## 設計意図の照合

固定 `x=0` は後付けの偶然ではなく、導入時の仕様として確認できる。

- `soren91/PLAY_STRATEGY.md` の「認識保留の bounded escape（2026-10-15 オーナー判断）」は、連続 `unknown-current` の停滞を割るため「校正済みの中央 `x=0` へ1回だけ代替投下する」と明記している。
- 同じ節は「代替投下は現在ピースを同定できないときだけ使う固定位置であり、戦略判断の代替ではない」と明記している。
- `soren91/LATENCY.md` も「既定 `x=0`」の代替投下として記録している。
- `soren91/observation_guard.mjs` のコメントは、bounded escape を「owner-approved bounded relaxation of the no blind timeout drop rule」とし、中央への fallback drop と説明している。
- 導入コミット `75fa4cc1` (`feat(soren91): bounded escape for a persistent recognition hold (#656)`) のメッセージも、owner approval、`x=0`、1停滞あたり最大3回・4秒間隔、`decide()` は現在ピース不明時に使えないことを明示している。
- `tests/test_soren91_realtime_io.mjs` は persistent recognition hold で fallback drop が1回送られること、kill switch、発動上限、他reasonでは発動しないことを固定している。テスト内の `HOLD_ESCAPE_X` も `0` である。

以上から、固定位置は「現在ピース不明時に通常戦略を呼ばない」という設計の一部と確認できる。

## Issueの12観測との突き合わせ

Issue本文の表（2026-11-27 11:25–11:52、7試合・12回）を再集計した。

- 12回中6回は `x=0` の着地面が最低レーンより高い。
- そのうち3回（game 3 turn 16、game 5 turn 5、game 7 turn 11）は差が +1.0 以上。
- 残り6回は差0で、このうち空盤面または少数ピースでは複数xが同じ最低面となるため、`x=0` が最低面の一つになる。
- 最大差は game 7 turn 11 の +6.90 で、中央固定が強く不利だった可能性を示す。

この分布は、最低レーンを一切参照しない固定 `x=0` の実装から期待される結果と一致する。特に、盤面が平ら・低ピース数では差0になり、中央に塔がある局面では差が大きくなる。

なお `soren91/game_history/` は `.gitignore` 対象で、この検証環境の git checkout には生JSONLが存在しない。本検証はIssue本文に記録された抽出表の再集計とコード照合であり、生ログから着地面プローブを独立再計算したものではない。ただし結論には影響しない。固定 `x=0` であることはコードと導入時仕様から直接確認できるためである。

## 懸念の検討

Issue本文の懸念（固定 `x=0` は「現在ピース不明時に余計な戦略判断をしない」ための意図的設計の可能性）は、文書と導入コミットにより裏付けられた。

ただし、最低レーン選択は合体期待や戦略的先読みを必要としないため、「戦略判断の代替」と「盤面幾何だけの安全側fallback」の間には設計上の区別がありうる。現在ピース種別が不明でも、既置ピースの座標・半径・deadlineは観測できているという前提なら、高さだけの粗い選択は可能である。一方、それは固定中央という明示仕様を変える設計変更であり、中央へ寄せる意図や認識不能時の予測不能な物理挙動と衝突しうる。実機で新旧escapeを比較するまでは採否を決められない。

## 最終判定

- 実装が仕様とずれているか: いいえ
- 固定 `x=0` で最低レーンを無視しているか: はい
- それは意図した設計か: はい
- 判定: **(b) 設計どおり**
- 残る論点: 設計自体の改善提案としては有効。実装は別PR、オーナー判断、実機比較が必要。
