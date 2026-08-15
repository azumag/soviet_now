# Twitch Closed Captions（FFmpeg直接配信）

## 目的と安全境界

VOICEVOXで再生する日本語発話を短い英語へ翻訳し、OBSや字幕専用RTMP proxyを使わず、既存のH.264へCEA-608/A/53として埋め込みます。TwitchはH.264 SEI内のCEA-708/EIA-608（ATSC A/72、CC1/field 1）を字幕入力として案内しています。

この機能は `DOCICH_CC_ENABLED=0` が既定です。FFmpegの字幕socketが存在しない間は翻訳自体を開始しません。翻訳、字幕計画、Unix socket、カスタムFFmpegのいずれかが失敗しても、字幕だけを停止して日本語音声と映像は継続します。配信先、配信キー、認証情報は扱いません。

## 構成

1. `say_enqueue.sh` がVOICEVOX用の日本語チャンクを確定します。Deferred radioの事前生成では、結合WAVだけでなく、同じ境界の個別WAV・日本語チャンクをprivate bundleとして保存します。
2. `lib/closed_captions.py` がローカルのOpenAI互換endpointへ一括翻訳を依頼します。モデル応答は `{"translations":[...]}` のJSONだけを受理し、thinking、tool trace、Markdown、説明文が前後に付いた応答は字幕計画ごと破棄します。配列の件数が音声チャンクより少ない場合は利用できる先頭分だけ字幕化し、余分な翻訳は無視します。
3. 英文を保守的なASCIIへ正規化し、1行32文字、最大2行、1チャンク1ページへ制限します。
4. `lib/closed_captions.sh` が翻訳とVOICEVOX合成を並行させ、再生中に次チャンクを `prepare`、音声境界で `commit`、発話終了時に `clear` します。事前生成ラジオもbundleの個別WAVを同じ順で再生するため、結合WAVを一括再生して字幕境界を失うことはありません。
5. FFmpegの `docichcc` filterが0600のUnix socketからNDJSONを受け、libcaptionでCC1 pop-on字幕へ変換します。`AV_FRAME_DATA_A53_CC` をFFmpegの `ccfifo` でフレームレートに合わせて配り、libx264の `-a53cc 1` がH.264 SEIへ格納します。

```text
Japanese chunks ─┬─> VOICEVOX WAV ───────────────> PulseAudio
                 └─> strict JSON translation ─> 32x2 plan
                                                │
                                    prepare/commit/clear
                                                │ Unix socket
                                                v
X11 video ───────────────────────────────> docichcc ─> libx264 -a53cc 1 ─> local RTMP relay
```

`executionId` と0〜31の連番で発話を識別します。古い発話から遅れて届いた `clear` は `STALE_EXECUTION` で拒否され、新しい字幕を消しません。FFmpeg再起動時はsocketと字幕状態が初期化され、次の発話から自動的に復帰します。

ラジオのrender-only処理は全チャンクが揃った場合だけready WAVとbundleを公開します。前景音声へ合成順を譲った場合や、音声数と字幕チャンク数が一致しない場合は一時成果を公開せず、backoff付きで再試行します。更新前に生成済みでbundleを持たないready WAVだけは、後方互換のため字幕なしの従来経路で再生します。

## 固定ソースとビルド

- FFmpeg `n6.1.1`: `e38092ef9395d7049f871ef4d5411eb410e283e0`
- libcaption `v0.8`: `e8b6261090eb3f2012427cc6b151c923f82453db`（MIT）

Ubuntuでは少なくともC/C++ toolchain、Git、CMake、pkg-config、x264/PulseAudio/GnuTLSに加え、`x11grab` 用のXCB（core、SHM、XFixes、Shape）開発packageを先に用意します。Ubuntu 22.04/24.04では `libx264-dev libpulse-dev libgnutls28-dev libxcb1-dev libxcb-shm0-dev libxcb-xfixes0-dev libxcb-shape0-dev` が対象です。package導入はホスト変更なので、配信停止・再起動とは分けて承認してから行います。

配布用binaryは本番と同じOS世代・CPU architectureでビルドします。`uname -m` と `/etc/os-release` を先に記録し、別architectureの検証binary（例: `x86_64`）をARM64本番（`aarch64`）へ配置しません。`DOCICH_FFMPEG_EXPECT_ARCH` を指定すると、ビルド環境が対象architectureと違う場合に開始前で停止します。

```bash
DOCICH_FFMPEG_EXPECT_ARCH="$(uname -m)" \
DOCICH_FFMPEG_CONFIGURE_ARGS="--enable-libpulse --enable-gnutls" \
  ./native/ffmpeg/build.sh /home/ubuntu/build/docich-cc

/home/ubuntu/build/docich-cc/ffmpeg-install/bin/ffmpeg -hide_banner -filters | grep ' docichcc '
/home/ubuntu/build/docich-cc/ffmpeg-install/bin/ffmpeg -hide_banner -h encoder=libx264 | grep a53cc
```

`build.sh` 自身も終了前に `docichcc`、`x11grab`、PulseAudio input、libx264、`a53cc`、RTMP protocolを検査します。字幕PoCだけ通るが本番のX11/PulseAudio入力を持たない不完全なbinaryは成功扱いにしません。

ビルドスクリプトは上記commitと一致しないsource treeを拒否し、libcaptionのMIT noticeをFFmpegのdocument directoryへ配置します。ローカルの固定英文PoCは外部配信せず、5秒のtest patternだけを作ります。

