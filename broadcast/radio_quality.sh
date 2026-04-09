#!/bin/bash
# broadcast/radio_quality.sh - ラジオ生成テキストの品質チェック
#
# 中国語出力・非日本語・無限ループ・文字化けを検出し、
# リライト用プロンプトを生成するユーティリティ。

# _radio_quality_check <talk_text> [corner_name]
#   stdout: "OK" / "FAIL:chinese_text" / "FAIL:wrong_language" / "FAIL:repetition_loop" / "FAIL:garbled"
#   return: 0=OK, 1=failed
_radio_quality_check() {
	local talk_text="$1" corner_name="${2:-}"

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
		"$corner_name" <<'PY'
import sys
import re
from collections import Counter

text_file = sys.argv[1]
min_ratio = float(sys.argv[2])
max_reps = int(sys.argv[3])
corner = sys.argv[4] if len(sys.argv) > 4 else ""
with open(text_file, 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()

if not text.strip():
    print("OK")
    sys.exit(0)

# 1. 中国語テキスト検出
# CJK Unified Ideographs: U+4E00-U+9FFF は日中共通だが、
# ひらがな/カタカナが全くなく漢字ばかりの場合は中国語と判定
# 保守的閾値: 市場/天気コーナーの数値多用に対応するため高め
cjk = re.findall(r'[\u4e00-\u9fff]', text)
kana = re.findall(r'[\u3040-\u30ff]', text)  # ひらがな + カタカナ
if len(cjk) > 100 and len(kana) < 10:
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
		*repetition_loop*) reason_msg="前回の出力で同じ文が繰り返される無限ループ状態になっていました" ;;
		*garbled*)        reason_msg="前回の出力が文字化けや制御文字を含んでいました" ;;
		*)                reason_msg="前回の出力に品質問題がありました (${fail_reason})" ;;
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
