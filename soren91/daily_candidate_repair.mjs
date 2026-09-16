const DEFAULT_MAX_REPAIRS = 1;

export async function validateAndRepairCandidate(improveModule, initialCandidate, {
  maxRepairs = DEFAULT_MAX_REPAIRS,
} = {}) {
  if (!improveModule || typeof improveModule.validateStrategy !== 'function') {
    throw new Error('candidate_validator_missing');
  }
  if (typeof improveModule.callClaudeToFix !== 'function') {
    throw new Error('candidate_repair_missing');
  }
  if (!Number.isInteger(maxRepairs) || maxRepairs < 0 || maxRepairs > 1) {
    throw new Error('candidate_repair_budget_invalid');
  }

  let candidate = initialCandidate;
  let validation = await improveModule.validateStrategy(candidate);
  let repairs = 0;

  while (!validation.valid && repairs < maxRepairs) {
    repairs += 1;
    const repaired = await improveModule.callClaudeToFix(candidate, validation.error, []);
    if (!repaired) break;
    candidate = repaired;
    validation = await improveModule.validateStrategy(candidate);
  }

  return { candidate, validation, repairs };
}
