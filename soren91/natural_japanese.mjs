/**
 * Collapse the over-polite forms the commentary model drifts into.
 *
 * The commentary prompts require です・ます, but models often over-correct to
 * 「〜でございます」. On air that reads as stiff, unnatural Japanese, so the
 * generated beat/midgame/ranking text is normalized before it is logged or
 * queued for speech. Fixed greetings such as ありがとうございます and
 * 申し訳ございません are intentionally left alone (they are ordinary speech).
 */
const RULES = [
  [/ではございません/g, 'ではありません'],
  [/でございません/g, 'ではありません'],
  [/でございまして/g, 'でして'],
  [/でございました/g, 'でした'],
  [/でございます/g, 'です'],
  [/にございます/g, 'にあります'],
  [/がございます/g, 'があります'],
  [/はございます/g, 'はあります'],
  [/もございます/g, 'もあります'],
  [/ております/g, 'ています'],
];

export const OVERPOLITE_FORMS = Object.freeze([
  'ではございません',
  'でございません',
  'でございまして',
  'でございました',
  'でございます',
  'にございます',
  'がございます',
  'はございます',
  'もございます',
  'ております',
]);

export function normalizeNaturalJapanese(text) {
  if (typeof text !== 'string' || text.length === 0) return text;
  let out = text;
  for (const [pattern, replacement] of RULES) out = out.replace(pattern, replacement);
  return out;
}
