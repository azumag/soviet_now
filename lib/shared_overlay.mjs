// Shared notification/status surface for game lifecycle transitions.
//
// This module deliberately has no game launcher.  It owns only a loopback HTTP
// surface and the small browser-side blank stage on which the existing direct
// overlay implementation can render.

import fs from 'node:fs';
import http from 'node:http';

import {
  directOverlayIdleHtml,
  directOverlaySurfaceVisible,
  installDirectOverlay,
  loadDirectOverlayConfig,
  stripOverlaySelfRefresh,
} from './direct_overlay.mjs';
import {
  DIRECT_BROADCAST_STATE_ROUTE,
  buildDirectBroadcastOverlayState,
} from './direct_broadcast_overlay.mjs';


export const SHARED_OVERLAY_DEFAULT_PORT = 8092;
export const SHARED_OVERLAY_DEFAULT_HOST = '127.0.0.1';
export const SHARED_OVERLAY_HEALTH_ROUTE = '/healthz';
export const SHARED_OVERLAY_STAGE_ELEMENT_ID = 'soren-direct-stream-stage';
export const SHARED_OVERLAY_DEFAULT_CONTEXT_FILE = '/home/ubuntu/docich/run-soren-live/game_switch.json';
export const SHARED_OVERLAY_SOREN_GAME = 'sorengame';
export const SHARED_OVERLAY_ROUTES = Object.freeze({
  root: '/',
  health: SHARED_OVERLAY_HEALTH_ROUTE,
  broadcastState: DIRECT_BROADCAST_STATE_ROUTE,
});

export const SHARED_OVERLAY_LAYOUT = Object.freeze({
  width: 1280,
  height: 720,
  game: Object.freeze({ left: 0, top: 90, width: 960, height: 540 }),
  sidebar: Object.freeze({ left: 960, top: 0, width: 320, height: 720 }),
  topRail: Object.freeze({ left: 0, top: 0, width: 960, height: 90 }),
  bottomRail: Object.freeze({ left: 0, top: 630, width: 960, height: 90 }),
});


function rounded(value) {
  const number = Number(value);
  return Number.isFinite(number) && Math.round(number) === number ? number : null;
}


/**
 * Validate the browser's real visible window, not only its CSS viewport.
 *
 * Headed Chromium can report an inner 1280x720 viewport while a toolbar and
 * window-manager frame make the actual X11 window 1288x805.  That window
 * spills below a 1280x720 display even though DOM layout checks pass.  The
 * shared service must not publish ready until the outer window is exactly the
 * stream canvas, positioned at the screen origin, and fully contained by the
 * reported screen.
 */
export function sharedOverlayViewportReady(viewport) {
  if (!viewport || typeof viewport !== 'object') return false;
  const exact = (value, expected) => rounded(value) === expected;
  const innerReady = exact(viewport.innerWidth, SHARED_OVERLAY_LAYOUT.width)
    && exact(viewport.innerHeight, SHARED_OVERLAY_LAYOUT.height);
  const outerReady = exact(viewport.outerWidth, SHARED_OVERLAY_LAYOUT.width)
    && exact(viewport.outerHeight, SHARED_OVERLAY_LAYOUT.height);
  const screenX = rounded(viewport.screenX);
  const screenY = rounded(viewport.screenY);
  const screenWidth = rounded(viewport.screenWidth);
  const screenHeight = rounded(viewport.screenHeight);
  const screenReady = screenX === 0
    && screenY === 0
    && screenWidth !== null
    && screenHeight !== null
    && screenWidth >= SHARED_OVERLAY_LAYOUT.width
    && screenHeight >= SHARED_OVERLAY_LAYOUT.height
    && screenX + SHARED_OVERLAY_LAYOUT.width <= screenWidth
    && screenY + SHARED_OVERLAY_LAYOUT.height <= screenHeight;
  const stage = viewport.stage;
  const stageReady = stage && exact(stage.left, 0) && exact(stage.top, 0)
    && exact(stage.width, SHARED_OVERLAY_LAYOUT.width)
    && exact(stage.height, SHARED_OVERLAY_LAYOUT.height);
  return Boolean(innerReady && outerReady && screenReady && stageReady);
}


function envValue(env, names, fallback = '') {
  for (const name of names) {
    if (Object.prototype.hasOwnProperty.call(env || {}, name)
        && String(env[name] ?? '').trim() !== '') {
      return String(env[name]).trim();
    }
  }
  return fallback;
}


function enabledValue(raw, fallback = false) {
  const value = String(raw ?? (fallback ? '1' : '0')).trim().toLowerCase();
  return !['0', 'false', 'no', 'off'].includes(value);
}


function integerValue(raw, fallback, { min = 1, max = 65535 } = {}) {
  const value = Number.parseInt(String(raw ?? ''), 10);
  if (!Number.isSafeInteger(value) || value < min || value > max) return fallback;
  return value;
}


