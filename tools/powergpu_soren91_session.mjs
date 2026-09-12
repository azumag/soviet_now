#!/usr/bin/env node
import fs from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const POWERGPU_BANDWIDTH_USD_PER_GB = 0.01;
const POWERGPU_STORAGE_USD_PER_GB_MONTH = 0.08;

export function defaults(env = process.env) {
  return {
    execute: false,
    gpu: env.SOREN91_POWERGPU_GPU || 'tesla-p4',
    type: env.SOREN91_POWERGPU_TYPE || 'interruptible',
    maxDph: Number(env.SOREN91_POWERGPU_MAX_DPH || 0.01),
    sessionSec: Number(env.SOREN91_GPU_SESSION_SEC || 300),
    bootTimeoutSec: Number(env.SOREN91_GPU_BOOT_TIMEOUT_SEC || 180),
    instanceMaxAgeSec: Number(env.SOREN91_GPU_INSTANCE_MAX_AGE_SEC || 600),
    minFps: Number(env.SOREN91_GPU_MIN_FPS || 30),
    width: Number(env.SOREN91_GPU_WIDTH || 960),
    height: Number(env.SOREN91_GPU_HEIGHT || 540),
    videoMbps: Number(env.SOREN91_GPU_VIDEO_MBPS || 2),
    audioMbps: Number(env.SOREN91_GPU_AUDIO_MBPS || 0.16),
    diskGb: Number(env.SOREN91_POWERGPU_DISK_GB || 8),
    imageDownloadGb: Number(env.SOREN91_GPU_IMAGE_DOWNLOAD_GB || 1.2),
    image: env.SOREN91_GPU_IMAGE || '',
    pricingJson: '',
    powergpuBin: env.SOREN91_POWERGPU_BIN || 'powergpu',
    cleanupInstance: null,
    deadlineEpoch: null,
  };
}

export function parseArgs(argv, env = process.env) {
  const options = defaults(env);
  const takesValue = new Map([
    ['--gpu', 'gpu'], ['--type', 'type'], ['--max-dph', 'maxDph'],
    ['--session-sec', 'sessionSec'], ['--boot-timeout-sec', 'bootTimeoutSec'],
    ['--instance-max-age-sec', 'instanceMaxAgeSec'], ['--min-fps', 'minFps'],
    ['--width', 'width'], ['--height', 'height'], ['--disk-gb', 'diskGb'],
    ['--image', 'image'], ['--pricing-json', 'pricingJson'], ['--powergpu-bin', 'powergpuBin'],
    ['--cleanup-instance', 'cleanupInstance'], ['--deadline-epoch', 'deadlineEpoch'],
  ]);
  const strings = new Set(['gpu', 'type', 'image', 'pricingJson', 'powergpuBin', 'cleanupInstance']);
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--execute') { options.execute = true; continue; }
    const key = takesValue.get(arg);
    if (!key) throw new Error(`unknown argument: ${arg}`);
    const value = argv[++index];
    if (value == null) throw new Error(`${arg} requires a value`);
    options[key] = strings.has(key) ? value : Number(value);
  }
  return options;
}

export function validateOptions(options) {
  if (options.gpu !== 'tesla-p4') throw new Error('Tier 0 PoC GPU must be tesla-p4');
  if (!['interruptible', 'od'].includes(options.type)) throw new Error('type must be interruptible or od');
  const hardMax = options.type === 'interruptible' ? 0.01 : 0.02;
  if (!(options.maxDph > 0 && options.maxDph <= hardMax)) throw new Error(`maxDph must be <= ${hardMax}`);
  if (!Number.isInteger(options.sessionSec) || options.sessionSec < 60 || options.sessionSec > 300) throw new Error('sessionSec must be 60..300');
  if (!Number.isInteger(options.bootTimeoutSec) || options.bootTimeoutSec < 30 || options.bootTimeoutSec > 300) throw new Error('bootTimeoutSec must be 30..300');
  if (!Number.isInteger(options.instanceMaxAgeSec) || options.instanceMaxAgeSec < options.sessionSec || options.instanceMaxAgeSec > 600) throw new Error('instanceMaxAgeSec must be sessionSec..600');
  if (options.minFps !== 30) throw new Error('Tier 0 PoC minFps must be 30');
  if (options.diskGb !== 8) throw new Error('Tier 0 PoC diskGb must be 8');
  if (options.width !== 960 || options.height !== 540) throw new Error('Tier 0 PoC output must be 960x540');
  if (options.execute && !options.image) throw new Error('--execute requires --image or SOREN91_GPU_IMAGE');
  return options;
}

