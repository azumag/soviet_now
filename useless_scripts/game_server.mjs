import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';

const CMD_FILE = '/tmp/soviet_cmd.txt';
const RESULT_FILE = '/tmp/soviet_result.txt';

// スクリプトのあるディレクトリを取得
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const SC_PATH = path.join(__dirname, 'sc.png');

class SovietGameServer {
  constructor(headless = true) {
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
    await this.page.goto(URL, { waitUntil: 'load', timeout: 60000 });
    await this.page.waitForTimeout(8000);

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
    const screenshotPath = filePath;
    await this.page.screenshot({ path: screenshotPath });
    console.log(`Screenshot: ${screenshotPath}`);
    return screenshotPath;
  }

  async moveAndDrop(x, y) {
    const absX = this.canvasInfo.rectX + x;
    const absY = this.canvasInfo.rectY + y;

    console.log(`Drop at (${x}, ${y})`);
    await this.page.mouse.move(absX, absY);
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
          const screenshotPath = await this.moveAndDrop(x, y);
          this.writeResult({ action: 'drop', x, y, screenshot: screenshotPath, moveCount: this.moveCount });
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

        case 'status':
          this.writeResult({
            action: 'status',
            moveCount: this.moveCount,
            canvasInfo: this.canvasInfo
          });
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

// headless モードを引数で制御（デフォルトは headless）
const headless = !process.argv.includes('--visible');
const server = new SovietGameServer(headless);
server.run().catch(console.error);