```bash
DOCICH_CC_FFMPEG_BIN=/home/ubuntu/build/docich-cc/ffmpeg-install/bin/ffmpeg \
  ./native/ffmpeg/poc.sh /tmp/docich-cc-poc.ts
```

成功時は `prepared`、`committed`、`cleared` に加え、A/53 payloadの存在と、埋め込んだ固定英文をlibcaptionで再復号できたことを示す `"verified":true` が出ます。

20回のIPC lifecycleとローカルp95を測る非配信stress PoCもあります。先頭・末尾の英文を再復号して、途中で字幕経路が途切れていないことも確認します。これはTwitch表示の20発話確認を置き換えるものではありません。

```bash
DOCICH_CC_FFMPEG_BIN=/home/ubuntu/build/docich-cc/ffmpeg-install/bin/ffmpeg \
  ./native/ffmpeg/stress_poc.sh /tmp/docich-cc-stress.ts
```

## 設定

カスタムFFmpegを配置しただけでは有効になりません。最初は必ずOFFでvalidateします。

```bash
SOREN_DIRECT_STREAM_FFMPEG_BIN=/home/ubuntu/build/docich-cc/ffmpeg-install/bin/ffmpeg
DOCICH_CC_ENABLED=0
DOCICH_CC_SOCKET=/run/user/1001/docich/ffmpeg-cc.sock
DOCICH_CC_TRANSLATION_URL=http://127.0.0.1:4100/v1/chat/completions
DOCICH_CC_TRANSLATION_MODELS=minimax-m3
DOCICH_CC_TRANSLATION_TIMEOUT_SEC=30
DOCICH_CC_TRANSLATION_ATTEMPTS=3
DOCICH_CC_SOCKET_TIMEOUT_SEC=3
```

翻訳APIにはJSON mode（`response_format: {"type":"json_object"}`）を指定します。`minimax-m3`ではローカルLiteLLM経路の`reasoning_effort=none`も明示し、字幕用途に不要なthinkingが完了トークンを使い切ることを防ぎます。英文は32文字を目標に圧縮し、複数チャンクのラジオでも64文字の絶対上限を超えにくい余裕を取ります。上限を超えた応答は不正応答として再試行します。そのうえで、応答にthinking、説明文、壊れたJSONが含まれる場合は抽出せず破棄します。音声チャンク数には固定上限を設けず、翻訳配列の件数だけが不足・超過した場合は順序付きの利用可能なprefixをそのまま採用し、不足した後半チャンクだけ字幕なしで音声を続行します。FFmpeg側の制御ページは0〜31の32スロットを使うため、32チャンクを超える音声ではsequenceをスロットへ循環割り当てし、各チャンクのcommit後に同じスロットを再利用します。同じモデルへ最大`DOCICH_CC_TRANSLATION_ATTEMPTS`回（1〜5、既定3）新規リクエストを行い、配列が空またはその他の形式不正なら当該発話だけ字幕なしで音声を続行します。接続失敗やtimeoutはこの再試行対象にせず次のモデルへ移るため、1モデルの通信障害で音声開始待ちが試行回数倍に延びることはありません。

`./direct_stream.sh validate --mode live` の `closed_captions_active` が `false` の場合も、映像・音声のpreflight自体は成功できます。これは意図したfail-openです。字幕を試すカナリアだけ `DOCICH_CC_ENABLED=1` にし、validate結果、FFmpeg statusの `closed_captions.active`、socket mode 0600を確認します。

## IPC v1

1接続につき4KiB以下のNDJSONを1件送ります。英文はJSON escape処理をFFmpegへ持ち込まないため `textBase64` で送ります。

```json
{"v":1,"op":"prepare","executionId":"say-123","page":0,"textBase64":"SGVsbG8u"}
{"v":1,"op":"commit","executionId":"say-123","page":0}
{"v":1,"op":"clear","executionId":"say-123"}
{"v":1,"op":"reset"}
```

filterは受理時に `accepted`、該当CC tupleを映像フレームへ注入し終えた時に `prepared` / `committed` / `cleared` / `reset` を返します。socket listenerを作れない場合、filterは警告だけを出して映像を通過させます。

## 実配信の受入ゲート

ローカルPoCだけではIssue完了にしません。本番変更・再起動は別承認を取り、既存のOBS→FFmpeg切替手順とrollbackを維持して次を確認します。

- Twitch playerのCCボタンで英語字幕を表示・非表示できる。
- 固定英文の後、通常の日本語発話20件で意味、順序、32x2表示、終了時clearを確認する。
- 短文・長文・句読点・ASCII化対象を含め、音声チャンクと字幕の取り違えがない。
- `prepare` は音声開始前に完了し、`commit` / `clear` の同期オーバーヘッドp95が500ms以下である。
- 翻訳endpoint停止、thinking混入、socket切断、無効なexecutionIdで、日本語音声と映像が継続する。
- 発話Aの遅延clearが発話Bを消さない。
- FFmpeg/relay再接続後、socketが再生成され、次の発話で字幕が復帰する。
- 30fps、speed、drop/dup、CPU、PulseAudio非無音率が既存の直接配信基準を満たす。

## rollback

字幕だけのrollbackは `.env` の `DOCICH_CC_ENABLED=0` です。カスタムFFmpegを残してもfilterと `-a53cc` はargvへ入りません。サービスreload/restartや本番切替は配信へ影響するため、通常の承認付き運用手順で行います。旧FFmpegへ戻す場合も、先にOFFで正常配信を確認してからbinary pathを戻します。