export function parseJsonOutput(output) {
  const text = String(output || '').trim();
  if (!text) throw new Error('empty JSON output');
  try { return JSON.parse(text); } catch {}
  for (const marker of ['{', '[']) {
    const start = text.indexOf(marker);
    if (start >= 0) {
      try { return JSON.parse(text.slice(start)); } catch {}
    }
  }
  throw new Error(`unable to parse JSON output: ${text.slice(0, 300)}`);
}

export function normalizePricing(value) {
  if (Array.isArray(value)) return value;
  for (const key of ['data', 'gpus', 'results']) {
    if (Array.isArray(value?.[key])) return value[key];
  }
  throw new Error('pricing response does not contain an array');
}

export function normalizeInstances(value) {
  if (Array.isArray(value)) return value;
  for (const key of ['instances', 'data', 'results']) {
    if (Array.isArray(value?.[key])) return value[key];
  }
  return [];
}

export function selectPricing(prices, options) {
  const entry = prices.find((candidate) => String(candidate.slug || candidate.gpu || '').toLowerCase() === options.gpu);
  if (!entry) return null;
  const rates = entry.price_per_gpu_hour || entry.prices || {};
  const dph = Number(options.type === 'interruptible'
    ? (rates.interruptible ?? entry.interruptible_price ?? entry.price_interruptible)
    : (rates.on_demand ?? entry.on_demand_price ?? entry.price_hr));
  if (!Number.isFinite(dph) || dph <= 0 || dph > options.maxDph) return null;
  const available = Number(entry.available_gpus ?? entry.available ?? 0);
  if (Number.isFinite(available) && available <= 0) return null;
  return { ...entry, dph };
}

export function estimateSessionCost(price, options) {
  const hours = options.instanceMaxAgeSec / 3600;
  const egressGb = ((options.videoMbps + options.audioMbps) * options.sessionSec / 8 / 1000) * 1.1;
  const compute = price.dph * hours;
  const network = (egressGb + options.imageDownloadGb) * POWERGPU_BANDWIDTH_USD_PER_GB;
  const storage = options.diskGb * POWERGPU_STORAGE_USD_PER_GB_MONTH * (options.instanceMaxAgeSec / (30 * 86400));
  return { total: compute + network + storage, compute, network, storage, egressGb, imageDownloadGb: options.imageDownloadGb, dph: price.dph };
}

export function buildLaunchArgs(options) {
  return [
    'launch', '--gpu', options.gpu, '--type', options.type,
    '--image', options.image, '--disk', String(options.diskGb),
    '--env', `SOREN91_POC_SESSION_SEC=${options.sessionSec}`,
    '--env', `SOREN91_POC_MIN_FPS=${options.minFps}`,
    '--env', `SOREN91_POC_WIDTH=${options.width}`,
    '--env', `SOREN91_POC_HEIGHT=${options.height}`,
    '--env', 'NVIDIA_DRIVER_CAPABILITIES=graphics,video,utility,display',
    '--json',
  ];
}

export function extractInstanceId(value) {
  if (typeof value === 'string') {
    const match = value.match(/\bi-[a-zA-Z0-9-]+\b/);
    if (match) return match[0];
  }
  const id = value?.id ?? value?.instance_id ?? value?.instance?.id;
  if (!id || !/^i-[a-zA-Z0-9-]+$/.test(String(id))) throw new Error('launch response has no PowerGPU instance id');
  return String(id);
}

export function extractLaunchPrice(value) {
  const price = Number(value?.price_hr ?? value?.price_per_hour ?? value?.instance?.price_hr);
  return Number.isFinite(price) && price > 0 ? price : null;
}

export function extractPocResult(logOutput) {
  const marker = 'SOREN91_POC_RESULT=';
  const lines = String(logOutput || '').split(/\r?\n/).filter((line) => line.includes(marker));
  if (!lines.length) return null;
  const payload = lines.at(-1).slice(lines.at(-1).indexOf(marker) + marker.length);
  try { return JSON.parse(payload); } catch { return null; }
}

function instanceId(value) {
  const id = value?.id ?? value?.instance_id ?? value?.instance?.id;
  return /^i-[a-zA-Z0-9-]+$/.test(String(id || '')) ? String(id) : null;
}

