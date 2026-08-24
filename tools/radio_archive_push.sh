#!/bin/bash
# tools/radio_archive_push.sh - ラジオ原稿のVM外退避 (soviet_now#113 / docich#10)
#
# 対象: backups/radio_scripts/<YYYYMMDD>/*.txt + .history + .meta.json (+ .mode/.voice sidecar)
# 退避先: Git鏡 (private, 既定無効) + Object Storage (rclone, 既定無効) のハイブリッド
# テキストのみ退避 (WAV/MP3は容量のため再合成で代替)
#
# 使い方:
#   ./tools/radio_archive_push.sh [--date YYYYMMDD] [--dry-run] [--now]
#   --date: 指定日のみ退避 (無指定は全日)
#   --dry-run: 副作用なし (git push / rclone copy しない)
#   --now: --dry-run を無視して即時push (cronからの手動トリガ用)
#
# 環境変数: core/config.sh の RADIO_ARCHIVE_* を参照 (ELOOP_LIB_DIR 経由で自動読込)
# 終了コード: 0=成功/スキップ, 1=引数エラー, 2=退避失敗
#
# 依存: bash, git, sha256sum/shasum, find, sort, date
# 任意: rclone / oci (RCLONE_ENABLED=1 のときのみ), gh (認証は不要だがあれば利用)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# .env と core/config.sh を読む (ELOOP_LIB_DIR は eloop_lib.sh で設定すべきだが、無ければ自前で設定)
if [ -z "${ELOOP_LIB_DIR:-}" ]; then
  export ELOOP_LIB_DIR="$SCRIPT_DIR"
fi
[ -f .env ] && set -a && . ./.env 2>/dev/null || true; set +a || true
[ -f core/config.sh ] && . core/config.sh 2>/dev/null || true

# ログヘルパ (soren_loop の log があれば使う、無ければ stderr へ)
if ! declare -F log >/dev/null 2>&1; then
  log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2; }
fi

# 引数解析
TARGET_DATE=""
DRY_RUN="${RADIO_ARCHIVE_DRY_RUN:-0}"
FORCE_NOW=0
while [ $# -gt 0 ]; do
  case "$1" in
    --date)
      TARGET_DATE="${2:?--date requires YYYYMMDD}"; shift 2
      case "$TARGET_DATE" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
        *) echo "invalid --date: $TARGET_DATE (expected YYYYMMDD)" >&2; exit 1 ;;
      esac
      ;;
    --dry-run) DRY_RUN=1; shift ;;
    --now) FORCE_NOW=1; DRY_RUN=0; shift ;;
    --help|-h) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done
# --now が指定されれば DRY_RUN を強制解除 (上位の --dry-run を上書き)
if [ "$FORCE_NOW" -eq 1 ]; then
  DRY_RUN=0
fi

# 有効無効チェック
if [ "${RADIO_ARCHIVE_ENABLED:-1}" != "1" ]; then
  log "[RADIO_ARCHIVE] disabled (RADIO_ARCHIVE_ENABLED != 1) -> skip"
  exit 0
fi

BACKUP_ROOT="backups/radio_scripts"
if [ ! -d "$BACKUP_ROOT" ]; then
  log "[RADIO_ARCHIVE] no backup root: $BACKUP_ROOT -> skip (nothing to archive)"
  exit 0
fi

# 対象ファイル列挙
collect_files() {
  local date_filter="$1"
  if [ -n "$date_filter" ]; then
    find "$BACKUP_ROOT/$date_filter" -type f \( -name '*.txt' -o -name '*.history' -o -name '*.meta.json' -o -name '*.mode' -o -name '*.voice' \) 2>/dev/null | sort
  else
    find "$BACKUP_ROOT" -type f \( -name '*.txt' -o -name '*.history' -o -name '*.meta.json' -o -name '*.mode' -o -name '*.voice' \) 2>/dev/null | sort
  fi
}

FILES=$(collect_files "$TARGET_DATE" || true)
FILE_COUNT=$(printf '%s\n' "$FILES" | grep -c . || true)
if [ "$FILE_COUNT" -eq 0 ] || [ -z "$FILES" ]; then
  log "[RADIO_ARCHIVE] no files to archive (date=${TARGET_DATE:-all}) -> skip"
  exit 0
