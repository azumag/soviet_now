# Projection live-apply gate runbook (docich#2487 / soviet_now#707)

## 目的

本番 runtime `/home/ubuntu/soren` は reviewed `games/soviet_now` gitlink の
**git 管理外 projection** であり、docich deploy plane は tracked path の
live bytes+mode が reviewed lineage と一致しない限り fail-closed する
(`vm-operations.yml` の "Reconcile reviewed pre-synced Soren checkout"、docich#279)。
2026-10-17〜23 の 5 連続 deploy 失敗 (docich#2482) は、improve loop が未 review の
候補 (v761/v764) を live `strategy.py` へ直接書いたことが原因だった。

`core/strategy_runtime.sh` の **projection live-apply gate** は、structed writer
(improve adopt / AB gate adopt / repair / revert) が共有する choke point で
「reviewed lineage に無い bytes を live へ書かない」ことを強制する。

## ポリシー (`IMPROVE_LIVE_APPLY_POLICY`)

base gate (`core/strategy_runtime.sh`) の値:

| 値 | 挙動 |
|---|---|
| `dry_run` | 拒否判定をログ + マーカーに残すだけで apply は通す (base runtime の既定。dev checkout / 単体テスト向け) |
| `enforce_reviewed` | reviewed lineage (`origin/main` → `main`) に無い bytes の live apply を**拒否**。候補は `tmp/state/projection_guard/<UTC>_<pid>/` に隔離 |
| `off` | gate 無効 (escape hatch。dev checkout 限定) |

### production projection の強制層 (`core/strategy_runtime_strict.sh`)

`soviet_now#717` 以降、`eloop_lib.sh` は base runtime の直後に strict layer を
source する。**本番の git-less projection では判定不能を許可にしない**:

- 有効ポリシーは常に `enforce_reviewed`。`.env` に残った `dry_run` / `off` は
  `SOREN_UNSAFE_PROJECTION_APPLY_ACK=1` を併記した明示的な二重 opt-in が無い限り
  `enforce_reviewed` に昇格する。
- `SOREN_PROJECTION_FORCE=0` だけでは本番 projection の guard を無効化できない。
- lineage clone 不在 / reviewed ref 不在 / target root 解決不能 / 想定外 verdict は
  `projection_guard_refused` として **fail-closed** (隔離 + マーカー)。
- 回帰: `tests/test_projection_guard_strict.py`、CI は isolated-apply。

- projection 検出: runtime root に `.git` が無いこと (`strategy/persist.sh` の前提)。
  strict layer は `SOREN_PROJECTION_FORCE=1` を尊重しつつ `=0` では解除しない。
- reviewed 参照: `SOREN_LINEAGE_REF_REPO` (既定 `SOREN_PERSIST_REPO` →
  `/home/ubuntu/soren-persist`)、ref は `SOREN_LINEAGE_REF` (既定 `origin/main` →
  `main`)。gate は fetch しない。persist clone の `origin/main` が古い場合は
  reviewed 済み候補を一時的に拒否しうる (次回 persist で解消)。

## 拒否された候補の upstream 手順

1. 隔離 bytes を確認する:
   ```bash
   ls -l /home/ubuntu/soren/tmp/state/projection_guard/
   sha256sum /home/ubuntu/soren/tmp/state/projection_guard/<id>/strategy.py
   cat /home/ubuntu/soren/tmp/state/projection_guard_last_event.json
   ```
2. **byte-exact** で soviet_now へ PR する (docich#2482 の #706 と同じ手順)。
   1 byte でも違うと reconcile の sha256 照合を通らない。
   - 元の mode は reviewed mode (`100755`) に合わせる (`git update-index --chmod=+x` 等)。
   - 隔離 dir の `strategy_helpers/` がある場合はそれも同じ PR に含める。
3. PR → CI green → main merge → docich `games/soviet_now` gitlink advance →
   production deploy。以降の improve は reviewed bytes を起点に再開する。

## 本番 (production projection) への適用

strict layer により、本番 projection では **.env の設定なしで enforce_reviewed** が
効く (apply 時に評価される)。導入・確認手順:

```bash
# 1) strict layer が本番に入っていることを確認 (deploy 済み SHA で)
grep -n 'strategy_runtime_strict' /home/ubuntu/soren/eloop_lib.sh
# 2) 拒否ログとマーカーを確認 (拒否が起きていない場合はマーカー未生成)
grep -h 'PROJECTION-GUARD' /home/ubuntu/soren/logs/*.log | tail -50
cat /home/ubuntu/soren/tmp/state/projection_guard_last_event.json
```

- `.env` に以前の段階導入値 (`dry_run` / `off`) が残っていても、strict layer が
  `enforce_reviewed` へ昇格させるため **worker 再起動は必須ではない**。明示的に
  安全側で揃えたい場合は `.env` から該当行を削除する (次回起動時に読まれる)。
- `enforce_reviewed` では live 反映が PR merge + gitlink advance の律速になる。
  candidate 生成・sandbox 検証・persist PR は従来どおり継続する。
- dry_run 観測が必要な場合 (dev checkout / 明示調査) は git-less projection では
  `SOREN_UNSAFE_PROJECTION_APPLY_ACK=1` を併用する。本番の常用設定にしない。

## マーカーとログ

| 出力 | 内容 |
|---|---|
| `tmp/state/projection_guard_last_event.json` | 直近の gate イベント (`would_refuse` / `refused` / `gate_degraded`) と理由・隔離 dir |
| `tmp/state/strategy_apply_last_failure.json` | 拒否時は `failure_code=projection_guard_refused` (apply 失敗の既存マーカーと同じ schema) |
| `tmp/state/improve_last_failure.json` | 端末失敗として `failed_no_apply:projection_guard_refused` が残る |
| ログ行 `PROJECTION-GUARD[<tag>]` | 拒否・dry_run 判定・degrade の記録 |

`gate_degraded` (dry_run degrade) は **dev checkout / 明示 ack の dry_run のみ**。
本番 projection では同じ状況が `refused` (fail-closed) として記録され、apply は
拒否される (`core/strategy_runtime_strict.sh`)。本番で `refused` が lineage 不在
理由で続く場合は `/home/ubuntu/soren-persist` の整備状況を確認する。

## deploy 側との関係

- gate が enforce で動いていれば、live は常に reviewed bytes のため
  `Reconcile reviewed pre-synced Soren checkout` は **走らない** (`Deploy staged code`
  が直接成功する) のが正常。
- 既に drift が存在する状態で enforce を入れても、live を勝手に書き換えない
  (拒否するだけ)。収束は #2482 の手順 (隔離 bytes を upstream するか、reviewed
  bytes へ戻す) で行う。
- 収束後は `tmp/state/active_branch.json` の `head_hash` が未 review のまま残りうる。
  repair は gate により skip されるが、改善系統を reviewed bytes から再開させたい
  場合は同ファイルを align/clear する (repair の自己修復は unreviewed head を
  live へ戻さない)。
