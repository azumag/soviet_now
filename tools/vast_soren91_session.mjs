#!/usr/bin/env node
import fs from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function defaults(env = process.env) {
  return {
    execute: false,
    market: env.SOREN91_VAST_MARKET || 'on-demand',
    maxDph: Number(env.SOREN91_VAST_MAX_DPH || 0.08),
    maxInetDownCost: Number(env.SOREN91_VAST_MAX_INET_DOWN_COST || 0.02),
    maxInetUpCost: Number(env.SOREN91_VAST_MAX_INET_UP_COST || 0.02),
    sessionSec: Number(env.SOREN91_GPU_SESSION_SEC || 300),
    bootTimeoutSec: Number(env.SOREN91_GPU_BOOT_TIMEOUT_SEC || 180),
    instanceMaxAgeSec: Number(env.SOREN91_GPU_INSTANCE_MAX_AGE_SEC || 600),
    minFps: Number(env.SOREN91_GPU_MIN_FPS || 25),
    width: Number(env.SOREN91_GPU_WIDTH || 960),
    height: Number(env.SOREN91_GPU_HEIGHT || 540),
    videoMbps: Number(env.SOREN91_GPU_VIDEO_MBPS || 2),
    audioMbps: Number(env.SOREN91_GPU_AUDIO_MBPS || 0.16),
    diskGb: Number(env.SOREN91_VAST_DISK_GB || 20),
    imageDownloadGb: Number(env.SOREN91_VAST_IMAGE_DOWNLOAD_GB || 1.2),
    image: env.SOREN91_GPU_IMAGE || '',
    offerJson: '',
    vastaiBin: env.SOREN91_VASTAI_BIN || 'vastai',
    cleanupInstance: null,
    deadlineEpoch: null,
  };
}

