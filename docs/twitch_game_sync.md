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

公開タイトルは `[dayN] {viewer activity} {viewer strategy?}` とする
(Twitch 上限140字)。PR番号やmain/VM/CI、コミットSHAなどの内部運用ログは
タイトル本文の情報源にしない。

- `dayN`: `update_stream_title_day.sh` と同じ基準日
  (`STREAM_DAY_EPOCH`、既定 2026-03-14) で算出する。
- `viewer activity` の既定: `prompts/viewer_title.md` の明示候補。
  このファイルは `tools/build_ops_brief.sh` がdocichの正本handoff最新節から生成する。
  handoffには、変更内容と確認段階に合った一般向け文面を次のように明示する。

  ```markdown
  ## 2026-09-20 ... — 内部向けの作業見出し
  - 視聴者向けタイトル: AIの取引コーナーからゲームへ、画面切替を改善
  ```

  `viewer_title:` も同義で使える。最新節に明示が無ければ古い節の候補を再利用しない。
- `prompts/ops_brief.md` はコメント返し等の内部運用メモとして残すが、
  Twitchタイトルの入力には使わない。
- 日次更新では、明示候補が無ければ現在のタイトル本文が一般向けかを確認して維持する。
  現タイトルもPR/Issue番号、main/VM/CI、SHA、merge/deploy等の内部ログなら、
  `STREAM_TITLE_PUBLIC_FALLBACK`（既定:
  `AIたちがゲーム・ニュース・会話に挑戦する実験配信`）へ置き換える。
- ゲーム切替等でタイトル本文を新規組成するときは、明示候補が無ければ同じfallbackを使う。
  `--activity` / `--strategy` も視聴者向け文面として検証し、内部識別子主体なら公開しない。
- 視聴者に意味のある固有名詞（例: NetHack、Jev）は保持する。
  「PR770をマージ」のような内部識別子だけから変更内容を推測してタイトルを捏造しない。
- `--category-only` はカテゴリーだけを同期し、現在のタイトルをそのまま保持する。
- 取得済みtitle+game_idと同一ならPATCHしない（冪等）。`--force` で強制。
- `--title-only` はカテゴリーを触らず、同じ公開タイトル契約で本文だけ更新する。

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
