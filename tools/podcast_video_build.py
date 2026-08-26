#!/usr/bin/env python3
"""
tools/podcast_video_build.py - ポッドキャストを YouTube 用の動画にする (docich#10)

入力: podcast_build.py が出した output/podcast/<YYYY-MM-DD>.{mp3,segments.json,meta.json,chapters.json}
出力: output/podcast/<YYYY-MM-DD>.mp4 + .thumbnail.png + .description.txt

音声は MP3 をそのまま使う (BGM ミックスと loudnorm 済み)。したがって
ポッドキャストの音と動画の音は完全に同一で、字幕は同じ合成から出た
segments.json のタイミングに一致する。

映像は doci (azumag/doci) の実装を借りる:
  - 字幕描画      doci.compose._render_caption_png / build_subtitles
  - 素材画像      doci.assets.fetch_image (Pexels) / doci.imagegen.generate_image
  - サムネイル    doci.thumbnail.render

使い方:
  ./tools/podcast_video_build.py --date 20260825 [--dry-run] [--scenes 50]
  --dry-run: 画像取得と動画書き出しをせず、シーン割りと字幕数だけ出す

依存: python3, ffmpeg, ffprobe, Pillow, doci
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ROOT_DEFAULT = SCRIPT_DIR / "output" / "podcast"

VIDEO_W = int(os.environ.get("PODCAST_VIDEO_WIDTH", "1920"))
VIDEO_H = int(os.environ.get("PODCAST_VIDEO_HEIGHT", "1080"))
VIDEO_FPS = int(os.environ.get("PODCAST_VIDEO_FPS", "30"))
# 1 シーンの目安秒。53 分なら 65 秒で約 50 シーン (ユーザー選択: 話題単位)
SCENE_SEC = float(os.environ.get("PODCAST_VIDEO_SCENE_SEC", "65"))
SCENE_MIN = int(os.environ.get("PODCAST_VIDEO_SCENE_MIN", "8"))
SCENE_MAX = int(os.environ.get("PODCAST_VIDEO_SCENE_MAX", "60"))
LOGO_FILE = os.environ.get("PODCAST_LOGO_FILE", "")
# 波形。背景を 30fps CFR に揃えた 2 段目へ入れるので待ち合わせが起きない
# (1 段目に画像 concat と同居させるとメモリが膨らむ)。
WAVEFORM = os.environ.get("PODCAST_VIDEO_WAVEFORM", "1") != "0"
WAVE_H = int(os.environ.get("PODCAST_VIDEO_WAVE_H", "150"))
WAVE_Y = int(os.environ.get("PODCAST_VIDEO_WAVE_Y", "925"))
WAVE_ALPHA = os.environ.get("PODCAST_VIDEO_WAVE_ALPHA", "0.5")
WAVE_COLOR = os.environ.get("PODCAST_VIDEO_WAVE_COLOR", "0xE8C87A")
PODCAST_TITLE = os.environ.get("PODCAST_TITLE", "同志のための時事ニュース")
SUB_FONT_SIZE = int(os.environ.get("PODCAST_VIDEO_SUB_SIZE", "54"))
SUB_MARGIN_V = int(os.environ.get("PODCAST_VIDEO_SUB_MARGIN", "90"))


def log(msg: str) -> None:
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr)


def parse_date(arg: str) -> datetime.date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date: {arg}")


# クレジット。VOICEVOX は音声ライブラリごとに表記が必須
# (東北イタコの規約: 動画内または概要欄に「VOICEVOX:東北イタコ」)。
VOICE_CREDIT = os.environ.get("PODCAST_VOICE_CREDIT", "VOICEVOX:東北イタコ")
VOICE_TERMS_URL = os.environ.get(
    "PODCAST_VOICE_TERMS_URL", "https://zunko.jp/con_ongen_kiyaku.html")
BGM_CREDIT = os.environ.get("PODCAST_BGM_CREDIT", "")
EXTRA_CREDIT = os.environ.get("PODCAST_EXTRA_CREDIT", "")


def credit_lines() -> list[str]:
    """概要欄に入れるクレジット。VOICEVOX の表記は規約上必須なので必ず入れる。"""
    out = ["----", "使用素材・クレジット", f"音声合成: {VOICE_CREDIT}"]
    if VOICE_TERMS_URL:
        out.append(f"  利用規約: {VOICE_TERMS_URL}")
    if BGM_CREDIT:
        out.append(f"BGM: {BGM_CREDIT}")
    out.append("画像: Pexels")
    if EXTRA_CREDIT:
        out += [x for x in EXTRA_CREDIT.split("|") if x.strip()]
    return out


def find_doci() -> Path | None:
    cands = []
    if os.environ.get("DOCI_DIR"):
        cands.append(Path(os.environ["DOCI_DIR"]))
    cands += [
        Path("/Users/azumag/azumag/work/doci/repo"),
        Path.home() / "azumag" / "work" / "doci" / "repo",
        Path("/home/ubuntu/doci"),
    ]
    for c in cands:
        if (c / "doci" / "compose.py").exists():
            return c.resolve()
    return None


def probe_dur(path: Path) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)], text=True)
        return float(out.strip())
    except (subprocess.CalledProcessError, ValueError, OSError):
        return 0.0


# ---------------- シーン割り ----------------

_STOP = set("これ それ あれ この その どの ため こと もの よう そして しかし また では ます です ました でした という について による により として ています ている 思います ようです でしょう".split())


def scene_query(texts: list[str]) -> str:
    """シーンの素材を探すためのキーワードを本文から作る。

    固有名詞 (カタカナ語・漢字語) を頻度順に拾う。Pexels は日本語クエリに弱いので
    英語の一般語を添えて外す (strip_brands は doci 側で行う)。
    """
    joined = "".join(texts)
    words: dict[str, int] = {}
    for w in re.findall(r"[ァ-ヴー]{3,}|[一-龥]{2,4}", joined):
        if w in _STOP or len(w) < 2:
            continue
        words[w] = words.get(w, 0) + 1
    top = [w for w, _ in sorted(words.items(), key=lambda x: -x[1])[:3]]
    return " ".join(top) if top else "news"


QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "query": {"type": "string"}},
                "required": ["n", "query"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

QUERY_PROMPT = """次はポッドキャスト番組の各シーンで読み上げられる本文です。
シーンごとに、背景に敷くストックフォトを探すための検索語を英語で作ってください。

