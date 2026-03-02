# v602 Strategy Changes Summary

## Analysis Context

Based on the batch summary analysis of v551 (6 games), we identified a critical issue:

### Counterintuitive Finding: Higher Scores with Lower Merge Rates
- **High score games** (top 3): avg_score=1350, **merge_rate=10.9%**
- **Low score games** (bottom 3): avg_score=752, **merge_rate=14.4%**

This suggests that **fewer but higher-quality merges** are more valuable than many risky merges.

### Problem Identification

1. **DRIFT_NO_MERGE dominates with low returns**
   - Frequency: 12.0% of all decisions
   - Average score delta: only 13.1 points
   - Indicates the strategy is passing up good merge opportunities

2. **Early game penalties too aggressive**
   - `EARLY_HIGH_TOWER_PENALTY`: -150 points (overly aggressive)
   - `EARLY_HIGH_LAYER_MERGE_WAIT`: +40 points (encourages "waiting" instead of merging)
   - Result: Strategy avoids good merge opportunities in early game

3. **Historical success with simplicity**
   - v128 (best historical score: 3689) focused on immediate factors
   - v408 confirmed that removing chain prediction logic improved performance
   - Current v551 has become too complex with lookahead logic

## v602 Changes

### 1. Reduced Early HIGH_TOWER Penalty (Less Aggressive)
- **Before**: EARLY_HIGH_TOWER_PENALTY = -150 points (turn < 20)
- **After**: EARLY_HIGH_TOWER_PENALTY = -50 points (turn < 20)
- **Mid-game**: -80 → -40 points (turn < 30)
- **Rationale**: Stop the strategy from over-avoiding good merge opportunities

### 2. Removed EARLY_HIGH_LAYER_MERGE_WAIT Bonus
- **Before**: +40 points for "waiting" in HIGH_LAYER with no merge
- **After**: Removed entirely
- **Rationale**: Discourage "waiting" and encourage taking available merges

### 3. Increased HIGH_LAYER Merge Bonuses (Quality over Quantity)
- **Before** (HIGH_LAYER_LOW):
  - DIRECT: +150, NEAR: +100, FAR: +75, NO_MERGE: +100
- **After** (HIGH_LAYER_LOW):
  - DIRECT: +200 (+33% increase)
  - NEAR: +150 (+50% increase)
  - FAR: +75 (unchanged)
  - NO_MERGE: +80 (-20% decrease)
- **Rationale**: Emphasize taking high-quality merges, discourage waiting

### 4. Strengthened Drift Adjustment for Merges
- **Before** (drift_penalty_base multiplier):
  - DIRECT: 0.5x, NEAR: 0.7x, NO_MERGE: 1.0x
- **After**:
  - DIRECT: 0.4x (-20% penalty, more aggressive merges)
  - NEAR: 0.6x (-14% penalty, more aggressive merges)
  - NO_MERGE: 1.0x (unchanged)
- **Rationale**: Make the strategy more willing to take merges

### 5. Merge-Quality-Aware Drift Uncertainty Penalty (MEDIUM Phase)
- **Before**: Fixed penalties regardless of merge quality
  - drift_unc < 0.5: -20, 0.5-0.7: -40, >0.7: -60
- **After**: Quality-based penalties
  - **DIRECT**: -5 / -10 / -15 (75-87% reduction)
  - **NEAR**: -10 / -20 / -30 (50-67% reduction)
  - **NO_MERGE**: -20 / -40 / -60 (unchanged)
- **Rationale**: Allow taking merges even with some drift uncertainty

## Success Criteria

### Primary Goals
- **Average score**: > 1051.0 (v551 baseline)
- **Merge rate**: > 10.9% (high score games from v551)
- **Reduce DRIFT_NO_MERGE frequency**: from 12.0% to < 8%

### Secondary Goals
- **Increase merge quality**: More DIRECT/NEAR merges, fewer FAR merges
- **Longer games**: More turns (avg > 80) before GAMEOVER
- **Stability**: Less variance (stddev < 370)

## Expected Outcomes

Based on the analysis, v602 should:
1. **Take more merges** (especially in HIGH_LAYER)
2. **Avoid over-waiting** for perfect opportunities
3. **Increase merge quality** (prioritize DIRECT/NEAR over FAR)
4. **Improve average score** by taking better merge opportunities

## Deployment Status

- **File**: `strategy.py.staging` (23907 bytes, modified 2026-03-02 10:54)
- **Current active**: `strategy.py` (22532 bytes, v551/v601, modified 2026-03-02 10:27)
- **Status**: Ready for deployment
- **Test run**: Syntax verified, produces valid decisions

## Next Steps

Option 1: **Deploy now** - Replace `strategy.py` with v602 and start batch testing
Option 2: **Wait** - Complete current game (score 1991 in progress), then deploy
Option 3: **Review** - Further review the changes before deployment

Recommendation: Deploy v602 now to start gathering batch test data, as the current game appears to be in progress with a good score (1991).