export function parseArgs(argv, env = process.env) {
  const options = defaults(env);
  const takesValue = new Map([
    ['--market', 'market'], ['--max-dph', 'maxDph'], ['--max-inet-down-cost', 'maxInetDownCost'],
    ['--max-inet-up-cost', 'maxInetUpCost'],
    ['--session-sec', 'sessionSec'], ['--boot-timeout-sec', 'bootTimeoutSec'],
    ['--instance-max-age-sec', 'instanceMaxAgeSec'], ['--min-fps', 'minFps'],
    ['--width', 'width'], ['--height', 'height'], ['--disk-gb', 'diskGb'],
    ['--image', 'image'], ['--offer-json', 'offerJson'], ['--vastai-bin', 'vastaiBin'],
    ['--cleanup-instance', 'cleanupInstance'], ['--deadline-epoch', 'deadlineEpoch'],
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--execute') { options.execute = true; continue; }
    const key = takesValue.get(arg);
    if (!key) throw new Error(`unknown argument: ${arg}`);
    const value = argv[++index];
    if (value == null) throw new Error(`${arg} requires a value`);
    options[key] = ['market', 'image', 'offerJson', 'vastaiBin'].includes(key) ? value : Number(value);
  }
  return options;
}

export function validateOptions(options) {
  if (options.market !== 'on-demand') throw new Error('PoC market must be on-demand');
  if (!(options.maxDph > 0 && options.maxDph <= 0.08)) throw new Error('maxDph must be <= 0.08');
  if (!(options.maxInetDownCost >= 0 && options.maxInetDownCost <= 0.02)) throw new Error('maxInetDownCost must be <= 0.02');
  if (!(options.maxInetUpCost >= 0 && options.maxInetUpCost <= 0.02)) throw new Error('maxInetUpCost must be <= 0.02');
  if (!Number.isInteger(options.sessionSec) || options.sessionSec < 60 || options.sessionSec > 300) throw new Error('sessionSec must be 60..300');
  if (!Number.isInteger(options.bootTimeoutSec) || options.bootTimeoutSec < 60 || options.bootTimeoutSec > 300) throw new Error('bootTimeoutSec must be 60..300');
  if (!Number.isInteger(options.instanceMaxAgeSec) || options.instanceMaxAgeSec < options.sessionSec || options.instanceMaxAgeSec > 600) throw new Error('instanceMaxAgeSec must be sessionSec..600');
  if (options.minFps !== 25) throw new Error('PoC minFps must be 25');
  if (options.diskGb !== 20) throw new Error('PoC diskGb must be 20');
  if (options.imageDownloadGb !== 1.2) throw new Error('PoC imageDownloadGb must be 1.2');
  if (options.width !== 960 || options.height !== 540) throw new Error('PoC output must be 960x540');
  if (options.execute && !options.image) throw new Error('--execute requires --image or SOREN91_GPU_IMAGE');
  return options;
}

export function buildSearchQuery(options) {
  return [
    'num_gpus=1', 'cpu_arch=amd64', 'compute_cap>=610', 'gpu_ram>=4',
    'cpu_cores_effective>=2', 'cpu_ram>=8', `disk_space>=${options.diskGb}`,
    'reliability>=0.98', 'verified=True', 'rentable=True', 'rented=False',
    'inet_down>=100', 'inet_up>=20', `inet_down_cost<=${options.maxInetDownCost}`,
    `inet_up_cost<=${options.maxInetUpCost}`, `dph<=${options.maxDph}`,
  ].join(' ');
}

export function parseJsonOutput(output) {
  const text = String(output || '').trim();
  try { return JSON.parse(text); } catch {}
  for (const marker of ['[', '{']) {
    const start = text.indexOf(marker);
    if (start >= 0) {
      try { return JSON.parse(text.slice(start)); } catch {}
    }
  }
  throw new Error(`unable to parse JSON output: ${text.slice(0, 300)}`);
}

export function normalizeOffers(value) {
  if (Array.isArray(value)) return value;
  for (const key of ['offers', 'results', 'instances']) {
    if (Array.isArray(value?.[key])) return value[key];
  }
  throw new Error('offer response does not contain an array');
}

export function estimateSessionCost(offer, options) {
  const hours = options.instanceMaxAgeSec / 3600;
  const dph = Number(offer.dph ?? offer.dph_total);
  const egressGb = ((options.videoMbps + options.audioMbps) * options.sessionSec / 8 / 1000) * 1.1;
  const compute = dph * hours;
  const networkUp = Number(offer.inet_up_cost || 0) * egressGb;
  const networkDown = Number(offer.inet_down_cost || 0) * options.imageDownloadGb;
  const network = networkUp + networkDown;
  const storage = Number(offer.storage_cost || 0) * options.diskGb * (options.instanceMaxAgeSec / (30 * 86400));
  return { total: compute + network + storage, compute, network, networkUp, networkDown, storage, egressGb, imageDownloadGb: options.imageDownloadGb, dph };
}

export function selectOffer(offers, options) {
  const acceptable = offers.filter((offer) => {
    const dph = Number(offer.dph ?? offer.dph_total);
    return Number.isFinite(dph) && dph <= options.maxDph
      && Number(offer.inet_down_cost || 0) <= options.maxInetDownCost
      && Number(offer.inet_up_cost || 0) <= options.maxInetUpCost
      && Number(offer.reliability || 0) >= 0.98
      && offer.verified !== false && offer.rentable !== false && offer.rented !== true;
  });
  acceptable.sort((left, right) => estimateSessionCost(left, options).total - estimateSessionCost(right, options).total);
  return acceptable[0] || null;
}

export function extractContractId(value) {
  if (typeof value === 'string') {
    const match = value.match(/(?:new_contract|instance_id|\bid\b)["']?\s*[:=]\s*(\d+)/);
    if (match) return Number(match[1]);
  }
  const id = Number(value?.new_contract ?? value?.instance_id ?? value?.id);
  if (!Number.isInteger(id) || id <= 0) throw new Error('create response has no instance id');
  return id;
}

export function extractPocResult(logOutput) {
  const marker = 'SOREN91_POC_RESULT=';
  const lines = String(logOutput || '').split(/\r?\n/).filter((line) => line.includes(marker));
  if (!lines.length) return null;
  const payload = lines.at(-1).slice(lines.at(-1).indexOf(marker) + marker.length);
  try { return JSON.parse(payload); } catch { return null; }
}

function run(bin, args, { timeout = 30_000, allowFailure = false } = {}) {
  const result = spawnSync(bin, args, { encoding: 'utf8', timeout });
  if (result.error) throw result.error;
  if (result.status !== 0 && !allowFailure) throw new Error(`${bin} ${args.slice(0, 3).join(' ')} failed: ${String(result.stderr || result.stdout).trim()}`);
  return { status: result.status, stdout: String(result.stdout || ''), stderr: String(result.stderr || '') };
}

export function buildCreateArgs(offer, options, label) {
  const offerId = Number(offer.id ?? offer.ask_contract_id);
  if (!Number.isInteger(offerId) || offerId <= 0) throw new Error('selected offer has no id');
  const env = [
    `-e SOREN91_POC_SESSION_SEC=${options.sessionSec}`,
    `-e SOREN91_POC_MIN_FPS=${options.minFps}`,
    `-e SOREN91_POC_WIDTH=${options.width}`,
    `-e SOREN91_POC_HEIGHT=${options.height}`,
    '-e NVIDIA_DRIVER_CAPABILITIES=graphics,video,utility,display',
  ].join(' ');
  return ['create', 'instance', String(offerId), '--image', options.image, '--disk', String(options.diskGb), '--label', label, '--env', env, '--cancel-unavail', '--raw'];
}

async function destroyInstance(options, instanceId) {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const result = run(options.vastaiBin, ['destroy', 'instance', String(instanceId), '--raw'], { timeout: 30_000, allowFailure: true });
      if (result.status === 0 || /(?:not found|does not exist|404)/i.test(`${result.stdout}\n${result.stderr}`)) return;
      throw new Error(`destroy instance ${instanceId} failed: ${String(result.stderr || result.stdout).trim()}`);
    } catch (error) {
      lastError = error;
      await sleep(2_000 * attempt);
    }
  }
  throw lastError || new Error(`failed to destroy instance ${instanceId}`);
}

function findInstanceByLabel(options, label) {
  const shown = run(options.vastaiBin, ['show', 'instances', '--raw'], { timeout: 30_000, allowFailure: true });
  if (shown.status !== 0) return null;
  try {
    const values = normalizeOffers(parseJsonOutput(shown.stdout));
    const match = values.find((value) => String(value.label || '') === label);
    const id = Number(match?.id ?? match?.instance_id);
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

function launchCleanupWatchdog(options, instanceId, deadlineEpoch) {
  const cleanupLog = `/tmp/soren91-vast-cleanup-${instanceId}.log`;
  const output = fs.openSync(cleanupLog, 'a');
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--cleanup-instance', String(instanceId), '--deadline-epoch', String(deadlineEpoch), '--vastai-bin', options.vastaiBin], {
    detached: true,
    stdio: ['ignore', output, output],
    env: process.env,
  });
  child.unref();
  fs.closeSync(output);
}

async function cleanupMode(options) {
  if (!Number.isInteger(options.cleanupInstance) || !Number.isFinite(options.deadlineEpoch)) throw new Error('cleanup mode requires instance and deadline');
  const delay = Math.max(0, options.deadlineEpoch * 1000 - Date.now());
  await sleep(delay);
  await destroyInstance(options, options.cleanupInstance);
}

export async function main(argv = process.argv.slice(2)) {
  const options = validateOptions(parseArgs(argv));
  if (options.cleanupInstance) return cleanupMode(options);

  let offerValue;
  if (options.offerJson) offerValue = JSON.parse(fs.readFileSync(options.offerJson, 'utf8'));
  else {
    const search = run(options.vastaiBin, ['search', 'offers', buildSearchQuery(options), '--storage', String(options.diskGb), '--order', 'dph', '--raw', '--type', options.market]);
    offerValue = parseJsonOutput(search.stdout);
  }
  const offer = selectOffer(normalizeOffers(offerValue), options);
  if (!offer) throw new Error('no offer satisfies the fixed PoC limits');
  const plan = { execute: options.execute, offerId: offer.id ?? offer.ask_contract_id, gpu: offer.gpu_name, cost: estimateSessionCost(offer, options), limits: { maxDph: options.maxDph, maxInetDownCost: options.maxInetDownCost, maxInetUpCost: options.maxInetUpCost, sessionSec: options.sessionSec, instanceMaxAgeSec: options.instanceMaxAgeSec } };
  console.log(JSON.stringify(plan, null, 2));
  if (!options.execute) return plan;

  const label = `soren91-poc-${Date.now()}`;
  const creationStartedEpoch = Math.floor(Date.now() / 1000);
  const deadlineEpoch = creationStartedEpoch + options.instanceMaxAgeSec;
  let instanceId = null;
  try {
    const createOutput = run(options.vastaiBin, buildCreateArgs(offer, options, label), { timeout: 60_000 }).stdout;
    try { instanceId = extractContractId(parseJsonOutput(createOutput)); }
    catch { instanceId = extractContractId(createOutput); }
  } catch (error) {
    instanceId = findInstanceByLabel(options, label);
    if (instanceId) await destroyInstance(options, instanceId).catch(() => {});
    throw error;
  }
  launchCleanupWatchdog(options, instanceId, deadlineEpoch);
  let result = null;
  try {
    const bootDeadline = Math.min(Date.now() + options.bootTimeoutSec * 1000, deadlineEpoch * 1000);
    let running = false;
    while (Date.now() < bootDeadline) {
      const shown = parseJsonOutput(run(options.vastaiBin, ['show', 'instance', String(instanceId), '--raw'], { allowFailure: true }).stdout || '{}');
      if (shown.actual_status === 'running') { running = true; break; }
      if (['exited', 'offline', 'unknown'].includes(shown.actual_status)) throw new Error(`instance failed during boot: ${shown.actual_status}`);
      await sleep(5_000);
    }
    if (!running) throw new Error('instance did not reach running before boot timeout');
    while (Date.now() < deadlineEpoch * 1000) {
      const logs = run(options.vastaiBin, ['logs', String(instanceId), '--tail', '300'], { allowFailure: true }).stdout;
      result = extractPocResult(logs);
      if (result) break;
      await sleep(5_000);
    }
    if (!result) throw new Error('PoC result not received before hard deadline');
    console.log(JSON.stringify({ instanceId, result }, null, 2));
    if (!result.pass) process.exitCode = 1;
    return result;
  } finally {
    await destroyInstance(options, instanceId);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  main().catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}
