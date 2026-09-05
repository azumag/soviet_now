# 直近の裏側の改修 (tools/build_ops_brief.sh が handoff.md から自動生成。手で編集しない)
- ゲーム切替分離: lifecycle broker と Robots 境界検出を commit/push（本番未反映・E2E前）
- 本番切替実施（ユーザー承認済み・encoder無再起動を実測）
- Omen 専用 fact-check タイムアウト延長を本番反映（240s でも不足と実測）
