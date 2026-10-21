# Node.js / sharp セキュリティ更新（2026-10-21）

確認元: `d75a6201776fb396e39b5878786e56ea230574b0`。

## 変更

- `.node-version`を22.23.4に固定し、Nodeを実行する全9 CI workflowのjobで明示的にsetupする。
- rootと`soren91`の`engines`は`^22.23.4 || ^24.22.1 || ^26.12.1`。古い24/26のpatchを許可しない。
- rootのsharp 0.34.5、soren91のsharp 0.33.5を、修正版0.35.5へ更新。両lockfileを維持する。
- Soren91 CIはrootとsoren91の両lockを`npm ci`で再現し、両方のhigh以上の監査をgateにする。
- macOSの`podcast_build.sh` / `podcast_daily.sh` / `short_video_build.sh`は既存24系列の修正版24.22.1を明示選択する。旧版を自動選択するnvm globとv24.18.0へのfallbackを削除した。

Node.js 22.23.4 / 24.22.1はeMudhra不正中間CAのSPKI拒否と証明書ストア更新を含む。
TLS検証の無効化や、ゲーム判断・音声・配信の制御は変更していない。

## 適用前の条件

macOSジョブをこの変更へ切り替える前に、
`/Users/azumag/.nvm/versions/node/v24.22.1/bin/node`を導入する。
未導入ならジョブはネットワーク通信や生成・公開処理の前に、必要なversionとpathを示して終了する。
この作業ではユーザーのMac、launchd、cron、OrbStackへ接続・変更していない。

Windows描画ホスト、macOS描画ホスト、VMのNodeは別々に更新・実効version確認が必要。
既存常駐プロセスにはファイル更新だけでは反映されない。担当ownerが正規手順で再起動後の実行ファイルとversionを確認する。
Sorenのmain統合後に、親docichの`games/soviet_now`参照も新しいcommitへ更新する。
本番への配備・照合は[docichのdeployment contract](https://github.com/azumag/docich/blob/main/AGENTS.md)に従う。

## 監査と検証

2026-10-21、Node.js 22.23.4 / npm 10.9.9で実施。

| lockfile | 変更前のnpm audit | 変更後 |
| --- | --- | --- |
| root | sharp由来 high 1 | 0件 |
| soren91 | sharp由来 high 1 | 0件 |

- 両lockの`npm ci --ignore-scripts --no-audit --no-fund`が成功。実際にsharpのnative画像処理を使う既存fixtureも通過。
- `node --test --test-concurrency=4 tests/test_soren91_*.mjs`: 638件、612成功、0失敗、26skip（OS専用・実ブラウザ未用意の検査等）。
- `python3 -m unittest tests.test_soren91_runtime_metrics tests.test_soren91_mute_flag`: 39件成功。
- external game audio / direct broadcast overlay / AB overlay / game observationのNodeテスト: 57件成功。
- `node tests/test_kick_chat_daemon.mjs`: all checks passed。
- workflow YAML解析、`git diff --check`が成功。

最初のsandbox実行にはlocalhost/子プロセスの環境制約があり、許可済みのローカルfixture環境で再実行した。
sparse checkoutで欠けていた共通promptを取得した後、全件を再実行して上記結果を得た。
外部ゲーム、実ブラウザ画面、配信先、認証済みAPIには接続していない。

WizのKeyv関連IoC（`wiz-sec-public/wiz-research-iocs/reports/keyv-packages.csv`、blob SHA
`9f165191323ff74a6d06ed6115363f0cdc912d2e`）と両lockの変更前後をpackage名とversionの組で照合し、一致0件。
過去インストール・グローバルCLI・キャッシュ・既存プロセスを調べた結果ではない。

## 実施段階

実装・ローカル検証まで。本番VM、OBS作業中バナー・音声、非公開handoffにはアクセスしていない。
merge、docich gitlink更新、VM配備、既存プロセス再起動、実機確認は未実施。
PR CI、レビュー、本番反映、実効versionの確認を別々に判定する。

## 根拠

- [Node.js 22.23.4](https://nodejs.org/en/blog/release/v22.23.4)
- [Node.js 24.22.1](https://nodejs.org/en/blog/release/v24.22.1)
- [eMudhra SPKI blocklist](https://github.com/nodejs/node/pull/66871)
- [NSS certificate store update](https://github.com/nodejs/node/pull/66877)
- [sharp / libvips advisory](https://github.com/advisories/GHSA-f88m-g3jw-g9cj)
- [sharp / libheif advisory](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c)
- [sharp 0.35.5 advisory](https://github.com/advisories/GHSA-wq5f-xc86-pv6w)
- [Wiz IoC repository](https://github.com/wiz-sec-public/wiz-research-iocs/blob/main/reports/keyv-packages.csv)
