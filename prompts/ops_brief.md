# 直近の裏側の改修 (tools/build_ops_brief.sh が handoff.md から自動生成。手で編集しない)
- Omen 専用 fact-check タイムアウト延長を本番反映（240s でも不足と実測）
- モデルチェーン実測: 構成は反映済みだが fact-check の Omen が 3/3 タイムアウト（→ 上の節で対応済み）
- ゲームと共通配信基盤の分離を実装中（未完了）
