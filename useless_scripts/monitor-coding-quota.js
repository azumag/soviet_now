require('dotenv').config();

const API_BASE_URL = 'https://api.z.ai';
const API_KEY = process.env.ZAI_API_KEY;

if (!API_KEY) {
  console.error('Error: ZAI_API_KEY not found in .env file');
  process.exit(1);
}

// Coding Plan用エンドポイント
const ENDPOINTS = {
  claude: `${API_BASE_URL}/api/anthropic/v1/messages`,
  coding: `${API_BASE_URL}/api/coding/paas/v4/chat/completions`
};

async function fetchCodingQuota(endpoint) {
  try {
    let body, headers;
    
    if (endpoint.includes('anthropic')) {
      // Claude Code用フォーマット
      headers = {
        'Content-Type': 'application/json',
        'Accept-Language': 'en-US,en',
        'Authorization': `Bearer ${API_KEY}`,
        'anthropic-version': '2023-06-01'
      };
      body = JSON.stringify({
        model: 'glm-4.7',
        max_tokens: 10,
        messages: [
          {
            role: 'user',
            content: 'Hi'
          }
        ]
      });
    } else {
      // 通常のCoding Planフォーマット
      headers = {
        'Content-Type': 'application/json',
        'Accept-Language': 'en-US,en',
        'Authorization': `Bearer ${API_KEY}`
      };
      body = JSON.stringify({
        model: 'glm-4.7',
        messages: [
          {
            role: 'system',
            content: 'You are a helpful AI assistant.'
          },
          {
            role: 'user',
            content: 'Hi'
          }
        ],
        temperature: 1.0,
        max_tokens: 10,
        stream: false
      });
    }

    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body
    });

    const responseHeaders = Object.fromEntries(response.headers);
    let data = null;
    
    try {
      data = await response.json();
    } catch (e) {
      // JSON parse error
    }

    // レート制限ヘッダーを抽出
    const rateLimitHeaders = {};
    Object.entries(responseHeaders).forEach(([key, value]) => {
      const lowerKey = key.toLowerCase();
      if (lowerKey.includes('rate') || 
          lowerKey.includes('limit') || 
          lowerKey.includes('quota') ||
          lowerKey.includes('remaining') ||
          lowerKey.includes('reset') ||
          lowerKey.includes('coding') ||
          lowerKey.includes('plan')) {
        rateLimitHeaders[key] = value;
      }
    });

    return { 
      success: response.ok, 
      status: response.status, 
      statusText: response.statusText,
      endpoint: endpoint.includes('anthropic') ? 'Claude/Goose' : 'Coding API',
      data, 
      rateLimitHeaders,
      allHeaders: responseHeaders 
    };
  } catch (error) {
    console.error(`Error fetching from ${endpoint}:`, error.message);
    throw error;
  }
}

async function displayQuotaMonitor() {
  console.log('========================================');
  console.log('Z AI Coding Plan Quota Monitor');
  console.log('========================================\n');

  console.log('🔍 Checking both endpoints...\n');

  // 両方のエンドポイントをチェック
  for (const [name, endpoint] of Object.entries(ENDPOINTS)) {
    console.log(`\n📡 Testing ${name.toUpperCase()} Endpoint:`);
    console.log(`   ${endpoint}`);
    console.log('----------------------------------------');

    try {
      const result = await fetchCodingQuota(endpoint);

      console.log(`Status: ${result.status} ${result.statusText}`);
      
      if (result.success) {
        console.log('✅ Request successful');
      } else {
        console.log('❌ Request failed');
      }

      // エラー詳細
      if (!result.success && result.data?.error) {
        console.log(`\nError Code: ${result.data.error.code}`);
        console.log(`Message: ${result.data.error.message}`);
      }

      // レート制限ヘッダー
      if (Object.keys(result.rateLimitHeaders).length > 0) {
        console.log('\n⏱️ Rate Limit Headers:');
        Object.entries(result.rateLimitHeaders).forEach(([key, value]) => {
          console.log(`  ${key}: ${value}`);
        });
      }

      // 特定のヘッダーを探す
      const quotaHeaders = [
        'x-coding-plan-quota',
        'x-coding-plan-remaining',
        'x-coding-plan-reset',
        'x-quota-limit',
        'x-quota-remaining',
        'x-ratelimit-coding-limit',
        'x-ratelimit-coding-remaining'
      ];

      const foundQuotaHeaders = quotaHeaders.filter(h => 
        result.allHeaders[h] || result.allHeaders[h.toLowerCase()]
      );

      if (foundQuotaHeaders.length > 0) {
        console.log('\n💰 Coding Plan Quota Headers:');
        foundQuotaHeaders.forEach(h => {
          const value = result.allHeaders[h] || result.allHeaders[h.toLowerCase()];
          console.log(`  ${h}: ${value}`);
        });
      }

      // 成功時のトークン使用情報
      if (result.success) {
        if (result.data?.usage) {
          console.log('\n💰 Token Usage:');
          console.log(`  Prompt: ${result.data.usage.prompt_tokens || 0}`);
          console.log(`  Completion: ${result.data.usage.completion_tokens || 0}`);
          console.log(`  Total: ${result.data.usage.total_tokens || 0}`);
        }
      }

      // ステータス解釈
      console.log('\n📋 Status Analysis:');
      if (result.status === 429) {
        console.log('  🟡 RATE LIMITED - Quota exhausted for this cycle');
        console.log('  ⏰ Quota resets every 5 hours');
      } else if (result.status === 401) {
        console.log('  🔴 AUTHENTICATION ERROR - Check API key');
      } else if (result.status === 404) {
        console.log('  🔴 ENDPOINT NOT FOUND - Check URL');
      } else if (result.success) {
        console.log('  🟢 OK - Coding Plan is active');
      }

    } catch (error) {
      console.error(`  ❌ Error: ${error.message}`);
    }

    console.log('----------------------------------------');
  }

  console.log('\n\n📚 Coding Plan Quota Information:');
  console.log('========================================');
  console.log('Lite Plan:  ~120 prompts per 5 hours');
  console.log('Pro Plan:   ~600 prompts per 5 hours');
  console.log('Max Plan:   ~2400 prompts per 5 hours');
  console.log('\nNote: Each prompt typically allows 15-20 model calls');
  console.log('Quota resets every 5 hours\n');

  console.log('========================================');
  console.log('Monitor completed at:', new Date().toLocaleString());
  console.log('========================================');
}

// メイン実行
displayQuotaMonitor();
