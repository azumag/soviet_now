import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// 履歴は cwd 相対 (tmp/soren91_comment_history.json) なので一時ディレクトリで実行する。
const dir = mkdtempSync(join(tmpdir(), 'soren91-comment-variation-'));
process.chdir(dir);

const { commentOpening, appendCommentHistory, hasDuplicateOpening, computeBoardDanger } = await import(
  '../soren91/comment.mjs'
);

test.after(() => {
  try { process.chdir('/'); } catch {}
  rmSync(dir, { recursive: true, force: true });
});

test('commentOpening takes the first sentence without the delimiter', () => {
  assert.equal(commentOpening('今回は3位です。次は頑張ります。'), '今回は3位です');
  assert.equal(commentOpening('危険です！積み上がりました。'), '危険です');
  assert.equal(commentOpening('\n\nまずはご挨拶です。'), 'まずはご挨拶です');
  assert.equal(commentOpening(''), '');
});

test('history dedup detects a repeated opening and allows a new one', () => {
  appendCommentHistory('ranking_comment', 1, '資本主義の勝利です！今回は1位でした。');
  assert.equal(hasDuplicateOpening('資本主義の勝利です。次もいきます。', 'ranking_comment'), true);
  assert.equal(hasDuplicateOpening('静かに見ていました。今日は3位です。', 'ranking_comment'), false);
});

test('history file is a JSON array under tmp/', () => {
  const path = join(dir, 'tmp', 'soren91_comment_history.json');
  const data = JSON.parse(readFileSync(path, 'utf-8'));
  assert.ok(Array.isArray(data));
  assert.ok(data.length >= 1);
  assert.equal(typeof data[0].text, 'string');
  assert.equal(data[0].kind, 'ranking_comment');
});

test('computeBoardDanger maps board height to danger levels', () => {
  assert.equal(computeBoardDanger({ pieces: [{ x: 0, y: 0, r: 0.3, type: 1 }] }).dangerLevel, '安全');
  assert.equal(
    computeBoardDanger({ pieces: [{ x: 0, y: 2.0, r: 0.4, type: 1 }] }).dangerLevel,
    '危険が迫っている',
  );
  assert.equal(
    computeBoardDanger({ pieces: [{ x: 0, y: 3.0, r: 0.4, type: 1 }] }).dangerLevel,
    '瀕死',
  );
  // ゴーストピース(UI誤検出)は危険度に数えない
  assert.equal(
    computeBoardDanger({ pieces: [{ x: -3.27, y: 3.25, r: 0.3, type: 7 }] }).dangerLevel,
    '安全',
  );
});
