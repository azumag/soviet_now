"""issue #132 P0-5: manifest だけで A/B の判定が再現できること。

判定器は manifest の事前登録以外の条件を読まない。害停止・採用・棄却の各境界と、
欠測 (壊れた行・tainted・idx 重複) の扱いを固定する。
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ab_verdict  # noqa: E402

MANIFEST = {
    "experiment": "test",
    "pattern": "ABBA",
    "preregistration": {"rule": {
        "primary": "score", "primary_kind": "block_diff", "adopt_ci_lower_gt": 0.0,
        "harm_min_blocks": 6, "harm_ucb_lt": 0.0, "futility": False,
        "final_blocks": 3, "final_games_per_arm": 6, "guardrail_t15_ratio": 0.5}},
}


def block(i, a_score, b_score, **kw):
    base = i * 4
    return [dict({"idx": base, "arm": "A", "score": a_score, "turns": 100}, **kw),
            dict({"idx": base + 1, "arm": "B", "score": b_score, "turns": 100}, **kw),
            dict({"idx": base + 2, "arm": "B", "score": b_score, "turns": 100}, **kw),
            dict({"idx": base + 3, "arm": "A", "score": a_score, "turns": 100}, **kw)]


def rows(n, a, b, jitter=0):
    out = []
    for i in range(n):
        out += block(i, a + (i % 2) * jitter, b - (i % 2) * jitter)
    return out


class AbVerdictTests(unittest.TestCase):
    def test_continue_before_final(self):
        r = ab_verdict.evaluate(MANIFEST, rows(2, 100, 200, jitter=10))
        self.assertEqual(r["verdict"], "CONTINUE")

    def test_final_triggers_on_either_threshold(self):
        """"k=50 (各100試合)" のように同じ節目を 2 通りで書いた事前登録では、
        末尾の端数ブロックで片方だけ満たす状態が起きる (v748 vs v752 は k=49 / n=100)。
        どちらかを満たせば最終判定に入る。"""
        m = json.loads(json.dumps(MANIFEST))
        m["preregistration"]["rule"]["final_blocks"] = 99      # ブロック側は未達
        m["preregistration"]["rule"]["final_games_per_arm"] = 6  # 試合数側は達成
        self.assertEqual(ab_verdict.evaluate(m, rows(3, 100, 300, jitter=10))["verdict"], "ADOPT")

    def test_adopt_when_ci_lower_positive(self):
        r = ab_verdict.evaluate(MANIFEST, rows(3, 100, 300, jitter=10))
        self.assertEqual(r["verdict"], "ADOPT", r["reason"])
        self.assertGreater(r["ci90_lower"], 0)

    def test_reject_when_ci_lower_not_positive(self):
        r = ab_verdict.evaluate(MANIFEST, rows(3, 300, 100, jitter=10))
        self.assertEqual(r["verdict"], "REJECT", r["reason"])

    def test_harm_stop_needs_min_blocks(self):
        m = json.loads(json.dumps(MANIFEST))
        m["preregistration"]["rule"]["harm_min_blocks"] = 3
        m["preregistration"]["rule"]["final_blocks"] = 99
        m["preregistration"]["rule"]["final_games_per_arm"] = 999  # 最終判定に入らせない
        r = ab_verdict.evaluate(m, rows(3, 500, 100, jitter=10))
        self.assertEqual(r["verdict"], "HARM_STOP", r["reason"])
        # min_blocks を上げれば同じデータでも止めない
        m["preregistration"]["rule"]["harm_min_blocks"] = 10
        self.assertEqual(ab_verdict.evaluate(m, rows(3, 500, 100, jitter=10))["verdict"], "CONTINUE")

    def test_guardrail_blocks_adoption(self):
        data = rows(3, 100, 300, jitter=10)
        for r_ in data:
            if r_["arm"] == "A":
                r_["first_turn_t15"] = 120  # A は全試合 T15 到達、B は 0
        out = ab_verdict.evaluate(MANIFEST, data)
        self.assertFalse(out["guardrail_ok"])
        self.assertEqual(out["verdict"], "REJECT")
        self.assertIn("guardrail", out["reason"])

    def test_excludes_are_counted_not_silent(self):
        data = rows(3, 100, 300, jitter=10)
        data.append({"_malformed": True})
        data.append(dict(data[0]))              # idx 重複
        data[1] = dict(data[1], tainted=True)   # tainted
        out = ab_verdict.evaluate(MANIFEST, data)
        self.assertEqual(out["excluded"]["malformed"], 1)
        self.assertEqual(out["excluded"]["tainted"], 1)
        self.assertEqual(out["excluded"]["duplicate_idx"], 1)

    def test_soviet_is_reported(self):
        data = rows(3, 100, 300, jitter=10)
        data[1]["soviet_created"] = True
        out = ab_verdict.evaluate(MANIFEST, data)
        self.assertEqual(out["soviet_created"]["B"], 1)

    def test_rule_comes_only_from_manifest(self):
        m = json.loads(json.dumps(MANIFEST))
        m["preregistration"]["rule"]["adopt_ci_lower_gt"] = 100000.0
        self.assertEqual(ab_verdict.evaluate(m, rows(3, 100, 300, jitter=10))["verdict"], "REJECT")


if __name__ == "__main__":
    unittest.main()


class HarmStopCalibrationTests(unittest.TestCase):
    """issue #132: 逐次害停止の較正。

    2026-08-31 に測り直した。純粋な A/A の ledger は存在しないので (当初 A/A と誤認していた
    2026-08-27 のものは実際には別戦略どうしの A/B)、root 370 試合を時系列に並べて 4 試合ごとに
    腕ラベルを無作為配分した合成 A/A で測った。旧規則 (k>=6 から毎ブロック UCB90<0) の誤発火は
    score 37.5% / merges_per_turn 41.2%、新既定 (k>=10 / UCB99) はどちらも 9.8%。
    大きな害 (score -400 / merges_per_turn -0.08) への検出力はどちらも 100%。
    既存 manifest の凍結した規則は変えない。
    """

    def test_default_is_the_calibrated_rule(self):
        r = ab_verdict.rule({})
        self.assertEqual(r["harm_min_blocks"], ab_verdict.HARM_MIN_BLOCKS_DEFAULT)
        self.assertAlmostEqual(r["harm_z"], ab_verdict.Z99, places=4)

    def test_legacy_manifest_keeps_z90(self):
        """harm_min_blocks を明示していた旧 manifest は当時の UCB90 のまま評価する。"""
        m = {"preregistration": {"rule": {"harm_min_blocks": 6}}}
        self.assertAlmostEqual(ab_verdict.rule(m)["harm_z"], ab_verdict.Z90, places=4)

    def test_explicit_harm_z_wins(self):
        m = {"preregistration": {"rule": {"harm_min_blocks": 6, "harm_z": 3.09}}}
        self.assertAlmostEqual(ab_verdict.rule(m)["harm_z"], 3.09, places=4)

    def test_stricter_z_stops_less(self):
        """境界域のデータでは z を上げると止まらなくなる。

        ブロック差を [-380, +20] × 3 にすると平均 -180 / SE 81.6 なので
        UCB90 = -75 (止まる) だが UCB99.9 = +72 (止まらない)。
        """
        data = []
        for i in range(6):
            a, b = (400, 20) if i % 2 == 0 else (100, 120)
            data += block(i, a, b)
        loose = {"pattern": "ABBA", "preregistration": {"rule": {
            "harm_min_blocks": 3, "harm_z": 1.2816, "final_blocks": 99, "final_games_per_arm": 999}}}
        strict = json.loads(json.dumps(loose))
        strict["preregistration"]["rule"]["harm_z"] = 3.09
        self.assertEqual(ab_verdict.evaluate(loose, data)["verdict"], "HARM_STOP")
        self.assertEqual(ab_verdict.evaluate(strict, data)["verdict"], "CONTINUE")


class FutilityStopTests(unittest.TestCase):
    """無益停止 (条件付き検出力) を固定する。

    2026-09-03 の合成 A/A 測定: 真に横ばいの候補は中央 k=22 (約 7 時間) で打ち切れる。
    ただし設計が非力だと本物の候補も殺すので、必要な k を確保した実験でのみ有効化する。
    既定は無効で、旧 manifest の挙動は変わらない。
    """

    def _manifest(self, **rule):
        base = {"primary": "score", "primary_kind": "block_diff", "adopt_ci_lower_gt": 0.0,
                "harm_min_blocks": 10, "harm_ucb_lt": 0.0, "harm_z": 2.3263,
                "final_blocks": 200, "final_games_per_arm": 400}
        base.update(rule)
        return {"pattern": "ABBA", "preregistration": {"rule": base}}

    def _flat(self, n):
        # 期待値ゼロ・ばらつきありのブロック差を作る
        out = []
        for i in range(n):
            a, b = (100, 120) if i % 2 == 0 else (120, 100)
            out += block(i, a, b)
        return out

    def test_disabled_by_default_keeps_old_behaviour(self):
        got = ab_verdict.evaluate(self._manifest(), self._flat(40))
        self.assertEqual(got["verdict"], "CONTINUE")

    def test_flat_candidate_is_stopped_for_futility(self):
        got = ab_verdict.evaluate(
            self._manifest(futility_cp_lt=0.10, futility_min_blocks=20), self._flat(40))
        self.assertEqual(got["verdict"], "FUTILITY_STOP")
        self.assertIn("conditional power", got["reason"])

    def test_not_stopped_before_the_minimum_block_count(self):
        got = ab_verdict.evaluate(
            self._manifest(futility_cp_lt=0.10, futility_min_blocks=30), self._flat(24))
        self.assertEqual(got["verdict"], "CONTINUE")

    def test_a_clearly_winning_candidate_is_not_stopped(self):
        out = []
        for i in range(40):
            a, b = (100, 900) if i % 2 == 0 else (120, 920)
            out += block(i, a, b)
        got = ab_verdict.evaluate(
            self._manifest(futility_cp_lt=0.10, futility_min_blocks=20), out)
        self.assertNotEqual(got["verdict"], "FUTILITY_STOP")

    def test_harm_stop_takes_precedence_over_futility(self):
        # ブロック差にばらつきを持たせる (差が一定だと sd=0 になり、
        # 既存実装では se が偽になって害停止も無益停止も評価されない)。
        out = []
        for i in range(40):
            a, b = (900, 100) if i % 2 == 0 else (700, 120)
            out += block(i, a, b)
        got = ab_verdict.evaluate(
            self._manifest(futility_cp_lt=0.10, futility_min_blocks=20), out)
        self.assertEqual(got["verdict"], "HARM_STOP")

    def test_conditional_power_is_monotone_in_the_observed_mean(self):
        low = ab_verdict.conditional_power(0.0, 1.0, 50, 200, 1.2816, 0.0)
        high = ab_verdict.conditional_power(0.5, 1.0, 50, 200, 1.2816, 0.0)
        self.assertLess(low, high)
        self.assertIsNone(ab_verdict.conditional_power(0.0, 1.0, 200, 200, 1.2816, 0.0))
