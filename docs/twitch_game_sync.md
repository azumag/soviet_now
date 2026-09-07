# Twitch 配信カテゴリー・タイトル連動の運用

`update_stream_game.sh` がゲーム切替時の Twitch 更新を受け持つ。
ゲーム切替本体 (docich switch / lifecycle broker) の自動フックは、
切替整備が一段落するまで未接続。切替時は手動で実行する。

```bash
./update_stream_game.sh --game robots --strategy "root v763 継続"
./update_stream_game.sh --game robots --dry-run   # 確認のみ
```

## ゲームとカテゴリーの対応表 (IGDB 照合済み)

Twitch のカテゴリーは IGDB が正本。`--resolve` で候補を引き、
`--verify` で toml と Twitch 実登録の一致を確認してから決める。

| game (`config/games/<id>.toml`) | Twitch category_id | Twitch 正式名 | title_prefix |
|---|---|---|---|
| sorengame | 1530787860 | Soren Game | `[Soren]` |
| robots | 11585 | Robots | `[Robots]` |
| nethack | 130 | NetHack | `[NetHack]` |
| hanjuku-hero | 21236 | Hanjuku Hero: Aa Sekai yo Hanjuku Nare...!! | `[半熟英雄]` |

- hanjuku-hero は SFC 初代 (副題 `Aa Sekai yo Hanjuku Nare...!!` 付き) を採用。
  汎用 `Hanjuku Hero` (26146) ではない。
- robots (BSD Robots) に完全一致の IGDB 項目はなく、最も近い `Robots` (11585) で代替。
- sorengame は既存のカスタムカテゴリー `Soren Game` (本番で使用中) を維持。

照合実測 (2026-09-05, `search/categories` + `games`):

```bash
./update_stream_game.sh --resolve "Hanjuku Hero"
./update_stream_game.sh --verify --game hanjuku-hero --games-dir ../../config/games
```

## ゲーム追加時の初期設定 (必須チェックリスト)

ゲームを増やすたびに以下を行う。`[twitch]` なしのゲーム追加は不可
(docich `tests/test_twitch_game_config.py` が CI で検出する)。

1. `./update_stream_game.sh --resolve "<ゲーム名>"` で IGDB 候補を列挙。
2. 実ゲームに最も近い候補を選び、正式名を一字一句メモする。
   完全一致がなければ近い既存カテゴリーで代替し、その旨を PR に書く。
3. `config/games/<id>.toml` に `[twitch]` を追加:
   ```toml
   [twitch]
   category_id = "<数字ID>"
   category_name = "<Twitch正式名>"
   title_prefix = "[<短名>]"
   ```
4. `./update_stream_game.sh --verify --game <id>` で一致を確認 (不一致は exit 5)。
5. 切替時に `--dry-run` で目標タイトルを確認してから本実行。

## タイトル形式

`{prefix} day{N} {activity} {strategy}` (Twitch 上限 140字、超過分は末尾から `…` 短縮)。

- `day{N>`: 既存 `update_stream_title_day.sh` と同じ基準日 (`STREAM_DAY_EPOCH`
  既定 2026-03-14) で算出。ゲームが変わっても通算を維持する。
- `activity` 既定: `prompts/ops_brief.md` の1件目 (= handoff 最新節の要約。
  handoff 更新 → `tools/build_ops_brief.sh` → 配布の流れに準じる)。
  `--activity ""` で省略可。
- `strategy` 既定: `$STREAM_GAME_STRATEGY` / `--strategy`。戦略の進捗
  (例: `root v763 継続`) を短文で乗せる。省略可。
- 取得済み title+game_id と同一なら PATCH しない (冪等)。`--force` で強制。
- `--title-only` はカテゴリーを触らずタイトルだけ更新
  (handoff 更新に伴う文言リフレッシュ用)。

## トークン

優先順: `TWITCH_GAME_TOKEN` > `TWITCH_TITLE_TOKEN` > `TWITCH_BOT_TOKEN` >
`TWITCH_PREDICTIONS_TOKEN`。PATCH には `channel:manage:broadcast` 必須。

実測 (2026-09-05): `PREDICTIONS` は polls/predictions のみで broadcast なし、
旧 `TITLE` は失効 (401)。`BOT` は broadcast 付き。
本番で PATCH が `exit 3` になる場合は broadcast 付きトークンを
`TWITCH_GAME_TOKEN` に設定する (中身はリポジトリに書かない)。
`--verify` / `--resolve` は読取のみで scope 不要。

## 自動フック (未接続・予定)

接続先: 切替成功の直後。

- 現行 CLI: `docich switch` 成功 (`state.set_current_game()` 完了) の直後。
- 将来: lifecycle broker の finish/parked 確定の直後。
- 呼び出し例: `update_stream_game.sh --game <name> --strategy "<進捗>"`
  (失敗しても切替自体は成功扱い。タイトル・カテゴリー更新の失敗で
  ゲームを巻き戻さないこと)。

`update_stream_title_day.sh` (毎日の day N 更新) は当面残す。
両者の差: day スクリプトはタイトルの数値部だけ置換しカテゴリーに触らない。
game スクリプトは切替時の prefix+activity+strategy+カテゴリー更新を受け持つ。
