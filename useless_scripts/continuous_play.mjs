import { chromium } from 'playwright';
import fs from 'fs';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';
const CMD_FILE = '/tmp/soviet_cmd.txt';
const RESULT_FILE = '/tmp/soviet_result.txt';
const SC_PATH = '/Users/azumag/work/sandbox/soren/sc.png';

class SovietGameController {
  constructor(headless = false) {
    this.browser = null;
    this.page = null;
    this.canvasInfo = null;
    this.moveCount = 0;
    this.running = true;
    this.headless = headless;
  }

  async init() {
    this.browser = await chromium.launch({
      headless: this.headless
    });

    const context = await this.browser.newContext();
    this.page = await context.newPage();

    console.log('Loading game...');
    await this.page.goto(URL, { waitUntil: 'networkidle' });
    await this.page.waitForTimeout(6000);

    this.canvasInfo = await this.page.evaluate(() => {
      const canvas = document.querySelector('canvas');
      if (canvas) {
        const rect = canvas.getBoundingClientRect();
        return {
          width: canvas.width,
          height: canvas.height,
          rectX: rect.x,
          rectY: rect.y,
          rectWidth: rect.width,
          rectHeight: rect.height
        };
      }
      return null;
    });

    console.log('Canvas:', this.canvasInfo);

    // ゲーム開始
    const centerX = this.canvasInfo.rectX + this.canvasInfo.width / 2;
    await this.page.mouse.click(centerX, this.canvasInfo.rectY + 200);
    await this.page.waitForTimeout(1500);

    // 初期スクリーンショット
    await this.screenshot(SC_PATH);
    this.writeResult('READY');
  }

  async screenshot(filePath = SC_PATH) {
    await this.page.screenshot({ path: filePath });
    console.log(`Screenshot: ${filePath}`);
    return filePath;
  }

  async moveAndDrop(x, y) {
    const absX = this.canvasInfo.rectX + x;
    const absY = this.canvasInfo.rectY + y;

    console.log(`Drop at (${x}, ${y})`);
    await this.page.mouse.move(absX, absY, { steps: 5 });
    await this.page.waitForTimeout(300);
    await this.page.mouse.click(absX, absY);
    await this.page.waitForTimeout(800);

    this.moveCount++;
    return await this.screenshot(SC_PATH);
  }

  writeResult(data) {
    fs.writeFileSync(RESULT_FILE, JSON.stringify({ success: true, data, timestamp: Date.now() }));
  }

  writeError(error) {
    fs.writeFileSync(RESULT_FILE, JSON.stringify({ success: false, error, timestamp: Date.now() }));
  }

  async processCommand(cmd) {
    try {
      const parts = cmd.trim().split(/\s+/);
      const command = parts[0].toLowerCase();

      console.log('Processing:', command);

      switch (command) {
        case 'drop':
          const x = parseInt(parts[1]);
          const y = parseInt(parts[2]);
          await this.moveAndDrop(x, y);
          this.writeResult({ action: 'drop', x, y, screenshot: SC_PATH, moveCount: this.moveCount });
          break;

        case 'screenshot':
          const scPath = await this.screenshot(parts[1]);
          this.writeResult({ action: 'screenshot', path: scPath });
          break;

        case 'wait':
          const ms = parseInt(parts[1]) || 1000;
          await this.page.waitForTimeout(ms);
          this.writeResult({ action: 'wait', ms });
          break;

        case 'close':
          this.running = false;
          this.writeResult({ action: 'close' });
          break;

        default:
          this.writeError(`Unknown command: ${command}`);
      }
    } catch (e) {
      this.writeError(e.message);
    }
  }

  async run() {
    await this.init();

    console.log('Server ready. Waiting for commands...');

    while (this.running) {
      try {
        if (fs.existsSync(CMD_FILE)) {
          const cmd = fs.readFileSync(CMD_FILE, 'utf-8').trim();
          fs.unlinkSync(CMD_FILE);

          if (cmd) {
            await this.processCommand(cmd);
          }
        }
      } catch (e) {
        console.error('Error:', e.message);
      }

      await new Promise(resolve => setTimeout(resolve, 100));
    }

    await this.browser.close();
    console.log('Server stopped');
  }
}

const headless = !process.argv.includes('--visible');
const server = new SovietGameController(headless);
server.run().catch(console.error);
