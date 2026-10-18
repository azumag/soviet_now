#!/bin/bash
# broadcast/radio_quality.sh - ラジオ生成テキストの品質チェック
#
# 中国語出力・不要な英語混入・非日本語・無限ループ・文字化け・ニュース素材の丸読みを検出し、
# リライト用プロンプトを生成するユーティリティ。

# _radio_quality_check <talk_text> [corner_name] [source_material]
#   stdout: "OK" / "FAIL:chinese_text" / "FAIL:wrong_language" / "FAIL:mixed_language" / "FAIL:repetition_loop" / "FAIL:garbled" / "FAIL:verbatim_source"
#   return: 0=OK, 1=failed
_radio_quality_check() {
	local talk_text="$1" corner_name="${2:-}" source_material="${3:-}"

	[ "${RADIO_QUALITY_CHECK_ENABLED:-1}" != "1" ] && echo "OK" && return 0

	# コーナーごとのスキップ
	case ",${RADIO_QUALITY_SKIP_CORNERS:-}," in
		*",${corner_name},"*) echo "OK"; return 0 ;;
	esac

	# 短いテキストはスキップ（別チェックでカバー済み）
	[ "${#talk_text}" -lt 50 ] && echo "OK" && return 0

	# テキストをtmpfileに書き出してPythonに渡す（pipe+heredocの競合を避けるため）
	local _qc_txt _qc_result
	_qc_txt=$(mktemp /tmp/eloop_radio_qc_XXXXXXXX)
	printf '%s' "$talk_text" > "$_qc_txt"
	_qc_result=$(python3 - \
		"$_qc_txt" \
		"${RADIO_QUALITY_MIN_JAPANESE_RATIO:-0.10}" \
		"${RADIO_QUALITY_MAX_REPETITIONS:-3}" \
		"$corner_name" \
		"$source_material" <<'PY'
import sys
import re
import os
import unicodedata
from collections import Counter

text_file = sys.argv[1]
min_ratio = float(sys.argv[2])
max_reps = int(sys.argv[3])
corner = sys.argv[4] if len(sys.argv) > 4 else ""
source_material = sys.argv[5] if len(sys.argv) > 5 else ""
with open(text_file, 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()

if not text.strip():
    print("OK")
    sys.exit(0)

# 1. 中国語テキスト検出
# CJK Unified Ideographs: U+4E00-U+9FFF は日中共通だが、
# ひらがな/カタカナが全くなく漢字ばかりの場合は中国語と判定
cjk = re.findall(r'[\u4e00-\u9fff]', text)
kana = re.findall(r'[\u3040-\u30ff]', text)  # ひらがな + カタカナ
# 全体が中国語
if len(cjk) > 100 and len(kana) < 10:
    print("FAIL:chinese_text")
    sys.exit(0)
# 文レベルの中国語混入検出: 句点区切りの文でCJK漢字のみ(かな無し)の文が多い場合
sents = re.split(r'[。！？\n]', text)
sents = [s.strip() for s in sents if len(s.strip()) >= 4]
if len(sents) >= 3:
    cn_sents = 0
    for s in sents:
        s_cjk = len(re.findall(r'[\u4e00-\u9fff]', s))
        s_kana = len(re.findall(r'[\u3040-\u30ff]', s))
        if s_cjk >= 4 and s_kana == 0:
            cn_sents += 1
    if cn_sents >= 2 or (len(sents) >= 4 and cn_sents / len(sents) >= 0.3):
        print("FAIL:chinese_text")
        sys.exit(0)

# 2. 非日本語検出（英語/韓国語など）
# 数値・記号・固有名詞が多いコーナー向けに緩めの閾値
# かつ kana が5文字未満の場合のみ判定（false positive回避）
if len(text) > 200:
    # 空白と数字を除いた文字数で比率計算
    base_chars = len(re.sub(r'[\s\d\W]', '', text))
    if base_chars > 0:
        ratio = (len(kana) + len(cjk)) / base_chars
        if ratio < min_ratio and len(kana) < 5:
            print("FAIL:wrong_language")
            sys.exit(0)

# 2b. 外国語混入検出（英語・簡体字中国語）
# 日本語の話し言葉に英文・英単語がそのまま混ざると、日本語TTS（VOICEVOX等）が
# 日本語の音素で誤読し、聞き取れない読み上げになる。簡体字中国語も日本語の
# 漢字として誤読される。単発の固有名詞（NATO等）は許容し、英文・英単語の
# 多用と、日本語では使わない簡体字だけを検出して再生成させる。
english_run = re.search(
    r"(?<![A-Za-z])[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){4,}(?![A-Za-z])",
    text,
)
lower_words = [
    w for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", text)
    if w[0].islower() and len(w) >= 3
]
simplified_chars = re.findall(
    r"[这们时说过还边续么纸论题单东车见话请谢让认识记该谁吗呢"
    r"关问间样为从发书标极级约结给统经员运达违远连进选适现应务动员华亲爱儿丽举习乡买"
    r"长头电听门无类团图药难义术击处备复组织济产业严饭开观觉实际]",
    text,
)
if english_run or simplified_chars:
    print("FAIL:mixed_language")
    sys.exit(0)

# 日本語本文へ英単語が1〜数語だけ混ざると、上の長文・多用判定を
# 通過してしまう。ニュース／時事コーナーでは、略称と固有名詞以外の
# 原綴りを読み上げない契約なので、不要な小文字語・混在語も再生成へ回す。
if corner in {"news", "jiji"}:
    allowed_latin = {
        "afd", "ai", "ap", "apec", "asean", "bbc", "brics", "cnn", "cptpp",
        "df", "dx", "eu", "g7", "g20", "gdp", "iaea", "imf", "it", "jaxa",
        "lgbtq", "ms", "msnow", "nasa", "nato", "nhk", "npt", "oecd", "opec",
        "politico", "reuters", "rcep", "sbi", "sdd", "soren", "ssd", "tbs",
        "tpp", "uk", "un", "us", "who", "wto", "youtube", "youtuber",
        "openai", "chatgpt", "google", "apple", "amazon", "microsoft", "meta",
        "tiktok", "facebook", "instagram", "spacex", "starlink", "iphone",
        "deepseek", "minimax", "claude", "codex", "opencode", "voicevox",
    }
    allowed_latin.update(
        word.casefold()
        for word in re.split(r"[\s,]+", os.environ.get("RADIO_QUALITY_ALLOWED_LATIN_WORDS", ""))
        if word.strip()
    )
    latin_words = re.findall(r"(?<![A-Za-z])([A-Za-z]{3,})(?![A-Za-z])", text)
    if any(word.casefold() not in allowed_latin and not word.isupper() for word in latin_words):
        print("FAIL:mixed_language")
        sys.exit(0)

if len(lower_words) >= 8:
    print("FAIL:mixed_language")
    sys.exit(0)

# 3. 無限ループ/繰り返し検出
# 10文字以上の文が max_reps 回以上繰り返される場合
sentences = re.split(r'[。！？\n]', text)
sentences = [s.strip() for s in sentences if len(s.strip()) >= 10]
if sentences:
    counts = Counter(sentences)
    if counts and max(counts.values()) >= max_reps:
        print("FAIL:repetition_loop")
        sys.exit(0)

# 4. 文字化け/制御文字検出
ansi_count = len(re.findall(r'\x1b\[[0-9;]*m', text))
ctrl_count = len(re.findall(r'[\x00-\x08\x0e-\x1f\x7f]', text))
if ansi_count + ctrl_count > 10:
    print("FAIL:garbled")
    sys.exit(0)

# 5. ニュース素材の丸読み検出（ニュースコーナー限定）
#    見出し・RSS要約をそのまま読み上げていないか、正規化した長い連続一致で見る。
#    素材は「朗読原稿」ではなく再構成の参考資料であり、原文コピーは再生成させる。
def _normalize(s: str) -> str:
    return re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', s or ''))

if corner == "news" and source_material.strip():
    try:
        min_verbatim = max(12, int(os.environ.get("RADIO_QUALITY_VERBATIM_MIN_CHARS", "40")))
    except ValueError:
        min_verbatim = 40
    material_norm = _normalize(source_material)
    body_norm = _normalize(text)
    if len(material_norm) >= min_verbatim and len(body_norm) >= min_verbatim:
        material_windows = {
            material_norm[i:i + min_verbatim]
            for i in range(0, len(material_norm) - min_verbatim + 1)
        }
        for i in range(0, len(body_norm) - min_verbatim + 1):
            if body_norm[i:i + min_verbatim] in material_windows:
                print("FAIL:verbatim_source")
                sys.exit(0)

print("OK")
PY
	)
	rm -f "$_qc_txt" 2>/dev/null || true
	printf '%s' "${_qc_result:-OK}"
	[ "${_qc_result:-OK}" = "OK" ] && return 0 || return 1
}

