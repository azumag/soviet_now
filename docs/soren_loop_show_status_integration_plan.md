# Soren Loop と Show Status 統合計画

## 目的

`soren_loop.sh` (メインループ) と `show_status.sh` (ステータス表示) を統合し、単一のステータス表示スクリプトとして一元管理することで、メンテナンス性と表示の一貫性を向上させる。

## 現状の課題

### 現在のアーキテクチャ

**soren_loop.sh (336行)**
- メインループ（while true）
- 1試合ずつプレイ: `play_one_game()` → `post_game_bookkeeping()` → `prepare_next_game()` → `trigger_adaptive_improvement()`
- メリケンAIタイム制御（20時台）
- eloop_lib.sh と eloop.sh の動的読み込み

**show_status.sh (1342行)**
- eloop 全体のステータス表示（4セクション: CORE/AUDIO/TWITCH/STRATEGY）
- AI改善プロセスの監視
- Workers, Queue, Regression, Rollbacks等の詳細情報
- 10秒間隔で常時表示（watchモード）

### 分離による問題点

1. **Loop Running 状態の分散**
   - `soren_loop.sh` と `show_status.sh` が異なる方法で `loop_running` を判定
   - どちらかが実装を更新する際、両方を更新する必要がある

2. **Workers/Queue情報の重複**
   - `soren_loop.sh` は Worker 状態を直接的に表示しない
   - `show_status.sh` は Workers をカウントして表示するが、これは loop 内部の状態と完全に同期するわけではない

3. **Meriken Time の通知ロジックの重複**
   - `soren_loop.sh` と `show_status.sh` で同じメッセージを表示する箇所がある

4. **改善プロセスの監視**
   - `soren_loop.sh` は改善のトリガーのみを制御
   - `show_status.sh` が改善の状態を監視・表示する

## 実装計画

### アーキテクチャの変更

#### 方針A: Show Status に統合（推奨）

```
show_status.sh (統合版)
├── メインループ (soren_loop.sh の while true をここに移行)
│   ├── eloop_lib.sh/source
│   ├── eloop_lib.sh/source
│   ├── loop_running 状態管理 (show_status.sh 内で一元管理)
│   └── play_one_game() → post_game_bookkeeping() → prepare_next_game() → trigger_adaptive_improvement()
├── メインループ変数展開
│   ├── GAME_NUM
│   ├── IMPROVE_PID
│   ├── HALT_STRATEGY_AFTER_SOVIET
│   ├── STOP_REQUESTED
│   └── MERIKEN_TIME_PENDING
├── UI表示 (現在の4セクション)
│   ├── CORE: Loop, Workers, Queue, QueueMeter, Safety
│   ├── AUDIO: Say, VOICEVOX, Radio, CommentGen, CommentQ, RadioQ, PlaybackQ, TriggerQ
│   ├── TWITCH: Chat, Pending, Latest
│   ├── STRATEGY: Version, DecideHash, Score, BestRef, Regression, Rollbacks
│   └── AI IMPROVE: 現在の改善プロセス
└── ラインログ表示モード
    └── --log-only オプションで一行ログのみ表示
```

#### 方針B: soren_loop.sh に表示ロジックを移行

```
soren_loop.sh (統合版)
├── メインループ
├── ステータス表示 (show_status.sh の表示ロジックをここに移行)
├── 一行ログ表示モード (--log-only)
└── 独立した watch モード (--watch-interval オプション)
```

**推奨**: 方針A（show_status.sh に統合）

理由:
- `show_status.sh` が既に eloop 全体の監視ロジックを持っている
- 一行ログ表示も、単一のスクリプト内で統合する方が自然
- eloop システム自体が「改善」を前提としているため、監視・表示機能を統合することで、改善中の状態をリアルタイムに把握しやすい

### ファイル構成の提案

