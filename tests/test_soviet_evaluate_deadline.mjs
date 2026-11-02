import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const bridgePath = fileURLToPath(new URL('../soviet_local.mjs', import.meta.url));

// Runs a small driver script in a child process so process.exit() can be
// observed.  cwd is a temp dir because JEV_ACK_ROOT is relative.
function runDriver(body, cwd) {
  return spawnSync(process.execPath, ['--input-type=module', '-e', body], {
    cwd,
    env: {
      ...process.env,
      SOREN_LOCAL_CONTROLLER_IMPORT_ONLY: '1',
      SOREN_EVALUATE_DEADLINE_MS: '150',
      BRIDGE: bridgePath,
    },
    timeout: 30000,
    encoding: 'utf8',
  });
}

const jevCommand = {
  action: 'drop',
  player_policy: 'jev',
  command_id: '33333333-3333-4333-8333-333333333333',
  run_id: '11111111-1111-4111-8111-111111111111',
  game_instance_id: '22222222-2222-4222-8222-222222222222',
  opportunity_seq: 7,
};

test('pending page.evaluate hits the deadline, emits BRIDGE-FATAL and exits', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'evaluate-deadline-'));
  try {
    const res = runDriver(`
      const m = await import(process.env.BRIDGE);
      const page = { evaluate: () => new Promise(() => {}) };
      await m.evaluateWithDeadline(page, () => null, undefined, 'getGameState');
      console.log('UNREACHABLE');
    `, dir);
    assert.match(res.stderr, /\[BRIDGE-FATAL\] page\.evaluate deadline exceeded \(getGameState\)/);
    assert.equal(res.status, 1);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('deadline during a JEV drop leaves an outcome_unknown ack', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'evaluate-deadline-jev-'));
  try {
    const res = runDriver(`
      const m = await import(process.env.BRIDGE);
      const cmd = ${JSON.stringify(jevCommand)};
      m.setActiveJevDropCommandForTest(cmd);
      const page = { evaluate: () => new Promise(() => {}) };
      await m.evaluateWithDeadline(page, () => null, undefined, 'drop_dispatch');
      console.log('UNREACHABLE');
    `, dir);
    assert.match(res.stderr, /\[BRIDGE-FATAL\] page\.evaluate deadline exceeded \(drop_dispatch\)/);
    assert.equal(res.status, 1);
    const ackPath = path.join(
      dir, 'tmp/state/jev_player/acks',
      jevCommand.run_id, jevCommand.game_instance_id, 'opportunity_00000007.json',
    );
    const ack = JSON.parse(fs.readFileSync(ackPath, 'utf8'));
    assert.equal(ack.status, 'outcome_unknown');
    assert.equal(ack.reason, 'evaluate_deadline');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a responsive evaluate resolves before the deadline', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'evaluate-deadline-ok-'));
  try {
    const res = runDriver(`
      const m = await import(process.env.BRIDGE);
      const page = { evaluate: async (fn) => fn() };
      const value = await m.evaluateWithDeadline(page, () => 42, undefined, 'getGameState');
      console.log('VALUE=' + value);
    `, dir);
    assert.equal(res.status, 0);
    assert.match(res.stdout, /VALUE=42/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
