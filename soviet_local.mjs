import { chromium } from 'playwright';
import fs from 'fs';
import http from 'http';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const BUILD_DIR = 'sorengame/build';
const COMMAND_FILE = 'commands.txt';
const GAME_STATE_PATH = 'game_state.json';
const SERVE_PORT = 8080;
const CDP_PORT = parseInt(process.env.SOREN_CDP_PORT || '9222', 10);
const CDP_ENDPOINT_FILE = path.join(__dirname, 'tmp', 'cdp_endpoint.json');

// MIME types for Unity WebGL build
const MIME_TYPES = {
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.data': 'application/octet-stream',
  '.wasm': 'application/wasm',
  '.gz': null, // handled specially
};

// Custom static file server that handles .gz files with correct Content-Encoding
function startServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let filePath = path.join(BUILD_DIR, req.url === '/' ? 'index.html' : req.url);
      filePath = decodeURIComponent(filePath);

      if (!fs.existsSync(filePath)) {
        res.writeHead(404);
        res.end('Not found');
        return;
      }

      const ext = path.extname(filePath);

      const noCache = {
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0',
      };

      if (ext === '.gz') {
        // Serve .gz files with Content-Encoding: gzip and correct Content-Type
        const innerExt = path.extname(filePath.slice(0, -3)); // e.g. .js from .js.gz
        const contentType = MIME_TYPES[innerExt] || 'application/octet-stream';
        res.writeHead(200, {
          'Content-Type': contentType,
          'Content-Encoding': 'gzip',
          ...noCache,
        });
      } else {
        const contentType = MIME_TYPES[ext] || 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': contentType, ...noCache });
      }

      fs.createReadStream(filePath).pipe(res);
    });

    server.listen(SERVE_PORT, () => {
      resolve(server);
    });
  });
}

// Read commands from commands.txt (same format as soviet_game.mjs)
function readCommands() {
  try {
    if (!fs.existsSync(COMMAND_FILE)) return [];
    const content = fs.readFileSync(COMMAND_FILE, 'utf-8').trim();
    if (!content) return [];

    const lines = content.split('\n').filter(l => l.trim());
    const commands = [];

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.toLowerCase() === 'retry') {
        commands.push({ action: 'retry' });
      } else if (trimmed.startsWith('[')) {
        try {
          commands.push(...JSON.parse(trimmed));
        } catch (e) {
          console.log('Failed to parse JSON:', trimmed);
        }
      } else if (trimmed.toLowerCase() === 'mute') {
        commands.push({ action: 'mute' });
      } else if (trimmed.toLowerCase() === 'unmute') {
        commands.push({ action: 'unmute' });
      } else {
        // x,y format (canvas coords) — convert to game X coord
        const parts = trimmed.split(',').map(s => parseInt(s.trim()));
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
          // Convert canvas X (410-830 range) to game X (-3 to +3 range)
          const boardL = 410, boardR = 830;
          const gameX = ((parts[0] - boardL) / (boardR - boardL)) * 6 - 3;
          const clampedX = Math.max(-3.0, Math.min(3.0, gameX));
          commands.push({ action: 'drop', x: clampedX });
        }
      }
    }
    return commands;
  } catch (e) {
    console.error('Error reading commands:', e);
    return [];
  }
}

function clearCommands() {
  try { fs.writeFileSync(COMMAND_FILE, ''); } catch (e) {}
}

// Get game state from JS Bridge
async function getGameState(page) {
  try {
    const state = await page.evaluate(() => window.__sorenGameState);
    return state || null;
  } catch (e) {
    console.error('Error getting game state:', e.message);
    return null;
  }
}

// Write game state to JSON file for AI loop
function writeGameState(state) {
  if (!state) return;
  fs.writeFileSync(GAME_STATE_PATH, JSON.stringify(state, null, 2));
}

// Check if state has changed (compare relevant fields)
function stateChanged(prev, curr) {
  if (!prev || !curr) return true;
  return prev.state !== curr.state ||
         prev.score !== curr.score ||
         JSON.stringify(prev.pieces) !== JSON.stringify(curr.pieces);
}

