import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const DEFAULT_MAX_REPAIRS = 1;
const REPAIR_OPENCODE_TIMEOUT_SEC = 420;

function classifyCodeError(text) {
  if (!text.includes('code error')) return null;
  if (/unexpected end|unterminated|string constant|template literal|unterminated comment/.test(text)) {
    return 'code_error_truncated_or_unterminated';
  }
  if (/already been declared|duplicate export|duplicate declaration/.test(text)) {
    return 'code_error_duplicate_declaration';
  }
  if (/cannot find package|cannot find module|module not found|err_module_not_found/.test(text)) {
    return 'code_error_module_dependency';
  }
  if (/is not defined|before initialization|cannot access .* before initialization/.test(text)) {
    return 'code_error_top_level_reference';
  }
  if (/unexpected token|invalid or unexpected token|syntaxerror|missing \)|missing \]|missing \}|illegal return|await is only valid|reserved word/.test(text)) {
    return 'code_error_syntax';
  }
  return 'code_error_other';
}

export function classifyCandidateValidation(error) {
  const text = String(error || '').toLowerCase();
  if (text.includes('no decide()') || text.includes('export function decide')) return 'missing_decide';
  if (text.includes('not exported as a function')) return 'decide_not_function';
  if (text.includes('invalid format')) return 'invalid_return';
  if (text.includes('out of range')) return 'x_out_of_range';
  if (text.includes('undefined variable')) return 'undefined_variable';
  if (text.includes('behavior')) return 'behavior_contract';
  return classifyCodeError(text) || 'other';
}

export function buildDailyCandidateRepairPrompt(failedCode, validationError, baselineStrategy) {
  const failed = String(failedCode || '');
  const baseline = String(baselineStrategy || '');
  const category = classifyCandidateValidation(validationError);
  const codeErrorRules = category.startsWith('code_error_')
    ? `\n## Code-error-specific repair rules\n- The reviewed baseline below is known-good source structure and is authoritative. Reproduce its complete module structure first, then apply only the smallest local evidence-backed strategy change.\n- Treat the failed candidate only as a non-authoritative hint about intended logic. Do NOT copy broken/truncated structure from it.\n- Do not duplicate helper names, constants, exports, or top-level declarations that already exist in the baseline.\n- Keep every brace, parenthesis, bracket, quote, template literal, and comment balanced and closed.\n- Do not add imports or references to packages/modules.\n- Do not execute board-state-dependent logic at module top level; boardState and derived state belong inside decide/helpers only.\n- Preserve the complete tail of the reviewed baseline; never stop after the changed helper or decide().\n- Before answering, mentally parse the whole module from first to last line and verify it is valid ESM.\n`
    : '';
  return `The proposed Soren91 strategy failed validation. Repair it once, conservatively, using the reviewed current strategy as the complete-module baseline.\n\n## Validation category\n${category}\n${codeErrorRules}\n## Mandatory output contract\n- Return EXACTLY ONE JavaScript code block and no prose.\n- That one block MUST be the COMPLETE replacement strategy.mjs module, not a patch, snippet, helper, or explanation.\n- It MUST contain the literal signature: export function decide(boardState)\n- preserve every helper/export from the reviewed baseline unless the improvement intentionally and safely replaces it.\n- preserve HOLD behavior and return { x: finite number in [-3, 3], reason: string, hold?: boolean }.\n- no imports, async/await, fetch, fs, subprocesses, network, or side effects.\n- If the failed candidate is partial or cannot be safely integrated, start from the reviewed baseline and make the smallest evidence-based change needed.\n\n## Reviewed current strategy.mjs baseline\n\`\`\`javascript\n${baseline}\n\`\`\`\n\n## Failed candidate (non-authoritative hint only)\n\`\`\`javascript\n${failed.slice(0, 8000)}${failed.length > 8000 ? '\n// ... failed candidate truncated; DO NOT copy truncation ...' : ''}\n\`\`\`\n\nReturn the complete corrected strategy.mjs now.`;
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