fi
log "[RADIO_ARCHIVE] found $FILE_COUNT files (date=${TARGET_DATE:-all})"

# Git鏡が無効かつ rclone も無効なら dry-run 相当で終了 (副作用なし)
GIT_ENABLED="${RADIO_ARCHIVE_GIT_ENABLED:-1}"
RCLONE_ENABLED="${RADIO_ARCHIVE_RCLONE_ENABLED:-0}"
GIT_REPO="${RADIO_ARCHIVE_GIT_REPO:-}"
RCLONE_REMOTE="${RADIO_ARCHIVE_RCLONE_REMOTE:-}"
if [ "$GIT_ENABLED" != "1" ] || [ -z "$GIT_REPO" ]; then
  GIT_ENABLED=0
fi
if [ "$RCLONE_ENABLED" != "1" ] || [ -z "$RCLONE_REMOTE" ]; then
  RCLONE_ENABLED=0
fi
if [ "$GIT_ENABLED" -eq 0 ] && [ "$RCLONE_ENABLED" -eq 0 ]; then
  log "[RADIO_ARCHIVE] both git and rclone disabled (GIT_REPO='$GIT_REPO' RCLONE_REMOTE='$RCLONE_REMOTE') -> dry-run mode (no push)"
  if [ "$DRY_RUN" -eq 1 ]; then
    log "[RADIO_ARCHIVE] dry-run: would archive $FILE_COUNT files"
    printf '%s\n' "$FILES" | head -n 20 | while read -r f; do log "  $f"; done
    [ "$FILE_COUNT" -gt 20 ] && log "  ... and $((FILE_COUNT - 20)) more"
  else
    log "[RADIO_ARCHIVE] no destination configured -> skip (set RADIO_ARCHIVE_GIT_REPO or RADIO_ARCHIVE_RCLONE_REMOTE)"
  fi
  exit 0
fi

# 誤爆ガード: publicな soviet_now の main へ backups/ を push しない
if printf '%s' "$GIT_REPO" | grep -q "soviet_now" && [ "${RADIO_ARCHIVE_GIT_BRANCH:-main}" = "main" ]; then
  # soviet_now の main は public。radio-archive は別ブランチか別リポジトリへ。
  # ただし RADIO_ARCHIVE_GIT_REPO が明示的に soviet_now で branch が radio-archive なら許容
  if [ "${RADIO_ARCHIVE_GIT_BRANCH}" = "main" ]; then
    log "[RADIO_ARCHIVE] guard: refusing to push backups/ to soviet_now main (public). Set RADIO_ARCHIVE_GIT_BRANCH=radio-archive or use private repo" >&2
    exit 2
  fi
fi

OVERALL_RC=0