```
show_status.sh (統合版)
├── #!/bin/bash (シバン変更)
├── 設定変数 (MIN_GAMES_BEFORE_IMPROVE, REGRESSION_*, BRANCH_* 等)
├── ログ関数 (log, log_line)
├── メインループ (soren_loop.sh のロジック)
│   ├── ドロップイン置換用: eloop.sh の関数群
│   ├── loop_running 状態管理
│   ├── Meriken Time 制御
│   └── トリガー改善
├── 状態ファイルアクセス関数 (現在の show_status.sh のロジック)
│   ├── improve_state.json
│   ├── rolling_scores.json
│   ├── best_strategy_anchor.json
│   ├── radio_state 等
├── UI描画関数
│   ├── show_status()
│   ├── show_status_line_log()
│   └── show_status_fullscreen()
└── 実行ループ (watchモード + line-logモード)
```

### 一行ログ表示の仕様

> **レビュー後**: モードを明確に分けることで、プロセス構造の複雑化を回避

#### オプション形式

```bash
# watch モード（監視のみ、デフォルト）
./show_status.sh

# watch モード（3秒間隔）
./show_status.sh 3

# loop モード（メインループ実行）
./show_status.sh --loop

# 一行ログ表示モード
./show_status.sh --log-only
```

#### 一行ログのフォーマット

```
[CORE] Loop: RUNNING PID=12345 | Queue: 3/12 games (avg: 450,520,0) | Workers: 4/5 | Status: normal
[AUDIO] Say: PLAYING radio:news (phase: waiting) | Radio: QUEUED [news] | CommentGen: generating
[TWITCH] Chat: CONNECTED | Pending: 5 comments | Latest: 20:30:01 azumagdev: いいね
[STRATEGY] Version: 20250325-01 | DecideHash: a1b2c3d4 | Score: comp=1234 p50=1120 q25=1050 n=3 | Regression: safe (NO anchor=xxxxxxxx n=3) | Improve: RUNNING PID=67890 (15% executing)
```

#### 表示情報の構成

**CORE セクション**
- Loop: RUNNING/STOPPED, PID
- Queue: 減算済みスコア, min_games ゲート
- Workers: 0-5 の現在稼働数
- QueueMeter: 総キュー数（A:蓄積, C:コメント, T:Twitch）

**AUDIO セクション**
- Say: PLAYING/PREPARING/WAITING/RETRY/SILENT, PID, phase, source
- VOICEVOX: SYNTH/locked, streaming/locked
- Radio: PLAYING/QUEUED/IDLE, corner, elapsed
- CommentGen: running/idle, phase
- CommentQ: playing/queued 数
- RadioQ: playing/queued 数

**TWITCH セクション**
- Chat: CONNECTED/DISCONNECTED, PID
- Pending: 未読コメント数
- Latest: 最新コメント（最大W-18文字）

**STRATEGY セクション**
- Version: 最新バージョン名, 行数
- DecideHash: MD5ハッシュ
- Score: comp, p50, q25, n
- BestRef: best hash, comp, p50, q25, n
- Regression: safe/warning/trigger, detail
- Rollbacks: total, rejected, last_age
- RB History: 最新2件
- AI IMPROVE: RUNNING/IDLE, PID, progress, phase, AI source, AI output

#### 一行ログの長さ制限

- 最大幅: `W=57`
- 詳細情報は必要に応じて切り捨て（簡易化）

### 実装詳細

#### 1. メインループの統合

`soren_loop.sh` のメインループを `show_status.sh` 内に移行:

```bash
main_loop() {
    while true; do
        # stop-file チェック
        if [ -f tmp/stop ]; then
            log "[STOP] Stop file detected"
            rm -f tmp/stop
            exit 130
        fi

        # .env 再読込
        [ -f .env ] && set -a && . ./.env && set +a

        # eloop_lib.sh/eloop.sh source
        source ./eloop_lib.sh 2>/dev/null || true
        source ./eloop.sh 2>/dev/null || true

        # GAME_NUM 更新
        GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

        # 改善状態チェック
        check_and_harvest_improvement
        start_comment_player
        start_comment_watcher
        process_external_audio_triggers "$GAME_NUM" "$(_last_score)"

        # HALT_STRATEGY_AFTER_SOVIET チェック
        if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
            log "[HALT] strategy停止中: コメント返し/読み上げのみ継続"
            sleep 5
            continue
        fi

        # manual_meriken_mode チェック
        if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
            if command -v soren91_is_running >/dev/null 2>&1 && ! soren91_is_running 2>/dev/null; then
                soren91_start 2>/dev/null || true
            fi
            log "[PAUSE] manual_meriken_mode: ゲームプレイ一時停止 (メリケンAI手動モード)"
            sleep 10
            continue
        fi

        # 改善中チェック
        if _is_improve_running; then
            log "[PAUSE] 改善中: ゲームプレイ一時停止 (メリケンAIが代打中)"
            sleep 10
            continue
        fi

        # MERIKEN_TIME_PENDING チェック
        if [ "${MERIKEN_TIME_PENDING:-0}" -eq 1 ]; then
            MERIKEN_TIME_PENDING=0
            if command -v _soren91_enabled >/dev/null 2>&1 && _soren91_enabled; then
                # 20時台メリケンAIタイム開始
                {
                    _mt_file=$(mktemp /tmp/eloop_meriken_time.XXXXXX)
                    printf '%s\n' "20時から21時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております" > "$_mt_file"
                    SAY_VOICEVOX_SPEAKER_OVERRIDE="${SOREN91_VOICEVOX_SPEAKER:-46}" SAY_CONTEXT_LABEL="meriken_time:announce" ./say_enqueue.sh "$_mt_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
                    rm -f "$_mt_file"
                } &
                ./twitch_chat.sh send "20時から21時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております 【91人対戦】ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91" 2>/dev/null &
                _run_scheduled_meriken_time_window \
                    "improve_complete" \
                    "[MERIKEN_TIME] 改善完了→20時台: メリケンAIタイム開始"
            fi
        fi

        # 非同期ジョブスケジューリング
        SCHEDULE_GAME_NUM="$GAME_NUM"
        SCHEDULE_SCORE=$(_last_score)
        schedule_nonessential_audio_jobs "$SCHEDULE_GAME_NUM" "$SCHEDULE_SCORE"

        # 1試合プレイ
        play_one_game
        play_rc=$?
        if [ "$play_rc" -eq "${PLAY_RECOVERED_RETRY_RC:-75}" ]; then
            log "[RECOVERY] decide例外リカバリ済み: この試合の後処理をスキップして次へ"
            sleep 2
            continue
        fi

        # 後処理
        post_game_bookkeeping

        # 定期 tmp/ クリーンアップ
        if (( GAME_NUM % 50 == 0 )); then
            cleanup_tmp_files
        fi

        # HALT_STRATEGY_AFTER_SOVIET チェック（再）
        if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
            log "[HALT] retry・次ゲーム操作を停止"
            sleep 5
            continue
        fi

        # 改善トリガー
        trigger_adaptive_improvement

        # サイクル区切り+20時台 Meriken Time チェック
        _meriken_acc_count=-1
        if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
            _meriken_acc_count=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo -1)
        fi
        if [ "${_meriken_acc_count}" -eq 0 ] && [ "$(date +%H)" = "20" ]; then
            if command -v _soren91_enabled >/dev/null 2>&1 && _soren91_enabled; then
                soren91_start 2>/dev/null || true
                {
                    _mt_file=$(mktemp /tmp/eloop_meriken_time.XXXXXX)
                    printf '%s\n' "20時から21時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております" > "$_mt_file"
                    SAY_VOICEVOX_SPEAKER_OVERRIDE="${SOREN91_VOICEVOX_SPEAKER:-46}" SAY_CONTEXT_LABEL="meriken_time:announce" ./say_enqueue.sh "$_mt_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
                    rm -f "$_mt_file"
                } &
                ./twitch_chat.sh send "20時から21時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております 【91人対戦】ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91" 2>/dev/null &
                _run_scheduled_meriken_time_window \
                    "cycle_boundary" \
                    "[MERIKEN_TIME] サイクル区切り+20時台: メリケンAIタイム開始"
            fi
        fi

        # 次のゲーム準備
        prepare_next_game

        sleep 2
    done
}
```

