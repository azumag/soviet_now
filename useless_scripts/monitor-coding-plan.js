require('dotenv').config();

const API_BASE_URL = 'https://api.z.ai';
const API_KEY = process.env.ZAI_API_KEY;

if (!API_KEY) {
  console.error('Error: ZAI_API_KEY not found in .env file');
  process.exit(1);
}

// Coding Plan用エンドポイント
const CODING_ENDPOINT = `${API_BASE_URL}/api/coding/paas/v4/chat/completions`;

class CodingPlanMonitor {
  constructor() {
    this.requestCount = 0;
    this.successCount = 0;
    this.errorCount = 0;
    this.quotaErrors = 0;
    this.lastError = null;
    this.startTime = new Date();
    this.models = new Set();
    this.tokenUsage = {
      prompt: 0,
      completion: 0,
      total: 0
    };
  }

  async checkQuota() {
    try {
      const response = await fetch(CODING_ENDPOINT, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept-Language': 'en-US,en',
          'Authorization': `Bearer ${API_KEY}`
        },
        body: JSON.stringify({
          model: 'glm-4.7',
          messages: [
            {
              role: 'user',
              content: 'Hi'
            }
          ],
          max_tokens: 5,
          stream: false
        })
      });

      this.requestCount++;

      if (response.ok) {
        this.successCount++;
        const data = await response.json();
        
        if (data.model) {
          this.models.add(data.model);
        }
        
        if (data.usage) {
          this.tokenUsage.prompt += data.usage.prompt_tokens || 0;
          this.tokenUsage.completion += data.usage.completion_tokens || 0;
          this.tokenUsage.total += data.usage.total_tokens || 0;
        }

        return {
          status: 'ok',
          statusCode: response.status,
          hasQuota: true,
          model: data.model,
          tokens: data.usage?.total_tokens || 0
        };
      } else {
        this.errorCount++;
        let errorData;
        try {
          errorData = await response.json();
        } catch (e) {
          errorData = { error: { message: response.statusText } };
        }

        const isQuotaError = response.status === 429 || 
          errorData?.error?.code === '1113' ||
          errorData?.error?.message?.includes('quota') ||
          errorData?.error?.message?.includes('balance');

        if (isQuotaError) {
          this.quotaErrors++;
        }

        this.lastError = {
          status: response.status,
          code: errorData?.error?.code,
          message: errorData?.error?.message
        };

        return {
          status: 'error',
          statusCode: response.status,
          hasQuota: !isQuotaError,
          error: this.lastError
        };
      }
    } catch (error) {
      this.errorCount++;
      this.lastError = { message: error.message };
      return {
        status: 'error',
        statusCode: 0,
        hasQuota: true,
        error: this.lastError
      };
    }
  }

  displayStatus() {
    const now = new Date();
    const elapsed = Math.floor((now - this.startTime) / 1000);
    const minutes = Math.floor(elapsed / 60);
    const seconds = elapsed % 60;

    console.log('\n========================================');
    console.log('Z AI Coding Plan Monitor Status');
    console.log('========================================');
    console.log(`Time: ${now.toLocaleString()}`);
    console.log(`Session Duration: ${minutes}m ${seconds}s\n`);

    console.log('📊 Request Statistics:');
    console.log('----------------------------------------');
    console.log(`Total Requests:    ${this.requestCount}`);
    console.log(`Successful:        ${this.successCount} ✅`);
    console.log(`Errors:            ${this.errorCount} ❌`);
    
    if (this.quotaErrors > 0) {
      console.log(`Quota Exhausted:   ${this.quotaErrors} 🚫`);
    }

    console.log(`\n💰 Token Usage (This Session):`);
    console.log('----------------------------------------');
    console.log(`Prompt Tokens:     ${this.tokenUsage.prompt.toLocaleString()}`);
    console.log(`Completion Tokens: ${this.tokenUsage.completion.toLocaleString()}`);
    console.log(`Total Tokens:      ${this.tokenUsage.total.toLocaleString()}`);

    if (this.models.size > 0) {
      console.log(`\n🤖 Models Used:`);
      console.log('----------------------------------------');
      this.models.forEach(model => console.log(`  • ${model}`));
    }

    if (this.lastError) {
      console.log(`\n⚠️ Last Error:`);
      console.log('----------------------------------------');
      console.log(`Status: ${this.lastError.status || 'N/A'}`);
      console.log(`Code: ${this.lastError.code || 'N/A'}`);
      console.log(`Message: ${this.lastError.message || 'N/A'}`);
    }

    console.log('\n📋 Quota Status:');
    console.log('----------------------------------------');
    if (this.quotaErrors > 0) {
      console.log('Status: 🔴 QUOTA EXHAUSTED');
      console.log('Note: Quota resets every 5 hours');
    } else if (this.successCount > 0) {
      const successRate = ((this.successCount / this.requestCount) * 100).toFixed(1);
      console.log(`Status: 🟢 ACTIVE (Success Rate: ${successRate}%)`);
    } else {
      console.log('Status: ⏳ Waiting for first request...');
    }

    console.log('\n💡 Coding Plan Limits:');
    console.log('----------------------------------------');
    console.log('Lite:  ~120 prompts / 5 hours');
    console.log('Pro:   ~600 prompts / 5 hours');
    console.log('Max:   ~2400 prompts / 5 hours');
    console.log('Note: Each prompt = 15-20 model calls');
  }

  async runCheck() {
    console.log('Checking Coding Plan status...');
    const result = await this.checkQuota();
    this.displayStatus();
    return result;
  }
}

// メイン実行
async function main() {
  const monitor = new CodingPlanMonitor();
  const intervalSeconds = parseInt(process.argv[2]) || 30; // デフォルト30秒
  
  console.log('========================================');
  console.log('Z AI Coding Plan Quota Monitor');
  console.log('========================================\n');
  console.log(`Auto-refresh interval: ${intervalSeconds}s`);
  console.log('Press Ctrl+C to stop\n');
  console.log('Starting in 3 seconds...\n');
  
  await new Promise(resolve => setTimeout(resolve, 3000));

  // 初回チェック
  await monitor.checkQuota();
  monitor.displayStatus();

  // 定期更新
  const interval = setInterval(async () => {
    console.clear();
    await monitor.checkQuota();
    monitor.displayStatus();
  }, intervalSeconds * 1000);

  // 終了ハンドラ
  process.on('SIGINT', () => {
    clearInterval(interval);
    console.log('\n\nMonitor stopped by user.');
    process.exit(0);
  });

  process.on('SIGTERM', () => {
    clearInterval(interval);
    console.log('\n\nMonitor stopped.');
    process.exit(0);
  });
}

main().catch(console.error);