# _radio_build_rewrite_prompt <saved_prompt_file> <failed_snippet> <fail_reason>
#   stdout: リライト指示を追記した新しいプロンプトファイルのパス
#   呼び出し元で rm -f すること
_radio_build_rewrite_prompt() {
	local saved_prompt_file="$1" failed_snippet="$2" fail_reason="$3"
	local rewrite_prompt_file
	rewrite_prompt_file=$(mktemp /tmp/eloop_radio_rewrite_XXXXXXXX)

	# 元プロンプトをコピー
	cat "$saved_prompt_file" > "$rewrite_prompt_file" 2>/dev/null || true

	# 失敗理由に応じたリライト指示を末尾に追加
	local reason_msg
	case "$fail_reason" in
		*chinese_text*)   reason_msg="前回の出力が中国語になっていました" ;;
		*wrong_language*) reason_msg="前回の出力が日本語ではありませんでした" ;;
		*mixed_language*) reason_msg="前回の出力に英語・中国語などの外国語がそのまま混ざっていました" ;;
		*repetition_loop*) reason_msg="前回の出力で同じ文が繰り返される無限ループ状態になっていました" ;;
		*garbled*)        reason_msg="前回の出力が文字化けや制御文字を含んでいました" ;;
		*verbatim_source*) reason_msg="前回の出力がニュース素材（見出し・RSS要約）の文面をそのまま読み上げていました" ;;
		*)                reason_msg="前回の出力に品質問題がありました (${fail_reason})" ;;
	esac

	case "$fail_reason" in
	*verbatim_source*)
		cat >> "$rewrite_prompt_file" <<REWRITE_INST

