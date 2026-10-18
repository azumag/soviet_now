# 建国 STOP の切替境界

建国の STOP は共有 `is_terminal()` では終端にしない。通常 runner は祝賀待機後に同じ盤面の MOVE を待ち、ゲームオーバーまで継続する。`mark-jev-one-game` と `player_change` に live runner の例外はない。

`command_boundary` だけが、以下をすべて満たす同じ試合の建国 STOP で live runner の切替 ACK を許可する。

- bridge の独立した `game_observation.json` は成功した実観測から更新され、静止中も最長1秒ごとに更新する。盤面・建国 counter・game nonce・STOP nonce を保持する。RETRY、bridge再起動、新試合は game nonce を、MOVE/観測欠落/盤面変更は STOP nonce を更新する。`game_state.json` のmtimeだけを観測鮮度と扱わない。
- 現在の runner PID・試合番号・起動時刻、game_count、markerのinode/mtime/size、bridgeのgame/STOP nonce、盤面が一致する。Linuxではboot IDとprocess start ticksも照合し、PID再利用・再起動を拒否する。
- この runner が同じ建国 STOP を、観測間隔3秒以内で連続300秒以上、monotonic clockで観測している。新runner、新marker、MOVE、観測欠落やwall-clockのジャンプは待機証拠をリセットする。現在の bridge観測とrunner証跡の両方が3秒以内で、未来timestamp、不正counter（NaN/Infinity/bool/非整数/0）を拒否する。

ACKは終端のbookkeepingやゲーム入力を発生させない。資源解放には既存の明示stop controlが必要。例外ACKの証跡tokenはstop要求時・不可逆claim時に再照合し、MOVE/鮮度切れ/帰属変更はwaitingに戻す。bridgeはoverlay readinessの待機後、claim直前に実盤面を再観測する。claim後にMOVEや盤面変化が起きるTOCTOUに対して、ACKが保持する盤面と現在のSTOP盤面を不可逆なUnity Quitと同じbrowser task内で最終照合する。ここにawaitや入力を挟まない。拒否時はQuit・音声停止・browser close・ページ復元を行わず、既存fenceを維持したfailedとして明示的な回復を待つ。

## bridge 復旧と実観測 (soviet_now#500)

`_ensure_bridge_alive` は `game_state.json` の mtime 停滞だけを根拠に bridge を kill/relaunch しない。mtime は盤面が変化した時だけ進むため、休止・境界待ちで同じ盤面を保持している間も止まる (2026-09-23 は lifecycle cancel 後の再開直後にこれが 806s 停滞と見え、bridge を再起動して盤面を失った)。

停滞を検知したら、まず `tmp/state/game_observation.json` (bridge が実観測から最長1秒ごとに更新する) を非破壊に再観測する。待機は `BRIDGE_OBSERVE_WAIT_SEC` (既定5s)、鮮度閾値は `BRIDGE_OBSERVE_FRESH_SEC` (既定15s)。実観測が生存していれば live page は生きており、凍結した `game_state.json` は保持対象の盤面と判定して保留し、kill/relaunch も RETRY/RELOAD も送らず盤面リセットへ自動で進まない。保留は `BRIDGE_STALE_NOTICE_SEC` (既定60s) ごとに理由と盤面をログする。実観測も途絶えている場合だけ従来どおり復旧する (プロセス消失・致命ログ署名の扱いは変えない)。

## 反映と依存

旧runner/旧bridgeにはこの証跡がないため、古いmarker・静止ファイルだけで現在の稼働試合を終了させない。コードのcommit/pushやCI成功で進行中switchの復旧を保証しない。新コードを正規に配備し、次の自然なrunner/bridge起動から有効にする。既存試合の強制終了や確認用再起動はこの修正の範囲外。

docichの `SorenAdapter.request_round_boundary()` はrequest後にstatusを読むだけであり、runnerがSTOPで待つ間には通常boundary pollが発生しない。親側では、同じrequest identityとdeadline/cancelを維持して能動 `boundary` pollを行い、RC0/1だけを許可する別修正と、統合済みSoren commitへのgitlink追随が必要。player_change専用pollの存在を通常switchの実装済みと扱わない。Soren PR582はこの親依存・最新HEADのレビュー/CI・正規配備・本番受入が揃うまで復旧完了ではない。

## 検証

`JEV player contracts` はbroker/terminal/証跡helper/runnerの `py_compile` と、実broker subprocess、証跡、通常runner継続のPython回帰、およびbridge観測writerのNode回帰を実行する。関連Python/helper/testパスだけの変更もworkflowを起動する。fixtureの成功は本番受入と区別する。
