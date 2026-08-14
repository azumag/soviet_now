# strategy.py decide hash を変える手動リファクタのチェックリスト

decide() の AST hash (extract_decide_hash.py) は、改善ループの active_branch pin 管理のキー。
手動で decide hash を変えると、次ゲーム開始時に
`repair_strategy_to_active_branch_head_if_needed()` が旧 head へ自動巻き戻しする。

## 必ずセットで行う手順
1. 変更版を検証: `validate_strategy_with_helpers strategy.py strategy_helpers`
   + 挙動不変ハーネス（実ゲームスナップショットで decide 出力一致）
2. 新 hash を確認: `python3 extract_decide_hash.py strategy.py`
3. アーカイブ登録: `cp strategy.py strategy_versions/by_hash/<NEW_HASH>.py`
4. active_branch 更新:
   - `tmp/state/active_branch.json` の `head_hash` を `<NEW_HASH>` へ
   - `lineage` の末尾に `<NEW_HASH>` を追加
   - 変更前の active_branch.json をバックアップ
5. repair が no-op になることを確認:
   `source ./eloop.sh && repair_strategy_to_active_branch_head_if_needed`
   → hash が `<NEW_HASH>` のまま

## 経緯
2026-08-13: コメント圧縮 + board_stats helper 抽出 (671db2f34b4c)。
適用直後に repair が 182f9ad3954d へ巻き戻し → 上記手順で解消。
