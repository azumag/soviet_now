import test from 'node:test';
import assert from 'node:assert/strict';
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const {
  copyEvidence,
  formatPrMarker,
  isAllowedEvidenceRelative,
  listEvidenceFiles,
  parseStrategyCode,
  summarizeBoardState,
} = await import('../soren91/daily_runtime_improve.mjs');

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-runtime-evidence-'));
  mkdirSync(join(dir, 'game_history'), { recursive: true });
  mkdirSync(join(dir, 'tmp', 'summaries'), { recursive: true });
  mkdirSync(join(dir, 'tmp', 'strategy_snapshots'), { recursive: true });
  mkdirSync(join(dir, 'tmp', 'game_screenshots', 'game_0001'), { recursive: true });
  writeFileSync(join(dir, 'game_history', 'game_0001.jsonl'), '{"turn":1}\n');
  writeFileSync(join(dir, 'tmp', 'summaries', 'game_0001.json'), '{"rank":12,"turns":30}\n');
  writeFileSync(join(dir, 'tmp', 'strategy_snapshots', 'game_0001_strategy.mjs'), 'export function decide(){return {x:0,reason:"x"}}\n');
  writeFileSync(join(dir, 'tmp', 'game_screenshots', 'game_0001', 'turn_0001.png'), Buffer.from('png-fixture'));
  return dir;
}

test('evidence allowlist excludes secrets and unrelated runtime files', () => {
  assert.equal(isAllowedEvidenceRelative('game_history/game_0012.jsonl'), true);
  assert.equal(isAllowedEvidenceRelative('game_history/latest_0012.jsonl'), true);
  assert.equal(isAllowedEvidenceRelative('tmp/summaries/game_0012.json'), true);
  assert.equal(isAllowedEvidenceRelative('tmp/summaries/ranking_0012.png'), true);
  assert.equal(isAllowedEvidenceRelative('tmp/game_screenshots/game_0012/turn_0042.png'), true);
  assert.equal(isAllowedEvidenceRelative('tmp/strategy_snapshots/game_0012_strategy.mjs'), true);
  assert.equal(isAllowedEvidenceRelative('.env'), false);
  assert.equal(isAllowedEvidenceRelative('tmp/soren91.log'), false);
  assert.equal(isAllowedEvidenceRelative('strategy.mjs'), false);
});

test('bounded evidence copy preserves only allowlisted files', () => {
  const runtime = fixture();
  const work = mkdtempSync(join(tmpdir(), 'soren91-runtime-copy-'));
  try {
    writeFileSync(join(runtime, 'game_history', 'ignore.txt'), 'secret-ish text');
    const manifest = copyEvidence(runtime, work);
    assert.equal(manifest.files.length, 4);
    assert.deepEqual(
      manifest.files.map(item => item.rel),
      [
        'game_history/game_0001.jsonl',
        'tmp/game_screenshots/game_0001/turn_0001.png',
        'tmp/strategy_snapshots/game_0001_strategy.mjs',
        'tmp/summaries/game_0001.json',
      ],
    );
  } finally {
    rmSync(runtime, { recursive: true, force: true });
    rmSync(work, { recursive: true, force: true });
  }
});

test('evidence collector fails closed on symlinks', () => {
  const runtime = fixture();
  try {
    symlinkSync('/etc/passwd', join(runtime, 'game_history', 'game_0002.jsonl'));
    assert.throws(() => listEvidenceFiles(runtime), /evidence_symlink/);
  } finally {
    rmSync(runtime, { recursive: true, force: true });
  }
});

test('evidence collector enforces total and per-file limits', () => {
  const runtime = fixture();
  try {
    assert.throws(() => listEvidenceFiles(runtime, { maxFiles: 2 }), /evidence_file_limit/);
    assert.throws(() => listEvidenceFiles(runtime, { maxFileBytes: 2 }), /evidence_file_too_large/);
    assert.throws(() => listEvidenceFiles(runtime, { maxTotalBytes: 2 }), /evidence_total_limit|evidence_file_too_large/);
  } finally {
    rmSync(runtime, { recursive: true, force: true });
  }
});

test('strategy parser accepts a complete code block and rejects prose', () => {
  const code = 'export function decide(boardState) { return { x: 0, reason: "safe" }; }';
  assert.equal(parseStrategyCode(`note\n\`\`\`javascript\n${code}\n\`\`\``), code);
  assert.equal(parseStrategyCode('no strategy here'), null);
});

test('snapshot summary exposes only bounded board facts', () => {
  const summary = summarizeBoardState(
    'tmp/game_screenshots/game_0001/turn_0010.png',
    { confidence: 0.83, method: 'walls' },
    {
      state: 'MOVE',
      perception: { reason: 'stable' },
      confidence: 0.76,
      pieces: [{ type: 1, y: -3 }, { type: 4, y: 1.234 }],
      nextPieces: [{ type: 2 }, null, { type: 5 }],
      hold: { type: 3 },
      holdKnownEmpty: false,
      garbage: { ratio: 0.2, height: -1, gauge: 0.4 },
    },
  );
  assert.deepEqual(summary.next, [2, null, 5]);
  assert.equal(summary.pieces, 2);
  assert.equal(summary.maxY, 1.23);
  assert.equal(summary.hold, 3);
  assert.equal(summary.perceptionReason, 'stable');
});

test('PR marker stays compatible with the existing reconcile contract', () => {
  assert.equal(formatPrMarker(12, 34), '<!-- improve-daily: from=12 to=34 -->');
});
