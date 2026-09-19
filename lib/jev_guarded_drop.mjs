// Pure bridge-side validation for one JEV drop.  The browser/Unity adapter
// calls this before touching __sorenCommand; it is deliberately independent
// from Playwright so stale and duplicate inputs can be tested offline.

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const CANDIDATE_RE = /^c[0-9]{2}$/;
const POLICY = 'jev';

function integer(value, minimum = 0) {
  return Number.isInteger(value) && value >= minimum;
}

function failure(reason) {
  return { ok: false, status: 'rejected', reason };
}

function commandKey(command) {
  return [
    command.run_id,
    command.game_instance_id,
    command.game_generation,
    command.player_generation,
    command.opportunity_seq,
  ].join(':');
}

function validateJevDrop(command, context = {}) {
  if (!command || typeof command !== 'object' || Array.isArray(command)) return failure('invalid_command');
  if (command.action !== 'drop' || command.player_policy !== POLICY) return failure('invalid_policy');
  for (const name of ['command_id', 'run_id', 'game_instance_id']) {
    if (typeof command[name] !== 'string' || !UUID_RE.test(command[name])) return failure(`invalid_${name}`);
  }
  if (typeof command.expires_at !== 'number' || !Number.isFinite(command.expires_at)) return failure('invalid_expiry');
  if (command.expires_at <= Date.now() / 1000) return failure('expired');
  if (!integer(command.game_generation) || !integer(command.player_generation)) return failure('invalid_generation');
  if (!integer(command.opportunity_seq, 1) || !integer(command.frame_seq)) return failure('invalid_sequence');
  if (!CANDIDATE_RE.test(command.candidate_id || '')) return failure('invalid_candidate_id');
  if (typeof command.x !== 'number' || !Number.isFinite(command.x) || command.x < -3 || command.x > 3) {
    return failure('invalid_x');
  }
  const identity = context.identity || context.state?.jev_identity;
  if (!identity || typeof identity !== 'object') return failure('identity_missing');
  for (const name of ['run_id', 'game_instance_id', 'game_generation', 'player_generation', 'opportunity_seq']) {
    if (identity[name] !== command[name]) return failure('identity_mismatch');
  }
  if (identity.frame_seq != null && command.frame_seq > identity.frame_seq) return failure('future_frame');
  if (identity.drop_piece_id != null && command.expected_drop_piece_id !== identity.drop_piece_id) {
    return failure('drop_piece_mismatch');
  }
  const state = context.state || {};
  if (state.state !== 'MOVE' && state.phase !== 'MOVE') return failure('phase_not_move');
  return { ok: true, status: 'ready', key: commandKey(command) };
}

class JevDropGuard {
  constructor({ maxSeen = 256 } = {}) {
    this.maxSeen = Number.isInteger(maxSeen) && maxSeen > 0 ? maxSeen : 256;
    this.seen = new Set();
  }

  dispatch(command, context = {}) {
    const checked = validateJevDrop(command, context);
    if (!checked.ok) return checked;
    if (this.seen.has(checked.key)) return { ok: false, status: 'duplicate', reason: 'duplicate_command', key: checked.key };
    this.seen.add(checked.key);
    while (this.seen.size > this.maxSeen) this.seen.delete(this.seen.values().next().value);
    return { ok: true, status: 'accepted', key: checked.key };
  }
}

export { JevDropGuard, commandKey, validateJevDrop };
