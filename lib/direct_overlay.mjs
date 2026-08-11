import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';


const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const DIRECT_OVERLAY_ROUTE = '/__soren_overlay/event';
export const DIRECT_OVERLAY_ELEMENT_ID = 'soren-direct-stream-overlay-event';
export const DIRECT_OVERLAY_ROUTES = Object.freeze({
  event: DIRECT_OVERLAY_ROUTE,
  stats: '/__soren_overlay/stats',
  ops: '/__soren_overlay/ops',
  improve: '/__soren_overlay/improve',
  wildcard: '/__soren_overlay/wildcard',
  avsync: '/__soren_overlay/av-sync',
});


export function directOverlayIdleHtml() {
  return '<!doctype html><meta http-equiv="refresh" content="2"><style>html,body{margin:0;background:transparent}</style><script>setTimeout(()=>location.reload(),1000)</script>';
}


function enabledValue(raw, fallback = '1') {
  return !['0', 'false', 'no', 'off'].includes(String(raw ?? fallback).trim().toLowerCase());
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
  const definitions = [
    surface(
      'event',
      resolveRepoFile(
        env.SOREN_DIRECT_OVERLAY_HTML_FILE || env.EVENT_OVERLAY_HTML_FILE,
        'tmp/state/event_overlay.html',
      ),
      {
        title: 'Soren stream event overlay',
        style: {
          inset: '0', width: '100vw', height: '100vh', zIndex: '2147483647',
        },
      },
    ),
    surface(
      'stats',
      resolveRepoFile(
        env.SOREN_DIRECT_STATS_OVERLAY_HTML_FILE || env.STATUS_OVERLAY_HTML_FILE,
        'tmp/state/status_overlay.html',
      ),
      {
        visibility: 'dashboard',
        suppressStateFile: wildcardStateFile,
        style: {
          left: '8px', top: '150px', width: '560px', height: '820px',
          transform: 'scale(0.38)', transformOrigin: 'top left', zIndex: '2147483620',
        },
      },
    ),
    surface(
      'ops',
      resolveRepoFile(
        env.SOREN_DIRECT_OPS_OVERLAY_HTML_FILE || env.SHOW_STATUS_OVERLAY_HTML_FILE,
        'tmp/state/show_status_overlay.html',
      ),
      {
        visibility: 'dashboard',
        suppressStateFile: wildcardStateFile,
        style: {
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
        style: {
          left: '0', top: '0', width: '128px', height: '128px', zIndex: '2147483646',
        },
      },
    ),
  ];
  const flagNames = {
    event: 'SOREN_DIRECT_EVENT_OVERLAY_ENABLED',
    stats: 'SOREN_DIRECT_STATS_OVERLAY_ENABLED',
    ops: 'SOREN_DIRECT_OPS_OVERLAY_ENABLED',
    improve: 'SOREN_DIRECT_IMPROVE_OVERLAY_ENABLED',
    wildcard: 'SOREN_DIRECT_WILDCARD_OVERLAY_ENABLED',
    avsync: 'SOREN_DIRECT_AV_SYNC_OVERLAY_ENABLED',
  };
  const surfaces = definitions.filter((item) => enabledValue(env[flagNames[item.key]]));
  const event = definitions[0];
  return {
    enabled: globalEnabled,
    backend,
    htmlFile: event.htmlFile,
    route: event.route,
    elementId: event.elementId,
    surfaces,
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
  const install = () => {
    if (!document.body) return;
    for (const item of surfaces) {
      if (document.getElementById(item.elementId)) continue;
      const frame = document.createElement('iframe');
      frame.id = item.elementId;
      frame.src = item.route;
      frame.title = item.title;
      frame.setAttribute('aria-hidden', 'true');
      Object.assign(frame.style, {
        position: 'fixed',
        border: '0',
        margin: '0',
        padding: '0',
        background: 'transparent',
        pointerEvents: 'none',
        ...item.style,
      });
      document.body.appendChild(frame);
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
    route: item.route,
    elementId: item.elementId,
    title: item.title,
    style: item.style,
  }));
  await page.addInitScript(overlayInstaller, payload);
  await page.evaluate(overlayInstaller, payload);
  return true;
}