要件:
- シーン番号 (n) は入力のとおりに返す。
- query は英語で 2〜4 語。撮影できる具体物・情景にする
  (例: wildfire smoke forest / hospital medical worker / cargo ship port)。
- 抽象語 (society, impact, situation)、単位や数値 (percent, million)、
  商標・人名・企業名は使わない。
- 報道写真ではなく、話題の雰囲気に合う一般的な情景でよい。

シーン:
{scenes}
"""


def scene_queries_via_llm(scenes: list[dict]) -> None:
    """各シーンの検索語を英語で作る。失敗したらキーワード抽出のまま使う。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("pb", SCRIPT_DIR / "tools" / "podcast_build.py")
        pb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pb)
    except Exception as e:
        log(f"検索語生成をスキップ (podcast_build を読めない: {e})")
        return
    body = "\n".join(
        f"{i + 1}. [{sc['section']}] " + "".join(sc["texts"])[:280]
        for i, sc in enumerate(scenes)
    )
    obj = pb.llm_generate(QUERY_PROMPT.format(scenes=body), schema=QUERY_SCHEMA)
    if not obj:
        log("検索語生成に失敗、キーワード抽出のまま続行")
        return
    got = 0
    for it in obj.get("items") or []:
        try:
            n = int(it.get("n")) - 1
        except (TypeError, ValueError):
            continue
        q = str(it.get("query") or "").strip()
        if q and 0 <= n < len(scenes):
            scenes[n]["query"] = q
            got += 1
    log(f"検索語を英語で生成: {got}/{len(scenes)} シーン")


def build_scenes(segments: list[dict], total: float, target_sec: float) -> list[dict]:
    """文の境界を割らずに、目安秒ごとのシーンへまとめる。"""
    n = max(SCENE_MIN, min(SCENE_MAX, round(total / target_sec) or 1))
    per = total / n
    scenes: list[dict] = []
    cur: list[dict] = []
    start = 0.0
    for seg in segments:
        cur.append(seg)
        if seg["end"] - start >= per and len(scenes) < n - 1:
            scenes.append({"start": start, "end": seg["end"],
                           "texts": [s["text"] for s in cur],
                           "section": cur[0].get("section", "")})
            start = seg["end"]
            cur = []
    if cur:
        scenes.append({"start": start, "end": total,
                       "texts": [s["text"] for s in cur],
                       "section": cur[0].get("section", "")})
    for sc in scenes:
        sc["query"] = scene_query(sc["texts"])
        sc["dur"] = round(sc["end"] - sc["start"], 3)
    return scenes