---
【再生成指示 - 必ず従うこと】
${reason_msg}。素材は朗読用の原稿ではなく、内容を再構成するための参考資料です。
前回の出力で素材の文・言い回しをそのまま使った箇所は全て捨て、事実関係だけを保ったまま、見出しも本文もあなた自身の言葉で書き直してください。
- 見出しの文言をそのまま音読せず、「要するに何が起きたのか」を自分の言葉で1-2文に言い換えること
- 素材に書かれた語順・文・言い回しをコピーしないこと
- 出典名・媒体名・URL・公開日時は読み上げないこと
前回の失敗出力（参考・使用禁止）: 「${failed_snippet:0:100}」
REWRITE_INST
		printf '%s' "$rewrite_prompt_file"
		return 0
		;;
	*mixed_language*)
		cat >> "$rewrite_prompt_file" <<REWRITE_INST

---
【再生成指示 - 必ず従うこと】
${reason_msg}。前回の出力は絶対に使用せず、最初から完全に日本語で書き直してください。
- 英単語・英文・中国語・その他の外国語の語句をそのまま書かないこと
- 人名・地名・組織名・製品名・料理名などの固有名詞や外来語は、アルファベットを使わずカタカナで表記すること
- ニュース素材などに外国語表記があっても、そのまま引用せず、意味が伝わる自然な日本語（カタカナ含む）に置き換えること
前回の失敗出力（参考・使用禁止）: 「${failed_snippet:0:100}」
REWRITE_INST
		printf '%s' "$rewrite_prompt_file"
		return 0
		;;
	esac

	cat >> "$rewrite_prompt_file" <<REWRITE_INST

---
【再生成指示 - 必ず従うこと】
${reason_msg}。前回の出力は絶対に使用せず、最初から完全に日本語で書き直してください。
全ての出力は日本語で行うこと。中国語・英語・その他の言語は一切使用禁止。
前回の失敗出力（参考・使用禁止）: 「${failed_snippet:0:100}」
REWRITE_INST

	printf '%s' "$rewrite_prompt_file"
}
