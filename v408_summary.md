# v408 Strategy Summary

## Changes from v407/v343

### Complete Removal of Chain Expectation Logic
- Removed `calc_chain_expectation()` function
- Removed chain expectation bonuses (merge and non-merge scenarios)
- Removed next/nextNext piece usage in scoring (except for NEXT_SAME bonus)

### v128 Structure Restored
- HIGH phase: height_mult=1.8 (fixed)
- Merge bonuses: DIRECT=1500, NEAR=800, FAR=300
- Balance correction: 20.0/30.0/40.0 per phase
- Drift penalty: 30.0
- HIGH_TOWER penalty: 1.3x multiplier
- NEXT_SAME bonus: 50.0 center preference

## Performance

### Decision Distribution
- v343: CHAIN_POTENTIAL 45.8% (score 1277) - FAILED
- v407: CHAIN_POTENTIAL 83.3% - FAILED
- v408: CHAIN_POTENTIAL 0% ✓ - SUCCESS

### Key Insight
v128 (best score 3689) completely removed chain reaction logic. The best strategy focuses on immediate factors (merge, height, balance) rather than future chain expectations.

## Success Criteria
- CHAIN_POTENTIAL: 0% ✓ ACHIEVED
- Target avg_score: > 1450.5 (v343 baseline)
- Ideal avg_score: approaching v128's 3689
