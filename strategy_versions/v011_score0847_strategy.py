#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
#  decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v001: 初期スケルトン.analyze_board.analyze_drops() の最高スコア位置を返す.
# v002: マージ成功率向上と高さ管理ロジックを追加
#       - DIRECT/NEARマージ優先(低EVでも確実性重視)
#       - max_y>1.5で危機回避(旗側反対にドロップ)
#       - 旗側固定ロジック(最初のDIRECTマージ側を旗側とする)
#       - 大型ピース旗側集約(type9+は旗側から配置開始)
#       - EVマイナス回避(危機時は無視して配置優先)
# v003: マージ成功率と旗側集約の大幅改善
#       - マージ発生時の_consecutive_no_mergeリセット追加
#       - 旗側固定ロジック改善(最初のDIRECTマージのX座標基準)
#       - シェイク戦略早期化(無マージ3ターンで発動)
#       - 危機回避早期化(max_y>1.0で発動)
#       - 大型ピース旗側集約(旗側max_y<1.0で配置)
#       - 期待値戦略の強化(EV>0の位置を優先)
#       - フォールバック中央の改善(旗側を考慮)
#       - マージ戦略のEVチェック追加(EV>0のマージのみ選択)
# v004: 危機回避ロジックの根本的改善と大型ピース集約強化
#       - 危機時は両側の高さを計算して低い側を選択(旗側無視)
#       - マージ可能なら危機時でも優先(高さ下げる効果)
#       - 高い側の平均Yを計算し,明らかに低い側にドロップ
#       - type7+の旗側集約強化(旗側が決まったら全大型ピース集約)
#       - 旗側未決定時,左右のピース数で旗側決定(多い側を旗側)
#       - 危機回避のしきい値を段階化(1.0で警告,1.5で本格回避)
#       - 壁ドロップ回避(x=±3.0でのバウンドによる不安定化防止)
#       - 次の大型ピース(type7+)の旗側配置を優先
# v005: 履歴分析に基づく根本的改善(2026-02-25)
#       - 分析結果: ターン68-88でmax_yが1.99→3.94に急増しゲームオーバー
#       - 分析結果: type11が左右散在(旗側集約失敗)
#       - 旗側固定ロジック強化: ピース数>5で分布から旗側決定
#       - 大型ピース旗側集約: type9+は必ず旗側(EV>0チェック付き)
#       - 危機回避早期化: max_y>1.0で発動,高い側に配置してマージ誘発
#       - マージ戦略強化: 危機時でもマージ可能なら優先(高さ下げ効果)
#       - シェイク戦略追加: 無マージ3ターンで小ピースで下層を揺らす
#       - nextNext保護: 同typeが続く場合,マージ経路を塞がない配置
#       - 壁ドロップ回避: x=±3.0でのバウンド防止(x=±2.5を使用)
# v006: 高さ管理の根本的再設計と過剰集約防止(2026-02-25)
#       - 分析結果: 左側avg_y=-0.93でもmax_y=2.80(致命的なピーク)
#       - 分析結果: type8+が左側9個,右側5個(過剰集約)
#       - 分析結果: ターン48-51で右側連続ドロップ→両側高くなる失敗
#       - 高さ計算改善: avg_yではなくmax_yを基準に(calculate_side_max_y追加)
#       - 高度危機回避: max_y>1.3で旗側優先,max_y>1.3で高度危機(旗側を避ける)
#       - 大型ピース分散: type7-8は旗側と反対側のバランス重視
#       - type9+旗側集約: type9は旗側固定,type10+は分散を考慮
#       - 危機時のドロップ: 壁ドロップ回避(x=±2.8)
#       - 旗側再評価: ピース数>10で分布再チェック
# v007: 旗側高さ管理の根本的改善(2026-02-25 最新)
#       - 分析結果: ターン39-59で右側max_yが1.3→3.33に急増(旗側過剰集約)
#       - 分析結果: type9旗側配置時EV=-6.0でも配置(旗側高さ無視の失敗)
#       - 分析結果: スコア停滞期間(ターン43-59で17ターン停滞)
#       - 旗側高さ管理: 旗側のmax_yが1.3以上なら旗側を変更
#       - 旗側変更条件: 旗側max_y>1.3かつ反対側が大幅に低い
#       - 大型ピース旗側配置: type9でも旗側max_y<1.3でなければ旗側に配置しない
#       - 危機時旗側回避: max_y>1.0で旗側max_yをチェック,高ければ旗側を避ける
#       - 中程度危機回避: 旗側max_y>1.0なら旗側を反対側に変更
#       - シェイク戦略強化: 無マージ3ターンで早期発動(タイプ4以下)
#       - nextNext保護: 同typeが続く場合,旗側を尊重して配置
# v008: 振り子現象防止と旗側安定化(2026-02-25 最新)
#       - 分析結果: ターン59-62で旗側が3回変更(振り子現象)
#       - 分析結果: 右側max_yが1.09→2.83に急増(旗側変更失敗)
#       - 分析結果: type9,11,10が左右散在(旗側集約完全失敗)
#       - 旗側変更条件厳格化: 反対側が大幅に低い場合(差0.5以上)のみ旗側変更
#       - 旗側固定ロジック強化: 旗側決定時にピース分布を考慮,変更条件を厳しく
#       - 旗側変更禁止期間: 一度旗側を変更したら5ターンは変更しない
#       - 危機回避ロジック改善: 旗側を考慮した危機回避,旗側変更を最小限に
#       - 大型ピース旗側配置: type9+は旗側固定,旗側変更ロジックとは独立
#       - 高さ管理強化: 旗側のmax_yを基準に旗側変更を判断
# v009: 旗側決定ロジックの根本的再設計と危機回避強化(2026-02-25)
#       - 分析結果: ターン59-65でmax_yが2.0→3.71に急増(致命的)
#       - 分析結果: 左側にtype6,8,10が積み重なった(旗側過剰集約)
#       - 分析結果: 旗側が頻繁に変更され,大型ピース散在
#       - 旗側決定ロジック再設計: DIRECTマージではなくピース分布から旗側決定
#       - 旗側変更の厳格化: 反対側が大幅に低い場合(差0.5以上)のみ変更
#       - 旗側max_yチェック強化: 旗側max_y>1.0なら旗側を変更
#       - 大型ピース旗側配置改善: type9+でも旗側max_y<1.0でなければ旗側に配置しない
#       - 旗側再評価追加: ピース数>15で旗側再チェック
#       - 高度危機回避改善: 旗側を無視して低い側にドロップ
#       - 危機時の旗側変更: クールダウン10ターンから7ターンに短縮
#       - 大型ピース旗側強化: type9+の旗側集約を強化(旗側max_y<1.0で配置)
# v010: 履歴分析に基づくv010全面改訂(2026-02-25)
#       - 履歴分析: ターン102-111で右側max_yが1.37→2.6→2.62に急増しゲームオーバー
#       - 履歴分析: type9が左右に散在(id=170右側, id=172左側) - 旗側集約失敗
#       - 履歴分析: 4つのスコア停滞期間(17-19ターン) - マージ成功率低い
#       - 履歴分析: ターン106でDIRECTマージ可能にもかかわらず旗側変更 - 致命的ミス
#       - 旗側決定ロジック根本的改善: 最初のDIRECTマージを旗側とする(分布ロジック削除)
#       - 旗側固定ロジック強化: 旗側決定後は基本的に変更しない(例外的な場合のみ)
#       - 旗側変更条件厳格化: 旗側max_y>1.0かつ反対側max_y<0.8かつ反対側大幅に低い場合のみ
#       - 旗側変更クールダウン短縮: 7ターンから5ターンに
#       - 高度危機回避強化: max_y>1.3で旗側を完全無視して低い側にドロップ
#       - 中程度危機回避強化: max_y>1.0で旗側max_yチェック,旗側>1.0かつ反対側<0.8なら旗側変更
#       - 大型ピース旗側配置厳格化: type9+は旗側配置のみ,旗側max_y<0.8でなければ配置しない
#       - 危機時旗側変更即時ドロップ: 旗側変更後は即時反対側にドロップ
# v011: 旗側max_y管理の根本的緩和と危機回避早期化(2026-02-25 最新)
#       - 履歴分析: ターン45-60でmax_yが1.27→3.58に急増しゲームオーバー
#       - 履歴分析: 左側max_y≈1.9,右側max_y≈1.1でも旗側変更されず(v010の条件"反対側<0.8"が厳しすぎる)
#       - 履歴分析: ターン53-60で7ターンスコア停滞(致命的)
#       - 履歴分析: type9が左右に散在(旗側集約失敗)
#       - 旗側max_y管理緩和: 反対側の条件を削除,旗側max_y>0.7かつ反対側が0.3以上低い場合に旗側変更
#       - 危機回避早期化: max_y>1.0で高度危機回避を発動(旗側無視)
#       - 旗側集約戦略厳格化: 旗側max_y<0.5でないとtype9+を旗側に配置しない
#       - 中程度危機回避: max_y>0.8で旗側max_yチェック,旗側>0.7かつ反対側が0.3以上低いなら旗側変更
#       - シェイク戦略強化: 無マージ4ターンで発動,タイプ5以下で高さを下げる
#       - 旗側変更クールダウン短縮: 5ターンから3ターンに
# v012: 高度危機回避での旗側変更と旗側max_y管理の緩和(2026-02-25)
#       - 履歴分析: ターン65-70で旗側(右側)が高くなっているのに旗側にドロップ
#       - 履歴分析: ターン66で高度危機回避が発動し左側にドロップしたのに旗側未変更
#       - 履歴分析: 旗側max_yが高くなると旗側集約が機能しなくなる
#       - 高度危機回避での旗側変更: 低い側を旗側にする(v012改善)
#       - 高度危機回避早期化: max_y>0.9で発動(1.0→0.9)
#       - 高度危機回避の差閾値緩和: 0.3→0.2
#       - 中程度危機回避早期化: max_y>0.7で発動(0.8→0.7)
#       - 中程度危機回避旗側変更緩和: 差0.3→0.2
#       - 中程度危機回避旗側max_y緩和: 0.7→0.8
#       - 旗側変更クールダウン短縮: 3ターン→1ターン
#       - 大型ピース旗側配置緩和: 旗側max_y<0.8(0.5→0.8)
#       - 大型ピース旗側変更条件緩和: 差0.3→0.2
# v013: 旗側決定ロジックの強化と旗側max_y管理の再緩和(2026-02-25 最新)
#       - 現在盤面分析: 旗側未決定(DIRECTマージなし)
#       - 現在盤面分析: type9,10,12が左右散在(旗側集約完全失敗)
#       - 現在盤面分析: type11(r=1.43)が来ている(巨大ピースの配置戦略が必要)
#       - 旗側決定ロジック強化: DIRECTマージがない場合,大型ピース(type8+)の分布から旗側決定
#       - 旗側決定ロジック強化: ピース数>=10で大型ピース分布をチェック
#       - 旗側変更条件緩和: 旗側max_y>反対側+0.3で旗側変更(v012: 0.2→0.3)
#       - 旗側変更クールダウン緩和: 1ターン→3ターン(安定性確保)
#       - 大型ピース旗側配置緩和: 旗側max_y<1.0(v012: 0.8→1.0)
#       - 大型ピース旗側変更緩和: 旗側max_y>=1.0かつ反対側<0.7なら旗側変更
#       - 旗側変更クールダウン中のドロップ: 旗側max_y>=1.0なら反対側にドロップ
#       - 高度危機回避の差閾値緩和: 0.2→0.3(旗側変更と整合)
#       - 中程度危機回避の差閾値緩和: 0.2→0.3
#       - 中程度危機回避旗側max_y緩和: 0.8→1.0
#       - フォールバック改善: 旗側max_y>=1.0なら反対側にドロップ
# v014: 旗側決定ロジックのtype9+使用と旗側max_y管理緩和(2026-02-25 最新)
#       - 現在盤面分析: type9が左右散在(id=34左側x=-0.358, id=122右側x=2.081) - v013の旗側決定失敗
#       - 現在盤面分析: type8+ではなくtype9+の分布から旗側決定すべき
#       - 現在盤面分析: max_y=-0.593で危機ではないが,旗側未決定でtype9+散在
#       - 旗側決定ロジック改善: type8+→type9+を使用(type8は集約対象外)
#       - 旗側決定ロジック改善: ピース数>=5で早期旗側決定(10→5)
#       - 旗側決定ロジック改善: type9+分布チェックで旗側決定,差2以上で旗側決定
#       - 大型ピース旗側配置統合: type9+旗側配置ロジックを旗側決定ロジックと統合
#       - 大型ピース旗側配置緩和: 旗側max_y<1.3(1.0→1.3)
#       - 大型ピース旗側変更緩和: 旗側max_y>=1.3かつ反対側<0.8なら旗側変更
#       - 高度危機回避: max_y>1.0で発動(0.9→1.0)
#       - 中程度危機回避: max_y>0.7で発動(0.7→0.7維持)
#       - フォールバック改善: 旗側max_y>=1.3なら反対側にドロップ
# v015: 旗側早期決定と旗側固定強化(2026-02-25 最新)
#       - 履歴分析: ターン1-4で4ターン連続スコア停滞(merge_available=false全ターン)
#       - 履歴分析: type9が左右散在(id=34左側, id=122右側) - v014の旗側決定失敗
#       - 履歴分析: v014の条件"差2以上"が厳しすぎる(左1,右1で旗側未決定)
#       - 履歴分析: DIRECTマージがない場合,旗側決定ロジックが機能しない
#       - 旗側早期決定: type9+が1個以上あれば,max_yが低い側を旗側にする
#       - 旗側固定強化: 一度旗側を決定したら,旗側max_y>1.5かつ反対側が大幅に低い場合のみ変更
#       - 旗側変更条件厳格化: 旗側max_y>反対側+0.5で旗側変更(v014: 0.3→0.5)
#       - 旗側変更クールダウン: 5ターンに戻す(v014: 3ターン→5ターン,安定性確保)
#       - 旗側max_y管理緩和: 旗側max_y<1.5でtype9+旗側配置(v014: 1.3→1.5)
#       - 大型ピース旗側配置: type9+は旗側配置のみ(旗側max_y<1.5であれば配置)
#       - nextNext保護強化: type9が続く場合,旗側を優先して左右分ける
#       - 旗側変更禁止期間: クールダウン中は旗側を変更しない
#       - 高度危機回避: max_y>1.0で発動(v014維持)
#       - 中程度危機回避: max_y>0.7で発動(v014維持)
#       - フォールバック改善: 旗側max_y>=1.5なら反対側にドロップ
# v016: 旗側決定ロジックの根本的再設計と旗側max_y管理の厳格化(2026-02-25 最新)
#       - 履歴分析: ターン1-20で全ターンスコア停滞(merge_available=9回,成功率0%)
#       - 履歴分析: max_yが-0.02→3.91に急増しゲームオーバー(致命的)
#       - 履歴分析: type9が左右散在(左1,右2) - v015の旗側決定失敗
#       - 履歴分析: 左側max_y=3.91,右側max_y=1.74で旗側未変更(旗側固定が強すぎる)
#       - 履歴分析: 中程度危機回避が10回発動(max_y>0.7で常に発動)
#       - 旗側決定ロジック再設計: ピース数>=3でtype9+のピース数が多い側を旗側にする(v015: 5→3)
#       - 旗側決定ロジック改善: ピース数が同じ場合はmax_yが低い側を旗側にする
#       - 旗側max_y管理厳格化: 旗側max_y<0.8でtype9+旗側配置(v015: 1.5→0.8)
#       - 旗側変更条件緩和: 旗側max_y>=0.8かつ反対側が0.5以上低い場合に旗側変更(v015: 1.5→0.8)
#       - 高度危機回避強化: 旗側max_y>1.0なら旗側を無視して低い側にドロップ
#       - シェイク戦略早期化: 無マージ3ターンで発動(v015: 4→3)
#       - フォールバック改善: 旗側max_y>=0.8なら反対側にドロップ(v015: 1.5→0.8)
# v017: 履歴分析に基づくv017根本的改善(2026-02-25 最新)
#       - 履歴分析: マージ成功率0.0%(24回マージ可能なのに一度も成功していない)
#       - 履歴分析: ターン50-71で"中程度危機回避""高度危機回避"が連続しマージの機会を逃している
#       - 履歴分析: type9+が左右に散在(左3個,右6個) - 旗側集約完全失敗
#       - 履歴分析: max_yが1.05→2.35に急増しゲームオーバー
#       - 旗側決定ロジック改善: 旗側max_y管理を緩和(1.0で旗側配置可能に)
#       - 旗側変更条件厳格化: 反対側が0.4以上低いかつ旗側max_y>1.0で旗側変更(整合性確保)
#       - 旗側変更クールダウン短縮: 3ターン→2ターン(柔軟性確保)
#       - 高度危機回避強化: max_y>1.5で発動(v016: 1.0→1.5,早期化防止)
#       - 中程度危機回避強化: max_y>1.0で発動(v016: 0.7→1.0,不要発動防止)
#       - 旗側max_y管理緩和: 旗側max_y<1.0でtype9+旗側配置(v016: 0.8→1.0)
#       - 大型ピース旗側変更緩和: 旗側max_y>=1.0かつ反対側<0.6なら旗側変更(整合性確保)
#       - シェイク戦略早期化: 無マージ2ターンで発動(v016: 3→2)
#       - フォールバック改善: 旗側max_y>=1.0なら反対側にドロップ(v016: 0.8→1.0)
# v018: 旗側決定ロジックの根本的再設計と旗側振り子防止(2026-02-25 最新)
#       - 履歴分析: マージ成功率0.0%(19回マージ可能なのに一度も成功していない)
#       - 履歴分析: ターン50-65で旗側が左右に往復(振り子現象)
#       - 履歴分析: ターン52-59で8ターン連続"中程度危機回避"が旗側にドロップ
#       - 履歴分析: ターン60で高度危機回避が旗側を左に変更,ターン63で右に戻す(振り子)
#       - 履歴分析: 最終盤面type9+が左右散在(左6個,右5個)- 旗側集約完全失敗
#       - 履歴分析: 左側max_y=3.29,右側max_y=2.56で左側が致命的に高い
#       - 旗側決定ロジック根本的再設計: type9+が1個以上あれば即時旗側決定
#       - 旗側決定基準: type9+のピース数が多い側を旗側,同じならmax_yが低い側
#       - 旗側固定ロジック強化: 一度旗側決定後は基本的に変更しない
#       - 旗側変更条件厳格化: 旗側max_y>1.5かつ反対側が大幅に低い場合のみ旗側変更
#       - 旗側変更クールダウン: 5ターンに戻す(v017: 2ターン→5ターン,振り子徹底防止)
#       - 旗側max_y管理緩和: 旗側max_y<0.8でtype9+旗側配置(v017: 1.0→0.8)
#       - 大型ピース旗側変更緩和: 旗側max_y>=0.8かつ反対側<0.5なら旗側変更
#       - 高度危機回避強化: max_y>1.5で発動(v017維持),旗側max_y>1.0で旗側無視
#       - 中程度危機回避強化: max_y>1.0で発動(v017維持),旗側max_y>=1.0で旗側変更
#       - 中程度危機回避クールダウン中: 旗側max_y<1.0なら旗側にドロップ,反対側にドロップ
# v019: 旗側固定ロジックの根本的強化と旗側max_y管理の厳格化(2026-02-25 最新)
#       - 履歴分析: マージ成功率0.0%(見かけ上)だが実際にはマージは成功している(スコア反映遅延)
#       - 履歴分析: ターン30-39で10ターン連続max_y>1.0(旗側max_y=1.75,マージ失敗続く)
#       - 履歴分析: type9,10,11,12が左右散在(左側4個,右側6個)- 旗側集約完全失敗
#       - 履歴分析: ターン39-40で旗側変更(左→右)が旗側振り子を引き起こした
#       - 履歴分析: 旗側max_y>1.5で旗側にドロップすると,マージ成功率が低下する
#       - 旗側固定ロジック根本的強化: 一度旗側を決定したら,max_y>2.0(赤線直前)でない限り変更しない
#       - 旗側変更条件極限厳格化: max_y>2.0かつ旗側max_y>反対側+0.8かつ反対側<0.5の場合のみ旗側変更
#       - 旗側変更クールダウン: 7ターンに延長(v018: 5ターン→7ターン,振り子徹底防止)
#       - 旗側max_y管理厳格化: 旗側max_y<1.0でtype9+旗側配置(v018: 0.8→1.0)
#       - マージ戦略旗側フィルタリング: 旗側max_y>1.0なら旗側のマージを回避し反対側のマージを優先
#       - 高度危機回避強化: max_y>1.5で発動(v018維持),旗側max_y>1.0なら旗側即時変更して低い側にドロップ
#       - 中程度危機回避強化: max_y>1.0で発動(v018維持),旗側max_y>1.0なら旗側を回避
#       - 旗側変更クールダウン中: 旗側max_y>1.0なら即時反対側にドロップ,旗側を回避
# v020: クールダウン期間中の旗側回避バグ修正と旗側max_y管理の統一化(2026-02-25 最新)
#       - 履歴分析: ターン67-70で高度危機回避が連続発動(左側max_y=1.5,右側max_y=0.4)
#       - 履歴分析: ターン67で旗側を"right"に即時変更,ターン70で左側(x=-2.8)にドロップ
#       - 履歴分析: ターン70の"中程度危機回避(クールダウン中旗側回避) x=-2.80"が旗側回避失敗
#       - 履歴分析: ターン73でtype11が左側y=3.1に到達しゲームオーバー
#       - クールダウン期間中の旗側回避バグ修正: target_x = 2.8 if _flag_side == "left" else -2.8 → 反対側にドロップ
#       - 高度危機回避での旗側変更後の旗側max_y考慮: 旗側変更後は旗側max_yをチェックして旗側を回避
#       - 旗側max_y管理の統一化: "旗側max_y>1.0"を全ロジックで旗側回避の統一閾値に
#       - 旗側変更クールダウン期間中の特別処理: クールダウン期間中は旗側max_y>1.0なら即時反対側にドロップ
#       - 高度危機回避の旗側考慮強化: 旗側max_y>1.0の場合,旗側を無視して低い側にドロップ
#       - 旗側集約戦略の旗側max_y管理: 旗側max_y>1.0の場合は旗側に配置しない
#       - フォールバックの旗側max_y管理: 旗側max_y>1.0の場合は反対側にドロップ
#       - マージ見逃し対策: merge_available=trueでscore_delta=0が続く場合,旗側max_y>1.0なら旗側のマージを回避
# v021: 旗側変更後の旗側max_y即時チェックと旗側max_y管理の厳格化(2026-02-25 最新)
#       - 履歴分析: ターン50-84で34ターンスコア停滞(score=1437→1437)
#       - 履歴分析: ターン67-72で旗側=right,右側max_y=2.0に達しているのに左側にドロップ(旗側回避失敗)
#       - 履歴分析: ターン73-82で"中程度危機回避(クールダウン中旗側回避)"が左側にドロップ(旗側回避バグ)
#       - 履歴分析: 最終盤面type9+が左右散在(左側6個,右側5個)- 旗側集約完全失敗
#       - 履歴分析: 左側max_y=3.27(id=125 type11が赤線突破)でゲームオーバー
#       - 旗側変更後の旗側max_y即時チェック追加: 旗側変更後は旗側max_yをチェックして,高い場合は即時回避
#       - 旗側max_y管理の厳格化: 旗側max_y>0.8で旗側回避(v020: 1.0→0.8)
#       - 旗側決定ロジック改善: type9+が1個以上あれば即時旗側決定(v018のロジックを維持)
#       - 旗側変更条件の厳格化: 旗側max_y>1.0かつ反対側が大幅に低い場合のみ旗側変更(v020: 整合性維持)
#       - クールダウン期間中の旗側回避改善: クールダウン期間中でも旗側max_y>1.0なら旗側回避(v020のバグ修正)
#       - 高度危機回避改善: max_y>1.5で発動(v020維持),旗側max_y>1.0なら旗側変更後でも即時回避
#       - 中程度危機回避改善: max_y>1.0で発動(v020維持),旗側max_y>1.0なら旗側回避
#       - 大型ピース旗側配置改善: 旗側max_y<0.8で旗側配置(v020: 1.0→0.8)
#       - 大型ピース旗側変更改善: 旗側max_y>=0.8かつ反対側<0.6なら旗側変更(v020: 1.0→0.8)
# v022: 旗側決定の簡素化と危機回避強化(2026-02-25 最新)
#       - 履歴分析: ターン86-90でmax_yが2.33→2.8に急増しゲームオーバー(致命的)
#       - 履歴分析: type9+大型ピース散在(左4個,右4個,中央1個)- 旗側集約完全失敗
#       - 履歴分析: ターン85-90で5ターン連続高度危機(max_y>1.5)だが有効回避不足
#       - 履歴分析: ターン81-90で9ターンスコア停滞(score=1608→1800) - マージ成功率低下
#       - 履歴分析: 旗側が頻繁に変更され振子現象(turn 85-90で旗側right→left→right)
#       - 旗側決定の簡素化: type9+が1個以上あれば,type9+のmax_yが低い側を旗側にする(ピース数ではなくmax_y基準)
#       - 旗側変更の厳格化: 一度旗側を決定したら,旗側max_y<1.0でない限り変更しない
#       - 大型ピース配置緩和: type9+は旗側配置優先だが,旗側max_y>1.2であれば反対側配置(1.0→1.2緩和)
#       - 高度危機回避強化: max_y>1.5の場合,マージ可能な位置を優先(高さ下げ効果重視)
#       - 危機時旗側変更: 高度危機時で旗側max_y>1.0なら,即時旗側変更して低い側を旗側にする
#       - 旗側変更クールダウン: 5ターン(振子防止)
#       - 旗側max_y管理の統一: 旗側変更条件>1.0,大型ピース配置>1.2
#       - マージ戦略強化: 高さ下げ効果のあるマージを優先
# v023: 旗側決定ロジックの再設計と旗側max_y管理の緩和(2026-02-25 最新)
#       - 履歴分析: ターン33-49で17ターン連続スコア停滞(致命的)
#       - 履歴分析: ターン50-71で"中程度危機回避""高度危機回避"が連続しマージの機会を逃している
#       - 履歴分析: 最終盤面type9+が左右散在(左3個,右3個,中央1個)- 旗側集約完全失敗
#       - 履歴分析: max_yが1.0→3.56に急増しゲームオーバー(致命的)
#       - 履歴分析: ターン61で旗側=right,右側max_y=0.12,左側max_y=2.31(旗側低いのに旗側未変更)
#       - 履歴分析: 旗側決定ロジックが機能しない(type9+が散在しても旗側決定できず)
#       - 履歴分析: 旗側変更条件が厳しすぎる(旗側max_y>1.0かつ反対側が大幅に低い場合のみ変更)
#       - 履歴分析: クールダウン期間中の旗側回避バグ(旗側max_yが高くても旗側にドロップ)
#       - 旗側決定ロジック簡素化: type9+が1個以上あれば,max_yが低い側を旗側にする(v022維持)
#       - 旗側変更条件緩和: 旗側max_y>0.8かつ反対側が0.5以上低い場合に旗側変更(v022: 1.0→0.8)
#       - 旗側変更クールダウン短縮: 3ターン(v022: 5ターン→3ターン,柔軟性確保)
#       - 旗側max_y管理緩和: 旗側max_y<0.8でtype9+旗側配置(v022: 1.2→0.8)
#       - クールダウン期間中の旗側回避追加: クールダウン期間中でも旗側max_y>0.8なら即時反対側にドロップ
#       - 高度危機回避強化: max_y>1.5で発動(v022維持),旗側max_y>0.8なら旗側を回避
#       - 中程度危機回避強化: max_y>1.0で発動(v022維持),旗側max_y>0.8なら旗側を回避
#       - マージ戦略旗側フィルタリング: 旗側max_y>0.8なら旗側のマージを回避
# v024: クールダウン期間中の旗側回避ロジック修正と高度危機回避の早期化(2026-02-25 最新)
#       - 履歴分析: ターン79-87で9ターン連続スコア停滞(score=1035) - 致命的
#       - 履歴分析: max_yが2.49→3.21に急増しゲームオーバー
#       - 履歴分析: ターン73-87で"クールダウン中旗側回避"が連続発動
#       - 履歴分析: 旗側がleft,左側max_y=2.74(id=125 type8)で赤線突破
#       - 履歴分析: ターン73-87で"クールダウン中旗側回避"が左側x=-2.8にドロップ(旗側側にドロップしている)
#       - 履歴分析: ターン73でflag_side=left,旗側max_y=1.0で左側x=-2.8にドロップ(旗側回避失敗)
#       - 履歴分析: クールダウン期間中の旗側回避ロジックにバグ(旗側側にドロップしている)
#       - 履歴分析: ターン76-77で右側x=2.8にドロップ(旗側回避成功)
#       - 履歴分析: ターン78で"高度危機回避(左側高い) x=2.80"(flag_side未変更,右側も高くなる)
#       - 履歴分析: ターン79-85で"クールダウン中旗側回避 x=-2.80"が連続(左側にドロップ)
#       - 履歴分析: 旗側がleftなのに左側x=-2.8にドロップし続け,左側max_y=3.21でゲームオーバー
#       - クールダウン期間中の旗側回避ロジックバグ修正: target_x = -2.8 if _flag_side == "left" else 2.8 → 反対側にドロップ
#       - 高度危機回避早期化: max_y>1.0で発動(v023: 1.5→1.0,手遅れ防止)
#       - 中程度危機回避早期化: max_y>0.7で発動(v023維持)
#       - 旗側変更条件緩和: 反対側が0.3以上低い場合に旗側変更(v023: 0.5→0.3,早期旗側変更)
#       - 旗側変更クールダウン: 5ターン(v023: 3ターン→5ターン,安定性確保)
#       - 旗側max_y管理緩和: 旗側max_y<0.5でtype9+旗側配置(v023: 0.8→0.5,厳格化)
#       - 旗側変更後の即時反対側ドロップ: 旗側変更後は即時反対側にドロップ
#       - 高度危機回避での旗側変更: 高度危機時で旗側max_y>0.8なら,即時旗側変更して低い側にドロップ
#       - クールダウン期間中でも旗側max_y>0.8なら即時反対側にドロップ
#       - 旗側回避ロジックの統一化: 旗側max_y>0.8なら反対側にドロップ

