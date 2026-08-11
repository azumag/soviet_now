import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { DIRECT_BROADCAST_STATE_ROUTE } from './direct_broadcast_overlay.mjs';


const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const DIRECT_OVERLAY_ROUTE = '/__soren_overlay/event';
export const DIRECT_OVERLAY_ELEMENT_ID = 'soren-direct-stream-overlay-event';
export const DIRECT_STAGE_ELEMENT_ID = 'soren-direct-stream-stage';
export const DIRECT_OVERLAY_ROUTES = Object.freeze({
  broadcastSidebar: '/__soren_overlay/broadcast/sidebar',
  broadcastTop: '/__soren_overlay/broadcast/top',
  broadcastBottom: '/__soren_overlay/broadcast/bottom',
  event: DIRECT_OVERLAY_ROUTE,
  stats: '/__soren_overlay/stats',
  ops: '/__soren_overlay/ops',
  improve: '/__soren_overlay/improve',
  wildcard: '/__soren_overlay/wildcard',
  avsync: '/__soren_overlay/av-sync',
});


export function directOverlayIdleHtml() {
  return '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent}</style>';
}


export function stripOverlaySelfRefresh(html) {
  return String(html ?? '')
    .replace(/<meta\b[^>]*http-equiv\s*=\s*["']?refresh["']?[^>]*>/gi, '')
    .replace(/<script>\s*setTimeout\(\(\)\s*=>\s*location[.]reload\(\)\s*,\s*1000\s*\)\s*;?\s*<\/script>/gi, '');
}


function enabledValue(raw, fallback = '1') {
  return !['0', 'false', 'no', 'off'].includes(String(raw ?? fallback).trim().toLowerCase());
}


function parseStageSize(raw, fallback, label) {
  const value = String(raw || fallback).trim().toLowerCase();
  const match = value.match(/^(\d{2,5})x(\d{2,5})$/);
  if (!match) throw new Error(`${label} must use WIDTHxHEIGHT`);
  const width = Number(match[1]);
  const height = Number(match[2]);
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height)
      || width < 320 || height < 180 || width > 3840 || height > 2160
      || width % 2 || height % 2) {
    throw new Error(`${label} must use supported even dimensions`);
  }
  return { width, height };
}


export function loadDirectStageConfig(env = process.env, platform = process.platform) {
  const backend = String(env.SOREN_STREAM_BACKEND || 'obs').trim().toLowerCase();
  const eligible = platform === 'linux'
    && backend === 'ffmpeg'
    && enabledValue(env.SOREN_DIRECT_OVERLAY_ENABLED);
  if (!eligible) return { enabled: false, mode: 'fullscreen', elementId: DIRECT_STAGE_ELEMENT_ID };

  const mode = String(env.SOREN_DIRECT_STAGE_LAYOUT || 'dashboard').trim().toLowerCase();
  if (!['dashboard', 'fullscreen'].includes(mode)) {
    throw new Error('SOREN_DIRECT_STAGE_LAYOUT must be dashboard or fullscreen');
  }
  if (mode === 'fullscreen') return { enabled: false, mode, elementId: DIRECT_STAGE_ELEMENT_ID };

  const output = parseStageSize(
    env.SOREN_DIRECT_STREAM_SIZE,
    '1280x720',
    'SOREN_DIRECT_STREAM_SIZE',
  );
  const game = parseStageSize(
    env.SOREN_DIRECT_GAME_DISPLAY_SIZE,
    '960x540',
    'SOREN_DIRECT_GAME_DISPLAY_SIZE',
  );
  if (game.width * 9 !== game.height * 16) {
    throw new Error('SOREN_DIRECT_GAME_DISPLAY_SIZE must use a 16:9 aspect ratio');
  }
  if (game.width >= output.width || game.height > output.height) {
    throw new Error('SOREN_DIRECT_GAME_DISPLAY_SIZE must leave room inside the stream output');
  }
  const sidebarWidth = output.width - game.width;
  if (sidebarWidth < 256) {
    throw new Error('SOREN_DIRECT_GAME_DISPLAY_SIZE must leave at least 256px for dashboard data');
  }
  const gameTop = Math.floor((output.height - game.height) / 2);
  return {
    enabled: true,
    mode,
    elementId: DIRECT_STAGE_ELEMENT_ID,
    outputWidth: output.width,
    outputHeight: output.height,
    gameLeft: 0,
    gameTop,
    gameWidth: game.width,
    gameHeight: game.height,
    sidebarLeft: game.width,
    sidebarWidth,
    topRailHeight: gameTop,
    bottomRailTop: gameTop + game.height,
    bottomRailHeight: output.height - gameTop - game.height,
  };
}


