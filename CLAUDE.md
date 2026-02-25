# Soren Game Project

Soviet/Soren パズルゲーム（スイカゲーム風・ソ連共和国旗）の AI 自動プレイプロジェクト。

## Unity WebGL ビルド

**ビルドガイド**: `sorengame/BUILD_GUIDE.md` を参照。

ビルドのポイント:
- プロジェクトソース: `sorengame/_extracted/soren-game-fixed/`
- NAS 上ではビルド不可（`._*` ファイル問題）。必ず `/tmp/soren-unity` にコピーして `dot_clean` 後にビルド
- アセットは **2つの ZIP** から補完が必要（テクスチャ GUID 問題、TMP シェーダー問題あり）
- 日本語ファイル名の展開は `ditto` を使うこと（`unzip` は文字化けする）
- commit 前に `dot_clean ./` を実行して `._*` ファイルを除去

## ゲーム操作

- `soviet_local.mjs` - ローカルビルドで AI プレイ（Playwright + JS Bridge）
- `soviet_game.mjs` - unityroom.com オンライン版で AI プレイ
- `commands.txt` に書き込んでドロップ指示、`game_state.json` から盤面読み取り
- AI 戦略: `STRATEGY.md`, 思考ログ: `think.md`
