# VOICEVOX 歌声合成 楽譜JSON リファレンス

## 楽譜JSON形式

```json
{
  "notes": [
    {"key": null, "frame_length": 15, "lyric": ""},
    {"key": 60, "frame_length": 45, "lyric": "ド"},
    {"key": 62, "frame_length": 45, "lyric": "レ"},
    ...
    {"key": null, "frame_length": 15, "lyric": ""}
  ]
}
```

## フィールド説明

- `key`: MIDIノート番号。null = 無音（休符）
- `frame_length`: フレーム数。93.75Hz なので 45フレーム ≈ 0.48秒（四分音符相当）
- `lyric`: 1モーラ（ひらがな1文字）。無音ノートは空文字 ""

## frame_length の目安

| 音価 | frame_length | 実時間 |
|------|-------------|--------|
| 八分音符 | 23 | ≈ 0.25秒 |
| 四分音符 | 45 | ≈ 0.48秒 |
| 付点四分 | 68 | ≈ 0.73秒 |
| 二分音符 | 90 | ≈ 0.96秒 |
| 全音符 | 180 | ≈ 1.92秒 |
| 短い休符 | 10 | ≈ 0.11秒 |
| 通常休符 | 15 | ≈ 0.16秒 |

## MIDIノート番号 → 音名

| 音名 | ド | レ | ミ | ファ | ソ | ラ | シ |
|------|----|----|----|----|----|----|-----|
| C3(低) | 48 | 50 | 52 | 53 | 55 | 57 | 59 |
| C4(中) | 60 | 62 | 64 | 65 | 67 | 69 | 71 |
| C5(高) | 72 | 74 | 76 | 77 | 79 | 81 | 83 |

半音: ド#=61, レ#=63, ファ#=66, ソ#=68, ラ#=70

## 重要ルール

1. **先頭と末尾に必ず無音ノート**を入れる: `{"key": null, "frame_length": 15, "lyric": ""}`
2. **lyricは1モーラずつ**分割する（例: 「きら」→ 「き」「ら」の2ノート）
3. **フレーズ間に短い無音**を入れると自然: `{"key": null, "frame_length": 10, "lyric": ""}`
4. 長く伸ばす音は frame_length を大きくする（二分音符=90、全音符=180）
5. ノート数が多すぎると合成に時間がかかるため、1曲50〜80ノート程度を目安に

## 収録曲の完全な楽譜例（そのまま使ってよい）

リクエストされた曲が下記にあれば、対応する楽譜JSONをそのまま（または前後の挨拶文だけ変えて）出力します。

### きらきら星（ド ド ソ ソ ラ ラ ソー）
```
C4 C4 G4 G4 A4 A4 G4(長) | F4 F4 E4 E4 D4 D4 C4(長)
```
```json
{"notes":[{"key":null,"frame_length":15,"lyric":""},{"key":60,"frame_length":45,"lyric":"き"},{"key":60,"frame_length":45,"lyric":"ら"},{"key":67,"frame_length":45,"lyric":"き"},{"key":67,"frame_length":45,"lyric":"ら"},{"key":69,"frame_length":45,"lyric":"ひ"},{"key":69,"frame_length":45,"lyric":"か"},{"key":67,"frame_length":90,"lyric":"る"},{"key":null,"frame_length":10,"lyric":""},{"key":65,"frame_length":45,"lyric":"お"},{"key":65,"frame_length":45,"lyric":"そ"},{"key":64,"frame_length":45,"lyric":"ら"},{"key":64,"frame_length":45,"lyric":"の"},{"key":62,"frame_length":45,"lyric":"ほ"},{"key":62,"frame_length":45,"lyric":"し"},{"key":60,"frame_length":90,"lyric":"よ"},{"key":null,"frame_length":15,"lyric":""}]}
```

### ちょうちょう（ソ ミ ミー ファ レ レー）
```
G4 E4 E4(長) | F4 D4 D4(長) | C4 D4 E4 F4 | G4 G4 G4(長)
```
```json
{"notes":[{"key":null,"frame_length":15,"lyric":""},{"key":67,"frame_length":45,"lyric":"ちょ"},{"key":64,"frame_length":45,"lyric":"ちょ"},{"key":64,"frame_length":90,"lyric":"う"},{"key":null,"frame_length":10,"lyric":""},{"key":65,"frame_length":45,"lyric":"ちょ"},{"key":62,"frame_length":45,"lyric":"ちょ"},{"key":62,"frame_length":90,"lyric":"う"},{"key":null,"frame_length":10,"lyric":""},{"key":60,"frame_length":45,"lyric":"な"},{"key":62,"frame_length":45,"lyric":"の"},{"key":64,"frame_length":45,"lyric":"ば"},{"key":65,"frame_length":45,"lyric":"に"},{"key":null,"frame_length":10,"lyric":""},{"key":67,"frame_length":45,"lyric":"と"},{"key":67,"frame_length":45,"lyric":"ん"},{"key":67,"frame_length":90,"lyric":"ぼ"},{"key":null,"frame_length":15,"lyric":""}]}
```

