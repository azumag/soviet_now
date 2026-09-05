import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  clearGameLifecycleResource,
  lifecycleAckMatches,
  lifecycleControlMatches,
  lifecycleRecordMatches,
  readGameLifecycleAck,
  readGameLifecycleControl,
  readGameLifecycleRequest,
  readGameLifecycleResource,
  writeGameLifecycleResource,
} from '../lib/game_lifecycle.mjs';


const REQUEST_ID = '08842091-bf83-4490-9102-40af8ecc98cc';


test('lifecycle file helpers enforce request/game/generation identity and write atomically', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'game-lifecycle-node-'));
  try {
  const request = {
    schema: 1,
    request_id: REQUEST_ID,
    game: 'sorengame',
    generation: 7,
    deadline_epoch: 1893456000.25,
    deadline_at: '2030-01-01T00:00:00.250Z',
  };
  const control = {
    schema: 1,
    action: 'stop',
    request_id: REQUEST_ID,
    game: 'sorengame',
    generation: 7,
    deadline_epoch: request.deadline_epoch,
    deadline_at: request.deadline_at,
  };
  fs.writeFileSync(path.join(root, 'request.json'), `${JSON.stringify(request)}\n`, { mode: 0o600 });
    fs.writeFileSync(path.join(root, 'ack.json'), `${JSON.stringify({ ...request, status: 'stop_requested' })}\n`, { mode: 0o600 });
    fs.writeFileSync(path.join(root, 'control.json'), `${JSON.stringify(control)}\n`, { mode: 0o600 });

    assert.deepEqual(readGameLifecycleRequest(root), request);
    assert.equal(readGameLifecycleAck(root).status, 'stop_requested');
    assert.deepEqual(readGameLifecycleControl(root), control);
    assert.equal(lifecycleControlMatches(control, request, 'stop'), true);
    assert.equal(lifecycleControlMatches({ ...control, request_id: '18842091-bf83-4490-9102-40af8ecc98cc' }, request, 'stop'), false);
    assert.equal(lifecycleControlMatches({ ...control, generation: 8 }, request, 'stop'), false);
    assert.equal(lifecycleControlMatches({ ...control, deadline_at: '2030-01-01T00:00:01.250Z' }, request, 'stop'), false);
    assert.equal(lifecycleRecordMatches({ ...request, status: 'stop_requested' }, request), true);
    assert.equal(lifecycleAckMatches(control, { ...request, status: 'stop_requested' }, ['stop_requested']), true);
    assert.equal(lifecycleAckMatches(control, { ...request, game: 'robots', status: 'stop_requested' }, ['stop_requested']), false);

    const resource = writeGameLifecycleResource(control, 'stopped', {
      browser_closed: true,
      context_closed: true,
      server_closed: true,
    }, root);
    assert.equal(resource.status, 'stopped');
    assert.equal(resource.deadline_epoch, request.deadline_epoch);
    assert.equal(resource.deadline_at, request.deadline_at);
    assert.equal(readGameLifecycleResource(root).request_id, REQUEST_ID);
    assert.equal(fs.statSync(path.join(root, 'game_resource.json')).mode & 0o777, 0o600);
    assert.deepEqual(fs.readdirSync(root).filter((item) => item.includes('.tmp.')), []);

    clearGameLifecycleResource(root);
    assert.equal(readGameLifecycleResource(root), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});


test('lifecycle records reject a reused UUID with a different game or generation', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'game-lifecycle-node-identity-'));
  try {
    const request = {
      schema: 1,
      request_id: REQUEST_ID,
      game: 'sorengame',
      generation: 7,
      deadline_epoch: 1893456000,
      deadline_at: '2030-01-01T00:00:00.000Z',
    };
    const control = { action: 'stop', ...request };
    assert.equal(lifecycleControlMatches(control, request, 'stop'), true);
    assert.equal(lifecycleControlMatches({ ...control, game: 'robots' }, request, 'stop'), false);
    assert.equal(lifecycleControlMatches({ ...control, generation: 8 }, request, 'stop'), false);
    assert.equal(lifecycleControlMatches({ ...control, deadline_epoch: request.deadline_epoch + 1 }, request, 'stop'), false);
    assert.equal(writeGameLifecycleResource({ ...control, deadline_at: '' }, 'stopped', {}, root), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});


test('invalid lifecycle control is ignored rather than converted into a resource ack', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'game-lifecycle-node-invalid-'));
  try {
    assert.equal(writeGameLifecycleResource({ request_id: 'not-a-uuid' }, 'stopped', {}, root), null);
    assert.equal(readGameLifecycleResource(root), null);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
