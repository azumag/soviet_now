import 'dotenv/config';
import { execFile } from 'child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

const RUNTIME_CONFIG_PATH = join(import.meta.dirname || '.', 'runtime_config.json');
const PROJECT_DIR = join(import.meta.dirname || '.', '..');
const AI_QUEUE_SCRIPT = join(PROJECT_DIR, 'lib', 'ai_generation_queue_cli.sh');
const DEFAULT_GEMINI_MODEL = 'gemini-2.5-flash';
const DEFAULT_OPENCODE_AGENT = 'glmflash';
// opencode CLI に渡す既定のモデルチェーン。運用では AI_COMMON_AGENTS
// (core/config.sh) をそのまま使い、opencode/opencode-go の項目だけを採る。
const DEFAULT_OPENCODE_MODELS = 'opencode:muse-spark-1.3-contributor-free,opencode:muse-spark-1.2-contributor-free,opencode-go:muse-spark-1.3-contributor,opencode-go:muse-spark-1.2-contributor,opencode-go:deepseek-v4.1-flash,opencode-go:deepseek-v4-flash';

export function parseOpencodeModels(raw) {
  const list = String(raw || '').split(',').map(part => part.trim()).filter(Boolean);
  const models = list.filter(spec => spec.startsWith('opencode:') || spec.startsWith('opencode-go:') || spec.startsWith('openrouter:'));
  return models.length > 0 ? models : DEFAULT_OPENCODE_MODELS.split(',');
}

// `opencode:<model>` -> `opencode/<model>` (opencode CLI の --model 形式)。
export function resolveOpencodeModel(spec) {
  const value = String(spec || '').trim();
  const index = value.indexOf(':');
  return index < 0 ? value : `${value.slice(0, index)}/${value.slice(index + 1)}`;
}
const DEFAULT_OLLAMA_BASE_URL = 'http://192.168.11.13:11434';
const DEFAULT_CLAUDE_TIMEOUT_MS = 30000;
const DEFAULT_CCOGENT_TIMEOUT_MS = 120000;
const DEFAULT_GEMINI_TIMEOUT_MS = 30000;
// 通常設定 (core/config.sh: RADIO_OPENCODE_TIMEOUT=180) に合わせる。
const DEFAULT_OPENCODE_TIMEOUT_MS = 180000;
// Per-model cap inside the opencode chain. One hanging provider must not eat
// the whole budget: 2026-09-16 on the VM `opencode/muse-spark-1.3-contributor-free`
// (and ...-1.2-contributor-free) hung until the full 180s timeout, so every
// beat/midgame comment died before reaching the opencode-go entries that answer
// in seconds. A cap makes the chain fall through quickly.
export const DEFAULT_OPENCODE_PER_MODEL_TIMEOUT_MS = 45000;

export function resolvePerModelTimeoutMs(timeoutMs, requestedMs) {
  const total = Number(timeoutMs) > 0 ? Number(timeoutMs) : DEFAULT_OPENCODE_TIMEOUT_MS;
  const requested = Number(requestedMs) > 0 ? Number(requestedMs) : DEFAULT_OPENCODE_PER_MODEL_TIMEOUT_MS;
  return Math.max(5000, Math.min(requested, total));
}

// A model that hits the per-attempt timeout is almost always rate limited /
// unreachable, not slow-but-working. Remember it for this process so every
// later comment does not pay the same timeout again.
export const OPENCODE_TIMEOUT_COOLDOWN_MS = 10 * 60 * 1000;
const hungOpencodeModels = new Map();

export function isOpencodeTimeout(err) {
  if (!err) return false;
  if (err.killed === true || err.signal === 'SIGTERM') return true;
  return /timed?\s*out|ETIMEDOUT/i.test(String(err.message || ''));
}
const DEFAULT_OPENCODE_PERMISSION = '{"*":"deny","read":"allow","glob":"allow","grep":"allow","list":"allow","web":"allow","web-search":"allow"}';

function opencodeQueueLane(tag, options = {}) {
  if (options.queueLane) return String(options.queueLane);
  const value = String(tag || '').toLowerCase();
  if (/(^|[_:-])(strategy|improve)([_:-]|$)/.test(value)) return 'improve';
  if (/(^|[_:-])(radio|jiji|celebration)([_:-]|$)/.test(value)) return 'radio';
  return 'comment';
}

