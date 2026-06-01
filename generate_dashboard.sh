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
SOREN91_MODE_FLAG_FILE="${SOREN91_MODE_FLAG_FILE:-tmp/.soren91_mode_active}"
MANUAL_MERIKEN_MODE_FILE="${MANUAL_MERIKEN_MODE_FILE:-tmp/state/manual_meriken_mode.json}"
SOREN91_PID_FILE="${SOREN91_PID_FILE:-soren91/tmp/soren91.pid}"
SOREN91_MAIN_PID_FILE="${SOREN91_MAIN_PID_FILE:-soren91/tmp/main.pid}"

write_empty_dashboard() {
	cat >score_dashboard.html <<'EMPTYEOF'
<!DOCTYPE html><html><head><meta charset="UTF-8">
</head>
<body style="background:transparent">
<script>setInterval(function(){location.reload();},3000);</script>
</body></html>
EMPTYEOF
	chmod 644 score_dashboard.html 2>/dev/null || true
}

manual_meriken_mode_file_enabled() {
	[ -f "$MANUAL_MERIKEN_MODE_FILE" ] || return 1
	python3 - "$MANUAL_MERIKEN_MODE_FILE" <<'PY' >/dev/null 2>&1
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    sys.exit(1)
sys.exit(0 if data.get("enabled") else 1)
PY
}

soren91_pid_file_alive() {
	local f="" pid="" cmd=""
	for f in "$SOREN91_MAIN_PID_FILE" "$SOREN91_PID_FILE"; do
		[ -f "$f" ] || continue
		pid=$(cat "$f" 2>/dev/null)
		case "$pid" in '' | *[!0-9]*) continue ;; esac
		kill -0 "$pid" 2>/dev/null || continue
		cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
		case "$cmd" in
		*main.mjs* | *run_player_loop.sh*) return 0 ;;
		esac
	done
	return 1
}

# 非GAMEOVER時は空HTML（OBSで非表示）
if [ "$GAME_STATE" != "GAMEOVER" ] && [ "$GAME_STATE" != "STOP" ] && [ "$DASHBOARD_SHOW_WHILE_PLAYING" = "0" ]; then
	write_empty_dashboard
	exit 0
fi

# メリケンAI中はSoren91側の画面を優先し、プレイ中のダッシュボードだけ非表示にする。
# GAMEOVER/STOP はメインゲーム終了時に OBS で show されるため、soren91 タブや
# stale PID が残っていても空HTMLで上書きしない。
if [ "$GAME_STATE" != "GAMEOVER" ] && [ "$GAME_STATE" != "STOP" ]; then
	if [ -f "$SOREN91_MODE_FLAG_FILE" ] || manual_meriken_mode_file_enabled || soren91_pid_file_alive; then
		write_empty_dashboard
		exit 0
	fi
fi

# 全履歴をHTMLへ丸ごと埋め込むとOBS側が重くなるため、統計は生成時に集計し、
# グラフ描画用の点列だけ直近N件へ絞る。
DASHBOARD_CHART_GAMES="${DASHBOARD_CHART_GAMES:-300}"
case "$DASHBOARD_CHART_GAMES" in '' | *[!0-9]*) DASHBOARD_CHART_GAMES=300 ;; esac

DASHBOARD_DATA_JSON=$(python3 dashboard_data.py "$DASHBOARD_CHART_GAMES")

# データ取得が失敗 (空 JSON) なら、既存 HTML を温存して即終了。
# OBS で表示中の dashboard を空ファイルで上書きしてしまう事故を防ぐ。
if [ -z "$DASHBOARD_DATA_JSON" ] || [ "$DASHBOARD_DATA_JSON" = "{}" ]; then
	echo "[generate_dashboard] WARN: dashboard_data.py returned empty; keeping existing HTML" >&2
	exit 0
fi

# アトミック書き込み: temp に書き、成功時のみ rename する。
# 途中で SIGPIPE / disk full 等で失敗すると、score_dashboard.html は 0 byte の
# 中途半端な状態になりうるため、それを避ける。
# 重要: temp は同じディレクトリに作る。/tmp や /var/folders に作って mv すると
# クロスデバイスコピーになり、umask が適用されてパーミッション 600 に落ちる。
# OBS から読めなくなる原因になりうる。
__DASH_TMP=$(mktemp "./score_dashboard.XXXXXX.html" 2>/dev/null) || __DASH_TMP="./score_dashboard.html.tmp"
trap '[ -n "$__DASH_TMP" ] && [ -f "$__DASH_TMP" ] && rm -f "$__DASH_TMP"' EXIT

