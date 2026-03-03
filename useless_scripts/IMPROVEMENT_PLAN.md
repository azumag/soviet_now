# 画像認識精度 改善計画

## 現状の問題

現在GLM-4.7とHaiku 4.5にゲームをプレイさせているが、画像認識精度が極めて低い。

**具体的な症状（think.mdより）:**
- NEXTの旗を正確に識別できない
- 盤面上の旗の種類を特定することが困難
- 併合可能かどうかの戦略的判断ができない
- 結果としてランダム配置に近い動きになっている

**構造的な難しさ:**
- 15カ国の国旗が色・形ともに酷似（ソ連構成国は赤基調が多い）
- ブロックが国土の形で不規則（矩形ではない）
- 回転・重なりにより部分的に隠れる
- 小さいブロック（低レベル）は1280x720で細部が潰れる

---

## 改善施策一覧

### 施策1: Unity WebGL内部状態の直接取得（最優先・根本解決）

**概要:** Playwrightを使ってUnity WebGLのゲーム内部状態をJavaScript経由で取得し、画像認識を完全にバイパスする。

**効果:** 根本解決（画像認識不要になる）
**実装コスト:** 中（Unity内部の探索が必要）

**調査手順:**
1. Playwright DevToolsでUnity WebGLのグローバル変数を探索
2. `window.unityInstance` や `GameInstance` の存在確認
3. `SendMessage` で呼び出し可能なゲーム内関数を調査
4. ゲームのメモリ領域からブロック情報（種類・位置）を取得

**実装イメージ:**
```javascript
// soviet_game.mjs に追加
async function getGameState(page) {
  return await page.evaluate(() => {
    // 方法A: Unity SendMessage
    if (window.unityInstance) {
      window.unityInstance.SendMessage('GameManager', 'ExportState');
      return window.__gameState; // ゲーム側でグローバル変数に書き出す
    }
    // 方法B: WebGLメモリの直接読み取り
    // 方法C: Unity のログ出力をフック
  });
}
```

**出力例（理想形）:**
```json
{
  "next": "estonia",
  "nextNext": "armenia",
  "blocks": [
    {"type": "georgia", "level": 5, "x": 480, "y": 600},
    {"type": "georgia", "level": 5, "x": 520, "y": 580}
  ],
  "highestY": 320,
  "deadlineY": 100
}
```

**成功すれば:** AIへの入力をテキスト化でき、Haikuでも正確な戦略判断が可能になる。

---

### 施策2: エリア別クロップ送信

**概要:** 盤面全体の1枚画像ではなく、目的別にクロップした画像を送信する。背景（クレムリン、スコア表示等）のノイズを除去し、AIが各エリアに集中できるようにする。

**効果:** 高
**実装コスト:** 低

**クロップ対象:**

| 画像ファイル | 対象エリア | 目的 |
|-------------|-----------|------|
| `next_block.png` | NEXT表示（右上） | 次に落とす国旗の識別 |
| `board.png` | プレイエリア（中央の灰色枠内） | 盤面上のブロック配置・種類の分析 |
| `soviet_now.png` | 全体（従来通り） | 全体俯瞰・デッドライン確認 |

**メリット:**
- **NEXTクロップ:** 単体表示なので識別精度が大幅向上
- **プレイエリアクロップ:** 背景のクレムリン・スコア・BGMボタン等のノイズを除去。ブロックだけに集中でき、解像度も有効活用できる
- 全体画像も残すことで、デッドラインとの距離感など俯瞰的判断も可能

**実装:**
```javascript
// soviet_game.mjs のスクリーンショット処理に追加

// 全体スクリーンショット（従来通り）
await page.screenshot({ path: 'soviet_now.png' });

// NEXTブロック領域のクロップ（右上エリア）
await page.screenshot({
  path: 'next_block.png',
  clip: { x: 950, y: 30, width: 200, height: 200 }  // 要調整
});

// プレイエリアのクロップ（灰色枠内）
await page.screenshot({
  path: 'board.png',
  clip: { x: 300, y: 50, width: 650, height: 670 }  // 要調整
});
```

**プロンプト変更:**
- `next_block.png` → 「この画像からNEXTの国旗を特定せよ」
- `board.png` → 「この画像から盤面上の全ブロックの種類と位置を分析せよ」
- `soviet_now.png` → 「全体画像でデッドラインまでの余裕を確認せよ」

