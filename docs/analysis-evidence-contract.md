# 分析を実装へ渡す前の証拠契約

#132 / docich#99 の実測で、分析AIがカウンタ欠測を `soviet=0/13` と記載し、
未観測の `next_type==14` と複数の加点/減点を同時提案した。分析ファイルの存在だけで
Stage2へ進んでいた。hostの旧briefもtype16観測からsoviet_countを作り、未観測を0としていた。

## hostの証拠

`strategy/analysis_contract.py evidence` が選択済みJSONLだけを読み、ファイルSHA、
実在turn、観測した供給type、試合内カウンタ証拠を保存する。symlink、root外、
重複入力、空/破損ログ、非有限JSON、重複キーを拒否する。

建国カウンタはknownな場所（行本体/state_snapshot/state/game_state）の
`makeSorenCount` に限定し、boolを数値にしない。同じ行の矛盾と試合内逆行は未知。
初回turn1の0基準が確認でき、その後正へ増加すれば途中でtype16が消えても建国を保持。
0件の断定にはさらに終了イベント/終了フラグと末尾0を要求する。継承された正の値、
終端未観測、欠測を0にしない。この保守的条件で未知が増えることは意図的。
既存の履歴スキーマを勝手に補完・書き換えず、観測の永続化は#132に残す。

hostの証拠原本をprivate receipt dirへ保存し、sandboxにはコピーを渡す。
briefは原本に基づき未知/分母を表示し、旧+4000表記や「最初の100%未達を必須にする」
指示を削除する。実行中のゲームや既存評価器の採点は変更しない。

## 分析の通過条件

分析文書に一つだけ `analysis_contract` JSONを要求する。
原本SHA、試合数、既知/未知の建国試合数、1仮説、1変更、存在するログ/turn、
編集許可対象、観測済みかつ現行方針1～11内の直接供給typeを照合する。
宣言された供給typeが不要な変更は空配列でよい。Implementation Plan中の既知の誤り
`next_type==14` 等も狭い構文検査で拒否する（一般的な自然言語理解ではない）。

- 通過: CLI成功＋非空文書＋証拠契約成功で初めてStage2へ。
- 不成立: `analysis_contract_invalid` で停止し、JSON判定と原文をhostへ保存。
- 合理的保留: `decision=hold` / 空changes / 非空reasonで `analysis_hold` として停止。
  欠測だけで全改善を禁止しない。観測できる別の失敗局面に裏づけられた仮説は許可する。

**この検査は、自由記述の全主張の真偽・因果性や、実装が本当に一変更かを証明しない。**
宣言を通過しただけで候補を採用しない。AST、隔離、レビュー、同一条件の実ゲーム比較が必要。
不正な文書を修復したことにして進めるfallbackは設けない。providerの利用制限とは別理由。

## 導入と検証

helper/分析prompt/workerを同じreview済みsourceからまとめて限定反映し、既存kernel leaseと
idle/pause照合を使う。CLI予算helperは#198が先行依存。方針7pathの旧manifestを再実行すると
今回の分析prompt差分をdriftとして拒否するので、安易に古いmanifestへ戻さない。

カウンタ欠測・継承・逆行・終端、複数変更、偽参照・hash、許可外path・供給、重複JSON、
明示保留を単体検査する。さらに実workerのbrief heredocとStage1ループをスタブAIで実行し、
未知のままの表示とStage2前停止、原文/判定保存を検査する。実モデルAPIはテストで呼ばない。
