import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  buildLaunchArgs,
  defaults,
  estimateSessionCost,
  extractInstanceId,
  extractLaunchPrice,
  extractPocResult,
  findRecoverableLaunch,
  main,
  selectPricing,
  validateOptions,
} from '../tools/powergpu_soren91_session.mjs';

const options = validateOptions({ ...defaults({}), image: 'ghcr.io/azumag/soren91-gpu-runner:test' });
const p4Pricing = {
  slug: 'tesla-p4',
  name: 'NVIDIA Tesla P4',
  available_gpus: 18,
  price_per_gpu_hour: { on_demand: 0.018, interruptible: 0.009, reserved: 0.011 },
};

test('Tier 0 is fixed to interruptible Tesla P4 at 30fps', () => {
  assert.equal(options.gpu, 'tesla-p4');
  assert.equal(options.type, 'interruptible');
  assert.equal(options.maxDph, 0.01);
  assert.equal(options.minFps, 30);
  assert.equal(options.width, 960);
  assert.equal(options.height, 540);
});

test('pricing accepts P4 interruptible below hard cap and rejects expensive or unavailable entries', () => {
  assert.equal(selectPricing([p4Pricing], options).dph, 0.009);
  assert.equal(selectPricing([{ ...p4Pricing, price_per_gpu_hour: { ...p4Pricing.price_per_gpu_hour, interruptible: 0.011 } }], options), null);
  assert.equal(selectPricing([{ ...p4Pricing, available_gpus: 0 }], options), null);
});

test('session cost includes compute, flat bandwidth and ephemeral storage', () => {
  const selected = selectPricing([p4Pricing], options);
  const cost = estimateSessionCost(selected, options);
  assert.equal(cost.dph, 0.009);
  assert.ok(cost.compute > 0 && cost.compute < 0.002);
  assert.ok(cost.network > 0.01 && cost.network < 0.02);
  assert.ok(cost.storage > 0 && cost.storage < 0.001);
  assert.ok(cost.total < 0.02);
});

test('launch args contain only bounded non-secret PoC settings', () => {
  const args = buildLaunchArgs(options);
  const rendered = args.join(' ');
  assert.match(rendered, /launch --gpu tesla-p4 --type interruptible/);
  assert.match(rendered, /SOREN91_POC_MIN_FPS=30/);
  assert.match(rendered, /NVIDIA_DRIVER_CAPABILITIES=graphics,video,utility,display/);
  assert.match(rendered, /--image ghcr\.io\/azumag\/soren91-gpu-runner:test/);
  assert.doesNotMatch(rendered, /api.key|token|passphrase/i);
});

test('PowerGPU launch and runner parsers accept expected outputs', () => {
  assert.equal(extractInstanceId({ id: 'i-52ab77c1', price_hr: 0.009 }), 'i-52ab77c1');
  assert.equal(extractInstanceId('instance i-b81f02aa running'), 'i-b81f02aa');
  assert.equal(extractLaunchPrice({ id: 'i-1', price_hr: 0.009 }), 0.009);
  assert.deepEqual(extractPocResult('noise\nSOREN91_POC_RESULT={"pass":true,"probe":{"fps":30.4}}\n'), { pass: true, probe: { fps: 30.4 } });
});

test('ambiguous launch recovery only accepts one strongly attributable new instance', () => {
  const before = new Set(['i-old1111']);
  const started = 1_800_000_000;
  const exact = {
    id: 'i-new2222',
    gpu_slug: 'tesla-p4',
    image: options.image,
    created_at: started + 1,
  };
  assert.equal(findRecoverableLaunch(before, { instances: [{ id: 'i-old1111' }, exact] }, options, started), 'i-new2222');

  assert.equal(findRecoverableLaunch(before, { instances: [
    { id: 'i-new3333', gpu_slug: 'tesla-p4', image: 'other/image', created_at: started - 60 },
  ] }, options, started), null);

  assert.equal(findRecoverableLaunch(before, { instances: [
    exact,
    { ...exact, id: 'i-new4444' },
  ] }, options, started), null);

  assert.equal(findRecoverableLaunch(before, { instances: [
    exact,
    { id: 'i-new8888', gpu_slug: 'l4', image: 'unrelated/image', created_at: started + 1 },
  ] }, options, started), null);
});

