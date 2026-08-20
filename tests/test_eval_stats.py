"""Tests for lib/eval_stats.py.

Two jobs:
1. Prove `quantile()`/`metrics()` are bit-compatible with the (still
   unmodified) embedded implementations in strategy/regression.sh, so a
   later change that replaces those heredocs with imports from this module
   is provably behavior-preserving.
2. Exercise the new statistical-gate / instadeath-classifier code
   (`decide`, `classify_instadeath`, `fisher_one_sided`, ...) in isolation,
   since nothing calls it yet (see soren-stat-gate-design.md Phase 0/1/2).
"""

import math
import random
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import eval_stats  # noqa: E402


# ---------------------------------------------------------------------------
# Reference implementations transcribed verbatim from strategy/regression.sh
# as of commit 7b3842965 (soviet_now submodule), for cross-checking. If these
# ever drift from the real file, QuantileCompatibilityTests and
# MetricsCompatibilityTests below are meant to catch it manually — grep the
# five line numbers cited in each _ref_* docstring and diff.
# ---------------------------------------------------------------------------

def _ref_quantile(vals, p):
    """strategy/regression.sh lines 40, 1116, 2430, 2675, 3426 (identical)."""
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _ref_metrics_variant_a(scores):
    """strategy/regression.sh line 52 (fixed weights, dict+mean, guards
    empty input only -- no min_games floor)."""
    if not scores:
        return None
    xs = [int(x) for x in scores]
    n = len(xs)
    mean = sum(xs) / n
    p25 = _ref_quantile(xs, 0.25)
    p50 = _ref_quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {"comp": comp, "p50": p50, "p25": p25, "lcb": lcb, "mean": mean, "n": n}


def _ref_metrics_variant_b(scores, min_games, lcb_z, w_p50, w_p25, w_lcb):
    """strategy/regression.sh line 1128 (parameterized weights/z, dict
    without "mean", gated by min_games)."""
    xs = [int(v) for v in scores]
    if len(xs) < min_games:
        return None
    n = len(xs)
    mean = sum(xs) / n
    p25 = _ref_quantile(xs, 0.25)
    p50 = _ref_quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - lcb_z * (std / math.sqrt(n))
    comp = (w_p50 * p50) + (w_p25 * p25) + (w_lcb * lcb)
    return {"comp": comp, "p50": p50, "p25": p25, "lcb": lcb, "n": n}


def _ref_metrics_variant_c(scores, lcb_z, w_p50, w_p25, w_lcb):
    """strategy/regression.sh line 2442 (tuple return, no min_games gate;
    caller is responsible for ensuring non-empty input)."""
    n = len(scores)
    mean = sum(scores) / n
    p25 = _ref_quantile(scores, 0.25)
    p50 = _ref_quantile(scores, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in scores) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - lcb_z * (std / math.sqrt(n))
    composite = w_p50 * p50 + w_p25 * p25 + w_lcb * lcb
    return composite, p50, p25, lcb, n


def _ref_composite_score_variant_d(scores, lcb_z, w_p50, w_p25, w_lcb):
    """strategy/regression.sh line 2687 (tuple return without lcb)."""
    n = len(scores)
    mean = sum(scores) / n
    p25 = _ref_quantile(scores, 0.25)
    p50 = _ref_quantile(scores, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in scores) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - lcb_z * (std / math.sqrt(n))
    return w_p50 * p50 + w_p25 * p25 + w_lcb * lcb, p50, p25, n


def _ref_metrics_variant_e(scores):
    """strategy/regression.sh line 3438: a fifth, previously-untested shape
    -- fixed weights/z like variant A, but dict WITHOUT "mean" like variant
    B, and guards only empty input (no external min_games floor) like
    variant A. Distinct enough from A-D that it needs its own reference
    (2026-08-20 review finding 1.1)."""
    xs = [int(v) for v in scores]
    if not xs:
        return None
    n = len(xs)
    mean = sum(xs) / n
    p25 = _ref_quantile(xs, 0.25)
    p50 = _ref_quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {"comp": comp, "p50": p50, "p25": p25, "lcb": lcb, "n": n}


SAMPLE_SCORE_LISTS = [
    [],
    [0],
    [7823],
    [100, 100, 100],
    [8814, 12649, 7823, 9078, 13439, 8326, 12420, 11281],
    [0, 0, 0, 22645, 19047],
    list(range(1, 21)),
]


