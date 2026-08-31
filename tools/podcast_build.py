#!/usr/bin/env python3
"""
tools/podcast_build.py - 日次ポッドキャスト生成 (docich#10 / soviet_now#113)

入力: backups/radio_scripts/<YYYYMMDD>/*.txt のうち news/jiji のみ
処理: その日の原稿群を LLM で 1 本の番組台本へ「編成し直し」てから合成する。
      素材をそのまま連結すると 3〜5 時間の朗読になり、番組として成立しないため
      (実測 08-19〜08-25: 2h43m〜5h24m、平均 4h。VOICEVOX 実測 306 字/分)。
出力: output/podcast/<YYYY-MM-DD>.mp3 + feed.xml + chapters.json + meta.json

使い方:
  ./tools/podcast_build.py --date 20260824 [--dry-run] [--out-dir output/podcast] [--voice 109]
  --date: YYYYMMDD または YYYY-MM-DD (無指定は昨日)
  --dry-run: 合成・MP3生成をスキップし、対象ファイルとRSSのみ表示
  --dummy: VOICEVOXが無い環境で無音WAVを生成 (テスト用)

依存: python3, ffmpeg, ffprobe, voicevox_tts.sh (VOICEVOX) or docich voicevox synth
任意: rclone/oci (アップロード時)

RSSは RSS 2.0 + iTunes 拡張。feed.xml は冪等に更新 (最新50件)。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

try:
    from news_topic_filter import is_low_value_news_title
    from sports_filter import is_sports_title
except Exception:
    # The source-side filter remains authoritative.  A packaging error in this
    # defense-in-depth layer must not make every podcast source disappear.
    def is_low_value_news_title(_text: str) -> bool:
        return False

    def is_sports_title(_text: str) -> bool:
        return False


# 原稿の探索: 環境変数 > Macのクローン (soren-radio-archive) > VMローカルの backups
# short_video_build.py の _backup_root() と同じ規約 (docich#10 Mac移行)
def _backup_root() -> Path:
    for env in ("SOREN_RADIO_ARCHIVE", "RADIO_ARCHIVE_DIR", "RADIO_ARCHIVE_GIT_DIR"):
        v = os.environ.get(env)
        if not v:
            continue
        p = Path(v)
        # RADIO_ARCHIVE_GIT_DIR は tmp/radio_archive_mirror を指すことがあるので backups へ正規化
        if p.name == "radio_archive_mirror":
            p = p.parent / "backups" / "radio_scripts"
        elif (p / "backups" / "radio_scripts").exists():
            p = p / "backups" / "radio_scripts"
        elif p.name == "soren-radio-archive":
            p = p / "backups" / "radio_scripts"
        if p.exists():
            return p
        if p.name == "radio_scripts":
            return p
    # VM では SCRIPT_DIR/backups が実データ。Mac にはこれが無いのでミラーを見る。
    for cand in (SCRIPT_DIR / "backups" / "radio_scripts",
                 Path.home() / "soren-radio-archive" / "backups" / "radio_scripts"):
        if cand.exists():
            return cand
    return SCRIPT_DIR / "backups" / "radio_scripts"


BACKUP_ROOT = _backup_root()
OUTPUT_ROOT_DEFAULT = SCRIPT_DIR / "output" / "podcast"
VOICEVOX_TTS = SCRIPT_DIR / "voicevox_tts.sh"
TEMPO_MAP = SCRIPT_DIR / "config" / "voicevox_tempo_map.txt"


def voice_tempo(speaker: str) -> str:
    """話速を返す。ポッドキャストは 1.2 (ユーザー指定)。

    PODCAST_VOICE_TEMPO を空にすると VM と同じ config/voicevox_tempo_map.txt に
    追従する (話者 109 は VM では 1.15)。配信側の話速はここでは変えない。
    """
    override = os.environ.get("PODCAST_VOICE_TEMPO", "1.2").strip()
    if override:
        return override
    try:
        for line in TEMPO_MAP.read_text(encoding="utf-8").splitlines():
            key, _, val = line.partition("|")
            if key.strip() == str(speaker) and val.strip():
                return val.strip()
    except OSError:
        pass
    return "1.0"


# BGM: インターナショナルを地の下に薄く流す。見つからなければ BGM 無しで続行する。
# コーナーの区切り。無音だけだと BGM が鳴り続けるぶん切れ目が埋もれるため、
# 「続いては、〇〇。」と見出しを読み上げて区切る (聴き比べて決定)。
PODCAST_GAP_SEC = float(os.environ.get("PODCAST_GAP_SEC", "2.6"))
PODCAST_GAP_AFTER_HEAD_SEC = float(os.environ.get("PODCAST_GAP_AFTER_HEAD_SEC", "0.5"))
PODCAST_ANNOUNCE_HEADING = os.environ.get("PODCAST_ANNOUNCE_HEADING", "1") != "0"

PODCAST_BGM_FILE = os.environ.get("PODCAST_BGM_FILE", "")
# 実測: 0.10 で BGM 単体 -39.5dB (語り -16.4dB より 23dB 下、ほぼ聞こえない)、
# 0.18 で -34.4dB。0.15 は約 20dB 下で、一般的な bed music の水準。
PODCAST_BGM_VOLUME = os.environ.get("PODCAST_BGM_VOLUME", "0.15")


def find_bgm() -> Path | None:
    cands = []
    if PODCAST_BGM_FILE:
        cands.append(Path(PODCAST_BGM_FILE))
    cands += [
        SCRIPT_DIR / "assets" / "bgm" / "internationale_piano.mp3",
        Path("/Users/azumag/azumag/work/doci/repo/channels/ideology/bgm/internationale_piano.mp3"),
        Path.home() / "azumag" / "work" / "doci" / "repo" / "channels" / "ideology" / "bgm" / "internationale_piano.mp3",
        Path.home() / "work" / "doci" / "channels" / "ideology" / "bgm" / "internationale_piano.mp3",
    ]
    for c in cands:
        if c.exists():
            return c
    return None

# 設定 (環境変数で上書き可)
PODCAST_TITLE = os.environ.get("PODCAST_TITLE", "同志のための時事ニュース")
PODCAST_LINK = os.environ.get("PODCAST_LINK", "https://github.com/azumag/soren-radio-archive")
PODCAST_DESCRIPTION = os.environ.get("PODCAST_DESCRIPTION", "その日の時事ニュースと考察を1日1本にまとめてお届けします。VOICEVOX:東北イタコ")
PODCAST_AUTHOR = os.environ.get("PODCAST_AUTHOR", "Soren Radio")
PODCAST_EMAIL = os.environ.get("PODCAST_EMAIL", "archive@soren.local")
PODCAST_IMAGE = os.environ.get("PODCAST_IMAGE", "https://example.com/podcast/cover.jpg")
PODCAST_CATEGORY = os.environ.get("PODCAST_CATEGORY", "News")
PODCAST_BASE_URL = os.environ.get("PODCAST_BASE_URL", "https://example.com/podcast")
PODCAST_LANGUAGE = os.environ.get("PODCAST_LANGUAGE", "ja")

def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr)

def parse_date(arg: str) -> datetime.date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date: {arg} (expected YYYYMMDD or YYYY-MM-DD)")

def collect_files(date: datetime.date) -> list[Path]:
    root = _backup_root()
    yyyymmdd = date.strftime("%Y%m%d")
    pattern = str(root / yyyymmdd / "radio_*.txt")
    if not hasattr(collect_files, "_logged"):
        log(f"backup_root: {root} (exists={root.exists()})")
        collect_files._logged = True
    files = [Path(p) for p in glob.glob(pattern)]
    # news/jiji のみ
    filtered = []
    for f in files:
        name = f.name
        # radio_<ts>_<game>_<corner>_<rand>.txt
        m = re.search(r"_([a-z_]+)_\d+\.txt$", name)
        corner = m.group(1) if m else ""
        # corner が news / jiji のみ対象 (テーマ等は除外)
        if corner in ("news", "jiji"):
            if corner == "news" and _exclude_news_source_from_podcast(f):
                log(f"podcast topic excluded: {f.name}")
                continue
            filtered.append(f)
        else:
            # 旧ファイルで corner が不明でも、内容に「ニュース」等があれば拾う? 今回は厳格に news/jiji のみ
            pass
    filtered.sort()
    return filtered


def _exclude_news_source_from_podcast(path: Path) -> bool:
    """Defense in depth for sports, entertainment and product-promo scripts.

    The radio selector normally rejects these before generation.  The podcast
    archive can still contain material generated by an older worker or stale
    cache, so inspect the opening portion where the selected headline/topic is
    introduced.  Do not use the whole script: later analysis may mention an
    unrelated film, sport or product as an analogy.
    """
    if os.environ.get("PODCAST_NEWS_TOPIC_FILTER_DISABLED", "0") == "1":
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    opening = clean_script(text)[:700]
    return is_sports_title(opening) or is_low_value_news_title(opening)

def clean_script(text: str) -> str:
    """ポッドキャスト用に時報イントロを除去し、不要な末尾を整形"""
    lines = text.strip().splitlines()
    # 先頭の時報・挨拶を除去 (最大3行)
    # 例: "こんばんは、現在時刻は0時です。" / "本日のニュースです。" / "22時を..."
    cleaned = []
    skip = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if i < 3 and re.search(r"(現在時刻は|おはよう|こんばんは|こんにちは|本日のニュースです|ニュースを一本)", s):
            skip = i + 1
            continue
        if i == skip and re.search(r"^22時|本日のニュースです|ニュースを一本", s):
            skip = i + 1
            continue
        if i >= skip:
            cleaned.append(line)
    # 末尾のゲーム言及を除去? ポッドキャストでは不要だが、MVPではそのまま残す
    # 空行を詰める
    text2 = "\n".join(cleaned).strip()
    # 連続空行を1つに
    text2 = re.sub(r"\n{3,}", "\n\n", text2)
    return text2

# 編成 (compose) の設定
# 素材の原稿は「配信中のラジオ」として書かれているため、そのまま読み上げても
# ポッドキャスト番組にならない。配信・ゲームの文脈を落として番組へ書き直す
# 「編成」段を挟む (docich#10)。
#
# 長さは PODCAST_TARGET_CHARS で決める (0 = 自動: その日の話題を全部拾う)。
# VOICEVOX 実測 306 字/分 なので、3500 字 ≒ 11 分、55000 字 ≒ 3 時間。
PODCAST_TARGET_CHARS = int(os.environ.get("PODCAST_TARGET_CHARS", "0"))
PODCAST_SECTION_CHARS = int(os.environ.get("PODCAST_SECTION_CHARS", "1800"))
PODCAST_TOPICS_PER_SECTION = int(os.environ.get("PODCAST_TOPICS_PER_SECTION", "5"))
PODCAST_SOURCE_CHAR_BUDGET = int(os.environ.get("PODCAST_SOURCE_CHAR_BUDGET", "200000"))
# 生成は codex CLI 経由 (ユーザー指定: codex + gpt luna)。
PODCAST_LLM_BIN = os.environ.get("PODCAST_LLM_BIN", "codex")
PODCAST_LLM_MODEL = os.environ.get("PODCAST_LLM_MODEL", "gpt-5.6-luna")
PODCAST_LLM_TIMEOUT = int(os.environ.get("PODCAST_LLM_TIMEOUT", "900"))
# 要約 (map) 段: 素材をまとめて 1 発で投げると API の時間上限に達するため小分けにする
# (実測 2026-08-26: 59,909 字 1 発は 15 分でタイムアウト)。
PODCAST_DIGEST_BATCH = int(os.environ.get("PODCAST_DIGEST_BATCH", "8"))
PODCAST_DIGEST_TIMEOUT = int(os.environ.get("PODCAST_DIGEST_TIMEOUT", "600"))
PODCAST_LLM_WORKERS = int(os.environ.get("PODCAST_LLM_WORKERS", "3"))

# 素材は配信のラジオ原稿なので、番組化にあたって落とすものを明示する
EXCLUDE_RULES = """- 配信とゲームに関する言及は全て落とす。次のものは書かない:
  ・時刻の挨拶や時報 (「こんばんは、現在時刻は23時です」「夜の10時を回りました」など)
  ・ゲームの試合数・スコア・盤面・獲得した国・戦略の話 (「45576回目のゲーム」「991点」など)
  ・配信のコーナー進行や番組内の移動 (「今夜のニュースコーナーに行きましょう」など)
  ・視聴者・リスナー・コメント・チャットへの呼びかけ、放送中であることの言及
  ・「それでは、また次回お会いしましょう」のような各原稿末尾の締め