#### 2. シグナル処理の再設計

**重要**: loop モードと watch モードで別々のシグナル処理を行う

**loop モードのシグナル処理**:
```bash
# 親プロセスから SIGINT/SIGTERM 無視状態を継承していると Ctrl-C が効かない。
# その場合でも確実に停止できるよう、起動直後にシグナル既定動作へ戻して再execする。
if [ -z "${SOREN_SIGRESET_DONE:-}" ]; then
    export SOREN_SIGRESET_DONE=1
    exec python3 - "$0" "$@" <<'PY'
import os
import signal
import sys

targets = {signal.SIGINT, signal.SIGTERM}
if hasattr(signal, "SIGQUIT"):
    targets.add(signal.SIGQUIT)

# 1) 無視ハンドラ継承を解除
for sig in targets:
    try:
        signal.signal(sig, signal.SIG_DFL)
    except Exception:
        pass

# 2) ブロックされたシグナルマスクも解除（Ctrl-Cが届かないケース対策）
try:
    signal.pthread_sigmask(signal.SIG_UNBLOCK, targets)
except Exception:
    pass

os.execv("/bin/bash", ["/bin/bash", sys.argv[1], *sys.argv[2:]])
PY
fi

# クリーンアップ trap
trap '_handle_stop_signal INT' INT
trap '_handle_stop_signal TERM' TERM

_handle_stop_signal() {
    local sig="${1:-INT}"
    STOP_REQUESTED=1
    rm -f tmp/stop
    log "[SIGNAL] ${sig} を受信: 停止処理に入ります"
    trap - INT TERM
    cleanup_all "signal:${sig}"
    trap - EXIT
    exit 130
}
```

**watch モードのシグナル処理**:
```bash
# カーソル非表示、終了時に復元
trap 'printf "\033[?25h\033[0m"; exit' EXIT INT TERM
```

#### 3. loop_running 状態管理の統一

`show_status.sh` 内で loop_running を一元管理:

```bash
loop_running=false
loop_pid=""

# PID管理
if [[ -f tmp/soren_loop.lock ]]; then
    loop_pid=$(cat tmp/soren_loop.lock 2>/dev/null)
    if [[ -n "$loop_pid" ]] && kill -0 "$loop_pid" 2>/dev/null; then
        loop_running=true
    else
        rm -f tmp/soren_loop.lock
    fi
fi

# メインループ起動（mode に応じて分岐）
case "${MODE:-watch}" in
    loop)
        main_loop
        ;;
    log-only)
        while true; do
            show_status_line_log
            sleep "${LOG_ONLY_INTERVAL:-10}"
        done
        ;;
    *)
        # watch モード（デフォルト）
        while true; do
            show_status | render
            if _maybe_run_fullscreen_random; then
                continue
            fi
            sleep "${WATCH_INTERVAL:-10}"
        done
        ;;
esac
```

#### 4. Workers カウントロジック

#### 3. Workers カウントロジック

