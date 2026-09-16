import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const DEFAULT_MAX_REPAIRS = 1;
const REPAIR_OPENCODE_TIMEOUT_SEC = 420;
const DECIDE_SIGNATURE = 'export function decide(boardState)';
const MAX_REPAIR_SOURCE_BYTES = 65536;
const MAX_DECIDE_REPAIR_BYTES = 12000;

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

function useDecideOnlyRepair(category) {
  return category === 'code_error_syntax' || category === 'code_error_truncated_or_unterminated';
}

function extractStrictDecideReplacement(source) {
  const raw = String(source || '').trim();
  if (!raw || raw.length > MAX_REPAIR_SOURCE_BYTES) return null;

  // Models sometimes ignore the narrow-output instruction and return a whole
  // module. That is safe to tolerate only by discarding everything except the
  // single decide() function before validation/execution.
  const start = raw.indexOf(DECIDE_SIGNATURE);
  if (start < 0 || raw.indexOf(DECIDE_SIGNATURE, start + DECIDE_SIGNATURE.length) >= 0) return null;
  const text = raw.slice(start);
  const braceStart = text.indexOf('{', DECIDE_SIGNATURE.length);
  if (braceStart < 0 || text.slice(DECIDE_SIGNATURE.length, braceStart).trim() !== '') return null;

  let depth = 0;
  let state = 'code';
  let escaped = false;
  for (let i = braceStart; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];

    if (state === 'line_comment') {
      if (ch === '\n') state = 'code';
      continue;
    }
    if (state === 'block_comment') {
      if (ch === '*' && next === '/') {
        state = 'code';
        i += 1;
      }
      continue;
    }
    if (state === 'single' || state === 'double' || state === 'template') {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === '\\') {
        escaped = true;
        continue;
      }
      if ((state === 'single' && ch === "'")
          || (state === 'double' && ch === '"')
          || (state === 'template' && ch === '`')) {
        state = 'code';
      }
      continue;
    }

    if (ch === '/' && next === '/') {
      state = 'line_comment';
      i += 1;
      continue;
    }
    if (ch === '/' && next === '*') {
      state = 'block_comment';
      i += 1;
      continue;
    }
    if (ch === "'") {
      state = 'single';
      continue;
    }
    if (ch === '"') {
      state = 'double';
      continue;
    }
    if (ch === '`') {
      state = 'template';
      continue;
    }
    if (ch === '{') {
      depth += 1;
      continue;
    }
    if (ch === '}') {
      depth -= 1;
      if (depth < 0) return null;
      if (depth === 0) {
        const decide = text.slice(0, i + 1).trim();
        if (decide.length > MAX_DECIDE_REPAIR_BYTES) return null;
        const body = decide.slice(DECIDE_SIGNATURE.length);
        // Only this extracted function is ever spliced into the reviewed
        // baseline. Keep obvious execution/escape surfaces out before the
        // normal full-module validator dynamically imports it.
        if (/\b(?:import|fetch|require|process|globalThis|eval)\b/.test(body)
            || /\bFunction\s*\(/.test(body)
            || /\bexport\b/.test(body)) {
          return null;
        }
        return decide;
      }
    }
  }
  return null;
}

export function spliceReviewedDecide(baselineStrategy, replacement) {
  const baseline = String(baselineStrategy || '');
  const first = baseline.indexOf(DECIDE_SIGNATURE);
  if (first < 0 || baseline.indexOf(DECIDE_SIGNATURE, first + DECIDE_SIGNATURE.length) >= 0) return null;
  if (baseline.slice(first).trim().at(-1) !== '}') return null;

  const decide = extractStrictDecideReplacement(replacement);
  if (!decide) return null;
  return `${baseline.slice(0, first)}${decide}\n`;
}

export function buildDailyCandidateRepairPrompt(failedCode, validationError, baselineStrategy) {
  const failed = String(failedCode || '');
  const baseline = String(baselineStrategy || '');
  const category = classifyCandidateValidation(validationError);

  if (useDecideOnlyRepair(category)) {
    return `The proposed Soren91 strategy failed JavaScript syntax validation. Repair it once, conservatively. The reviewed baseline below is known-good and will be kept byte-for-byte before decide(); your response will replace ONLY its final decide() function.\n\n## Validation category\n${category}\n\n## Mandatory narrow repair contract\n- Return EXACTLY ONE JavaScript code block and no prose.\n- The block MUST contain ONLY one complete function with the literal signature: export function decide(boardState)\n- Do NOT return the rest of strategy.mjs. If you do, the runner will discard every part except the single decide() function.\n- Do NOT add imports, exports, top-level declarations, helper functions, async/await, fetch, fs, subprocesses, network, or side effects.\n- Call only helpers/constants that already exist in the reviewed baseline.\n- Prefer quoted strings plus concatenation for reason text rather than complex template literals.\n- Keep every brace, parenthesis, bracket, quote, template literal, and comment balanced and closed.\n- Preserve HOLD behavior and return { x: finite number in [-3, 3], reason: string, hold?: boolean }.\n- The failed candidate came from today's retained evidence. Preserve only the smallest useful intended strategy change that can be expressed safely inside decide() using existing baseline helpers.\n- If the failed candidate's structure is malformed, ignore its structure and use the reviewed baseline decide() as the skeleton.\n\n## Reviewed current strategy.mjs baseline\n\`\`\`javascript\n${baseline}\n\`\`\`\n\n## Failed candidate (non-authoritative evidence of intended change only)\n\`\`\`javascript\n${failed.slice(0, 8000)}${failed.length > 8000 ? '\n// ... failed candidate truncated; do not copy truncation ...' : ''}\n\`\`\`\n\nReturn only the complete replacement decide() function now.`;
  }

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
    if (!baseline.includes(DECIDE_SIGNATURE)) break;
    const category = classifyCandidateValidation(validation.error);
    const prompt = buildDailyCandidateRepairPrompt(candidate, validation.error, baseline);
    const repaired = await callRepairModel(improveModule, prompt);
    if (!repaired) break;

    if (useDecideOnlyRepair(category)) {
      const reconstructed = spliceReviewedDecide(baseline, repaired);
      if (!reconstructed) break;
      candidate = reconstructed;
    } else {
      candidate = repaired;
    }
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
