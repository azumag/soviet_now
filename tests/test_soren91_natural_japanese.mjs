import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeNaturalJapanese, OVERPOLITE_FORMS } from '../soren91/natural_japanese.mjs';

test('collapses the over-polite forms seen in the commentary log', () => {
  assert.equal(
    normalizeNaturalJapanese(
      '第四試合のお時間でございます。正直、乗り気ではございませんが、資本主義の底力は惜しみなく発揮してまいります。'),
    '第四試合のお時間です。正直、乗り気ではありませんが、資本主義の底力は惜しみなく発揮してまいります。',
  );
  assert.equal(
    normalizeNaturalJapanese(
      '積み上がりにまだ十分なゆとりがございますので、安心して見守ることができる状況でございます。'),
    '積み上がりにまだ十分なゆとりがありますので、安心して見守ることができる状況です。',
  );
  assert.equal(normalizeNaturalJapanese('ただいまでございまして、次は参ります。'),
    'ただいまでして、次は参ります。');
  assert.equal(normalizeNaturalJapanese('昨日は雨でございました。'), '昨日は雨でした。');
  assert.equal(normalizeNaturalJapanese('手元に材料がございます。'), '手元に材料があります。');
});

test('leaves ordinary greetings and fixed phrases alone', () => {
  for (const text of ['ありがとうございます。', '申し訳ございません。', 'おはようございます。']) {
    assert.equal(normalizeNaturalJapanese(text), text);
  }
  assert.equal(normalizeNaturalJapanese(''), '');
  assert.equal(normalizeNaturalJapanese(null), null);
  assert.equal(normalizeNaturalJapanese(undefined), undefined);
});

test('is idempotent and exposes the guarded forms', () => {
  const sample = 'ゆとりがございますので、状況でございます。';
  const once = normalizeNaturalJapanese(sample);
  assert.equal(normalizeNaturalJapanese(once), once);
  assert.ok(OVERPOLITE_FORMS.includes('でございます'));
  assert.ok(!once.includes('でございます'));
});
