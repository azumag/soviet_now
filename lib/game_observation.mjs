/** Fresh bridge observations; state-file mtime is deliberately not a heartbeat. */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export class GameObservationWriter {
  constructor(file, { now = () => Date.now() / 1000, nonce = () => crypto.randomUUID() } = {}) {
    this.file = file;
    this.now = now;
    this.nonce = nonce;
    this.reset();
  }

  reset() {
    this.gameId = this.nonce();
    this.stopId = null;
    this.previousPhase = null;
    this.previousBoard = null;
    this.writtenAt = -Infinity;
    try { fs.unlinkSync(this.file); } catch {}
  }

  observe(state) {
    if (!state || typeof state !== 'object') {
      this.stopId = null;
      this.previousBoard = null;
      try { fs.unlinkSync(this.file); } catch {}
      return;
    }
    const previousStopId = this.stopId;
    const previousPhase = this.previousPhase;
    if (this.previousPhase === 'GAMEOVER' && state.state !== 'GAMEOVER') this.reset();
    const board = {
      state: state.state ?? null, score: state.score ?? null,
      makeSorenCount: state.makeSorenCount ?? null, pieces: state.pieces ?? null,
    };
    const serialized = JSON.stringify(board);
    const eligible = board.state === 'STOP' && Number.isSafeInteger(board.makeSorenCount)
      && board.makeSorenCount > 0;
    const changed = serialized !== this.previousBoard;
    if (!eligible) this.stopId = null;
    else if (!this.stopId || changed) this.stopId = this.nonce();
    this.previousPhase = state.state;
    this.previousBoard = serialized;
    const now = this.now();
    const identityChanged = this.stopId !== previousStopId || state.state !== previousPhase;
    if (!identityChanged && now - this.writtenAt >= 0 && now - this.writtenAt < 1) return;
    const record = {
      schema: 1, game_id: this.gameId, stop_id: this.stopId, board, observed_epoch: now,
    };
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      const temp = `${this.file}.${process.pid}.tmp`;
      fs.writeFileSync(temp, JSON.stringify(record), { mode: 0o600 });
      fs.renameSync(temp, this.file);
      this.writtenAt = now;
    } catch {
      // A failed heartbeat must not leave fresh-looking boundary evidence.
      try { fs.unlinkSync(this.file); } catch {}
    }
  }
}
