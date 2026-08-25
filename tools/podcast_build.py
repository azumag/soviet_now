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

# 設定 (環境変数で上書き可)
PODCAST_TITLE = os.environ.get("PODCAST_TITLE", "ソ連ゲーム時事ラジオ")
PODCAST_LINK = os.environ.get("PODCAST_LINK", "https://github.com/azumag/soren-radio-archive")
PODCAST_DESCRIPTION = os.environ.get("PODCAST_DESCRIPTION", "ソ連ゲーム配信の時事ニュース・考察を1日1本にまとめたポッドキャスト。VOICEVOX:東北イタコ")
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
            filtered.append(f)
        else:
            # 旧ファイルで corner が不明でも、内容に「ニュース」等があれば拾う? 今回は厳格に news/jiji のみ
            pass
    filtered.sort()
    return filtered

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
- 番組名は「{title}」。配信やゲームを想起させる語をタイトル・コーナー名に使わない。

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


def plan_sections(digests: list[dict], date: datetime.date) -> dict | None:
    """話題一覧から番組の構成案を作る。"""
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
    obj = llm_generate(
        PLAN_PROMPT.format(n_sections=n_sections, coverage=coverage,
                           title=PODCAST_TITLE, topics=topics),
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
    unused = valid - seen
    if unused:
        log(f"構成に入らなかった話題 {len(unused)} 件")
    return {
        "title": str(obj.get("title") or "").strip() or f"{date.strftime('%Y年%m月%d日')} 時事まとめ",
        "summary": str(obj.get("summary") or "").strip(),
        "sections": sections,
    }


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


def compose_episode(files: list[Path], date: datetime.date, dummy: bool = False) -> dict | None:
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

    plan = plan_sections(digests, date)
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
    log(f"編成完了: 「{episode['title']}」 {len(sections)} コーナー / 本文 {total} 字 "
        f"(読み上げ見込み {total / 306:.1f} 分)")
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
    if mp3_path.exists() and not args.dry_run:
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
        episode = compose_episode(files, date, dummy=args.dummy)
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

            def _silence(name: str, secs: float = 0.8) -> None:
                sp = tmpdir / name
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                                "-t", str(secs), "-q:a", "9", "-acodec", "pcm_s16le", str(sp)],
                               capture_output=True)
                _add(sp)

            # 累積秒からチャプターを作るので、各 wav の実測 duration を足していく
            elapsed = 0.0

            def _elapsed_add(wav: Path) -> None:
                nonlocal elapsed
                elapsed += get_duration(wav)

            intro_text = (f"{PODCAST_TITLE}、{date.month}月{date.day}日のまとめです。"
                          f"{episode['title']}。")
            intro_wav = tmpdir / "intro.wav"
            log(f"synth intro ({len(intro_text)} chars)")
            if synthesize_wav(intro_text, intro_wav, args.voice, dummy=args.dummy) and _add(intro_wav):
                chapters.append({"startTime": 0, "title": "イントロ"})
                _elapsed_add(intro_wav)
                _silence("silence_intro.wav")
                _elapsed_add(tmpdir / "silence_intro.wav")

            failed = 0
            for idx, sec in enumerate(episode["sections"]):
                wav = tmpdir / f"{idx:03d}.wav"
                log(f"synth section {idx + 1}/{len(episode['sections'])} 「{sec['heading']}」 ({len(sec['text'])} chars)")
                if not synthesize_wav(sec["text"], wav, args.voice, dummy=args.dummy) or not _add(wav):
                    log(f"synth failed for section 「{sec['heading']}」, skipping")
                    failed += 1
                    continue
                chapters.append({"startTime": int(elapsed), "title": sec["heading"]})
                _elapsed_add(wav)
                if idx < len(episode["sections"]) - 1:
                    sp_name = f"silence_{idx}.wav"
                    _silence(sp_name)
                    _elapsed_add(tmpdir / sp_name)

            outro_text = f"以上、{PODCAST_TITLE}でした。"
            outro_wav = tmpdir / "outro.wav"
            if synthesize_wav(outro_text, outro_wav, args.voice, dummy=args.dummy) and _add(outro_wav):
                chapters.append({"startTime": int(elapsed), "title": "エンディング"})

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

            # loudnorm + resample + mp3
            # 2-pass loudnorm は手間なので 1-pass で I=-16
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
