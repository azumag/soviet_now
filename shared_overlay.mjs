#!/usr/bin/env node

// CLI for the game-independent notification/status surface.  Importing this
// file is side-effect free; the browser and HTTP server are created only when
// it is executed as the entrypoint.

import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  closeSharedOverlayServer,
  installSharedOverlay,
  loadSharedOverlayConfig,
  setSharedOverlayBrowserReady,
  setSharedOverlayFramesReady,
  startSharedOverlayServer,
  trackOwnedServerSockets,
  waitForSharedOverlayFrames,
} from './lib/shared_overlay.mjs';
import { startTwicaOverlayProxy } from './lib/twica_overlay_proxy.mjs';


const ENTRYPOINT = path.resolve(fileURLToPath(import.meta.url));


function enabledValue(raw, fallback = false) {
  const value = String(raw ?? (fallback ? '1' : '0')).trim().toLowerCase();
  return !['0', 'false', 'no', 'off'].includes(value);
}


function serviceUrl(server, config) {
  const address = server.address();
  const port = address && typeof address === 'object' ? address.port : config.port;
  const host = String(config.host || '127.0.0.1').includes(':')
    ? `[${config.host}]`
    : config.host;
  return `http://${host}:${port}/`;
}


function closeOwnedServer(server, socketTracker = null) {
  socketTracker?.dispose?.();
  if (!server || !server.listening) return Promise.resolve();
  try { server.closeAllConnections?.(); } catch {}
  return new Promise((resolve) => server.close(() => resolve()));
}


async function measurePage(page) {
  return page.evaluate(() => {
    const stage = document.getElementById('soren-direct-stream-stage');
    const rect = stage?.getBoundingClientRect?.();
    return {
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      screenX: window.screenX,
      screenY: window.screenY,
      devicePixelRatio: window.devicePixelRatio,
      stage: rect ? {
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      } : null,
    };
  });
}


/**
 * Start the shared browser/server pair and wait until a termination signal.
 * Only the browser and server created here are closed by the returned cleanup
 * path; no Soren/game process is discovered or signalled.
 */
