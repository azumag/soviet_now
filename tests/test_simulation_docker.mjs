import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { snapshotPolicies, policyWorker } from '../tools/simulation_lab.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
test('real container denies host data, input writes and outbound network, and is removed', {
  skip: process.env.SOREN_TEST_DOCKER !== '1', timeout: 30000,
}, async () => {
  fs.mkdirSync(path.join(ROOT, 'tmp'), { recursive: true });
  const out = fs.mkdtempSync(path.join(ROOT, 'tmp/simulation-docker-unit-'));
  let worker;
  const before = process.env.HOST_SECRET_PROBE;
  try {
    process.env.HOST_SECRET_PROBE = 'fake-test-secret-must-not-cross';
    const snapshot = snapshotPolicies(out, { A: path.join(ROOT, 'strategy.py') });
    const secretPath = path.join(out, 'fake-host-secret'); fs.writeFileSync(secretPath, 'fake test secret');
    // Deliberately bypass the AST gate in this fixture to test the actual OS boundary.
    fs.writeFileSync(path.join(snapshot, 'policies/A.py'), `
import os
import socket
assert os.environ.get('HOST_SECRET_PROBE') is None
for target in [${JSON.stringify(secretPath)}, '/input/policies/A.py', '/escape-marker']:
    try:
        with open(target, 'w' if target != ${JSON.stringify(secretPath)} else 'r') as f:
            raise AssertionError('unexpected file access: ' + target)
    except OSError:
        pass
s = socket.socket(); s.settimeout(0.2)
assert s.connect_ex(('1.1.1.1', 80)) != 0
s.close()
def decide(gs, an):
    return {'x': 0, 'reason': 'isolation checked'}
`);
    const image = spawnSync('docker', ['image', 'inspect', 'python:3.12-slim', '--format', '{{.Id}}'], { encoding: 'utf8' });
    assert.equal(image.status, 0, 'requires local python:3.12-slim image');
    worker = policyWorker('A', snapshot, image.stdout.trim(), path.join(out, 'A.stderr.log'));
    const config = await worker.request('config'); assert.ok(config.policy_bundle);
    const observed = await worker.request('observe', { pieces: [] }); assert.equal(observed.settled, true);
    await worker.close(); worker = undefined;
    const cid = fs.readFileSync(path.join(out, 'A.cid'), 'utf8').trim();
    assert.equal(spawnSync('docker', ['inspect', cid], { stdio: 'ignore' }).status, 1);
    assert.equal(fs.readFileSync(secretPath, 'utf8'), 'fake test secret');
  } finally {
    await worker?.close();
    if (before === undefined) delete process.env.HOST_SECRET_PROBE; else process.env.HOST_SECRET_PROBE = before;
    fs.rmSync(out, { recursive: true, force: true });
  }
});
