import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class ContinuousImproveGameplayTests(unittest.TestCase):
    def run_bash(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script, "bash", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_continuous_toggle_is_hot_reloadable_without_overriding_explore_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/core/runtime_toggles.sh"
unset IMPROVE_KEEP_MAIN_GAME_RUNNING
! _improve_keep_main_game_running

mkdir -p "$2/tmp/state"
printf '%s\n' \
    'MIN_GAMES_BEFORE_IMPROVE=999' \
    'MIN_GAMES_BEFORE_REGRESSION=888' \
    'IMPROVE_KEEP_MAIN_GAME_RUNNING=1' >"$2/.env"
cd "$2"
ELOOP_LIB_DIR="$2"
EXPLORE_MODE=1
MIN_GAMES_BEFORE_IMPROVE=3
MIN_GAMES_BEFORE_REGRESSION=4
RUNTIME_TOGGLES_MIN_INTERVAL=0
reload_runtime_toggles_force

_improve_keep_main_game_running
[ "$MIN_GAMES_BEFORE_IMPROVE" = 3 ]
[ "$MIN_GAMES_BEFORE_REGRESSION" = 4 ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_game_snapshot_stays_stable_while_next_strategy_is_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/core/strategy_runtime.sh"
root="$2"
mkdir -p "$root/current_helpers" "$root/next_helpers" "$root/state"
printf 'OLD_STRATEGY\n' >"$root/strategy.py"
printf 'OLD_HELPER\n' >"$root/current_helpers/helper.py"
printf 'NEW_STRATEGY\n' >"$root/next.py"
printf 'NEW_HELPER\n' >"$root/next_helpers/helper.py"
STRATEGY_APPLY_LOCK_FILE="$root/state/apply.lock"

strategy_runtime_create_game_snapshot \
    "$root/strategy.py" "$root/runtime" "$root/game_snapshot.py" \
    "$root/current_helpers"
strategy_runtime_atomic_apply_bundle \
    "$root/next.py" "$root/strategy.py" \
    "$root/next_helpers" "$root/current_helpers"

[ "$(cat "$root/runtime/strategy.py")" = OLD_STRATEGY ]
[ "$(cat "$root/runtime/strategy_helpers/helper.py")" = OLD_HELPER ]
[ "$(cat "$root/game_snapshot.py")" = OLD_STRATEGY ]
[ "$(cat "$root/strategy.py")" = NEW_STRATEGY ]
[ "$(cat "$root/current_helpers/helper.py")" = NEW_HELPER ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_main_loop_does_not_pause_for_improvement_in_continuous_mode(self):
        loop = (REPO_ROOT / "soren_loop.sh").read_text(encoding="utf-8")
        running_branch = loop.split(
            'if [ "$_improve_running_now" -eq 1 ] && _improve_keep_main_game_running; then',
            1,
        )[1].split('elif [ "$_improve_running_now" -eq 1 ]; then', 1)[0]
        self.assertNotRegex(running_branch, re.compile(r"^\s*continue\s*$", re.MULTILINE))
        self.assertIn("_run_improve_runtime_monitor", running_branch)
        self.assertIn(
            'if ! _improve_keep_main_game_running && _is_improve_running; then',
            loop,
        )
        self.assertIn(
            'if ! _improve_keep_main_game_running && [ -f "$IMPROVE_LOCK_FILE" ]',
            loop,
        )

    def test_continuous_mode_keeps_parallel_evaluation_non_blocking(self):
        improve = (REPO_ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        self.assertIn(
            '_improve_keep_main_game_running && main_loop_arg="--no-block-main-loop"',
            improve,
        )
        self.assertIn(
            '_improve_keep_main_game_running && wildcard_main_loop_arg="--no-block-main-loop"',
            improve,
        )
        lifecycle = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        self.assertIn(
            "継続プレイ設定: soren91代打を起動せず、メインゲームと改善を並行実行",
            lifecycle,
        )


if __name__ == "__main__":
    unittest.main()
