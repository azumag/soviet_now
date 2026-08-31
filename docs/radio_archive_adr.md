# Radio Archive ADR (soviet_now#113 / docich#10)

> 決定日: 2026-08-24 / 対象: `backups/radio_scripts/<YYYYMMDD>/*.txt + .history + .meta.json` のVM外永続化
> 前提: `broadcast/radio_state.sh:154 _radio_backup_script` が再生完了時にローカルへ保存する仕組みは 2026-08-17 に導入済み (PR #112)

## 背景と課題

- 現状はVMローカル (`/home/ubuntu/soren/backups/radio_scripts/`) のみに蓄積。VM障害・誤削除で消失する。
- `docich#10` のポッドキャスト/ショート動画化は、過去原稿を入力素材として再利用する前提。退避が未解決だと素材が失われる。
- VMはgit管理外 (`/home/ubuntu/soren` はtar展開物) で、`soviet_now` 本体はpublicリポジトリ。誤ってpublicへ機密をpushしないガードが必要。

## 決定 (推奨構成)

### 退避先: ハイブリッド (Git鏡 + Object Storage)

| 用途 | 先 | 理由 |
|---|---|---|
| **テキスト原稿** (`*.txt`/`*.history`/`*.meta.json`) | **Git鏡 (private) + Object Storage (private bucket)** | テキストは年10MB (20本/日×2KB×365) でGitでも持つ。Gitは履歴追跡・レビュー性、Object Storageは無制限・バイナリ将来対応 |
| **WAV/MP3** | **退避しない (再合成で代替)** | 1本5MB×20本×365=36GB/年でGitは即超過、Object Storageでもコスト。Podcastは原稿から `voicevox_tts.sh` で再合成すれば品質十分。WAVが必要な日だけ手動 `rclone copy` |

Git鏡の置き場: **新規 private リポジトリ `azumag/soren-radio-archive`** を推奨。`azumag/soviet_now` の orphan branch `radio-archive` も技術的には可能だが、publicリポジトリを汚し、誤って `main` へ混入するリスクがあるため非推奨。`azumag/docich` もpublicのため同様。

Object Storage: `oci:soren-radio-archive` (Oracle Cloud, 東京リージョン) の private bucket `soren-radio-archive`。`rclone` または `oci os object bulk-upload` で `soren-radio-archive/<YYYYMMDD>/` へミラー。将来podcastのMP3も同bucketの `podcast/` 配下 (public-read) に置くが、原稿は private のまま分離する。

### 運用ルール

- **頻度:** 1日1回 04:00 JST (JSTは `RADIO_ARCHIVE_PUSH_HOUR_JST=04:00`)。配信低トラフィック帯。失敗時は30分後リトライ、3回まで exponential backoff。
- **重複排除:** `sha256sum` + `tmp/state/radio_archive_pushed.json` (前回push済みファイルの `path → sha256` マップ) で差分のみpush。既存 `_radio_text_hash` と同思想。
- **保持:** Object Storageは無期限 (ライフサイクルで1年後に Glacier可)。Git鏡は `main` に日次コミット `radio: <YYYYMMDD> N scripts`、90日以前は月次タグ `archive-YYYYMM` にsquashして履歴軽量化 (将来)。
- **機密:** 原稿に個人チャットは含まれないが、将来の `comment` 混入に備え常にprivate。public誤爆ガードとして `pre-push` hook で `origin` が `azumag/soviet_now` `main` への `backups/` pushを拒否、かつ `RADIO_ARCHIVE_GIT_REPO` が空ならpushをスキップ (fail-open)。
- **冪等性:** 同じ `YYYYMMDD` を何度pushしても `git add` は差分のみ、Object Storageは `rclone copy` の上書きで冪等。

### 実装場所

- `soviet_now` 側が主: `core/config.sh` に `RADIO_ARCHIVE_*` 定数、`tools/radio_archive_push.sh` 本体、`deploy/radio-archive.{service,timer}` (systemd user timer)。
- `docich` 側は将来 `src/docich/podcast.py` で退避バケットを入力として再利用。退避自体は `soviet_now` が所有。

## 検討した代替案

| 候補 | 不採用理由 |
|---|---|
| sovet_now publicへ直接コミット | 本流汚染・誤爆リスク |
| Gist | API制限・履歴管理弱い |
| 別ホスト rsync | 単一障害点・運用コスト |
| 毎時push | Git履歴汚染・APIレート |

## 参照

- `broadcast/radio_state.sh:154 _radio_backup_script` / `core/config.sh:37 AI_COMMON_AGENTS`
- `soviet_now#113` / `docich#10` issue本文
- `plan` サブエージェント (opus) による `docich#10` 実装計画 (2026-08-24)

## 次のステップ

- [x] config定数追加 (`core/config.sh`)
- [ ] `tools/radio_archive_push.sh` 実装
- [ ] `deploy/radio-archive.{service,timer}` 作成
- [ ] `docs/radio_archive_restore.md` (復元手順) 作成
- [ ] VM反映・2日間dry-run観測で `soviet_now#113` close
