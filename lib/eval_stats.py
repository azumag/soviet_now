"""Shared statistics helpers for the improve-loop scoring/ranking code.

As of 2026-08-20, `strategy/regression.sh` contains five textually-identical
copies of `quantile()` and five near-identical variants of `metrics()` /
`composite_score()` (four `metrics()` shapes at lines 52/1128/2442/3438, plus
a `composite_score()` tuple variant at 2687), embedded in separate
`python3 - <<'PY'` heredocs. This module is the single implementation those
call sites are meant to migrate to (see `soren-stat-gate-design.md`, section
C-0). Landing it here first, without wiring it into `regression.sh` yet, is
Phase 0 of that rollout: the module exists and is tested, but nothing's
behavior changes until a later, separately reviewed change actually replaces
the embedded defs with imports.

Two intentional, review-verified (2026-08-20) differences from the embedded
originals, neither reachable by any current caller (both existing call sites
already pre-coerce to int before calling in), but worth knowing before that
migration diff:
  - `metrics()` coerces every score via `int(v)`; some embedded variants
    (regression.sh:2442, 2687) don't, so a future float-score caller would
    see this module silently truncate where the original wouldn't.
  - `metrics()` returns None on empty input across the board; two embedded
    variants (2442, 2687) instead raise ZeroDivisionError on empty input
    (no `if not scores` guard). None is the safer direction for a caller
    that forgets to check, but it's a behavior difference all the same.

This module is also the home for the statistical-significance gate and
instadeath classifier described in `soren-stat-gate-design.md` sections A and
B. Those (`decide`, `classify_instadeath`, `fisher_one_sided`, ...) are new
code with no existing counterpart to be compatible with; they are unused by
any caller until the rollout reaches Phase 2+ (`STAT_GATE_MODE=shadow`).

Deliberately stdlib-only: the target VM has no scipy/numpy (verified
2026-08-20 via `python3 -c "import scipy"` -> ModuleNotFoundError, Python
3.12.3).
"""

import math
from statistics import NormalDist


DEAD_EVAL_THRESHOLD_DEFAULT = 3000


# ---------------------------------------------------------------------------
# Quantile / composite metrics (bit-compatible with the existing embedded
# implementations in strategy/regression.sh; see tests/test_eval_stats.py)
# ---------------------------------------------------------------------------

def quantile(vals, p):
    """Linear-interpolation quantile (numpy's default 'linear' method).

    Bit-identical algorithm to the five duplicate `quantile()` defs in
    strategy/regression.sh (lines 40, 1116, 2430, 2675, 3426 as of commit
    7b3842965 in the soviet_now submodule).
    """
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


def metrics(scores, w_p50=0.55, w_p25=0.30, w_lcb=0.15, lcb_z=1.28, min_games=0):
    """Composite/percentile/lcb metrics for a list of scores.

    Generalizes the five duplicate metrics()/composite_score() variants in
    strategy/regression.sh (lines 52, 1128, 2442, 2687, 3438), which differ
    only in:
      - fixed weights (0.55/0.30/0.15) and z (1.28) vs caller-supplied
        weights/z (the ranking call sites pass RANK_WEIGHT_*/RANK_LCB_Z)
      - whether a `min_games` floor gates the result (returns None below it)
      - dict vs bare-tuple return shape at the call site (callers unpack
        this function's dict themselves; the math is identical either way)
      - whether the returned dict includes a "mean" key (see module
        docstring for the two other minor differences: int() coercion and
        None-vs-raise on empty input)

    Returns a dict {"comp","p50","p25","lcb","mean","n"}, or None if
    `scores` is empty or shorter than `min_games`.
    """
    xs = [int(v) for v in scores]
    n = len(xs)
    if n == 0 or n < min_games:
        return None
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - lcb_z * (std / math.sqrt(n))
    comp = w_p50 * p50 + w_p25 * p25 + w_lcb * lcb
    return {"comp": comp, "p50": p50, "p25": p25, "lcb": lcb, "mean": mean, "n": n}