function runAiQueueCli(args) {
  return new Promise((resolve, reject) => {
    const child = execFile(AI_QUEUE_SCRIPT, args, {
      cwd: PROJECT_DIR,
      encoding: 'utf-8',
      maxBuffer: 64 * 1024,
      env: { ...process.env, AI_GENERATION_QUEUE_OWNER_PID: String(process.pid) },
    }, (err, stdout, stderr) => {
      if (err) {
        const queueErr = new Error(`opencode queue command failed (code=${err.code ?? 'unknown'})`);
        queueErr.code = err.code;
        queueErr.queueStderr = String(stderr || '').slice(0, 300);
        return reject(queueErr);
      }
      resolve(String(stdout || '').trim());
    });
    child.stdin?.on('error', () => {});
  });
}

async function acquireOpencodeQueue(tag, options = {}) {
  if (process.env.AI_GENERATION_QUEUE_ENABLED === '0') return null;
  const lane = opencodeQueueLane(tag, options);
  const token = await runAiQueueCli(['acquire', lane]);
  return token ? { lane, token } : null;
}

async function releaseOpencodeQueue(tag, slot) {
  if (!slot) return;
  try {
    await runAiQueueCli(['release', slot.lane, slot.token]);
  } catch (err) {
    console.error(`[${tag}] opencode queue release failed: code=${err?.code ?? 'unknown'}`);
  }
}

function readRuntimeConfig() {
  if (!existsSync(RUNTIME_CONFIG_PATH)) return {};
  try {
    return JSON.parse(readFileSync(RUNTIME_CONFIG_PATH, 'utf-8'));
  } catch {
    return {};
  }
}

function readTextConfig() {
  const runtimeConfig = readRuntimeConfig();
  return runtimeConfig?.merikenTextGeneration ?? {};
}