function instanceGpu(value) {
  return String(
    value?.gpu_slug ?? value?.gpu ?? value?.gpu_name
    ?? value?.machine?.gpu_slug ?? value?.machine?.gpu ?? value?.machine?.gpu_name ?? ''
  ).toLowerCase();
}

function instanceImage(value) {
  return String(
    value?.image ?? value?.image_ref ?? value?.container_image
    ?? value?.instance?.image ?? value?.instance?.image_ref ?? ''
  );
}

function instanceCreatedEpoch(value) {
  const raw = value?.created_at ?? value?.created ?? value?.createdAt ?? value?.instance?.created_at;
  if (raw == null) return null;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw > 1e12 ? Math.floor(raw / 1000) : Math.floor(raw);
  const parsed = Date.parse(String(raw));
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

export function findRecoverableLaunch(beforeIds, currentValue, options, creationStartedEpoch) {
  const candidates = normalizeInstances(currentValue).filter((value) => {
    const id = instanceId(value);
    return id && !beforeIds.has(id);
  });
  // 同時に別instanceも増えている場合は、どれがこのcontroller由来か断定できないため触らない。
  if (candidates.length !== 1) return null;

  const value = candidates[0];
  const gpu = instanceGpu(value);
  const image = instanceImage(value);
  const created = instanceCreatedEpoch(value);
  const gpuMatches = Boolean(gpu) && /(?:tesla[- _]?p4|\bp4\b)/i.test(gpu);
  const imageMatches = Boolean(image) && image === options.image;
  const recentEnough = created == null || created >= creationStartedEpoch - 5;
  // 回収対象は常にGPUとimageの両方が一致し、created_atがある場合は今回のlaunch時刻にも一致するものだけ。
  return gpuMatches && imageMatches && recentEnough ? instanceId(value) : null;
}

function run(bin, args, { timeout = 30_000, allowFailure = false } = {}) {
  const result = spawnSync(bin, args, { encoding: 'utf8', timeout });
  if (result.error) throw result.error;
  if (result.status !== 0 && !allowFailure) {
    throw new Error(`${bin} ${args.slice(0, 4).join(' ')} failed: ${String(result.stderr || result.stdout).trim()}`);
  }
  return { status: result.status, stdout: String(result.stdout || ''), stderr: String(result.stderr || '') };
}

async function loadPricing(options) {
  if (options.pricingJson) return JSON.parse(fs.readFileSync(options.pricingJson, 'utf8'));
  const response = await fetch('https://powergpu.io/v1/gpus/pricing', { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error(`PowerGPU pricing API failed: HTTP ${response.status}`);
  return response.json();
}

function listInstances(options) {
  const result = run(options.powergpuBin, ['list', '--json'], { timeout: 30_000, allowFailure: true });
  if (result.status !== 0) return null;
  try { return parseJsonOutput(result.stdout); } catch { return null; }
}

async function destroyInstance(options, targetInstanceId) {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const result = run(options.powergpuBin, ['destroy', targetInstanceId, '--yes'], { timeout: 30_000, allowFailure: true });
      if (result.status === 0 || /(?:not found|does not exist|404)/i.test(`${result.stdout}\n${result.stderr}`)) return;
      throw new Error(`destroy ${targetInstanceId} failed: ${String(result.stderr || result.stdout).trim()}`);
    } catch (error) {
      lastError = error;
      await sleep(2_000 * attempt);
    }
  }
  throw lastError || new Error(`failed to destroy ${targetInstanceId}`);
}

function launchCleanupWatchdog(options, targetInstanceId, deadlineEpoch) {
  const cleanupLog = `/tmp/soren91-powergpu-cleanup-${targetInstanceId}.log`;
  const output = fs.openSync(cleanupLog, 'a');
  const child = spawn(process.execPath, [
    fileURLToPath(import.meta.url),
    '--cleanup-instance', targetInstanceId,
    '--deadline-epoch', String(deadlineEpoch),
    '--powergpu-bin', options.powergpuBin,
  ], {
    detached: true,
    stdio: ['ignore', output, output],
    env: process.env,
  });
  child.unref();
  fs.closeSync(output);
}

async function cleanupMode(options) {
  if (!options.cleanupInstance || !Number.isFinite(options.deadlineEpoch)) throw new Error('cleanup mode requires instance and deadline');
  const delay = Math.max(0, options.deadlineEpoch * 1000 - Date.now());
  await sleep(delay);
  await destroyInstance(options, options.cleanupInstance);
}

function instanceState(value) {
  return String(value?.status ?? value?.state ?? value?.instance?.status ?? '').toLowerCase();
}

export async function main(argv = process.argv.slice(2)) {
  const options = validateOptions(parseArgs(argv));
  if (options.cleanupInstance) return cleanupMode(options);

  const pricing = selectPricing(normalizePricing(await loadPricing(options)), options);
  if (!pricing) throw new Error(`PowerGPU ${options.gpu}/${options.type} is unavailable or exceeds $${options.maxDph}/h`);

  const plan = {
    provider: 'powergpu', tier: 0, execute: options.execute,
    gpu: options.gpu, type: options.type, availableGpus: pricing.available_gpus ?? null,
    cost: estimateSessionCost(pricing, options),
    limits: { maxDph: options.maxDph, minFps: options.minFps, sessionSec: options.sessionSec, instanceMaxAgeSec: options.instanceMaxAgeSec },
  };
  console.log(JSON.stringify(plan, null, 2));
  if (!options.execute) return plan;

  const creationStartedEpoch = Math.floor(Date.now() / 1000);
  const deadlineEpoch = creationStartedEpoch + options.instanceMaxAgeSec;
  const beforeValue = listInstances(options);
  if (beforeValue == null) throw new Error('unable to establish PowerGPU instance baseline before launch');
  const beforeIds = new Set(normalizeInstances(beforeValue).map(instanceId).filter(Boolean));
  let targetInstanceId = null;
  try {
    let launchedRaw;
    try {
      // Custom image cold-pullを考慮し、CLI応答待ちは4分まで許容する。
      // 課金instance自体のhard deadlineはcreationStartedEpochから600秒のまま。
      launchedRaw = run(options.powergpuBin, buildLaunchArgs(options), { timeout: 240_000 }).stdout;
    } catch (launchError) {
      const afterValue = listInstances(options);
      targetInstanceId = afterValue
        ? findRecoverableLaunch(beforeIds, afterValue, options, creationStartedEpoch)
        : null;
      if (!targetInstanceId) {
        console.error('PowerGPU launch failed before an instance id was returned; no uniquely attributable new PoC instance was found. Check `powergpu list` before retrying.');
      }
      throw launchError;
    }

    let launched;
    try {
      try { launched = parseJsonOutput(launchedRaw); } catch { launched = launchedRaw; }
      targetInstanceId = extractInstanceId(launched);
    } catch (launchResponseError) {
      const afterValue = listInstances(options);
      targetInstanceId = afterValue
        ? findRecoverableLaunch(beforeIds, afterValue, options, creationStartedEpoch)
        : null;
      if (!targetInstanceId) {
        console.error('PowerGPU launch returned no usable instance id; no uniquely attributable new PoC instance was found. Check `powergpu list` before retrying.');
      }
      throw launchResponseError;
    }
    const lockedDph = extractLaunchPrice(typeof launched === 'string' ? null : launched);
    if (lockedDph != null && lockedDph > options.maxDph) {
      await destroyInstance(options, targetInstanceId).catch(() => {});
      throw new Error(`launched rate $${lockedDph}/h exceeds hard cap $${options.maxDph}/h`);
    }
    launchCleanupWatchdog(options, targetInstanceId, deadlineEpoch);

    const bootDeadline = Math.min(Date.now() + options.bootTimeoutSec * 1000, deadlineEpoch * 1000);
    let running = false;
    while (Date.now() < bootDeadline) {
      const currentValue = listInstances(options);
      if (currentValue) {
        const current = normalizeInstances(currentValue).find((candidate) => instanceId(candidate) === targetInstanceId);
        const state = instanceState(current);
        if (state === 'running') { running = true; break; }
        if (['failed', 'destroyed', 'error'].includes(state)) throw new Error(`instance failed during boot: ${state}`);
      }
      await sleep(3_000);
    }
    if (!running) throw new Error('instance did not reach running before boot timeout');

    let result = null;
    while (Date.now() < deadlineEpoch * 1000) {
      const logs = run(options.powergpuBin, ['logs', targetInstanceId], { timeout: 30_000, allowFailure: true });
      result = extractPocResult(`${logs.stdout}\n${logs.stderr}`);
      if (result) break;
      await sleep(5_000);
    }
    if (!result) throw new Error('PoC result not received before hard deadline');
    console.log(JSON.stringify({ provider: 'powergpu', instanceId: targetInstanceId, result }, null, 2));
    if (!result.pass) process.exitCode = 1;
    return result;
  } finally {
    if (targetInstanceId) await destroyInstance(options, targetInstanceId);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}
