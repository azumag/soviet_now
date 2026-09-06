# 改善spawnと方針更新の排他: kernel-lease-v1

通常の `.improve_spawn.lock/owner` に記録するPIDと90秒TTLの回復契約は維持する。
ただし、ディレクトリの取得・stale回収・解放はすべて `strategy/spawn_guard.py` に
委譲し、隣接する `.improve_spawn.lock.lease` の `flock(LOCK_EX|LOCK_NB)` 中に行う。
既定のパスは `tmp/state/` 配下。競合、helper欠落、権限不足、不正なパスは取得失敗とし、
旧実装へのフォールバックは行わない。TTLの既定値と設定は変えない。

## 方針更新側との契約

- docichのinstallerも同じ永続lease inodeを排他的にロックする。
- installerはディレクトリguardを取得し、idle/no improve.lockを再確認した後、
  バックアップ・全ファイル更新・事後検証・失敗時の復元が完了するまでleaseを保持する。
- ディレクトリが100秒または1日前でも、leaseを保持中はruntimeから取得できない。
- 保持プロセスが終了するとkernelがleaseを解放する。残った通常guardは既存の
  dead-owner/TTL条件で回復する。PIDの再利用でlease自体が期限切れになることはない。
- **leaseファイルは削除・置換しない。** 古いmtimeは異常ではない。手動清掃も禁止。
- leaseのsymlink・複数hardlink・非regular fileは拒否する。未知のguard内容を
  `rm -rf` で消さない。正常なguardはownerファイルだけを含む。

## 反映順序

これは旧runtimeとの混在稼働中にinstallerを実行してよいという契約ではない。
旧shellはleaseを参照しないため、ディスク上のhashだけでは排他を保証できない。

1. 通常schedulerを既存のpause制御で止め、進行中の改善が終了し、spawn guardが
   使われていないことを確認する。既存のユーザー休止は解除しない。
2. helperを先に配置し、そのhashを検証してから2つのshell wrapperを反映する。
   全mainやlive独自差分を一括上書きしない。どちらも事前・事後hashを固定する。
3. `soren_loop.sh` の次試合先頭と `improve_daemon.sh` の次監視周期での
   `eloop_lib.sh` 再読み込みを確認する。配信service/soren_loopは再起動しない。
4. その後にdocich installerを使用する。installerはhelper/wrapperのreview済みhashを
   再照合し、旧版・欠落・第三の内容を拒否する。
5. 適用後のhash・単発実行の入力・休止状態は別々に検証する。ロックテストを
   AI候補の性能向上や実改善1サイクル完走の証拠と扱わない。

## 検証

`python3 -m unittest tests.test_spawn_guard_lease` は実際のshell入口を使い、
長時間保持、最初のmkdir、通常TTL回収、fresh owner、保持process死亡、symlink、
別ownerの解放拒否、同じshellでの取得/解放を一時ディレクトリで検査する。
docich側のクロスリポジトリCIはsource commitを固定して実installerとの相互運用を検査する。

Refs: azumag/docich#96
