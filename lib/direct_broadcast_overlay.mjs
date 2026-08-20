import fs from 'fs';


export const DIRECT_BROADCAST_STATE_ROUTE = '/__soren_overlay/broadcast/state';
export const DIRECT_BROADCAST_VERSION = 1;


function readUtf8(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return '';
  }
}


function readJson(file) {
  try {
    const value = JSON.parse(readUtf8(file));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}


function sourceUpdatedAt(file) {
  try {
    return Math.floor(fs.statSync(file).mtimeMs / 1000);
  } catch {
    return 0;
  }
}


function decodeHtmlEntities(value) {
  return String(value ?? '')
    .replace(/&#x([0-9a-f]+);/gi, (_, raw) => String.fromCodePoint(Number.parseInt(raw, 16)))
    .replace(/&#([0-9]+);/g, (_, raw) => String.fromCodePoint(Number.parseInt(raw, 10)))
    .replace(/&quot;/g, '"')
    .replace(/&#x27;|&#39;|&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}


export function extractLegacyOverlayText(documentHtml) {
  const match = String(documentHtml ?? '').match(/<pre\b[^>]*>([\s\S]*?)<\/pre>/i);
  if (!match) return '';
  return decodeHtmlEntities(
    match[1]
      .replace(/<br\s*\/?\s*>/gi, '\n')
      .replace(/<[^>]*>/g, ''),
  ).replace(/\r\n?/g, '\n').trimEnd();
}


function parseJsonConstant(documentHtml, name, fallback) {
  const document = String(documentHtml ?? '');
  const marker = new RegExp(`(?:^|\\n)\\s*const\\s+${name}\\s*=\\s*`, 'm').exec(document);
  if (!marker) return fallback;
  const start = marker.index + marker[0].length;
  let inString = false;
  let escaped = false;
  let depth = 0;
  let end = -1;
  for (let index = start; index < document.length; index += 1) {
    const char = document[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') inString = true;
    else if (char === '{' || char === '[') depth += 1;
    else if (char === '}' || char === ']') depth = Math.max(0, depth - 1);
    else if (char === ';' && depth === 0) {
      end = index;
      break;
    }
  }
  if (end < start) return fallback;
  try {
    return JSON.parse(document.slice(start, end));
  } catch {
    return fallback;
  }
}


function parseVisibleSeconds(documentHtml) {
  const match = String(documentHtml ?? '').match(/^\s*const\s+VISIBLE_SEC\s*=\s*(\d+)\s*;\s*$/m);
  if (!match) return 18;
  const value = Number(match[1]);
  return Number.isSafeInteger(value) && value >= 1 && value <= 3600 ? value : 18;
}


export function parseLegacyEventOverlayDocument(documentHtml) {
  const eventsRaw = parseJsonConstant(documentHtml, 'EVENTS', []);
  const workRaw = parseJsonConstant(documentHtml, 'WORK', {});
  const generatorsRaw = parseJsonConstant(documentHtml, 'GEN', []);
  return {
    events: Array.isArray(eventsRaw)
      ? eventsRaw.filter((item) => item && typeof item === 'object' && !Array.isArray(item)).slice(-18)
      : [],
    work: workRaw && typeof workRaw === 'object' && !Array.isArray(workRaw) ? workRaw : {},
    generators: Array.isArray(generatorsRaw)
      ? generatorsRaw.filter((item) => item && typeof item === 'object' && !Array.isArray(item)).slice(-4)
      : [],
    visibleSec: parseVisibleSeconds(documentHtml),
  };
}


function feed(label, file) {
  const text = extractLegacyOverlayText(readUtf8(file));
  return {
    label,
    text,
    available: text.length > 0,
    updatedAt: sourceUpdatedAt(file),
    lineCount: text ? text.split('\n').length : 0,
  };
}


const ANSI_RE = /\x1b\[[0-9;?]*[A-Za-z]/g;


function readLogTail(file, limit = 24) {
  const lines = readUtf8(file)
    .split('\n')
    .map((line) => line.replace(ANSI_RE, '').replace(/\r$/, ''))
    .filter((line) => line.length > 0)
    .slice(-limit);
  return lines;
}


function wildcardPhaseActive(state) {
  const phase = String(state?.phase || '').toLowerCase();
  return ['generating', 'running'].includes(phase);
}


function topOverrideState(file) {
  try {
    const raw = readUtf8(file);
    if (!raw.trim()) return null;
    const data = JSON.parse(raw);
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    if (data.enabled === false) return { enabled: false, lines: [], updatedAt: Number(data.updatedAt) || 0 };
    if (data.enabled === true && Array.isArray(data.lines)) {
      const lines = data.lines.map((s) => String(s).trim()).filter(Boolean).slice(0, 4);
      if (!lines.length) return null;
      return { enabled: true, lines, updatedAt: Number(data.updatedAt) || 0 };
    }
    return null;
  } catch {
    return null;
  }
}


function improveFeed(stateFile, logFile, wildcardStateFile) {
  const state = readJson(stateFile);
  const wildcard = readJson(wildcardStateFile);
  const status = String(state.status || 'idle').toLowerCase();
  const active = status === 'running' && !wildcardPhaseActive(wildcard);
  const logLines = readLogTail(logFile, 24);
  return {
    active,
    status,
    phase: String(state.phase || ''),
    detail: String(state.detail || ''),
    progress: Number(state.progress) || 0,
    pid: Number(state.pid) || 0,
    startedAt: Number(state.started_at) || 0,
    updatedAt: Number(state.updated_at) || 0,
    logUpdatedAt: sourceUpdatedAt(logFile),
    logLines,
    lineCount: logLines.length,
    available: active || logLines.length > 0,
    sourceUpdatedAt: sourceUpdatedAt(stateFile),
  };
}


export function buildDirectBroadcastOverlayState(config, nowMs = Date.now()) {
  const sources = config?.sources || {};
  const eventDocument = readUtf8(sources.eventHtmlFile);
  const notifications = parseLegacyEventOverlayDocument(eventDocument);
  const topOverride = topOverrideState(sources.topOverrideFile);
  return {
    version: DIRECT_BROADCAST_VERSION,
    updatedAt: Math.floor(Number(nowMs) / 1000),
    feeds: {
      showStatusG: feed('SHOW-STATUS-G', sources.statsHtmlFile),
      showStatus: feed('SHOW-STATUS', sources.opsHtmlFile),
      improve: improveFeed(
        sources.improveStateFile,
        sources.improveLogFile,
        sources.wildcardStateFile,
      ),
    },
    notifications: {
      ...notifications,
      sourceUpdatedAt: sourceUpdatedAt(sources.eventHtmlFile),
    },
    topOverride: topOverride ? { ...topOverride, sourceUpdatedAt: sourceUpdatedAt(sources.topOverrideFile) } : null,
  };
}
