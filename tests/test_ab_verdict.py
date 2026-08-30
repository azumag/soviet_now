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