# ---------------------------------------------------------------------------
# Instadeath separation (soren-stat-gate-design.md section B). Unused by any
# caller until Phase 1 (INSTADEATH_SPLIT_ENABLED=1).
# ---------------------------------------------------------------------------

def alive(scores, threshold=DEAD_EVAL_THRESHOLD_DEFAULT):
    """Scores at or above the instadeath threshold (eval-score space)."""
    return [s for s in scores if int(s) >= threshold]


def dead_count(scores, threshold=DEAD_EVAL_THRESHOLD_DEFAULT):
    return len(scores) - len(alive(scores, threshold))


def run_lengths(flags):
    """Lengths of consecutive True-runs in a boolean sequence."""
    lengths = []
    cur = 0
    for f in flags:
        if f:
            cur += 1
        else:
            if cur:
                lengths.append(cur)
            cur = 0
    if cur:
        lengths.append(cur)
    return lengths


def burst_ratio(flags):
    """Ratio of the observed mean true-run length to the iid-expected mean
    run length (1/(1-p)) for the same true-rate p.

    ~1 means the True flags are scattered independently (iid); >>1 means
    they cluster into long runs, which for instadeaths is the fingerprint of
    a harness/infra outage rather than a strategy regression (2026-08-20
    VM data: observed ratio 9.5 on the 2026-08-06 outage window).
    """
    n = len(flags)
    if n == 0:
        return 0.0
    p = sum(1 for f in flags if f) / n
    if p <= 0:
        return 0.0
    lens = run_lengths(flags)
    if not lens:
        return 0.0
    observed_mean = sum(lens) / len(lens)
    if p >= 1.0:
        # Every game in the window died: the maximal possible burst. The iid
        # expectation 1/(1-p) diverges, so report the observed run length
        # directly rather than falling through to the "looks iid" answer of
        # 0.0 -- that would silently defeat classify_instadeath's HARNESS
        # detection for exactly the total-outage case it exists to catch
        # (2026-08-20 review finding: a 2026-08-06-style 20-game dead window
        # scored 0.0 and was misclassified UNKNOWN instead of HARNESS).
        return float(observed_mean)
    expected_mean = 1.0 / (1.0 - p)
    return observed_mean / expected_mean


def _hypergeom_pmf(k, K, n, N):
    try:
        return math.comb(K, k) * math.comb(N - K, n - k) / math.comb(N, n)
    except ValueError:
        return 0.0


def fisher_one_sided(dead_cur, n_cur, dead_ref, n_ref):
    """One-sided Fisher exact test p-value for H1: cur's dead-rate > ref's.

    2x2 table margins are [dead_cur, n_cur-dead_cur] / [dead_ref,
    n_ref-dead_ref]. stdlib-only (math.comb), since the VM has no scipy.
    """
    N = n_cur + n_ref
    K = dead_cur + dead_ref
    n = n_cur
    if N == 0 or n == 0 or K == 0 or K == N:
        return 1.0
    k_obs = dead_cur
    k_max = min(K, n)
    p = sum(_hypergeom_pmf(k, K, n, N) for k in range(k_obs, k_max + 1))
    return min(1.0, max(0.0, p))