export function stripAnsi(text) {
  return String(text || '')
    .replace(/\u001b\[[0-9;]*[a-zA-Z]/g, '')
    .replace(/[\x00-\x09\x0b-\x0d\x0e-\x1f]/g, '')
    .replace(/\r/g, '');
}

function containsProviderErrorText(text) {
  return /invalid bearer token|authentication_error|failed to authenticat(?:e|ed)|api error[: ]|request_id|invalid error token|invalid token|not logged in|please run \/login|potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|unsafe or sensitive content in input or generation|content policy|safety policy|rate limit|rate_limit|too many requests|429\b|overloaded_error|quota|usage limit/i.test(String(text || ''));
}

function containsClaudeLoginErrorText(text) {
  return /not logged in|please run \/login/i.test(String(text || ''));
}

function shellSingleQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function cleanOpencodeOutput(raw) {
  const lines = stripAnsi(raw).split('\n');
  const kept = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith('>')) continue;
    if (trimmed === '^D') continue;
    if (trimmed.startsWith('Script started on ')) continue;
    if (trimmed.startsWith('Script done on ')) continue;
    if (/^\/[^ ]*$/.test(trimmed)) continue;
    if (/^\/Users\//.test(trimmed)) continue;
    if (/^⚙/.test(trimmed)) continue;
    if (/^\{\s*"query"/.test(trimmed)) continue;
    if (/^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b/i.test(trimmed)) continue;
    if (/^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b/i.test(trimmed)) continue;
    if (/^(read|glob|grep|ls|edit|write|multiedit)\b/i.test(trimmed)) continue;
    if (/^(error|warning)\s*:/i.test(trimmed)) continue;
    if (/file not found:|no such file or directory|permission denied|invalid arguments/i.test(trimmed)) continue;
    kept.push(
      line.replace(/<\/?(arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>/g, '').trim(),
    );
  }
  return kept.filter(Boolean).join('\n').trim();
}

export function extractOpencodeJsonText(raw) {
  const pieces = [];
  for (const line of String(raw || '').split('\n')) {
    if (!line.trim()) continue;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      throw new Error('opencode returned invalid JSON event');
    }
    if (!event || typeof event !== 'object' || event.error) {
      throw new Error('opencode returned error event');
    }
    if (!['step_start', 'step_finish', 'text'].includes(event.type)) {
      throw new Error(`opencode returned unexpected event type: ${String(event.type || '')}`);
    }
    const part = event.part ?? {};
    if (!part || typeof part !== 'object' || part.error) {
      throw new Error('opencode returned error part');
    }
    if (
      ['tool-calls', 'tool_calls', 'error'].includes(part.reason)
      || 'tool' in part
      || 'toolCallID' in part
      || 'tool_calls' in part
    ) {
      throw new Error('opencode returned tool/error event');
    }
    if (event.type === 'text') {
      if (part.type != null && part.type !== 'text') {
        throw new Error('opencode returned invalid text part');
      }
      if (typeof part.text !== 'string') {
        throw new Error('opencode returned invalid text event');
      }
      pieces.push(part.text);
    }
  }
  const text = pieces.join('').trim();
  if (!text) throw new Error('opencode returned no text');
  return text;
}

function makeProviderError(message, detail = '') {
  const err = new Error(detail ? `${message}: ${detail}` : message);
  err.providerFailure = true;
  return err;
}

function parseTimeoutMs(value, fallbackMs) {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed * 1000 : fallbackMs;
}

export function resolveTextAiConfig() {
  const textConfig = readTextConfig();
  return {
    claudePreset: process.env.SOREN91_TEXT_CLAUDE_PRESET
      || process.env.SOREN91_COMMENT_CLAUDE_MODEL
      || textConfig.claudePreset
      || 'haiku',
    claudeFallbackPreset: process.env.SOREN91_TEXT_CLAUDE_FALLBACK_PRESET
      || process.env.SOREN91_COMMENT_CLAUDE_FALLBACK_MODEL
      || textConfig.claudeFallbackPreset
      || 'ccogent',
    geminiModel: process.env.SOREN91_TEXT_GEMINI_MODEL
      || process.env.SOREN91_COMMENT_GEMINI_MODEL
      || process.env.SOREN91_GEMINI_FALLBACK_MODEL
      || textConfig.geminiFallbackModel
      || DEFAULT_GEMINI_MODEL,
    opencodeAgent: process.env.SOREN91_TEXT_OPENCODE_AGENT
      || process.env.SOREN91_COMMENT_OPENCODE_AGENT
      || process.env.RADIO_FALLBACK
      || textConfig.opencodeFallbackAgent
      || DEFAULT_OPENCODE_AGENT,
    ollamaBaseUrl: process.env.SOREN91_OLLAMA_BASE_URL
      || textConfig.ollamaBaseUrl
      || DEFAULT_OLLAMA_BASE_URL,
    claudeTimeoutMs: parseTimeoutMs(
      process.env.SOREN91_TEXT_CLAUDE_TIMEOUT
        || process.env.SOREN91_COMMENT_CLAUDE_TIMEOUT
        || textConfig.claudeTimeoutSec,
      DEFAULT_CLAUDE_TIMEOUT_MS,
    ),
    ccogentTimeoutMs: parseTimeoutMs(
      process.env.SOREN91_TEXT_CCOGENT_TIMEOUT
        || process.env.SOREN91_COMMENT_CCOGENT_TIMEOUT
        || textConfig.ccogentTimeoutSec,
      DEFAULT_CCOGENT_TIMEOUT_MS,
    ),
    geminiTimeoutMs: parseTimeoutMs(
      process.env.SOREN91_TEXT_GEMINI_TIMEOUT
        || process.env.SOREN91_COMMENT_GEMINI_TIMEOUT
        || process.env.COMMENT_GEMINI_TIMEOUT
        || textConfig.geminiTimeoutSec,
      DEFAULT_GEMINI_TIMEOUT_MS,
    ),
    opencodeTimeoutMs: parseTimeoutMs(
      process.env.SOREN91_TEXT_OPENCODE_TIMEOUT
        || process.env.SOREN91_COMMENT_OPENCODE_TIMEOUT
        || process.env.COMMENT_OPENCODE_TIMEOUT
        || process.env.RADIO_OPENCODE_TIMEOUT
        || textConfig.opencodeTimeoutSec,
      DEFAULT_OPENCODE_TIMEOUT_MS,
    ),
    // 1モデルあたりの上限 (秒)。opencode chain 内のハングを早期に切って
    // 次のモデルへ落とすためのもの。
    opencodePerModelTimeoutMs: parseTimeoutMs(
      process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT
        || process.env.SOREN91_COMMENT_OPENCODE_MODEL_TIMEOUT,
      DEFAULT_OPENCODE_PER_MODEL_TIMEOUT_MS,
    ),
    // opencode CLI に投げるモデルチェーン。既定は通常チェーン AI_COMMON_AGENTS。
    opencodeModels: parseOpencodeModels(process.env.AI_COMMON_AGENTS || process.env.RADIO_AGENTS),
    opencodePermission: process.env.SOREN91_TEXT_OPENCODE_PERMISSION
      || process.env.SOREN91_COMMENT_OPENCODE_PERMISSION
      || process.env.COMMENT_OPENCODE_PERMISSION
      || textConfig.opencodePermission
      || DEFAULT_OPENCODE_PERMISSION,
  };
}

export function resolveClaudePreset(selection = resolveTextAiConfig().claudePreset) {
  const config = resolveTextAiConfig();
  const preset = String(selection || '').trim() || 'haiku';
  const ollamaEnv = {
    ANTHROPIC_AUTH_TOKEN: 'ollama',
    ANTHROPIC_BASE_URL: config.ollamaBaseUrl,
    ANTHROPIC_API_KEY: '',
  };
  switch (preset) {
    case 'haiku':
      return { preset, command: 'claude', model: 'haiku', env: {} };
    case 'sonnet':
      return { preset, command: 'claude', model: 'sonnet', env: {} };
    case 'ccogent':
      return { preset, command: 'ccogent', model: '', env: {}, verbose: false, shell: true, promptAsArg: true, timeoutMs: config.ccogentTimeoutMs };
    case 'gemma4e':
      return { preset, command: 'claude', model: 'gemma4:latest', env: ollamaEnv };
    case 'qwen35e':
      return { preset, command: 'claude', model: 'qwen3.5:9b', env: ollamaEnv };
    default:
      return { preset, command: 'claude', model: preset, env: {} };
  }
}

export function extractPlainText(raw) {
  const lines = stripAnsi(raw)
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .filter(line => !line.startsWith('```'))
    .filter(line => !/^(assistant|analysis|final|tool_call|tool_result)$/i.test(line))
    .filter(line => !/^(agent|model|provider)\s*[:=]/i.test(line));
  if (lines.length === 0) return null;
  return lines.join('\n').trim();
}

function parseOutputOrThrow(raw, parseOutput) {
  const parser = typeof parseOutput === 'function' ? parseOutput : extractPlainText;
  const parsed = parser(String(raw || ''));
  if (!parsed) {
    throw new Error('model returned empty text');
  }
  return parsed;
}

export function runClaudeText(tag, promptText, options = {}) {
  const config = resolveTextAiConfig();
  const target = resolveClaudePreset(options.claudePreset || config.claudePreset);
  const env = { ...process.env, ...target.env, ...(options.extraEnv || {}) };
  if (!target.env.ANTHROPIC_BASE_URL) {
    delete env.ANTHROPIC_BASE_URL;
    delete env.ANTHROPIC_AUTH_TOKEN;
  }
  const timeoutMs = options.timeoutMs || target.timeoutMs || config.claudeTimeoutMs;
  return new Promise((resolve, reject) => {
    const args = ['-p'];
    if (target.promptAsArg) args.push(promptText);
    if (target.model) args.push('--model', target.model);
    if (target.verbose !== false) args.push('--verbose');
    const command = target.command || 'claude';
    const execCommand = target.shell ? 'zsh' : command;
    const execArgs = target.shell ? ['-ic', `${command} -- ${args.map(shellSingleQuote).join(' ')}`] : args;
    const child = execFile(execCommand, execArgs, {
      encoding: 'utf-8',
      maxBuffer: 2 * 1024 * 1024,
      timeout: timeoutMs,
      cwd: '/tmp',
      env,
    }, (err, stdout, stderr) => {
      const stderrPreview = String(stderr || '').slice(0, 500);
      const combined = `${stdout || ''}\n${stderr || ''}`;
      if (containsClaudeLoginErrorText(combined)) {
        console.error(`[${tag}] ${command} unavailable: not logged in`);
      }
      if (containsProviderErrorText(combined)) {
        if (stderrPreview) console.error(`[${tag}] ${command} stderr:`, stderrPreview);
        return reject(makeProviderError(`${command} provider/rate-limit failure (${target.preset})`, stderrPreview || String(stdout || '').slice(0, 300)));
      }
      if (err) {
        console.error(`[${tag}] ${command} error: code=${err.code} signal=${err.signal} killed=${err.killed} preset=${target.preset} stderr=${stderrPreview || '(empty)'} stdout_preview=${String(stdout || '').slice(0, 200)}`);
        return reject(err);
      }
      try {
        resolve(parseOutputOrThrow(stdout, options.parseOutput));
      } catch (parseErr) {
        reject(parseErr);
      }
    });
    child.stdin.on('error', () => {});
    if (target.promptAsArg) {
      child.stdin.end();
    } else {
      child.stdin.write(promptText);
      child.stdin.end();
    }
  });
}

export function runGeminiText(tag, promptText, options = {}) {
  const config = resolveTextAiConfig();
  const model = options.geminiModel || config.geminiModel;
  const args = ['-p', '', '-o', 'text'];
  if (model) args.push('--model', model);

  return new Promise((resolve, reject) => {
    const child = execFile('gemini', args, {
      encoding: 'utf-8',
      maxBuffer: 2 * 1024 * 1024,
      timeout: options.timeoutMs || config.geminiTimeoutMs,
      cwd: '/tmp',
      env: { ...process.env, ...(options.extraEnv || {}) },
    }, (err, stdout, stderr) => {
      const stderrPreview = String(stderr || '').slice(0, 500);
      const combined = `${stdout || ''}\n${stderr || ''}`;
      if (containsProviderErrorText(combined)) {
        if (stderrPreview) console.error(`[${tag}] gemini stderr:`, stderrPreview);
        return reject(makeProviderError('gemini provider/rate-limit failure', stderrPreview || String(stdout || '').slice(0, 300)));
      }
      if (err) {
        if (stderrPreview) console.error(`[${tag}] gemini stderr:`, stderrPreview);
        return reject(err);
      }
      try {
        resolve(parseOutputOrThrow(stdout, options.parseOutput));
      } catch (parseErr) {
        reject(parseErr);
      }
    });
    child.stdin.on('error', () => {});
    child.stdin.write(promptText);
    child.stdin.end();
  });
}

function runOpencodeOnce({ model, promptText, timeoutMs, permission, extraEnv, parseOutput }) {
  return new Promise((resolve, reject) => {
    const tempDir = mkdtempSync(join(tmpdir(), 'soren91_opencode_text_'));
    const child = execFile('opencode', ['run', '--format', 'json', '--model', model], {
      encoding: 'utf-8',
      timeout: timeoutMs,
      cwd: tempDir,
      env: { ...process.env, OPENCODE_PERMISSION: permission, ...(extraEnv || {}) },
      maxBuffer: 2 * 1024 * 1024,
    }, (err, stdout, stderr) => {
      try {
        const combined = `${stdout || ''}\n${stderr || ''}`;
        if (containsProviderErrorText(combined)) {
          return reject(makeProviderError(`opencode provider failure (${model})`, combined.slice(0, 300)));
        }
        if (err) return reject(err);
        const text = extractOpencodeJsonText(stdout);
        const cleaned = cleanOpencodeOutput(text);
        try {
          resolve(parseOutputOrThrow(cleaned, parseOutput));
        } catch (parseErr) {
          reject(parseErr);
        }
      } finally {
        try { rmSync(tempDir, { recursive: true, force: true }); } catch {}
      }
    });
    child.stdin.on('error', () => {});
    child.stdin.write(promptText);
    child.stdin.end();
  });
}

// 通常チェーン (AI_COMMON_AGENTS) を順に試す。opencode CLI の --model は
// `opencode/<model>` 形式。timeout は通常設定 (RADIO_OPENCODE_TIMEOUT) を流用。
async function runOpencodeTextUnqueued(tag, promptText, options = {}) {
  const config = resolveTextAiConfig();
  const agents = options.opencodeAgent
    ? [options.opencodeAgent] : (config.opencodeModels || []);
  const models = agents.map(resolveOpencodeModel).filter(Boolean);
  if (models.length === 0) {
    throw makeProviderError('no opencode models configured');
  }
  const timeoutMs = options.timeoutMs || config.opencodeTimeoutMs;
  const perModelTimeoutMs = resolvePerModelTimeoutMs(
    timeoutMs,
    options.perModelTimeoutMs || config.opencodePerModelTimeoutMs,
  );
  const permission = options.opencodePermission || config.opencodePermission;

  let lastErr = null;
  for (const model of models) {
    const cooldownUntil = hungOpencodeModels.get(model) || 0;
    if (cooldownUntil > Date.now()) {
      console.error(`[${tag}] opencode model skipped (timed out recently): ${model}`);
      continue;
    }
    try {
      const text = await runOpencodeOnce({
        model, promptText, timeoutMs: perModelTimeoutMs, permission,
        extraEnv: options.extraEnv, parseOutput: options.parseOutput,
      });
      if (text) return text;
    } catch (err) {
      lastErr = err;
      if (isOpencodeTimeout(err)) {
        hungOpencodeModels.set(model, Date.now() + OPENCODE_TIMEOUT_COOLDOWN_MS);
      }
      console.error(`[${tag}] opencode model failed (${model}): ${err?.message || err}`);
    }
  }
  throw lastErr || makeProviderError('opencode returned no text for any model');
}

export async function runOpencodeText(tag, promptText, options = {}) {
  const queueSlot = await acquireOpencodeQueue(tag, options);
  try {
    return await runOpencodeTextUnqueued(tag, promptText, options);
  } finally {
    await releaseOpencodeQueue(tag, queueSlot);
  }
}

export async function generateTextWithFallbacks(tag, promptText, options = {}) {
  const allowGemini = process.env.SOREN91_ALLOW_GEMINI === '1';
  const requestedProviders = String(options.fallbackMode || 'claude,opencode')
    .split(',')
    .map(provider => provider.trim().toLowerCase())
    .filter(Boolean);
  let fallbackProviders = requestedProviders.filter(provider => provider !== 'gemini' || allowGemini);
  if (!allowGemini && requestedProviders.includes('gemini')) {
    console.error(`[${tag}] gemini fallback disabled; set SOREN91_ALLOW_GEMINI=1 to opt in`);
  }
  if (fallbackProviders.length === 0) {
    fallbackProviders = ['claude'];
  }
  let lastErr = null;

  for (const provider of fallbackProviders) {
    if (provider === 'claude') {
      try {
        return await runClaudeText(tag, promptText, options);
      } catch (err) {
        lastErr = err;
        const claudeFallbackPreset = options.claudeFallbackPreset || resolveTextAiConfig().claudeFallbackPreset;
        const currentPreset = resolveClaudePreset(options.claudePreset || resolveTextAiConfig().claudePreset).preset;
        if (claudeFallbackPreset && claudeFallbackPreset !== currentPreset) {
          console.error(`[${tag}] claude failed -> ${claudeFallbackPreset} fallback (${err.message})`);
          try {
            return await runClaudeText(tag, promptText, { ...options, claudePreset: claudeFallbackPreset });
          } catch (fallbackErr) {
            lastErr = fallbackErr;
            console.error(`[${tag}] ${claudeFallbackPreset} also failed (${fallbackErr.message})`);
          }
        } else {
          console.error(`[${tag}] claude failed (${err.message})`);
        }
      }
      continue;
    }

    if (provider === 'gemini') {
      try {
        return await runGeminiText(tag, promptText, options);
      } catch (err) {
        lastErr = err;
        console.error(`[${tag}] gemini failed (${err.message})`);
      }
      continue;
    }

    if (provider === 'opencode' && options.includeOpencodeFallback !== false) {
      try {
        return await runOpencodeText(tag, promptText, options);
      } catch (err) {
        lastErr = err;
        console.error(`[${tag}] opencode failed (${err.message})`);
      }
    }
  }

  throw lastErr || new Error(`no usable fallback providers: ${fallbackProviders.join(',') || '(empty)'}`);
}

function parseCliArgs(argv) {
  const parsed = {
    tag: 'text_ai',
    promptFile: '',
    fallbackMode: 'claude,opencode',
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case '--tag':
        parsed.tag = argv[i + 1] || parsed.tag;
        i += 1;
        break;
      case '--prompt-file':
        parsed.promptFile = argv[i + 1] || '';
        i += 1;
        break;
      case '--fallbacks':
        parsed.fallbackMode = argv[i + 1] || parsed.fallbackMode;
        i += 1;
        break;
      default:
        break;
    }
  }
  return parsed;
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  if (!args.promptFile) {
    console.error('Usage: node text_ai.mjs --tag <tag> --prompt-file <path> [--fallbacks claude|claude,opencode]');
    process.exit(2);
  }
  const promptText = readFileSync(args.promptFile, 'utf-8');
  const includeOpencodeFallback = String(args.fallbackMode || '').split(',').includes('opencode');
  try {
    const result = await generateTextWithFallbacks(args.tag, promptText, {
      fallbackMode: args.fallbackMode,
      includeOpencodeFallback,
      parseOutput: extractPlainText,
    });
    process.stdout.write(String(result || ''));
  } catch (err) {
    console.error(err?.message || String(err));
    process.exit(1);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  await main();
}