# --- Git鏡 push ---
if [ "$GIT_ENABLED" -eq 1 ]; then
  GIT_BRANCH="${RADIO_ARCHIVE_GIT_BRANCH:-main}"
  GIT_DIR_TMP=$(mktemp -d /tmp/radio_archive_git_XXXXXX)
  trap 'rm -rf "$GIT_DIR_TMP"' EXIT
  log "[RADIO_ARCHIVE][git] clone $GIT_REPO (branch=$GIT_BRANCH) -> $GIT_DIR_TMP"
  # clone 試行: まず目的ブランチ、失敗したらデフォルトブランチ、最後は空リポジトリとして init
  if ! git clone --depth 1 --branch "$GIT_BRANCH" "$GIT_REPO" "$GIT_DIR_TMP" 2>/dev/null; then
    log "[RADIO_ARCHIVE][git] branch $GIT_BRANCH not found, trying default branch"
    rm -rf "$GIT_DIR_TMP"
    mkdir -p "$GIT_DIR_TMP"
    if ! git clone --depth 1 "$GIT_REPO" "$GIT_DIR_TMP" 2>/dev/null; then
      log "[RADIO_ARCHIVE][git] clone failed, initializing empty repo"
      rm -rf "$GIT_DIR_TMP"
      mkdir -p "$GIT_DIR_TMP"
      git -C "$GIT_DIR_TMP" init -q
      git -C "$GIT_DIR_TMP" remote add origin "$GIT_REPO"
    fi
    # 目的ブランチを作成/切替
    if ! git -C "$GIT_DIR_TMP" checkout -B "$GIT_BRANCH" 2>/dev/null; then
      git -C "$GIT_DIR_TMP" checkout --orphan "$GIT_BRANCH"
      git -C "$GIT_DIR_TMP" rm -rf . 2>/dev/null || true
    fi
  fi

  # backups をミラー
  mkdir -p "$GIT_DIR_TMP/backups/radio_scripts"
  # rsync があれば差分を高速に、無ければ cp -a
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$BACKUP_ROOT"/ "$GIT_DIR_TMP/backups/radio_scripts/" 2>/dev/null || cp -a "$BACKUP_ROOT"/. "$GIT_DIR_TMP/backups/radio_scripts/" 2>/dev/null || true
  else
    # 対象が --date 絞りのときは該当ディレクトリのみ、それ以外は全体
    if [ -n "$TARGET_DATE" ]; then
      mkdir -p "$GIT_DIR_TMP/backups/radio_scripts/$TARGET_DATE"
      cp -a "$BACKUP_ROOT/$TARGET_DATE"/. "$GIT_DIR_TMP/backups/radio_scripts/$TARGET_DATE/" 2>/dev/null || true
    else
      cp -a "$BACKUP_ROOT"/. "$GIT_DIR_TMP/backups/radio_scripts/" 2>/dev/null || true
    fi
  fi

  # 変更有無チェック
  git -C "$GIT_DIR_TMP" add -A backups/radio_scripts/ 2>/dev/null || true
  if git -C "$GIT_DIR_TMP" diff --cached --quiet 2>/dev/null; then
    log "[RADIO_ARCHIVE][git] no changes to push (already up to date)"
  else
    CHANGED_COUNT=$(git -C "$GIT_DIR_TMP" diff --cached --name-only | wc -l | tr -d ' ')
    log "[RADIO_ARCHIVE][git] $CHANGED_COUNT files changed, committing"
    if [ "$DRY_RUN" -eq 1 ]; then
      log "[RADIO_ARCHIVE][git] dry-run: would commit and push to $GIT_REPO $GIT_BRANCH"
      git -C "$GIT_DIR_TMP" diff --cached --stat 2>/dev/null | head -n 20 | while read -r line; do log "  $line"; done
    else
      # コミット
      DATE_LABEL="${TARGET_DATE:-$(date +%Y%m%d)}"
      COMMIT_MSG="radio: $DATE_LABEL $CHANGED_COUNT scripts

Automated archive from $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)
Source: backups/radio_scripts/${TARGET_DATE:-all}
Pushed by tools/radio_archive_push.sh
"
      if ! git -C "$GIT_DIR_TMP" -c user.name="soren-archive" -c user.email="archive@soren.local" commit -m "$COMMIT_MSG" 2>&1 | cat; then
        log "[RADIO_ARCHIVE][git] commit failed" >&2
        OVERALL_RC=2
      else
        # push (リトライ3回)
        PUSHED=0
        for attempt in 1 2 3; do
          if git -C "$GIT_DIR_TMP" push origin "$GIT_BRANCH" 2>&1 | cat; then
            PUSHED=1
            log "[RADIO_ARCHIVE][git] push succeeded (attempt $attempt)"
            break
          else
            log "[RADIO_ARCHIVE][git] push failed (attempt $attempt/3), retrying in $((attempt * 10))s" >&2
            sleep $((attempt * 10))
            # pull --rebase して再試行 (競合時のみ)
            git -C "$GIT_DIR_TMP" pull --rebase origin "$GIT_BRANCH" 2>/dev/null || true
          fi
        done
        if [ "$PUSHED" -eq 0 ]; then
          log "[RADIO_ARCHIVE][git] push failed after 3 attempts" >&2
          OVERALL_RC=2
        fi
      fi
    fi
  fi
  rm -rf "$GIT_DIR_TMP" 2>/dev/null || true
  trap - EXIT
