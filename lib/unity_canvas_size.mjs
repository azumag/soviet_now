const SIZE_PATTERN = /^(\d+)[,x](\d+)$/i;

export function parseUnityCanvasSize(raw) {
  const value = String(raw || '').trim();
  if (!value) return null;
  const match = value.match(SIZE_PATTERN);
  if (!match) {
    throw new Error('SOREN_GAME_INTERNAL_SIZE must be WIDTH,HEIGHT (for example 480,270)');
  }
  const width = Number(match[1]);
  const height = Number(match[2]);
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height)
      || width < 160 || height < 90 || width > 1920 || height > 1080) {
    throw new Error('SOREN_GAME_INTERNAL_SIZE must be between 160x90 and 1920x1080');
  }
  if (width * 9 !== height * 16) {
    throw new Error('SOREN_GAME_INTERNAL_SIZE must use a 16:9 aspect ratio');
  }
  return { width, height };
}

function replaceDimension(tag, name, value) {
  const attribute = new RegExp(`\\b${name}\\s*=\\s*(?:"[^"]*"|'[^']*'|[^\\s>]+)`, 'i');
  if (attribute.test(tag)) return tag.replace(attribute, `${name}=${value}`);
  return tag.replace(/>$/, ` ${name}=${value}>`);
}

export function rewriteUnityCanvasSize(html, size) {
  if (!size) return html;
  let found = false;
  const rewritten = html.replace(
    /<canvas\b[^>]*\bid\s*=\s*(?:"unity-canvas"|'unity-canvas'|unity-canvas)[^>]*>/i,
    (tag) => {
      found = true;
      return replaceDimension(replaceDimension(tag, 'width', size.width), 'height', size.height);
    },
  );
  if (!found) throw new Error('unity-canvas element was not found in index.html');
  return rewritten;
}
