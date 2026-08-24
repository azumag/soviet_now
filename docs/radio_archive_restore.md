# Radio Archive 復元手順 (soviet_now#113)

> 対象: `backups/radio_scripts/<YYYYMMDD>/*.txt` + `*.history` + `*.meta.json` (+ `.mode`/`.voice` sidecar)
> 退避先: Git鏡 (`azumag/soren-radio-archive`, branch `main` or `radio-archive`) + Object Storage (`soren-radio-archive` bucket) のハイブリッド
> 生成元: `broadcast/radio_state.sh:154 _radio_backup_script` が再生完了時に `backups/radio_scripts/<YYYYMMDD>/` へ保存

## 前提

- 本番 `/home/ubuntu/soren` は git 管理外。VM の `gh` は device flow 済み。
- ローカル `games/soviet_now` は `azumag/soviet_now` の submodule (`main` 追跡)。
- 退避は `tools/radio_archive_push.sh` が 04:00 JST に `systemd timer` で実行。テキストのみ退避 (WAV/MP3は再合成で代替)。

## 1. Git鏡からの復元

### 1.1 最新の全量を clone

```bash
# private リポジトリのため gh 認証済みであること
gh auth status
git clone git@github.com:azumag/soren-radio-archive.git /tmp/soren-radio-archive
# または https
git clone https://github.com/azumag/soren-radio-archive.git /tmp/soren-radio-archive
ls /tmp/soren-radio-archive/backups/radio_scripts/ | head
```

### 1.2 特定日付のみ復元

```bash
DATE=20260824
git -C /tmp/soren-radio-archive log --oneline -- backups/radio_scripts/$DATE | head
# VM へ戻す
rsync -av /tmp/soren-radio-archive/backups/radio_scripts/$DATE/ /home/ubuntu/soren/backups/radio_scripts/$DATE/
# ローカルへ戻す
rsync -av /tmp/soren-radio-archive/backups/radio_scripts/$DATE/ ./backups/radio_scripts/$DATE/
```

### 1.3 差分のみ復元 (VM 側で不足分を補う)

```bash
# VM 上で
./tools/radio_archive_push.sh --dry-run  # 不足があればログに出る
# 手動で特定日を再退避したいときは --date を使う (逆方向ではなく、VM から Git へ再 push)
./tools/radio_archive_push.sh --date 20260824 --now
```

## 2. Object Storage からの復元

### 2.1 rclone 経由

```bash
# 事前に rclone config で oci: が設定済みであること
rclone ls oci:soren-radio-archive/radio_scripts/ | head
rclone copy oci:soren-radio-archive/radio_scripts/20260824 ./backups/radio_scripts/20260824 --progress
# VM へ
rclone copy oci:soren-radio-archive/radio_scripts/20260824 /home/ubuntu/soren/backups/radio_scripts/20260824 --progress
```

### 2.2 oci cli 経由

```bash
oci os object list --bucket-name soren-radio-archive --prefix radio_scripts/20260824/ | head
oci os object bulk-download --bucket-name soren-radio-archive --download-dir ./backups --prefix radio_scripts/20260824/
```

## 3. VM 障害時の初回セットアップ

```bash
# 新VMで soren を展開後、退避先からリストア
mkdir -p /home/ubuntu/soren/backups/radio_scripts
# Git鏡が正の場合
git clone --depth 1 --branch main git@github.com:azumag/soren-radio-archive.git /tmp/restore
rsync -av /tmp/restore/backups/radio_scripts/ /home/ubuntu/soren/backups/radio_scripts/
# または Object Storage が正の場合
rclone copy oci:soren-radio-archive/radio_scripts/ /home/ubuntu/soren/backups/radio_scripts/ --progress
ls /home/ubuntu/soren/backups/radio_scripts/ | wc -l
```

## 4. Podcast / Short 動画化での利用

```bash
# Podcast は退避バケットを入力として再構成する (docich#10)
./tools/podcast_build.py --date 20260824 --dry-run  # 入力: backups/radio_scripts/20260824/news+jiji
./tools/short_video_build.py --pick-one --no-upload --dry-run  # 単発 news
# 退避が無い日は podcast_build がスキップする
```

## 5. トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| `radio_archive_push.sh: no files to archive` | `backups/radio_scripts/` が空 | 正常。再生完了後にのみファイルができる。`backups/radio_scripts/<today>` を確認 |
| `guard: refusing to push backups/ to soviet_now main` | `RADIO_ARCHIVE_GIT_REPO` が public の `main` を指している | `RADIO_ARCHIVE_GIT_REPO=git@github.com:azumag/soren-radio-archive.git` と `RADIO_ARCHIVE_GIT_BRANCH=main` に修正 |
| `rclone: not found` | rclone 未導入 | `RADIO_ARCHIVE_RCLONE_ENABLED=0` にして Git のみに倒すか、`sudo apt install rclone` |
| `git push failed after 3 attempts` | 競合 or 認証失敗 | `gh auth status` / `ssh -T git@github.com` を確認。手動で `git -C /tmp/radio_archive_git_* pull --rebase` して再試行 |
| `permission denied` | private リポジトリへの権限なし | `gh auth login` or `ssh-add` で再認証 |

## 6. 運用メモ

- 退避は 04:00 JST に `systemd timer` (`deploy/radio-archive/radio-archive.timer`) で実行。手動は `./tools/radio_archive_push.sh --now`。
- 状態は `tmp/state/radio_archive_pushed.json` に最終 push 時刻と件数を記録。`show_status.sh` には将来 `archive_last_push` を表示予定。
- Git鏡は 90日以前を月次タグ `archive-YYYYMM` に squash して軽量化する運用を将来導入 (現時点は無期限)。
- WAV/MP3 は退避対象外。必要なら `rclone copy tmp/.radio_deferred_queue/*.ready.wav` を手動で実行。

## 参照

- `core/config.sh: RADIO_ARCHIVE_*`
- `tools/radio_archive_push.sh --help`
- `deploy/radio-archive/radio-archive.{service,timer}`
- `deploy/hooks/pre-push`
- `docs/radio_archive_adr.md`
- `soviet_now#113` / `docich#10`
