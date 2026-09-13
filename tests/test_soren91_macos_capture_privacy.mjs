import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const helper = fs.readFileSync(path.join(root, 'tools/macos/soren91_window_capture.swift'), 'utf8');

test('capture helper failure path does not enumerate unrelated application window titles', () => {
  assert.doesNotMatch(helper, /candidate titles/i);
  assert.doesNotMatch(helper, /\.map\s*\{\s*\$0\.title/);
  assert.match(helper, /expected exactly 1 matching capture window, found/);
});