- 素材が配信の文脈で書かれていても、ポッドキャスト単体で聞いて意味が通る文章に書き直す。"""

DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "topic": {"type": "string"},
                    "points": {"type": "string"},
                },
                "required": ["n", "topic", "points"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "topics": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["heading", "topics"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "summary", "sections"],
    "additionalProperties": False,
}

WRITE_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

DIGEST_PROMPT = """次に示すのは、ある1日にライブ配信の中で読み上げられたラジオ原稿です。
それぞれについて、扱っている話題を1件ずつ短く要約してください。

要件:
- 原稿ごとに1件。原稿番号 (n) は入力のとおりに返す。
- topic は話題を25字以内で。points は要点を150字以内で、固有名詞・数値・日付を落とさずに。
- 配信やゲームの話しか無い原稿は topic を「対象外」とし points を空にする。
- 原稿に無い事実を足さない。感想や評価を足さない。

原稿:
{sources}
"""

PLAN_PROMPT = """あなたはポッドキャスト番組の構成作家です。
以下は、ある1日に扱われた時事ニュース・考察の話題一覧です。
これを1本のポッドキャスト番組として成立させるための構成案を作ってください。

要件:
- 関連する話題をまとめて {n_sections} 個前後のコーナーに配分する。
- 各コーナーには topics として話題番号を並べる。1つの話題は1つのコーナーにだけ入れる。
- {coverage}
- 「対象外」とされた話題は使わない。
- heading はコーナー名 (15字以内)。番組の流れとして自然な順に並べる。
- title はエピソードのタイトル (30字以内)、summary は番組概要 (120字以内)。
- 番組名は「{title}」。title には番組名を含めない (冒頭で別に読み上げるため二重になる)。
  title に「:」「：」「-」などの記号を使わず、そのまま読める一続きの言葉にする。
