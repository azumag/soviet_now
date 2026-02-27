# Soren Game AI

Soviet/Soren パズルゲーム（ソ連共和国旗）の AI 自動プレイプロジェクト。

同typeのピース2個が接触すると合体進化する (`type_N + type_N → type_{N+1}`)。
プレイヤーはドロップX座標のみ指定可能。デッドライン超えでゲームオーバー。

## アーキテクチャ

```
soviet_local.mjs          ← ゲーム実行環境 (HTTP server + Playwright + Unity WebGL)
    ↕ commands.txt / game_state.json
AI ループ (3種類から選択)
    ├── eloop.sh           ← 自己改善ループ (推奨)
    ├── jloop.sh           ← JSON構造データ版ループ
    └── sloop.sh           ← 画像認識版ループ (レガシー)
```

## AI ループ

### eloop.sh — Self-Improving Strategy Loop (推奨)

Python スクリプト (`strategy.py`) が1試合を自律プレイし、試合終了後に AI がスクリプトを改善する「メタ学習ループ」。

```bash
node soviet_local.mjs &    # ゲーム起動
bash eloop.sh              # AI ループ開始
```

**フロー:**
```
eloop.sh (外側ループ)
  ├── strategy_runner.py    → 1試合を自律プレイ
  │     ├── game_state.json を読む
  │     ├── analyze_board.py で盤面解析
  │     ├── strategy.py の decide() でドロップX決定
  │     ├── commands.txt に書き込み
  │     └── game_history/latest.jsonl にターンログ記録
  ├── GAMEOVER 検知 → スコア取得
  ├── バージョン保存 (strategy_versions/vNNN_scoreXXX_strategy.py)
  ├── AI 呼び出し (prompts/improve_strategy.md)
  │     → strategy.py を解析・改善・上書き
  ├── バリデーション (decide() 存在・シグネチャ・テスト実行)
  │     → 失敗時は前バージョンに自動復元
  └── retry → 次の試合へ
```

**主要ファイル:**

| ファイル | 役割 |
|---------|------|
| `strategy.py` | AI が改善する決定関数。`decide(game_state, analysis) -> {x, reason}` |
| `strategy_runner.py` | 内側ループ。strategy.py で1試合プレイ + JSONL履歴記録 |
| `eloop.sh` | 外側ループ。試合実行→AI改善→バリデーション→リトライ |
| `analyze_board.py` | 盤面解析。マージ判定・着地予測・期待値計算 |
| `prompts/improve_strategy.md` | AI改善用プロンプト |
| `strategy_versions/` | strategy.py のバージョン履歴 |
| `game_history/` | 試合ごとのターンログ (JSONL) |
| `best_score.txt` | ハイスコア記録 |

### jloop.sh — JSON-based State Loop

毎ターン AI (LLM) を呼び出して盤面判断→ドロップを行う。`analyze_board.py` の解析レポートを入力とする。

```bash
node soviet_local.mjs &
bash jloop.sh
```

**ステートマシン:** `WAIT_READY → DECIDE → EXECUTE → WAIT_READY`
- DECIDE: `analyze_board.py` で盤面解析 → AI にプロンプト → `tmp/plan.md` / `tmp/plan.json` 出力
- EXECUTE: plan からドロップ座標抽出 → `commands.txt` 書き込み
- GAME_OVER: AI に振り返り → retry

### sloop.sh — Simple State Loop (レガシー)

画像認識ベース。`OBSERVE → DECIDE → EXECUTE` の3段構成。スクリーンショットから盤面を読み取る。

```bash
node soviet_local.mjs &
bash sloop.sh
```

## セットアップ

### 前提条件

