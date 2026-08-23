/** Authoritative user-facing country names for Soviet Game commentary. */
export const COUNTRY_NAMES = Object.freeze({
  1: 'アルメニア', 2: 'モルドバ', 3: 'エストニア', 4: 'ラトビア',
  5: 'リトアニア', 6: 'ジョージア', 7: 'アゼルバイジャン', 8: 'タジキスタン',
  9: 'キルギス', 10: 'ベラルーシ', 11: 'ウズベキスタン', 12: 'トルクメニスタン',
  13: 'ウクライナ', 14: 'カザフスタン', 15: 'ロシア', 16: 'ソ連',
});

const INTERNAL_COUNTRY_RE = /(?<![A-Za-z])(?:[Tt]ype|[Tt]|タイプ)\s*-?\s*(\d{1,2})(?!\d|[A-Za-z_]|\.[\dA-Za-z_])/gu;
const INTERNAL_COUNTRY_PAIR_RE = /(?<![A-Za-z])(?:[Tt]ype|[Tt]|タイプ)\s*-?\s*(\d{1,2})\s*\/\s*(\d{1,2})(?!\d|[A-Za-z_]|\.[\dA-Za-z_])/gu;
const INTERNAL_COUNTRY_COUNT_RE = /(?<![A-Za-z])(?:[Tt]ype|[Tt]|タイプ)\s*-?\s*(\d{1,2})\s*(?:[xX×*])\s*(\d+)(?!\d|[A-Za-z_])/gu;
const BEST_COUNTRY_KEY_RE = /(?<![A-Za-z])(?:max_piece|max|best_max|best|source_best)_type\s*[:=]\s*(\d{1,2})(?!\d|[A-Za-z_])/giu;
const HIGH_COUNTRY_COUNTS_LABEL_RE = /(?<![A-Za-z])high_type_counts\s*[:=]\s*/giu;
const STAGE_TARGET_KEY_RE = /(?<![A-Za-z])(?:stage_target|target_type)\s*[:=]\s*(\d{1,2})(?!\d|[A-Za-z_])/giu;
const REASON_COUNTRY_TOKEN_RE = /(?<![A-Za-z0-9])[Tt](1[0-6]|[1-9])(?=_|$)/gu;

export function normalizeCountryReferences(text) {
  return String(text || '')
    .replace(BEST_COUNTRY_KEY_RE, (match, rawType) => (
      COUNTRY_NAMES[Number(rawType)] ? `最高国=${COUNTRY_NAMES[Number(rawType)]}` : match
    ))
    .replace(STAGE_TARGET_KEY_RE, (match, rawType) => (
      COUNTRY_NAMES[Number(rawType)] ? `対象国=${COUNTRY_NAMES[Number(rawType)]}` : match
    ))
    .replace(HIGH_COUNTRY_COUNTS_LABEL_RE, '終盤の国別個数=')
    .replace(INTERNAL_COUNTRY_PAIR_RE, (match, leftType, rightType) => {
      const left = COUNTRY_NAMES[Number(leftType)];
      const right = COUNTRY_NAMES[Number(rightType)];
      return left && right ? `${left}・${right}` : match;
    })
    .replace(INTERNAL_COUNTRY_COUNT_RE, (match, rawType, rawCount) => (
      COUNTRY_NAMES[Number(rawType)]
        ? `${COUNTRY_NAMES[Number(rawType)]}${rawCount}個`
        : match
    ))
    .replace(INTERNAL_COUNTRY_RE, (match, rawType) => (
      COUNTRY_NAMES[Number(rawType)] || match
    ))
    .replace(REASON_COUNTRY_TOKEN_RE, (match, rawType) => (
      COUNTRY_NAMES[Number(rawType)] || match
    ));
}