```bash
workers_online=0

# loop_running
$loop_running && workers_online=$((workers_online + 1))

# improve process
if [[ -f "$TMP_STATE_DIR/improve_state.json" ]]; then
    local imp_pid=$(python3 -c "import json; print(json.load(open('$TMP_STATE_DIR/improve_state.json')).get('pid',0))" 2>/dev/null)
    if [[ -n "$imp_pid" ]] && kill -0 "$imp_pid" 2>/dev/null; then
        workers_online=$((workers_online + 1))
    fi
fi

# say
if [[ -f tmp/.say_queue/pid ]]; then
    local say_pid=$(cat tmp/.say_queue/pid 2>/dev/null)
    [[ -n "$say_pid" ]] && kill -0 "$say_pid" 2>/dev/null && workers_online=$((workers_online + 1))
fi

# twitch_chat
if [[ -f tmp/.twitch_chat/daemon.pid ]]; then
    local twitch_pid=$(cat tmp/.twitch_chat/daemon.pid 2>/dev/null)
    [[ -n "$twitch_pid" ]] && kill -0 "$twitch_pid" 2>/dev/null && workers_online=$((workers_online + 1))
fi

# comment_gen
if [[ -f tmp/.twitch_chat/comment_gen.pid ]]; then
    local cg_pid=$(cat tmp/.twitch_chat/comment_gen.pid 2>/dev/null)
    [[ -n "$cg_pid" ]] && kill -0 "$cg_pid" 2>/dev/null && workers_online=$((workers_online + 1))
fi
```

#### 5. log_line 関数

一行ログ表示用の簡易ログ関数:

```bash
log_line() {
    local level="$1"
    shift
    local message="$*"
    local timestamp=$(date '+%H:%M:%S')
    printf "[%s] [%s] %s\n" "$timestamp" "$level" "$message"
}
```

#### 6. show_status_line_log 関数

一行ログを表示:

```bash
show_status_line_log() {
    local timestamp=$(date '+%H:%M:%S')

    # CORE
    local loop_status=""
    local loop_pid=""
    if [[ -f tmp/soren_loop.lock ]]; then
        loop_pid=$(cat tmp/soren_loop.lock 2>/dev/null)
        if [[ -n "$loop_pid" ]] && kill -0 "$loop_pid" 2>/dev/null; then
            loop_status="RUNNING PID=${loop_pid}"
        else
            loop_status="STOPPED"
        fi
    fi

    local queue_info=""
    local acc_count=$(python3 -c "import json; d=json.load(open('$TMP_STATE_DIR/accumulated_games.json')); print(d.get('count',0))" 2>/dev/null || echo 0)
    local min_games=${MIN_GAMES_BEFORE_IMPROVE:-12}
    local queue_color=""
    (( acc_count > 0 )) && queue_color="[${acc_count}/${min_games}]"
    (( acc_count >= min_games )) && queue_color="${C_GREEN}${queue_color}${C_RESET}"
    queue_info="Queue: ${queue_color}"

    local workers_info=""
    local workers_online=0
    $loop_running && workers_online=$((workers_online + 1))
    if [[ -f "$TMP_STATE_DIR/improve_state.json" ]]; then
        local imp_pid=$(python3 -c "import json; print(json.load(open('$TMP_STATE_DIR/improve_state.json')).get('pid',0))" 2>/dev/null)
        [[ -n "$imp_pid" ]] && kill -0 "$imp_pid" 2>/dev/null && workers_online=$((workers_online + 1))
    fi
    workers_info="Workers: ${workers_online}/5"

    # ... 他のセクションも同様に ...

    printf "[%s] [CORE] Loop: %s | %s | %s\n" "$timestamp" "$loop_status" "$queue_info" "$workers_info"
    printf "[%s] [AUDIO] %s\n" "$timestamp" "$audio_status"
    printf "[%s] [TWITCH] %s\n" "$timestamp" "$twitch_status"
    printf "[%s] [STRATEGY] %s\n" "$timestamp" "$strategy_status"
}
```

### 改善のメリット

#### 1. 状態の一元管理
- `loop_running` などの状態を `show_status.sh` 内で一元管理
- eloop システムの監視・表示・制御を統合
- 状態の不一致を防止

#### 2. メンテナンス性の向上
- メインループとステータス表示を同じファイル内で管理
- loop 内部の変更は表示ロジックも同時に考慮しやすい
- 関数の依存関係が明確になる

#### 3. 表示の一貫性
- CORE/AUDIO/TWITCH/STRATEGY の4セクションが loop 内部の状態と完全に同期
- 一行ログ表示とフル表示が同じデータソースに基づく

#### 4. 監視の強化
- 改善中の状態をリアルタイムで監視できる
- Meriken Time の通知ロジックの重複を解消

