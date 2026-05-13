import 'dotenv/config';
import { execFile } from 'child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';

const RUNTIME_CONFIG_PATH = join(import.meta.dirname || '.', 'runtime_config.json');
const DEFAULT_GEMINI_MODEL = 'gemini-2.5-flash';
const DEFAULT_OPENCODE_AGENT = 'glmflash';
const DEFAULT_OLLAMA_BASE_URL = 'http://192.168.11.3:11434';
const DEFAULT_CLAUDE_TIMEOUT_MS = 30000;
const DEFAULT_GEMINI_TIMEOUT_MS = 30000;
const DEFAULT_OPENCODE_TIMEOUT_MS = 30000;
const DEFAULT_OPENCODE_PERMISSION = '{"*":"deny","read":"allow","glob":"allow","grep":"allow","list":"allow","web":"allow","web-search":"allow"}';

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
        || textConfig.opencodeTimeoutSec,
      DEFAULT_OPENCODE_TIMEOUT_MS,
    ),
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
      return { preset, model: 'haiku', env: {} };
    case 'sonnet':
      return { preset, model: 'sonnet', env: {} };
    case 'gemma4e':
      return { preset, model: 'gemma4:latest', env: ollamaEnv };
    case 'qwen35e':
      return { preset, model: 'qwen3.5:9b', env: ollamaEnv };
    default:
      return { preset, model: preset, env: {} };
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
  const timeoutMs = options.timeoutMs || config.claudeTimeoutMs;
  return new Promise((resolve, reject) => {
    const child = execFile('claude', [
      '-p',
      '--model',
      target.model,
      '--verbose',
    ], {
      encoding: 'utf-8',
      maxBuffer: 2 * 1024 * 1024,
      timeout: timeoutMs,
      cwd: '/tmp',
      env,
    }, (err, stdout, stderr) => {
      const stderrPreview = String(stderr || '').slice(0, 500);
      const combined = `${stdout || ''}\n${stderr || ''}`;
      if (containsClaudeLoginErrorText(combined)) {
        console.error(`[${tag}] claude unavailable: not logged in`);
      }
      if (containsProviderErrorText(combined)) {
        if (stderrPreview) console.error(`[${tag}] claude stderr:`, stderrPreview);
        return reject(makeProviderError(`claude provider/rate-limit failure (${target.preset})`, stderrPreview || String(stdout || '').slice(0, 300)));
      }
      if (err) {
        console.error(`[${tag}] claude error: code=${err.code} signal=${err.signal} killed=${err.killed} preset=${target.preset} stderr=${stderrPreview || '(empty)'} stdout_preview=${String(stdout || '').slice(0, 200)}`);
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

export function runOpencodeText(tag, promptText, options = {}) {
  const config = resolveTextAiConfig();
  const agent = options.opencodeAgent || config.opencodeAgent;
  const tempDir = mkdtempSync(join(tmpdir(), 'soren91_opencode_text_'));
  const promptFile = join(tempDir, 'prompt.txt');
  const rawFile = join(tempDir, 'raw.txt');
  writeFileSync(promptFile, promptText, 'utf-8');

  return new Promise((resolve, reject) => {
    const command = `LC_ALL=en_US.UTF-8 opencode run --agent ${shellSingleQuote(agent)} "$(cat ${shellSingleQuote(promptFile)})" 2>&1`;
    execFile('script', ['-q', rawFile, 'bash', '-lc', command], {
      encoding: 'utf-8',
      timeout: options.timeoutMs || config.opencodeTimeoutMs,
      env: {
        ...process.env,
        OPENCODE_PERMISSION: options.opencodePermission || config.opencodePermission,
        ...(options.extraEnv || {}),
      },
      maxBuffer: 2 * 1024 * 1024,
    }, (err) => {
      try {
        const raw = existsSync(rawFile) ? readFileSync(rawFile, 'utf-8') : '';
        const cleaned = cleanOpencodeOutput(raw);
        if (containsProviderErrorText(cleaned)) {
          return reject(makeProviderError(`opencode provider failure (${agent})`, cleaned.slice(0, 300)));
        }
        if (err) {
          if (cleaned) console.error(`[${tag}] opencode raw:`, cleaned.slice(0, 500));
          return reject(err);
        }
        try {
          resolve(parseOutputOrThrow(cleaned, options.parseOutput));
        } catch (parseErr) {
          reject(parseErr);
        }
      } finally {
        try { unlinkSync(promptFile); } catch {}
        try { unlinkSync(rawFile); } catch {}
        try { rmSync(tempDir, { recursive: true, force: true }); } catch {}
      }
    });
  });
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
        const claudeFallbackPreset = options.claudeFallbackPreset || 'haiku';
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