def classify_instadeath(cur_flags, ref_flags=None, cfg=None):
    """Classify a recent window of instadeath flags as one of:
    NORMAL / HARNESS / STRATEGY / UNKNOWN.

    `cur_flags`: list[bool], True = that game was an instadeath, for the
    current strategy's recent window.
    `ref_flags`: same, for the anchor/reference strategy; only used for the
    STRATEGY-vs-HARNESS Fisher check (optional).
    `cfg` (all optional, see soren-stat-gate-design.md section D for the
    matching DEAD_* config defaults):
      dead_alert_rate (0.10), dead_burst_ratio (3.0), dead_hard_ratio (0.5),
      dead_max_turns (3), dead_alpha (0.01), dead_near_total_rate (0.90),
      plus caller-supplied evidence that this module cannot derive from
      flags alone:
      cur_dead_hard_ratio (float|None) - fraction of this window's deaths
        that were raw==0 (no piece placed at all),
      spans_hash_change (bool) - whether the current death-run started
        before the strategy hash last changed,
      median_death_turns (float|None) - median turn count among the deaths.
    """
    cfg = cfg or {}
    n = len(cur_flags)
    if n == 0:
        return ("NORMAL", {"rate": 0.0})
    rate = sum(1 for f in cur_flags if f) / n
    alert_rate = cfg.get("dead_alert_rate", 0.10)
    if rate < alert_rate:
        return ("NORMAL", {"rate": rate})

    burst = burst_ratio(cur_flags)
    votes = 0
    detail = {"rate": rate, "burst_ratio": burst}
    if burst >= cfg.get("dead_burst_ratio", 3.0):
        votes += 1

    hard_ratio = cfg.get("cur_dead_hard_ratio")
    if hard_ratio is not None:
        detail["hard_ratio"] = hard_ratio
        if hard_ratio >= cfg.get("dead_hard_ratio", 0.5):
            votes += 1

    spans_hash_change = cfg.get("spans_hash_change", False)
    detail["spans_hash_change"] = spans_hash_change
    if spans_hash_change:
        votes += 1

    median_death_turns = cfg.get("median_death_turns")
    if median_death_turns is not None:
        detail["median_death_turns"] = median_death_turns
        if median_death_turns <= cfg.get("dead_max_turns", 3):
            votes += 1

    # burst_ratio is blind in the p=0.85..0.99 band: 1/(1-p) diverges at
    # roughly the same rate the observed run length does, so the ratio stays
    # near 1 even for a near-total outage (2026-08-20 review round 2, issue
    # 6 -- a 19/20-dead window scored burst_ratio=0.95 and needed a second
    # vote to reach HARNESS). A strategy alone plausibly cannot make ~90%+ of
    # games die instantly regardless of clustering, so treat a near-total
    # rate as independent evidence rather than relying on burst_ratio to
    # catch it.
    if rate >= cfg.get("dead_near_total_rate", 0.90):
        votes += 1

    detail["votes_harness"] = votes
    if votes >= 2:
        return ("HARNESS", detail)

    if ref_flags is not None:
        dead_cur = sum(1 for f in cur_flags if f)
        dead_ref = sum(1 for f in ref_flags if f)
        p = fisher_one_sided(dead_cur, len(cur_flags), dead_ref, len(ref_flags))
        detail["fisher_p"] = p
        if p < cfg.get("dead_alpha", 0.01):
            return ("STRATEGY", detail)

    return ("UNKNOWN", detail)


# ---------------------------------------------------------------------------
# Statistical significance gate (soren-stat-gate-design.md section A). Unused
# by any caller until Phase 2+ (STAT_GATE_MODE=shadow|enforce).
# ---------------------------------------------------------------------------

def winsor_limits(pool, lo_q=0.05, hi_q=0.95):
    """(lo, hi) clip bounds for winsorizing, from a pooled sample.

    Must be computed once from BOTH arms pooled together (not per-arm) so
    the two arms share the same clip bounds; see soren-stat-gate-design.md
    F-1 risk #5.
    """
    if not pool:
        return (0.0, 0.0)
    return (quantile(pool, lo_q), quantile(pool, hi_q))


def wstats(xs, lo, hi):
    """Winsorized mean/variance(unbiased, n-1)/n for one arm.

    Values are clipped to [lo, hi] (not discarded) before computing an
    unbiased sample variance, so a single arm's Welch SE is well-defined.
    """
    clipped = [min(max(float(x), lo), hi) for x in xs]
    n = len(clipped)
    if n == 0:
        return (0.0, 0.0, 0)
    wmean = sum(clipped) / n
    if n > 1:
        var = sum((x - wmean) ** 2 for x in clipped) / (n - 1)
    else:
        var = 0.0
    return (wmean, var, n)


