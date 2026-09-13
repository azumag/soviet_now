// Soren91 renderer backend selector (pure selection logic, Issue #303).
//
// selectRendererBackend() picks which renderer backend should serve the next
// session. It is a pure function: no cloud API calls, no spawning, no I/O.
// Fallback *execution* against PowerGPU (OCI/GPU launch, health checks) is
// Issue #309's scope; this file only decides the order.
//
// Priority:
//   1. Local Tier -1 hosts in `localOrder` (first usable wins).
//   2. powergpu-p4-interruptible.
//   3. powergpu-p4-ondemand.
//   4. Any other usable candidate, in input order.
//
// A candidate is usable when `available && !busy`, with an explicit
// `healthy === false` treated as unusable (missing `healthy` means unknown,
// which does NOT disqualify — local agents report via /v1/status and older
// callers may omit the field).
//
// NOTE on the default localOrder (['local-macos', 'local-windows']): this is
// PROVISIONAL. Issue #303 says the Mac-vs-Windows order must be decided from
// measured boot time, stability, and power draw, and those measurements are
// NOT done yet (no 30-minute soak, no production E2E as of 2026-09-13).
// Override via the `localOrder` option once real numbers exist.

// Default local preference order. PROVISIONAL (see NOTE above): macOS first
// is a placeholder until boot-time / stability / power measurements decide
// the real order between local-macos and local-windows.
export const DEFAULT_LOCAL_ORDER = ['local-macos', 'local-windows'];

const CLOUD_FALLBACK_ORDER = ['powergpu-p4-interruptible', 'powergpu-p4-ondemand'];

export function tierForBackend(backend) {
  if (backend === 'local-macos' || backend === 'local-windows') return -1;
  if (backend === 'powergpu-p4-interruptible') return 0;
  if (backend === 'powergpu-p4-ondemand') return 1;
  return null;
}

export function isUsableCandidate(candidate) {
  if (!candidate || typeof candidate.backend !== 'string') return false;
  if (!candidate.available || candidate.busy) return false;
  if (candidate.healthy === false) return false;
  return true;
}

export function selectRendererBackend(
  candidates,
  { localOrder = DEFAULT_LOCAL_ORDER } = {},
) {
  const list = Array.isArray(candidates) ? candidates : [];
  for (const backend of localOrder) {
    const hit = list.find((candidate) => candidate?.backend === backend && isUsableCandidate(candidate));
    if (hit) return { backend, tier: tierForBackend(backend), reason: `local backend ${backend} available` };
  }
  for (const backend of CLOUD_FALLBACK_ORDER) {
    const hit = list.find((candidate) => candidate?.backend === backend && isUsableCandidate(candidate));
    if (hit) return { backend, tier: tierForBackend(backend), reason: `cloud fallback ${backend} available` };
  }
  const rest = list.find(isUsableCandidate);
  if (rest) return { backend: rest.backend, tier: tierForBackend(rest.backend), reason: `fallback ${rest.backend} available` };
  return { backend: null, tier: null, reason: 'no candidate available' };
}
