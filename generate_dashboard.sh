#!/bin/bash
# score_history.txt を読み込んで score_dashboard.html を生成する
# 既定では GAMEOVER/STOP のみフルダッシュボードを生成する。
# MOVE中も表示したい場合は DASHBOARD_SHOW_WHILE_PLAYING=1 を指定する。
cd "$(dirname "$0")"

# 引数でゲーム状態を受け取る
if [ -n "$1" ]; then
    GAME_STATE="$1"
else
    GAME_STATE="MOVE"
fi

DASHBOARD_SHOW_WHILE_PLAYING="${DASHBOARD_SHOW_WHILE_PLAYING:-0}"

# 非GAMEOVER時は空HTML（OBSで非表示）
if [ "$GAME_STATE" != "GAMEOVER" ] && [ "$GAME_STATE" != "STOP" ] && [ "$DASHBOARD_SHOW_WHILE_PLAYING" = "0" ]; then
    cat > score_dashboard.html <<'EMPTYEOF'
<!DOCTYPE html><html><head><meta charset="UTF-8">
</head>
<body style="background:transparent">
<script>setInterval(function(){location.reload();},3000);</script>
</body></html>
EMPTYEOF
    exit 0
fi

# スコアデータをJSON配列に変換 (1行1スコア形式)
SCORES_JSON=$(awk -F'\t' 'NF {
  n++
  if ($2 != "") { print "{\"ts\":\"" $1 "\",\"game\":" n ",\"score\":" $2 "}" }
  else if ($1 ~ /^[0-9]+$/) { print "{\"ts\":null,\"game\":" n ",\"score\":" $1 "}" }
}' score_history.txt | paste -sd, -)

