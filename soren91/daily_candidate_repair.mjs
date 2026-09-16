import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const DEFAULT_MAX_REPAIRS = 1;
const REPAIR_OPENCODE_TIMEOUT_SEC = 420;
const DECIDE_SIGNATURE = 'export function decide(boardState)';
const MAX_REPAIR_SOURCE_BYTES = 65536;
const MAX_DECIDE_REPAIR_BYTES = 12000;
const BEHAVIOR_X_DELTA = 0.02;
const MIN_BEHAVIOR_PROBES = 6;
const MIN_OBSERVED_BEHAVIOR_PROBES = 3;
const MAX_OBSERVED_GAMES = 3;
const MAX_OBSERVED_PROBES = 64;
const MAX_OBSERVED_HISTORY_LINES = 4096;
const MAX_OBSERVED_STATE_BYTES = 256 * 1024;
const DIRECT_SEARCH_BYPASS_RE = /\b(?:candidates|evaluate|simulateDrop|compareMove)\s*\(/;
const PROBE_RADII = Object.freeze({
  1: 0.207, 2: 0.259, 3: 0.316, 4: 0.380, 5: 0.414, 6: 0.470,
  7: 0.559, 8: 0.660, 9: 0.746, 10: 0.846, 11: 0.982, 12: 1.068,
  13: 1.207, 14: 1.385, 15: 1.600,
});

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
  if (text.includes('source search consistency')) return 'source_search_consistency';
  if (text.includes('source unpositioned piece')) return 'source_unpositioned_piece';
  if (text.includes('no decide()') || text.includes('export function decide')) return 'missing_decide';
  if (text.includes('not exported as a function')) return 'decide_not_function';
  if (text.includes('invalid format')) return 'invalid_return';
  if (text.includes('out of range')) return 'x_out_of_range';
  if (text.includes('undefined variable')) return 'undefined_variable';
  if (text.includes('behavior')) return 'behavior_contract';
  return classifyCodeError(text) || 'other';
}

function useDecideOnlyRepair(category) {
  return category === 'code_error_syntax'
    || category === 'code_error_truncated_or_unterminated'
    || category === 'code_error_top_level_reference'
    || category === 'source_unpositioned_piece';
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

function sourceCodeOnly(source) {
  let result = '';
  let state = 'code';
  let escaped = false;
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    const next = source[i + 1];
    if (state === 'line') {
      result += ch === '\n' ? '\n' : ' ';
      if (ch === '\n') state = 'code';
      continue;
    }
    if (state === 'block') {
      result += ch === '\n' ? '\n' : ' ';
      if (ch === '*' && next === '/') {
        result += ' ';
        state = 'code';
        i += 1;
      }
      continue;
    }
    if (state === 'single' || state === 'double' || state === 'template') {
      result += ch === '\n' ? '\n' : ' ';
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
          || (state === 'template' && ch === '`')) state = 'code';
      continue;
    }
    if (ch === '/' && next === '/') {
      result += '  ';
      state = 'line';
      i += 1;
      continue;
    }
    if (ch === '/' && next === '*') {
      result += '  ';
      state = 'block';
      i += 1;
      continue;
    }
    if (ch === "'") {
      result += ' ';
      state = 'single';
      continue;
    }
    if (ch === '"') {
      result += ' ';
      state = 'double';
      continue;
    }
    if (ch === '`') {
      result += ' ';
      state = 'template';
      continue;
    }
    result += ch;
  }
  return result;
}

