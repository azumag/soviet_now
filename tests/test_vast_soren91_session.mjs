import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  buildCreateArgs,
  buildSearchQuery,
  defaults,
  estimateSessionCost,
  extractContractId,
  extractPocResult,
  main,
  selectOffer,
  validateOptions,
} from '../tools/vast_soren91_session.mjs';

const options = validateOptions({ ...defaults({}), image: 'ghcr.io/azumag/soren91-gpu-runner:test' });

test('fixed search query keeps the PoC inside price and reliability limits', () => {
  const query = buildSearchQuery(options);
  assert.match(query, /compute_cap>=610/);
  assert.match(query, /reliability>=0\.98/);
  assert.match(query, /inet_down>=100/);
  assert.match(query, /inet_down_cost<=0\.02/);
  assert.match(query, /inet_up_cost<=0\.02/);
  assert.match(query, /dph<=0\.08/);
  assert.match(query, /verified=True/);
});

test('offer selection uses complete five-minute session cost', () => {
  const cheapComputeExpensiveNetwork = { id: 1, dph: 0.01, inet_down_cost: 0.02, inet_up_cost: 0.02, storage_cost: 0.01, reliability: 0.99, verified: true, rentable: true, rented: false };
  const selected = { id: 2, dph: 0.012, inet_down_cost: 0, inet_up_cost: 0, storage_cost: 0.01, reliability: 0.99, verified: true, rentable: true, rented: false };
  assert.equal(selectOffer([cheapComputeExpensiveNetwork, selected], options).id, 2);
  const cost = estimateSessionCost(selected, options);
  assert.equal(cost.dph, 0.012);
  assert.ok(cost.total > 0 && cost.total < 0.01);
  assert.ok(cost.egressGb > 0.08 && cost.egressGb < 0.1);
  assert.equal(cost.imageDownloadGb, 1.2);
});

test('offer selection rejects price and reliability violations', () => {
  assert.equal(selectOffer([
    { id: 1, dph: 0.081, inet_down_cost: 0, inet_up_cost: 0, reliability: 1 },
    { id: 2, dph: 0.01, inet_down_cost: 0, inet_up_cost: 0, reliability: 0.97 },
  ], options), null);
});

test('create arguments contain only bounded non-secret PoC settings', () => {
  const args = buildCreateArgs({ id: 42 }, options, 'soren91-poc-test');
  assert.deepEqual(args.slice(0, 3), ['create', 'instance', '42']);
  const rendered = args.join(' ');
  assert.match(rendered, /SOREN91_POC_SESSION_SEC=300/);
  assert.match(rendered, /NVIDIA_DRIVER_CAPABILITIES=graphics,video,utility,display/);
  assert.doesNotMatch(rendered, /api.key|token|passphrase/i);
});

test('result and instance id parsers accept Vast and runner output', () => {
  assert.equal(extractContractId({ success: true, new_contract: 1234 }), 1234);
  assert.equal(extractContractId("{'success': True, 'new_contract': 5678}"), 5678);
  assert.deepEqual(extractPocResult('noise\nSOREN91_POC_RESULT={"pass":true,"probe":{"fps":29.7}}\n'), { pass: true, probe: { fps: 29.7 } });
});

test('hard safety caps cannot be raised from command options', () => {
  assert.throws(() => validateOptions({ ...options, maxDph: 0.081 }), /maxDph/);
  assert.throws(() => validateOptions({ ...options, maxInetDownCost: 0.021 }), /maxInetDownCost/);
  assert.throws(() => validateOptions({ ...options, sessionSec: 301 }), /sessionSec/);
  assert.throws(() => validateOptions({ ...options, instanceMaxAgeSec: 601 }), /instanceMaxAgeSec/);
  assert.throws(() => validateOptions({ ...options, market: 'bid' }), /on-demand/);
  assert.throws(() => validateOptions({ ...options, minFps: 24 }), /minFps/);
  assert.throws(() => validateOptions({ ...options, diskGb: 21 }), /diskGb/);
});

test('default controller run is a side-effect-free offer-file dry-run', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'soren91-vast-test-'));
  const fixture = path.join(directory, 'offers.json');
  fs.writeFileSync(fixture, JSON.stringify([
    { id: 77, gpu_name: 'GTX 1050 Ti', dph: 0.06, inet_down_cost: 0.01, inet_up_cost: 0.01, reliability: 0.99, verified: true, rentable: true, rented: false },
  ]));
  try {
    const plan = await main(['--offer-json', fixture]);
    assert.equal(plan.execute, false);
    assert.equal(plan.offerId, 77);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