def welch_bounds(mc, vc, nc, ma, va, na, alpha):
    """One-sided (1-alpha) Welch bounds on delta = mc - ma.

    Returns (delta, se, lcb, ucb) where lcb/ucb both use the same one-sided
    z = NormalDist().inv_cdf(1-alpha) (not alpha/2), so each bound alone is
    a valid one-sided (1-alpha)-confidence bound:
      - regression check:   ucb < -delta_threshold
      - promotion check:    lcb >  delta_threshold
      - non-inferiority:    lcb > -delta_threshold
    """
    delta = mc - ma
    if nc <= 0 or na <= 0:
        return (delta, float("inf"), float("-inf"), float("inf"))
    se = math.sqrt(vc / nc + va / na)
    if not (0 < alpha < 1):
        return (delta, se, float("-inf"), float("inf"))
    z = NormalDist().inv_cdf(1 - alpha)
    return (delta, se, delta - z * se, delta + z * se)


def group_sequential_alpha(alpha, k_looks):
    """Bonferroni-adjusted alpha for a fixed, pre-registered look schedule
    of k_looks total looks (see soren-stat-gate-design.md A-3)."""
    if k_looks is None or k_looks <= 0:
        return alpha
    return alpha / k_looks


_DECIDE_DEFAULTS = {
    "dead_eval_threshold": DEAD_EVAL_THRESHOLD_DEFAULT,
    "stat_anchor_min_n": 100,
    "winsor_lo_q": 0.05,
    "winsor_hi_q": 0.95,
    "stat_hard_min_n": 16,
    "stat_hard_look_stride": 8,
    "stat_hard_look_k": 11,
    "alpha_hard": 0.01,
    "delta_hard": 2000,
    "stat_looks": (24, 48, 72, 100),
    "alpha_soft": 0.05,
    "delta_soft": 500,
    "alpha_promote": 0.01,
    "delta_promote": 500,
    "delta_harmless": 1500,
    "alpha_noninf": 0.05,
}