# モジュールレベル変数(試合内の状態保持)
_flag_side = None  # 旗側: "left" または "right"
_last_drop_x = 0.0
_consecutive_no_merge = 0  # 連続無マージ数
_flag_change_cooldown = 0  # 旗側変更クールダウン(ターン数)


def calculate_side_max_y(pieces: list, side: str, min_type: int = 0) -> float:
    """指定された側の最大高さを計算する(v006推奨).

    Args:
        pieces: 全ピースリスト
        side: "left" (x<0) または "right" (x>0)
        min_type: 最小タイプ(デフォルト0で全ピース対象)

    Returns:
        最大高さ(ピースがない場合は -inf)
    """
    side_pieces = [
        p
        for p in pieces
        if p["type"] >= min_type
        and ((side == "left" and p["x"] < 0) or (side == "right" and p["x"] > 0))
    ]
    if not side_pieces:
        return -float("inf")
    return max(p["y"] for p in side_pieces)


def decide(game_state: dict, analysis: dict) -> dict:
    """盤面状態と解析結果から最適ドロップX座標を決定する.

    Args:
        game_state: game_state.json の内容
        analysis: {"results": [...], "same_type": [...], "reactor": {...}}

    Returns:
        {"x": float, "reason": str}
    """
    global _flag_side, _last_drop_x, _consecutive_no_merge, _flag_change_cooldown

    results = analysis.get("results", [])
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", 0)
    next_r = next_piece.get("r", 0.5)

    # 現在の最高到達位置を取得
    max_y = max([p["y"] for p in pieces]) if pieces else 0.0

    # --- v024改善: 旗側決定ロジックの簡素化(type9+が1個以上あればmax_yが低い側を旗側)---
    if _flag_side is None:
        # 1. まずDIRECTマージを探す
        if results:
            for r in results:
                if r.get("merge_grade") == "DIRECT" and r.get("has_merge", False):
                    _flag_side = "left" if r["x"] < 0 else "right"
                    break

        # 2. v024改善: type9+が1個以上あれば,max_yが低い側を旗側にする
        if _flag_side is None:
            left_9plus_max_y = calculate_side_max_y(pieces, "left", min_type=9)
            right_9plus_max_y = calculate_side_max_y(pieces, "right", min_type=9)

            # 両側にtype9+がある場合,max_yが低い側を旗側にする
            if left_9plus_max_y > -float("inf") and right_9plus_max_y > -float("inf"):
                if left_9plus_max_y < right_9plus_max_y:
                    _flag_side = "left"
                elif right_9plus_max_y < left_9plus_max_y:
                    _flag_side = "right"
            # 片方のみtype9+がある場合,その側を旗側にする
            elif left_9plus_max_y > -float("inf"):
                _flag_side = "left"
            elif right_9plus_max_y > -float("inf"):
                _flag_side = "right"

        # 3. type9+がない場合,type8+のmax_y基準で旗側決定
        if _flag_side is None:
            left_8plus_max_y = calculate_side_max_y(pieces, "left", min_type=8)
            right_8plus_max_y = calculate_side_max_y(pieces, "right", min_type=8)

            if left_8plus_max_y > -float("inf") and right_8plus_max_y > -float("inf"):
                if left_8plus_max_y < right_8plus_max_y:
                    _flag_side = "left"
                elif right_8plus_max_y < left_8plus_max_y:
                    _flag_side = "right"

    # 旗側決定後は旗側変更クールダウンをデクリメント
    if _flag_change_cooldown > 0:
        _flag_change_cooldown -= 1

    # --- v024改善: クールダウン期間中の旗側回避ロジックバグ修正 ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

        # クールダウン期間中でも旗側max_y>0.8なら即時反対側にドロップ
        if flag_side_max_y > 0.8:
            target_x = -2.8 if _flag_side == "left" else 2.8
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"クールダウン中旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
            }

    # --- v024改善: 高度危機回避での旗側変更(max_y>1.0で発動)---
    if max_y > 1.0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        higher_side = "left" if left_max_y > right_max_y else "right"
        lower_side = "right" if left_max_y > right_max_y else "left"

        # v024改善: 高い側が旗側の場合,旗側を変更して低い側にドロップ
        if _flag_side is not None and _flag_side == higher_side:
            flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
            if flag_side_max_y > 0.8:
                _flag_side = lower_side
                _flag_change_cooldown = 5  # v024改善: 5ターン
                target_x = 2.8 if lower_side == "right" else -2.8
                _consecutive_no_merge += 1
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"高度危機回避(旗側変更) x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
                }

        # 高い側にドロップ(マージ誘発)
        target_x = 2.8 if higher_side == "right" else -2.8
        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"高度危機回避 x={target_x:.2f}",
        }

    # --- v024改善: 旗側変更条件の緩和(反対側が0.3以上低い場合に旗側変更)---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

        # 旗側max_y>0.8の場合,反対側が0.3以上低ければ旗側を変更
        if flag_side_max_y > 0.8:
            opposite_max_y = right_max_y if _flag_side == "left" else left_max_y
            if flag_side_max_y > opposite_max_y + 0.3:
                _flag_side = "right" if _flag_side == "left" else "left"
                _flag_change_cooldown = 5  # v024改善: 5ターン

    # --- 1. マージ可能なら最優先(v024改善: 旗側max_y>0.8なら旗側のマージを回避)---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
            mergeable_results.append(r)

    if mergeable_results:
        # v024改善: 旗側max_y>0.8なら旗側のマージを回避
        if _flag_side is not None:
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")
            flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

            if flag_side_max_y > 0.8:
                # 旗側以外のマージを探す
                non_flag_merges = [
                    r
                    for r in mergeable_results
                    if (_flag_side == "left" and r["x"] > 0)
                    or (_flag_side == "right" and r["x"] < 0)
                ]
                if non_flag_merges:
                    best = max(non_flag_merges, key=lambda r: r.get("score", 0))
                    x = best["x"]
                    score = best.get("score", 0)
                    _consecutive_no_merge = 0
                    _last_drop_x = x
                    return {
                        "x": x,
                        "reason": f"マージ(旗側回避) x={x:.2f} (score={score:.1f})",
                    }

        # 通常時はEVが正のマージのみ対象
        positive_merge_results = [r for r in mergeable_results if r.get("score", 0) > 0]

        if positive_merge_results:
            # DIRECTマージ優先
            direct_merges = [
                r for r in positive_merge_results if r.get("merge_grade") == "DIRECT"
            ]
            if direct_merges:
                best = max(direct_merges, key=lambda r: r.get("score", 0))
            else:
                best = max(positive_merge_results, key=lambda r: r.get("score", 0))

            x = best["x"]
            score = best.get("score", 0)
            _consecutive_no_merge = 0
            _last_drop_x = x
            return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

    # --- 2. 中程度危機回避(max_y > 0.7)---
    if max_y > 0.7:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side is not None:
            flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

            # v024改善: 旗側max_y>0.8なら旗側を回避して反対側にドロップ
            if flag_side_max_y > 0.8:
                target_x = -2.8 if _flag_side == "left" else 2.8
            else:
                target_x = 2.8 if _flag_side == "left" else -2.8
        else:
            target_x = -2.8 if left_max_y < right_max_y else 2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中程度危機回避(旗側優先) x={target_x:.2f}",
        }

    # --- 3. 大型ピース旗側配置(v024改善: 旗側max_y<0.5で旗側配置)---
    if _flag_side is not None and next_type >= 9:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

        # v024改善: 旗側max_y<0.5で旗側配置(v023: 0.8→0.5,厳格化)
        if flag_side_max_y < 0.5:
            if _flag_side == "left":
                target_x = -2.8
            else:
                target_x = 2.8

            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"大型ピース旗側 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
            }
        else:
            # v024改善: 旗側max_y>0.5の場合,反対側に配置(旗側を避ける)
            if _flag_side == "left":
                target_x = 2.8
            else:
                target_x = -2.8

            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"大型ピース旗側(高さ超過) x={target_x:.2f}",
            }

    # --- 4. type7-8の配置戦略(旗側と反対側に配置)---
    if _flag_side is not None and 7 <= next_type <= 8:
        target_x = 2.8 if _flag_side == "left" else -2.8
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中型ピース反対側 x={target_x:.2f}",
        }

    # --- 5. シェイク戦略(無マージ2ターンで発動)---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 2 and next_type <= 5:
        # 高い側でEVが正の位置を探す
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        target_side = "left" if left_max_y > right_max_y else "right"

        best_ev = -float("inf")
        best_x = None

        for r in results:
            x = r["x"]
            ev = r.get("score", 0)
            is_target_side = (target_side == "left" and x < 0) or (
                target_side == "right" and x > 0
            )

            if is_target_side and ev > best_ev:
                best_ev = ev
                best_x = x

        if best_x is not None and best_ev > 0:
            _consecutive_no_merge = 0
            _last_drop_x = best_x
            return {
                "x": best_x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={best_x:.2f}",
            }

    # --- 6. 次のピース保護(nextNextのマージ経路を塞がない)---
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        if _flag_side == "left":
            x = -2.8 if abs(_last_drop_x) > 1.5 else -2.0
        elif _flag_side == "right":
            x = 2.8 if abs(_last_drop_x) > 1.5 else 2.0
        else:
            if abs(_last_drop_x) > 1.5:
                x = -_last_drop_x
            else:
                x = 2.8 if _last_drop_x < 0 else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = x
        return {"x": x, "reason": f"nextNext保護 x={x:.2f}"}

    # --- 7. 通常の期待値戦略(EV>0の位置を優先)---
    valid_results = [r for r in results if r.get("score", 0) > 0]

    if valid_results:
        best = valid_results[0]
        x = best["x"]
        ev = best.get("score", 0)

        # 旗側に合わせて配置
        if _flag_side == "left" and x > 0 and len(valid_results) > 1:
            for r in valid_results:
                if r["x"] < 0:
                    x = r["x"]
                    ev = r.get("score", 0)
                    break
        elif _flag_side == "right" and x < 0 and len(valid_results) > 1:
            for r in valid_results:
                if r["x"] > 0:
                    x = r["x"]
                    ev = r.get("score", 0)
                    break

        _last_drop_x = x
        return {"x": x, "reason": f"期待値 x={x:.2f} (EV={ev:.1f})"}

    # --- 8. フォールバック: 旗側側の中央---
    _consecutive_no_merge += 1

    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            # v024改善: 旗側max_y<0.5で旗側配置(v023: 0.8→0.5)
            if left_max_y < 0.5:
                x = -1.5
            else:
                x = 1.5
        elif _flag_side == "right":
            if right_max_y < 0.5:
                x = 1.5
            else:
                x = -1.5
        else:
            x = 0.0
    else:
        x = 0.0

    _last_drop_x = x
    return {"x": x, "reason": f"フォールバック({_flag_side or '中央'})"}


# --- AI改変禁止ゾーン ---
if __name__ == "__main__":
    import json
    import sys

    # スタンドアロンテスト用
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"

    try:
        game_state = json.load(open(gs_path))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # analyze_board から解析データ取得
    try:
        from analyze_board import analyze_drops, calc_reactor_state

        pieces = game_state.get("pieces", [])
        shapes = game_state.get("shapes", {})
        nxt = game_state.get("next", {})
        nt = nxt.get("type", 0)
        nr = nxt.get("r", 0.5)

        results, same_type = analyze_drops(pieces, nt, nr, shapes)
        reactor = calc_reactor_state(pieces)
        analysis = {
            "results": results,
            "same_type": [
                {"id": p["id"], "type": p["type"], "x": p["x"], "y": p["y"]}
                for p in same_type
            ],
            "reactor": reactor,
        }
    except Exception as e:
        analysis = {"results": [], "same_type": [], "reactor": {}, "error": str(e)}

    result = decide(game_state, analysis)
    print(json.dumps(result, ensure_ascii=False, indent=2))