- 過去回と同じ書き出し、中心語、文型を繰り返さない。特に抽象語だけを並べた
  「〜世界で問われる○○と○○」のような型を使い回さず、その日に固有の話題が伝わる題にする。
- 配信やゲームを想起させる語をタイトル・コーナー名に使わない。

{title_history}

話題一覧:
{topics}
"""

WRITE_PROMPT = """あなたはポッドキャスト番組の構成作家です。
以下の素材から、番組の1コーナー「{heading}」の読み上げ本文を書いてください。

要件:
- 日本語でおよそ {target} 字。
{exclude}
- 素材にある話題を落とさずに扱う。ただし同じ話題が重複していれば1つにまとめる。
- 素材に無い事実を足さない。固有名詞・数値・日付は素材のとおりに書く。
- 音声で読み上げるため、箇条書き・記号・URL・絵文字・見出し記号は使わない。地の文で書く。
- 冒頭でコーナー名を読み上げない。番組の挨拶や締めも書かない (別途付ける)。
- 一つのコーナーとして筋の通った流れにする。話題から話題への繋ぎを書く。

素材:
{sources}
"""


def _extract_json(text: str) -> dict | None:
    """JSON オブジェクトを取り出す。

    --output-schema を付けているので通常は素の JSON が返るが、
    コードフェンスや前置きが混じった場合も拾えるようにしておく。
    """
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    start = t.find("{")
    if start < 0:
        return None
    for end in range(len(t), start, -1):
        if t[end - 1] != "}":
            continue
        try:
            obj = json.loads(t[start:end])
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def llm_generate(prompt: str, timeout: int = PODCAST_LLM_TIMEOUT, schema: dict | None = None):
    """codex CLI で 1 回生成する。schema を渡すと JSON を強制し dict で返す。

    失敗したら None を返し、呼び出し側で扱う。
    """
    bin_path = shutil.which(PODCAST_LLM_BIN) or PODCAST_LLM_BIN
    with tempfile.TemporaryDirectory(prefix="podcast_llm_") as td:
        tdp = Path(td)
        out_file = tdp / "out.txt"
        cmd = [bin_path, "exec", "-m", PODCAST_LLM_MODEL, "--skip-git-repo-check",
               "-C", str(tdp), "-o", str(out_file), "--color", "never"]
        if schema is not None:
            sf = tdp / "schema.json"
            sf.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            cmd += ["--output-schema", str(sf)]
        cmd.append(prompt)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            log(f"llm timeout ({timeout}s)")
            return None
        except OSError as e:
            log(f"llm 起動失敗 ({bin_path}): {e}")
            return None
        if not out_file.exists():
            err = (r.stderr or r.stdout or "").strip()
            log(f"llm failed (rc={r.returncode}): {err[-600:]}")
            return None
        raw = out_file.read_text(encoding="utf-8").strip()
        if not raw:
            log("llm の出力が空")
            return None
        if schema is None:
            return raw
        obj = _extract_json(raw)
        if obj is None:
            log(f"llm の JSON を解釈できなかった: {raw[:300]}")
        return obj


def _clean_body(src: Path) -> str:
    return clean_script(src.read_text(encoding="utf-8", errors="ignore"))


def _digest_batch(idx: int, batch: list[tuple[int, str]]) -> list[dict]:
    """原稿を数本ずつ要約する。失敗したらそのバッチだけ諦める。"""
    sources = "\n\n".join(f"--- 原稿{n} ---\n{body}" for n, body in batch)
    obj = llm_generate(DIGEST_PROMPT.format(sources=sources),
                       timeout=PODCAST_DIGEST_TIMEOUT, schema=DIGEST_SCHEMA)
    if not obj:
        log(f"要約バッチ {idx} 失敗 (原稿 {batch[0][0]}〜{batch[-1][0]})")
        return []
    out = []
    for it in obj.get("items") or []:
        if not isinstance(it, dict):
            continue
        topic = str(it.get("topic") or "").strip()
        points = str(it.get("points") or "").strip()
        try:
            n = int(it.get("n"))
        except (TypeError, ValueError):
            continue
        if not topic or topic == "対象外":
            continue
        out.append({"n": n, "topic": topic, "points": points})
    return out


def _run_parallel(jobs: list, worker, label: str) -> list:
    """jobs を並列実行し、入力順の結果リストを返す。例外はその要素だけ空にする。"""
    results = [None] * len(jobs)
    workers = max(1, min(PODCAST_LLM_WORKERS, len(jobs)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(worker, i, j): i for i, j in enumerate(jobs)}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                log(f"{label} {i + 1} で例外: {e}")
                results[i] = None
            done += 1
            log(f"{label} {done}/{len(jobs)} 完了")
    return results


def digest_sources(files: list[Path]) -> tuple[list[dict], dict[int, str]]:
    """その日の原稿を小分けに要約し、話題一覧と本文の対応表を作る (map 段)。"""
    bodies: list[tuple[int, str]] = []
    used = 0
    for n, src in enumerate(files, 1):
        body = _clean_body(src)
        if not body:
            continue
        if used + len(body) > PODCAST_SOURCE_CHAR_BUDGET:
            log(f"素材の字数予算 {PODCAST_SOURCE_CHAR_BUDGET} に達したため以降の原稿は使わない "
                f"(採用 {len(bodies)}/{len(files)} 本)")
            break
        used += len(body)
        bodies.append((n, body))
    if not bodies:
        return [], {}

    by_n = {n: body for n, body in bodies}
    batches = [bodies[i:i + PODCAST_DIGEST_BATCH]
               for i in range(0, len(bodies), PODCAST_DIGEST_BATCH)]
    log(f"要約: {len(bodies)} 本 / {used} 字 を {len(batches)} バッチ "
        f"(1 バッチ {PODCAST_DIGEST_BATCH} 本, 並列 {PODCAST_LLM_WORKERS}, model={PODCAST_LLM_MODEL})")
    groups = _run_parallel(batches, lambda i, b: _digest_batch(i + 1, b), "要約")
    digests = [d for g in groups if g for d in g]
    if len(digests) < len(bodies):
        log(f"要約できたのは {len(digests)}/{len(bodies)} 件 (配信・ゲームのみの原稿は除外される)")
    return digests, by_n


def _strip_show_name(title: str) -> str:
    """エピソード題の先頭に番組名が入っていたら落とす。

    冒頭で番組名を読み上げた直後に題名を読むので、含まれていると二重になる。
    """
    t = title.strip()
    if t.startswith(PODCAST_TITLE):
        t = t[len(PODCAST_TITLE):]
    return t.lstrip(" 　:：-—–〜~・|｜")


def _normalize_episode_title(title: str) -> str:
    """比較用に空白・記号を落とす。日本語の文字は残す。"""
    return re.sub(r"[^\w]+", "", title, flags=re.UNICODE).lower()


def _title_similarity(left: str, right: str) -> float:
    a = _normalize_episode_title(left)
    b = _normalize_episode_title(right)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _most_similar_title(title: str, recent_titles: list[str]) -> tuple[str, float]:
    scored = [(old, _title_similarity(title, old)) for old in recent_titles]
    return max(scored, key=lambda item: item[1], default=("", 0.0))


def _title_too_similar(title: str, recent_titles: list[str], threshold: float = 0.55) -> bool:
    return _most_similar_title(title, recent_titles)[1] >= threshold


def recent_episode_titles(out_dir: Path, date: datetime.date, limit: int = 7) -> list[str]:
    """対象日より前の meta.json から新しい順に題名を返す。"""
    found: list[tuple[datetime.date, str]] = []
    for path in out_dir.glob("*.meta.json"):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.meta\.json", path.name)
        if not m:
            continue
        try:
            ep_date = datetime.date.fromisoformat(m.group(1))
            if ep_date >= date:
                continue
            obj = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                continue
            title = str(obj.get("title") or "").strip()
        except (OSError, ValueError, TypeError):
            continue
        if title:
            found.append((ep_date, title))
    found.sort(key=lambda item: item[0], reverse=True)
    return [title for _, title in found[:max(0, limit)]]


def _title_history_prompt(recent_titles: list[str], rejected: list[str]) -> str:
    if not recent_titles and not rejected:
        return "過去回のタイトル: なし"
    lines = ["過去回のタイトル（これらと似せない）:"]
    lines.extend(f"- {title}" for title in recent_titles)
    if rejected:
        lines.append("今回すでに類似として却下した案（これらも使わない）:")
        lines.extend(f"- {title}" for title in rejected)
    return "\n".join(lines)


def _fallback_episode_title(sections: list[dict], date: datetime.date,
                            recent_titles: list[str]) -> str:
    """LLMが類似案を繰り返した場合、具体的なコーナー名から題を作る。"""
    headings = [str(sec.get("heading") or "").strip() for sec in sections]
    headings = [h for h in headings if h]
    if len(headings) >= 2:
        candidate = f"{headings[0][:12]}から読む{headings[1][:12]}"
    elif headings:
        candidate = f"{headings[0]}をめぐる今日の論点"[:30]
    else:
        candidate = f"{date.month}月{date.day}日の時事焦点"
    if _title_too_similar(candidate, recent_titles):
        return f"{date.month}月{date.day}日の時事焦点"
    return candidate


def plan_sections(digests: list[dict], date: datetime.date,
                  recent_titles: list[str] | None = None) -> dict | None:
    """話題一覧から番組の構成案を作る。"""
    recent_titles = recent_titles or []
    if PODCAST_TARGET_CHARS > 0:
        n_sections = max(1, round(PODCAST_TARGET_CHARS / PODCAST_SECTION_CHARS))
        coverage = ("話題は取捨選択してよい。その日の主要な話題を優先し、"
                    "収まらないものは落とす。")
    else:
        n_sections = max(1, -(-len(digests) // PODCAST_TOPICS_PER_SECTION))
        coverage = "一覧の話題は原則すべてどれかのコーナーに入れる。"
    topics = "\n".join(f"{d['n']}. {d['topic']}: {d['points']}" for d in digests)
    log(f"構成: 話題 {len(digests)} 件 -> {n_sections} コーナー前後 "
        f"(1 コーナー {PODCAST_SECTION_CHARS} 字目標)")
    rejected: list[str] = []
    for attempt in range(3):
        obj = llm_generate(
            PLAN_PROMPT.format(
                n_sections=n_sections, coverage=coverage, title=PODCAST_TITLE,
                title_history=_title_history_prompt(recent_titles, rejected), topics=topics),
            schema=PLAN_SCHEMA)
        if not obj:
            return None
        sections = []
        seen: set[int] = set()
        valid = {d["n"] for d in digests}
        for sec in obj.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            heading = str(sec.get("heading") or "").strip()
            nums = []
            for t in sec.get("topics") or []:
                try:
                    t = int(t)
                except (TypeError, ValueError):
                    continue
                if t in valid and t not in seen:
                    seen.add(t)
                    nums.append(t)
            if heading and nums:
                sections.append({"heading": heading, "topics": nums})
        if not sections:
            log("構成案にコーナーが無い")
            return None

        candidate = (_strip_show_name(str(obj.get("title") or "").strip())
                     or f"{date.strftime('%Y年%m月%d日')} 時事まとめ")
        old, score = _most_similar_title(candidate, recent_titles)
        if old and score >= 0.55:
            log(f"タイトル案「{candidate}」は過去回「{old}」と類似度 {score:.2f} のため却下")
            rejected.append(candidate)
            if attempt < 2:
                continue
            candidate = _fallback_episode_title(sections, date, recent_titles)
            log(f"類似案が続いたため具体的なコーナー名からタイトルを作成: 「{candidate}」")

        unused = valid - seen
        if unused:
            log(f"構成に入らなかった話題 {len(unused)} 件")
        return {
            "title": candidate,
            "summary": str(obj.get("summary") or "").strip(),
            "sections": sections,
        }
    return None


def write_section(sec: dict, by_n: dict[int, str]) -> str | None:
    """1 コーナーの読み上げ本文を書く。素材は元の原稿そのもの。"""
    sources = "\n\n".join(f"--- 素材{n} ---\n{by_n[n]}" for n in sec["topics"] if n in by_n)
    if not sources:
        return None
    obj = llm_generate(
        WRITE_PROMPT.format(heading=sec["heading"], target=PODCAST_SECTION_CHARS,
                            exclude=EXCLUDE_RULES, sources=sources),
        schema=WRITE_SCHEMA)
    if not obj:
        return None
    text = str(obj.get("text") or "").strip()
    return text or None


def load_script(path: Path) -> dict | None:
    """保存済みの台本 (<date>.script.txt) を読み戻す。

    音声設定 (話速・ピッチ・BGM) だけを変えて作り直したいときに、
    LLM の編成をやり直さずに同じ内容で再合成するために使う。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log(f"台本を読めない ({path}): {e}")
        return None
    title, summary = "", ""
    sections: list[dict] = []
    cur: dict | None = None
    buf: list[str] = []

    def flush():
        if cur is not None:
            cur["text"] = "\n".join(buf).strip()
            if cur["text"]:
                sections.append(cur)

    for line in lines:
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            flush()
            head = line[3:].strip()
            head = re.sub(r"^\d+\.\s*", "", head)
            cur, buf = {"heading": head or "トピック", "text": ""}, []
            continue
        if cur is None:
            if line.strip() and not summary:
                summary = line.strip()
        else:
            buf.append(line)
    flush()
    if not sections:
        log(f"台本からコーナーを読み取れなかった: {path}")
        return None
    total = sum(len(x["text"]) for x in sections)
    log(f"台本を読み込み: 「{title}」 {len(sections)} コーナー / 本文 {total} 字")
    return {"title": title, "summary": summary, "sections": sections,
            "source_count": None, "used_count": None}