def decide(cur_scores, ref_scores, cfg=None):
    """Accept/reject/promote decision for one strategy vs. its reference
    (anchor), per soren-stat-gate-design.md section A-1/A-2.

    `cur_scores` / `ref_scores`: raw eval-score lists (not pre-filtered for
    instadeaths; this function applies dead_eval_threshold itself).
    `cfg`: overrides for any key in `_DECIDE_DEFAULTS`; see
    soren-stat-gate-design.md section D for the matching STAT_* config
    defaults and their rationale.

    Returns a dict with at least a "verdict" key, one of:
      REGRESSION_HARD | REGRESSION_SOFT | PROMOTE | NONINFERIOR |
      INCONCLUSIVE | INSUFFICIENT_REFERENCE | INSUFFICIENT_CURRENT | NOT_A_LOOK
    plus delta/se/lcb/ucb/alpha_used/z_used/look_index/n_*/dead_rate_* for
    logging (see the [STATGATE] log line format in the design doc).

    NONINFERIOR and PROMOTE are both terminal verdicts checked at the same
    look, PROMOTE first: a genuinely-better current strategy that narrowly
    misses PROMOTE's (stricter) significance bar at one look, but clears the
    (looser) non-inferiority bar, gets returned as NONINFERIOR -- evaluation
    stops there rather than continuing to the next look where PROMOTE might
    have fired. Measured (2026-08-20 review) at a true delta of +2000: ~49%
    PROMOTE / ~51% NONINFERIOR at the first eligible look. If a caller wants
    "keep watching for promotion" and "release exploration" as independent
    signals rather than one terminal choice, treat NONINFERIOR as advisory
    and let the caller decide whether to keep sampling toward the next look.

    NOTE: this function is not yet called by strategy/regression.sh or
    strategy/improve.sh. It ships now (Phase 0) so it can be unit-tested in
    isolation before Phase 2 wires it in behind STAT_GATE_MODE=shadow.
    """
    merged = dict(_DECIDE_DEFAULTS)
    if cfg:
        merged.update(cfg)
    threshold = merged["dead_eval_threshold"]

    alive_c = alive(cur_scores, threshold)
    alive_a = alive(ref_scores, threshold)
    n_total_cur = len(cur_scores)
    n_total_ref = len(ref_scores)
    nc = len(alive_c)
    na = len(alive_a)

    base = {
        "n_alive_cur": nc,
        "n_total_cur": n_total_cur,
        "n_alive_ref": na,
        "n_total_ref": n_total_ref,
        "dead_rate_cur": (dead_count(cur_scores, threshold) / n_total_cur) if n_total_cur else 0.0,
        "dead_rate_ref": (dead_count(ref_scores, threshold) / n_total_ref) if n_total_ref else 0.0,
        "delta": None,
        "se": None,
        "lcb": None,
        "ucb": None,
        "alpha_used": None,
        "z_used": None,
        "look_index": None,
    }

    # Eligibility is gated on the reference arm's TOTAL games, not its
    # alive-filtered count: stat_anchor_min_n mirrors the >=100-total-games
    # requirement _refresh_best_strategy_anchor() already applies before a
    # hash can become anchor (soren-stat-gate-design.md C-1c). Gating on
    # n_alive_ref instead would make a perfectly normal anchor (100 games,
    # one instadeath -> 99 alive) silently fail eligibility every time,
    # indistinguishable in the verdict from "anchor genuinely too young"
    # (2026-08-20 review finding, issue 2).
    if n_total_ref < merged["stat_anchor_min_n"]:
        return dict(base, verdict="INSUFFICIENT_REFERENCE",
                    reason="reference arm below stat_anchor_min_n (total games)")
    if na == 0:
        return dict(base, verdict="INSUFFICIENT_REFERENCE",
                    reason="reference arm has no alive scores after dead-game filtering")
    if nc == 0:
        # Distinct verdict from INSUFFICIENT_REFERENCE: a current strategy
        # with zero alive scores (100% instadeath in its window) is the most
        # severe signal this function can see, and a caller branching only
        # on verdict name must not treat it the same as "reference isn't
        # ready yet" -- that reads as "skip the gate, nothing to compare
        # against" when the right response is closer to B-4's quarantine
        # path (2026-08-20 review round 2, issue 5).
        return dict(base, verdict="INSUFFICIENT_CURRENT",
                    reason="current arm has no alive scores (100% instadeath in window)")

    lo, hi = winsor_limits(alive_a + alive_c, merged["winsor_lo_q"], merged["winsor_hi_q"])
    mc, vc, _ = wstats(alive_c, lo, hi)
    ma, va, _ = wstats(alive_a, lo, hi)

    # Tracks the most recent computed delta/se/lcb/ucb so a look that computes
    # stats but crosses no threshold still reports them (instead of falling
    # through to NOT_A_LOOK with everything None) -- Phase 2's [STATGATE]
    # shadow log wants these even on a quiet look (2026-08-20 review, issue 5).
    last_computed = base

    # HARD layer: fixed-stride looks starting at stat_hard_min_n. Uses
    # (nc - hard_min_n) % stride, not nc % stride as soren-stat-gate-design.md
    # A-1's pseudocode literally shows -- these only coincide when
    # hard_min_n is itself a multiple of stride (true at the defaults:
    # 16 % 8 == 0). This offset form is deliberate: it guarantees the FIRST
    # eligible n (hard_min_n) is always a look, which nc % stride == 0 would
    # not if hard_min_n weren't a stride multiple.
    #
    # stat_hard_look_k (default 11) is sized for the n<=100 horizon implied
    # by stat_looks' last entry; if a caller raises the current-run keep
    # window past 100 without also raising stat_hard_look_k, the Bonferroni
    # correction here silently under-corrects (more HARD looks actually
    # happen than k accounts for).
    hard_min_n = merged["stat_hard_min_n"]
    hard_stride = merged["stat_hard_look_stride"]
    if nc >= hard_min_n and hard_stride > 0 and (nc - hard_min_n) % hard_stride == 0:
        alpha_hard = group_sequential_alpha(merged["alpha_hard"], merged["stat_hard_look_k"])
        delta, se, lcb, ucb = welch_bounds(mc, vc, nc, ma, va, na, alpha_hard)
        result = dict(base, delta=delta, se=se, lcb=lcb, ucb=ucb,
                      alpha_used=alpha_hard, z_used=NormalDist().inv_cdf(1 - alpha_hard),
                      look_index=(nc - hard_min_n) // hard_stride)
        last_computed = result
        if ucb < -merged["delta_hard"]:
            return dict(result, verdict="REGRESSION_HARD", reason="hard-layer ucb below -delta_hard")

    # SOFT layer: pre-registered look schedule only.
    looks = tuple(merged["stat_looks"])
    if nc in looks:
        k = len(looks)
        alpha_soft = group_sequential_alpha(merged["alpha_soft"], k)
        alpha_promote = group_sequential_alpha(merged["alpha_promote"], k)
        alpha_noninf = group_sequential_alpha(merged["alpha_noninf"], k)
        delta, se, lcb, ucb = welch_bounds(mc, vc, nc, ma, va, na, alpha_soft)
        look_index = looks.index(nc) + 1
        result = dict(base, delta=delta, se=se, lcb=lcb, ucb=ucb,
                      alpha_used=alpha_soft, z_used=NormalDist().inv_cdf(1 - alpha_soft),
                      look_index=look_index)
        last_computed = result
        if ucb < -merged["delta_soft"]:
            return dict(result, verdict="REGRESSION_SOFT", reason="soft-layer ucb below -delta_soft")
        # Promotion uses its own (stricter) alpha per soren-stat-gate-design.md
        # A-3 ("PROMOTE はここだけ厳格に") -- reusing alpha_soft here would
        # silently promote on a looser bar than the design specifies, right on
        # the winner's-curse path the whole gate exists to guard (2026-08-20
        # review, issue 3). Report ALL four bounds recomputed at alpha_promote
        # (not just lcb) so alpha_used/z_used match delta/se/lcb/ucb together
        # -- reporting a mix of soft-layer and promote-layer numbers would
        # make the [STATGATE] log internally inconsistent (2026-08-20 review
        # round 2, issue 1).
        p_delta, p_se, promote_lcb, promote_ucb = welch_bounds(mc, vc, nc, ma, va, na, alpha_promote)
        if promote_lcb > merged["delta_promote"]:
            return dict(base, verdict="PROMOTE", delta=p_delta, se=p_se,
                        lcb=promote_lcb, ucb=promote_ucb,
                        alpha_used=alpha_promote, z_used=NormalDist().inv_cdf(1 - alpha_promote),
                        look_index=look_index,
                        reason="soft-layer lcb above delta_promote (at alpha_promote)")
        # Non-inferiority uses its own alpha; same reasoning as PROMOTE above
        # -- report the alpha_noninf-based bounds this verdict is actually
        # computed from, not the alpha_soft ones (2026-08-20 review round 2,
        # issue 3: this had the same silent-mismatch shape as issue 1/3).
        ni_delta, ni_se, ni_lcb, ni_ucb = welch_bounds(mc, vc, nc, ma, va, na, alpha_noninf)
        if ni_lcb > -merged["delta_harmless"]:
            return dict(base, verdict="NONINFERIOR", delta=ni_delta, se=ni_se,
                        lcb=ni_lcb, ucb=ni_ucb,
                        alpha_used=alpha_noninf, z_used=NormalDist().inv_cdf(1 - alpha_noninf),
                        look_index=look_index,
                        reason="lcb above -delta_harmless at alpha_noninf")
        return dict(result, verdict="INCONCLUSIVE", reason="no look threshold crossed")

    if last_computed is not base:
        # A HARD look ran (nc past stat_hard_min_n, on-stride) and didn't
        # cross -delta_hard, but nc also isn't a SOFT look point -- the
        # statistics are real and worth logging, "NOT_A_LOOK" only means "no
        # SOFT-layer verdict was possible here" (2026-08-20 review round 2,
        # issue 2: the old single reason string implied nc wasn't a look of
        # ANY kind, which was false whenever this branch carries HARD stats).
        return dict(last_computed, verdict="NOT_A_LOOK",
                    reason="hard-layer look taken and crossed no threshold; "
                           "n_alive_cur is not a SOFT look point")
    return dict(base, verdict="NOT_A_LOOK",
                reason="n_alive_cur is not a HARD or SOFT look point")
