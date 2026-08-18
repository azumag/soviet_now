import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class RadioPeakHourDeferTests(unittest.TestCase):
    def run_bash(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script, "bash", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_should_defer_when_enabled_and_peak(self):
        # _radio_peak_hour_should_defer は _is_peak_hour_utc の結果と ENABLED トグルの
        # 組み合わせを返す。時刻に依存させず、_is_peak_hour_utc をスタブ化して
        # ENABLED 切り替えの挙動だけを検証する (時刻境界自体は improve 側テストが担保)。
        result = self.run_bash(
            r'''
set -e
source "$1/strategy/improve.sh"
sed -n '/^_radio_peak_hour_should_defer()/,/^}/p' "$1/broadcast/radio_engine.sh" > "$TMPDIR/defer_fn.sh"
. "$TMPDIR/defer_fn.sh"

# ピーク判定スタブ (外部 io / 現在時刻に依存させない)
_is_peak_hour_utc() { return 0; }

RADIO_PEAK_HOUR_DEFER_ENABLED=1
_radio_peak_hour_should_defer || exit 10

# ENABLED=0 ならピーク判定が true でも false
RADIO_PEAK_HOUR_DEFER_ENABLED=0
if _radio_peak_hour_should_defer; then exit 12; fi

# ピーク判定が false なら default (ENABLED=1) でも false
_is_peak_hour_utc() { return 1; }
RADIO_PEAK_HOUR_DEFER_ENABLED=1
if _radio_peak_hour_should_defer; then exit 13; fi

exit 0
''',
            str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_ranges_match_improve(self):
        # gate の既定範囲 (config.sh) が improve と同一であることを静的検証する。
        result = self.run_bash(
            r'''
set -e
cfg=$(grep -m1 '^RADIO_PEAK_HOUR_UTC_RANGES=' "$1/core/config.sh")
imp=$(grep -m1 '^IMPROVE_PEAK_HOUR_UTC_RANGES=' "$1/core/config.sh")
cfg_def=${cfg#*:-}
imp_def=${imp#*:-}
cfg_def=${cfg_def%\"*}
imp_def=${imp_def%\"*}
[ "$cfg_def" = "$imp_def" ] || { echo "radio default differs from improve: $cfg_def vs $imp_def" >&2; exit 1; }
exit 0
''',
            str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_wired_into_generate_and_play(self):
        src = (REPO_ROOT / "broadcast" / "radio_engine.sh").read_text(encoding="utf-8")
        self.assertIn("_radio_peak_hour_should_defer", src)
        self.assertIn("peak_hour_deferred", src)
        self.assertIn("_play_deferred_radio_queue_once", src)

    def test_toggle_wired(self):
        for path in (
            "core/runtime_toggles.sh",
            "set_toggle.sh",
        ):
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
            self.assertIn("RADIO_PEAK_HOUR_DEFER_ENABLED", text)
            self.assertIn("RADIO_PEAK_HOUR_UTC_RANGES", text)