def compose_episode(files: list[Path], date: datetime.date, dummy: bool = False,
                    recent_titles: list[str] | None = None) -> dict | None:
    """その日の原稿群を 1 本の番組へ編成し直す。失敗したら None。

    要約 (map) -> 構成案 -> コーナーごとに執筆 の三段。
    1 回の生成が小さく収まるので、長さを伸ばしても API の時間上限に当たらない。
    dummy=True (テスト用) では LLM を呼ばず、原稿 1 本をそのまま 1 コーナーに割り当てる。
    """
    if dummy:
        parts = [b for b in (_clean_body(f) for f in files) if b]
        if not parts:
            log("編成できる本文が無い")
            return None
        return {
            "title": f"{date.strftime('%Y年%m月%d日')} 時事まとめ",
            "summary": "テスト用のダミー編成",
            "sections": [{"heading": f"トピック{i + 1}", "text": t} for i, t in enumerate(parts)],
            "source_count": len(files),
            "used_count": len(parts),
        }

    digests, by_n = digest_sources(files)
    if not digests:
        log("要約が 1 件も取れなかったため編成できない")
        return None

    plan = plan_sections(digests, date, recent_titles=recent_titles)
    if not plan:
        return None

    log(f"執筆: {len(plan['sections'])} コーナー (並列 {PODCAST_LLM_WORKERS})")
    texts = _run_parallel(plan["sections"], lambda i, s: write_section(s, by_n), "執筆")
    sections = []
    for sec, text in zip(plan["sections"], texts):
        if not text:
            log(f"コーナー「{sec['heading']}」の執筆に失敗、飛ばす")
            continue
        sections.append({"heading": sec["heading"], "text": text})
    if not sections:
        log("執筆できたコーナーが無い")
        return None

    episode = {
        "title": plan["title"],
        "summary": plan["summary"],
        "sections": sections,
        "source_count": len(files),
        "used_count": len(digests),
    }
    total = sum(len(x["text"]) for x in sections)
    rate = 306.0 * float(voice_tempo(os.environ.get("PODCAST_VOICE", "109")) or 1.0)
    log(f"編成完了: 「{episode['title']}」 {len(sections)} コーナー / 本文 {total} 字 "
        f"(読み上げ見込み {total / rate:.1f} 分 @ {rate:.0f}字/分)")
    return episode