export async function runSharedOverlay(options = {}) {
  // Keep module imports side-effect free and allow the library/route tests to
  // run in environments that do not install Playwright.  The CLI path loads
  // the browser dependency only when it is actually asked to launch Chromium.
  const { chromium } = options.chromium
    ? { chromium: options.chromium }
    : await import('playwright');
  const config = options.config || loadSharedOverlayConfig(
    options.env || process.env,
    options.platform || 'linux',
  );
  const server = await startSharedOverlayServer(config, {
    host: options.host || config.host,
    port: options.port ?? config.port,
  });
  const twicaProxyStarter = options.startTwicaOverlayProxy || startTwicaOverlayProxy;
  let browser = null;
  let page = null;
  let twicaProxy = null;
  let twicaSocketTracker = null;
  let closePromise = null;
  let cleanupRequested = false;
  let pageClosed = false;
  let browserClosed = false;
  let twicaProxyClosed = false;
  let serverClosed = false;
  const cleanupResources = async () => {
    setSharedOverlayBrowserReady(server, false);
    // Closing our own page is helpful for headed Chromium, while browser.close
    // remains the authoritative cleanup for child processes.  The flags make
    // repeated cleanup passes safe when a resource is assigned after SIGTERM.
    if (page && !pageClosed) {
      pageClosed = true;
      try { await page.close({ runBeforeUnload: false }); } catch {}
    }
    if (browser && !browserClosed) {
      browserClosed = true;
      try { await browser.close(); } catch {}
    }
    if (twicaProxy && !twicaProxyClosed) {
      twicaProxyClosed = true;
      try { await closeOwnedServer(twicaProxy, twicaSocketTracker); } catch {}
    }
    if (!serverClosed) {
      serverClosed = true;
      await closeSharedOverlayServer(server);
    }
  };
  const close = () => {
    cleanupRequested = true;
    // Do not memoize only the first pass.  SIGTERM can arrive while Chromium
    // or the TwiCa proxy is still being allocated; a later assignment must
    // enqueue another pass instead of observing an already-resolved promise.
    const previous = closePromise || Promise.resolve();
    closePromise = previous.then(() => cleanupResources());
    return closePromise;
  };

  let signalReceived = false;
  let stopping = false;
  let runtimeFailure = null;
  let resolveSignal;
  const signalPromise = new Promise((resolve) => { resolveSignal = resolve; });
  const onSignal = (signal) => {
    stopping = true;
    signalReceived = true;
    if (options.log !== false) console.log(`[SHARED-OVERLAY] ${signal}; cleaning up`);
    void close().finally(resolveSignal);
  };
  const onRuntimeFailure = (reason) => {
    if (stopping || closePromise) return;
    stopping = true;
    runtimeFailure = new Error(`shared overlay browser ${reason}`);
    signalReceived = true;
    setSharedOverlayBrowserReady(server, false);
    if (options.log !== false) console.error(`[SHARED-OVERLAY] ${runtimeFailure.message}`);
    void close().finally(resolveSignal);
  };
  // Register before proxy/browser launch so an early SIGTERM still closes the
  // HTTP listener and any resource that has already been allocated.
  process.once('SIGTERM', onSignal);
  process.once('SIGINT', onSignal);

  try {
    const launchEnv = { ...process.env };
    // Passing a copied environment explicitly documents that DISPLAY is
    // inherited from the caller (normally the Xvfb/desktop display).
    if (process.env.DISPLAY) launchEnv.DISPLAY = process.env.DISPLAY;
    const headless = enabledValue(
      options.headless ?? process.env.SOREN_SHARED_OVERLAY_HEADLESS,
      false,
    );
    const args = [
      '--window-size=1280,720',
      '--window-position=0,0',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-infobars',
    ];
    const kiosk = !headless && enabledValue(
      options.kiosk ?? process.env.SOREN_SHARED_OVERLAY_KIOSK,
      true,
    );
    if (kiosk) {
      args.push('--kiosk');
    }

    // direct_overlay rewrites a configured TwiCa URL to this local proxy. The
    // shared service owns that proxy when it is enabled, so stopping Soren's
    // game bridge does not silently remove the common overlay path.
    const twicaSurface = config.direct?.surfaces?.find((item) => item.key === 'twica');
    if (twicaSurface?.upstreamUrl) {
      const proxyPort = Number.parseInt(
        process.env.SOREN_DIRECT_TWICA_PROXY_PORT || '18080',
        10,
      );
      if (Number.isSafeInteger(proxyPort) && proxyPort > 0 && proxyPort < 65536) {
        twicaProxy = await twicaProxyStarter({
          port: proxyPort,
          upstream: twicaSurface.upstreamUrl,
          log: options.log === false ? null : console,
        });
        twicaSocketTracker = trackOwnedServerSockets(twicaProxy);
      }
    }
    if (signalReceived || cleanupRequested) {
      await close();
      return { config, server, browser, page, close };
    }
    browser = await chromium.launch({
      headless,
      executablePath: process.env.SOREN_CHROME_EXECUTABLE_PATH || undefined,
      env: launchEnv,
      args,
    });
    browser.on?.('disconnected', () => onRuntimeFailure('disconnected'));
    if (signalReceived || cleanupRequested) {
      await close();
      return { config, server, browser, page, close };
    }
    page = await browser.newPage({
      viewport: { width: 1280, height: 720 },
      deviceScaleFactor: 1,
    });
    page.on?.('close', () => onRuntimeFailure('page closed'));
    page.on?.('crash', () => onRuntimeFailure('page crashed'));
    if (signalReceived || cleanupRequested) {
      await close();
      return { config, server, browser, page, close };
    }
    await page.goto(serviceUrl(server, server.sharedOverlayConfig || config), {
      waitUntil: 'domcontentloaded',
    });
    const installed = await installSharedOverlay(page, {
      ...config,
      host: server.sharedOverlayConfig?.host || config.host,
      port: server.sharedOverlayConfig?.port || config.port,
    });
    const frameReadiness = await waitForSharedOverlayFrames(page, config, {
      timeoutMs: options.overlayReadyTimeoutMs ?? 10000,
    });
    const viewport = await measurePage(page);
    if (signalReceived) {
      await signalPromise;
      if (runtimeFailure) throw runtimeFailure;
      return { config, server, browser, page, close };
    }
    setSharedOverlayBrowserReady(server, true, viewport);
    setSharedOverlayFramesReady(server, frameReadiness.ready);
    if (options.log !== false) {
      console.log(`[SHARED-OVERLAY] listening ${serviceUrl(server, server.sharedOverlayConfig || config)}`);
      console.log(`[SHARED-OVERLAY] stage ${JSON.stringify(installed.stage)}`);
      console.log(`[SHARED-OVERLAY] viewport ${JSON.stringify(viewport)}`);
      console.log(`[SHARED-OVERLAY] frames=${frameReadiness.frames.length}`);
      console.log(`[SHARED-OVERLAY] overlays=${installed.overlaysInstalled ? 'installed' : 'disabled'}`);
    }

    await signalPromise;
    if (runtimeFailure) throw runtimeFailure;
  } catch (error) {
    stopping = true;
    await close();
    throw error;
  } finally {
    process.off('SIGTERM', onSignal);
    process.off('SIGINT', onSignal);
  }
  return { config, server, browser, page, close };
}


export { loadSharedOverlayConfig } from './lib/shared_overlay.mjs';


const isMain = process.argv[1] && path.resolve(process.argv[1]) === ENTRYPOINT;
if (isMain) {
  runSharedOverlay().catch((error) => {
    console.error(`[SHARED-OVERLAY] failed: ${error?.message || String(error)}`);
    process.exitCode = 1;
  });
}
