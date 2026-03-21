# Strategy Improvement Prompt

You are 同志AI — improving the strategy for a Suika-style (watermelon game / ソ連ゲーム) physics puzzle game.

## Game Rules — 盤面の物理ルール

### ピースと併合
- 15種類のピース (type 1〜15)。type が大きいほどサイズ（半径）が大きい
- **同じ type のピース同士が物理的に接触すると、1つ上の type に併合**: type N + type N → type N+1
- 併合するとスコア加算。高い type ほど高得点
- type 15 が最大（ソ連建国）。type 15 同士の併合はない
- **併合は物理的に接触したペアのみ発生**。同じ type でも離れていれば併合しない

### 物理エンジンの挙動
- ピースは**重力で落下**し、他のピースや壁・床と**衝突・回転**する
- ピースは円ではなく**国土形状の凸ポリゴン**（異方性がある）。着地後に回転・転がりが発生し、最終位置の正確な予測は困難
- **併合時に爆発衝撃波が発生**。周囲のピースが押しのけられ、空間配置が大きく変わる
- この衝撃波が**連鎖反応の主因**: 併合 → 衝撃波 → 別のピースが接触 → さらに併合

### 連鎖反応（チェイン）— 高得点の鍵
- type N-1 のペアを type N の近くに事前配置 → N-1 併合の衝撃波で type N 同士も接触 → 多段連鎖
- **連鎖を設計することが高スコアへの最重要戦略**
- 壁際での併合は反対方向への射出を引き起こし、予想外の連鎖が生まれることもある
- 小ピース (type 1〜4) の落下衝撃で周囲のピースが動く「攪拌効果」も間接的な併合トリガーになる

### 盤面
- Board X range: [-3.0, +3.0] (walls at ±3.5)
- Board Y range: [-5.0 floor, +3.32 deadline]
- ピースがデッドライン (~y=2.5) を超えるとゲームオーバー
- プレイヤーが制御できるのは**ドロップ X 座標のみ**（Y は重力任せ）

### 91人対戦版の特徴
- 他プレイヤーとリアルタイム対戦。生存とスコアの両方が重要
- **おじゃまブロック**: 対戦相手から送られる灰色ブロック（後述）
- おじゃまゲージ: 相手の併合活動に応じて溜まり、満タンで自分の盤面におじゃまが降る

## Strategy Interface
```javascript
export function decide(boardState) {
  // boardState: {
  //   pieces: [{type, x, y, r}],   // 検出されたピース (国ピースのみ、おじゃまは除外)
  //   next: {type, r},              // 次にドロップするピース (nextPieces[0] と同じ)
  //   nextPieces: [{type, r}, ...], // 次の最大3ピース (1番目=next, 2番目, 3番目)
  //   hold: {type, r} | null,       // HOLD領域のピース (null=空)
  //   canHold: boolean,             // このターンでHOLD使用可能か
  //   score: number,
  //   confidence: number,
  //   garbage: { ratio, height, pixelCount, gauge }  // おじゃまブロック情報
  //     ratio: ボード内のおじゃまの割合 (0-1)
  //     height: おじゃまの最高到達Y座標 (ゲーム座標, 高い=危険)
  //     gauge: おじゃまゲージレベル (0-1, 1に近いほどおじゃま発動が近い)
  // }
  // Returns: { x: number [-3.0, 3.0], reason: string, hold?: boolean }
}
```

## HOLD Mechanic
- Right-click saves the current cursor piece to HOLD, or swaps with the held piece
- `boardState.hold`: the currently held piece ({type, r}) or null if empty
- `boardState.canHold`: true if hold is available (resets after each drop, false after holding)
- Return `{ x: 0, reason: 'HOLD_...', hold: true }` to use HOLD (x is ignored)
- After holding, the bot re-analyzes the board with the swapped piece before deciding
- Use HOLD when: current piece has no merge targets but held piece does, or save current piece for later
- **HOLD logic MUST be preserved in any strategy improvement**

## Garbage Blocks (おじゃまブロック)
- Gray blocks sent by opponents
- They stack from the bottom of the board
- They disappear when you merge pieces (create a country)
- garbage.ratio: proportion of board occupied by garbage (0-1)
- garbage.height: highest Y coordinate of garbage (game coords, higher = more dangerous)
- garbage.gauge: ojama gauge level (0-1), indicates how close to the next ojama drop
  - gauge >= 0.3: prepare for incoming ojama (boost merge priority)
  - gauge >= 0.6: ojama imminent (aggressively prioritize merges)
- When garbage.ratio > 0.15, enter OJAMA_MERGE mode (prioritize merges early)
- When garbage.ratio > 0.4, enter GBG_URGENT mode (aggressive clearing)
- Merging near the bottom of the board is more effective for clearing garbage

## 戦略原則

### 1. 濃度管理（同 type 集約）
同 type ピースが空間的に散在すると併合機会が激減する。**同 type ピースを空間的に近くに集める**ことが基本中の基本。

### 2. パイプライン維持（type の階段配置）
type 1 → 2 → ... → 15 への併合経路を維持する。各段階の type が空間的に近接していれば連鎖が途切れない。隣接 type 同士が離れすぎると連鎖停止 = スコアが伸びない。

### 3. 大型ピースの片側集約
大型ピース (type 9+) は半径が大きく一度置くと移動困難。左右に分散すると二度と合流不可能。**最初の大型ピースが置かれた側に以後の大型を全て集約**すること。

### 4. 高さ管理
デッドラインを超えたらゲームオーバー。特に高い列への積み増しは避け、低い位置に併合機会を作る。

### 5. 連鎖設計
type N-1 ペアを type N の近くに配置し、併合時の衝撃波で自動的に次の併合が起きるよう仕込む。これが最も得点効率が高い。

### 6. 先読み (Look-ahead)
nextPieces[1], [2] を使って 2〜3 手先を計画。次に来るピースの併合先を今のうちに準備する。

### 7. 小ピースの触媒利用
small pieces (type 1〜4) は落下時の衝撃で周囲のピースを揺らす。マージ先がない小ピースでも、高密度エリアに落とせば攪拌効果で併合を誘発できる。

## Important Constraints
- Input comes from screenshot analysis (imperfect - pieces may be misclassified)
- Strategy should be noise-tolerant
- Keep the code simple and readable
- Do NOT import external modules - pure logic only
- You MUST only output strategy.mjs code. Do NOT modify any other files
- The function signature `export function decide(boardState)` MUST be preserved
- Return value MUST be `{ x: number, reason: string, hold?: boolean }` where x is in [-3.0, 3.0]
- HOLD logic (checking boardState.hold and canHold) MUST be preserved
- Do NOT use async/await, fetch, fs, or any side effects - pure computation only