function sidebarSurfaceStyle(stage, naturalWidth, naturalHeight, placement, zIndex) {
  const gap = 8;
  const slotHeight = (stage.outputHeight - (gap * 3)) / 2;
  const scale = Math.min(
    (stage.sidebarWidth - (gap * 2)) / naturalWidth,
    slotHeight / naturalHeight,
  );
  const visualWidth = naturalWidth * scale;
  const visualHeight = naturalHeight * scale;
  const left = stage.sidebarLeft + ((stage.sidebarWidth - visualWidth) / 2);
  const top = placement === 'top' ? gap : stage.outputHeight - visualHeight - gap;
  return {
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    width: `${naturalWidth}px`,
    height: `${naturalHeight}px`,
    transform: `scale(${scale.toFixed(4)})`,
    transformOrigin: 'top left',
    zIndex,
  };
}


function resolveRepoFile(raw, fallback) {
  const value = String(raw || fallback).trim();
  return path.isAbsolute(value) ? value : path.resolve(REPO_ROOT, value);
}


function surface(key, file, options = {}) {
  return {
    key,
    route: DIRECT_OVERLAY_ROUTES[key],
    elementId: `soren-direct-stream-overlay-${key}`,
    title: options.title || `Soren ${key} overlay`,
    htmlFile: file,
    visibility: options.visibility || 'always',
    visibilityStateFile: options.visibilityStateFile || '',
    suppressStateFile: options.suppressStateFile || '',
    pollMs: options.pollMs ?? 1000,
    region: options.region || '',
    style: options.style || {},
  };
}


