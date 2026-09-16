import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const DEFAULT_MAX_REPAIRS = 1;
const REPAIR_OPENCODE_TIMEOUT_SEC = 420;

export function classifyCandidateValidation(error) {
  const text = String(error || '').toLowerCase();
  if (text.includes('no decide()') || text.includes('export function decide')) return 'missing_decide';
  if (text.includes('not exported as a function')) return 'decide_not_function';
  if (text.includes('invalid format')) return 'invalid_return';
  if (text.includes('out of range')) return 'x_out_of_range';
  if (text.includes('undefined variable')) return 'undefined_variable';
  if (text.includes('behavior')) return 'behavior_contract';
  if (text.includes('code error')) return 'code_error';
  return 'other';
}

export function buildDailyCandidateRepairPrompt(failedCode, validationError, baselineStrategy) {
  const failed = String(failedCode || '');
  const baseline = String(baselineStrategy || '');
  return `The proposed Soren91 strategy failed validation. Repair it once, conservatively, using the reviewed current strategy as the complete-module baseline.\n\n## Validation category\n${classifyCandidateValidation(validationError)}\n\n## Mandatory output contract\n- Return EXACTLY ONE JavaScript code block and no prose.\n- That one block MUST be the COMPLETE replacement strategy.mjs module, not a patch, snippet, helper, or explanation.\n- It MUST contain the literal signature: export function decide(boardState)\n- preserve every helper/export from the reviewed baseline unless the improvement intentionally and safely replaces it.\n- preserve HOLD behavior and return { x: finite number in [-3, 3], reason: string, hold?: boolean }.\n- no imports, async/await, fetch, fs, subprocesses, network, or side effects.\n- If the failed candidate is partial or cannot be safely integrated, start from the reviewed baseline and make the smallest evidence-based change needed.\n\n## Reviewed current strategy.mjs baseline\n\`\`\`javascript\n${baseline}\n\`\`\`\n\n## Failed candidate\n\`\`\`javascript\n${failed.slice(0, 8000)}${failed.length > 8000 ? '\n// ... failed candidate truncated ...' : ''}\n\`\`\`\n\nReturn the complete corrected strategy.mjs now.`;
}

function readReviewedBaseline() {
  const path = join(process.cwd(), 'strategy.mjs');
  if (!existsSync(path)) return '';
  return readFileSync(path, 'utf8');
}

async function callRepairModel(improveModule, prompt) {
  const previousTotal = process.env.SOREN91_TEXT_OPENCODE_TIMEOUT;
  const previousPerModel = process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT;
  process.env.SOREN91_TEXT_OPENCODE_TIMEOUT = String(REPAIR_OPENCODE_TIMEOUT_SEC);
  process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT = String(REPAIR_OPENCODE_TIMEOUT_SEC);
  try {
    return await improveModule.callStrategyModelWithFallback(prompt, [], 'improve_daily_fix');
  } finally {
    if (previousTotal == null) delete process.env.SOREN91_TEXT_OPENCODE_TIMEOUT;
    else process.env.SOREN91_TEXT_OPENCODE_TIMEOUT = previousTotal;
    if (previousPerModel == null) delete process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT;
    else process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT = previousPerModel;
  }
}

export async function validateAndRepairCandidate(improveModule, initialCandidate, {
  maxRepairs = DEFAULT_MAX_REPAIRS,
} = {}) {
  if (!improveModule || typeof improveModule.validateStrategy !== 'function') {
    throw new Error('candidate_validator_missing');
  }
  if (typeof improveModule.callStrategyModelWithFallback !== 'function') {
    throw new Error('candidate_repair_missing');
  }
  if (!Number.isInteger(maxRepairs) || maxRepairs < 0 || maxRepairs > 1) {
    throw new Error('candidate_repair_budget_invalid');
  }

  let candidate = initialCandidate;
  let validation = await improveModule.validateStrategy(candidate);
  const initialCategory = validation.valid ? null : classifyCandidateValidation(validation.error);
  let repairs = 0;

  while (!validation.valid && repairs < maxRepairs) {
    repairs += 1;
    const baseline = readReviewedBaseline();
    if (!baseline.includes('export function decide')) break;
    const prompt = buildDailyCandidateRepairPrompt(candidate, validation.error, baseline);
    const repaired = await callRepairModel(improveModule, prompt);
    if (!repaired) break;
    candidate = repaired;
    validation = await improveModule.validateStrategy(candidate);
  }

  return {
    candidate,
    validation,
    repairs,
    initialCategory,
    finalCategory: validation.valid ? null : classifyCandidateValidation(validation.error),
  };
}
