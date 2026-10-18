import unittest

from lib.overlay_dashboard_cards import dashboard_css, render_game_dashboard, render_ops_dashboard


class OverlayDashboardCardsTest(unittest.TestCase):
    def test_soren91_game_dashboard_uses_large_metrics_and_recent_bars(self):
        raw = """SOREN/CORNER: SOREN91 / soren91 / ACTIVE
Live: this corner results 42
Stats: 120 results / best=1 / Recent30=5.2
  Trend: -2.1 vs previous 30 / better
  wins=7 / lower rank is better
Rank Timeline
Last8: 12 9 4 7 1 3 2 5"""
        html = render_game_dashboard(raw)
        self.assertIn("GAME PERFORMANCE", html)
        self.assertIn("BEST", html)
        self.assertIn(">1<", html)
        self.assertIn("RECENT 30", html)
        self.assertIn(">5.2<", html)
        self.assertIn("LOWER IS BETTER", html)
        self.assertEqual(html.count('class="bar-wrap"'), 8)
        self.assertIn("TREND", html)

    def test_jev_game_dashboard_keeps_report_metrics(self):
        raw = """SOREN/CORNER: JEV / sorengame / ACTIVE
Live: player policy=jev generation=4
Stats: 52 reports / best=8080 / Recent30=6120
  Trend: +430.0 vs previous 22 / better
  Reported scores; may include interrupted runs
Score Timeline
Last8: 4100 4800 5300 5100 6200 6800 7200 8080"""
        html = render_game_dashboard(raw)
        self.assertIn("sorengame", html)
        self.assertIn("8080", html)
        self.assertIn("6120", html)
        self.assertIn("REPORTS", html)
        self.assertEqual(html.count('class="bar-wrap"'), 8)

    def test_ops_dashboard_summarizes_healthy_services(self):
        raw = """━━━ SOREN OPS ━━━
  HEALTH
    ● Loop        RUNNING  PID=101
    ● Workers     7/7 ONLINE  [████████████]
    ● Backend     FFMPEG LIVE  relay=ok
  ACTIVITY
    ◆ Game        3試合目 (games) R1 [120,220,330]
    ▸ QueueMeter  [██░░░░░░░░]  A=3 C=1 T=0
    ▾ LastDrop    observed
  AUDIO
    ♪ Say         PLAYING  PID=202
  TWITCH
    ● Chat        CONNECTED  PID=303
  YOUTUBE
    ● Chat        CONNECTED  PID=404"""
        html = render_ops_dashboard(raw)
        self.assertIn("SYSTEM HEALTH", html)
        self.assertIn("7 / 7", html)
        self.assertNotIn("FFMPEG", html)
        self.assertIn("STREAM", html)
        self.assertIn("LIVE", html)
        self.assertIn("LOOP", html)
        self.assertIn("TWITCH", html)
        self.assertIn("YOUTUBE", html)
        self.assertIn("ALL SYSTEMS NOMINAL", html)
        self.assertIn(">4<", html)  # Queue total A+C+T.

    def test_ops_dashboard_surfaces_faults_in_attention(self):
        raw = """  HEALTH
    ● Workers     6/7 DEGRADED
    ! Duplicates  DETECTED  chat_worker=10,11
    ○ Loop        STOPPED
    ● Backend     FFMPEG LIVE"""
        html = render_ops_dashboard(raw)
        self.assertIn("DEGRADED", html)
        self.assertIn("ATTENTION", html)
        self.assertIn("Duplicates", html)
        self.assertIn("STOPPED", html)

    def test_dashboard_css_uses_flat_chrome_without_decorative_edge_accents(self):
        css = dashboard_css()
        self.assertIn(".broadcast-card { box-sizing:border-box; min-height:0; overflow:hidden; border:0;", css)
        self.assertIn(".metric { min-width:0; padding:12px 13px; border:0;", css)
        self.assertIn(".service { min-width:0; padding:9px 11px; border:0;", css)
        self.assertIn(".activity-box { padding:8px 10px; border:0;", css)
        self.assertNotIn("inset 3px 0", css)

    def test_unknown_feed_falls_back_to_legacy_path(self):
        self.assertEqual(render_game_dashboard("Score Timeline\nfoo"), "")
        self.assertEqual(render_ops_dashboard("something unrelated"), "")


if __name__ == "__main__":
    unittest.main()