export function loadDirectOverlayConfig(env = process.env, platform = process.platform) {
  const backend = String(env.SOREN_STREAM_BACKEND || 'obs').trim().toLowerCase();
  const wildcardStateFile = resolveRepoFile(
    env.WILDCARD_PARALLEL_STATUS_FILE,
    'tmp/state/wildcard_parallel_status.json',
  );
  const globalEnabled = platform === 'linux'
    && backend === 'ffmpeg'
    && enabledValue(env.SOREN_DIRECT_OVERLAY_ENABLED);
  const stage = loadDirectStageConfig(env, platform);
  const eventHtmlFile = resolveRepoFile(
    env.SOREN_DIRECT_OVERLAY_HTML_FILE || env.EVENT_OVERLAY_HTML_FILE,
    'tmp/state/event_overlay.html',
  );
  const statsHtmlFile = resolveRepoFile(
    env.SOREN_DIRECT_STATS_OVERLAY_HTML_FILE || env.STATUS_OVERLAY_HTML_FILE,
    'tmp/state/status_overlay.html',
  );
  const opsHtmlFile = resolveRepoFile(
    env.SOREN_DIRECT_OPS_OVERLAY_HTML_FILE || env.SHOW_STATUS_OVERLAY_HTML_FILE,
    'tmp/state/show_status_overlay.html',
  );
  const broadcastHtmlFile = resolveRepoFile(
    env.SOREN_DIRECT_BROADCAST_OVERLAY_HTML_FILE,
    'overlays/direct_broadcast_overlay.html',
  );
  // Chromium/Xvfb can composite a transparent full-frame iframe as opaque black
  // above a WebGL canvas. Keep every broadcast surface outside the game rect.
  const broadcastSurfaces = [
    surface('broadcastSidebar', broadcastHtmlFile, {
      title: 'Soren direct broadcast sidebar',
      pollMs: 60000,
      region: 'sidebar',
      style: {
        left: `${stage.sidebarLeft || 960}px`, top: '0',
        width: `${stage.sidebarWidth || 320}px`,
        height: `${stage.outputHeight || 720}px`, zIndex: '2147483630',
      },
    }),
    surface('broadcastTop', broadcastHtmlFile, {
      title: 'Soren direct broadcast top rail',
      pollMs: 60000,
      region: 'top',
      style: {
        left: '0', top: '0', width: `${stage.gameWidth || 960}px`,
        height: `${stage.topRailHeight || 90}px`, zIndex: '2147483630',
      },
    }),
    surface('broadcastBottom', broadcastHtmlFile, {
      title: 'Soren direct broadcast bottom rail',
      pollMs: 60000,
      region: 'bottom',
      style: {
        left: '0', top: `${stage.bottomRailTop || 630}px`,
        width: `${stage.gameWidth || 960}px`,
        height: `${stage.bottomRailHeight || 90}px`, zIndex: '2147483630',
      },
    }),
  ];
  const definitions = [
    surface(
      'event',
      eventHtmlFile,
      {
        title: 'Soren stream event overlay',
        style: stage.enabled ? {
          left: '0', top: '0', width: `${stage.gameWidth}px`,
          height: `${stage.outputHeight}px`, zIndex: '2147483647',
        } : {
          inset: '0', width: '100vw', height: '100vh', zIndex: '2147483647',
        },
      },
    ),
    surface(
      'stats',
      statsHtmlFile,
      {
        visibility: 'dashboard',
        suppressStateFile: wildcardStateFile,
        style: stage.enabled ? sidebarSurfaceStyle(
          stage, 560, 820, 'top', '2147483620',
        ) : {
          left: '8px', top: '150px', width: '560px', height: '820px',
          transform: 'scale(0.38)', transformOrigin: 'top left', zIndex: '2147483620',
        },
      },
    ),
    surface(
      'ops',
      opsHtmlFile,
      {
        visibility: 'dashboard',
        suppressStateFile: wildcardStateFile,
        style: stage.enabled ? sidebarSurfaceStyle(
          stage, 520, 980, 'bottom', '2147483621',
        ) : {
          right: '8px', top: '150px', width: '520px', height: '980px',
          transform: 'scale(0.34)', transformOrigin: 'top right', zIndex: '2147483621',
        },
      },
    ),
    surface(
      'improve',
      resolveRepoFile(
        env.SOREN_DIRECT_IMPROVE_OVERLAY_HTML_FILE || env.IMPROVE_OVERLAY_HTML_FILE,
        'tmp/state/improve_overlay.html',
      ),
      {
        visibility: 'improve',
        visibilityStateFile: resolveRepoFile(
          env.IMPROVE_STATE_FILE,
          'tmp/state/improve_state.json',
        ),
        suppressStateFile: wildcardStateFile,
        style: {
          inset: '0', width: '100vw', height: '100vh', zIndex: '2147483640',
        },
      },
    ),
    surface(
      'wildcard',
      resolveRepoFile(
        env.SOREN_DIRECT_WILDCARD_OVERLAY_HTML_FILE || env.WILDCARD_PARALLEL_HTML_FILE,
        'tmp/state/wildcard_parallel_overlay.html',
      ),
      {
        visibility: 'wildcard',
        visibilityStateFile: wildcardStateFile,
        style: {
          inset: '0', width: '100vw', height: '100vh', zIndex: '2147483642',
        },
      },
    ),
    surface(
      'avsync',
      resolveRepoFile(
        env.SOREN_DIRECT_AV_SYNC_OVERLAY_HTML_FILE,
        'tmp/state/direct_av_sync_probe.html',
      ),
      {
        title: 'Soren A/V sync acceptance probe',
        pollMs: 250,
        style: {
          left: '0', top: '0', width: '128px', height: '128px', zIndex: '2147483646',
        },
      },
    ),
  ];
  const flagNames = {
    broadcastSidebar: 'SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED',
    broadcastTop: 'SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED',
    broadcastBottom: 'SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED',
    event: 'SOREN_DIRECT_EVENT_OVERLAY_ENABLED',
    stats: 'SOREN_DIRECT_STATS_OVERLAY_ENABLED',
    ops: 'SOREN_DIRECT_OPS_OVERLAY_ENABLED',
    improve: 'SOREN_DIRECT_IMPROVE_OVERLAY_ENABLED',
    wildcard: 'SOREN_DIRECT_WILDCARD_OVERLAY_ENABLED',
    avsync: 'SOREN_DIRECT_AV_SYNC_OVERLAY_ENABLED',
  };
  // dashboard配信では既存のevent/stats/ops HTMLを直接縮小表示しない。
  // それらはread-only data sourceとしてbroadcast専用面へ取り込み、
  // fullscreen互換時だけ従来の個別surfaceをそのまま使う。
  const useBroadcastSurface = stage.enabled
    && enabledValue(env.SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED);
  const candidates = useBroadcastSurface
    ? [...broadcastSurfaces, ...definitions.filter((item) => ['improve', 'wildcard', 'avsync'].includes(item.key))]
    : definitions;
  const surfaces = candidates.filter((item) => enabledValue(env[flagNames[item.key]]));
  const event = definitions[0];
  const broadcastEnabled = surfaces.some((item) => item.region === 'sidebar');
  return {
    enabled: globalEnabled,
    backend,
    htmlFile: event.htmlFile,
    route: event.route,
    elementId: event.elementId,
    stage,
    surfaces,
    broadcast: broadcastEnabled ? {
      stateRoute: DIRECT_BROADCAST_STATE_ROUTE,
      sources: { eventHtmlFile, statsHtmlFile, opsHtmlFile },
    } : null,
  };
}