cat > score_dashboard.html <<HTMLEOF
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Soren eloop Score Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: transparent;
    color: #e0e0e0;
    font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
    padding: 20px;
  }
  .stats-bar {
    display: flex;
    justify-content: center;
    gap: 16px;
    margin-bottom: 16px;
    flex-wrap: wrap;
    max-width: 1400px;
    margin-left: auto;
    margin-right: auto;
  }
  .stat {
    text-align: center;
    background: rgba(17,17,39,0.95);
    padding: 10px 24px;
    border-radius: 10px;
    border: 1px solid #333;
    flex: 1;
    min-width: 140px;
  }
  .stat-label {
    font-size: 0.75em;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .stat-value { font-size: 1.8em; font-weight: bold; }
  .stat-value.best { color: #ffd700; }
  .stat-value.second { color: #c0c0c0; }
  .stat-value.third { color: #cd7f32; }
  .stat-value.avg { color: #4ecdc4; }
  .stat-value.games { color: #a78bfa; }
  .stat-value.recent { color: #f97316; }
  .stat-value.trend { font-size: 1.15em; }
  .stat-value.trend-up { color: #86efac; }
  .stat-value.trend-flat { color: #94a3b8; }
  .stat-value.trend-down { color: #fb923c; }
  .rank-label { font-size: 0.6em; vertical-align: super; margin-right: 2px; }
  .chart-container {
    position: relative;
    width: 100%;
    max-width: 1400px;
    margin: 0 auto;
    background: rgba(17,17,39,0.95);
    border-radius: 12px;
    padding: 20px 20px 10px 20px;
    border: 1px solid #333;
  }
  canvas { width: 100%; display: block; }
  .refresh-indicator {
    position: fixed;
    top: 10px;
    right: 16px;
    font-size: 0.7em;
    color: #555;
  }
  .legend {
    display: flex;
    justify-content: center;
    gap: 24px;
    margin-top: 10px;
    font-size: 0.8em;
    flex-wrap: wrap;
  }
  .legend-item { display: flex; align-items: center; gap: 6px; }
  .legend-dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
</style>
</head>
<body>

<div id="dashboard">
<div class="stats-bar">
  <div class="stat"><div class="stat-label">1st Best</div><div class="stat-value best" id="best">-</div></div>
  <div class="stat"><div class="stat-label">2nd Best</div><div class="stat-value second" id="second">-</div></div>
  <div class="stat"><div class="stat-label">3rd Best</div><div class="stat-value third" id="third">-</div></div>
  <div class="stat"><div class="stat-label">Average</div><div class="stat-value avg" id="avg">-</div></div>
  <div class="stat"><div class="stat-label">Games</div><div class="stat-value games" id="games">-</div></div>
  <div class="stat"><div class="stat-label">Recent 10 Avg</div><div class="stat-value recent" id="recent">-</div></div>
  <div class="stat"><div class="stat-label">Trend</div><div class="stat-value trend" id="trend">-</div></div>
</div>
<div class="chart-container">
  <canvas id="chart"></canvas>
  <div class="legend">
    <div class="legend-item"><span class="legend-dot" style="background:#4ecdc4"></span> Score</div>
    <div class="legend-item"><span class="legend-dot" style="background:#ffd700"></span> Best</div>
    <div class="legend-item"><span class="legend-dot" style="background:rgba(255,107,107,0.8)"></span> 10-game Moving Avg</div>
    <div class="legend-item"><span class="legend-dot" style="background:#f8fafc;border:2px solid #111827"></span> Overall Trend (green=up / white=stable / red=down)</div>
    <div class="legend-item"><span class="legend-dot" style="background:rgba(78,205,196,0.15)"></span> Overall Avg</div>
  </div>
</div>
</div>

<div class="refresh-indicator" id="refreshInfo">Generated: $(date '+%H:%M:%S')</div>
<script>
const SCORES = [${SCORES_JSON}];
const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');

function movingAvg(scores, w) {
  return scores.map((_, i) => {
    const sl = scores.slice(Math.max(0, i - w + 1), i + 1);
    return sl.reduce((s, d) => s + d.score, 0) / sl.length;
  });
}

function linearTrend(scores) {
  const n = scores.length;
  if (!n) return { slope: 0, intercept: 0, r2: 0, y0: 0, yN: 0, dir: 'flat' };
  if (n === 1) {
    const y = scores[0].score;
    return { slope: 0, intercept: y, r2: 1, y0: y, yN: y, dir: 'flat' };
  }

  let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0, sumYY = 0;
  scores.forEach((d, i) => {
    const x = i;
    const y = d.score;
    sumX += x;
    sumY += y;
    sumXY += x * y;
    sumXX += x * x;
    sumYY += y * y;
  });

  const den = n * sumXX - sumX * sumX;
  const slope = den === 0 ? 0 : (n * sumXY - sumX * sumY) / den;
  const intercept = (sumY - slope * sumX) / n;

  const corrDen = Math.sqrt(
    Math.max(0, (n * sumXX - sumX * sumX) * (n * sumYY - sumY * sumY))
  );
  const corrNum = n * sumXY - sumX * sumY;
  const r = corrDen === 0 ? 0 : corrNum / corrDen;
  const r2 = Math.max(0, Math.min(1, r * r));

  const y0 = intercept;
  const yN = intercept + slope * (n - 1);
  const absSlope = Math.abs(slope);
  const dir = absSlope < 1.5 ? 'flat' : (slope > 0 ? 'up' : 'down');
  return { slope, intercept, r2, y0, yN, dir };
}

function drawChart(scores) {
  if (!scores.length) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const W = rect.width - 40;
  const H = Math.min(500, window.innerHeight - 260);
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const sorted = scores.map(d => d.score).sort((a, b) => b - a);
  const unique = [...new Set(sorted)];
  const maxScore = unique[0] || 0;
  const secondScore = unique[1] || '-';
  const thirdScore = unique[2] || '-';
  const avgScore = scores.reduce((s, d) => s + d.score, 0) / scores.length;
  const recent10 = scores.slice(-10);
  const recent10Avg = recent10.reduce((s, d) => s + d.score, 0) / recent10.length;
  const ma = movingAvg(scores, 10);
  const trend = linearTrend(scores);

  document.getElementById('best').textContent = maxScore;
  document.getElementById('second').textContent = secondScore;
  document.getElementById('third').textContent = thirdScore;
  document.getElementById('avg').textContent = Math.round(avgScore);
  document.getElementById('games').textContent = scores.length;
  document.getElementById('recent').textContent = Math.round(recent10Avg);
  const trendEl = document.getElementById('trend');
  const slopeRounded = (Math.round(trend.slope * 10) / 10).toFixed(1);
  const fitPct = Math.round(trend.r2 * 100);
  if (trend.dir === 'up') {
    trendEl.textContent = 'UP ' + '+' + slopeRounded + '/g';
  } else if (trend.dir === 'down') {
    trendEl.textContent = 'DOWN ' + slopeRounded + '/g';
  } else {
    trendEl.textContent = 'STABLE ' + slopeRounded + '/g';
  }
  trendEl.title = 'line fit=' + fitPct + '%';
  trendEl.classList.remove('trend-up', 'trend-flat', 'trend-down');
  trendEl.classList.add(
    trend.dir === 'up' ? 'trend-up' : (trend.dir === 'down' ? 'trend-down' : 'trend-flat')
  );

  const padL = 55, padR = 20, padT = 20, padB = 40;
  const cW = W - padL - padR, cH = H - padT - padB;
  const yMax = Math.ceil(maxScore / 500) * 500 + 200;
  const tTimes = scores.map((d,i) => d.ts ? new Date(d.ts).getTime() : i);
  const tMin = tTimes[0], tMax = tTimes[tTimes.length-1];
  const tRange = tMax - tMin || 1;
  const xScale = i => padL + ((tTimes[i] - tMin) / tRange) * cW;
  const yScale = v => padT + cH - (v / yMax) * cH;

  ctx.clearRect(0, 0, W, H);

  ctx.strokeStyle = '#1e1e3a'; ctx.lineWidth = 1;
  ctx.font = '11px monospace'; ctx.fillStyle = '#555';
  for (let i = 0; i <= 5; i++) {
    const v = (yMax / 5) * i, y = yScale(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillText(Math.round(v), 4, y + 4);
  }

  const xi = Math.max(1, Math.ceil(scores.length / 15));
  ctx.fillStyle = '#555';
  const spanMs = tMax - tMin;
  const dateFmt = spanMs > 7*86400000
    ? d => (d.getMonth()+1)+'/'+d.getDate()
    : spanMs > 86400000
      ? d => (d.getMonth()+1)+'/'+d.getDate()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')
      : d => String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
  for (let i = 0; i < scores.length; i += xi) {
    const label = scores[i].ts ? dateFmt(new Date(scores[i].ts)) : String(scores[i].game);
    ctx.fillText(label, xScale(i) - 16, H - 8);
  }

  ctx.fillStyle = 'rgba(78,205,196,0.08)';
  ctx.fillRect(padL, yScale(avgScore) - 1, cW, 2);
  ctx.fillStyle = 'rgba(78,205,196,0.3)'; ctx.font = '10px monospace';
  ctx.fillText('avg ' + Math.round(avgScore), padL + 4, yScale(avgScore) - 5);

  ctx.strokeStyle = 'rgba(255,215,0,0.3)'; ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.moveTo(padL, yScale(maxScore)); ctx.lineTo(W - padR, yScale(maxScore)); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(255,215,0,0.5)';
  ctx.fillText('best ' + maxScore, padL + 4, yScale(maxScore) - 5);

  for (let i = 0; i < scores.length; i++) {
    const x = xScale(i), y = yScale(scores[i].score);
    const bW = Math.max(1.5, cW / scores.length * 0.5);
    const ratio = scores[i].score / yMax;
    ctx.fillStyle = \`rgba(\${Math.round(78+ratio*177)},\${Math.round(205-ratio*105)},\${Math.round(196-ratio*100)},0.6)\`;
    ctx.fillRect(x - bW/2, y, bW, yScale(0) - y);
  }

  for (let i = 0; i < scores.length; i++) {
    ctx.beginPath();
    ctx.arc(xScale(i), yScale(scores[i].score), 2.5, 0, Math.PI * 2);
    ctx.fillStyle = scores[i].score === maxScore ? '#ffd700'
                  : scores[i].score === secondScore ? '#c0c0c0'
                  : scores[i].score === thirdScore ? '#cd7f32'
                  : '#4ecdc4';
    ctx.fill();
  }

  ctx.strokeStyle = 'rgba(255,107,107,0.8)'; ctx.lineWidth = 2;
  ctx.beginPath();
  ma.forEach((v, i) => { const x = xScale(i), y = yScale(v); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
  ctx.stroke();

  const trendColor = trend.dir === 'up'
    ? '#00ff85'
    : (trend.dir === 'down' ? '#ff4d4f' : '#f8fafc');
  const trendDash = trend.dir === 'flat' ? [4, 7] : [14, 8];
  ctx.setLineDash(trendDash);
  ctx.strokeStyle = 'rgba(0,0,0,0.7)';
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.moveTo(xScale(0), yScale(trend.y0));
  ctx.lineTo(xScale(scores.length - 1), yScale(trend.yN));
  ctx.stroke();
  ctx.strokeStyle = trendColor;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(xScale(0), yScale(trend.y0));
  ctx.lineTo(xScale(scores.length - 1), yScale(trend.yN));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = trendColor;
  ctx.font = '10px monospace';
  const trendLabel = 'trend ' + trend.dir + ' ' +
    (trend.slope >= 0 ? '+' : '') + slopeRounded + '/game fit=' + fitPct + '%';
  const trendMidY = yScale((trend.y0 + trend.yN) / 2);
  const trendLabelY = Math.max(padT + 12, Math.min(H - padB - 8, trendMidY - 8));
  const trendLabelX = padL + 4;
  const trendLabelW = ctx.measureText(trendLabel).width + 8;
  ctx.fillStyle = 'rgba(0,0,0,0.65)';
  ctx.fillRect(trendLabelX - 4, trendLabelY - 9, trendLabelW, 14);
  ctx.fillStyle = trendColor;
  ctx.fillText(trendLabel, trendLabelX, trendLabelY);
  const trendStartX = xScale(0), trendStartY = yScale(trend.y0);
  const trendEndX = xScale(scores.length - 1), trendEndY = yScale(trend.yN);
  ctx.beginPath(); ctx.arc(trendStartX, trendStartY, 3, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(trendEndX, trendEndY, 3, 0, Math.PI * 2); ctx.fill();

  // Highlight top 3 scores on chart
  const rankMarkers = [
    { score: maxScore, color: '#ffd700', label: '1st' },
    { score: secondScore, color: '#c0c0c0', label: '2nd' },
    { score: thirdScore, color: '#cd7f32', label: '3rd' },
  ];
  rankMarkers.forEach(r => {
    if (typeof r.score !== 'number') return;
    const ri = scores.findIndex(d => d.score === r.score);
    if (ri < 0) return;
    const rx = xScale(ri), ry = yScale(r.score);
    ctx.beginPath(); ctx.arc(rx, ry, 6, 0, Math.PI * 2);
    ctx.strokeStyle = r.color; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = r.color; ctx.font = 'bold 12px monospace';
    ctx.fillText(r.score, rx + 10, ry - 4);
  });
}

drawChart(SCORES);
window.addEventListener('resize', () => drawChart(SCORES));

// Auto-reload every 3 seconds (OBS CEF doesn't support meta refresh on file://)
setInterval(function(){location.reload();},3000);
</script>
</body>
</html>
HTMLEOF

echo "Generated score_dashboard.html ($(echo "[${SCORES_JSON}]" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))') games)"