---

### 施策3: リファレンス画像の同時送信

**概要:** 15カ国の国旗一覧画像を作成し、プロンプトと一緒に毎回送信。「この中のどれに該当するか？」という比較タスクに変換する。

**効果:** 中
**実装コスト:** 低

**手順:**
1. ゲーム内の右下にある国旗進化図をクロップして `flag_reference.png` として保存
2. または15カ国の国旗を並べた一覧画像を手動作成
3. プロンプトで「flag_reference.pngの国旗一覧と照合して識別せよ」と指示

---

### 施策4: 段階的プロンプト分割（Multi-step Analysis）

**概要:** 1回のプロンプトで全てをやらせず、段階に分ける。

**効果:** 中
**実装コスト:** 中（ループスクリプトの改修必要）

**ステップ分割案:**
1. **識別フェーズ:** NEXTブロック画像だけ送信 → 国旗名を返す
2. **盤面分析フェーズ:** 盤面画像を送信 → 各ブロックの種類・位置リストを返す
3. **戦略フェーズ:** テキスト情報（盤面状態JSON + STRATEGY.md）のみ送信 → 座標を返す

**メリット:** 各ステップが単純なタスクになり、小さいモデルでも精度が出やすい。

---

### 施策5: Canvas ピクセルデータによる補助情報

**概要:** Playwrightでcanvasの特定領域のピクセルデータを取得し、色情報をテキストとしてAIに補助的に渡す。

**効果:** 低〜中
**実装コスト:** 中

**実装:**
```javascript
const colorInfo = await page.evaluate(() => {
  const canvas = document.querySelector('canvas');
  const ctx = canvas.getContext('2d');
  // NEXT領域の主要色を抽出
  const imageData = ctx.getImageData(950, 30, 200, 200);
  // 主要色のヒストグラムを計算して返す
});
```

**注意:** Unity WebGLのcanvasは `getContext('2d')` が使えない場合がある（WebGLコンテキストのため）。`readPixels` 等WebGL APIでの取得が必要になる可能性あり。

---

## 実装優先順位

| 順位 | 施策 | 効果 | コスト | 備考 |
|------|------|------|--------|------|
| **1** | Unity内部状態取得 | ★★★ 根本解決 | 中 | 成功すれば他は不要 |
| **2** | エリア別クロップ送信 | ★★☆ | 低 | NEXT+プレイエリア。施策1の調査中にすぐ実装可 |
| **3** | リファレンス画像 | ★☆☆ | 低 | 画像1枚用意するだけ |
| **4** | 段階的プロンプト | ★★☆ | 中 | ループスクリプト改修 |
| **5** | ピクセルデータ補助 | ★☆☆ | 中 | WebGL制約に注意 |

### 施策6: STATE.md 基準の状態遷移ステップ実行（次フェーズ）