### メリーさんの羊（ミ レ ド レ ミ ミー）
```
E4 D4 C4 D4 E4 E4(長) | D4 D4 D4(長) | E4 G4 G4(長) | E4 D4 C4 D4 E4 E4(長)
```
```json
{"notes":[{"key":null,"frame_length":15,"lyric":""},{"key":64,"frame_length":45,"lyric":"め"},{"key":62,"frame_length":45,"lyric":"り"},{"key":60,"frame_length":45,"lyric":"い"},{"key":62,"frame_length":45,"lyric":"さ"},{"key":64,"frame_length":45,"lyric":"ん"},{"key":64,"frame_length":90,"lyric":"の"},{"key":null,"frame_length":10,"lyric":""},{"key":62,"frame_length":45,"lyric":"ひ"},{"key":62,"frame_length":45,"lyric":"つ"},{"key":62,"frame_length":90,"lyric":"じ"},{"key":null,"frame_length":10,"lyric":""},{"key":64,"frame_length":45,"lyric":"め"},{"key":67,"frame_length":45,"lyric":"ぐ"},{"key":67,"frame_length":90,"lyric":"る"},{"key":null,"frame_length":10,"lyric":""},{"key":64,"frame_length":45,"lyric":"め"},{"key":62,"frame_length":45,"lyric":"り"},{"key":60,"frame_length":45,"lyric":"い"},{"key":62,"frame_length":45,"lyric":"さ"},{"key":64,"frame_length":45,"lyric":"ん"},{"key":64,"frame_length":90,"lyric":"の"},{"key":null,"frame_length":15,"lyric":""}]}
```

### かえるのうた（ド レ ミ ファ ミ レ ドー）
```
C4 D4 E4 F4 E4 D4 C4(長) | E4 F4 G4 A4 G4 F4 E4(長)
```
```json
{"notes":[{"key":null,"frame_length":15,"lyric":""},{"key":60,"frame_length":45,"lyric":"け"},{"key":62,"frame_length":45,"lyric":"ろ"},{"key":64,"frame_length":45,"lyric":"け"},{"key":65,"frame_length":45,"lyric":"ろ"},{"key":64,"frame_length":45,"lyric":"け"},{"key":62,"frame_length":45,"lyric":"ろ"},{"key":60,"frame_length":90,"lyric":"け"},{"key":null,"frame_length":10,"lyric":""},{"key":64,"frame_length":45,"lyric":"あ"},{"key":65,"frame_length":45,"lyric":"わ"},{"key":67,"frame_length":45,"lyric":"せ"},{"key":69,"frame_length":45,"lyric":"て"},{"key":67,"frame_length":45,"lyric":"か"},{"key":65,"frame_length":45,"lyric":"え"},{"key":64,"frame_length":90,"lyric":"る"},{"key":null,"frame_length":15,"lyric":""}]}
```

### ハッピーバースデー（ソ ソ ラ ソ ド シー）
```
G4 G4 A4 G4 C5 B4(長) | G4 G4 A4 G4 D5 C5(長)
```
```json
{"notes":[{"key":null,"frame_length":15,"lyric":""},{"key":67,"frame_length":45,"lyric":"お"},{"key":67,"frame_length":45,"lyric":"め"},{"key":69,"frame_length":45,"lyric":"で"},{"key":67,"frame_length":45,"lyric":"と"},{"key":72,"frame_length":45,"lyric":"う"},{"key":71,"frame_length":90,"lyric":"う"},{"key":null,"frame_length":10,"lyric":""},{"key":67,"frame_length":45,"lyric":"あ"},{"key":67,"frame_length":45,"lyric":"り"},{"key":69,"frame_length":45,"lyric":"が"},{"key":67,"frame_length":45,"lyric":"と"},{"key":74,"frame_length":45,"lyric":"う"},{"key":72,"frame_length":90,"lyric":"う"},{"key":null,"frame_length":15,"lyric":""}]}
```

## リクエストへの応え方

1. リクエストされた曲が上記にあれば、その楽譜JSONを使って歌う
2. 上記にない曲でも単純なメロディなら自作してよい（音域は C4 〜 D5、ノート数は上記例程度〜80以内）
3. 曲の指定がない・知らない・難しい場合は、上記の中から好きな曲を選ぶ。**毎回同じ曲（きらきら星など）にせず、曲を変えること**

## 出力例

「歌って」と言われたら、トーク本文の後に以下の形式で楽譜JSONを出力してください:

```
歌ってみます。ちょうちょうをどうぞ。
===SING===
{"notes":[{"key":null,"frame_length":15,"lyric":""},{"key":67,"frame_length":45,"lyric":"ちょ"},{"key":64,"frame_length":45,"lyric":"ちょ"},{"key":64,"frame_length":90,"lyric":"う"},{"key":null,"frame_length":10,"lyric":""},{"key":65,"frame_length":45,"lyric":"ちょ"},{"key":62,"frame_length":45,"lyric":"ちょ"},{"key":62,"frame_length":90,"lyric":"う"},{"key":null,"frame_length":10,"lyric":""},{"key":60,"frame_length":45,"lyric":"な"},{"key":62,"frame_length":45,"lyric":"の"},{"key":64,"frame_length":45,"lyric":"ば"},{"key":65,"frame_length":45,"lyric":"に"},{"key":null,"frame_length":10,"lyric":""},{"key":67,"frame_length":45,"lyric":"と"},{"key":67,"frame_length":45,"lyric":"ん"},{"key":67,"frame_length":90,"lyric":"ぼ"},{"key":null,"frame_length":15,"lyric":""}]}
===SING===
```

歌唱リクエストには必ず ===SING=== を出力すること（テキストのみの返事は不可）。