function readState(file) {
  if (!file) return {};
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}


function wildcardActive(file) {
  const phase = String(readState(file).phase || '').toLowerCase();
  return ['generating', 'running'].includes(phase);
}


export function directOverlaySurfaceVisible(item) {
  if (!item || item.visibility === 'always') return true;
  if (item.visibility === 'dashboard') return !wildcardActive(item.suppressStateFile);
  if (item.visibility === 'wildcard') return wildcardActive(item.visibilityStateFile);
  if (item.visibility === 'improve') {
    if (wildcardActive(item.suppressStateFile)) return false;
    const state = readState(item.visibilityStateFile);
    return String(state.status || '').toLowerCase() === 'running';
  }
  return false;
}


function overlayInstaller(surfaces) {
  if (window.top !== window) return;
  const idleHtml = '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent}</style>';
  const stripSelfRefresh = (html) => String(html ?? '')
    .replace(/<meta\b[^>]*http-equiv\s*=\s*["']?refresh["']?[^>]*>/gi, '')
    .replace(/<script>\s*setTimeout\(\(\)\s*=>\s*location[.]reload\(\)\s*,\s*1000\s*\)\s*;?\s*<\/script>/gi, '');
  const install = () => {
    if (!document.body) return;
    for (const item of surfaces) {
      if (document.getElementById(item.elementId)) continue;
      const frames = [0, 1].map((index) => {
        const frame = document.createElement('iframe');
        frame.id = index === 0 ? item.elementId : `${item.elementId}-buffer`;
        frame.title = index === 0 ? item.title : `${item.title} buffer`;
        frame.setAttribute('aria-hidden', 'true');
        if (item.region) frame.dataset.sorenOverlayRegion = item.region;
        Object.assign(frame.style, {
          position: 'fixed',
          border: '0',
          margin: '0',
          padding: '0',
          background: 'transparent',
          pointerEvents: 'none',
          opacity: index === 0 ? '1' : '0',
          visibility: index === 0 ? 'visible' : 'hidden',
          ...item.style,
        });
        frame.srcdoc = idleHtml;
        document.body.appendChild(frame);
        return frame;
      });
      let activeIndex = 0;
      let lastHtml = '';
      let pendingHtml = '';
      let generation = 0;
      const refresh = async () => {
        try {
          const response = await fetch(item.route, { cache: 'no-store' });
          if (!response.ok) return;
          const html = stripSelfRefresh(await response.text());
          if (html === lastHtml || html === pendingHtml) return;
          pendingHtml = html;
          const requestGeneration = ++generation;
          const incomingIndex = 1 - activeIndex;
          const incoming = frames[incomingIndex];
          const outgoing = frames[activeIndex];
          incoming.addEventListener('load', () => {
            if (requestGeneration !== generation) return;
            incoming.style.visibility = 'visible';
            incoming.style.opacity = '1';
            outgoing.style.opacity = '0';
            outgoing.style.visibility = 'hidden';
            activeIndex = incomingIndex;
            lastHtml = html;
            pendingHtml = '';
            outgoing.srcdoc = idleHtml;
            console.warn(`[DIRECT-OVERLAY-POLL] ${item.key} ${html.includes('const events=') ? 'active' : 'idle'}`);
          }, { once: true });
          incoming.srcdoc = html;
        } catch {
          pendingHtml = '';
          // Retry on the next poll; overlay IO must never block game rendering.
        }
      };
      void refresh();
      setInterval(refresh, Math.max(100, Number(item.pollMs) || 1000));
    }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, { once: true });
  } else {
    install();
  }
}


export async function installDirectOverlay(page, config) {
  if (!config.enabled) return false;
  const payload = config.surfaces.map((item) => ({
    key: item.key,
    route: item.route,
    elementId: item.elementId,
    title: item.title,
    pollMs: item.pollMs,
    region: item.region,
    style: item.style,
  }));
  await page.addInitScript(overlayInstaller, payload);
  await page.evaluate(overlayInstaller, payload);
  return true;
}