// Execute a command via JS Bridge
async function executeCommand(page, command) {
  if (command.action === 'retry') {
    console.log('Executing: RETRY');
    await page.evaluate(() => { window.__sorenCommand = 'RETRY'; });
    await page.waitForTimeout(2000);
    // Re-inject best score after scene reload
    try {
      const bestScore = parseInt(fs.readFileSync('best_score.txt', 'utf-8').trim(), 10);
      if (bestScore > 0) {
        await page.evaluate((s) => { window.__sorenCommand = 'SET_RECORD:' + s; }, bestScore);
        console.log(`Re-injected best score record: ${bestScore}`);
        await page.waitForTimeout(500);
      }
    } catch (e) { /* ignore */ }
  } else if (command.action === 'cmd') {
    console.log(`Executing: ${command.value}`);
    await page.evaluate((v) => { window.__sorenCommand = v; }, command.value);
    await page.waitForTimeout(1000);
  } else if (command.action === 'drop') {
    console.log(`Executing: DROP at x=${command.x.toFixed(3)}`);
    await page.evaluate((x) => { window.__sorenCommand = 'DROP:' + x; }, command.x);
    await page.waitForTimeout(500);
  } else if (command.action === 'mute') {
    console.log('Executing: MUTE');
    await page.evaluate(() => {
      if (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext) {
        try { Module.WebAudio.audioContext.suspend(); } catch {}
      }
    });
  } else if (command.action === 'unmute') {
    console.log('Executing: UNMUTE');
    await page.evaluate(() => {
      if (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext) {
        try { Module.WebAudio.audioContext.resume(); } catch {}
      }
    });
  }

  // Update state after command
  const state = await getGameState(page);
  writeGameState(state);
  return state;
}