function startsFromUnpositionedInterface(expression) {
  const text = String(expression || '').trim();
  return /^boardState\s*(?:\?\.|\.)\s*(?:next|hold)\b/.test(text)
    || /^boardState\s*(?:\?\.|\.)\s*nextPieces\s*(?:\?\.)?\s*\[/.test(text)
    || /^normalizePiece\s*\(\s*boardState\s*(?:\?\.|\.)\s*(?:next|hold)\b/.test(text)
    || /^normalizePiece\s*\(\s*boardState\s*(?:\?\.|\.)\s*nextPieces\s*(?:\?\.)?\s*\[/.test(text);
}

function findUnpositionedAliases(code) {
  const aliases = new Set();
  const assignments = [];
  const assignmentRe = /\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([^;\n]+)/g;
  for (const match of code.matchAll(assignmentRe)) assignments.push({ name: match[1], expression: match[2] });

  let changed = true;
  while (changed) {
    changed = false;
    for (const { name, expression } of assignments) {
      if (aliases.has(name)) continue;
      const text = expression.trim();
      const fromInterface = startsFromUnpositionedInterface(text);
      const fromAlias = [...aliases].some(alias => {
        const escaped = alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return new RegExp(`^(?:normalizePiece\\s*\\(\\s*)?${escaped}\\b`).test(text);
      });
      if (fromInterface || fromAlias) {
        aliases.add(name);
        changed = true;
      }
    }
  }
  return aliases;
}

export function validateStrategySourceContracts(source) {
  const raw = String(source || '');
  const decide = extractStrictDecideReplacement(raw);
  // Missing/malformed decide code is categorized by the normal validator. This
  // source contract only adds architecture checks once a single decide body is
  // safely extractable.
  if (!decide) return { valid: true, error: null };

  const code = sourceCodeOnly(decide);
  if (!/\bsearch\s*\(/.test(code) || DIRECT_SEARCH_BYPASS_RE.test(code)) {
    return {
      valid: false,
      error: 'Strategy contract: source search consistency; decide bypasses multi-ply search',
    };
  }

  if (/\bboardState\s*(?:\?\.|\.)\s*(?:next|hold)\s*(?:\?\.|\.)\s*(?:x|y)\b/.test(code)
      || /\bboardState\s*(?:\?\.|\.)\s*nextPieces\s*(?:\?\.)?\s*\[[^\]]+\]\s*(?:\?\.|\.)\s*(?:x|y)\b/.test(code)) {
    return {
      valid: false,
      error: 'Strategy contract: source unpositioned piece; coordinate access is invalid',
    };
  }

  for (const alias of findUnpositionedAliases(code)) {
    const escaped = alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (new RegExp(`\\b${escaped}\\s*(?:\\?\\.|\\.)\\s*(?:x|y)\\b`).test(code)) {
      return {
        valid: false,
        error: 'Strategy contract: source unpositioned piece; coordinate access is invalid',
      };
    }
  }
  return { valid: true, error: null };
}

function probePiece(type, x, y, confidence = 1) {
  return { type, x, y, r: PROBE_RADII[type], confidence };
}

export function buildBehaviorReplayProbes() {
  const probes = [];
  for (let i = 0; i < 12; i += 1) {
    const pieces = [];
    const count = 3 + (i % 5);
    for (let j = 0; j < count; j += 1) {
      const type = 1 + ((i + j * 2) % 7);
      const column = j % 4;
      const row = Math.floor(j / 4);
      pieces.push(probePiece(
        type,
        -2.35 + column * 1.55 + ((i % 3) - 1) * 0.07,
        -4.55 + row * 0.88 + (j % 2) * 0.06,
        j === count - 1 && i % 4 === 0 ? 0.58 : 0.92,
      ));
    }
    const nextType = 1 + ((i * 3) % 6);
    const secondType = 1 + ((i * 3 + 2) % 6);
    const thirdType = 1 + ((i * 3 + 4) % 6);
    const holdType = 1 + ((i + 3) % 6);
    const hasHold = i % 3 !== 1;
    probes.push({
      pieces,
      next: { type: nextType, r: PROBE_RADII[nextType], confidence: 0.95 },
      nextPieces: [
        { type: nextType, r: PROBE_RADII[nextType], confidence: 0.95 },
        { type: secondType, r: PROBE_RADII[secondType], confidence: 0.9 },
        { type: thirdType, r: PROBE_RADII[thirdType], confidence: 0.9 },
      ],
      hold: hasHold ? { type: holdType, r: PROBE_RADII[holdType], confidence: 0.95 } : null,
      holdKnownEmpty: !hasHold,
      canHold: i % 4 !== 3,
      score: i * 250,
      confidence: 0.9,
      garbage: {
        ratio: [0, 0.08, 0.2, 0.42][i % 4],
        height: [-5, -3.4, -1.8, -0.4][i % 4],
        pixelCount: i * 17,
        gauge: [0, 0.25, 0.62, 0.9][(i + 1) % 4],
        columns: i % 3 === 0
          ? [{ left: -0.45, right: 0.35, top: -4.25 + (i % 4) * 0.35 }]
          : [],
      },
    });
  }
  return probes;
}

function gameToken(game) {
  return String(game).padStart(4, '0');
}

function observedStateLooksUsable(state) {
  return !!state && typeof state === 'object' && !Array.isArray(state)
    && Array.isArray(state.pieces) && state.pieces.length <= 256
    && !!state.next && typeof state.next === 'object' && !Array.isArray(state.next)
    && Number.isInteger(state.next.type) && state.next.type >= 1 && state.next.type <= 15;
}

/**
 * Replay only completed retained matches from the private daily workDir.
 * A live latest_N history is ignored until game_N summary exists. Selecting the
 * newest completed games keeps this relevant to the current daily batch without
 * adding a new state/control-plane input or reading outside the copied evidence.
 */
export function loadObservedBehaviorReplayProbes({
  cwd = process.cwd(),
  maxGames = MAX_OBSERVED_GAMES,
  maxProbes = MAX_OBSERVED_PROBES,
} = {}) {
  if (!Number.isInteger(maxGames) || maxGames < 1 || maxGames > 8
      || !Number.isInteger(maxProbes) || maxProbes < 1 || maxProbes > 128) {
    throw new Error('candidate_behavior_probe_budget_invalid');
  }
  const summariesDir = join(cwd, 'tmp', 'summaries');
  const historyDir = join(cwd, 'game_history');
  if (!existsSync(summariesDir) || !existsSync(historyDir)) return [];

  let games;
  try {
    games = readdirSync(summariesDir)
      .map(name => name.match(/^game_(\d+)\.json$/)?.[1] ?? null)
      .filter(Boolean)
      .map(value => Number.parseInt(value, 10))
      .filter(game => Number.isInteger(game) && game >= 0)
      .sort((a, b) => b - a)
      .slice(0, maxGames);
  } catch {
    return [];
  }

  const probes = [];
  const seen = new Set();
  for (const game of games) {
    const token = gameToken(game);
    const archived = join(historyDir, `game_${token}.jsonl`);
    const latest = join(historyDir, `latest_${token}.jsonl`);
    const path = existsSync(archived) ? archived : existsSync(latest) ? latest : null;
    if (!path) continue;

    let lines;
    try {
      lines = readFileSync(path, 'utf8').split(/\r?\n/).filter(line => line.trim());
    } catch {
      continue;
    }
    if (lines.length > MAX_OBSERVED_HISTORY_LINES) continue;

    for (const line of lines) {
      if (probes.length >= maxProbes) break;
      let record;
      try {
        record = JSON.parse(line);
      } catch {
        continue;
      }
      const state = record?.state;
      if (!observedStateLooksUsable(state)) continue;
      let serialized;
      try {
        serialized = JSON.stringify(state);
      } catch {
        continue;
      }
      if (!serialized || serialized.length > MAX_OBSERVED_STATE_BYTES || seen.has(serialized)) continue;
      seen.add(serialized);
      probes.push(state);
    }
    if (probes.length >= maxProbes) break;
  }
  return probes;
}

function cloneProbe(value) {
  return JSON.parse(JSON.stringify(value));
}

function actionFromDecision(value) {
  if (!value || typeof value !== 'object' || !Number.isFinite(value.x)) return null;
  return { x: Number(value.x), hold: value.hold === true };
}

async function importReplayStrategy(source, label) {
  const text = String(source || '');
  if (!text.includes(DECIDE_SIGNATURE)) return null;
  const encoded = Buffer.from(text, 'utf8').toString('base64');
  return import(`data:text/javascript;base64,${encoded}#${label}-${Date.now()}-${Math.random()}`);
}

export async function compareCandidateBehavior(baselineStrategy, candidateStrategy, {
  minXDelta = BEHAVIOR_X_DELTA,
  probes = null,
} = {}) {
  if (!(Number.isFinite(minXDelta) && minXDelta > 0 && minXDelta <= 0.5)) {
    throw new Error('candidate_behavior_delta_invalid');
  }
  if (probes != null && !Array.isArray(probes)) throw new Error('candidate_behavior_probes_invalid');
  const replayProbes = Array.isArray(probes) && probes.length > 0 ? probes : buildBehaviorReplayProbes();
  const baseline = await importReplayStrategy(baselineStrategy, 'baseline');
  const candidate = await importReplayStrategy(candidateStrategy, 'candidate');
  if (typeof baseline?.decide !== 'function' || typeof candidate?.decide !== 'function') {
    return { changed: false, compared: 0, changedCount: 0, candidateFailures: 1 };
  }

  let compared = 0;
  let changedCount = 0;
  let candidateFailures = 0;
  for (const probe of replayProbes) {
    let baselineDecision;
    try {
      baselineDecision = actionFromDecision(baseline.decide(cloneProbe(probe)));
    } catch {
      continue;
    }
    if (!baselineDecision) continue;

    let candidateDecision;
    try {
      candidateDecision = actionFromDecision(candidate.decide(cloneProbe(probe)));
    } catch {
      candidateFailures += 1;
      continue;
    }
    if (!candidateDecision) {
      candidateFailures += 1;
      continue;
    }

    compared += 1;
    if (baselineDecision.hold !== candidateDecision.hold
        || Math.abs(baselineDecision.x - candidateDecision.x) >= minXDelta) {
      changedCount += 1;
    }
  }
  return {
    changed: changedCount > 0,
    compared,
    changedCount,
    candidateFailures,
  };
}

function compactObservedProbe(state) {
  const pieces = Array.isArray(state?.pieces) ? state.pieces.slice(0, 24).map(piece => ({
    type: piece?.type ?? null,
    x: Number.isFinite(piece?.x) ? Math.round(piece.x * 100) / 100 : null,
    y: Number.isFinite(piece?.y) ? Math.round(piece.y * 100) / 100 : null,
    confidence: Number.isFinite(piece?.confidence) ? Math.round(piece.confidence * 100) / 100 : null,
  })) : [];
  return {
    pieces,
    next: state?.next?.type ?? null,
    nextPieces: Array.isArray(state?.nextPieces) ? state.nextPieces.slice(0, 3).map(piece => piece?.type ?? null) : [],
    hold: state?.hold?.type ?? null,
    holdKnownEmpty: state?.holdKnownEmpty === true,
    canHold: state?.canHold === true,
    garbageRatio: Number.isFinite(state?.garbage?.ratio) ? Math.round(state.garbage.ratio * 100) / 100 : null,
    garbageGauge: Number.isFinite(state?.garbage?.gauge) ? Math.round(state.garbage.gauge * 100) / 100 : null,
  };
}

function formatObservedRepairContext(observedProbes) {
  if (!Array.isArray(observedProbes) || observedProbes.length === 0) return '';
  const sample = observedProbes.slice(0, 6).map((probe, index) =>
    `- replay${index + 1}: ${JSON.stringify(compactObservedProbe(probe))}`);
  return `\n## Retained-match behavior replay targets\nThese sanitized board states came from the newest completed retained game histories in today's private evidence copy. A behavior-contract repair must change x by at least ${BEHAVIOR_X_DELTA} or change HOLD on at least one of these states while remaining safe on the others.\n${sample.join('\n')}\n`;
}

export function buildDailyCandidateRepairPrompt(failedCode, validationError, baselineStrategy, observedProbes = []) {
  const failed = String(failedCode || '');
  const baseline = String(baselineStrategy || '');
  const category = classifyCandidateValidation(validationError);
  const sourceRepair = category === 'source_search_consistency' || category === 'source_unpositioned_piece';
  const observedContext = (category === 'behavior_contract' || sourceRepair)
    ? formatObservedRepairContext(observedProbes)
    : '';
  const sourceRules = category === 'source_search_consistency'
    ? `\n## Search-consistency repair rules\n- Keep the reviewed decide() root-selection control flow: the normal plan comes from search(), the optional HOLD plan comes from search(), and the final action is chosen between those search plans.\n- In decide(), do NOT directly call candidates(), evaluate(), simulateDrop(), or compareMove().\n- If the intended improvement changes height/root/merge/risk ranking, implement it inside evaluate(), compareMove(), comparePath(), or search() so it participates in the full beam path.\n- Any root/pathRisk/minClearance/depth/value fields used together must describe the SAME search path. Never copy future metrics from one root onto another immediate root.\n- Compare HOLD against the actual no-HOLD search plan that will be played, and preserve multi-ply look-ahead.\n- Preserve only the smallest evidence-backed scoring/search change needed to realize the failed candidate's useful intent.\n`
    : category === 'source_unpositioned_piece'
      ? `\n## Unpositioned-piece repair rules\n- Only boardState.pieces[] has x/y. boardState.next, nextPieces[], hold, and aliases normalized from them are unpositioned.\n- Never read or infer x/y from current, held, alternative, next, queue entries, or any alias derived from those unpositioned pieces.\n- Use search() / evaluated placement results and positioned boardState.pieces[] for spatial decisions.\n`
      : '';

  if (useDecideOnlyRepair(category)) {
    return `The proposed Soren91 strategy failed JavaScript/source validation. Repair it once, conservatively. The reviewed baseline below is known-good and will be kept byte-for-byte before decide(); your response will replace ONLY its final decide() function.\n\n## Validation category\n${category}\n${observedContext}${sourceRules}\n## Mandatory narrow repair contract\n- Return EXACTLY ONE JavaScript code block and no prose.\n- The block MUST contain ONLY one complete function with the literal signature: export function decide(boardState)\n- Do NOT return the rest of strategy.mjs. If you do, the runner will discard every part except the single decide() function.\n- The reviewed module prefix is immutable for this repair. All boardState/current-turn logic must stay inside decide(); never add top-level state references.\n- Do NOT add imports, exports, top-level declarations, helper functions, async/await, fetch, fs, subprocesses, network, or side effects.\n- Call only helpers/constants that already exist in the reviewed baseline.\n- Prefer quoted strings plus concatenation for reason text rather than complex template literals.\n- Keep every brace, parenthesis, bracket, quote, template literal, and comment balanced and closed.\n- Preserve HOLD behavior and return { x: finite number in [-3, 3], reason: string, hold?: boolean }.\n- The failed candidate came from today's retained evidence. Preserve only the smallest useful intended strategy change that can be expressed safely inside decide() using existing baseline helpers.\n- The repaired candidate must make at least one real gameplay decision change: a meaningfully different x (>= ${BEHAVIOR_X_DELTA}) or a different HOLD choice on a plausible board state. Formatting, comments, reason text, or diagnostics alone are not an improvement.\n- If retained-match replay targets are shown above, the repaired candidate MUST change x/HOLD on at least one of those actual retained states.\n- If the failed candidate's structure is malformed, ignore its structure and use the reviewed baseline decide() as the skeleton.\n\n## Reviewed current strategy.mjs baseline\n\`\`\`javascript\n${baseline}\n\`\`\`\n\n## Failed candidate (non-authoritative evidence of intended change only)\n\`\`\`javascript\n${failed.slice(0, 8000)}${failed.length > 8000 ? '\n// ... failed candidate truncated; do not copy truncation ...' : ''}\n\`\`\`\n\nReturn only the complete replacement decide() function now.`;
  }

  const codeErrorRules = category.startsWith('code_error_')
    ? `\n## Code-error-specific repair rules\n- The reviewed baseline below is known-good source structure and is authoritative. Reproduce its complete module structure first, then apply only the smallest local evidence-backed strategy change.\n- Treat the failed candidate only as a non-authoritative hint about intended logic. Do NOT copy broken/truncated structure from it.\n- Do not duplicate helper names, constants, exports, or top-level declarations that already exist in the baseline.\n- Keep every brace, parenthesis, bracket, quote, template literal, and comment balanced and closed.\n- Do not add imports or references to packages/modules.\n- Do not execute board-state-dependent logic at module top level; boardState and derived state belong inside decide/helpers only.\n- Preserve the complete tail of the reviewed baseline; never stop after the changed helper or decide().\n- Before answering, mentally parse the whole module from first to last line and verify it is valid ESM.\n`
    : '';
  return `The proposed Soren91 strategy failed validation. Repair it once, conservatively, using the reviewed current strategy as the complete-module baseline.\n\n## Validation category\n${category}\n${observedContext}${sourceRules}${codeErrorRules}\n## Mandatory output contract\n- Return EXACTLY ONE JavaScript code block and no prose.\n- That one block MUST be the COMPLETE replacement strategy.mjs module, not a patch, snippet, helper, or explanation.\n- It MUST contain the literal signature: export function decide(boardState)\n- preserve every helper/export from the reviewed baseline unless the improvement intentionally and safely replaces it.\n- preserve HOLD behavior and return { x: finite number in [-3, 3], reason: string, hold?: boolean }.\n- no imports, async/await, fetch, fs, subprocesses, network, or side effects.\n- The repaired candidate must make at least one real gameplay decision change: a meaningfully different x (>= ${BEHAVIOR_X_DELTA}) or a different HOLD choice on a plausible board state. Formatting, comments, reason text, or diagnostics alone do not satisfy the improvement contract.\n- If retained-match replay targets are shown above, the repaired candidate MUST change x/HOLD on at least one of those actual retained states.\n- If the failed candidate is partial or cannot be safely integrated, start from the reviewed baseline and make the smallest evidence-based change needed.\n\n## Reviewed current strategy.mjs baseline\n\`\`\`javascript\n${baseline}\n\`\`\`\n\n## Failed candidate (non-authoritative hint only)\n\`\`\`javascript\n${failed.slice(0, 8000)}${failed.length > 8000 ? '\n// ... failed candidate truncated; DO NOT copy truncation ...' : ''}\n\`\`\`\n\nReturn the complete corrected strategy.mjs now.`;
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

async function validateWithBehaviorNovelty(improveModule, candidate, baseline, observedProbes) {
  const sourceValidation = validateStrategySourceContracts(candidate);
  if (!sourceValidation.valid) return sourceValidation;

  const validation = await improveModule.validateStrategy(candidate);
  if (!validation.valid || !baseline.includes(DECIDE_SIGNATURE) || !String(candidate || '').includes(DECIDE_SIGNATURE)) {
    return validation;
  }

  // Keep deterministic probes as a safety/coverage check, but do not require a
  // novelty hit on synthetic states when real retained match states exist.
  const deterministic = await compareCandidateBehavior(baseline, candidate);
  if (deterministic.candidateFailures > 0) {
    return {
      valid: false,
      error: 'Strategy contract: behavior replay failed for candidate on deterministic probe',
    };
  }
  if (deterministic.compared < MIN_BEHAVIOR_PROBES) {
    return {
      valid: false,
      error: 'Strategy contract: behavior replay coverage was insufficient',
    };
  }

  if (Array.isArray(observedProbes) && observedProbes.length > 0) {
    const observed = await compareCandidateBehavior(baseline, candidate, { probes: observedProbes });
    if (observed.candidateFailures > 0) {
      return {
        valid: false,
        error: 'Strategy contract: behavior replay failed for candidate on retained-match probe',
      };
    }
    const required = Math.min(MIN_OBSERVED_BEHAVIOR_PROBES, observedProbes.length);
    if (observed.compared < required) {
      return {
        valid: false,
        error: 'Strategy contract: retained-match behavior replay coverage was insufficient',
      };
    }
    if (!observed.changed) {
      return {
        valid: false,
        error: 'Strategy contract: behavior no-op; candidate does not change x/hold on retained-match replay probes',
      };
    }
    return validation;
  }

  if (!deterministic.changed) {
    return {
      valid: false,
      error: 'Strategy contract: behavior no-op; candidate does not change x/hold on deterministic replay probes',
    };
  }
  return validation;
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

  const baseline = readReviewedBaseline();
  const observedProbes = loadObservedBehaviorReplayProbes();
  let candidate = initialCandidate;
  let validation = await validateWithBehaviorNovelty(improveModule, candidate, baseline, observedProbes);
  const initialCategory = validation.valid ? null : classifyCandidateValidation(validation.error);
  let repairs = 0;

  while (!validation.valid && repairs < maxRepairs) {
    repairs += 1;
    if (!baseline.includes(DECIDE_SIGNATURE)) break;
    const category = classifyCandidateValidation(validation.error);
    const prompt = buildDailyCandidateRepairPrompt(candidate, validation.error, baseline, observedProbes);
    const repaired = await callRepairModel(improveModule, prompt);
    if (!repaired) break;

    if (useDecideOnlyRepair(category)) {
      const reconstructed = spliceReviewedDecide(baseline, repaired);
      if (!reconstructed) break;
      candidate = reconstructed;
    } else {
      candidate = repaired;
    }
    validation = await validateWithBehaviorNovelty(improveModule, candidate, baseline, observedProbes);
  }

  return {
    candidate,
    validation,
    repairs,
    initialCategory,
    finalCategory: validation.valid ? null : classifyCandidateValidation(validation.error),
  };
}