cat >"$__DASH_TMP" <<HTMLEOF
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
  html {
    width: 1940px;
    height: 1080px;
    overflow: hidden;
    background: transparent;
  }
  body {
    width: 1940px;
    height: 1080px;
    overflow: hidden;
    background: transparent;
    color: #e0e0e0;
    font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
    padding: 18px 22px;
  }
  #dashboard {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .stats-bar {
    display: flex;
    justify-content: center;
    gap: 14px;
    margin-bottom: 16px;
    flex-wrap: wrap;
    width: 100%;
    flex: 0 0 auto;
  }
  .stat {
    text-align: center;
    background: rgba(17,17,39,0.95);
    padding: 18px 24px;
    border-radius: 10px;
    border: 1px solid #333;
    flex: 1;
    min-width: 168px;
    min-height: 150px;
  }
  .stat-label {
    font-size: 1.05em;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .stat-value { font-size: 4.35em; font-weight: bold; line-height: 1.02; }
  .stat-value.best { color: #ffd700; }
  .stat-value.avg { color: #4ecdc4; }
  .stat-value.games { color: #a78bfa; }
  .stat-value.eval, .mini-value.eval { color: #22d3ee; }
  .stat-value.recent, .mini-value.recent { color: #f97316; }
  .stat-value.trend, .mini-value.trend { font-size: 3.3em; line-height: 1.05; }
  .stat-value.trend-up, .mini-value.trend-up { color: #86efac; }
  .stat-value.trend-flat, .mini-value.trend-flat { color: #94a3b8; }
  .stat-value.trend-down, .mini-value.trend-down { color: #fb923c; }
  .stat-value.russia, .mini-value.russia { color: #facc15; }
  .stat-value.hot, .mini-value.hot { color: #fb7185; }
  .stat-value.cool, .mini-value.cool { color: #38bdf8; }
  .rank-label { font-size: 0.6em; vertical-align: super; margin-right: 2px; }
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(220px, 1fr));
    gap: 10px;
    width: 100%;
    margin: 0 0 12px 0;
    flex: 0 0 auto;
  }
  .mini-stat {
    background: rgba(17,17,39,0.92);
    border: 1px solid #2c2c45;
    border-radius: 8px;
    padding: 10px 12px;
    min-height: 112px;
  }
  .mini-label {
    color: #8b8fa3;
    font-size: 0.85em;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .mini-value {
    color: #e5e7eb;
    font-size: 2.45em;
    font-weight: 700;
    line-height: 1.12;
  }
  #gateFocus {
    font-size: 1.7em;
    line-height: 1.1;
    word-break: keep-all;
  }
  .mini-sub {
    color: #7c8197;
    font-size: 0.78em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .mini-lines {
    margin-top: 4px;
    display: grid;
    gap: 2px;
    color: #8b91aa;
    font-size: 0.82em;
    line-height: 1.18;
  }
  .mini-lines b {
    color: #d7dde9;
    font-weight: 800;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .metric-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 12px;
  }
  .stat .metric-row { margin-top: 4px; }
  .mini-stat .metric-row { margin-top: 2px; }
  .donut {
    --pct: 0%;
    --ring: #94a3b8;
    position: relative;
    width: 50px;
    height: 50px;
    flex: 0 0 auto;
    border-radius: 50%;
    background:
      radial-gradient(circle at center, #111127 0 52%, transparent 53%),
      conic-gradient(var(--ring) var(--pct), rgba(148,163,184,0.18) 0);
    border: 1px solid rgba(148, 163, 184, 0.14);
    box-shadow: 0 0 12px rgba(148, 163, 184, 0.22);
  }
  .stat .donut {
    width: 82px;
    height: 82px;
  }
  .donut span {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: #d7dde9;
    font-size: 0.78em;
    font-weight: 800;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .stat .donut span { font-size: 0.92em; }
  .trend-badge {
    width: 56px;
    min-height: 48px;
    border-radius: 8px;
    border: 1px solid rgba(148, 163, 184, 0.18);
    background: rgba(148, 163, 184, 0.10);
    display: grid;
    grid-template-rows: auto auto;
    place-items: center;
    padding: 5px 4px;
  }
  .stat .trend-badge { width: 76px; min-height: 64px; padding: 7px 5px; }
  .trend-badge .arrow {
    font-size: 1.6em;
    line-height: 0.9;
    font-weight: 900;
  }
  .stat .trend-badge .arrow { font-size: 2.35em; }
  .trend-badge .delta {
    margin-top: 3px;
    font-size: 0.72em;
    font-weight: 800;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: #cbd5e1;
  }
  .stat .trend-badge .delta { font-size: 0.88em; margin-top: 4px; }
  .trend-badge.up {
    color: #86efac;
    border-color: rgba(134, 239, 172, 0.34);
    background: rgba(34, 197, 94, 0.14);
    box-shadow: inset 0 0 18px rgba(34, 197, 94, 0.10);
  }
  .trend-badge.flat {
    color: #cbd5e1;
    background: rgba(148, 163, 184, 0.12);
  }
  .trend-badge.down {
    color: #fb923c;
    border-color: rgba(251, 146, 60, 0.34);
    background: rgba(249, 115, 22, 0.14);
    box-shadow: inset 0 0 18px rgba(249, 115, 22, 0.10);
  }
  @media (max-width: 1400px) {
    .stats-grid { grid-template-columns: repeat(4, minmax(180px, 1fr)); }
  }
  .chart-container {
    position: relative;
    width: 100%;
    min-height: 0;
    margin: 0;
    background: rgba(17,17,39,0.95);
    border-radius: 12px;
    padding: 10px 18px;
    border: 1px solid #333;
    flex: 1 1 auto;
    overflow: hidden;
  }
  .charts-wrap {
    display: flex;
    flex-direction: column;
    gap: 10px;
    flex: 1 1 auto;
    min-height: 0;
  }
  .chart-main { flex: 1 1 auto; min-height: 0; }
  .chart-rate {
    flex: 0 0 210px;
    padding: 8px 18px 6px 18px;
    display: flex;
    flex-direction: column;
  }
  .rate-chart-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    color: #8b93a7;
    font-size: 0.82em;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin-bottom: 2px;
    flex: 0 0 auto;
  }
  .rate-chart-title { white-space: nowrap; }
  .rate-chart-current {
    color: #facc15;
    font-weight: 800;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 1.05em;
    text-transform: none;
    letter-spacing: 0;
  }
  .rate-chart-current.zero { color: #fb7185; }
  .rate-chart-current.high { color: #86efac; }
  #rateChart {
    flex: 1 1 auto;
    min-height: 0;
  }
  canvas { width: 100%; display: block; }
  .refresh-indicator {
    position: fixed;
    top: 10px;
    right: 16px;
    font-size: 0.7em;
    color: #555;
  }
  .legend { display: none; }
</style>
</head>
<body>

<div id="dashboard">
<div class="stats-bar">
  <div class="stat"><div class="stat-label">All Best</div><div class="metric-row"><div class="stat-value best" id="best">-</div><div class="donut" id="bestDonut"><span>-</span></div></div></div>
  <div class="stat"><div class="stat-label">All Games</div><div class="metric-row"><div class="stat-value games" id="games">-</div><div class="donut" id="gamesDonut"><span>-</span></div></div></div>
  <div class="stat"><div class="stat-label">All Avg</div><div class="metric-row"><div class="stat-value avg" id="avg">-</div><div class="trend-badge" id="avgTrend"><div class="arrow">→</div><div class="delta">0</div></div></div></div>
  <div class="stat"><div class="stat-label">All Russia</div><div class="metric-row"><div class="stat-value russia" id="russiaRate">-</div><div class="donut" id="russiaRateDonut"><span>-</span></div></div></div>
</div>
<div class="stats-grid">
  <div class="mini-stat"><div class="mini-label">Eval Score</div><div class="metric-row"><div class="mini-value eval" id="evalAvg">-</div><div class="trend-badge" id="evalRecent100Trend"><div class="arrow">→</div><div class="delta">0</div></div></div><div class="mini-lines"><div>best <b id="evalBest">-</b> / r100 <b id="evalRecent100Avg">-</b></div><div>p90 <b id="evalRecentP90">-</b> / <span id="evalAvgSub">-</span></div></div></div>
  <div class="mini-stat"><div class="mini-label">Raw Recent</div><div class="metric-row"><div class="mini-value recent" id="recent100Avg">-</div><div class="trend-badge" id="recent100Trend"><div class="arrow">→</div><div class="delta">0</div></div></div><div class="mini-lines"><div>r10 <b id="recent10Avg">-</b> / r50 <b id="recent50Avg">-</b></div><div>best <b id="recentBest">-</b> / med <b id="recentMedian">-</b></div></div></div>
  <div class="mini-stat"><div class="mini-label">Raw Band</div><div class="metric-row"><div class="mini-value cool" id="recentP90">-</div><div class="donut" id="recentP90Donut"><span>-</span></div></div><div class="mini-lines"><div>3000+ <b id="recent3000">-</b> <span id="recent3000Sub"></span></div><div>2000+ <b id="recent2000">-</b> <span id="recent2000Sub"></span></div></div></div>
  <div class="mini-stat"><div class="mini-label">Purge Target</div><div class="metric-row"><div class="mini-value hot" id="gateFocus">-</div><div class="donut" id="gateFocusDonut"><span>-</span></div></div><div class="mini-sub" id="gateFocusSub">-</div></div>
  <div class="mini-stat"><div class="mini-label">Founding r100</div><div class="metric-row"><div class="mini-value cool" id="gateRussia">-</div><div class="donut" id="gateRussiaDonut"><span>-</span></div></div><div class="mini-lines"><div>T15 <b id="gateRussiaInline">-</b> / T14 <b id="gateKazakhstan">-</b></div><div>T13 <b id="gateUkraineInline">-</b> / <span id="gateFocusCountry">-</span></div></div><div style="display:none"><span id="gateTurkmenistan"></span><span id="gateTurkmenistanSub"></span><span id="gateUkraine"></span><span id="gateUkraineSub"></span><span id="gateKazakhstanSub"></span><span id="gateRussiaSub"></span></div></div>
  <div class="mini-stat"><div class="mini-label">Russia Now</div><div class="metric-row"><div class="mini-value russia" id="russiaRecent100">-</div><div class="donut" id="russiaRecent100Donut"><span>-</span></div></div><div class="mini-lines"><div>today <b id="russiaToday">-</b> / 24h <b id="russiaLast24h">-</b></div><div id="russiaRecent100Sub">-</div></div><div style="display:none"><span id="russiaTodaySub"></span></div></div>
  <div class="mini-stat"><div class="mini-label">Last Russia</div><div class="metric-row"><div class="mini-value russia" id="russiaLast">-</div><div class="donut" id="russiaLastDonut"><span>-</span></div></div><div class="mini-sub" id="russiaLastSub">-</div></div>
  <div class="mini-stat"><div class="mini-label">Recent Trend</div><div class="metric-row"><div class="mini-value trend" id="trend">-</div><div class="trend-badge" id="chartTrend"><div class="arrow">→</div><div class="delta">0</div></div></div><div class="mini-sub" id="trendSub">chart window</div></div>
</div>
<div class="charts-wrap">
<div class="chart-container chart-main">
  <canvas id="chart"></canvas>
</div>
<div class="chart-container chart-rate">
  <div class="rate-chart-header">
    <span class="rate-chart-title">ろシア建国率 rolling 100</span>
    <span class="rate-chart-current" id="rateChartCurrent">-</span>
  </div>
  <canvas id="rateChart"></canvas>
</div>
</div>
</div>

<div class="refresh-indicator" id="refreshInfo">Generated: $(date '+%H:%M:%S')</div>
<script>
const DASHBOARD_DATA = ${DASHBOARD_DATA_JSON};
const SCORES = DASHBOARD_DATA.chartScores;
const EVAL_SCORES = DASHBOARD_DATA.chartEvalScores || [];
const SCORE_STATS = DASHBOARD_DATA.scoreStats;
const EVAL_SCORE_STATS = DASHBOARD_DATA.evalScoreStats || null;
const RUSSIA_STATS = DASHBOARD_DATA.russiaStats;
const RUSSIA_RATE_SERIES = DASHBOARD_DATA.russiaRateSeries || { window: 100, step: 0, maxPoints: 300, current: null, points: [] };
const STAGE_GATE_STATS = DASHBOARD_DATA.stageGateStats || { window: 0, stages: [], focus: null };
const PURGE_TARGET_STATS = DASHBOARD_DATA.purgeTargetStats || {};
const CURRENT_GAME = SCORE_STATS.currentGame;
const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');
const rateCanvas = document.getElementById('rateChart');
const rateCtx = rateCanvas ? rateCanvas.getContext('2d') : null;

function clampPct(value, max) {
  if (!isFinite(value) || !isFinite(max) || max <= 0) return 0;
  return Math.max(0, Math.min(100, (value / max) * 100));
}

function setDonut(id, value, max, color, label) {
  const el = document.getElementById(id);
  if (!el) return;
  const pct = clampPct(value, max);
  el.style.setProperty('--pct', pct.toFixed(1) + '%');
  el.style.setProperty('--ring', color || '#94a3b8');
  const span = el.querySelector('span');
  if (span) span.textContent = label || Math.round(pct) + '%';
}

function setTrendBadge(id, delta, unit) {
  const el = document.getElementById(id);
  if (!el) return;
  const arrow = el.querySelector('.arrow');
  const deltaEl = el.querySelector('.delta');
  const absDelta = Math.abs(Number(delta) || 0);
  const dir = absDelta < 5 ? 'flat' : (delta > 0 ? 'up' : 'down');
  el.classList.remove('up', 'flat', 'down');
  el.classList.add(dir);
  if (arrow) arrow.textContent = dir === 'up' ? '▲' : (dir === 'down' ? '▼' : '→');
  if (deltaEl) {
    const signed = dir === 'flat' ? '±' : (delta > 0 ? '+' : '-');
    deltaEl.textContent = signed + Math.round(absDelta) + (unit || '');
  }
}

function updateExtraStats(scores) {
  const totalGames = CURRENT_GAME || scores.length;
  const now = new Date();
  const lastRussia = RUSSIA_STATS.last || null;
  const gamesSinceRussia = lastRussia ? Math.max(0, totalGames - lastRussia.game) : 100;

  document.getElementById('russiaRate').textContent = RUSSIA_STATS.rate.toFixed(2) + '%';
  document.getElementById('russiaRecent100').textContent = RUSSIA_STATS.recent100Rate.toFixed(2) + '%';
  document.getElementById('russiaRecent100Sub').textContent = RUSSIA_STATS.recent100 + '/100 recent';
  document.getElementById('russiaToday').textContent = String(RUSSIA_STATS.today);
  document.getElementById('russiaLast24h').textContent = String(RUSSIA_STATS.last24h);
  document.getElementById('russiaTodaySub').textContent = RUSSIA_STATS.last24h + '/24h';
  document.getElementById('russiaLast').textContent = lastRussia ? ('G' + lastRussia.game) : '-';
  document.getElementById('russiaLastSub').textContent = lastRussia ? ((totalGames - lastRussia.game) + ' games ago / ' + lastRussia.score + 'pt') : '-';
  const evalStats = EVAL_SCORE_STATS || {};
  const hasEvalStats = (evalStats.count || 0) > 0;
  document.getElementById('evalBest').textContent = hasEvalStats ? evalStats.best : '-';
  document.getElementById('evalAvg').textContent = hasEvalStats ? evalStats.average : '-';
  document.getElementById('evalAvgSub').textContent = hasEvalStats ? ('n=' + evalStats.count + ' / raw avg ' + SCORE_STATS.average) : 'eval_score_historyなし';
  document.getElementById('evalRecent100Avg').textContent = hasEvalStats ? evalStats.recent100Average : '-';
  document.getElementById('evalRecentP90').textContent = hasEvalStats ? evalStats.recent100P90 : '-';
  document.getElementById('recent10Avg').textContent = SCORE_STATS.recent10Average;
  document.getElementById('recent50Avg').textContent = SCORE_STATS.recent50Average;
  document.getElementById('recent100Avg').textContent = SCORE_STATS.recent100Average;
  document.getElementById('recentBest').textContent = SCORE_STATS.recent100Best || '-';
  document.getElementById('recentMedian').textContent = SCORE_STATS.recent100Median;
  document.getElementById('recentP90').textContent = SCORE_STATS.recent100P90;
  document.getElementById('recent3000').textContent = SCORE_STATS.recent100Score3000Rate.toFixed(2) + '%';
  document.getElementById('recent3000Sub').textContent = '(' + SCORE_STATS.recent100Score3000 + '/100)';
  document.getElementById('recent2000').textContent = SCORE_STATS.recent100Score2000Rate.toFixed(2) + '%';
  document.getElementById('recent2000Sub').textContent = '(' + SCORE_STATS.recent100Score2000 + '/100)';
  const gateStats = STAGE_GATE_STATS;
  const gateWindow = gateStats.window || 0;
  const gateHasSamples = gateWindow > 0;
  const stages = gateStats.stages || [];
  const stageByType = Object.fromEntries(stages.map((s) => [String(s.type), s]));
  const ukraine = stageByType['13'] || null;
  const kazakhstan = stageByType['14'] || null;
  const russia = stageByType['15'] || null;
  const purgeAnchor = PURGE_TARGET_STATS.anchor || {};
  const purgeCurrent = PURGE_TARGET_STATS.current || {};
  const purgeTarget = purgeAnchor.target || null;
  const currentTargetRate = purgeCurrent.targetRate || null;
  const thresholdPct = Number(PURGE_TARGET_STATS.thresholdPct || 0);
  const targetStatus = purgeCurrent.targetReached ? 'OK' : (purgeCurrent.purgeZone ? '粛清圏' : '未達');
  document.getElementById('gateFocus').textContent = purgeTarget ? purgeTarget.name : 'Inactive';
  document.getElementById('gateFocusSub').textContent = purgeTarget
    ? ('anchor ' + purgeTarget.rate.toFixed(1) + '% >= ' + thresholdPct.toFixed(0) + '% / current ' + targetStatus + ' best T' + (purgeCurrent.bestMaxType || 0))
    : ('anchor targetなし / threshold ' + thresholdPct.toFixed(0) + '%');
  document.getElementById('gateFocusCountry').textContent = purgeTarget
    ? ('target ' + purgeTarget.name + '(T' + purgeTarget.type + ') ' + targetStatus)
    : 'target none';
  document.getElementById('gateTurkmenistan').textContent = '-';
  document.getElementById('gateTurkmenistanSub').textContent = currentTargetRate ? (currentTargetRate.reached + ' / ' + currentTargetRate.total) : 'inactive';
  document.getElementById('gateUkraine').textContent = gateHasSamples && ukraine ? ukraine.rate.toFixed(1) + '%' : '-';
  document.getElementById('gateUkraineInline').textContent = gateHasSamples && ukraine ? ukraine.rate.toFixed(1) + '%' : '-';
  document.getElementById('gateUkraineSub').textContent = gateHasSamples && ukraine ? (ukraine.reached + ' / ' + gateWindow) : 'inactive';
  document.getElementById('gateKazakhstan').textContent = gateHasSamples && kazakhstan ? kazakhstan.rate.toFixed(1) + '%' : '-';
  document.getElementById('gateKazakhstanSub').textContent = gateHasSamples && kazakhstan ? (kazakhstan.reached + ' / ' + gateWindow) : 'inactive';
  document.getElementById('gateRussia').textContent = gateHasSamples && russia ? russia.rate.toFixed(1) + '%' : '-';
  document.getElementById('gateRussiaInline').textContent = gateHasSamples && russia ? russia.rate.toFixed(1) + '%' : '-';
  document.getElementById('gateRussiaSub').textContent = gateHasSamples && russia ? (russia.reached + ' / ' + gateWindow) : 'inactive';

  setDonut('bestDonut', SCORE_STATS.best, 6000, '#ffd700');
  setDonut('gamesDonut', SCORE_STATS.count % 1000, 1000, '#a78bfa', (SCORE_STATS.count % 1000) + '/1k');
  setDonut('russiaRateDonut', RUSSIA_STATS.rate, 2, '#facc15', RUSSIA_STATS.rate.toFixed(1) + '%');
  setTrendBadge('avgTrend', SCORE_STATS.recent100Average - SCORE_STATS.average, '');
  setTrendBadge('evalRecent100Trend', hasEvalStats ? evalStats.recent100Average - evalStats.average : 0, '');
  setTrendBadge('recent10Trend', SCORE_STATS.recent10Average - SCORE_STATS.recent50Average, '');
  setTrendBadge('recent50Trend', SCORE_STATS.recent50Average - SCORE_STATS.recent100Average, '');
  setTrendBadge('recent100Trend', SCORE_STATS.recent100Average - SCORE_STATS.average, '');
  setDonut('recentBestDonut', SCORE_STATS.recent100Best, SCORE_STATS.best || 1, '#fb7185');
  setTrendBadge('recentMedianTrend', SCORE_STATS.recent100Median - SCORE_STATS.recent100Average, '');
  setDonut('recentP90Donut', SCORE_STATS.recent100P90, 3500, '#38bdf8');
  setDonut('recent3000Donut', SCORE_STATS.recent100Score3000Rate, 100, '#fb7185', SCORE_STATS.recent100Score3000 + '/100');
  setDonut('recent2000Donut', SCORE_STATS.recent100Score2000Rate, 100, '#38bdf8', SCORE_STATS.recent100Score2000 + '/100');
  setDonut('gateFocusDonut', currentTargetRate ? currentTargetRate.rate : 0, 100, purgeCurrent.targetReached ? '#22c55e' : '#fb7185', purgeTarget ? (purgeCurrent.targetReached ? 'OK' : (purgeCurrent.purgeZone ? '圏' : 'NG')) : 'off');
  setDonut('gateTurkmenistanDonut', 0, 100, '#38bdf8', '-');
  setDonut('gateUkraineDonut', gateHasSamples && ukraine ? ukraine.rate : 0, 100, '#38bdf8', gateHasSamples && ukraine ? (ukraine.reached + '/' + gateWindow) : '-');
  setDonut('gateKazakhstanDonut', gateHasSamples && kazakhstan ? kazakhstan.rate : 0, 100, '#38bdf8', gateHasSamples && kazakhstan ? (kazakhstan.reached + '/' + gateWindow) : '-');
  setDonut('gateRussiaDonut', gateHasSamples && russia ? russia.rate : 0, 100, '#facc15', gateHasSamples && russia ? (russia.reached + '/' + gateWindow) : '-');
  setDonut('russiaRecent100Donut', RUSSIA_STATS.recent100Rate, 20, '#facc15', RUSSIA_STATS.recent100 + '/100');
  setDonut('russiaTodayDonut', RUSSIA_STATS.today, 20, '#facc15', String(RUSSIA_STATS.today));
  setDonut('russiaLastDonut', Math.max(0, 100 - gamesSinceRussia), 100, '#facc15', gamesSinceRussia + 'g');
}

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

function drawChart(scores, evalScores) {
  if (!scores.length) return;
  updateExtraStats(scores);
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const W = rect.width - 36;
  const H = Math.max(120, Math.floor(rect.height - 20));
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const sorted = scores.map(d => d.score).sort((a, b) => b - a);
  const visibleEvalScores = (evalScores || []).slice(-scores.length);
  const evalOffset = Math.max(0, scores.length - visibleEvalScores.length);
  const hasEvalSeries = visibleEvalScores.some(d => Number.isFinite(d.score));
  const evalSorted = visibleEvalScores.map(d => d.score).filter(Number.isFinite).sort((a, b) => b - a);
  const unique = [...new Set(sorted)];
  const chartMaxScore = unique[0] || 0;
  const chartSecondScore = unique[1] || '-';
  const chartThirdScore = unique[2] || '-';
  const avgScore = SCORE_STATS.average;
  const ma = movingAvg(scores, 10);
  const trend = linearTrend(scores);

  document.getElementById('best').textContent = SCORE_STATS.best;
  document.getElementById('avg').textContent = avgScore;
  document.getElementById('games').textContent = SCORE_STATS.count;
  document.getElementById('games').title = 'chart shows latest ' + scores.length + ' games';
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
  document.getElementById('trendSub').textContent = 'last ' + scores.length + ' games / fit ' + fitPct + '%';
  trendEl.classList.remove('trend-up', 'trend-flat', 'trend-down');
  trendEl.classList.add(
    trend.dir === 'up' ? 'trend-up' : (trend.dir === 'down' ? 'trend-down' : 'trend-flat')
  );
  setTrendBadge('chartTrend', trend.slope, '/g');

  const padL = 58, padR = 58, padT = 12, padB = 28;
  const cW = W - padL - padR, cH = H - padT - padB;
  const yMax = Math.ceil(Math.max(chartMaxScore, avgScore) / 500) * 500 + 200;
  const evalMaxScore = evalSorted[0] || 0;
  const evalAvgScore = EVAL_SCORE_STATS ? EVAL_SCORE_STATS.average : 0;
  const y2Max = Math.ceil(Math.max(evalMaxScore, evalAvgScore, 1) / 500) * 500 + 200;
  const xRange = Math.max(1, scores.length - 1);
  const xScale = i => padL + (i / xRange) * cW;
  const yScale = v => padT + cH - (v / yMax) * cH;
  const y2Scale = v => padT + cH - (v / y2Max) * cH;

  ctx.clearRect(0, 0, W, H);

  ctx.strokeStyle = '#2a2b55'; ctx.lineWidth = 1;
  ctx.font = '12px monospace'; ctx.fillStyle = '#8b93a7';
  for (let i = 0; i <= 3; i++) {
    const v = (yMax / 3) * i, y = yScale(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillText(Math.round(v), 4, y + 4);
  }
  if (hasEvalSeries) {
    ctx.fillStyle = '#facc15';
    for (let i = 0; i <= 3; i++) {
      const v = (y2Max / 3) * i, y = y2Scale(v);
      ctx.fillText(Math.round(v), W - padR + 4, y + 4);
    }
  }
  ctx.fillStyle = '#4ecdc4';
  ctx.fillText('raw', 4, padT + 12);
  if (hasEvalSeries) {
    ctx.fillStyle = '#facc15';
    ctx.fillText('eval', W - padR + 4, padT + 12);
  }

  const xi = Math.max(1, Math.ceil(scores.length / 8));
  ctx.fillStyle = '#8b93a7';
  for (let i = 0; i < scores.length; i += xi) {
    const label = 'G' + scores[i].game;
    ctx.fillText(label, xScale(i) - 20, H - 8);
  }

  ctx.fillStyle = 'rgba(78,205,196,0.08)';
  ctx.beginPath();
  scores.forEach((d, i) => {
    const x = xScale(i), y = yScale(d.score);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.lineTo(xScale(scores.length - 1), yScale(0));
  ctx.lineTo(xScale(0), yScale(0));
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = 'rgba(78,205,196,0.95)'; ctx.lineWidth = 2.2;
  ctx.beginPath();
  scores.forEach((d, i) => {
    const x = xScale(i), y = yScale(d.score);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  if (hasEvalSeries) {
    ctx.strokeStyle = 'rgba(250,204,21,0.92)';
    ctx.lineWidth = 2.2;
    ctx.setLineDash([10, 5]);
    ctx.beginPath();
    let startedEvalLine = false;
    visibleEvalScores.forEach((d, j) => {
      const evalScore = d.score;
      if (!Number.isFinite(evalScore)) return;
      const x = xScale(evalOffset + j), y = y2Scale(evalScore);
      if (!startedEvalLine) {
        ctx.moveTo(x, y);
        startedEvalLine = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.strokeStyle = 'rgba(255,107,107,0.95)'; ctx.lineWidth = 2.6;
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
  ctx.lineWidth = 3.2;
  ctx.beginPath();
  ctx.moveTo(xScale(0), yScale(trend.y0));
  ctx.lineTo(xScale(scores.length - 1), yScale(trend.yN));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = trendColor;
  ctx.font = '12px monospace';
  const trendLabel = 'trend ' + trend.dir + ' ' +
    (trend.slope >= 0 ? '+' : '') + slopeRounded + '/game fit=' + fitPct + '%';
  const trendMidY = yScale((trend.y0 + trend.yN) / 2);
  const trendLabelY = Math.max(padT + 12, Math.min(H - padB - 8, trendMidY - 8));
  const trendLabelX = padL + 4;
  const trendLabelW = ctx.measureText(trendLabel).width + 8;
  ctx.fillStyle = 'rgba(0,0,0,0.65)';
  ctx.fillRect(trendLabelX - 4, trendLabelY - 12, trendLabelW, 18);
  ctx.fillStyle = trendColor;
  ctx.fillText(trendLabel, trendLabelX, trendLabelY);
  ctx.fillStyle = '#ffd700';
  ctx.font = 'bold 12px monospace';
  ctx.fillText('best ' + chartMaxScore, padL + 4, padT + 12);
  if (hasEvalSeries) {
    ctx.fillStyle = '#facc15';
    ctx.fillText('eval best ' + evalMaxScore, Math.max(padL + 110, W - padR - 130), padT + 12);
  }
}

function drawRussiaRateChart(series) {
  const currentEl = document.getElementById('rateChartCurrent');
  if (!rateCanvas || !rateCtx) return;
  const points = (series && series.points) || [];
  const current = series ? series.current : null;
  if (currentEl) {
    if (current) {
      const r = current.rate;
      currentEl.textContent = r.toFixed(2) + '% (' + current.count + '/' + (series.window || 100) + ') @G' + current.game;
      currentEl.classList.remove('zero', 'high');
      if (r === 0) currentEl.classList.add('zero');
      else if (r >= 5) currentEl.classList.add('high');
    } else {
      currentEl.textContent = 'no data';
      currentEl.classList.remove('high');
      currentEl.classList.add('zero');
    }
  }
  if (!points.length) return;

  const dpr = window.devicePixelRatio || 1;
  const rect = rateCanvas.parentElement.getBoundingClientRect();
  const W = Math.max(160, Math.floor(rect.width - 36));
  const H = Math.max(80, Math.floor(rect.height - 8));
  rateCanvas.width = W * dpr; rateCanvas.height = H * dpr;
  rateCanvas.style.width = W + 'px'; rateCanvas.style.height = H + 'px';
  rateCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const padL = 44, padR = 12, padT = 6, padB = 22;
  const cW = W - padL - padR, cH = H - padT - padB;
  const rates = points.map(p => p.rate);
  const maxRate = Math.max.apply(null, rates);
  const yMax = Math.max(1, Math.ceil((maxRate + 0.5) * 2) / 2);
  const gameMin = points[0].game;
  const gameMax = points[points.length - 1].game;
  const xRange = Math.max(1, gameMax - gameMin);
  const xScale = g => padL + ((g - gameMin) / xRange) * cW;
  const yScale = v => padT + cH - (v / yMax) * cH;

  rateCtx.clearRect(0, 0, W, H);

  rateCtx.strokeStyle = '#2a2b55'; rateCtx.lineWidth = 1;
  rateCtx.font = '11px monospace'; rateCtx.fillStyle = '#8b93a7';
  for (let i = 0; i <= 3; i++) {
    const v = (yMax / 3) * i, y = yScale(v);
    rateCtx.beginPath();
    rateCtx.moveTo(padL, y);
    rateCtx.lineTo(W - padR, y);
    rateCtx.stroke();
    rateCtx.fillText(v.toFixed(1) + '%', 2, y + 4);
  }

  rateCtx.fillStyle = '#8b93a7';
  const xi = Math.max(1, Math.ceil(points.length / 6));
  for (let i = 0; i < points.length; i += xi) {
    rateCtx.fillText('G' + points[i].game, xScale(points[i].game) - 18, H - 6);
  }
  if ((points.length - 1) % xi !== 0) {
    const last = points[points.length - 1];
    rateCtx.fillText('G' + last.game, xScale(last.game) - 18, H - 6);
  }

  rateCtx.fillStyle = 'rgba(250,204,21,0.12)';
  rateCtx.beginPath();
  points.forEach((p, i) => {
    const x = xScale(p.game), y = yScale(p.rate);
    i === 0 ? rateCtx.moveTo(x, y) : rateCtx.lineTo(x, y);
  });
  rateCtx.lineTo(xScale(gameMax), yScale(0));
  rateCtx.lineTo(xScale(gameMin), yScale(0));
  rateCtx.closePath();
  rateCtx.fill();

  rateCtx.strokeStyle = 'rgba(250,204,21,0.95)';
  rateCtx.lineWidth = 2.2;
  rateCtx.beginPath();
  points.forEach((p, i) => {
    const x = xScale(p.game), y = yScale(p.rate);
    i === 0 ? rateCtx.moveTo(x, y) : rateCtx.lineTo(x, y);
  });
  rateCtx.stroke();

  if (current) {
    const cx = xScale(current.game), cy = yScale(current.rate);
    rateCtx.fillStyle = '#facc15';
    rateCtx.beginPath();
    rateCtx.arc(cx, cy, 4, 0, Math.PI * 2);
    rateCtx.fill();
    rateCtx.strokeStyle = 'rgba(0,0,0,0.7)';
    rateCtx.lineWidth = 3;
    rateCtx.beginPath();
    rateCtx.arc(cx, cy, 4, 0, Math.PI * 2);
    rateCtx.stroke();
  }

  rateCtx.fillStyle = '#facc15';
  rateCtx.font = 'bold 11px monospace';
  rateCtx.fillText('window ' + (series.window || 100) + ' / step ' + (series.step || 0), padL + 4, padT + 12);
}

drawChart(SCORES, EVAL_SCORES);
drawRussiaRateChart(RUSSIA_RATE_SERIES);
window.addEventListener('resize', () => {
  drawChart(SCORES, EVAL_SCORES);
  drawRussiaRateChart(RUSSIA_RATE_SERIES);
});

// Auto-reload every 3 seconds (OBS CEF doesn't support meta refresh on file://)
setInterval(function(){location.reload();},3000);
</script>
</body>
</html>
HTMLEOF

# サイズ妥当性チェック: テンプレート (178 byte) より大きいことを確認してから rename
if [ -s "$__DASH_TMP" ] && [ "$(wc -c <"$__DASH_TMP")" -gt 500 ]; then
	# OBS / Web から file:// で読まれるためパーミッションを明示的に 644 に
	chmod 644 "$__DASH_TMP" 2>/dev/null || true
	mv "$__DASH_TMP" score_dashboard.html
	trap - EXIT
	echo "Generated score_dashboard.html ($(python3 -c 'import json,sys; print(json.load(sys.stdin)["scoreStats"]["count"])' <<<"${DASHBOARD_DATA_JSON}") games, chart=${DASHBOARD_CHART_GAMES})"
else
	echo "[generate_dashboard] WARN: temp file too small ($(wc -c <"$__DASH_TMP" 2>/dev/null) bytes); keeping existing HTML" >&2
	rm -f "$__DASH_TMP"
	trap - EXIT
	exit 0
fi
