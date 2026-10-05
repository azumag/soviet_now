"""Offline regressions for public-catalog theme repetition and prompt hygiene.

All histories and shell-integration fixtures are synthetic. Public catalog
bindings are read from the repository. Shell integration sources real functions
in a temporary tree with no providers, playback or network.
"""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "radio_theme_history_regression", ROOT / "lib/radio_theme_history.py"
)
history = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = history
SPEC.loader.exec_module(history)

# The public titles are the three real aliases; their details are deliberately
# disjoint synthetic text so a family match cannot pass via keyword overlap.
UFO_TITLES = ("エリア51", "UFO目撃事件", "矢追純一とUFO特番")
UFO_BODIES = (
    "エリア51の話。砂漠の検証拠点を深掘りして",
    "UFO目撃事件の話。夜空の観測記録を深掘りして",
    "矢追純一とUFO特番の話。テレビ番組の制作史を深掘りして",
)
OTHER = "時計職人の話。歯車の精密な組立工程を深掘りして"
OTHER_TWO = "絹織物の話。染料の選別方法を深掘りして"
FAMILY = "# family: ufo | " + " | ".join(UFO_TITLES)
PRIVATE = "SYNTHETIC_PRIVATE_HISTORY_MARKER"
GENERATED = "SYNTHETIC_GENERATED_PAYLOAD_MARKER"
RAW_BODY = "SYNTHETIC_CATALOG_DETAIL_MARKER"


class RadioThemeHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="radio-theme-history-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for folder in ("lib", "data", "prompts", "history"):
            (self.root / folder).mkdir()
        (self.root / "lib/radio_theme_history.py").symlink_to(
            ROOT / "lib/radio_theme_history.py"
        )
        shutil.copyfile(ROOT / "prompts/radio_theme.md", self.root / "prompts/radio_theme.md")
        self.catalog = self.root / "data/radio_themes.txt"
        self.bodies = self.root / "history/bodies.txt"
        self.keys = self.root / "history/keys.txt"
        self.topics = self.root / "history/topics.txt"
        self.status = self.root / "pick-status.json"
        self.write_catalog([FAMILY, *UFO_BODIES, OTHER, OTHER_TWO])

    def write_catalog(self, rows):
        self.catalog.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def write_histories(self, rows, mode="dual"):
        for path in (self.bodies, self.keys):
            path.unlink(missing_ok=True)
        if mode in ("bodies", "dual"):
            self.bodies.write_text("\n".join(rows) + "\n", encoding="utf-8")
        if mode in ("keys", "dual"):
            self.keys.write_text(
                "\n".join(history.normalize(row) for row in rows) + "\n", encoding="utf-8"
            )

    def shell(self, script, **overrides):
        env = {
            "PATH": os.environ["PATH"],
            "LANG": "C.UTF-8",
            "ELOOP_LIB_DIR": str(self.root),
            "TMP_HISTORY_DIR": str(self.root / "history"),
            "PAST_RADIO_THEME_BODIES": str(self.bodies),
            "PAST_RADIO_THEME_KEYS": str(self.keys),
            "PAST_RADIO_TOPICS": str(self.topics),
            "RADIO_THEME_PICK_STATUS_FILE": str(self.status),
            "RADIO_WEB_GROUNDING_ENABLED": "0",
        }
        env.update({name: str(value) for name, value in overrides.items()})
        sources = "\n".join(
            "source " + shlex.quote(str(ROOT / "broadcast" / name))
            for name in ("radio_themes.sh", "radio_persona.sh", "radio_corners.sh")
        )
        result = subprocess.run(
            ["bash", "-eu", "-c", sources + "\nlog() { :; }\n" + script],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def cli(self, command, *, body="", keep=400, limit=12, exclude="", stdin="", category=""):
        result = subprocess.run(
            [
                sys.executable, str(ROOT / "lib/radio_theme_history.py"), command,
                "--catalog", str(self.catalog), "--bodies", str(self.bodies),
                "--keys", str(self.keys), "--keep", str(keep), "--limit", str(limit),
                "--body=" + body, "--exclude=" + exclude, "--category", category,
            ],
            cwd=self.root,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def match(self, body, **overrides):
        return self.shell(
            "_radio_theme_recent_match_mode " + shlex.quote(body), **overrides
        ).strip()

    def test_all_public_ufo_aliases_match_keys_bodies_dual_or_missing_history(self):
        catalog = history.read_catalog(self.catalog)
        self.assertEqual(
            {catalog[history.normalize(body)].families for body in UFO_BODIES},
            {frozenset({"ufo"})},
        )
        for mode in ("keys", "bodies", "dual", "missing"):
            for past_index, past in enumerate(UFO_BODIES):
                with self.subTest(mode=mode, past=UFO_TITLES[past_index]):
                    self.write_histories([past], mode)
                    before = {
                        p: p.read_bytes() if p.exists() else None
                        for p in (self.bodies, self.keys)
                    }
                    output = self.shell(
                        "\n".join(
                            "_radio_theme_recent_match_mode " + shlex.quote(body)
                            + "; printf '\\036\\n'"
                            for body in UFO_BODIES
                        )
                    )
                    modes = [part.strip() for part in output.split("\x1e")[:-1]]
                    expected = [
                        "" if mode == "missing" else "exact" if index == past_index else "family:ufo"
                        for index in range(3)
                    ]
                    self.assertEqual(modes, expected)
                    for p, data in before.items():
                        self.assertEqual(p.read_bytes() if p.exists() else None, data)

    def test_public_catalog_declares_the_three_ufo_aliases(self):
        # Inspect the checked-in public binding contract. Never pick themes
        # from the full catalog or use operational histories as fixtures.
        public_catalog = ROOT / "data/radio_themes.txt"
        by_title = {topic.title: topic for topic in history.read_catalog(public_catalog).values()}
        for title in UFO_TITLES:
            with self.subTest(title=title):
                self.assertEqual(by_title[title].families, frozenset({"ufo"}))
        self.assertEqual(by_title["ボルシチ"].families, frozenset())
        candidates = history.catalog_candidates(public_catalog)
        self.assertTrue(candidates)
        self.assertFalse(any(row.lstrip().startswith("#") for row in candidates))

    def test_unrelated_theme_is_available_and_selected_after_ufo(self):
        self.write_catalog([FAMILY, *UFO_BODIES, OTHER])
        self.write_histories([UFO_BODIES[0]])
        self.assertEqual(self.match(OTHER), "")
        self.assertEqual(
            self.cli("available", stdin="\n".join([*UFO_BODIES, OTHER]) + "\n"),
            OTHER + "\n",
        )
        self.assertEqual(self.shell("_pick_radio_theme"), OTHER + "\n")
        status = json.loads(self.status.read_text())
        self.assertEqual(status["available_theme_count"], 1)
        self.assertFalse(status["history_exhausted"])

    def test_keys_only_selection_migration_preserves_legacy_history(self):
        self.write_catalog([FAMILY, *UFO_BODIES, OTHER])
        self.write_histories([UFO_BODIES[0]], "keys")
        old_key = history.normalize(UFO_BODIES[0])
        self.assertEqual(self.shell("_pick_radio_theme"), OTHER + "\n")
        self.assertEqual(self.keys.read_text().splitlines(), [old_key, history.normalize(OTHER)])
        self.assertEqual(self.bodies.read_text().splitlines(), [old_key, OTHER])

    def test_retention_400_includes_age_201_and_400_but_not_401(self):
        # Ages are one-based: the last row has age 1.
        for mode in ("keys", "bodies", "dual"):
            for age in (201, 400, 401):
                with self.subTest(mode=mode, age=age):
                    rows = [UFO_BODIES[0]] + [f"synthetic filler {n}" for n in range(age - 1)]
                    self.write_histories(rows, mode)
                    expected = "family:ufo" if age <= 400 else ""
                    self.assertEqual(self.match(UFO_BODIES[2]), expected)

    def test_configured_keep_controls_matching_and_both_history_writes(self):
        for age in (3, 4):
            with self.subTest(age=age):
                self.write_histories([UFO_BODIES[0]] + [f"filler {n}" for n in range(age - 1)])
                self.assertEqual(
                    self.match(UFO_BODIES[1], PAST_RADIO_THEME_HISTORY_KEEP=3),
                    "family:ufo" if age == 3 else "",
                )
        rows = [f"old synthetic record {n}" for n in range(5)]
        self.write_histories(rows)
        self.shell("_radio_mark_theme_used " + shlex.quote(OTHER), PAST_RADIO_THEME_HISTORY_KEEP=3)
        self.assertEqual(self.bodies.read_text().splitlines(), [*rows[-2:], OTHER])
        self.assertEqual(
            self.keys.read_text().splitlines(),
            [history.normalize(row) for row in [*rows[-2:], OTHER]],
        )

    def test_default_mark_keeps_last_400_in_both_histories(self):
        rows = [f"old synthetic record {n}" for n in range(400)]
        self.write_histories(rows)
        self.shell("_radio_mark_theme_used " + shlex.quote(OTHER))
        self.assertEqual(self.bodies.read_text().splitlines(), [*rows[1:], OTHER])
        self.assertEqual(
            self.keys.read_text().splitlines(),
            [history.normalize(row) for row in [*rows[1:], OTHER]],
        )

    def test_category_exhaustion_preserves_other_history_and_soviet_tab_protocol(self):
        oldest = "地下鉄建設の話。掘削装置の組立を深掘りして"
        newest = "舞台衣装の話。布地の仕立工程を深掘りして"
        self.write_catalog([FAMILY, "[soviet] " + oldest, "[soviet] " + newest, OTHER])
        rows = [oldest, OTHER, newest]
        self.write_histories(rows)
        before = {p: p.read_bytes() for p in (self.bodies, self.keys)}
        self.assertEqual(self.shell("_pick_radio_theme soviet"), "[soviet]\t" + oldest + "\n")
        for p, data in before.items():
            self.assertTrue(p.read_bytes().startswith(data))
        self.assertEqual(self.bodies.read_text().splitlines(), [*rows, oldest])
        self.assertEqual(
            self.keys.read_text().splitlines(),
            [history.normalize(row) for row in [*rows, oldest]],
        )
        status = json.loads(self.status.read_text())
        self.assertTrue(status["history_exhausted"])
        self.assertFalse(status["history_reset"])
        self.assertFalse(status["used_default_fallback"])
        self.assertEqual(status["filter_category"], "soviet")
        self.assertEqual(status["selected_category"], "soviet")
        self.assertEqual(status["selected_theme"], oldest)
        self.assertEqual(status["available_theme_count"], 1)

    def test_latest_family_alias_controls_least_recent_reuse(self):
        self.write_catalog([FAMILY, "[soviet] " + UFO_BODIES[0], UFO_BODIES[2], "[soviet] " + OTHER])
        rows = [UFO_BODIES[0], "synthetic filler", OTHER, UFO_BODIES[2]]
        self.write_histories(rows)
        self.assertEqual(self.match(UFO_BODIES[0]), "exact")
        self.assertEqual(
            history.oldest_candidates([UFO_BODIES[0], OTHER], [rows], history.read_catalog(self.catalog)),
            [OTHER],
        )
        self.assertEqual(self.shell("_pick_radio_theme soviet"), "[soviet]\t" + OTHER + "\n")

    def test_least_recent_reuse_uses_newest_copy_across_both_histories(self):
        self.write_histories([UFO_BODIES[0], OTHER, "synthetic filler"], "bodies")
        self.keys.write_text(history.normalize(UFO_BODIES[2]) + "\n", encoding="utf-8")
        self.assertEqual(
            self.cli("oldest", stdin="\n".join([UFO_BODIES[0], OTHER]) + "\n"),
            OTHER + "\n",
        )

    def test_legacy_exact_normalization_and_keyword_fallback_without_catalog(self):
        body = "[soviet]　（UFO）／目撃の話。ロズウェル、光を深掘りして"
        expected = "ufo 目撃 ロズウェル 光"
        self.assertEqual(history.normalize(body), expected)
        self.assertEqual(self.shell("_radio_theme_key_from_body " + shlex.quote(body)).strip(), expected)
        self.catalog.unlink()
        self.keys.write_text(expected + "\n", encoding="utf-8")
        self.assertEqual(self.match(body), "exact")
        self.write_histories(["望遠鏡工房の話。古い装置を深掘りして"], "bodies")
        self.assertTrue(self.match("望遠鏡工房の話。レンズ加工を深掘りして").startswith("overlap:"))
        self.assertEqual(self.match(OTHER), "")

    def test_metadata_comments_and_duplicate_bodies_are_not_candidates(self):
        rows = [FAMILY, "# ordinary comment", "   # indented comment", "", " ", OTHER, OTHER]
        self.write_catalog(rows)
        self.assertEqual(history.catalog_candidates(self.catalog), [OTHER])
        self.assertEqual(len(history.read_catalog(self.catalog)), 1)
        self.assertEqual(self.cli("catalog"), OTHER + "\n")
        self.assertEqual(self.shell("_pick_radio_theme"), OTHER + "\n")
        status = json.loads(self.status.read_text())
        self.assertEqual(status["source_theme_count"], 1)
        self.assertEqual(status["deduped_theme_count"], 1)
        self.assertFalse(status["used_default_fallback"])

    def test_missing_or_empty_catalog_and_history_use_safe_fallback(self):
        fallback = "世界の料理と文化の話。各国の食卓と暮らしの違いを深掘りして"
        for mode in ("missing", "empty"):
            with self.subTest(catalog=mode):
                self.write_histories([], "missing")
                if mode == "missing":
                    self.catalog.unlink()
                else:
                    self.catalog.write_text("", encoding="utf-8")
                self.assertEqual(self.cli("recent"), "")
                self.assertEqual(self.match(OTHER), "")
                self.assertEqual(self.shell("_pick_radio_theme"), fallback + "\n")
                status = json.loads(self.status.read_text())
                self.assertTrue(status["used_default_fallback"])
                self.assertFalse(status["history_reset"])
                self.assertFalse(status["history_exhausted"])
                self.assertEqual(self.bodies.read_text().splitlines(), [fallback])

    def test_empty_histories_use_catalog_and_empty_memo_fallback(self):
        self.write_catalog([FAMILY, OTHER])
        self.write_histories([])
        self.assertEqual(self.cli("recent"), "")
        self.assertEqual(
            self.shell("_radio_past_topics_block"),
            "まだ過去のトークはありません。自由に話してください。\n",
        )
        self.assertEqual(self.shell("_pick_radio_theme"), OTHER + "\n")
        status = json.loads(self.status.read_text())
        self.assertFalse(status["used_default_fallback"])
        self.assertFalse(status["history_exhausted"])

    def test_recent_memo_contains_public_titles_only_in_all_history_formats(self):
        detailed = "時計職人の話。" + RAW_BODY + "を深掘りして"
        self.write_catalog([FAMILY, *UFO_BODIES, detailed])
        for mode in ("keys", "bodies", "dual"):
            with self.subTest(mode=mode):
                self.write_histories([detailed, UFO_BODIES[0], PRIVATE], mode)
                self.topics.write_text(
                    "[12:34] Game#1 [theme]: " + GENERATED + "\n" + PRIVATE + "\n",
                    encoding="utf-8",
                )
                memo = self.shell("_radio_past_topics_block")
                self.assertIn("- エリア51\n", memo)
                self.assertIn("- 時計職人\n", memo)
                self.assertIn("脱線テーマの雑談をしました", memo)
                for forbidden in (PRIVATE, GENERATED, RAW_BODY, "family:", "[同系:", UFO_BODIES[0]):
                    self.assertNotIn(forbidden, memo)
                self.assertEqual(memo.count("- エリア51\n"), 1)

    def test_recent_memo_excludes_current_theme_and_its_family(self):
        self.write_histories([OTHER, *UFO_BODIES])
        memo = self.shell("_radio_past_topics_block " + shlex.quote(UFO_BODIES[1]))
        self.assertIn("- 時計職人\n", memo)
        for title in UFO_TITLES:
            self.assertNotIn(title, memo)
        self.assertEqual(self.cli("recent", exclude=OTHER).count("- 時計職人\n"), 0)

    def test_recent_title_order_and_limit_merge_both_histories_without_raw_rows(self):
        self.write_histories([OTHER, UFO_BODIES[0]], "bodies")
        self.keys.write_text(
            "\n".join(history.normalize(row) for row in [UFO_BODIES[0], OTHER_TWO]) + "\n",
            encoding="utf-8",
        )
        recent = self.cli("recent", limit=2)
        self.assertEqual(
            [line for line in recent.splitlines() if line in {"- " + title for title in [*UFO_TITLES, "時計職人", "絹織物"]}],
            ["- エリア51", "- 絹織物"],
        )
        self.assertNotIn("- 時計職人\n", recent)

    def test_real_theme_corner_passes_selected_theme_exclusion_into_prompt(self):
        self.write_histories([OTHER, *UFO_BODIES, PRIVATE])
        self.topics.write_text("[12:34] Game#1 [theme]: " + GENERATED + "\n", encoding="utf-8")
        captured = self.root / "captured-prompt.txt"
        captured_args = self.root / "captured-args.txt"
        # GNU envsubst is not a dependency of this offline test. This tiny
        # stand-in substitutes the template's exported variables only.
        substitution = (
            "import os,re,sys; sys.stdout.write(re.sub(r'\\$\\{([A-Za-z_][A-Za-z_0-9]*)\\}', "
            "lambda m: os.environ.get(m[1], ''), sys.stdin.read()))"
        )
        script = "\n".join([
            "_radio_time_context() { _rc_time='00:00'; }",
            "_pick_radio_theme() { printf '%s\\n' " + shlex.quote(UFO_BODIES[1]) + "; }",
            "_radio_persona_block() { printf 'Synthetic persona\\n'; }",
            "_radio_output_rules() { printf 'Synthetic output rules\\n'; }",
            "_radio_stage_research() { printf 'Synthetic public grounding\\n'; }",
            "envsubst() { python3 -c " + shlex.quote(substitution) + "; }",
            "_radio_generate_and_play() { cp \"$1\" " + shlex.quote(str(captured))
            + "; printf '%s\\n' \"$@\" >" + shlex.quote(str(captured_args))
            + "; rm -f \"$1\"; }",
            "start_radio_corner_theme 1 0",
        ])
        self.shell(script)
        self.assertEqual(
            captured_args.read_text(encoding="utf-8").splitlines()[1:],
            ["1", "0", "theme", "--topic", UFO_BODIES[1]],
        )
        prompt = captured.read_text(encoding="utf-8")
        self.assertIn(UFO_BODIES[1], prompt)
        memo = prompt.split("【重複回避メモ:", 1)[1].split("【状況】", 1)[0]
        self.assertIn("- 時計職人\n", memo)
        for title in UFO_TITLES:
            self.assertNotIn(title, memo)
        for forbidden in (PRIVATE, GENERATED, "family:", "[同系:"):
            self.assertNotIn(forbidden, prompt)


if __name__ == "__main__":
    unittest.main()