# ---------------- 字幕 (ASS) ----------------

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def write_ass(caps: list[tuple[str, float, float]], path: Path, font: str) -> Path:
    """字幕を ASS で書き出す。

    透過 PNG を 1 本のトラックに連結して overlay する方式は、背景が 1 枚 69 秒
    保持されるのに対し字幕トラックが 30fps で進むため、overlay の待ち合わせで
    大量のフレームがバッファされる (実測で書き出しが進まずメモリが膨張した)。
    libass はタイミングを自前で持つのでフレームを溜めない。
    """
    head = [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {VIDEO_W}", f"PlayResY: {VIDEO_H}",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # BorderStyle=3 で背景ボックス、Alignment=2 で下中央
        f"Style: Def,{font},{SUB_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00101010,&H96101010,"
        f"1,0,0,0,100,100,0,0,3,3,0,2,80,80,{SUB_MARGIN_V},1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = []
    for text, st, en in caps:
        body = text.replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},Def,,0,0,0,,{body}")
    path.write_text("\n".join(head + lines) + "\n", encoding="utf-8")
    return path


def pick_font() -> str:
    """libass に渡す日本語フォント名。実在するものを選ぶ。"""
    override = os.environ.get("PODCAST_VIDEO_FONT", "").strip()
    if override:
        return override
    try:
        out = subprocess.check_output(["fc-list", ":lang=ja", "family"], text=True)
        fams = {f.split(",")[0].strip() for f in out.splitlines() if f.strip()}
        for want in ("Hiragino Sans", "ヒラギノ角ゴシック", "Hiragino Kaku Gothic ProN",
                     "Noto Sans CJK JP", "YuGothic"):
            if want in fams:
                return want
        if fams:
            return sorted(fams)[0]
    except (subprocess.CalledProcessError, OSError):
        pass
    return "Hiragino Sans"


# ---------------- 素材 ----------------

