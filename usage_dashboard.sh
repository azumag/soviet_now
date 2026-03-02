#!/bin/bash
# Claude Code Usage Dashboard Generator
# Generates an HTML dashboard showing 5h block and weekly usage

set -e

DASHBOARD_FILE="/tmp/claude_usage_dashboard.html"

echo "Collecting usage data..."

# Get blocks data (recent)
BLOCKS_JSON=$(bun x ccusage blocks --json --recent 2>/dev/null || echo '{"blocks":[]}')

# Get weekly data
WEEKLY_JSON=$(bun x ccusage weekly --json 2>/dev/null || echo '{"weekly":[]}')

# Get active block
ACTIVE_BLOCK=$(echo "$BLOCKS_JSON" | jq '[.blocks[] | select(.isActive == true)] | .[0] // empty' 2>/dev/null || echo 'null')

# Get current week
CURRENT_WEEK=$(echo "$WEEKLY_JSON" | jq '.weekly[-1] // {}' 2>/dev/null || echo '{}')

cat > "$DASHBOARD_FILE" << 'HTMLEOF'
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Code Usage Dashboard</title>
<style>
  :root {
    --bg: #0a0a0f;
    --card: #12121a;
    --border: #1e1e2e;
    --text: #e0e0e8;
    --text-dim: #6e6e8a;
    --accent: #7c6ff0;
    --accent2: #4ecdc4;
    --warn: #f0a030;
    --danger: #e84057;
    --success: #4ecdc4;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 24px;
  }
  .header {
    text-align: center;
    margin-bottom: 32px;
  }
  .header h1 {
    font-size: 20px;
    font-weight: 600;
    letter-spacing: 2px;
    color: var(--accent);
    text-transform: uppercase;
  }
  .header .plan {
    font-size: 12px;
    color: var(--text-dim);
    margin-top: 4px;
  }
  .header .updated {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 2px;
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    max-width: 1100px;
    margin: 0 auto;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
  }
  .card.full { grid-column: 1 / -1; }
  .card h2 {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 16px;
  }
  .gauge-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }
  .gauge {
    position: relative;
    width: 200px;
    height: 120px;
  }
  .gauge svg {
    width: 200px;
    height: 120px;
  }
  .gauge-label {
    text-align: center;
  }
  .gauge-value {
    font-size: 32px;
    font-weight: 700;
    color: var(--text);
  }
  .gauge-sub {
    font-size: 12px;
    color: var(--text-dim);
    margin-top: 2px;
  }

  /* Progress bars */
  .progress-section {
    margin-bottom: 20px;
  }
  .progress-section:last-child {
    margin-bottom: 0;
  }
  .progress-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 8px;
  }
  .progress-label {
    font-size: 13px;
    color: var(--text);
  }
  .progress-value {
    font-size: 13px;
    font-weight: 600;
  }
  .progress-track {
    height: 20px;
    background: #1a1a2a;
    border-radius: 10px;
    overflow: hidden;
    position: relative;
  }
  .progress-fill {
    height: 100%;
    border-radius: 10px;
    transition: width 1s ease;
    position: relative;
  }
  .progress-fill.low { background: linear-gradient(90deg, #4ecdc4, #44b8b0); }
  .progress-fill.mid { background: linear-gradient(90deg, #f0a030, #e8901a); }
  .progress-fill.high { background: linear-gradient(90deg, #e84057, #d0304a); }
  .progress-fill::after {
    content: '';
    position: absolute;
    top: 0; right: 0; bottom: 0; left: 0;
    background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 50%, transparent 100%);
    animation: shimmer 2s infinite;
  }
  @keyframes shimmer {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(100%); }
  }
  .progress-detail {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 6px;
    display: flex;
    justify-content: space-between;
  }

  /* Block timeline */
  .timeline {
    display: flex;
    gap: 4px;
    align-items: flex-end;
    height: 100px;
    padding: 0 4px;
  }
  .timeline-bar {
    flex: 1;
    min-width: 20px;
    border-radius: 4px 4px 0 0;
    position: relative;
    cursor: pointer;
    transition: opacity 0.2s;
  }
  .timeline-bar:hover { opacity: 0.8; }
  .timeline-bar .tooltip {
    display: none;
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    background: #1a1a2e;
    border: 1px solid var(--border);
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 11px;
    white-space: nowrap;
    z-index: 10;
    color: var(--text);
  }
  .timeline-bar:hover .tooltip { display: block; }
  .timeline-labels {
    display: flex;
    gap: 4px;
    margin-top: 6px;
    padding: 0 4px;
  }
  .timeline-labels span {
    flex: 1;
    min-width: 20px;
    text-align: center;
    font-size: 9px;
    color: var(--text-dim);
  }

  /* Model breakdown */
  .model-list { list-style: none; }
  .model-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .model-item:last-child { border-bottom: none; }
  .model-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .model-name {
    flex: 1;
    font-size: 12px;
  }
  .model-cost {
    font-size: 13px;
    font-weight: 600;
  }
  .model-bar-track {
    width: 120px;
    height: 6px;
    background: #1a1a2a;
    border-radius: 3px;
    overflow: hidden;
  }
  .model-bar-fill {
    height: 100%;
    border-radius: 3px;
  }

  /* Stats row */
  .stats-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
  }
  .stat-item {
    text-align: center;
  }
  .stat-value {
    font-size: 22px;
    font-weight: 700;
    color: var(--accent);
  }
  .stat-label {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 4px;
  }

  /* Time remaining */
  .time-remaining {
    text-align: center;
    padding: 12px;
    background: #1a1a2a;
    border-radius: 8px;
    margin-top: 12px;
  }
  .time-remaining .time {
    font-size: 28px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .time-remaining .label {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 2px;
  }

  .no-data {
    color: var(--text-dim);
    text-align: center;
    padding: 30px;
    font-size: 13px;
  }
</style>
</head>
<body>

<div class="header">
  <h1>Claude Code Usage</h1>
  <div class="plan">Max 5x Plan ($100/mo)</div>
  <div class="updated" id="updatedTime"></div>
</div>

<div class="grid">
  <!-- 5h Block Gauge -->
  <div class="card">
    <h2>Current 5h Block</h2>
    <div id="blockGauge"></div>
  </div>

  <!-- Weekly Usage -->
  <div class="card">
    <h2>This Week</h2>
    <div id="weeklyGauge"></div>
  </div>

  <!-- Block History Timeline -->
  <div class="card full">
    <h2>Recent Blocks (3 days)</h2>
    <div id="blockTimeline"></div>
  </div>

  <!-- Model Breakdown -->
  <div class="card">
    <h2>Model Breakdown (This Week)</h2>
    <div id="modelBreakdown"></div>
  </div>

  <!-- Stats -->
  <div class="card">
    <h2>Weekly Stats</h2>
    <div id="weeklyStats"></div>
  </div>
</div>

<script>
HTMLEOF

# Inject data
cat >> "$DASHBOARD_FILE" << DATAEOF
const blocksData = $BLOCKS_JSON;
const weeklyData = $WEEKLY_JSON;
DATAEOF

cat >> "$DASHBOARD_FILE" << 'SCRIPTEOF'

// Helper functions
function formatCost(usd) {
  return '$' + usd.toFixed(2);
}

function formatTokens(n) {
  if (n >= 1e9) return (n/1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return n.toString();
}

function getColorClass(pct) {
  if (pct < 60) return 'low';
  if (pct < 85) return 'mid';
  return 'high';
}

function getColor(pct) {
  if (pct < 60) return '#4ecdc4';
  if (pct < 85) return '#f0a030';
  return '#e84057';
}

function timeAgo(date) {
  const diff = Date.now() - new Date(date).getTime();
  const hours = Math.floor(diff / 3600000);
  const mins = Math.floor((diff % 3600000) / 60000);
  if (hours > 0) return `${hours}h ${mins}m ago`;
  return `${mins}m ago`;
}

// Updated time
document.getElementById('updatedTime').textContent = 'Updated: ' + new Date().toLocaleString('ja-JP');

// Non-gap blocks
const realBlocks = blocksData.blocks.filter(b => !b.isGap);
const activeBlock = realBlocks.find(b => b.isActive);
const currentWeek = weeklyData.weekly ? weeklyData.weekly[weeklyData.weekly.length - 1] : null;

// ===== 5h Block Gauge =====
const blockEl = document.getElementById('blockGauge');

if (activeBlock) {
  const start = new Date(activeBlock.startTime);
  const end = new Date(activeBlock.endTime);
  const now = Date.now();
  const elapsed = now - start.getTime();
  const total = end.getTime() - start.getTime();
  const timePct = Math.min(100, (elapsed / total) * 100);
  const remaining = Math.max(0, end.getTime() - now);
  const remHours = Math.floor(remaining / 3600000);
  const remMins = Math.floor((remaining % 3600000) / 60000);

  // Estimate block cost limit (~$40 for Max 5x based on typical usage)
  const blockLimit = 40;
  const costPct = Math.min(100, (activeBlock.costUSD / blockLimit) * 100);

  blockEl.innerHTML = `
    <div class="progress-section">
      <div class="progress-header">
        <span class="progress-label">Time Elapsed</span>
        <span class="progress-value" style="color:${getColor(timePct)}">${timePct.toFixed(0)}%</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill ${getColorClass(timePct)}" style="width:${timePct}%"></div>
      </div>
      <div class="progress-detail">
        <span>${start.toLocaleTimeString('ja-JP', {hour:'2-digit',minute:'2-digit'})}</span>
        <span>${end.toLocaleTimeString('ja-JP', {hour:'2-digit',minute:'2-digit'})}</span>
      </div>
    </div>
    <div class="progress-section">
      <div class="progress-header">
        <span class="progress-label">Cost</span>
        <span class="progress-value" style="color:${getColor(costPct)}">${formatCost(activeBlock.costUSD)}</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill ${getColorClass(costPct)}" style="width:${costPct}%"></div>
      </div>
      <div class="progress-detail">
        <span>${formatTokens(activeBlock.totalTokens)} tokens</span>
        <span>~${formatCost(blockLimit)} limit (est.)</span>
      </div>
    </div>
    <div class="time-remaining">
      <div class="time" style="color:${getColor(timePct)}">${remHours}h ${String(remMins).padStart(2,'0')}m</div>
      <div class="label">Remaining in block</div>
    </div>
  `;
} else {
  // No active block - show last block
  const lastBlock = realBlocks[realBlocks.length - 1];
  if (lastBlock) {
    const endTime = new Date(lastBlock.actualEndTime || lastBlock.endTime);
    blockEl.innerHTML = `
      <div class="no-data">
        <div style="font-size:24px;margin-bottom:8px">No Active Block</div>
        <div>Last block ended: ${endTime.toLocaleString('ja-JP')}</div>
        <div style="margin-top:8px">Cost: ${formatCost(lastBlock.costUSD)} / ${formatTokens(lastBlock.totalTokens)} tokens</div>
      </div>
    `;
  } else {
    blockEl.innerHTML = '<div class="no-data">No block data available</div>';
  }
}

// ===== Weekly Usage =====
const weeklyEl = document.getElementById('weeklyGauge');

if (currentWeek) {
  // Max 5x weekly estimate (~$500-600 based on data patterns)
  const weeklyLimit = 600;
  const weekPct = Math.min(100, (currentWeek.totalCost / weeklyLimit) * 100);
  const daysInWeek = (() => {
    const ws = new Date(currentWeek.week);
    const now = new Date();
    return Math.min(7, Math.max(1, Math.ceil((now - ws) / 86400000)));
  })();
  const dailyAvg = currentWeek.totalCost / daysInWeek;
  const projected = dailyAvg * 7;
  const projPct = Math.min(100, (projected / weeklyLimit) * 100);

  weeklyEl.innerHTML = `
    <div class="progress-section">
      <div class="progress-header">
        <span class="progress-label">Weekly Cost</span>
        <span class="progress-value" style="color:${getColor(weekPct)}">${formatCost(currentWeek.totalCost)}</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill ${getColorClass(weekPct)}" style="width:${weekPct}%"></div>
      </div>
      <div class="progress-detail">
        <span>Week of ${currentWeek.week}</span>
        <span>~${formatCost(weeklyLimit)} limit (est.)</span>
      </div>
    </div>
    <div class="progress-section">
      <div class="progress-header">
        <span class="progress-label">Projected (7 days)</span>
        <span class="progress-value" style="color:${getColor(projPct)}">${formatCost(projected)}</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill ${getColorClass(projPct)}" style="width:${projPct}%"></div>
      </div>
      <div class="progress-detail">
        <span>Avg: ${formatCost(dailyAvg)}/day</span>
        <span>${daysInWeek}/7 days elapsed</span>
      </div>
    </div>
    <div class="progress-section">
      <div class="progress-header">
        <span class="progress-label">Total Tokens</span>
        <span class="progress-value" style="color:var(--accent)">${formatTokens(currentWeek.totalTokens)}</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill low" style="width:${Math.min(100, weekPct)}%"></div>
      </div>
    </div>
  `;
} else {
  weeklyEl.innerHTML = '<div class="no-data">No weekly data available</div>';
}

// ===== Block Timeline =====
const timelineEl = document.getElementById('blockTimeline');

if (realBlocks.length > 0) {
  const maxCost = Math.max(...realBlocks.map(b => b.costUSD));

  let barsHtml = '<div class="timeline">';
  let labelsHtml = '<div class="timeline-labels">';

  realBlocks.forEach(block => {
    const pct = maxCost > 0 ? (block.costUSD / maxCost) * 100 : 0;
    const costPctEst = (block.costUSD / 40) * 100;
    const color = getColor(costPctEst);
    const startT = new Date(block.startTime);
    const dateStr = `${startT.getMonth()+1}/${startT.getDate()}`;
    const timeStr = startT.toLocaleTimeString('ja-JP', {hour:'2-digit',minute:'2-digit'});
    const status = block.isActive ? ' (ACTIVE)' : '';

    barsHtml += `
      <div class="timeline-bar" style="height:${Math.max(4, pct)}%;background:${color}">
        <div class="tooltip">
          ${dateStr} ${timeStr}${status}<br>
          Cost: ${formatCost(block.costUSD)}<br>
          Tokens: ${formatTokens(block.totalTokens)}<br>
          Models: ${block.models.join(', ')}
        </div>
      </div>`;
    labelsHtml += `<span>${timeStr}</span>`;
  });

  barsHtml += '</div>';
  labelsHtml += '</div>';
  timelineEl.innerHTML = barsHtml + labelsHtml;
} else {
  timelineEl.innerHTML = '<div class="no-data">No block data</div>';
}

// ===== Model Breakdown =====
const modelEl = document.getElementById('modelBreakdown');

if (currentWeek && currentWeek.modelBreakdowns) {
  const models = currentWeek.modelBreakdowns;
  const maxModelCost = Math.max(...models.map(m => m.cost));
  const colors = {'claude-opus-4-6':'#7c6ff0', 'claude-sonnet-4-6':'#4ecdc4', 'claude-haiku-4-5-20251001':'#f0a030'};
  const names = {'claude-opus-4-6':'Opus 4.6', 'claude-sonnet-4-6':'Sonnet 4.6', 'claude-haiku-4-5-20251001':'Haiku 4.5'};

  let html = '<ul class="model-list">';
  models.sort((a,b) => b.cost - a.cost).forEach(m => {
    const color = colors[m.modelName] || '#888';
    const name = names[m.modelName] || m.modelName;
    const pct = maxModelCost > 0 ? (m.cost / maxModelCost) * 100 : 0;
    const sharePct = (m.cost / currentWeek.totalCost * 100).toFixed(1);
    html += `
      <li class="model-item">
        <div class="model-dot" style="background:${color}"></div>
        <span class="model-name">${name} <span style="color:var(--text-dim)">(${sharePct}%)</span></span>
        <div class="model-bar-track">
          <div class="model-bar-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <span class="model-cost" style="color:${color}">${formatCost(m.cost)}</span>
      </li>`;
  });
  html += '</ul>';
  modelEl.innerHTML = html;
} else {
  modelEl.innerHTML = '<div class="no-data">No model data</div>';
}

// ===== Weekly Stats =====
const statsEl = document.getElementById('weeklyStats');

if (currentWeek) {
  const daysInWeek = (() => {
    const ws = new Date(currentWeek.week);
    const now = new Date();
    return Math.min(7, Math.max(1, Math.ceil((now - ws) / 86400000)));
  })();
  const blocksThisWeek = realBlocks.filter(b => {
    const bDate = new Date(b.startTime);
    const weekStart = new Date(currentWeek.week);
    return bDate >= weekStart;
  }).length;

  statsEl.innerHTML = `
    <div class="stats-row" style="grid-template-columns:1fr 1fr;margin-bottom:16px">
      <div class="stat-item">
        <div class="stat-value">${formatCost(currentWeek.totalCost)}</div>
        <div class="stat-label">Total Cost</div>
      </div>
      <div class="stat-item">
        <div class="stat-value">${formatTokens(currentWeek.totalTokens)}</div>
        <div class="stat-label">Total Tokens</div>
      </div>
    </div>
    <div class="stats-row" style="grid-template-columns:1fr 1fr">
      <div class="stat-item">
        <div class="stat-value">${blocksThisWeek}</div>
        <div class="stat-label">Blocks Used</div>
      </div>
      <div class="stat-item">
        <div class="stat-value">${daysInWeek}/7</div>
        <div class="stat-label">Days Elapsed</div>
      </div>
    </div>
    <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">
      <div class="stats-row" style="grid-template-columns:1fr 1fr 1fr">
        <div class="stat-item">
          <div class="stat-value" style="font-size:16px;color:#7c6ff0">${formatTokens(currentWeek.inputTokens)}</div>
          <div class="stat-label">Input</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="font-size:16px;color:#4ecdc4">${formatTokens(currentWeek.outputTokens)}</div>
          <div class="stat-label">Output</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="font-size:16px;color:#f0a030">${formatTokens(currentWeek.cacheReadTokens)}</div>
          <div class="stat-label">Cache Read</div>
        </div>
      </div>
    </div>
  `;
} else {
  statsEl.innerHTML = '<div class="no-data">No stats available</div>';
}

</script>
</body>
</html>
SCRIPTEOF

echo "Dashboard generated: $DASHBOARD_FILE"
open "$DASHBOARD_FILE"