def synthesize_wav(text: str, wav_path: Path, voice: str, dummy: bool = False) -> bool:
    """VOICEVOXでWAVを生成。dummyなら無音(1秒)を生成。"""
    if dummy:
        # 1秒無音を ffprobe で後で duration を測れるように生成
        # text 長から duration を推測して無音長を変える (100字≒10秒)
        chars = len(text)
        secs = max(2, min(30, int(chars / 10) + 2))
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono", "-t", str(secs), "-q:a", "9", "-acodec", "pcm_s16le", str(wav_path)]
        result = subprocess.run(cmd, capture_output=True)
        return result.returncode == 0 and wav_path.stat().st_size > 0
    # 本番: voicevox_tts.sh 経由
    env = os.environ.copy()
    env["VOICEVOX_SPEAKER"] = voice
    # 話速は voice_tempo() が決める (ポッドキャストは 1.2)
    env["VOICEVOX_TEMPO"] = voice_tempo(voice)
    # docich の apply_ny_pause_fix は i 母音の直後の「ニュ」の前に 0.20 秒のポーズを入れる。
    # 番組名「同志のための時事ニュース」がジジ‖ニュウスに割れて聞こえるため、
    # ポッドキャストでは無効にする (聴き比べて決定)。配信側の設定には影響しない。
    env["VOICEVOX_NY_PAUSE_FIX"] = os.environ.get("PODCAST_NY_PAUSE_FIX", "0")
    # ピッチ (docich 側で pitchScale に加算される)
    pitch = os.environ.get("PODCAST_VOICE_PITCH", "-0.02").strip()
    if pitch:
        env["VOICEVOX_PITCH"] = pitch
    # docich が無い環境でもエラーで明確に
    if not VOICEVOX_TTS.exists():
        log(f"voicevox_tts.sh not found: {VOICEVOX_TTS}")
        return False
    # 一時ファイルにテキストを書き、voicevox_tts.sh -o wav -f txt
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt", delete=False) as tf:
        tf.write(text)
        tf_path = tf.name
    try:
        cmd = [str(VOICEVOX_TTS), "-o", str(wav_path), "-f", tf_path]
        # VOICEVOX_TIMEOUT を長めに
        env["VOICEVOX_TIMEOUT"] = env.get("VOICEVOX_TIMEOUT", "120")
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        if result.returncode != 0:
            # 例外の本体は traceback 末尾に出るので、先頭を切ると原因が読めない
            err = (result.stderr or "").strip()
            tail = err[-600:] if len(err) > 600 else err
            log(f"voicevox failed (rc={result.returncode}): ...{tail}" if len(err) > 600 else f"voicevox failed (rc={result.returncode}): {tail}")
            return False
        return wav_path.exists() and wav_path.stat().st_size > 0
    except subprocess.TimeoutExpired:
        log("voicevox timeout")
        return False
    finally:
        try:
            os.unlink(tf_path)
        except:
            pass