async function runLocalController() {
  // Check build directory exists
  if (!fs.existsSync(BUILD_DIR)) {
    console.error(`Build directory not found: ${BUILD_DIR}`);
    console.error('Please build the Unity WebGL project first (File → Build Settings → Build)');
    process.exit(1);
  }

  // Start local server
  console.log(`Starting local server for ${BUILD_DIR} on port ${SERVE_PORT}...`);
  let server;
  try {
    server = await startServer();
    console.log(`Server started on port ${SERVE_PORT}`);
  } catch (e) {
    console.error('Failed to start server:', e.message);
    process.exit(1);
  }

  // Cleanup on exit
  function removeCdpEndpoint() {
    try { fs.unlinkSync(CDP_ENDPOINT_FILE); } catch {}
  }
  process.on('SIGINT', () => {
    console.log('\nShutting down...');
    removeCdpEndpoint();
    server.close();
    process.exit(0);
  });
  process.on('exit', removeCdpEndpoint);

  let browser;
  try {
    browser = await chromium.launch({
      headless: false,
      args: ['--window-size=1300,800', `--remote-debugging-port=${CDP_PORT}`],
    });
  } catch (e) {
    console.error(`Failed to launch browser: ${e.message}`);
    removeCdpEndpoint();
    server.close();
    process.exit(1);
  }

  // Write CDP endpoint file for soren91 shared browser mode
  try {
    fs.writeFileSync(CDP_ENDPOINT_FILE, JSON.stringify({
      url: `http://localhost:${CDP_PORT}`,
      port: CDP_PORT,
      pid: process.pid,
      startedAt: new Date().toISOString(),
    }));
    console.log(`CDP endpoint written: ${CDP_ENDPOINT_FILE} (port=${CDP_PORT})`);
  } catch (e) {
    console.warn(`Failed to write CDP endpoint file: ${e.message}`);
  }

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
  });

  const page = await context.newPage();

  console.log('=== Soren Local Game Controller ===');
  console.log(`Navigating to http://localhost:${SERVE_PORT}...`);

  await page.goto(`http://localhost:${SERVE_PORT}`, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // Wait for Unity canvas to initialize
  let canvasReady = false;
  for (let i = 0; i < 60; i++) {
    canvasReady = await page.evaluate(() => {
      const canvas = document.getElementById('unity-canvas') || document.querySelector('canvas');
      return canvas && canvas.width > 300;
    });
    if (canvasReady) break;
    console.log(`Waiting for Unity canvas init... (${i + 1}/60)`);
    await page.waitForTimeout(1000);
  }

  if (!canvasReady) {
    console.error('Unity canvas failed to initialize!');
    await browser.close();
    server.close();
    return;
  }

  console.log('Unity canvas ready');

  // Force canvas to fill viewport exactly — hide footer, reset margins, override container positioning
  const canvasInfo = await page.evaluate(() => {
    // Hide footer
    const footer = document.getElementById('unity-footer');
    if (footer) footer.style.display = 'none';
    // Hide loading bar
    const loadingBar = document.getElementById('unity-loading-bar');
    if (loadingBar) loadingBar.style.display = 'none';
    // Reset body
    document.body.style.margin = '0';
    document.body.style.padding = '0';
    document.body.style.overflow = 'hidden';
    // Override container — remove centering transform, pin to top-left
    const container = document.getElementById('unity-container');
    if (container) {
      container.style.position = 'absolute';
      container.style.left = '0';
      container.style.top = '0';
      container.style.transform = 'none';
    }
    // Ensure canvas fills exactly
    const canvas = document.getElementById('unity-canvas');
    if (canvas) {
      canvas.style.width = '1280px';
      canvas.style.height = '720px';
      canvas.style.display = 'block';
    }
    return {
      canvasWidth: canvas?.width,
      canvasHeight: canvas?.height,
      cssWidth: canvas?.style.width,
      cssHeight: canvas?.style.height,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
    };
  });
  console.log('Canvas layout:', JSON.stringify(canvasInfo));

  // Capture browser console for debugging
  page.on('console', msg => {
    if (msg.type() === 'error' || msg.type() === 'warning' || msg.text().includes('FIXUI')) {
      console.log(`[BROWSER ${msg.type().toUpperCase()}] ${msg.text()}`);
    }
  });

  // Wait for JS Bridge to be active
  let bridgeReady = false;
  for (let i = 0; i < 30; i++) {
    const state = await getGameState(page);
    if (state && state.state) {
      bridgeReady = true;
      console.log(`JS Bridge active, game state: ${state.state}`);
      break;
    }
    console.log(`Waiting for JS Bridge... (${i + 1}/30)`);
    await page.waitForTimeout(1000);
  }

  if (!bridgeReady) {
    console.error('JS Bridge not responding. Is SorenBridge component attached in the scene?');
    await browser.close();
    server.close();
    return;
  }

  // Inject best score record from best_score.txt
  try {
    const bestScore = parseInt(fs.readFileSync('best_score.txt', 'utf-8').trim(), 10);
    if (bestScore > 0) {
      await page.evaluate((s) => { window.__sorenCommand = 'SET_RECORD:' + s; }, bestScore);
      console.log(`Injected best score record: ${bestScore}`);
      await page.waitForTimeout(500);
    }
  } catch (e) {
    console.log('No best_score.txt found, skipping record injection');
  }

  // Click to start the game
  await page.mouse.click(640, 360);
  await page.waitForTimeout(2000);

  // Initial state
  const initialState = await getGameState(page);
  writeGameState(initialState);
  console.log('Initial game state saved');
  console.log(`Watching for commands in: ${COMMAND_FILE}`);

  // Main loop: poll commands and game state
  let processedCount = 0;
  let lastState = null;
  const STATE_CHECK_INTERVAL = 3;
  const NULL_STATE_WARN_THRESHOLD = 10;
  const NULL_STATE_RELOAD_THRESHOLD = 30;

  let checkCount = 0;
  let nullStateCount = 0;
  while (true) {
    const commands = readCommands();

    if (commands.length > processedCount) {
      for (let i = processedCount; i < commands.length; i++) {
        const state = await executeCommand(page, commands[i]);
        lastState = state;
        if (state) nullStateCount = 0;
        processedCount++;

        if (i === commands.length - 1) {
          clearCommands();
          processedCount = 0;
        }
      }
    } else {
      checkCount++;
      if (checkCount >= STATE_CHECK_INTERVAL) {
        checkCount = 0;
        const state = await getGameState(page);

        if (!state) {
          nullStateCount++;
          if (nullStateCount === NULL_STATE_WARN_THRESHOLD) {
            console.warn(`[BRIDGE] game state null ${nullStateCount} times in a row — JS Bridge may be broken`);
          }
          if (nullStateCount >= NULL_STATE_RELOAD_THRESHOLD) {
            console.warn(`[BRIDGE] game state null ${nullStateCount} times — reloading page to recover`);
            try {
              await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
              // Wait for Unity canvas + Bridge to re-init
              for (let i = 0; i < 60; i++) {
                const s = await getGameState(page);
                if (s && s.state) {
                  console.log(`[BRIDGE] Recovered after reload, state: ${s.state}`);
                  // Re-inject best score
                  try {
                    const bestScore = parseInt(fs.readFileSync('best_score.txt', 'utf-8').trim(), 10);
                    if (bestScore > 0) {
                      await page.evaluate((sc) => { window.__sorenCommand = 'SET_RECORD:' + sc; }, bestScore);
                      console.log(`[BRIDGE] Re-injected best score: ${bestScore}`);
                      await page.waitForTimeout(500);
                    }
                  } catch (e2) { /* ignore */ }
                  // Click to start game
                  await page.mouse.click(640, 360);
                  await page.waitForTimeout(2000);
                  lastState = s;
                  writeGameState(s);
                  break;
                }
                await page.waitForTimeout(1000);
              }
            } catch (e) {
              console.error(`[BRIDGE] Reload failed: ${e.message}`);
            }
            nullStateCount = 0;
          }
        } else {
          if (nullStateCount >= NULL_STATE_WARN_THRESHOLD) {
            console.log(`[BRIDGE] game state recovered after ${nullStateCount} null reads`);
          }
          nullStateCount = 0;

          if (stateChanged(lastState, state)) {
            writeGameState(state);
            console.log(`State: ${state.state}, score=${state.score}, pieces=${state.pieces?.length || 0}`);
          }
          lastState = state;
        }
      }
    }

    await page.waitForTimeout(200);
  }
}

runLocalController().catch(console.error);