function loopbackHost(raw) {
  const value = String(raw || SHARED_OVERLAY_DEFAULT_HOST).trim().toLowerCase();
  return ['127.0.0.1', 'localhost', '::1'].includes(value)
    ? (value === 'localhost' ? '127.0.0.1' : value)
    : SHARED_OVERLAY_DEFAULT_HOST;
}


function directEnvironment(env) {
  const result = { ...(env || {}) };
  // The shared service is the FFmpeg dashboard companion.  Supplying these
  // defaults lets it run independently of the Soren game process.  The
  // shared service owns a required display contract, so values inherited
  // from a game-specific environment cannot disable it or select the legacy
  // fullscreen path.
  result.SOREN_STREAM_BACKEND = 'ffmpeg';
  result.SOREN_DIRECT_OVERLAY_ENABLED = '1';
  // The independent surface has one contract, regardless of a caller's
  // Soren-game sizing overrides.  Keeping custom values here would make the
  // direct overlay iframe styles use a different sidebar/rail geometry than
  // the blank stage below.
  result.SOREN_DIRECT_STAGE_LAYOUT = 'dashboard';
  result.SOREN_DIRECT_STREAM_SIZE = '1280x720';
  result.SOREN_DIRECT_GAME_DISPLAY_SIZE = '960x540';
  result.SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED = '1';
  return result;
}


function contextFileFromEnv(env) {
  return envValue(env, [
    'SOREN_ACTIVE_GAME_CONTEXT_FILE',
    'SOREN_GAME_CONTEXT_FILE',
    'SOREN_ACTIVE_GAME_FILE',
    'SOREN_GAME_STATE_CONTEXT_FILE',
    'SOREN_GAME_SWITCH_CONTEXT_FILE',
    'ACTIVE_GAME_CONTEXT_FILE',
    'ACTIVE_GAME_FILE',
    'GAME_CONTEXT_FILE',
    'SOREN_GAME_SWITCH_FILE',
    'GAME_SWITCH_FILE',
  ], SHARED_OVERLAY_DEFAULT_CONTEXT_FILE);
}


function sourceUpdatedAt(file) {
  try {
    return Math.floor(fs.statSync(file).mtimeMs / 1000);
  } catch {
    return 0;
  }
}


function readJsonObject(file) {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
  } catch {
    // An absent or partly-written lifecycle file is a safe waiting state.
  }
  return {};
}