def make_logo(path: Path) -> Path | None:
    """番組ロゴが無ければ、タイトルから簡素なロゴを描いて作る。"""
    if LOGO_FILE and Path(LOGO_FILE).exists():
        return Path(LOGO_FILE)
    cand = SCRIPT_DIR / "assets" / "podcast_logo.png"
    if cand.exists():
        return cand
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log("Pillow が無いためロゴを省く")
        return None
    font = None
    for f in ("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
              "/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/Library/Fonts/Arial Unicode.ttf"):
        if Path(f).exists():
            try:
                font = ImageFont.truetype(f, 34)
                break
            except OSError:
                continue
    if font is None:
        log("日本語フォントが見つからないためロゴを省く")
        return None
    img = Image.new("RGBA", (560, 92), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 559, 91], radius=14, fill=(140, 20, 20, 200))
    d.text((28, 26), PODCAST_TITLE, font=font, fill=(255, 235, 200, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def fetch_scene_image(doci_dir: Path, sc: dict, out: Path, idx: int) -> Path | None:
    sys.path.insert(0, str(doci_dir))
    try:
        from doci import assets
    except ImportError as e:
        log(f"doci.assets を読めない: {e}")
        return None
    try:
        got = assets.fetch_image(sc["query"], out, width=VIDEO_W, height=VIDEO_H,
                                 orientation="landscape", variant=idx)
    except Exception as e:  # 素材が無い日もあるので致命傷にしない
        log(f"  シーン{idx + 1} 素材取得に失敗 ({sc['query']}): {e}")
        return None
    return got


def crop_caps(caps: list) -> tuple[int, int, int, int]:
    """字幕 PNG を、実際に描かれている帯だけに切り詰める。

    全画面 (1920x1080 RGBA = 8.3MB/frame) のまま overlay すると、
    30fps のトラックがフィルタ内に積まれてメモリを食う (実測 3.8GB)。
    字幕が占めるのは高さの 2 割程度なので、共通の外接矩形で切って
    その位置へ overlay する。戻り値は (x, y, w, h)。
    """
    if not caps:
        return (0, 0, VIDEO_W, VIDEO_H)
    try:
        from PIL import Image
    except ImportError:
        return (0, 0, VIDEO_W, VIDEO_H)
    x0, y0, x1, y1 = VIDEO_W, VIDEO_H, 0, 0
    for png, _, _ in caps:
        with Image.open(png) as im:
            b = im.getbbox()
        if not b:
            continue
        x0, y0 = min(x0, b[0]), min(y0, b[1])
        x1, y1 = max(x1, b[2]), max(y1, b[3])
    if x1 <= x0 or y1 <= y0:
        return (0, 0, VIDEO_W, VIDEO_H)
    # 偶数に揃える (yuv 変換とスケーラの都合)
    x0, y0 = x0 - (x0 % 2), y0 - (y0 % 2)
    w, h = (x1 - x0 + 1) // 2 * 2, (y1 - y0 + 1) // 2 * 2
    for png, _, _ in caps:
        with Image.open(png) as im:
            im.crop((x0, y0, x0 + w, y0 + h)).save(png)
    log(f"字幕を {w}x{h} に切り詰め (全画面比 {w * h * 100 // (VIDEO_W * VIDEO_H)}%, "
        f"位置 {x0},{y0})")
    return (x0, y0, w, h)


def solid_bg(path: Path, idx: int) -> Path:
    """素材が取れなかったシーン用の単色背景。"""
    shades = ["#2b1b1b", "#231d2b", "#1b2b25", "#2b271b", "#1b222b"]
    c = shades[idx % len(shades)]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c={c}:s={VIDEO_W}x{VIDEO_H}", "-frames:v", "1", str(path)],
                   check=False)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--out-dir", default=str(OUTPUT_ROOT_DEFAULT))
    ap.add_argument("--dry-run", action="store_true", help="シーン割りと字幕数だけ出す")
    ap.add_argument("--scenes", type=int, help="シーン数を明示する")
    ap.add_argument("--limit-sec", type=float, help="先頭 N 秒だけ書き出す (検証用)")
    args = ap.parse_args()

    date = parse_date(args.date) if args.date else datetime.date.today() - datetime.timedelta(days=1)
    iso = date.isoformat()
    out_dir = Path(args.out_dir)

    mp3 = out_dir / f"{iso}.mp3"
    seg_file = out_dir / f"{iso}.segments.json"
    meta_file = out_dir / f"{iso}.meta.json"
    chap_file = out_dir / f"{iso}.chapters.json"
    for f in (mp3, seg_file):
        if not f.exists():
            log(f"必要なファイルが無い: {f} (先に podcast_build.py を実行してください)")
            return 2

    segments = json.loads(seg_file.read_text(encoding="utf-8"))
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    total = probe_dur(mp3)
    if args.limit_sec:
        total = min(total, args.limit_sec)
        segments = [x for x in segments if x["start"] < total]
        log(f"検証モード: 先頭 {total:.0f} 秒だけ書き出す")
    log(f"音声 {total:.1f}秒 / 字幕 {len(segments)} 文 / 「{meta.get('title', '')}」")

    target = (total / args.scenes) if args.scenes else SCENE_SEC
    scenes = build_scenes(segments, total, target)
    log(f"シーン {len(scenes)} 枚 (1 枚あたり平均 {total / len(scenes):.0f} 秒)")
    scene_queries_via_llm(scenes)
    for i, sc in enumerate(scenes[:5]):
        log(f"  {i + 1}: {sc['start']:.0f}-{sc['end']:.0f}s [{sc['section']}] query={sc['query']}")
    if len(scenes) > 5:
        log(f"  ... 他 {len(scenes) - 5} 枚")

    # 説明欄 (チャプター + クレジット)
    desc = [meta.get("summary", ""), ""]
    if chap_file.exists():
        desc.append("チャプター")
        for c in json.loads(chap_file.read_text(encoding="utf-8"))["chapters"]:
            s = int(c["startTime"])
            desc.append(f"{s // 60}:{s % 60:02d} {c['title']}")
        desc.append("")
    desc += credit_lines()
    desc_path = out_dir / f"{iso}.description.txt"

    if args.dry_run:
        log("dry-run: 素材取得と書き出しはしない")
        log("--- 説明欄 ---")
        print("\n".join(desc))
        return 0

    desc_path.write_text("\n".join(desc) + "\n", encoding="utf-8")
    log(f"description written: {desc_path}")

    doci_dir = find_doci()
    if not doci_dir:
        log("doci が見つからない (DOCI_DIR を設定してください)")
        return 2
    log(f"doci: {doci_dir}")
    return build_video(doci_dir, scenes, segments, mp3, total, out_dir, iso, meta)


def build_video(doci_dir: Path, scenes: list[dict], segments: list[dict],
                mp3: Path, total: float, out_dir: Path, iso: str, meta: dict) -> int:
    sys.path.insert(0, str(doci_dir))
    try:
        from doci import compose as dcompose
        from doci.channel import StyleSpec
    except ImportError as e:
        log(f"doci を読み込めない: {e}")
        return 2

    out_mp4 = out_dir / f"{iso}.mp4"
    style = StyleSpec()

    with tempfile.TemporaryDirectory(prefix="podcast_video_") as td:
        tmp = Path(td)

        # 1. シーン画像
        imgs: list[Path] = []
        for i, sc in enumerate(scenes):
            p = tmp / f"scene_{i:03d}.jpg"
            got = fetch_scene_image(doci_dir, sc, p, i)
            if got and Path(got).exists():
                imgs.append(Path(got))
            else:
                imgs.append(solid_bg(tmp / f"scene_{i:03d}.png", i))
            if (i + 1) % 10 == 0:
                log(f"  素材 {i + 1}/{len(scenes)}")
        log(f"素材 {len(imgs)} 枚そろった")

        # 2. 字幕: doci のチャンク分割と折り返しを使い、ASS で出す
        class _Seg:
            __slots__ = ("text", "start", "end")

            def __init__(self, d):
                self.text, self.start, self.end = d["text"], d["start"], d["end"]

        # この環境の ffmpeg 8.0.1 には libass が無く subtitles/ass フィルタを使えない
        # (実測: "No such filter: 'subtitles'")。doci と同じ透過 PNG 方式で描く。
        # doci のチャンク上限は 9:16 前提 (13字x2行=26字)。16:9 では 1 行が
        # 幅比で 23 字へ広がるため、26 字のままだと語の途中で切れる
        # (実測: 「夜も気温が下がりにくくな」で途切れた)。画面比に合わせて広げる。
        per_line = (dcompose.SUB_LINE_CHARS if VIDEO_W <= VIDEO_H
                    else max(dcompose.SUB_LINE_CHARS,
                             round(dcompose.SUB_LINE_CHARS * VIDEO_W / VIDEO_H)))
        dcompose.SUB_MAX_CHARS = per_line * dcompose.SUB_MAX_LINES
        log(f"字幕チャンク上限を {dcompose.SUB_MAX_CHARS} 字へ (1 行 {per_line} 字 x "
            f"{dcompose.SUB_MAX_LINES} 行)")

        caps: list[tuple[Path, float, float]] = []
        for text, st, en in dcompose.build_subtitles([_Seg(d) for d in segments]):
            png = tmp / f"cap_{len(caps):04d}.png"
            if dcompose._render_caption_png(text, png, VIDEO_W, VIDEO_H, style):
                caps.append((png, st, en))
        log(f"字幕 {len(caps)} 枚 (PNG)")
        if not caps:
            log("字幕を 1 枚も描けなかった (Pillow / 日本語フォントを確認)")
        cap_off = crop_caps(caps)

        # 3. 背景スライドショー
        concat = tmp / "bg.txt"
        with open(concat, "w", encoding="utf-8") as f:
            for img, sc in zip(imgs, scenes):
                f.write(f"file '{Path(img).resolve()}'\nduration {sc['dur']:.3f}\n")
            f.write(f"file '{Path(imgs[-1]).resolve()}'\n")

        logo = make_logo(tmp / "logo.png")
        return render(tmp, concat, caps, cap_off, mp3, total, out_mp4, logo)


def render(tmp: Path, concat: Path, caps: list, cap_off: tuple, mp3: Path,
           total: float, out_mp4: Path, logo: Path | None) -> int:
    """背景 -> (ロゴ) -> 字幕 -> 音声。

    メモリ対策として 2 段に分ける:
      1. 背景スライドショーを 30fps CFR の中間ファイルへ (入力 1 本)
      2. その中間ファイルへ字幕 PNG トラックを重ねる

    1 段で済ませると、背景 (画像 1 枚を 69 秒保持 = 実質 0.014fps) と
    字幕トラック (30fps) のフレームレート差により overlay が遅い側を待ち、
    速い側のフレームを大量にバッファする (実測で数 GB / frame=0 のまま停止)。
    背景を先に 30fps へ揃えるとこの待ち合わせが起きない。
    """
    bg_mp4 = tmp / "bg.mp4"
    vf = (f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
          f"crop={VIDEO_W}:{VIDEO_H},setsar=1,eq=brightness=-0.14:saturation=0.85")
    cmd1 = ["ffmpeg", "-y", "-v", "error", "-stats",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", vf, "-r", str(VIDEO_FPS), "-fps_mode", "cfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-pix_fmt", "yuv420p", "-t", f"{total:.3f}", str(bg_mp4)]
    log("背景スライドショーを書き出し")
    if subprocess.run(cmd1).returncode != 0:
        log("背景の書き出しに失敗")
        return 2
    log(f"背景 done ({bg_mp4.stat().st_size} bytes, {probe_dur(bg_mp4):.1f}s)")

    inputs = ["-i", str(bg_mp4), "-i", str(mp3)]
    filt = []
    last = "0:v"
    idx = 2
    amap = "1:a"

    if WAVEFORM:
        # 音声を 2 系統に分け、片方を波形にする。背景と同じ 30fps で出すので
        # overlay がフレームを溜めない。字幕帯 (y 690-920) の下に置く。
        filt.append("[1:a]asplit=2[aout][awav]")
        filt.append(f"[awav]showwaves=s={VIDEO_W}x{WAVE_H}:mode=cline:rate={VIDEO_FPS}:"
                    f"colors={WAVE_COLOR},format=rgba,"
                    f"colorchannelmixer=aa={WAVE_ALPHA}[wf]")
        filt.append(f"[{last}][wf]overlay=0:{WAVE_Y}:eof_action=pass[bgw]")
        last, amap = "bgw", "[aout]"

    if logo and logo.exists():
        inputs += ["-i", str(logo)]
        filt.append(f"[{last}][{idx}:v]overlay=48:44[lg]")
        last, idx = "lg", idx + 1

    if caps:
        # 字幕は時間順に 1 本の透過トラックへ連結し、fps を背景に揃えてから
        # 1 回だけ overlay する (字幕本数によらず overlay は 1 段)。
        cx, cy, cw, ch = cap_off
        blank = tmp / "blank.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c=black@0.0:s={cw}x{ch},format=rgba",
                        "-frames:v", "1", str(blank)], check=False)
        cap_list = tmp / "caps.txt"
        with open(cap_list, "w", encoding="utf-8") as f:
            t = 0.0
            for png, st, en in caps:
                if st - t > 0.04:
                    f.write(f"file '{blank.resolve()}'\nduration {st - t:.3f}\n")
                f.write(f"file '{Path(png).resolve()}'\nduration {max(en - st, 0.05):.3f}\n")
                t = en
            if total - t > 0.04:
                f.write(f"file '{blank.resolve()}'\nduration {total - t:.3f}\n")
            f.write(f"file '{blank.resolve()}'\n")
        inputs += ["-f", "concat", "-safe", "0", "-i", str(cap_list)]
        filt.append(f"[{idx}:v]fps={VIDEO_FPS},format=rgba,setsar=1[cap]")
        filt.append(f"[{last}][cap]overlay={cx}:{cy}:eof_action=pass[vout]")
        last = "vout"

    if filt:
        graph = ["-filter_complex", ";".join(filt), "-map", f"[{last}]"]
    else:
        graph = ["-map", "0:v"]
    cmd2 = (["ffmpeg", "-y", "-v", "error", "-stats"] + inputs + graph
            + ["-map", amap, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", "-t", f"{total:.3f}", str(out_mp4)])
    log(f"字幕{'・波形' if WAVEFORM else ''}と音声を合成 -> {out_mp4}")
    if subprocess.run(cmd2).returncode != 0:
        log("合成に失敗")
        return 2
    log(f"mp4 done: {out_mp4} ({out_mp4.stat().st_size} bytes, {probe_dur(out_mp4):.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
