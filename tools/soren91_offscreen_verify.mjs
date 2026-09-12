#!/usr/bin/env node
// Soren91 macOS offscreen (virtual display) verification helpers (Issue #303).
//
// Pure, side-effect-free functions shared by soren91_macos_session.mjs and
// soren91_macos_renderer.mjs, and directly importable from node --test on any
// platform. No pixels are ever captured here — only display/window bounds
// arithmetic (IDs + rectangles), so there is nothing privacy-sensitive.
//
// The renderer places the Chrome window inside the virtual display's bounds
// (see placementFor) and then MUST prove — from measured bounds, never from
// requested coordinates — that the window does not intersect any physical
// display (see computePhysicalOverlap). Any overlap fails the run
// fail-closed: a visible window would be the Issue #303 privacy incident
// all over again.

export function isFiniteRect(value) {
  return Boolean(value)
    && Number.isFinite(value.x) && Number.isFinite(value.y)
    && Number.isFinite(value.width) && Number.isFinite(value.height);
}

// Accepts either `{x,y,width,height}` or `{displayID, bounds:{...}}`, as a
// parsed object or a JSON string (the session passes the holder's handshake
// through SOREN91_LOCAL_VDISPLAY_BOUNDS). Returns `{ displayID, bounds }`.
export function parseVDisplayBounds(value) {
  let payload = value;
  if (typeof payload === 'string') {
    try { payload = JSON.parse(payload); } catch {
      throw new Error(`virtual display bounds is not valid JSON: ${value}`);
    }
  }
  const bounds = payload?.bounds ?? payload;
  if (!isFiniteRect(bounds)) {
    throw new Error(`virtual display bounds must be {x,y,width,height} with finite numbers: ${JSON.stringify(payload)}`);
  }
  if (!(bounds.width > 0 && bounds.height > 0)) {
    throw new Error(`virtual display bounds must have positive size: ${JSON.stringify(bounds)}`);
  }
  const displayID = payload?.displayID ?? payload?.displayId ?? null;
  if (displayID != null && !(Number.isFinite(displayID) && displayID > 0)) {
    throw new Error(`virtual display displayID must be a positive number: ${JSON.stringify(payload)}`);
  }
  return {
    displayID,
    bounds: { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height },
  };
}

// Window origin inside the virtual display: origin + margin so the window
// fully fits even with its outer (chrome-inclusive) size. The caller must
// still verify the MEASURED window rect afterwards — this is only the ask.
export function placementFor(bounds, { margin = 20 } = {}) {
  if (!isFiniteRect(bounds)) throw new Error('placementFor requires finite display bounds');
  if (!(Number.isFinite(margin) && margin >= 0)) throw new Error('placementFor margin must be >= 0');
  return { left: bounds.x + margin, top: bounds.y + margin };
}

export function rectIntersectionArea(a, b) {
  if (!isFiniteRect(a) || !isFiniteRect(b)) return 0;
  const x0 = Math.max(a.x, b.x);
  const y0 = Math.max(a.y, b.y);
  const x1 = Math.min(a.x + a.width, b.x + b.width);
  const y1 = Math.min(a.y + a.height, b.y + b.height);
  return Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
}

// `windowRect` is the MEASURED Chrome window rect ({x,y,width,height}).
// `displays` are `{id, bounds}` entries from the helper's --list output.
// The virtual display itself (excludeDisplayId) is skipped; every other
// display counts as physical. Returns `{ overlap, area, displayIds }` where
// overlap is true when even 1px intersects a physical display. The proof is
// fail-closed: malformed display entries, or a list that does not contain the
// virtual display we were told to exclude, abort instead of being treated as
// "no physical overlap".
export function computePhysicalOverlap(windowRect, displays, excludeDisplayId = null) {
  if (!isFiniteRect(windowRect)) throw new Error('computePhysicalOverlap requires a finite measured windowRect');
  if (!Array.isArray(displays)) throw new Error('computePhysicalOverlap requires a displays array');
  let area = 0;
  const displayIds = [];
  let sawExcludedDisplay = excludeDisplayId == null;
  for (const display of displays) {
    if (!Number.isFinite(display?.id)
      || !isFiniteRect(display?.bounds)
      || !(display.bounds.width > 0 && display.bounds.height > 0)) {
      throw new Error('computePhysicalOverlap received malformed display bounds (fail-closed)');
    }
    if (display.id === excludeDisplayId) {
      sawExcludedDisplay = true;
      continue;
    }
    const part = rectIntersectionArea(windowRect, display.bounds);
    if (part > 0) {
      area += part;
      displayIds.push(display.id);
    }
  }
  if (!sawExcludedDisplay) {
    throw new Error(`virtual display ${excludeDisplayId} missing from online display list (fail-closed)`);
  }
  return { overlap: area > 0, area, displayIds };
}

// Parses the helper's --list output (a single JSON line on stderr; the
// helper never writes frame data, so either stream may carry it — prefer
// stderr). Returns the displays array. Throws fail-closed on any deviation.
export function parseVDisplayList(line) {
  let payload;
  try { payload = JSON.parse(String(line || '')); } catch {
    throw new Error(`virtual display --list emitted non-JSON: ${line}`);
  }
  if (payload?.ok !== true || !Array.isArray(payload?.displays)) {
    throw new Error(`virtual display --list failed (fail-closed): ${payload?.error || JSON.stringify(payload)}`);
  }
  return payload.displays;
}
