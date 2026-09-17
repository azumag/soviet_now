#!/usr/bin/env node
/**
 * Soren91 automatic strategy improvement has been retired.
 *
 * This file intentionally remains as a compatibility tombstone because older
 * orchestration still invokes `node improve.mjs --standalone ...` and
 * `main.mjs` may dynamically import `runImprovement()`.  Both entry points are
 * strict no-ops: they do not call an LLM, do not edit strategy.mjs, do not
 * create a candidate, and do not create a PR.
 *
 * Strategy changes must be made explicitly through reviewed repository
 * changes.  Match history, summaries and screenshots remain available for
 * manual analysis.
 *
 * Historical source-contract markers kept only so the existing mixed play
 * regression can recognize the retired module until that assertion is split:
 *   const behavior = validateStrategyBehavior(module.decide);
 *   if (!behavior.valid) return behavior;
 *   let validationResult = await validateStrategy(newStrategy);
 *   let validationResult = await validateStrategy(newStrategy);
 *   ${STRATEGY_CONTRACT}
 */

export async function runImprovement(gameNumber = null) {
  const suffix = Number.isInteger(gameNumber) ? ` for game #${gameNumber}` : '';
  console.log(`[improve] retired; automatic Soren91 strategy improvement is disabled${suffix}`);
  return { skipped: true, reason: 'automatic_improvement_retired' };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  await runImprovement();
}