**概要:** Ralph Loop（[参考記事](https://zenn.dev/azumag/articles/9f7b59f2c3cfb0)）の STATE.md 状態遷移パターンをゲームプレイループに適用。1手ごとの処理を複数フェーズに分割し、各フェーズをAIエージェントが段階的に実行する。

**効果:** 高（施策4の段階的プロンプトを状態マシンとして構造化）
**実装コスト:** 中

**状態遷移フロー:**

| 状態 | 処理内容 | 成果物 | 次の状態 |
|------|---------|--------|---------|
| OBSERVE | スクリーンショット取得・前処理 | `soviet_now.png`, `next_block.png`, `board.png` | IDENTIFY |
| IDENTIFY | NEXT旗・盤面ブロックの識別 | `tmp/IDENTIFY.md`（盤面状態JSON） | STRATEGIZE |
| STRATEGIZE | 戦略判断（STRATEGY.md参照） | `tmp/STRATEGY_DECISION.md`（配置座標・理由） | EXECUTE |
| EXECUTE | 座標クリック実行 | `rloop.sh` への座標出力 | VERIFY |
| VERIFY | 実行結果の確認・ゲームオーバー判定 | `tmp/VERIFY.md`（結果・学習メモ） | OBSERVE |
| GAME_OVER | 敗因分析・STRATEGY.md改訂 | STRATEGY.md更新 | OBSERVE（次ゲーム） |

**メインループ実装イメージ:**
```bash
while true; do
    state=$(head -n 1 "./STATE.md" | tr -d '\n\r')

    case "$state" in
    "OBSERVE")     # スクリーンショット取得＋クロップ（施策2）
                   node screenshot.mjs
                   echo "IDENTIFY" > STATE.md ;;
    "IDENTIFY")    # AI呼び出し：画像→盤面状態テキスト化
                   claude --prompt "$identify_prompt" --images next_block.png,board.png \
                     > tmp/IDENTIFY.md
                   echo "STRATEGIZE" > STATE.md ;;
    "STRATEGIZE")  # AI呼び出し：テキストのみで戦略判断
                   claude --prompt "$strategy_prompt" --context tmp/IDENTIFY.md,STRATEGY.md \
                     > tmp/STRATEGY_DECISION.md
                   echo "EXECUTE" > STATE.md ;;
    "EXECUTE")     # 座標抽出＋クリック実行
                   coord=$(head -n 1 tmp/STRATEGY_DECISION.md)
                   node click.mjs "$coord"
                   echo "VERIFY" > STATE.md ;;
    "VERIFY")      # 実行後確認・ゲームオーバー判定
                   node screenshot.mjs
                   result=$(claude --prompt "$verify_prompt" --images soviet_now.png)
                   if echo "$result" | grep -q "GAME_OVER"; then
                     echo "GAME_OVER" > STATE.md
                   else
                     echo "OBSERVE" > STATE.md
                   fi ;;
    "GAME_OVER")   # 敗因分析・戦略改訂
                   claude --prompt "$postmortem_prompt" \
                     --context tmp/IDENTIFY.md,tmp/STRATEGY_DECISION.md,STRATEGY.md
                   echo "OBSERVE" > STATE.md ;;
    esac

    sleep 2
done
```

**施策4（段階的プロンプト）との違い:**
- 施策4は「プロンプトを分ける」だけだが、施策6は**状態ファイルで進行管理**する
- 各フェーズの成果物が `tmp/` に残り、**デバッグ・改善が容易**
- 途中で止めて人間が介入（STATE.mdを手動書き換え）できる
- フェーズごとにモデルを使い分け可能（IDENTIFY=Sonnet, STRATEGIZE=Haiku等）
- スタック検出（同じ状態N回でフォールバック）を組み込める

**フォールバック機構:**
```bash
# 同じ状態が連続した場合、別モデルに切り替え
if [ "$same_count" -ge 5 ]; then
    # Haiku → Sonnet にアップグレード or GLM-4.7 に切り替え
    use_fallback=true
fi
```

**段階的導入:**
1. まず現行の `rloop.sh` に STATE.md の読み書きだけ追加
2. OBSERVE → EXECUTE の最小ループを動かす
3. IDENTIFY / STRATEGIZE フェーズを順次分離
4. VERIFY / GAME_OVER フェーズでフィードバックループを完成

---

## 推奨アプローチ

**フェーズ1（即時）:** 施策2のエリア別クロップを実装。低コストで一定の改善が見込める。

**フェーズ2（調査）:** 施策1のUnity内部状態取得を調査。DevToolsでゲームの構造を探索。

**フェーズ3（施策1が成功した場合）:** 画像認識を廃止し、テキストベースの盤面情報でAIに判断させる。Haikuでも十分な精度が出る。

**フェーズ3（施策1が失敗した場合）:** 施策3 + 施策4を追加実装。画像認識の精度を最大限引き上げる。

**フェーズ4（次フェーズ）:** 施策6のSTATE.md状態遷移を導入。ゲームループ全体を状態マシン化し、各フェーズの独立性・デバッグ性・モデル使い分けを実現。施策1〜5の成果を状態遷移の各フェーズに自然に組み込める統合フレームワークとなる。

---

## 強いモデルについて

**結論:** 強いモデル（Sonnet/Opus）は補助的には有効だが、根本解決にはならない。

- NEXT識別（単体表示）: Sonnet級でかなり改善する
- 盤面上の重なったブロック識別: 最強モデルでも信頼性に限界
- コスト: 1手ごとにOpus呼び出しは非現実的（速度・費用）
- **推奨:** 施策1〜5を先に実装し、それでも不足なら戦略フェーズのみSonnetを使う
