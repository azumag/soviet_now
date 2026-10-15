"""Synthetic post-game archive -> writer regression; no live/game operations."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_soren_stage_ledger import ROOT, raw, snapshots, m, progress


class ArchiveWriterTests(unittest.TestCase):
    def execute(self, directory, mode, *, loop=None, version=None, writer=None, old_newer=False, strict=True):
        root = Path(directory)
        for name in ("lib", "history", "versions", "tmp/state"):
            root.joinpath(name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "lib/soren_stage_ledger.py", root / "lib/soren_stage_ledger.py")
        for name, source, default in (("version.sh", version, "core/version.sh"),
                                      ("writer.sh", writer, "strategy/ab_interleave.sh")):
            root.joinpath(name).write_text(source if source is not None else (ROOT / default).read_text())
        root.joinpath("state.json").write_text(json.dumps({"games_recorded": 0, "decision_rule": {"frozen": True}}))
        root.joinpath("strategy.py.game_snapshot").write_text("synthetic snapshot")
        # A previous positive game OUTSIDE this experiment, with the same hash/turns.
        old = root / "history/20000101_000000_score1234.jsonl"
        old.write_bytes(raw())
        if old_newer:
            os.utime(old, (4102444800, 4102444800))
        if mode != "missing":
            root.joinpath("history/latest.jsonl").write_bytes(raw(snapshots(False)))
        source = loop if loop is not None else (ROOT / "eloop.sh").read_text()
        begin = source.index("\t# スコア履歴", source.index("post_game_bookkeeping() {"))
        end = source.index("\t\t# 試合ごとの結果はチャットへ投稿せず", begin)
        block = source[begin:end]
        root.joinpath("generate_dashboard.sh").write_text("#!/bin/bash\nexit 0\n")
        root.joinpath("generate_dashboard.sh").chmod(0o700)
        shell = '''set -eu
source ./version.sh
source ./writer.sh
log() { :; }
_ab_hash() { printf '%s\\n' aaaaaaaaaaaa; }
update_best() { return 1; }
archive_gameover_screenshots() { :; }
record_completed_game_for_adaptive_improvement() { printf '%s\\n' "$@" > accumulator_args.txt; }
_ab_gate_after_game() { printf called > gate_called.txt; }
AB_ARM=A AB_HASH=aaaaaaaaaaaa AB_IDX=0 GAME_NUM=99
AB_STATE_FILE=state.json AB_GAMES_FILE=games.jsonl
HISTORY_DIR=history HISTORY_FILE=history/latest.jsonl
STRATEGY_FILE=strategy.py STRATEGY_VERSIONS_DIR=versions GAME_COUNT_FILE=game_count.txt
LAST_SCORE=1234 LAST_TURNS=3 RESULT_JSON='{"score":1234,"final_types":[16],"soviet_created":false}'
# Prove a previous successful call's provenance cannot survive this call.
LAST_CREATED_ARCHIVE_FILE=history/20000101_000000_score1234.jsonl
OBS_DASHBOARD_VISIBILITY_ENABLED=0 BATCH_COMMENTARY_ENABLED=0 ACCUMULATED_GAMES_FILE=unused
'''
        if mode in ("copy_failure", "partial_copy_failure"):
            shell += '''cp() {
    if [ "$1" = "$HISTORY_FILE" ]; then
'''
            if mode == "partial_copy_failure":
                shell += '''        printf '{partial' > "$2"
'''
            shell += '''        return 1
    fi
    command cp "$@"
}
'''
        elif mode == "temp_failure":
            shell += 'mktemp() { return 1; }\n'
        elif mode == "publish_failure":
            shell += 'ln() { return 1; }\n'
        shell += 'bookkeeping_fixture() {\nlocal game_num_display=100 _soviet_for_acc=false _russia_for_acc=true\n' + block + '\n}\nbookkeeping_fixture\n'
        if not strict:
            shell = shell.replace('set -eu\n', 'set -u\n', 1)
        env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        run = subprocess.run(["bash", "-c", shell], cwd=root, env=env, text=True, capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(old.read_bytes(), raw())
        self.assertEqual(root.joinpath("gate_called.txt").read_text(), "called")
        return (json.loads(root.joinpath("games.jsonl").read_text()),
                json.loads(root.joinpath("state.json").read_text()))

    def test_missing_or_failed_copy_never_binds_old_positive_archive(self):
        for mode in ("missing", "copy_failure", "partial_copy_failure", "temp_failure", "publish_failure"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
                row, state = self.execute(directory, mode)
                self.assertEqual(row["stage_evidence"], {
                    "schema_version": 1, "status": "unavailable", "reason": "archive_creation_unverified"})
                self.assertNotIn("archive_sha256", row)
                self.assertEqual(m.retained_pair(row), (None, "producer_unavailable"))
                self.assertEqual((row["idx"], row["game_num"], row["score"], row["turns"]), (0, "100", 1234, 3))
                self.assertEqual(row["eval"], 26636)
                self.assertEqual((row["soviet_created"], row["russia_created"]), (False, True))
                self.assertEqual(state, {"games_recorded": 1, "last_arm": "A", "decision_rule": {"frozen": True}})
                self.assertEqual(len(list(Path(directory, "history").glob("[0-9]*_score*.jsonl"))), 1)
                data, evidence = progress.enrich([row], Path(directory, "history"))
                self.assertIsNone(data[0]["_two_russias_observed"])
                self.assertEqual(evidence["counts"], {"producer_unavailable": 1})
                root = Path(directory)
                self.assertEqual(root.joinpath("score_history.txt").read_text().split("\t")[1], "1234\n")
                self.assertEqual(root.joinpath("eval_score_history.txt").read_text().split("\t")[1], "26636\n")
                self.assertEqual(root.joinpath("game_count.txt").read_text(), "100\n")
                self.assertEqual(root.joinpath("accumulator_args.txt").read_text().splitlines()[1:],
                                 ["26636", "false", "true"])
                expected = {"20000101_000000_score1234.jsonl"}
                if mode != "missing":
                    expected.add("latest.jsonl")
                self.assertEqual({p.name for p in root.joinpath("history").iterdir()}, expected)

    def test_success_uses_created_archive_even_with_newer_old_positive(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            # Old archive sorts first by mtime; today's successful archive is negative/unknown.
            root = Path(directory)
            row, _ = self.execute(directory, "success", old_newer=True)
            self.assertEqual(row["stage_evidence"]["status"], "observed")
            self.assertNotEqual(row["archive"], "20000101_000000_score1234.jsonl")
            self.assertIsNone(row["stage_evidence"]["two_russias_observed"])
            self.assertEqual(m.retained_pair(row), (None, "retained_observation"))
            self.assertEqual(len(list(root.joinpath("history").glob("[0-9]*_score*.jsonl"))), 2)

    def test_same_second_collision_is_unavailable_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            root.joinpath("version.sh").write_text((ROOT / "core/version.sh").read_text())
            root.joinpath("latest.jsonl").write_bytes(raw())
            shell = '''set -eu
source ./version.sh
log() { :; }
date() { printf '%s\\n' 20261010_000000; }
HISTORY_DIR=. HISTORY_FILE=latest.jsonl
archive_history 1234
first="$LAST_CREATED_ARCHIVE_FILE"
printf '{}\\n' > "$HISTORY_FILE"
if archive_history 1234; then exit 1; fi
[ -z "$LAST_CREATED_ARCHIVE_FILE" ]
printf '%s\\n' "$first"
'''
            run = subprocess.run(["bash", "-c", shell], cwd=root, text=True, capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            first = run.stdout.strip()
            self.assertEqual(root.joinpath(first).read_bytes(), raw())
            self.assertEqual({p.name for p in root.iterdir() if p.is_file()},
                             {"version.sh", "latest.jsonl", Path(first).name})


if __name__ == "__main__":
    unittest.main()