function safeText(value, fallback = '') {
  const text = String(value ?? '')
    .replace(/[\u0000-\u001f\u007f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return text.slice(0, 80) || fallback;
}


function objectValue(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}


/**
 * Normalize the lifecycle context without passing through unrelated fields.
 * The canonical shape is { active: { game, phase }, phase }, but a small
 * amount of backwards-compatible tolerance keeps the service useful for
 * hand-written test/context files.
 */
export function normalizeActiveGameContext(raw, updatedAt = 0) {
  const document = objectValue(raw) || {};
  const active = document.active;
  const activeObject = objectValue(active);
  const explicitInactive = active === false || active === null;
  const gameRaw = activeObject?.game
    ?? (typeof active === 'string' ? active : undefined);
  const phaseRaw = activeObject?.phase ?? document.phase;
  const game = safeText(gameRaw);
  const phase = safeText(phaseRaw, game ? 'active' : 'waiting').toLowerCase();
  const activeGame = Boolean(game) && !explicitInactive;
  return {
    active: activeGame,
    game: activeGame ? game : '',
    phase: activeGame ? phase : 'waiting',
    updatedAt: Number.isSafeInteger(Number(updatedAt)) ? Number(updatedAt) : 0,
  };
}


/** Read only the active game and phase from the lifecycle context file. */
export function loadActiveGameContext(file = SHARED_OVERLAY_DEFAULT_CONTEXT_FILE) {
  const updatedAt = sourceUpdatedAt(file);
  return normalizeActiveGameContext(readJsonObject(file), updatedAt);
}


function directConfigFor(config) {
  return config?.direct || config?.overlay || config || {};
}


const SHARED_OVERLAY_LEGACY_SURFACE_KEYS = new Set([
  'event',
  'stats',
  'ops',
  'improve',
]);
const SHARED_OVERLAY_REQUIRED_SURFACE_METADATA = Object.freeze({
  broadcastSidebar: Object.freeze({
    route: '/__soren_overlay/broadcast/sidebar',
    elementId: 'soren-direct-stream-overlay-broadcastSidebar',
    region: 'sidebar',
    style: Object.freeze({
      left: '960px', top: '0', width: '320px', height: '720px', zIndex: '2147483630',
    }),
  }),
  broadcastTop: Object.freeze({
    route: '/__soren_overlay/broadcast/top',
    elementId: 'soren-direct-stream-overlay-broadcastTop',
    region: 'top',
    style: Object.freeze({
      left: '0', top: '0', width: '960px', height: '90px', zIndex: '2147483630',
    }),
  }),
  broadcastBottom: Object.freeze({
    route: '/__soren_overlay/broadcast/bottom',
    elementId: 'soren-direct-stream-overlay-broadcastBottom',
    region: 'bottom',
    style: Object.freeze({
      left: '0', top: '630px', width: '960px', height: '90px', zIndex: '2147483630',
    }),
  }),
});
const SHARED_OVERLAY_OPTIONAL_SURFACE_ROUTES = new Set([
  '/__soren_overlay/twica',
  '/__soren_overlay/wildcard',
  '/__soren_overlay/av-sync',
]);


/**
 * Reject a caller-supplied direct config that could fall back to the
 * game-specific/fullscreen overlay.  The standalone service has one fixed
 * dashboard contract; a malformed config must fail closed instead of
 * serving Soren stats from a different game context.
 */
export function assertSharedOverlayContract(direct) {
  const value = direct || {};
  const stage = value.stage || {};
  const problems = [];
  if (value.backend !== 'ffmpeg') problems.push('backend');
  if (value.enabled !== true) problems.push('enabled');
  if (stage.enabled !== true || stage.mode !== 'dashboard') problems.push('stage');
  if (stage.elementId !== SHARED_OVERLAY_STAGE_ELEMENT_ID) problems.push('stage.elementId');
  const expectedStage = {
    outputWidth: SHARED_OVERLAY_LAYOUT.width,
    outputHeight: SHARED_OVERLAY_LAYOUT.height,
    gameLeft: SHARED_OVERLAY_LAYOUT.game.left,
    gameTop: SHARED_OVERLAY_LAYOUT.game.top,
    gameWidth: SHARED_OVERLAY_LAYOUT.game.width,
    gameHeight: SHARED_OVERLAY_LAYOUT.game.height,
    sidebarLeft: SHARED_OVERLAY_LAYOUT.sidebar.left,
    sidebarWidth: SHARED_OVERLAY_LAYOUT.sidebar.width,
    topRailHeight: SHARED_OVERLAY_LAYOUT.topRail.height,
    bottomRailTop: SHARED_OVERLAY_LAYOUT.bottomRail.top,
    bottomRailHeight: SHARED_OVERLAY_LAYOUT.bottomRail.height,
  };
  for (const [key, expected] of Object.entries(expectedStage)) {
    if (Number(stage[key]) !== expected) problems.push(`stage.${key}`);
  }
  if (!value.broadcast || value.broadcast.stateRoute !== DIRECT_BROADCAST_STATE_ROUTE) {
    problems.push('broadcast');
  }
  const surfaces = Array.isArray(value.surfaces) ? value.surfaces : [];
  if (new Set(surfaces.map((item) => item?.key)).size !== surfaces.length) {
    problems.push('surface-keys');
  }
  if (new Set(surfaces.map((item) => item?.route)).size !== surfaces.length) {
    problems.push('surface-routes-duplicate');
  }
  if (new Set(surfaces.map((item) => item?.elementId)).size !== surfaces.length) {
    problems.push('surface-elements-duplicate');
  }
  const surfaceKeys = new Set(surfaces.map((item) => item?.key));
  for (const [key, metadata] of Object.entries(SHARED_OVERLAY_REQUIRED_SURFACE_METADATA)) {
    const surface = surfaces.find((item) => item?.key === key);
    if (!surfaceKeys.has(key)
        || surface?.route !== metadata.route
        || surface?.elementId !== metadata.elementId
        || surface?.region !== metadata.region) {
      problems.push(`surface.${key}`);
      continue;
    }
    const style = surface.style && typeof surface.style === 'object' ? surface.style : {};
    const expectedStyleKeys = Object.keys(metadata.style).sort();
    const actualStyleKeys = Object.keys(style).sort();
    if (expectedStyleKeys.length !== actualStyleKeys.length
        || expectedStyleKeys.some((name, index) => name !== actualStyleKeys[index])
        || expectedStyleKeys.some((name) => style[name] !== metadata.style[name])) {
      problems.push(`surface.${key}.style`);
    }
  }
  if (surfaces.some((item) => SHARED_OVERLAY_LEGACY_SURFACE_KEYS.has(item?.key))) {
    problems.push('legacy-surfaces');
  }
  if (surfaces.some((surface) => !SHARED_OVERLAY_OPTIONAL_SURFACE_ROUTES.has(surface?.route)
      && !Object.values(SHARED_OVERLAY_REQUIRED_SURFACE_METADATA)
        .some((required) => required.route === surface?.route))) {
    problems.push('surface-routes');
  }
  if (problems.length) {
    throw new Error(`shared overlay direct contract mismatch: ${problems.join(', ')}`);
  }
  return value;
}


/**
 * Load the direct overlay configuration with service-safe dashboard defaults.
 * The direct overlay loader is still the source of truth for overlay files,
 * flags, routes, and the broadcast state source list.
 */
export function loadSharedOverlayConfig(env = process.env, platform = 'linux') {
  const sourceEnv = env || {};
  const directEnv = directEnvironment(sourceEnv);
  const direct = loadDirectOverlayConfig(directEnv, platform);
  assertSharedOverlayContract(direct);
  const port = integerValue(
    envValue(sourceEnv, [
      'SOREN_SHARED_OVERLAY_PORT',
      'SHARED_OVERLAY_PORT',
      'SOREN_OVERLAY_PORT',
      'OVERLAY_PORT',
    ], String(SHARED_OVERLAY_DEFAULT_PORT)),
    SHARED_OVERLAY_DEFAULT_PORT,
    { min: 1, max: 65535 },
  );
  const host = loopbackHost(envValue(sourceEnv, ['SOREN_SHARED_OVERLAY_HOST', 'SHARED_OVERLAY_HOST']));
  const contextFile = contextFileFromEnv(sourceEnv);
  return {
    kind: 'shared-overlay',
    host,
    port,
    contextFile,
    layout: SHARED_OVERLAY_LAYOUT,
    direct,
    enabled: Boolean(direct.enabled),
  };
}


function genericStatsFeed(context, nowSeconds) {
  const game = context.active ? context.game : 'WAITING';
  const phase = context.active ? context.phase : 'waiting';
  const text = [
    `GAME: ${game}`,
    `Phase: ${phase}`,
    'SOREN STATUS: INACTIVE',
  ].join('\n');
  return {
    label: 'GAME',
    text,
    segments: text.split('\n').map((line) => [{ t: line }]),
    available: true,
    updatedAt: context.updatedAt || nowSeconds,
    lineCount: 3,
    generic: true,
    game: context.active ? context.game : null,
  };
}


function inactiveImproveFeed(context, nowSeconds) {
  const detail = context.active
    ? `Soren improve inactive for ${context.game}`
    : 'Soren improve inactive while waiting for an active game';
  return {
    active: false,
    status: 'inactive',
    phase: 'inactive',
    detail,
    progress: 0,
    pid: 0,
    startedAt: 0,
    updatedAt: context.updatedAt || nowSeconds,
    logUpdatedAt: 0,
    logLines: [],
    lineCount: 0,
    available: false,
    sourceUpdatedAt: context.updatedAt || 0,
    generic: true,
    game: context.active ? context.game : null,
  };
}


function isSorenContext(context) {
  return context.active && context.game.toLowerCase() === SHARED_OVERLAY_SOREN_GAME;
}


/**
 * Build the existing broadcast payload and mask Soren-specific feeds whenever
 * lifecycle ownership belongs to another game (or is currently waiting).
 * Notification and common OPS feeds intentionally remain untouched.
 */
export function buildSharedBroadcastOverlayState(config, nowMs = Date.now()) {
  const shared = config || {};
  const direct = assertSharedOverlayContract(directConfigFor(shared));
  const nowSeconds = Math.floor(Number(nowMs) / 1000);
  const contextFile = shared.contextFile || SHARED_OVERLAY_DEFAULT_CONTEXT_FILE;
  const context = loadActiveGameContext(contextFile);
  const state = buildDirectBroadcastOverlayState(direct.broadcast || direct, nowMs);
  const withContext = {
    ...state,
    gameContext: {
      active: context.active,
      game: context.active ? context.game : null,
      phase: context.phase,
      updatedAt: context.updatedAt,
    },
  };
  if (isSorenContext(context)) return withContext;
  return {
    ...withContext,
    feeds: {
      ...state.feeds,
      showStatusG: genericStatsFeed(context, nowSeconds),
      improve: inactiveImproveFeed(context, nowSeconds),
    },
  };
}


// Keep a short alias for callers that treat the payload as the shared state.
export const buildSharedOverlayState = buildSharedBroadcastOverlayState;


function stageGeometry(config) {
  const stage = directConfigFor(config).stage;
  if (stage?.enabled
      && Number(stage.outputWidth) === SHARED_OVERLAY_LAYOUT.width
      && Number(stage.outputHeight) === SHARED_OVERLAY_LAYOUT.height
      && Number(stage.gameLeft) === SHARED_OVERLAY_LAYOUT.game.left
      && Number(stage.gameTop) === SHARED_OVERLAY_LAYOUT.game.top
      && Number(stage.gameWidth) === SHARED_OVERLAY_LAYOUT.game.width
      && Number(stage.gameHeight) === SHARED_OVERLAY_LAYOUT.game.height) {
    return stage;
  }
  return {
    enabled: true,
    mode: 'dashboard',
    elementId: SHARED_OVERLAY_STAGE_ELEMENT_ID,
    outputWidth: SHARED_OVERLAY_LAYOUT.width,
    outputHeight: SHARED_OVERLAY_LAYOUT.height,
    gameLeft: SHARED_OVERLAY_LAYOUT.game.left,
    gameTop: SHARED_OVERLAY_LAYOUT.game.top,
    gameWidth: SHARED_OVERLAY_LAYOUT.game.width,
    gameHeight: SHARED_OVERLAY_LAYOUT.game.height,
    sidebarLeft: SHARED_OVERLAY_LAYOUT.sidebar.left,
    sidebarWidth: SHARED_OVERLAY_LAYOUT.sidebar.width,
    topRailHeight: SHARED_OVERLAY_LAYOUT.topRail.height,
    bottomRailTop: SHARED_OVERLAY_LAYOUT.bottomRail.top,
    bottomRailHeight: SHARED_OVERLAY_LAYOUT.bottomRail.height,
  };
}


/**
 * Install only the shared stage background.  Unlike installDirectGameStage,
 * this never looks for a game container or canvas and therefore cannot start
 * or mutate a Soren game.
 */
export async function installBlankDirectGameStage(page, config = {}) {
  const stage = stageGeometry(config);
  return page.evaluate(({ stage }) => {
    document.body.style.margin = '0';
    document.body.style.padding = '0';
    document.body.style.overflow = 'hidden';
    document.body.style.background = '#050914';
    document.documentElement.style.margin = '0';
    document.documentElement.style.background = '#050914';

    document.getElementById(stage.elementId)?.remove();
    const stageElement = document.createElement('div');
    stageElement.id = stage.elementId;
    stageElement.setAttribute('aria-hidden', 'true');
    Object.assign(stageElement.style, {
      position: 'fixed', left: '0', top: '0',
      width: `${stage.outputWidth}px`, height: `${stage.outputHeight}px`,
      overflow: 'hidden', pointerEvents: 'none', zIndex: '0',
      background: '#050914',
    });
    const panel = (style) => {
      const element = document.createElement('div');
      Object.assign(element.style, {
        position: 'absolute', boxSizing: 'border-box', pointerEvents: 'none',
        ...style,
      });
      stageElement.appendChild(element);
    };
    // Explicitly paint the game rectangle so the service remains visibly
    // blank while keeping the exact shared game coordinates.
    panel({
      left: `${stage.gameLeft}px`, top: `${stage.gameTop}px`,
      width: `${stage.gameWidth}px`, height: `${stage.gameHeight}px`,
      background: '#000',
    });
    panel({
      left: `${stage.sidebarLeft}px`, top: '0',
      width: `${stage.sidebarWidth}px`, height: `${stage.outputHeight}px`,
      background: 'linear-gradient(180deg, #07111f 0%, #030914 100%)',
      borderLeft: '1px solid rgba(56,189,248,.28)',
    });
    panel({
      left: '0', top: '0', width: `${stage.gameWidth}px`, height: `${stage.topRailHeight}px`,
      background: 'linear-gradient(180deg, #07111f 0%, #050b15 100%)',
      borderBottom: '1px solid rgba(56,189,248,.22)',
    });
    panel({
      left: '0', top: `${stage.bottomRailTop}px`,
      width: `${stage.gameWidth}px`, height: `${stage.bottomRailHeight}px`,
      background: 'linear-gradient(180deg, #050b15 0%, #07111f 100%)',
      borderTop: '1px solid rgba(56,189,248,.22)',
    });
    document.body.appendChild(stageElement);
    return {
      stageMode: 'blank',
      gameLeft: stage.gameLeft,
      gameTop: stage.gameTop,
      gameWidth: stage.gameWidth,
      gameHeight: stage.gameHeight,
      sidebarLeft: stage.sidebarLeft,
      sidebarWidth: stage.sidebarWidth,
      outputWidth: stage.outputWidth,
      outputHeight: stage.outputHeight,
    };
  }, { stage });
}


export async function installSharedOverlay(page, config) {
  const shared = config || loadSharedOverlayConfig();
  const direct = assertSharedOverlayContract(directConfigFor(shared));
  const stage = await installBlankDirectGameStage(page, shared);
  const overlaysInstalled = await installDirectOverlay(page, direct);
  return { stage, overlaysInstalled };
}


/**
 * Wait for the three fixed broadcast frames to load and render their state.
 * installDirectOverlay intentionally installs asynchronous pollers and returns
 * immediately; the standalone service must not advertise ready until the
 * first HTML/state response and the canonical frame geometry are observable.
 */
export async function waitForSharedOverlayFrames(page, config, options = {}) {
  const direct = assertSharedOverlayContract(directConfigFor(config));
  const frameGeometry = {
    sidebar: SHARED_OVERLAY_LAYOUT.sidebar,
    top: SHARED_OVERLAY_LAYOUT.topRail,
    bottom: SHARED_OVERLAY_LAYOUT.bottomRail,
  };
  const frames = Object.entries(SHARED_OVERLAY_REQUIRED_SURFACE_METADATA).map(([key, metadata]) => ({
    key,
    elementId: metadata.elementId,
    region: metadata.region,
    ...frameGeometry[metadata.region],
  }));
  // Keep the reference in this readiness helper even though the contract
  // assertion above already validates the required surfaces.  This prevents a
  // future caller from accidentally checking a different config's frames.
  if (!direct.surfaces.some((item) => item?.key === 'broadcastSidebar')) {
    throw new Error('shared overlay required broadcast frames unavailable');
  }
  const timeoutRaw = Number(options.timeoutMs ?? 10000);
  const timeoutMs = Number.isSafeInteger(timeoutRaw) && timeoutRaw > 0 ? timeoutRaw : 10000;
  const check = ({ frames: expectedFrames }) => {
    const equalArray = (actual, expected) => Array.isArray(actual)
      && actual.length === expected.length
      && actual.every((value, index) => Number(value) === expected[index]);
    for (const expected of expectedFrames) {
      // direct_overlay keeps two frames per surface and swaps the loaded
      // buffer into view.  During the first refresh the canonical elementId
      // is still the hidden idle frame, while `${elementId}-buffer` owns the
      // rendered HTML.  Readiness must follow the visible active frame, not
      // assume that the first frame remains the active one.
      const candidates = [
        document.getElementById(expected.elementId),
        document.getElementById(`${expected.elementId}-buffer`),
      ].filter((frame) => frame && frame.dataset.sorenOverlayRegion === expected.region);
      const visible = candidates.filter((frame) => {
        const visibility = String(frame.style?.visibility || '').toLowerCase();
        const opacity = Number.parseFloat(frame.style?.opacity || '1');
        return visibility !== 'hidden' && (!Number.isFinite(opacity) || opacity > 0);
      });
      if (visible.length !== 1) return false;
      const frame = visible[0];
      try {
        const outer = frame.getBoundingClientRect();
        if (Math.round(outer.left) !== expected.left
            || Math.round(outer.top) !== expected.top
            || Math.round(outer.width) !== expected.width
            || Math.round(outer.height) !== expected.height) return false;
        const frameDocument = frame.contentDocument;
        if (!frameDocument || frameDocument.readyState !== 'complete') return false;
        const overlay = frameDocument.getElementById('broadcast-overlay');
        if (!overlay) return false;
        const inner = overlay.getBoundingClientRect();
        const innerWidth = expected.region === 'sidebar' ? 320 : 960;
        const innerHeight = expected.region === 'sidebar' ? 720 : 90;
        if (Math.round(inner.left) !== 0
            || Math.round(inner.top) !== 0
            || Math.round(inner.width) !== innerWidth
            || Math.round(inner.height) !== innerHeight) return false;
        const health = frame.contentWindow?.__sorenBroadcastOverlayHealth;
        if (!health || health.version !== 3 || Number(health.updatedAt) <= 0
            || health.error || health.merged !== true || health.region !== expected.region) return false;
        if (!equalArray(health.layout?.game, [0, 90, 960, 540])
            || !equalArray(health.layout?.sidebar, [960, 0, 320, 720])
            || !equalArray(health.layout?.rails, [90, 90])) return false;
      } catch {
        return false;
      }
    }
    return true;
  };
  const payload = { frames };
  if (typeof page.waitForFunction === 'function') {
    await page.waitForFunction(check, payload, { timeout: timeoutMs });
  } else {
    const ready = await page.evaluate(check, payload);
    if (ready !== true) throw new Error('shared overlay required frames not ready');
  }
  // Health is published after the first render; wait two frames so the
  // browser compositor has observed that DOM before ready is announced.
  await page.evaluate(() => new Promise((resolve) => {
    if (typeof requestAnimationFrame !== 'function') {
      resolve();
      return;
    }
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  return { ready: true, frames };
}


function noCacheHeaders(contentType) {
  return {
    'Content-Type': contentType,
    'Cache-Control': 'no-store, no-cache, must-revalidate',
    Pragma: 'no-cache',
    Expires: '0',
    'X-Content-Type-Options': 'nosniff',
  };
}


function jsonResponse(res, status, body, headOnly = false) {
  const payload = JSON.stringify(body);
  res.writeHead(status, noCacheHeaders('application/json; charset=utf-8'));
  if (!headOnly) res.end(payload);
  else res.end();
}


function htmlResponse(res, status, html, headOnly = false) {
  res.writeHead(status, noCacheHeaders('text/html; charset=utf-8'));
  if (!headOnly) res.end(html);
  else res.end();
}


export function blankSharedOverlayHtml() {
  return '<!doctype html><html><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=1280,height=720,initial-scale=1">'
    + '<title>Shared overlay</title>'
    + '<style>html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#050914}</style>'
    + '</head><body></body></html>';
}


function routeTable(config) {
  const direct = assertSharedOverlayContract(directConfigFor(config));
  const surfaces = direct.enabled && Array.isArray(direct.surfaces) ? direct.surfaces : [];
  const surfaceByRoute = new Map(
    surfaces.filter((item) => item && typeof item.route === 'string' && item.route.startsWith('/'))
      .map((item) => [item.route, item]),
  );
  const stateRoute = direct.enabled && direct.broadcast?.stateRoute
    ? direct.broadcast.stateRoute
    : '';
  return { direct, surfaceByRoute, stateRoute };
}


export function sharedOverlayAllowedRoutes(config) {
  const { surfaceByRoute, stateRoute } = routeTable(config);
  return [
    SHARED_OVERLAY_HEALTH_ROUTE,
    '/',
    ...(stateRoute ? [stateRoute] : []),
    ...surfaceByRoute.keys(),
  ];
}


function serverAddressPort(server, fallback) {
  const address = server?.address?.();
  return address && typeof address === 'object' && Number.isSafeInteger(address.port)
    ? address.port
    : fallback;
}


function requestPath(req) {
  try {
    return new URL(req.url || '/', 'http://127.0.0.1').pathname;
  } catch {
    return null;
  }
}


function healthPayload(config, server) {
  const context = loadActiveGameContext(config.contextFile);
  const runtime = server?.sharedOverlayRuntime || {};
  return {
    ok: true,
    service: 'shared-overlay',
    // HTTP liveness and browser/layout readiness are intentionally separate:
    // the endpoint becomes available before Chromium has painted the stage.
    ready: Boolean(runtime.browserReady && runtime.windowReady
      && runtime.layoutReady && runtime.overlayReady),
    browserReady: Boolean(runtime.browserReady),
    windowReady: Boolean(runtime.windowReady),
    layoutReady: Boolean(runtime.layoutReady),
    overlayReady: Boolean(runtime.overlayReady),
    port: serverAddressPort(server, config.port),
    layout: {
      width: SHARED_OVERLAY_LAYOUT.width,
      height: SHARED_OVERLAY_LAYOUT.height,
      game: [0, 90, 960, 540],
      sidebar: [960, 0, 320, 720],
      rails: [90, 90],
    },
    gameContext: {
      active: context.active,
      game: context.active ? context.game : null,
      phase: context.phase,
      updatedAt: context.updatedAt,
    },
    ...(runtime.viewport ? { viewport: runtime.viewport } : {}),
  };
}


function handleRequest(req, res, config, server) {
  const headOnly = req.method === 'HEAD';
  if (req.method !== 'GET' && !headOnly) {
    res.writeHead(405, { Allow: 'GET, HEAD', 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Method Not Allowed');
    return;
  }
  const pathname = requestPath(req);
  if (!pathname) {
    res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Bad Request');
    return;
  }
  const { direct, surfaceByRoute, stateRoute } = routeTable(config);
  if (pathname === SHARED_OVERLAY_HEALTH_ROUTE) {
    jsonResponse(res, 200, healthPayload(config, server), headOnly);
    return;
  }
  if (pathname === '/') {
    htmlResponse(res, 200, blankSharedOverlayHtml(), headOnly);
    return;
  }
  if (stateRoute && pathname === stateRoute) {
    try {
      jsonResponse(res, 200, buildSharedBroadcastOverlayState(config), headOnly);
    } catch {
      jsonResponse(res, 503, { version: 1, error: 'broadcast overlay state unavailable' }, headOnly);
    }
    return;
  }
  const surface = surfaceByRoute.get(pathname);
  if (surface) {
    if (!directOverlaySurfaceVisible(surface) || !surface.htmlFile) {
      htmlResponse(res, 200, directOverlayIdleHtml(), headOnly);
      return;
    }
    try {
      const html = stripOverlaySelfRefresh(fs.readFileSync(surface.htmlFile, 'utf8'));
      htmlResponse(res, 200, html, headOnly);
    } catch {
      htmlResponse(res, 200, directOverlayIdleHtml(), headOnly);
    }
    return;
  }
  // Do not fall through to a static file server.  The shared service has no
  // game/build route and never exposes arbitrary files, env, or source paths.
  res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end('Not Found');
}


export function createSharedOverlayServer(config = loadSharedOverlayConfig(), options = {}) {
  assertSharedOverlayContract(directConfigFor(config));
  const resolved = {
    ...config,
    host: loopbackHost(options.host || config.host),
    // Port zero is useful for isolated unit tests and still binds loopback;
    // the service default remains 8092 when no override is supplied.
    port: integerValue(options.port ?? config.port, SHARED_OVERLAY_DEFAULT_PORT, { min: 0 }),
  };
  const server = http.createServer((req, res) => handleRequest(req, res, resolved, server));
  server.sharedOverlayConfig = resolved;
  server.sharedOverlayRuntime = {
    browserReady: false,
    windowReady: false,
    layoutReady: false,
    overlayReady: false,
    viewport: null,
  };
  return server;
}


export async function startSharedOverlayServer(config = loadSharedOverlayConfig(), options = {}) {
  const server = createSharedOverlayServer(config, options);
  await new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off('listening', onListening);
      reject(error);
    };
    const onListening = () => {
      server.off('error', onError);
      resolve();
    };
    server.once('error', onError);
    server.once('listening', onListening);
    server.listen(server.sharedOverlayConfig.port, server.sharedOverlayConfig.host);
  });
  return server;
}


export function closeSharedOverlayServer(server) {
  if (!server || !server.listening) return Promise.resolve();
  if (server.sharedOverlayRuntime) {
    server.sharedOverlayRuntime.browserReady = false;
    server.sharedOverlayRuntime.windowReady = false;
    server.sharedOverlayRuntime.layoutReady = false;
    server.sharedOverlayRuntime.overlayReady = false;
  }
  // Tests and short-lived rehearsal runs may leave keep-alive connections;
  // this is our own server, so closing them is safe and keeps SIGTERM bounded.
  try { server.closeAllConnections?.(); } catch {}
  return new Promise((resolve) => {
    server.close(() => resolve());
  });
}


export function setSharedOverlayBrowserReady(server, ready, viewport = null) {
  if (!server?.sharedOverlayRuntime) return false;
  const browserReady = Boolean(ready);
  server.sharedOverlayRuntime.browserReady = browserReady;
  // A failed browser/window readiness update must clear every derived ready
  // flag before retaining optional geometry diagnostics.  Otherwise a
  // headless rehearsal can leave windowReady/layoutReady true merely because
  // its inner viewport happens to match the dashboard dimensions.
  if (!browserReady) {
    server.sharedOverlayRuntime.windowReady = false;
    server.sharedOverlayRuntime.layoutReady = false;
    server.sharedOverlayRuntime.overlayReady = false;
    if (viewport && typeof viewport === 'object') {
      server.sharedOverlayRuntime.viewport = viewport;
    } else {
      server.sharedOverlayRuntime.viewport = null;
    }
    return true;
  }
  if (viewport && typeof viewport === 'object') {
    server.sharedOverlayRuntime.viewport = viewport;
    const visible = sharedOverlayViewportReady(viewport);
    server.sharedOverlayRuntime.windowReady = visible;
    server.sharedOverlayRuntime.layoutReady = visible;
    if (!visible) server.sharedOverlayRuntime.overlayReady = false;
  } else {
    server.sharedOverlayRuntime.viewport = null;
    server.sharedOverlayRuntime.windowReady = false;
    server.sharedOverlayRuntime.layoutReady = false;
    server.sharedOverlayRuntime.overlayReady = false;
  }
  return true;
}


export function setSharedOverlayFramesReady(server, ready) {
  if (!server?.sharedOverlayRuntime) return false;
  server.sharedOverlayRuntime.overlayReady = Boolean(ready);
  return true;
}


/** Track every TCP connection accepted by an owned proxy, including upgraded
 * WebSocket sockets that Node's closeAllConnections() deliberately leaves
 * open.  The tracker is intentionally generic so it can be tested without an
 * upstream service or a live browser. */
export function trackOwnedServerSockets(server) {
  const sockets = new Set();
  const onConnection = (socket) => {
    if (!socket) return;
    sockets.add(socket);
    socket.once?.('close', () => sockets.delete(socket));
  };
  server?.on?.('connection', onConnection);
  return {
    sockets,
    close() {
      for (const socket of sockets) {
        try { socket.destroy?.(); } catch {}
      }
      sockets.clear();
    },
    dispose() {
      server?.off?.('connection', onConnection);
      this.close();
    },
  };
}


export {
  directOverlayIdleHtml,
  directOverlaySurfaceVisible,
  installDirectOverlay,
  loadDirectOverlayConfig,
};


// Naming aliases keep the small service usable from lifecycle callers without
// coupling them to whether the implementation is described as a server or a
// service.
export const createSharedOverlayService = createSharedOverlayServer;
export const startSharedOverlayService = startSharedOverlayServer;
export const installSharedOverlayStage = installBlankDirectGameStage;
export const getActiveGameContext = loadActiveGameContext;
export const IdleHtml = directOverlayIdleHtml;