test('recent recovery candidates still require both exact GPU and image identity', () => {
  const before = new Set();
  const started = 1_800_000_000;
  assert.equal(findRecoverableLaunch(before, { instances: [
    { id: 'i-new3333', gpu_slug: 'tesla-p4', image: 'other/image', created_at: started + 1 },
  ] }, options, started), null);
  assert.equal(findRecoverableLaunch(before, { instances: [
    { id: 'i-new4444', gpu_slug: 'l4', image: options.image, created_at: started + 1 },
  ] }, options, started), null);
  assert.equal(findRecoverableLaunch(before, { instances: [
    { id: 'i-new7777', gpu_slug: 'tesla-p4', image: options.image, created_at: started - 60 },
  ] }, options, started), null);
});

test('recovery without created_at requires both P4 and exact image identity', () => {
  const before = new Set();
  assert.equal(findRecoverableLaunch(before, { data: [
    { id: 'i-new5555', gpu_name: 'NVIDIA Tesla P4', image_ref: options.image },
  ] }, options, 1_800_000_000), 'i-new5555');
  assert.equal(findRecoverableLaunch(before, { data: [
    { id: 'i-new6666', gpu_name: 'NVIDIA Tesla P4' },
  ] }, options, 1_800_000_000), null);
});

test('hard safety caps cannot be raised from command options', () => {
  assert.throws(() => validateOptions({ ...options, maxDph: 0.011 }), /maxDph/);
  assert.throws(() => validateOptions({ ...options, minFps: 29 }), /minFps/);
  assert.throws(() => validateOptions({ ...options, gpu: 'rtx-3060' }), /tesla-p4/);
  assert.throws(() => validateOptions({ ...options, sessionSec: 301 }), /sessionSec/);
  assert.throws(() => validateOptions({ ...options, instanceMaxAgeSec: 601 }), /instanceMaxAgeSec/);
});

test('on-demand fallback is allowed only with the P4 $0.02/h cap', () => {
  const od = validateOptions({ ...options, type: 'od', maxDph: 0.02 });
  assert.equal(selectPricing([p4Pricing], od).dph, 0.018);
  assert.throws(() => validateOptions({ ...od, maxDph: 0.021 }), /maxDph/);
});

test('default controller run is a side-effect-free pricing-file dry-run', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'soren91-powergpu-test-'));
  const fixture = path.join(directory, 'pricing.json');
  fs.writeFileSync(fixture, JSON.stringify({ data: [p4Pricing] }));
  try {
    const plan = await main(['--pricing-json', fixture]);
    assert.equal(plan.provider, 'powergpu');
    assert.equal(plan.tier, 0);
    assert.equal(plan.execute, false);
    assert.equal(plan.cost.dph, 0.009);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('paid launch fails closed when the pre-launch instance baseline is unavailable', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'soren91-powergpu-baseline-'));
  const fixture = path.join(directory, 'pricing.json');
  const fake = path.join(directory, 'powergpu');
  const launched = path.join(directory, 'launch-called');
  fs.writeFileSync(fixture, JSON.stringify({ data: [p4Pricing] }));
  fs.writeFileSync(fake, `#!/bin/sh\nif [ "$1" = "list" ]; then exit 1; fi\ntouch ${JSON.stringify(launched)}\nexit 0\n`, { mode: 0o755 });
  try {
    await assert.rejects(
      main(['--pricing-json', fixture, '--execute', '--image', options.image, '--powergpu-bin', fake]),
      /unable to establish PowerGPU instance baseline before launch/,
    );
    assert.equal(fs.existsSync(launched), false);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('successful launch without a usable id recovers only the attributable instance and destroys it', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'soren91-powergpu-no-id-'));
  const fixture = path.join(directory, 'pricing.json');
  const fake = path.join(directory, 'powergpu');
  const launched = path.join(directory, 'launched');
  const calls = path.join(directory, 'calls.log');
  fs.writeFileSync(fixture, JSON.stringify({ data: [p4Pricing] }));
  fs.writeFileSync(fake, `#!/bin/sh\necho "$*" >> ${JSON.stringify(calls)}\ncase "$1" in\n  list)\n    if [ -f ${JSON.stringify(launched)} ]; then\n      now=$(date +%s)\n      printf '{"instances":[{"id":"i-new7777","gpu_slug":"tesla-p4","image":"${options.image}","created_at":%s}]}\\n' "$now"\n    else\n      printf '{"instances":[]}\\n'\n    fi\n    ;;\n  launch)\n    touch ${JSON.stringify(launched)}\n    printf '{"status":"accepted"}\\n'\n    ;;\n  destroy) exit 0 ;;\n  *) exit 2 ;;\nesac\n`, { mode: 0o755 });
  try {
    await assert.rejects(
      main(['--pricing-json', fixture, '--execute', '--image', options.image, '--powergpu-bin', fake]),
      /launch response has no PowerGPU instance id/,
    );
    assert.match(fs.readFileSync(calls, 'utf8'), /destroy i-new7777 --yes/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