#### 5. 運用の簡素化
- `./show_status.sh` だけでメインループとステータス表示を制御
- `./show_status.sh --log-only` で監視用のシンプルな表示が可能
- ラジオ放送との連携も一箇所で管理できる

#### 6. テストの容易さ
- フル表示と一行表示が同じコードベースでテスト可能
- UI表示ロジックを独立させることでテストしやすい

### リスクと対策

> **自己レビューの結果**: シバン不一致、シグナル処理の複雑化、stop_soren.sh の依存が重要なリスクとして明らかになった。

#### 高リスク

##### リスク1: シグナル処理の複雑化（重要）

**現象**: soren_loop.sh にシグナルリセットロジックがあるが、show_status.sh は watch モードで trap を使用しており、統合後に競合する可能性がある。

**対策**:
- loop モードと watch モードで別々のシグナル処理を行う
- loop モードでは soren_loop.sh のシグナルリセットロジックを使用
- watch モードでは現在の show_status.sh の trap を使用
- 各モードのシグナル処理を明確に分離し、干渉しないように設計

##### リスク2: stop_soren.sh の依存（重要）

**現象**: stop_soren.sh が `tmp/soren_loop.lock` に依存しており、統合後に lockfile の管理が変わると停止機能が壊れる可能性がある。

**対策**:
- lockfile の管理方法を変更しない
- `tmp/soren_loop.lock` はそのまま使用する
- stop_soren.sh の互換性を確保するために、lockfile の使用方法を明確にドキュメント化

##### リスク3: シバン不一致（重要）

**現象**: soren_loop.sh は bash、show_status.sh は zsh で、統合時にどちらにするか決める必要がある。

**対策**:
- show_status.sh のシバンを `#!/bin/bash` に統合
- eloop 系モジュールとの整合性を確保
- Zsh 固有の構文を Bash 互換に修正

#### 中リスク

##### リスク4: プロセス構造の複雑化

**現象**: 同一ファイルでループと監視の両方を実行すると、プロセス構造が複雑になる。

**対策**:
- モードを明確に分ける（watch モード、loop モード、log-only モード）
- 各モードのエントリーポイントを明確にする
- プロセス構造を図示してドキュメント化

#### 低リスク

##### リスク5: show_status.sh の肥大化

**現象**: 統合により `show_status.sh` が 1342行からさらに増加する可能性がある

**対策**:
- メインループロジックを `show_status.sh` 内に直接埋め込むのではなく、`main_loop()` 関数として独立させる
- UI描画関数を別ファイル（`show_status_ui.sh`）に分割する（将来の拡張に備える）
- 一行ログ表示ロジックを `show_status_line_log.sh` に分割する

##### リスク6: eloop_lib.sh/eloop.sh の依存関係

**現象**: メインループが `eloop.sh` の関数（play_one_game, post_game_bookkeeping等）に依存しているため、統合に難航する可能性がある

**対策**:
- eloop.sh は `source ./eloop.sh` で動的読み込み（既に実装済み）
- 統合後も eloop.sh は変更せず、関数定義のみを `show_status.sh` に移行する
- `show_status.sh` のヘッダーに eloop.sh の役割を明記する

##### リスク7: soren_loop.sh の目的が不明確になる

**現象**: `soren_loop.sh` が「親スクリプト」としての役割が不明確になる可能性がある

**対策**:
- `soren_loop.sh` を `show_status.sh` のラッパーとして残し、古い呼び出し先を一時的に維持する
- いずれ `soren_loop.sh` を廃止し、直接 `show_status.sh` を呼び出すようにする
- ソース管理上のメモを残す: `soren_loop.sh → show_status.sh に統合予定`

##### リスク8: 一行ログのフォーマット変更の影響

**現象**: 一行ログのフォーマットが変更された場合、依存するツール（監視スクリプト等）に影響を与える可能性がある

