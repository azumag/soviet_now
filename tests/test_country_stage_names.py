from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dashboard_data
import status_dashboard
from lib import normalize_speech_text
from lib.country_names import COUNTRY_NAMES as AUTHORITATIVE_COUNTRY_NAMES
from lib.country_names import country_name, country_named_reason


EXPECTED_COUNTRY_NAMES = {
    1: "アルメニア",
    2: "モルドバ",
    3: "エストニア",
    4: "ラトビア",
    5: "リトアニア",
    6: "ジョージア",
    7: "アゼルバイジャン",
    8: "タジキスタン",
    9: "キルギス",
    10: "ベラルーシ",
    11: "ウズベキスタン",
    12: "トルクメニスタン",
    13: "ウクライナ",
    14: "カザフスタン",
    15: "ロシア",
    16: "ソ連",
}


class CountryStageNamesTest(unittest.TestCase):
    def test_speech_normalizer_replaces_every_supported_type_format(self):
        formats = ("T{n}", "t{n}", "type {n}", "Type-{n}", "タイプ{n}")

        for piece_type, expected_name in EXPECTED_COUNTRY_NAMES.items():
            for template in formats:
                with self.subTest(piece_type=piece_type, template=template):
                    source = template.format(n=piece_type)
                    self.assertEqual(
                        normalize_speech_text.normalize(
                            source, replace_countries=True
                        ),
                        expected_name,
                    )

    def test_speech_normalizer_does_not_invent_unknown_country_names(self):
        self.assertEqual(
            normalize_speech_text.replace_country_references("T17"), "T17"
        )
        self.assertEqual(
            normalize_speech_text.replace_country_references("type 17"),
            "type 17",
        )

    def test_generic_speech_normalizer_requires_country_opt_in(self):
        source = "ソ連のT-34戦車、T-72戦車、Type 2 diabetes、T2"
        self.assertEqual(
            normalize_speech_text.normalize(source),
            "ソ連のt-34戦車、t-72戦車、type 2 diabetes、t2",
        )
        self.assertEqual(
            normalize_speech_text.normalize("T2", replace_countries=True),
            "モルドバ",
        )
        self.assertEqual(
            normalize_speech_text.replace_country_references(
                "T-34 T-72 type 17"
            ),
            "T-34 T-72 type 17",
        )

    def test_speech_normalizer_does_not_replace_embedded_type_suffix(self):
        self.assertEqual(
            normalize_speech_text.normalize("prototype15 Prototype16"),
            "prototype15 prototype16",
        )
        for source in ("T2.5", "type 11.5", "T15abc"):
            with self.subTest(source=source):
                self.assertEqual(
                    normalize_speech_text.replace_country_references(source), source
                )
        self.assertEqual(
            normalize_speech_text.replace_country_references(
                "FIRST_RUSSIA_T11_LANE T15_suffix"
            ),
            "FIRST_RUSSIA_ウズベキスタン_LANE ロシア_suffix",
        )
        self.assertEqual(
            normalize_speech_text.replace_country_references("T2が来た"),
            "モルドバが来た",
        )
        self.assertEqual(
            normalize_speech_text.replace_country_references("T2. 次です"),
            "モルドバ. 次です",
        )
        self.assertEqual(normalize_speech_text.normalize("T2.5"), "t2.5")

    def test_country_only_normalizer_preserves_non_country_letter_case(self):
        self.assertEqual(
            normalize_speech_text.replace_country_references(
                "USAはT11、Type-15、type 17を確認"
            ),
            "USAはウズベキスタン、ロシア、type 17を確認",
        )
        self.assertEqual(
            normalize_speech_text.replace_country_references(
                "max_piece_type=14 high_type_counts=T14x2 type15/16 T17 "
                "stage_target=13 target_type=12"
            ),
            "最高国=カザフスタン 終盤の国別個数=カザフスタン2個 "
            "ロシア・ソ連 T17 対象国=ウクライナ 対象国=トルクメニスタン",
        )

    def test_strategy_reason_tokens_use_country_names_on_user_surfaces(self):
        self.assertEqual(
            country_named_reason("FIRST_RUSSIA_T11_LANE_COVER_AVOID"),
            "FIRST_RUSSIA_ウズベキスタン_LANE_COVER_AVOID",
        )
        self.assertEqual(
            country_named_reason("POST_RUSSIA_T12_CONTACT_SHOT"),
            "POST_RUSSIA_トルクメニスタン_CONTACT_SHOT",
        )
        self.assertEqual(country_named_reason("UNKNOWN_T17_REASON"), "UNKNOWN_T17_REASON")

    def test_status_dashboard_last_drop_humanizes_reason_identifier(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            history_dir = root / "game_history"
            history_dir.mkdir()
            (root / "strategy.py").write_text(
                'reasons.append("FIRST_RUSSIA_T11_LANE_COVER_AVOID")\n',
                encoding="utf-8",
            )
            (history_dir / "latest.jsonl").write_text(
                json.dumps(
                    {
                        "turn": 67,
                        "decision_x": 0.35,
                        "decision_reason": "FIRST_RUSSIA_T11_LANE_COVER_AVOID",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                latest = status_dashboard.load_latest_drop()
            finally:
                os.chdir(old_cwd)

            self.assertIn("ウズベキスタン", latest)
            self.assertNotIn("T11", latest)

    def test_dashboard_returns_authoritative_name_for_every_country(self):
        self.assertEqual(AUTHORITATIVE_COUNTRY_NAMES, EXPECTED_COUNTRY_NAMES)
        self.assertEqual(
            {piece_type: dashboard_data.country_name(piece_type) for piece_type in range(1, 17)},
            EXPECTED_COUNTRY_NAMES,
        )
        self.assertEqual(country_name("11"), "ウズベキスタン")
        self.assertEqual(country_name(17), "不明な国")
        self.assertEqual(country_name(1.9), "不明な国")
        self.assertEqual(country_name(float("nan")), "不明な国")

    def test_dashboard_stage_labels_keep_uzbekistan_and_turkmenistan_distinct(self):
        self.assertIn((11, "ウズベキスタン"), dashboard_data.STAGE_TYPES)
        self.assertIn((12, "トルクメニスタン"), dashboard_data.STAGE_TYPES)

    def test_archive_restart_country_rows_fit_dashboard_width(self):
        candidate = {
            "status": "ok",
            "count": 1,
            "candidates": [
                {
                    "hash": "123456789abcdef0",
                    "comp": 12345,
                    "p25": 9876,
                    "n": 123,
                    "russia": 12,
                    "soviet": 3,
                    "best_type": 12,
                    "origin_retry": True,
                }
            ],
        }
        with mock.patch.object(
            status_dashboard,
            "load_archive_restart_candidate",
            return_value=candidate,
        ):
            lines = status_dashboard.render_archive_restart_candidates()

        self.assertTrue(any("最高国=トルクメニスタン" in line for line in lines))
        for line in lines:
            with self.subTest(line=line):
                self.assertLessEqual(
                    status_dashboard.ansi_display_width(line), status_dashboard.W
                )

    def test_ranking_prompt_and_raw_comment_normalizer_use_country_names(self):
        script = """
import { COUNTRY_NAMES, normalizeCountryReferences } from './soren91/country_names.mjs';
console.log(JSON.stringify({
  names: COUNTRY_NAMES,
  normalized: normalizeCountryReferences('T11 Type-15 type 17'),
  reason: normalizeCountryReferences('判断理由はFIRST_RUSSIA_T11_LANE_COVER_AVOIDです。 stage_target=13'),
  military: normalizeCountryReferences('T-34 T-72 type 17'),
}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        payload = json.loads(result.stdout)
        prompt = (REPO_ROOT / "soren91/prompts/ranking_comment.md").read_text(
            encoding="utf-8"
        )
        comment_source = (REPO_ROOT / "soren91/comment.mjs").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("15種類のピース", prompt)
        self.assertNotIn("(type", prompt)
        self.assertNotIn("${stage.name}(type${stage.type})", comment_source)
        self.assertIn("アルメニアからソ連まで16種類の国", prompt)
        self.assertIn("内部の type・T・タイプ番号は本文へ一切出力しない", prompt)
        self.assertEqual(
            payload["normalized"],
            "ウズベキスタン ロシア type 17",
        )
        self.assertEqual(
            payload["reason"],
            "判断理由はFIRST_RUSSIA_ウズベキスタン_LANE_COVER_AVOIDです。 対象国=ウクライナ",
        )
        self.assertEqual(payload["military"], "T-34 T-72 type 17")
        self.assertEqual(
            payload["names"],
            {str(piece_type): name for piece_type, name in EXPECTED_COUNTRY_NAMES.items()},
        )

    def test_every_comment_prompt_route_gets_country_only_output_contract(self):
        comment_script = (REPO_ROOT / "broadcast/comment.sh").read_text(
            encoding="utf-8"
        )
        contract = "内部の type・T・タイプ番号は返答本文へ一切出力しない"
        self.assertIn(contract, comment_script)
        self.assertLess(comment_script.index("comment_response_${template_category}.md"), comment_script.index(contract))
        self.assertLess(comment_script.index("prompts/comment_template.md"), comment_script.index(contract))
        normalize_call = '_country_named_attempt_talk=$(printf \'%s\' "$attempt_talk" | _comment_replace_country_references'
        queue_call = '_comment_write_country_named_queue_file "$attempt_talk" "$queue_file"'
        self.assertIn(normalize_call, comment_script)
        self.assertIn(queue_call, comment_script)
        self.assertIn("国名正規化失敗のため未変換本文を破棄して再生成", comment_script)
        self.assertLess(
            comment_script.index(normalize_call),
            comment_script.index(
                'printf \'%s\' "$attempt_talk" | _comment_build_translation_prompt'
            ),
        )
        self.assertLess(
            comment_script.index(normalize_call),
            comment_script.index(queue_call),
        )
        self.assertLess(comment_script.index(normalize_call), comment_script.index('comments_talk="$attempt_talk"'))
        self.assertLess(comment_script.index('comments_talk="$attempt_talk"'), comment_script.index('_ov_reply=$(printf'))

    def test_user_facing_surfaces_do_not_fall_back_to_stage_numbers(self):
        dashboard = (REPO_ROOT / "generate_dashboard.sh").read_text(encoding="utf-8")
        comment_prompt = (REPO_ROOT / "prompts/comment_template.md").read_text(
            encoding="utf-8"
        )
        celebration = (REPO_ROOT / "broadcast/radio_celebration.sh").read_text(
            encoding="utf-8"
        )
        eloop = (REPO_ROOT / "eloop.sh").read_text(encoding="utf-8")
        soren_loop = (REPO_ROOT / "soren_loop.sh").read_text(encoding="utf-8")
        phyrogenetic = (REPO_ROOT / "core/phyrogenetic.sh").read_text(
            encoding="utf-8"
        )
        show_status = (REPO_ROOT / "show_status.sh").read_text(encoding="utf-8")
        status_dashboard = (REPO_ROOT / "status_dashboard.py").read_text(
            encoding="utf-8"
        )
        strategy_runner = (REPO_ROOT / "strategy_runner.py").read_text(
            encoding="utf-8"
        )
        batch_summary = (REPO_ROOT / "batch_summary.py").read_text(
            encoding="utf-8"
        )
        monitor_report = (REPO_ROOT / "monitor_report_stale_report.sh").read_text(
            encoding="utf-8"
        )
        soren91_control = (REPO_ROOT / "soren91_control.sh").read_text(
            encoding="utf-8"
        )
        soren91_prompt = (
            REPO_ROOT / "soren91" / "prompts" / "explain_strategy.md"
        ).read_text(encoding="utf-8")
        comment_response = (REPO_ROOT / "prompts/comment_response.md").read_text(
            encoding="utf-8"
        )
        comment_response_game = (
            REPO_ROOT / "prompts/comment_response_game.md"
        ).read_text(encoding="utf-8")

        self.assertIn("ロシア <b id=\"gateRussiaInline\"", dashboard)
        self.assertIn("カザフスタン <b id=\"gateKazakhstan\"", dashboard)
        self.assertNotIn("best T'", dashboard)
        self.assertNotIn("'(T' + purgeTarget.type", dashboard)
        self.assertIn("必ず国名で呼ぶこと", comment_prompt)
        self.assertNotIn("レベル14の「ロシア」", celebration)
        self.assertNotIn("レベル15の「ソ連」", celebration)
        self.assertNotIn('f"Type{stage}', soren_loop)
        self.assertNotIn('f"Type{target}', phyrogenetic)
        self.assertNotIn("'Type'+str(stage)", eloop)
        self.assertNotIn("COUNTRY_NAMES", eloop)
        self.assertNotIn('T{fresh_best}', show_status)
        self.assertNotIn('T14p{t14_peak}', show_status)
        self.assertNotIn('T{best_type}', show_status)
        self.assertNotIn("ru sv  t origin", status_dashboard)
        self.assertIn("country_name(cand.get('best_type', 0), '-')", status_dashboard)
        self.assertIn("decision = country_named_reason(decision)", show_status)
        self.assertIn("decision = country_named_reason(decision)", status_dashboard)
        self.assertEqual(
            strategy_runner.count(
                'reason = country_named_reason(decision.get("reason"), default="")'
            ),
            2,
        )
        self.assertIn("country_named_reason(reason)", batch_summary)
        self.assertIn("source_bits.append(country_name(source_best_type))", monitor_report)
        self.assertNotIn('source_bits.append(f"T{source_best_type}")', monitor_report)
        self.assertIn("SAY_REPLACE_COUNTRY_REFERENCES=1", soren91_control)
        self.assertIn("T1〜T16、type番号、タイプ番号は出力せず", soren91_prompt)
        for prompt in (comment_response, comment_response_game):
            self.assertNotIn("15 piece types", prompt)
            self.assertNotIn("Type 15 is the maximum", prompt)
            self.assertIn("Never call a country by an internal type/T number", prompt)

    def test_archive_restart_progress_message_and_overlay_use_country_name(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            state_dir = root / "state"
            lib_dir = root / "lib"
            state_dir.mkdir()
            lib_dir.mkdir()
            shutil.copy2(
                REPO_ROOT / "wildcard_progress_report.sh",
                root / "wildcard_progress_report.sh",
            )
            shutil.copy2(
                REPO_ROOT / "lib" / "country_names.py",
                lib_dir / "country_names.py",
            )
            (root / "eloop_lib.sh").write_text(
                """TMP_STATE_DIR="$TEST_STATE_DIR"
enqueue_audio_text() {
    printf '%s|%s\\n' "$1" "$2" >> "$TEST_AUDIO_LOG"
}
""",
                encoding="utf-8",
            )
            (root / "overlay_notify.sh").write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$TEST_OVERLAY_LOG\"\n",
                encoding="utf-8",
            )
            (root / "overlay_notify.sh").chmod(0o755)

            strategy_hash = "abcdef123456"
            origin_file = state_dir / "origin.json"
            run_file = state_dir / "run.json"
            anchor_file = state_dir / "anchor.json"
            reporter_state = state_dir / "reporter.json"
            parallel_file = state_dir / "parallel.json"
            audio_log = root / "audio.log"
            overlay_log = root / "overlay.log"
            origin_file.write_text(
                json.dumps(
                    {
                        strategy_hash: {
                            "origin_type": "archive_restart",
                            "source_n": 100,
                            "source_russia_count": 1,
                            "source_best_max_type": 14,
                        }
                    }
                ),
                encoding="utf-8",
            )
            run_file.write_text(
                json.dumps(
                    {
                        "hash": strategy_hash,
                        "games_total": 1,
                        "scores": [12000],
                    }
                ),
                encoding="utf-8",
            )
            anchor_file.write_text(
                json.dumps({"comp": 10000}), encoding="utf-8"
            )
            parallel_file.write_text("{}", encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "TEST_STATE_DIR": str(state_dir),
                    "TEST_AUDIO_LOG": str(audio_log),
                    "TEST_OVERLAY_LOG": str(overlay_log),
                    "WILDCARD_ORIGIN_FILE": str(origin_file),
                    "CURRENT_STRATEGY_RUN_FILE": str(run_file),
                    "BEST_STRATEGY_ANCHOR_FILE": str(anchor_file),
                    "WILDCARD_PROGRESS_AUDIO_STATE_FILE": str(reporter_state),
                    "WILDCARD_PARALLEL_STATUS_FILE": str(parallel_file),
                    "WILDCARD_PROGRESS_AUDIO_MILESTONES": "1",
                    "WILDCARD_PROGRESS_AUDIO_MIN_DELTA": "0",
                }
            )
            result = subprocess.run(
                ["bash", "wildcard_progress_report.sh"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            combined = "\n".join(
                (
                    result.stdout,
                    audio_log.read_text(encoding="utf-8"),
                    overlay_log.read_text(encoding="utf-8"),
                )
            )
            self.assertIn("最高国カザフスタン", combined)
            self.assertIn("source_best_country=カザフスタン", combined)
            self.assertNotIn("T14", combined)
            self.assertNotIn("source_best_type=14", combined)

    def test_batch_commentary_normalizes_game_countries_before_enqueue(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            (root / "lib").mkdir()
            shutil.copy2(REPO_ROOT / "batch_commentary.sh", root)
            shutil.copy2(REPO_ROOT / "batch_summary.py", root)
            shutil.copy2(
                REPO_ROOT / "lib" / "country_names.py",
                root / "lib" / "country_names.py",
            )
            shutil.copy2(
                REPO_ROOT / "lib" / "normalize_speech_text.py",
                root / "lib" / "normalize_speech_text.py",
            )
            audio_log = root / "audio.log"
            (root / "eloop_lib.sh").write_text(
                """_ai_agent_spec_valid() { return 0; }
ai_generate_list() { printf '%s' "$TEST_AI_TEXT"; }
_ai_guard_model_output() { cat; }
enqueue_audio_text() {
    printf '%s|%s\\n' "$1" "$2" >> "$TEST_AUDIO_LOG"
}
""",
                encoding="utf-8",
            )
            history = root / "game.jsonl"
            history.write_text(
                json.dumps(
                    {
                        "turn": 1,
                        "score": 1234,
                        "score_delta": 0,
                        "decision_reason": "HEIGHT_CONTROL",
                        "merge_available": False,
                        "max_y": -1.0,
                        "state_snapshot": {
                            "pieces": [{"id": 1, "type": 14}]
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            accumulated = root / "accumulated.json"
            accumulated.write_text(
                json.dumps(
                    {
                        "count": 1,
                        "files": [str(history)],
                        "scores": "1234",
                        "raw_scores": "1234",
                        "hash": "abcdef123456",
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "BATCH_COMMENTARY_AGENTS": "test:agent",
                    "BATCH_COMMENTARY_STATE_DIR": str(root / "state"),
                    "BATCH_COMMENTARY_DEBUG_DIR": str(root / "debug"),
                    "TEST_AUDIO_LOG": str(audio_log),
                    "TEST_AI_TEXT": (
                        "今回の最高国はT14で、T15への道筋も確認しました。"
                        "次は併合率を保ちながら盤面を整えます。未知のT17は記録名のまま扱います。"
                    ),
                }
            )
            result = subprocess.run(
                ["bash", "batch_commentary.sh", str(accumulated), "1"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            spoken = audio_log.read_text(encoding="utf-8")
            self.assertIn("カザフスタン", spoken)
            self.assertIn("ロシア", spoken)
            self.assertIn("T17", spoken)
            self.assertNotIn("T14", spoken)
            self.assertNotIn("T15", spoken)

    def test_celebration_normalizes_game_countries_before_output_file(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            (root / "broadcast").mkdir()
            (root / "lib").mkdir()
            debug_dir = root / "debug"
            debug_dir.mkdir()
            shutil.copy2(
                REPO_ROOT / "broadcast" / "radio_celebration.sh",
                root / "broadcast" / "radio_celebration.sh",
            )
            shutil.copy2(
                REPO_ROOT / "lib" / "country_names.py",
                root / "lib" / "country_names.py",
            )
            shutil.copy2(
                REPO_ROOT / "lib" / "normalize_speech_text.py",
                root / "lib" / "normalize_speech_text.py",
            )
            runner = root / "run.sh"
            runner.write_text(
                """#!/bin/bash
set -e
cd "$(dirname "$0")"
TMP_DEBUG_DIR="$TEST_DEBUG_DIR"
RADIO_FACT_CHECK_ENABLED=0
RADIO_CLAUDE_MODEL=test
RADIO_MAIN_AGENT=test
RADIO_MAIN_FALLBACK=test
_radio_set_state() { :; }
_radio_clear_state() { :; }
log() { :; }
_run_claude_radio() { printf '%s' "$TEST_AI_TEXT"; }
_run_opencode_radio() { return 1; }
_sanitize_onair_text() { cat; }
_normalize_radio_tone() { cat; }
_is_valid_radio_talk() { return 0; }
source ./broadcast/radio_celebration.sh
generate_russia_celebration 1234 45 67
""",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "TEST_DEBUG_DIR": str(debug_dir),
                    "TEST_AI_TEXT": (
                        "T14からT15へ到達した祝賀です。"
                        "次は二つのロシアを育てます。未知のT17はそのままです。"
                    ),
                }
            )
            result = subprocess.run(
                ["bash", str(runner)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            spoken = (debug_dir / "radio_russia_celebration.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("カザフスタンからロシア", spoken)
            self.assertIn("T17", spoken)
            self.assertNotIn("T14", spoken)
            self.assertNotIn("T15", spoken)

    def test_comment_queue_writer_never_writes_raw_text_on_normalizer_failure(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            success_file = root / "success.txt"
            failure_file = root / "failure.txt"
            success = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        "source broadcast/comment.sh\n"
                        '_comment_write_country_named_queue_file "$1" "$2"'
                    ),
                    "comment-country-success",
                    "T14 T15 T17",
                    str(success_file),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(
                success_file.read_text(encoding="utf-8"),
                "カザフスタン ロシア T17\n",
            )

            failure = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        "source broadcast/comment.sh\n"
                        "_comment_replace_country_references() { return 1; }\n"
                        '_comment_write_country_named_queue_file "$1" "$2"'
                    ),
                    "comment-country-failure",
                    "T14",
                    str(failure_file),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(failure.returncode, 0)
            self.assertFalse(failure_file.exists())


if __name__ == "__main__":
    unittest.main()
