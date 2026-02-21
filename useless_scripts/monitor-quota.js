require('dotenv').config();

const API_BASE_URL = 'https://api.z.ai';
const API_KEY = process.env.ZAI_API_KEY;

if (!API_KEY) {
  console.error('Error: ZAI_API_KEY not found in .env file');
  process.exit(1);
}

async function fetchQuotaLimit() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/paas/v4/chat/completions`, {
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
            role: 'system',
            content: 'You are a helpful AI assistant.'
          },
          {
            role: 'user',
            content: 'Hello'
          }
        ],
        temperature: 1.0,
        stream: false
      })
    });

    const headers = Object.fromEntries(response.headers);
    let data = null;
    let error = null;

    try {
      data = await response.json();
    } catch (e) {
      // JSON parse error
    }

    if (!response.ok) {
      error = {
        status: response.status,
        statusText: response.statusText,
        data: data
      };
    }

    // レート制限ヘッダーを抽出
    const rateLimitHeaders = {};
    Object.entries(headers).forEach(([key, value]) => {
      const lowerKey = key.toLowerCase();
      if (lowerKey.includes('rate') || 
          lowerKey.includes('limit') || 
          lowerKey.includes('quota') ||
          lowerKey.includes('remaining') ||
          lowerKey.includes('reset')) {
        rateLimitHeaders[key] = value;
      }
    });

    return { 
      success: response.ok, 
      status: response.status, 
      statusText: response.statusText,
      data, 
      error,
      rateLimitHeaders,
      allHeaders: headers 
    };
  } catch (error) {
    console.error('Error fetching quota:', error.message);
    throw error;
  }
}

async function displayQuotaMonitor() {
  console.log('========================================');
  console.log('Z AI API Quota Monitor');
  console.log('========================================\n');

  try {
    const result = await fetchQuotaLimit();

    console.log('📊 Response Status:');
    console.log('----------------------------------------');
    console.log(`Status: ${result.status} ${result.statusText}`);
    
    if (result.success) {
      console.log('✅ Request successful\n');
    } else {
      console.log('❌ Request failed\n');
    }

    // エラー詳細
    if (result.error) {
      console.log('🚫 Error Details:');
      console.log('----------------------------------------');
      if (result.error.data?.error) {
        console.log(`Code: ${result.error.data.error.code}`);
        console.log(`Message: ${result.error.data.error.message}`);
      }
      console.log();
    }

    // レート制限ヘッダー
    if (Object.keys(result.rateLimitHeaders).length > 0) {
      console.log('⏱️ Rate Limit Headers:');
      console.log('----------------------------------------');
      Object.entries(result.rateLimitHeaders).forEach(([key, value]) => {
        console.log(`${key}: ${value}`);
      });
      console.log();
    } else {
      console.log('⚠️ No rate limit headers found in response\n');
    }

    // 成功時のレスポンス情報
    if (result.success && result.data) {
      console.log('📝 Response Info:');
      console.log('----------------------------------------');
      console.log(`Model: ${result.data.model || 'N/A'}`);
      console.log(`Response ID: ${result.data.id || 'N/A'}`);
      
      if (result.data.usage) {
        console.log(`\n💰 Token Usage:`);
        console.log('----------------------------------------');
        console.log(`Prompt Tokens:     ${result.data.usage.prompt_tokens || 0}`);
        console.log(`Completion Tokens: ${result.data.usage.completion_tokens || 0}`);
        console.log(`Total Tokens:      ${result.data.usage.total_tokens || 0}`);
      }
      console.log();
    }

    // すべてのレスポンスヘッダー（デバッグ用）
    console.log('🔍 All Response Headers:');
    console.log('----------------------------------------');
    Object.entries(result.allHeaders).forEach(([key, value]) => {
      console.log(`${key}: ${value}`);
    });

    // クオータ解釈
    console.log('\n📋 Quota Status Analysis:');
    console.log('----------------------------------------');
    
    if (result.status === 429) {
      if (result.error?.data?.error?.message?.includes('balance') || 
          result.error?.data?.error?.message?.includes('resource')) {
        console.log('Status: 🔴 QUOTA EXHAUSTED / INSUFFICIENT BALANCE');
        console.log('Action: Please recharge your account or purchase a resource package');
      } else {
        console.log('Status: 🟡 RATE LIMITED');
        console.log('Action: Too many requests, please wait before retrying');
      }
    } else if (result.status === 401) {
      console.log('Status: 🔴 AUTHENTICATION ERROR');
      console.log('Action: Check your API key');
    } else if (result.success) {
      console.log('Status: 🟢 OK - API is accessible');
      
      // 使用量があれば表示
      if (result.data?.usage?.total_tokens) {
        console.log(`Current request used: ${result.data.usage.total_tokens} tokens`);
      }
    }

  } catch (error) {
    console.error('\n❌ Failed to fetch quota information');
    console.error(error.message);
    process.exit(1);
  }

  console.log('\n========================================');
  console.log('Monitor completed at:', new Date().toLocaleString());
  console.log('========================================');
}

// メイン実行
displayQuotaMonitor();