**対策**:
- 一行ログのフォーマットは保守的で、将来の変更を予期して固定する
- フォーマット仕様を別ファイル（`logs/line_log_format.txt`）にドキュメント化する
- 変更がある場合はマイグレーションスクリプトを作成する

##### リスク9: リリース管理

**現象**: 統合によって `show_status.sh` の変更が大きなリリースになる可能性がある

**対策**:
- 統合を 1つの大きな commit ではなく、複数の小さな commit に分割する
- 1つずつ commit してテストし、問題があれば早めにリバートする
- commit の粒度は:
  1. 一行ログ表示の実装（フェーズ1）
  2. シバンの統合（フェーズ2）
  3. モードの分離（フェーズ3）
  4. シグナル処理の再設計（フェーズ4）
  5. メインループの統合（フェーズ5）
  6. UI描画関数の再構築（フェーズ6）
  7. テストとマイグレーション（フェーズ9）

### 実装フェーズ（レビュー後調整版）

> **自己レビューの結果**: シバン不一致、シグナル処理の複雑化、stop_soren.sh の依存、メインループと監視の分離の曖昧さが明らかになったため、フェーズを再構成。

#### フェーズ1: 一行ログ表示の実装（優先度: 高）

- `log_line()` 関数の実装
- `show_status_line_log()` 関数の実装
- `--log-only` オプションの実装
- テストしてフォーマットを確認
- **重要**: show_status.sh は watch モードのみを維持（loop モードは追加しない）

#### フェーズ2: シバンの統合（優先度: 高）

- show_status.sh のシバンを `#!/bin/bash` に統合
- eloop 系モジュールとの整合性を確保
- Zsh 固有の構文を Bash 互換に修正

#### フェーズ3: モードの分離（優先度: 中）

```bash
# Usage:
#   ./show_status.sh          # watch モード（監視のみ）
#   ./show_status.sh --loop  # loop モード（メインループ実行）
#   ./show_status.sh --log-only  # 一行ログ表示
```

- `--loop` オプションを追加して loop モードを実装
- `--log-only` オプションを追加して一行ログ表示モードを実装
- デフォルトは watch モード（従来通り）

#### フェーズ4: シグナル処理の再設計（優先度: 中）

- loop モードと watch モードで別々のシグナル処理を行う
- loop モードでは soren_loop.sh のシグナルリセットロジックを使用:
  ```bash
  # 親プロセスから SIGINT/SIGTERM 無視状態を継承していると Ctrl-C が効かない。
  # その場合でも確実に停止できるよう、起動直後にシグナル既定動作へ戻して再execする。
  if [ -z "${SOREN_SIGRESET_DONE:-}" ]; then
      export SOREN_SIGRESET_DONE=1
      exec python3 - "$0" "$@" <<'PY'
  import os
  import signal
  import sys

  targets = {signal.SIGINT, signal.SIGTERM}
  if hasattr(signal, "SIGQUIT"):
      targets.add(signal.SIGQUIT)

  # 1) 無視ハンドラ継承を解除
  for sig in targets:
      try:
          signal.signal(sig, signal.SIG_DFL)
      except Exception:
          pass

  # 2) ブロックされたシグナルマスクも解除（Ctrl-Cが届かないケース対策）
  try:
      signal.pthread_sigmask(signal.SIG_UNBLOCK, targets)
  except Exception:
      pass

  os.execv("/bin/bash", ["/bin/bash", sys.argv[1], *sys.argv[2:]])
  PY
  fi
  ```
- watch モードでは現在の show_status.sh の trap を使用:
  ```bash
  trap 'printf "\033[?25h\033[0m"; exit' EXIT INT TERM
  ```

#### フェーズ5: loop_running 状態管理の統合（優先度: 中）

- `loop_running` 状態を `show_status.sh` 内で一元管理
- メインループを `show_status.sh` 内に移行
- `main_loop()` 関数として整理

#### フェーズ6: Workers/Queue のカウントロジックの統合（優先度: 中）