fi

# --- rclone / oci push ---
if [ "$RCLONE_ENABLED" -eq 1 ]; then
  RCLONE_BUCKET="${RADIO_ARCHIVE_RCLONE_BUCKET:-soren-radio-archive}"
  # rclone が無ければ oci cli を試す
  if command -v rclone >/dev/null 2>&1; then
    RCLONE_CMD="rclone"
  elif command -v oci >/dev/null 2>&1; then
    RCLONE_CMD="oci"
  else
    log "[RADIO_ARCHIVE][rclone] rclone/oci not found, skipping rclone push" >&2
    RCLONE_CMD=""
  fi
  if [ -n "$RCLONE_CMD" ]; then
    if [ "$RCLONE_CMD" = "rclone" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        log "[RADIO_ARCHIVE][rclone] dry-run: would copy $FILE_COUNT files to $RCLONE_REMOTE:$RCLONE_BUCKET/radio_scripts/"
        # rclone の --dry-run 相当をログ
        log "[RADIO_ARCHIVE][rclone] rclone copy $BACKUP_ROOT $RCLONE_REMOTE:$RCLONE_BUCKET/radio_scripts/ --dry-run"
      else
        log "[RADIO_ARCHIVE][rclone] copying to $RCLONE_REMOTE:$RCLONE_BUCKET/radio_scripts/"
        if ! rclone copy "$BACKUP_ROOT" "$RCLONE_REMOTE:$RCLONE_BUCKET/radio_scripts/" --progress 2>&1 | cat; then
          log "[RADIO_ARCHIVE][rclone] copy failed" >&2
          OVERALL_RC=2
        else
          log "[RADIO_ARCHIVE][rclone] copy succeeded"
        fi
      fi
    else
      # oci cli: bulk-upload (要 compartment と bucket が事前作成済み)
      if [ "$DRY_RUN" -eq 1 ]; then
        log "[RADIO_ARCHIVE][oci] dry-run: would bulk-upload $BACKUP_ROOT to $RCLONE_BUCKET"
      else
        log "[RADIO_ARCHIVE][oci] bulk-upload to $RCLONE_BUCKET"
        if ! oci os object bulk-upload --bucket-name "$RCLONE_BUCKET" --src-dir "$BACKUP_ROOT" --object-prefix "radio_scripts/" 2>&1 | cat; then
          log "[RADIO_ARCHIVE][oci] bulk-upload failed" >&2
          OVERALL_RC=2
        else
          log "[RADIO_ARCHIVE][oci] bulk-upload succeeded"
        fi
      fi
    fi
  fi
fi

# 状態ファイル更新 (成功時のみ)
if [ "$OVERALL_RC" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  STATE_FILE="${RADIO_ARCHIVE_STATE_FILE:-tmp/state/radio_archive_pushed.json}"
  mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true
  # 簡易: 最終push時刻とファイル数を記録 (shaまでは持たず git/rclone に任せる)
  python3 - "$STATE_FILE" "$FILE_COUNT" "$TARGET_DATE" <<'PY' 2>/dev/null || true
import json, sys, time
from pathlib import Path
state_file = Path(sys.argv[1])
count = int(sys.argv[2]) if sys.argv[2].isdigit() else 0
date_label = sys.argv[3] if len(sys.argv) > 3 else ""
try:
    data = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
except:
    data = {}
data["version"] = 1
data["updated_at"] = int(time.time())
data["last_push_at"] = data["updated_at"]
data["last_file_count"] = count
data["last_date"] = date_label
data["last_status"] = "ok"
state_file.parent.mkdir(parents=True, exist_ok=True)
state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  log "[RADIO_ARCHIVE] state updated: $STATE_FILE"
fi

if [ "$OVERALL_RC" -eq 0 ]; then
  log "[RADIO_ARCHIVE] done (files=$FILE_COUNT, dry_run=$DRY_RUN, git=$GIT_ENABLED, rclone=$RCLONE_ENABLED)"
else
  log "[RADIO_ARCHIVE] failed (rc=$OVERALL_RC)" >&2
fi
exit "$OVERALL_RC"