- **ゲームソースコード**: [ソ連パズル（BOOTH）](https://chemicalpudding.booth.pm/items/5222746) から購入・ダウンロード
- **Unity**: 2022.3.62f3 LTS (Unity Hub 経由でインストール)
- **Unity WebGL Build Support**: Unity Hub → Installs → Add Modules → WebGL Build Support
- **Node.js**: v18+ (Playwright 依存)
- **Python**: 3.10+ (`analyze_board.py`, `strategy.py` 用)
- **LLM CLI ツール** (AI ループで使用):
  - [opencode](https://github.com/opencode-ai/opencode) — GLM 系モデルをエージェントとして実行
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`) — Anthropic Claude を CLI から実行
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`gemini`) — Google Gemini を CLI から実行

### LLM モデル設定

AI ループ (`eloop.sh`, `jloop.sh`, `sloop.sh`) は複数の LLM CLI ツールを統一的に呼び分ける `run_cmd()` 関数を持つ。

#### モデル変数

`eloop.sh` / `jloop.sh` の冒頭で使用モデルを設定:

```bash
MODEL_PRIMARY="glm"              # 主要モデル
MODEL_FALLBACK="opencode:glmflash"  # フォールバック
```

`run_ai()` は PRIMARY でまず実行し、期待出力が得られなければ FALLBACK に切り替える。

#### モデルスペックと CLI マッピング

`run_cmd()` はスペック文字列を解析して対応する CLI を呼び出す:

| スペック | CLI コマンド | 説明 |
|---------|------------|------|
| `glm` | `opencode run --agent="zai"` | GLM-4.7 (zhipu) — **zai エージェント**として設定済み |
| `opencode:glmflash` | `opencode run --agent="glmflash"` | GLM-4-Flash (軽量フォールバック) |
| `opencode:<agent>` | `opencode run --agent="<agent>"` | 任意の opencode エージェント |
| `sonnet` | `claude -p --model=sonnet --permission-mode=acceptEdits` | Claude Sonnet |
| `opus` | `claude -p --model=opus --permission-mode=acceptEdits` | Claude Opus |
| `claude` | `claude -p --model=Haiku --permission-mode=acceptEdits` | Claude Haiku |
| `gemini` | `gemini -p -y -s` | Gemini (デフォルトモデル) |
| `gemini-flash` | `gemini -p -y -s --model=gemini-2.5-flash` | Gemini 2.5 Flash |

#### opencode のエージェント設定

opencode は `.opencode/agents/` ディレクトリにエージェント設定ファイルを配置して使用する。本プロジェクトでは:

- **zai** — GLM-4.7 (zhipu/glm-4.7) を使用するメインエージェント。`MODEL_PRIMARY="glm"` で呼び出される
- **glmflash** — GLM-4-Flash を使用する軽量エージェント。`MODEL_FALLBACK="opencode:glmflash"` で呼び出される

`opencode run --agent="zai"` のように `--agent` フラグでエージェント名を指定すると、対応するモデル設定で LLM が実行される。

#### Claude Code の使い方

Claude Code (`claude`) は `-p` (パイプモード) でプロンプトを渡し、`--permission-mode=acceptEdits` でファイル編集を自動許可する:

```bash
claude -p "$prompt" --model=sonnet --permission-mode=acceptEdits
```

対話的に使う場合は引数なしで `claude` を実行。本プロジェクトの `CLAUDE.md` が自動読み込みされ、プロジェクト固有の指示が適用される。

### 1. ゲームのビルド

本プロジェクトはゲームの Unity ソースコードに **JS Bridge を注入した改造ビルド**を使用する。
JS Bridge (`SorenBridge.cs` + `SorenBridge.jslib`) が Unity と Playwright の間で盤面データとコマンドを双方向通信する。

> **詳細は `sorengame/BUILD_GUIDE.md` を参照。** 以下は概要のみ。

#### ソースコード展開

BOOTH からダウンロードした ZIP を展開。**日本語ファイル名を含むため `ditto` を使用する**（`unzip` は文字化けする）:

```bash
ditto -x -k --sequesterRsrc soren-game.zip /tmp/soren-original/
ditto -x -k --sequesterRsrc soren-game-fixed.zip /tmp/soren-fixed/
```

#### アセット補完

展開済みプロジェクト (`sorengame/_extracted/soren-game-fixed/`) には以下が不足している。**2つの ZIP から補完が必要**:

| 補完元 | コピー対象 | 理由 |
|--------|-----------|------|
| `soren-game.zip` | `Texture/Republic/` (001-016.png + .meta) | Prefab の GUID が元 ZIP のテクスチャを参照 |
| `soren-game.zip` | `Texture/Stage/`, `Texture/Hammer and Sickle/` | UI 背景・槌鎌マーク |
| `soren-game.zip` | `Assets/TextMesh Pro/` | TMP シェーダー（なければテキストがマゼンタ） |
| `soren-game-fixed.zip` | `Texture/` (Background, Circle, Effect, Flag Animation) | テクスチャ補完 |
| `soren-game-fixed.zip` | `Sound/` (BGM, SE) | 音声ファイル（日本語ファイル名） |

#### JS Bridge 注入

本リポジトリの以下のファイルがブリッジ機構:

- `Assets/SORENGAMEFIXED/Script/SorenBridge.cs` — Unity 側: `MainManager.GetBridgeState()` / `ExecuteBridgeCommand()` を 0.1秒ごとに呼び出し
- `Assets/Plugins/WebGL/SorenBridge.jslib` — JS 側: `window.__sorenGameState` / `window.__sorenCommand` を介してブラウザとやり取り
- `Assets/Editor/WebGLBuilder.cs` — ビルド時に `SorenBridge` コンポーネントを Main Manager に自動アタッチ

#### ビルド実行

**macOS NAS 上ではビルド不可**（`._*` リソースフォークファイルが il2cpp エラーを引き起こす）。必ずローカルディスクで作業:

```bash
# ローカルにコピー + AppleDouble 除去
rm -rf /tmp/soren-unity
cp -R sorengame/_extracted/soren-game-fixed/* /tmp/soren-unity/
dot_clean /tmp/soren-unity/

# バッチビルド
WEBGL_BUILD_PATH=/tmp/soren-build \
/Applications/Unity/Hub/Editor/2022.3.62f3/Unity.app/Contents/MacOS/Unity \
  -quit -batchmode -nographics \
  -projectPath /tmp/soren-unity \
  -buildTarget WebGL \
  -executeMethod WebGLBuilder.BuildWebGL \
  -logFile /tmp/unity_build.log

# ビルド結果をデプロイ
cp -R /tmp/soren-build/* sorengame/build/
```

#### トラブルシューティング

| 症状 | 原因 | 解決策 |
|------|------|--------|
| マゼンタの国旗ピース | `Texture/Republic/` 未配置 | `soren-game.zip` から Republic/ をコピー |
| マゼンタの UI テキスト | `TextMesh Pro/` 未配置 | `soren-game.zip` から `Assets/TextMesh Pro/` をコピー |
| il2cpp コンパイルエラー | NAS 上の `._*` ファイル | ローカルディスクにコピー + `dot_clean` |
| unzip で音声ファイル破損 | 日本語ファイル名の文字化け | `ditto -x -k --sequesterRsrc` を使用 |
| wasm-opt SIGABRT | Library キャッシュ不整合 | `rm -rf /tmp/soren-unity/Library` して再ビルド |

### 2. Node.js 依存インストール

```bash
npm install        # playwright, sharp 等
npx playwright install chromium
```

### 3. 起動

```bash
# ゲーム起動 (ローカルビルド)
node soviet_local.mjs &

# AI ループ開始 (いずれか選択)
bash eloop.sh      # 自己改善ループ (推奨)
bash jloop.sh      # JSON構造データ版
bash sloop.sh      # 画像認識版 (レガシー)
```

オンライン版 (unityroom.com) を使う場合:
```bash
node soviet_game.mjs &
```

## コマンドインターフェース

`commands.txt` に書き込むとゲーム操作が実行される。

```bash
# キャンバス座標でドロップ (x: 410-830, y: 350固定)
echo "620,350" > commands.txt

# ゲームオーバー時にリトライ
echo "retry" > commands.txt

# JSON形式
echo '[{"action":"cmd","value":"FIXUI"}]' > commands.txt
```

### game_state.json

JS Bridge 経由で Unity から読み出した構造データ。

```json
{
  "state": "MOVE",
  "score": 1572,
  "next": {"type": 5, "r": 0.477},
  "nextNext": {"type": 9, "r": 1.068},
  "pieces": [{"id": 2, "type": 8, "x": -2.369, "y": -3.739, "r": 0.846, ...}],
  "shapes": {"8": [[x,y], ...]}
}
```

- `state`: MOVE / DROP / GAMEOVER / STOP
- `type`: 1-16 (共和国番号、1=最小、16=ソ連)
- `pieces`: 全ピースの位置・半径・速度・回転
- `shapes`: ピース種別ごとの凸ポリゴン頂点

## 戦略: 人工化学フレームワーク

### ゲームの本質 — 物理挙動を伴う項書き換え系

このゲームは**空間に埋め込まれた項書き換え系 (Term Rewriting System) としての人工化学**である。

項書き換え系では、記号列（項）に対して書き換え規則を繰り返し適用して計算を進める。本ゲームでは「ピース」が項、「同種合体」が書き換え規則に対応する。通常の項書き換え系との決定的な違いは、**書き換えの発火条件が物理空間上の接触**であること — つまり書き換え規則の適用可能性が空間的配置に依存する。

| 人工化学の構成要素 | ゲームにおける対応 | 項書き換え系との対比 |
|---|---|---|
| 分子種 S | type 1〜16 のピース | 項のアルファベット |
| 反応規則 R | `type_N + type_N → type_{N+1}` | 書き換え規則 |
| 反応器 A | 物理エンジン (重力・衝突・回転・爆発) + 箱型容器 | 書き換え戦略 + 空間制約 |
| 反応物供給 | next/nextNext のドロップ | 項の入力 |

標準的な人工化学の三つ組 `(S, R, A)` に加えて、このシステムは以下の特性を持つ:

- **空間的局所性**: 反応（書き換え）は物理的に接触したペアのみで発生。同じ分子種でも空間的に離れていれば反応不可能
- **重力による非対称性**: ピースは下方に蓄積し、上方から供給される。これが反応器の空間構造を支配する
- **爆発衝撃波**: 反応（マージ）成功時に force=450, radius=2.0 の衝撃波が発生し、周囲の分子の空間配置を擾乱する。これが連鎖反応（多段書き換え）の主因
- **ポリゴン形状**: ピースは円ではなく凸ポリゴン。着地後に回転・転がりが発生し、最終位置が予測困難

### プレイヤーの役割 = 反応器管理者

プレイヤーはドロップX座標のみ制御可能。反応規則 R は固定されており変更できない。したがってプレイヤーの役割は**反応器 A の管理** — 反応物の空間配置を制御して反応効率を最大化すること。

これは化学工学における反応器設計問題と同構造:

```
入力: ランダムな分子種が順次供給される (next/nextNext)
制御変数: 供給位置 (ドロップX座標, -3.0〜+3.0)
目的関数: 総反応収量（スコア）の最大化
制約: 容器溢れ (デッドライン超え) で停止
```

### 人工化学から導出される戦略原則

この理論的枠組みから、以下の戦略が自然に導出される:

#### 1. 濃度管理（同type集約）

化学反応速度は反応物の濃度に比例する（質量作用の法則の空間版）。同typeピースが空間的に散在すると「希薄溶液」状態となり、反応（マージ）確率が事実上ゼロに低下する。**同typeピースの空間的密度を最大化する**ことが反応効率の基本。

`analyze_board.py` の `calc_reactor_state()` がこれを定量化:
- `reactive_pairs`: 接触圏内の同typeペア数（即時反応可能）
- `near_pairs`: 近接ペア（触媒操作で反応誘導可能）
- `type_count`: 分子種ごとの濃度

#### 2. パイプライン維持（反応経路の接続性）

項書き換え系では `type1 → type2 → ... → type16` という書き換え列が最大得点経路。この経路が成立するには、各段階の反応物（typeN と typeN+1）が空間的に近接している必要がある。パイプラインの「断絶」（隣接typeが空間的に分離）は連鎖反応の停止を意味する。

`analyze_board.py` の `pipeline` がこれを監視:
- `[OK]`: typeN と typeN+1 が距離 3.0 以内
- `[WARN]`: 距離 3.0-5.0（連鎖困難）
- `[BROKEN]`: 距離 5.0 超（経路断絶、即時修復が必要）

#### 3. 触媒操作（間接的反応誘発）

直接接触していなくても、小ピースの投入による物理衝撃（シェイク）や、マージ時の爆発衝撃波で間接的に反応を誘発できる。これは化学における触媒の役割に相当する:

- **シェイク触媒**: 小ピース(type1〜4)は比較的重量がある。 盤面ピースの重量バランスに不均衡がおきると、ピースが重みで振動し、攪拌効果があり、接触誘発
- **爆発連鎖**: typeN-1 ペアを typeN の近くに配置 → N-1 マージの衝撃波で typeN ペアも接触 → 多段連鎖
- **壁バウンド**: 壁際マージの射出で新ピースが反対方向に飛び、離れたピースと予期せぬ反応

#### 4. 旗側集約（空間的対称性の自発的破れ）

大型ピース (type9+) は半径が大きく移動困難。容器の左右に分散すると二度と合流できない。これは相分離（空間的対称性の自発的破れ）として理解できる: 最初の type9+ が置かれた側（旗側）に以後の大型ピースを全て集約することで、高type反応の空間的条件を維持する。

旗側集約は本プロジェクトで最も強い実証的根拠を持つ原則であり、49個の確定済み原則の多くが旗側管理に関連する。

#### 5. 連鎖設計（多段書き換えの仕込み）

項書き換え系の最大の特徴は**一つの書き換えが次の書き換えの発火条件を満たす**連鎖。ゲームでは `N-1+N-1→N` の生成物が既存の typeN と接触して `N+N→N+1` を誘発する多段連鎖がこれに対応する。

連鎖設計 = typeN-1 ペアを typeN の近くに事前配置しておくこと。爆発衝撃波による空間擾乱が連鎖の確率を高める。

### 実装における理論の反映

| 理論的概念 | analyze_board.py での実装 | strategy.py での利用 |
|---|---|---|
| 反応確率 | `MERGE_PROB`: DIRECT=0.90, NEAR=0.50 | 期待値ベースのドロップ位置選択 |
| 連鎖反応期待値 | `calc_chain_potential()` | マージ後のtype+1連鎖確率 |
| 触媒効果 | `calc_shake_ev()` | シェイクによる間接マージ期待値 |
| 爆発衝撃波 | `estimate_explosion_displacement()` | マージ後の周囲ピース移動予測 |
| パイプライン健全性 | `calc_reactor_state()` の `pipeline` | 断絶検知→修復優先 |
| 空間的濃度 | `reactive_pairs`, `near_pairs` | 即時反応可能ペアへの触媒投入判断 |

### 関連文献

- `strategy_versions/best_score*_strategy.py` — ハイスコア時の戦略 (殿堂入り)