- Workers カウントロジックを `show_status.sh` 内で統合
- QueueMeter の実装
- 4セクションの表示を loop 内部の状態に基づくように修正

#### フェーズ7: Meriken Time の通知ロジックの統合（優先度: 低）

- メリケンAIタイムの通知ロジックを `show_status.sh` 内に統合
- Meriken Time の表示を CORE/AUDIO セクションに追加

#### フェーズ8: soren_loop.sh の廃止（優先度: 低）

- `soren_loop.sh` のメインループを削除
- `show_status.sh` を直接呼び出すように変更
- 古い呼び出し先を一時的に維持し、確認後削除

#### フェーズ9: テストとマイグレーション（優先度: 低）

- 完全な統合テスト
- ログファイルの検証
- 監視ツールの更新

### テスト計画

#### 単体テスト

1. **一行ログ表示**
   - `./show_status.sh --log-only` で正常に表示されるか確認
   - フォーマットが一貫しているか確認
   - 特殊な状態（loop停止、改善中等）でも正しく表示されるか確認

2. **loop_running 状態管理**
   - `show_status.sh` を実行した際、loop_running が正しく判定されるか確認
   - 並行して `soren_loop.sh` を実行した際、競合しないか確認

3. **Workers カウント**
   - Workers カウントが正しく計算されるか確認
   - 各ワーカーの状態変化に対して正しく反応するか確認

#### 統合テスト

1. **フル表示のテスト**
   - `./show_status.sh` で4セクションが正しく表示されるか確認
   - 10秒間隔で更新されるか確認
   - フルスクリーンコマンドが正常に動作するか確認

2. **メインループのテスト**
   - `./show_status.sh` でメインループが正しく動作するか確認
   - 1試合が正常にプレイされるか確認
   - 後処理が正常に実行されるか確認

3. **Meriken Time のテスト**
   - 20時台に Meriken Time が正しく発火するか確認
   - ニュース取得・再生・チャット投稿が正常に動作するか確認

#### 監視ツールのテスト

1. **監視スクリプトの更新**
   - 一行ログをパースする監視スクリプトが正常に動作するか確認
   - 状態変化を検知できるか確認

#### ログファイルの検証

1. **log_line() の出力**
   - ログファイルに正しく記録されるか確認
   - フォーマットが一貫しているか確認

### マイグレーション計画

#### 旧 soren_loop.sh の残し方

1. **ラッパーとして残す**
   ```bash
   #!/bin/bash
   # soren_loop.sh は廃止予定: show_status.sh に統合
   exec ./show_status.sh "$@"
   ```

2. **メモを残す**
   ```bash
   #!/bin/bash
   # DEPRECATED: このファイルは廃止予定です。
   # show_status.sh に統合されました。詳細は CLAUDE.md を参照してください。
   exec ./show_status.sh "$@"
   ```

#### バックアップ

- 統合前に `soren_loop.sh.bak` を作成
- 統合前に `show_status.sh.bak` を作成

### その他の考慮

#### ドキュメント

- `CLAUDE.md` に統合の概要を追加
- `docs/show_status.md` に詳細な説明を追加

#### セキュリティ

- PID管理（`tmp/soren_loop.lock`）の競合を回避
- 一行ログの長さ制限（max_width=57）

#### パフォーマンス

- `show_status_line_log()` は UI描画より軽量にする
- ファイルアクセス回数を最小限にする
- Pythonスクリプトの実行回数を最適化

#### 将来の拡張

- UI描画関数を独立させる
- テーマ切り替え機能の追加
- カスタムフィルタの追加

---

## まとめ

`soren_loop.sh` と `show_status.sh` を統合することで、eloop システムの状態管理・監視・制御を一元化し、メンテナンス性と表示の一貫性を向上させることができます。

特に、一行ログ表示モード（`--log-only`）を追加することで、監視用のシンプルな表示が可能になり、運用の簡素化が期待できます。

実装はフェーズに分けて慎重に進め、テストとマイグレーションを徹底することで、リスクを最小限に抑えることができます。