def split_sentences(text: str) -> list[str]:
    """字幕用に文へ分ける。合成の区切りも文に合わせるので、タイミングが正確に取れる。

    docich 側の split_chunks も「。」で切って 200 字までまとめる作りなので、
    文単位にしても音の切れ方は変わらない (境界は同じ「。」の位置)。
    """
    out: list[str] = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=。)|(?<=！)|(?<=？)", line):
            part = part.strip()
            if not part:
                continue
            # 極端に長い文は読点で割る (字幕が2行に収まらないため)
            if len(part) > 90:
                buf = ""
                for piece in part.split("、"):
                    cand = buf + ("、" if buf else "") + piece
                    if len(cand) > 90 and buf:
                        out.append(buf)
                        buf = piece
                    else:
                        buf = cand
                if buf:
                    out.append(buf)
            else:
                out.append(part)
    return out


def get_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], text=True)
        return float(out.strip())
    except:
        return 0.0

def rss_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

def generate_rss(episodes: list[dict], out_path: Path):
    """episodes: list of {date, title, description, mp3_path, duration, pubDate, guid, url, length} sorted desc"""
    now = datetime.datetime.now(datetime.timezone.utc)
    rss = []
    rss.append('<?xml version="1.0" encoding="UTF-8"?>')
    rss.append('<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:podcast="https://podcastindex.org/namespace/1.0">')
    rss.append('<channel>')
    rss.append(f'  <title>{rss_escape(PODCAST_TITLE)}</title>')
    rss.append(f'  <link>{rss_escape(PODCAST_LINK)}</link>')
    rss.append(f'  <description>{rss_escape(PODCAST_DESCRIPTION)}</description>')
    rss.append(f'  <language>{PODCAST_LANGUAGE}</language>')
    rss.append(f'  <itunes:author>{rss_escape(PODCAST_AUTHOR)}</itunes:author>')
    rss.append(f'  <itunes:owner><itunes:name>{rss_escape(PODCAST_AUTHOR)}</itunes:name><itunes:email>{PODCAST_EMAIL}</itunes:email></itunes:owner>')
    rss.append(f'  <itunes:explicit>false</itunes:explicit>')
    rss.append(f'  <itunes:category text="{rss_escape(PODCAST_CATEGORY)}" />')
    rss.append(f'  <itunes:image href="{rss_escape(PODCAST_IMAGE)}" />')
    rss.append(f'  <lastBuildDate>{now.strftime("%a, %d %b %Y %H:%M:%S %z")}</lastBuildDate>')
    for ep in episodes[:50]:
        rss.append('  <item>')
        rss.append(f'    <title>{rss_escape(ep["title"])}</title>')
        rss.append(f'    <description>{rss_escape(ep["description"])}</description>')
        rss.append(f'    <pubDate>{ep["pubDate"]}</pubDate>')
        rss.append(f'    <guid isPermaLink="false">{rss_escape(ep["guid"])}</guid>')
        rss.append(f'    <enclosure url="{rss_escape(ep["url"])}" length="{ep["length"]}" type="audio/mpeg" />')
        rss.append(f'    <itunes:duration>{int(ep["duration"])}</itunes:duration>')
        rss.append(f'    <itunes:explicit>false</itunes:explicit>')
        rss.append('  </item>')
    rss.append('</channel>')
    rss.append('</rss>')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(rss) + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--dry-run", action="store_true", help="skip synth and mp3")
    ap.add_argument("--dummy", action="store_true", help="use silent wav instead of VOICEVOX (for test)")
    ap.add_argument("--from-script", action="store_true",
                    help="編成をやり直さず、保存済みの <date>.script.txt から音声を作り直す")
    ap.add_argument("--out-dir", default=str(OUTPUT_ROOT_DEFAULT), help="output dir")
    ap.add_argument("--voice", default="109", help="VOICEVOX speaker id")
    ap.add_argument("--base-url", default=PODCAST_BASE_URL, help="podcast base URL for enclosure")
    args = ap.parse_args()

    if args.date:
        date = parse_date(args.date)
    else:
        date = datetime.date.today() - datetime.timedelta(days=1)
    yyyymmdd = date.strftime("%Y%m%d")
    iso = date.isoformat()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / f"{iso}.mp3"
    # 既にMP3があり、ソースより新しければスキップ (冪等)
    files = collect_files(date)
    if not files:
        log(f"no source files for {yyyymmdd} (news/jiji) -> skip")
        # 既存の RSS は更新しない
        return 0
    log(f"found {len(files)} source files for {yyyymmdd}:")
    for f in files:
        log(f"  {f.name}")

    # 既存MP3の新しさチェック
    if mp3_path.exists() and not args.dry_run and not args.from_script:
        mtime = mp3_path.stat().st_mtime
        newest_src = max(p.stat().st_mtime for p in files)
        if mtime >= newest_src:
            log(f"mp3 already up to date: {mp3_path} -> skip synth")
            # RSS は更新が必要かチェック (後で)
            skip_synth = True
        else:
            skip_synth = False
    else:
        skip_synth = False

    if args.dry_run:
        log(f"dry-run: would compose {len(files)} source scripts into 1 episode -> {mp3_path}")
        # RSS dry-run 表示
        log(f"dry-run: would update feed.xml with {iso}")
        return 0

    episode = None
    chapters = []
    if skip_synth:
        duration = get_duration(mp3_path)
        length = mp3_path.stat().st_size
    else:
        # 編成: その日の原稿群を 1 本の番組台本へ再構成する
        if args.from_script:
            episode = load_script(out_dir / f"{iso}.script.txt")
        else:
            title_history = recent_episode_titles(out_dir, date)
            if title_history:
                log(f"直近タイトル {len(title_history)} 件を類似回避に使用: "
                    + " / ".join(f"「{title}」" for title in title_history))
            episode = compose_episode(files, date, dummy=args.dummy,
                                      recent_titles=title_history)
        if not episode:
            # 素材の丸ごと連結 (3〜5時間) は作りたい成果物ではないのでフォールバックしない
            log("編成に失敗したため中止する")
            return 3

        tmpdir = Path(tempfile.mkdtemp(prefix="podcast_"))
        wavs = []
        try:
            def _add(wav: Path) -> bool:
                if not wav.exists() or wav.stat().st_size == 0:
                    return False
                wavs.append(wav)
                return True

            def _silence(name: str, secs: float) -> None:
                sp = tmpdir / name
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                                "-t", str(secs), "-q:a", "9", "-acodec", "pcm_s16le", str(sp)],
                               capture_output=True)
                _add(sp)

            # 累積秒からチャプターを作るので、各 wav の実測 duration を足していく
            elapsed = 0.0
            segments: list[dict] = []

            def _elapsed_add(wav: Path) -> None:
                nonlocal elapsed
                elapsed += get_duration(wav)

            wd = "月火水木金土日"[date.weekday()]
            intro_text = (f"{PODCAST_TITLE}。{date.year}年{date.month}月{date.day}日、"
                          f"{wd}曜日のまとめです。{episode['title']}。")
            intro_wav = tmpdir / "intro.wav"
            log(f"synth intro ({len(intro_text)} chars)")
            if synthesize_wav(intro_text, intro_wav, args.voice, dummy=args.dummy) and _add(intro_wav):
                chapters.append({"startTime": 0, "title": "イントロ"})
                _elapsed_add(intro_wav)
                segments.append({"text": intro_text, "start": 0.0,
                                 "end": round(elapsed, 3), "section": "イントロ"})

            failed = 0
            for idx, sec in enumerate(episode["sections"]):
                # 区切りの無音 -> チャプター位置 -> 見出し読み上げ -> 短い無音 -> 本文
                gap_name = f"gap_{idx}.wav"
                _silence(gap_name, PODCAST_GAP_SEC)
                _elapsed_add(tmpdir / gap_name)
                chapters.append({"startTime": int(elapsed), "title": sec["heading"]})

                if PODCAST_ANNOUNCE_HEADING:
                    lead = ("まずは、" if idx == 0 else "続いては、") + f"{sec['heading']}。"
                    head_wav = tmpdir / f"head_{idx:03d}.wav"
                    if synthesize_wav(lead, head_wav, args.voice, dummy=args.dummy) and _add(head_wav):
                        head_start = elapsed
                        _elapsed_add(head_wav)
                        segments.append({"text": lead, "start": round(head_start, 3),
                                         "end": round(elapsed, 3), "section": sec["heading"]})
                        after_name = f"after_{idx}.wav"
                        _silence(after_name, PODCAST_GAP_AFTER_HEAD_SEC)
                        _elapsed_add(tmpdir / after_name)
                    else:
                        log(f"見出し「{sec['heading']}」の合成に失敗、読み上げを省く")

                # 本文は文単位で合成する。字幕 (動画化) に必要な文ごとの
                # start/end が正確に取れ、音の切れ方は docich の chunk 分割
                # (「。」区切り) と変わらない。
                sentences = split_sentences(sec["text"])
                log(f"synth section {idx + 1}/{len(episode['sections'])} 「{sec['heading']}」 "
                    f"({len(sec['text'])} chars / {len(sentences)} 文)")
                sec_ok = 0
                for si, sent in enumerate(sentences):
                    wav = tmpdir / f"{idx:03d}_{si:03d}.wav"
                    if not synthesize_wav(sent, wav, args.voice, dummy=args.dummy) or not _add(wav):
                        log(f"  文 {si + 1}/{len(sentences)} の合成に失敗、飛ばす: {sent[:30]}")
                        continue
                    start = elapsed
                    _elapsed_add(wav)
                    segments.append({"text": sent, "start": round(start, 3),
                                     "end": round(elapsed, 3), "section": sec["heading"]})
                    sec_ok += 1
                if sec_ok == 0:
                    log(f"synth failed for section 「{sec['heading']}」, skipping")
                    failed += 1
                    continue

            outro_text = f"以上、{PODCAST_TITLE}でした。"
            outro_wav = tmpdir / "outro.wav"
            _silence("gap_outro.wav", PODCAST_GAP_SEC)
            _elapsed_add(tmpdir / "gap_outro.wav")
            if synthesize_wav(outro_text, outro_wav, args.voice, dummy=args.dummy) and _add(outro_wav):
                chapters.append({"startTime": int(elapsed), "title": "エンディング"})
                outro_start = elapsed
                _elapsed_add(outro_wav)
                segments.append({"text": outro_text, "start": round(outro_start, 3),
                                 "end": round(elapsed, 3), "section": "エンディング"})
            episode["segments"] = segments

            if failed:
                log(f"warning: {failed}/{len(episode['sections'])} セクションの合成に失敗した")
            if not wavs:
                log("no wavs generated, abort")
                return 2

            # concat list
            concat_list = tmpdir / "concat.txt"
            with open(concat_list, "w", encoding="utf-8") as f:
                for w in wavs:
                    # ffmpeg concat demuxer は絶対パス or 相対パス
                    f.write(f"file '{w.resolve()}'\n")

            # ffmpeg concat + loudnorm + mp3
            # 中間を wav にしてから mp3 にする (loudnorm は wav で)
            concat_wav = tmpdir / "concat.wav"
            cmd1 = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(concat_wav)]
            log(f"concat {len(wavs)} wavs -> {concat_wav}")
            r1 = subprocess.run(cmd1, capture_output=True, text=True)
            if r1.returncode != 0:
                log(f"concat failed: {r1.stderr[:1000]}")
                return 2

            # loudnorm + resample + (BGM ミックス) + mp3
            # 2-pass loudnorm は手間なので 1-pass で I=-16
            bgm = find_bgm()
            speech_dur = get_duration(concat_wav)
            if bgm:
                # インターナショナルを地の下に薄く敷く。素材が短いのでループし、
                # 語りの長さで切り、冒頭と末尾はフェードする。
                # amix の normalize=0 を外すと語りの音量が半分になるので必ず付ける。
                fade_out_at = max(0.0, speech_dur - 4.0)
                filt = (
                    "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100[v];"
                    f"[1:a]aresample=44100,volume={PODCAST_BGM_VOLUME},"
                    f"afade=t=in:st=0:d=3,afade=t=out:st={fade_out_at:.2f}:d=4[b];"
                    "[v][b]amix=inputs=2:duration=first:normalize=0[out]"
                )
                cmd2 = ["ffmpeg", "-y", "-i", str(concat_wav),
                        "-stream_loop", "-1", "-i", str(bgm),
                        "-filter_complex", filt, "-map", "[out]",
                        "-c:a", "libmp3lame", "-q:a", "2",
                        "-id3v2_version", "3", "-write_id3v1", "1", str(mp3_path)]
                log(f"encode mp3 (BGM: {bgm.name} vol={PODCAST_BGM_VOLUME}) -> {mp3_path}")
            else:
                log("BGM が見つからないため BGM 無しで書き出す (PODCAST_BGM_FILE で指定可)")
                cmd2 = ["ffmpeg", "-y", "-i", str(concat_wav), "-filter_complex", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100", "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3", "-write_id3v1", "1", str(mp3_path)]
                log(f"encode mp3 -> {mp3_path}")
            r2 = subprocess.run(cmd2, capture_output=True, text=True)
            if r2.returncode != 0:
                log(f"mp3 encode failed: {r2.stderr[:1000]}")
                return 2

            duration = get_duration(mp3_path)
            length = mp3_path.stat().st_size
            log(f"mp3 done: {mp3_path} ({length} bytes, {duration:.1f}s)")

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # 編成結果のタイトル/概要を保存する (過去回の feed 再生成でも使う)
    if episode and not args.dry_run:
        # 台本そのものも残す。音声だけだと内容を後から確認・レビューできないため。
        script_path = out_dir / f"{iso}.script.txt"
        body = [f"# {episode['title']}", "", episode.get("summary", ""), ""]
        for i, sec in enumerate(episode["sections"], 1):
            body += [f"## {i}. {sec['heading']}", "", sec["text"], ""]
        script_path.write_text("\n".join(body), encoding="utf-8")
        log(f"script written: {script_path} ({sum(len(x['text']) for x in episode['sections'])} 字)")

        # 字幕付き動画 (doci compose) 用に文ごとのタイミングを残す
        segs = episode.get("segments") or []
        if segs:
            seg_path = out_dir / f"{iso}.segments.json"
            seg_path.write_text(json.dumps(segs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            log(f"segments written: {seg_path} ({len(segs)} 文)")

        meta_path = out_dir / f"{iso}.meta.json"
        meta_path.write_text(json.dumps({
            "title": episode["title"],
            "summary": episode["summary"],
            "source_count": episode.get("source_count"),
            "used_count": episode.get("used_count"),
            "sections": [x["heading"] for x in episode["sections"]],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"meta written: {meta_path}")

    # RSS 更新: 既存の output 内の mp3 を列挙して feed を再生成 (最新50件)
    episodes = []
    for mp3 in sorted(out_dir.glob("*.mp3")):
        # ファイル名が YYYY-MM-DD.mp3 形式のみ対象
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})\.mp3$", mp3.name)
        if not m:
            continue
        y, mo, d = m.groups()
        try:
            d_obj = datetime.date(int(y), int(mo), int(d))
        except:
            continue
        pub = datetime.datetime.combine(d_obj, datetime.time(6, 0), tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
        # RFC2822
        pub_str = pub.astimezone(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
        dur = get_duration(mp3)
        ln = mp3.stat().st_size
        base = args.base_url.rstrip("/")
        url = f"{base}/episodes/{mp3.name}"
        # タイトル/概要は編成時に保存した meta.json を優先する
        ep_title = f"{d_obj.strftime('%Y年%m月%d日')} 時事ニュースまとめ"
        desc = f"{d_obj.strftime('%Y年%m月%d日')}のニュースまとめ。"
        meta_file = out_dir / f"{d_obj.isoformat()}.meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                if meta.get("title"):
                    ep_title = f"{d_obj.strftime('%m/%d')} {meta['title']}"
                if meta.get("summary"):
                    desc = meta["summary"]
            except (ValueError, OSError) as e:
                log(f"meta 読み込み失敗 ({meta_file.name}): {e}")
        episodes.append({
            "date": d_obj,
            "title": ep_title,
            "description": desc,
            "mp3_path": mp3,
            "duration": dur,
            "pubDate": pub_str,
            "guid": f"soren-radio-{d_obj.isoformat()}",
            "url": url,
            "length": ln,
        })
    episodes.sort(key=lambda x: x["date"], reverse=True)
    feed_path = out_dir / "feed.xml"
    generate_rss(episodes, feed_path)
    log(f"feed updated: {feed_path} ({len(episodes)} episodes)")

    # chapters.json: 合成時に各 wav の実測 duration を積んだ値を使う
    if chapters and not args.dry_run:
        chapters_path = out_dir / f"{iso}.chapters.json"
        chapters_path.write_text(json.dumps({"version": "1.0.0", "chapters": chapters}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"chapters written: {chapters_path} ({len(chapters)} chapters)")

    # 検証: xmllint があれば feed.xml を検証
    if shutil.which("xmllint"):
        r = subprocess.run(["xmllint", "--noout", str(feed_path)], capture_output=True)
        if r.returncode != 0:
            log(f"xmllint failed: {r.stderr.decode()[:500]}")
            return 2
        else:
            log("xmllint OK")

    # ホスティング: rclone で public bucket へアップロード (任意)
    rclone_enabled = os.environ.get("PODCAST_RCLONE_ENABLED", "0") == "1"
    rclone_remote = os.environ.get("PODCAST_RCLONE_REMOTE", "")
    rclone_bucket = os.environ.get("PODCAST_RCLONE_BUCKET", "soren-radio-archive")
    rclone_prefix = os.environ.get("PODCAST_RCLONE_PREFIX", "podcast")
    if rclone_enabled and rclone_remote and not args.dry_run:
        if shutil.which("rclone"):
            dest = f"{rclone_remote}:{rclone_bucket}/{rclone_prefix}/"
            log(f"rclone copy {out_dir} -> {dest}")
            r = subprocess.run(["rclone", "copy", str(out_dir), dest, "--progress"], capture_output=True, text=True)
            if r.returncode != 0:
                log(f"rclone copy failed: {r.stderr[:1000]}")
                # 失敗しても feed は生成済みなので警告のみで続行
            else:
                log(f"rclone copy succeeded to {dest}")
        elif shutil.which("oci"):
            # oci bulk-upload
            log(f"oci bulk-upload {out_dir} -> {rclone_bucket}/{rclone_prefix}/")
            r = subprocess.run(["oci", "os", "object", "bulk-upload", "--bucket-name", rclone_bucket, "--src-dir", str(out_dir), "--object-prefix", f"{rclone_prefix}/"], capture_output=True, text=True)
            if r.returncode != 0:
                log(f"oci bulk-upload failed: {r.stderr[:1000]}")
            else:
                log(f"oci bulk-upload succeeded")
        else:
            log("rclone/oci not found, skipping upload")

    log("podcast_build done")
    return 0

if __name__ == "__main__":
    sys.exit(main())
