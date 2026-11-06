// Transitional, read-only compatibility loader. The canonical shared state
// adapter belongs to docich; Soviet keeps its self-contained fallback until
// both live services and game-only smoke tests have validated the cutover.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { buildDirectBroadcastOverlayState as legacyStateBuilder } from './direct_broadcast_overlay.mjs';

const STATE_ROUTE = '/__soren_overlay/broadcast/state';

export async function loadBroadcastStateBuilder(env = process.env) {
  const root = String(env.DOCICH_ROOT || '/home/ubuntu/docich');
  const candidate = path.resolve(root, 'overlays/direct_broadcast_overlay_state.mjs');
  try {
    if (fs.statSync(candidate).isFile()) {
      const module = await import(pathToFileURL(candidate).href);
      if (module.DIRECT_BROADCAST_STATE_ROUTE !== STATE_ROUTE
          || module.DIRECT_BROADCAST_VERSION !== 1
          || typeof module.buildDirectBroadcastOverlayState !== 'function') {
        throw new Error('docich broadcast-state contract mismatch');
      }
      return module.buildDirectBroadcastOverlayState;
    }
  } catch {
    // Standalone Soviet deployments, incomplete docich upgrades, and invalid
    // modules must not break the game/stream. The established local path stays.
  }
  return legacyStateBuilder;
}