class QuantileCompatibilityTests(unittest.TestCase):
    def test_matches_reference_on_fixed_samples(self):
        for xs in SAMPLE_SCORE_LISTS:
            for p in (0.0, 0.25, 0.5, 0.75, 0.9, 1.0):
                self.assertEqual(eval_stats.quantile(xs, p), _ref_quantile(xs, p),
                                  msg=f"xs={xs} p={p}")

    def test_matches_reference_on_random_samples(self):
        rng = random.Random(20260820)
        for _ in range(200):
            n = rng.randint(0, 40)
            xs = [rng.randint(0, 30000) for _ in range(n)]
            p = rng.random()
            self.assertEqual(eval_stats.quantile(xs, p), _ref_quantile(xs, p))

    def test_known_values(self):
        self.assertEqual(eval_stats.quantile([], 0.5), 0.0)
        self.assertEqual(eval_stats.quantile([42], 0.9), 42.0)
        self.assertEqual(eval_stats.quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(eval_stats.quantile([1, 2, 3, 4], 0.0), 1.0)
        self.assertEqual(eval_stats.quantile([1, 2, 3, 4], 1.0), 4.0)


class MetricsCompatibilityTests(unittest.TestCase):
    """metrics() with each call site's actual argument pattern must equal
    that call site's reference implementation exactly (float equality;
    both do the same arithmetic in the same order)."""

    def test_variant_a_fixed_weights_dict_with_mean(self):
        for xs in SAMPLE_SCORE_LISTS:
            expected = _ref_metrics_variant_a(xs)
            got = eval_stats.metrics(xs)  # library defaults == variant A's fixed constants
            self.assertEqual(got, expected, msg=f"xs={xs}")

    def test_variant_a_empty_returns_none(self):
        self.assertIsNone(eval_stats.metrics([]))

    def test_variant_b_parameterized_with_min_games_gate(self):
        cases = [
            (SAMPLE_SCORE_LISTS[4], 5, 1.6, 0.5, 0.3, 0.2),
            (SAMPLE_SCORE_LISTS[4], 20, 1.6, 0.5, 0.3, 0.2),  # below floor -> None
            (SAMPLE_SCORE_LISTS[6], 10, 2.0, 0.4, 0.4, 0.2),
        ]
        for xs, min_games, lcb_z, w_p50, w_p25, w_lcb in cases:
            expected = _ref_metrics_variant_b(xs, min_games, lcb_z, w_p50, w_p25, w_lcb)
            got = eval_stats.metrics(xs, w_p50=w_p50, w_p25=w_p25, w_lcb=w_lcb,
                                      lcb_z=lcb_z, min_games=min_games)
            if expected is None:
                self.assertIsNone(got)
            else:
                self.assertEqual(got["p50"], expected["p50"])
                self.assertEqual(got["p25"], expected["p25"])
                self.assertEqual(got["lcb"], expected["lcb"])
                self.assertEqual(got["comp"], expected["comp"])
                self.assertEqual(got["n"], expected["n"])

    def test_variant_c_tuple_return(self):
        xs = SAMPLE_SCORE_LISTS[4]
        lcb_z, w_p50, w_p25, w_lcb = 1.28, 0.6, 0.25, 0.15
        comp_ref, p50_ref, p25_ref, lcb_ref, n_ref = _ref_metrics_variant_c(
            xs, lcb_z, w_p50, w_p25, w_lcb)
        got = eval_stats.metrics(xs, w_p50=w_p50, w_p25=w_p25, w_lcb=w_lcb, lcb_z=lcb_z)
        self.assertEqual(got["comp"], comp_ref)
        self.assertEqual(got["p50"], p50_ref)
        self.assertEqual(got["p25"], p25_ref)
        self.assertEqual(got["lcb"], lcb_ref)
        self.assertEqual(got["n"], n_ref)

    def test_variant_d_composite_score_tuple(self):
        xs = SAMPLE_SCORE_LISTS[4]
        lcb_z, w_p50, w_p25, w_lcb = 1.28, 0.55, 0.30, 0.15
        comp_ref, p50_ref, p25_ref, n_ref = _ref_composite_score_variant_d(
            xs, lcb_z, w_p50, w_p25, w_lcb)
        got = eval_stats.metrics(xs, w_p50=w_p50, w_p25=w_p25, w_lcb=w_lcb, lcb_z=lcb_z)
        self.assertEqual(got["comp"], comp_ref)
        self.assertEqual(got["p50"], p50_ref)
        self.assertEqual(got["p25"], p25_ref)
        self.assertEqual(got["n"], n_ref)

    def test_variant_e_fixed_weights_dict_without_mean(self):
        for xs in SAMPLE_SCORE_LISTS:
            expected = _ref_metrics_variant_e(xs)
            got = eval_stats.metrics(xs)  # library defaults match variant E's fixed constants too
            if expected is None:
                self.assertIsNone(got)
            else:
                self.assertEqual(got["comp"], expected["comp"])
                self.assertEqual(got["p50"], expected["p50"])
                self.assertEqual(got["p25"], expected["p25"])
                self.assertEqual(got["lcb"], expected["lcb"])
                self.assertEqual(got["n"], expected["n"])

    def test_random_cross_check_all_variants(self):
        rng = random.Random(3141592)
        for _ in range(100):
            n = rng.randint(1, 50)
            xs = [rng.randint(0, 30000) for _ in range(n)]
            lcb_z = rng.uniform(0.5, 2.5)
            w_p50, w_p25, w_lcb = rng.random(), rng.random(), rng.random()
            got = eval_stats.metrics(xs, w_p50=w_p50, w_p25=w_p25, w_lcb=w_lcb, lcb_z=lcb_z)
            comp_ref, p50_ref, p25_ref, lcb_ref, n_ref = _ref_metrics_variant_c(
                xs, lcb_z, w_p50, w_p25, w_lcb)
            self.assertAlmostEqual(got["comp"], comp_ref, places=9)
            self.assertAlmostEqual(got["lcb"], lcb_ref, places=9)
            self.assertEqual(got["n"], n_ref)


class InstadeathHelperTests(unittest.TestCase):
    def test_alive_filters_below_threshold(self):
        self.assertEqual(eval_stats.alive([0, 2999, 3000, 3001, 30000]), [3000, 3001, 30000])

    def test_alive_custom_threshold(self):
        self.assertEqual(eval_stats.alive([100, 200, 300], threshold=200), [200, 300])

    def test_dead_count(self):
        self.assertEqual(eval_stats.dead_count([0, 0, 5000, 6000]), 2)
        self.assertEqual(eval_stats.dead_count([]), 0)

    def test_alive_is_identity_when_nothing_below_threshold(self):
        # Phase 1's acceptance criterion (soren-stat-gate-design.md B):
        # in the current clean-period regime (0 instadeaths observed), the
        # instadeath split must be a pure no-op for metrics() -- comp/p50/
        # p25/lcb/n computed on the raw scores and on alive(scores) must be
        # bit-identical, since alive() should filter nothing. Fixture shaped
        # like the VM's real rolling_scores.json (n=100, values in the
        # thousands, none below DEAD_EVAL_THRESHOLD).
        rng = random.Random(20260820)
        scores = [rng.randint(5000, 25000) for _ in range(100)]
        self.assertEqual(eval_stats.alive(scores), scores)
        m_raw = eval_stats.metrics(scores)
        m_alive = eval_stats.metrics(eval_stats.alive(scores))
        self.assertEqual(m_raw, m_alive)

    def test_run_lengths(self):
        self.assertEqual(eval_stats.run_lengths([]), [])
        self.assertEqual(eval_stats.run_lengths([False, False]), [])
        self.assertEqual(eval_stats.run_lengths([True, True, False, True]), [2, 1])
        self.assertEqual(eval_stats.run_lengths([True]), [1])

    def test_burst_ratio_iid_like_is_near_one(self):
        # Deterministic pseudo-iid pattern: every 5th flag is a death (p=0.2,
        # runs of length 1 only) -> observed mean run len 1, iid-expected
        # mean run len 1/(1-0.2)=1.25 -> ratio 0.8, not >>1.
        flags = [(i % 5 == 0) for i in range(100)]
        ratio = eval_stats.burst_ratio(flags)
        self.assertAlmostEqual(ratio, 0.8, places=9)
        self.assertGreater(ratio, 0.5)
        self.assertLess(ratio, 1.5)

    def test_burst_ratio_total_outage_is_maximal_not_zero(self):
        # 2026-08-20 review bug: p==1.0 used to hit the (0, inf) guard and
        # silently return 0.0 -- the SAME answer as "no bursting at all" --
        # for the single worst case (a fully-dead window) the whole
        # burst-detector exists to catch. Must now report the observed run
        # length itself (one run spanning the whole window).
        flags = [True] * 20
        self.assertEqual(eval_stats.burst_ratio(flags), 20.0)

    def test_burst_ratio_clustered_is_high(self):
        # One long run of 50 deaths then 50 alive: p=0.5, iid-expected mean
        # run len 1/(1-0.5)=2, observed mean run len 50 -> ratio 25.
        flags = [True] * 50 + [False] * 50
        ratio = eval_stats.burst_ratio(flags)
        self.assertGreater(ratio, 10.0)

    def test_burst_ratio_empty(self):
        self.assertEqual(eval_stats.burst_ratio([]), 0.0)

    def test_fisher_one_sided_no_difference(self):
        p = eval_stats.fisher_one_sided(5, 100, 5, 100)
        self.assertGreater(p, 0.4)  # roughly symmetric, not significant

    def test_fisher_one_sided_strong_difference(self):
        # 40/100 dead in current vs 2/100 in reference: should be extremely
        # significant that current's dead rate is higher.
        p = eval_stats.fisher_one_sided(40, 100, 2, 100)
        self.assertLess(p, 0.001)

    def test_fisher_one_sided_edge_cases(self):
        self.assertEqual(eval_stats.fisher_one_sided(0, 0, 0, 0), 1.0)
        self.assertEqual(eval_stats.fisher_one_sided(0, 10, 0, 10), 1.0)
        self.assertEqual(eval_stats.fisher_one_sided(10, 10, 10, 10), 1.0)  # K == N

    def test_classify_instadeath_normal_below_alert_rate(self):
        flags = [False] * 95 + [True] * 5  # 5% < default 10% alert rate
        verdict, detail = eval_stats.classify_instadeath(flags)
        self.assertEqual(verdict, "NORMAL")

    def test_classify_instadeath_harness_via_burst_and_hard_ratio(self):
        # Matches the 2026-08-06 VM outage fingerprint: high rate, clustered
        # runs, mostly raw==0 deaths.
        flags = [True] * 60 + [False] * 40
        verdict, detail = eval_stats.classify_instadeath(
            flags, cfg={"cur_dead_hard_ratio": 0.6})
        self.assertEqual(verdict, "HARNESS")
        self.assertGreaterEqual(detail["votes_harness"], 2)

    def test_classify_instadeath_fisher_p_present_even_when_harness(self):
        # Phase 0 review follow-up item #1 (carried into Phase 1's monitor,
        # which persists `detail`): fisher_p must be computed whenever
        # ref_flags is given, even on the HARNESS early-return, not only in
        # the UNKNOWN fallthrough. The verdict itself is unaffected.
        flags = [True] * 60 + [False] * 40
        ref = [False] * 100
        verdict, detail = eval_stats.classify_instadeath(
            flags, ref_flags=ref, cfg={"cur_dead_hard_ratio": 0.6})
        self.assertEqual(verdict, "HARNESS")
        self.assertIn("fisher_p", detail)
        self.assertLess(detail["fisher_p"], 0.01)

    def test_classify_instadeath_no_fisher_p_without_ref_flags(self):
        flags = [True] * 60 + [False] * 40
        verdict, detail = eval_stats.classify_instadeath(
            flags, cfg={"cur_dead_hard_ratio": 0.6})
        self.assertNotIn("fisher_p", detail)

    def test_classify_instadeath_harness_for_near_total_outage(self):
        # 2026-08-20 review round 2, issue 6: burst_ratio alone is blind in
        # the p=0.85..0.99 band (1/(1-p) diverges about as fast as the
        # observed run length does), so a 19/20-dead window with one death
        # detector already voting (hard_ratio) used to stay at votes=1 and
        # classify UNKNOWN instead of HARNESS. The new near-total-rate
        # detector (rate >= 0.90) supplies the second vote here.
        flags = [True] * 19 + [False]
        verdict, detail = eval_stats.classify_instadeath(
            flags, cfg={"cur_dead_hard_ratio": 0.6})
        self.assertEqual(verdict, "HARNESS")
        self.assertGreaterEqual(detail["votes_harness"], 2)

    def test_classify_instadeath_below_near_total_rate_threshold_unchanged(self):
        # Below the 0.90 threshold, behavior is unchanged by the issue-6 fix:
        # a 17/20 (85%) window with only the hard_ratio vote still can't
        # reach HARNESS on its own. This isn't claimed to be ideal detection
        # -- just pinning that the new detector doesn't overreach past its
        # documented threshold.
        flags = [True] * 17 + [False] * 3
        verdict, detail = eval_stats.classify_instadeath(
            flags, cfg={"cur_dead_hard_ratio": 0.6})
        self.assertEqual(verdict, "UNKNOWN")
        self.assertEqual(detail["votes_harness"], 1)

    def test_classify_instadeath_harness_for_total_outage_window(self):
        # The scenario DEAD_QUARANTINE_WINDOW/RATE exist for: every game in
        # the most recent 20-game window died (shape of the 2026-08-06,
        # 711-game-long outage run). Before the burst_ratio fix this
        # returned UNKNOWN (votes_harness=1) and quarantine never fired.
        flags = [True] * 20
        verdict, detail = eval_stats.classify_instadeath(
            flags, cfg={"cur_dead_hard_ratio": 0.6})
        self.assertEqual(verdict, "HARNESS")
        self.assertGreaterEqual(detail["votes_harness"], 2)

    def test_classify_instadeath_strategy_via_fisher(self):
        # 50% dead but alternating (not clustered), so burst_ratio stays low
        # and no HARNESS vote fires; the elevated dead rate should instead be
        # attributed to the strategy via the Fisher check against a clean
        # reference (2026-08-20 review: this test previously shadowed `cur`
        # with a second assignment, leaving the first dead, and asserted
        # nothing conditional on the actual verdict).
        cur = [(i % 2 == 0) for i in range(50)]
        ref = [False] * 50  # 0% dead reference
        verdict, detail = eval_stats.classify_instadeath(cur, ref_flags=ref)
        self.assertEqual(verdict, "STRATEGY")
        self.assertIn("fisher_p", detail)
        self.assertLess(detail["fisher_p"], 0.01)

    def test_classify_instadeath_empty(self):
        verdict, detail = eval_stats.classify_instadeath([])
        self.assertEqual(verdict, "NORMAL")


class WinsorAndWelchTests(unittest.TestCase):
    def test_winsor_limits_empty(self):
        self.assertEqual(eval_stats.winsor_limits([]), (0.0, 0.0))

    def test_winsor_limits_matches_quantile(self):
        pool = list(range(1, 101))
        lo, hi = eval_stats.winsor_limits(pool, 0.05, 0.95)
        self.assertEqual(lo, eval_stats.quantile(pool, 0.05))
        self.assertEqual(hi, eval_stats.quantile(pool, 0.95))

    def test_wstats_clips_outliers(self):
        xs = [10, 10, 10, 10, 10000]
        wmean, var, n = eval_stats.wstats(xs, lo=5, hi=15)
        self.assertEqual(n, 5)
        # the 10000 outlier is clipped to 15, so mean should be close to 11
        clipped = [10, 10, 10, 10, 15]
        expected_mean = sum(clipped) / 5
        expected_var = sum((x - expected_mean) ** 2 for x in clipped) / (5 - 1)
        self.assertAlmostEqual(wmean, expected_mean, places=9)
        # unclipped variance would be dominated by the 10000 outlier and be
        # enormous; the clipped variance must stay small (this is the whole
        # point of winsorizing before computing SE).
        self.assertAlmostEqual(var, expected_var, places=9)
        self.assertLess(var, 100)

    def test_wstats_empty(self):
        self.assertEqual(eval_stats.wstats([], 0, 10), (0.0, 0.0, 0))

    def test_wstats_single_element_zero_variance(self):
        wmean, var, n = eval_stats.wstats([7], 0, 10)
        self.assertEqual(n, 1)
        self.assertEqual(var, 0.0)

    def test_welch_bounds_symmetric_around_delta(self):
        delta, se, lcb, ucb = eval_stats.welch_bounds(100, 50, 20, 90, 40, 20, 0.05)
        self.assertAlmostEqual(delta, 10, places=9)
        self.assertAlmostEqual((lcb + ucb) / 2, delta, places=9)
        self.assertLess(lcb, delta)
        self.assertGreater(ucb, delta)

    def test_welch_bounds_zero_n_is_infinite(self):
        delta, se, lcb, ucb = eval_stats.welch_bounds(100, 50, 0, 90, 40, 20, 0.05)
        self.assertEqual(se, float("inf"))
        # an infinite SE must degrade the bounds to "no threshold can ever
        # fire", not leave them at some finite (and wrong) value.
        self.assertEqual(lcb, float("-inf"))
        self.assertEqual(ucb, float("inf"))

    def test_group_sequential_alpha(self):
        self.assertAlmostEqual(eval_stats.group_sequential_alpha(0.05, 4), 0.0125)
        self.assertEqual(eval_stats.group_sequential_alpha(0.05, 0), 0.05)
        self.assertEqual(eval_stats.group_sequential_alpha(0.05, None), 0.05)


class DecideTests(unittest.TestCase):
    """decide() is not wired into any caller yet; these tests exercise it
    standalone against synthetic score distributions."""

    def test_insufficient_reference_below_anchor_min_n(self):
        result = eval_stats.decide([9000] * 30, [9000] * 50,
                                    cfg={"stat_anchor_min_n": 100})
        self.assertEqual(result["verdict"], "INSUFFICIENT_REFERENCE")

    def test_not_a_look_between_look_points(self):
        result = eval_stats.decide([9000] * 30, [9000] * 100,
                                    cfg={"stat_anchor_min_n": 50, "stat_hard_min_n": 9999})
        self.assertEqual(result["verdict"], "NOT_A_LOOK")

    def test_identical_distributions_are_inconclusive_or_noninferior(self):
        rng = random.Random(99)
        ref = [rng.gauss(10000, 2000) for _ in range(100)]
        cur = [rng.gauss(10000, 2000) for _ in range(24)]
        result = eval_stats.decide(cur, ref, cfg={"stat_hard_min_n": 9999})
        self.assertIn(result["verdict"], ("INCONCLUSIVE", "NONINFERIOR"))

    def test_strongly_worse_current_triggers_hard_regression(self):
        rng = random.Random(7)
        ref = [rng.gauss(10000, 1000) for _ in range(100)]
        # mean well above DEAD_EVAL_THRESHOLD (3000) so alive() doesn't
        # incidentally filter any of these as instadeaths.
        cur = [rng.gauss(6000, 1000) for _ in range(20)]
        result = eval_stats.decide(cur, ref, cfg={"stat_hard_min_n": 16, "stat_hard_look_stride": 4})
        self.assertEqual(result["n_alive_cur"], 20)
        self.assertEqual(result["verdict"], "REGRESSION_HARD")

    def test_strongly_better_current_triggers_promote(self):
        rng = random.Random(11)
        ref = [rng.gauss(10000, 1500) for _ in range(100)]
        cur = [rng.gauss(16000, 1500) for _ in range(24)]
        result = eval_stats.decide(cur, ref, cfg={"stat_hard_min_n": 9999})
        self.assertEqual(result["verdict"], "PROMOTE")

    def test_dead_scores_are_excluded_before_comparison(self):
        # current has many instadeaths (eval<3000) mixed with normal scores;
        # decide() should filter them out via alive() before comparing.
        rng = random.Random(5)
        ref = [rng.gauss(10000, 1000) for _ in range(100)]
        cur_alive = [rng.gauss(10000, 1000) for _ in range(24)]
        cur = cur_alive + [0, 100, 500, 2999] * 5  # 20 instadeaths mixed in
        result = eval_stats.decide(cur, ref, cfg={"stat_hard_min_n": 9999})
        self.assertEqual(result["n_alive_cur"], 24)
        self.assertEqual(result["n_total_cur"], 24 + 20)
        self.assertIn(result["verdict"], ("INCONCLUSIVE", "NONINFERIOR"))

    def test_reference_eligibility_uses_total_games_not_alive_count(self):
        # 2026-08-20 review, issue 2: gating eligibility on n_alive_ref (not
        # n_total_ref) meant a completely normal anchor -- 100 games, one
        # instadeath among them -- silently failed eligibility every single
        # time, indistinguishable in the verdict from "anchor genuinely has
        # too few games yet".
        ref_100_total_1_dead = [9000] * 99 + [0]
        cur = [9000] * 24
        result = eval_stats.decide(cur, ref_100_total_1_dead, cfg={"stat_hard_min_n": 9999})
        self.assertEqual(result["n_total_ref"], 100)
        self.assertEqual(result["n_alive_ref"], 99)
        self.assertNotEqual(result["verdict"], "INSUFFICIENT_REFERENCE")

    def test_reference_insufficient_when_totally_dead_despite_100_games(self):
        # Distinct failure mode from the above: 100 total games but ZERO
        # alive after filtering. This must still be INSUFFICIENT_REFERENCE
        # (there is nothing to compare against), just for a different,
        # explicitly-labeled reason than "not enough games yet". Pin the
        # reason with a "reference"-specific substring, not just "alive" --
        # that word appears in BOTH the reference-side and current-side
        # reason strings, so it can't tell the two branches apart
        # (2026-08-20 review round 2, issue 5's test-tightening note).
        ref_all_dead = [0] * 100
        cur = [9000] * 24
        result = eval_stats.decide(cur, ref_all_dead, cfg={"stat_hard_min_n": 9999})
        self.assertEqual(result["verdict"], "INSUFFICIENT_REFERENCE")
        self.assertIn("reference arm has no alive scores", result["reason"])

    def test_current_arm_totally_dead_is_a_distinct_verdict(self):
        # 2026-08-20 review round 2, issue 5: the current-arm-all-dead case
        # used to share the INSUFFICIENT_REFERENCE verdict with genuine
        # reference-eligibility failures, even though it's a different (and
        # more severe) situation a caller should handle differently.
        ref = [9000] * 100
        cur_all_dead = [0] * 24
        result = eval_stats.decide(cur_all_dead, ref, cfg={"stat_hard_min_n": 9999})
        self.assertEqual(result["verdict"], "INSUFFICIENT_CURRENT")
        self.assertIn("current arm has no alive scores", result["reason"])

    def test_regression_soft_fires_on_moderate_gap(self):
        # A gap large enough to be significant at the SOFT layer's alpha but
        # exercised with the HARD layer disabled, so this specifically pins
        # REGRESSION_SOFT (previously untested; only HARD had coverage).
        rng = random.Random(1234)
        ref = [rng.gauss(10000, 1000) for _ in range(100)]
        cur = [rng.gauss(8000, 1000) for _ in range(24)]  # ~2000pt gap
        result = eval_stats.decide(cur, ref, cfg={"stat_hard_min_n": 9999})
        self.assertEqual(result["verdict"], "REGRESSION_SOFT")

    def test_promote_uses_alpha_promote_not_alpha_soft(self):
        # 2026-08-20 review, issue 3: PROMOTE previously reused alpha_soft
        # (0.05/4=0.0125), looser than the design's specified alpha_promote
        # (0.01/4=0.0025) -- exactly backwards for the path the whole gate
        # exists to guard against winner's-curse promotion. Pick a gap that
        # clears delta_promote at the LOOSER alpha_soft bar (proving the old
        # bug would have promoted here) but not at the stricter alpha_promote
        # bar the design actually specifies.
        rng = random.Random(4242)
        ref = [rng.gauss(10000, 1500) for _ in range(100)]
        cur = [rng.gauss(11650, 1500) for _ in range(24)]
        result = eval_stats.decide(cur, ref, cfg={"stat_hard_min_n": 9999})
        self.assertNotEqual(result["verdict"], "PROMOTE")
        # 2026-08-20 review round 2, issue 8: pin the actual verdict (not
        # just "not PROMOTE") so a future winsorize/SE change that pushed
        # this scenario to e.g. REGRESSION_SOFT wouldn't silently pass.
        self.assertEqual(result["verdict"], "NONINFERIOR")

        alive_c = eval_stats.alive(cur)
        alive_a = eval_stats.alive(ref)
        lo, hi = eval_stats.winsor_limits(alive_a + alive_c, 0.05, 0.95)
        mc, vc, nc = eval_stats.wstats(alive_c, lo, hi)
        ma, va, na = eval_stats.wstats(alive_a, lo, hi)
        alpha_soft_at_k4 = eval_stats.group_sequential_alpha(0.05, 4)
        alpha_promote_at_k4 = eval_stats.group_sequential_alpha(0.01, 4)
        _, _, loose_lcb, _ = eval_stats.welch_bounds(mc, vc, nc, ma, va, na, alpha_soft_at_k4)
        _, _, strict_lcb, _ = eval_stats.welch_bounds(mc, vc, nc, ma, va, na, alpha_promote_at_k4)
        # Confirms the scenario actually exercises the bug this test guards:
        # the pre-fix (alpha_soft) bar would have called this PROMOTE, the
        # correct (alpha_promote) bar does not -- both sides of the bug,
        # not just "loose would have promoted" (2026-08-20 review round 2,
        # issue 8: the margin between these two was thin enough at the
        # original scenario that a silent regression could slip through;
        # asserting both bounds directly, not just the resulting verdict,
        # narrows that gap).
        self.assertGreater(loose_lcb, 500)
        self.assertLess(strict_lcb, 500)

    def test_not_a_look_still_reports_hard_layer_stats(self):
        # 2026-08-20 review, issue 5: a HARD-layer look (nc a multiple of the
        # stride past stat_hard_min_n) that doesn't cross -delta_hard used to
        # fall through to NOT_A_LOOK with delta/se/lcb/ucb all wiped to None
        # if nc also isn't a SOFT look point. Phase 2's shadow log wants
        # those numbers even on a quiet look.
        rng = random.Random(55)
        ref = [rng.gauss(10000, 1000) for _ in range(100)]
        cur = [rng.gauss(9900, 1000) for _ in range(32)]  # 32: HARD look, not a SOFT look
        result = eval_stats.decide(
            cur, ref, cfg={"stat_hard_min_n": 16, "stat_hard_look_stride": 8})
        self.assertEqual(result["verdict"], "NOT_A_LOOK")
        self.assertIsNotNone(result["delta"])
        self.assertIsNotNone(result["se"])
        self.assertIsNotNone(result["lcb"])
        self.assertIsNotNone(result["ucb"])

    def test_decide_pools_winsor_limits_across_both_arms(self):
        # soren-stat-gate-design.md F-1 risk #5 explicitly calls for a test
        # pinning that decide() winsorizes using clip bounds from BOTH arms
        # pooled together, not each arm's own quantiles. Construct current
        # with outliers extreme enough that per-arm vs pooled clipping give
        # measurably different deltas, so the test can't pass vacuously.
        ref = list(range(9000, 11000, 20))  # 100 values, no outliers
        cur = list(range(9900, 10120, 10)) + [50000, 60000]  # 22 + 2 outliers = 24
        result = eval_stats.decide(cur, ref, cfg={"stat_hard_min_n": 9999})

        alive_c = eval_stats.alive(cur)
        alive_a = eval_stats.alive(ref)
        lo_pooled, hi_pooled = eval_stats.winsor_limits(alive_a + alive_c, 0.05, 0.95)
        mc_pooled, _, _ = eval_stats.wstats(alive_c, lo_pooled, hi_pooled)
        ma_pooled, _, _ = eval_stats.wstats(alive_a, lo_pooled, hi_pooled)
        expected_delta = mc_pooled - ma_pooled
        self.assertAlmostEqual(result["delta"], expected_delta, places=9)

        # Guard against a vacuous test: per-arm clipping must give a
        # meaningfully different delta, or this test wouldn't actually
        # distinguish the two implementations.
        lo_c, hi_c = eval_stats.winsor_limits(alive_c, 0.05, 0.95)
        lo_a, hi_a = eval_stats.winsor_limits(alive_a, 0.05, 0.95)
        mc_perarm, _, _ = eval_stats.wstats(alive_c, lo_c, hi_c)
        ma_perarm, _, _ = eval_stats.wstats(alive_a, lo_a, hi_a)
        perarm_delta = mc_perarm - ma_perarm
        self.assertGreater(abs(expected_delta - perarm_delta), 100)

    def test_false_positive_rate_near_nominal_at_delta_soft_zero(self):
        # Coarse calibration smoke test (design doc's suggested validity
        # check, corrected 2026-08-20 across two review passes: with
        # Bonferroni over 4 correlated nested looks the actual FP rate lands
        # below the naive alpha_soft (0.05) the design doc originally
        # quoted, in the ballpark of alpha_soft/K (~0.0125) but not exactly
        # -- a larger 9000-trial measurement (3000 x 3 seeds) converged on
        # ~0.017. 300 trials alone is too few to pin a ~1-2% rate precisely
        # (+/-0.008ish at 1 sigma), which is why this asserts a loose upper
        # bound (well above either estimate) rather than a tight calibration
        # proof -- see soren-stat-gate-design.md's FP-number correction note
        # for the full measurement history.
        rng = random.Random(20260820)
        false_positives = 0
        trials = 300
        for _ in range(trials):
            ref = [rng.gauss(10000, 2000) for _ in range(100)]
            cur = [rng.gauss(10000, 2000) for _ in range(24)]
            result = eval_stats.decide(
                cur, ref, cfg={"stat_hard_min_n": 9999, "delta_soft": 0})
            if result["verdict"] == "REGRESSION_SOFT":
                false_positives += 1
        rate = false_positives / trials
        self.assertLess(rate, 0.08, msg=f"false positive rate {rate} over {trials} trials")


if __name__ == "__main__":
    unittest.main()
