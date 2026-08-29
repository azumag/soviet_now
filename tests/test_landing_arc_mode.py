"""analyze_board の着地モデル切替 (ANALYZE_BOARD_LANDING_ARC)。

実測 (実戦 852 手): 従来の箱積みモデルは駒の上への着地 y を平均 +0.69 高く予測し (95% が過大)、
締切超過の予測は 52 件中 25 件が誤検出だった (実際の超過 27 件、見落とし 0)。
mode 3 (deadline のみ円弧接触) は誤検出を 20 件へ減らし、見落とし 0 を維持し、併合判定は完全に不変。
既定 (mode 0) では従来と同一であることをここで固定する。
"""
import importlib
import os
import unittest


def _reload(mode):
    if mode is None:
        os.environ.pop("ANALYZE_BOARD_LANDING_ARC", None)
    else:
        os.environ["ANALYZE_BOARD_LANDING_ARC"] = mode
    import analyze_board as ab
    importlib.reload(ab)
    return ab


PIECES = [
    {"id": 1, "type": 5, "x": -0.6, "y": -3.0, "r": 0.5},
    {"id": 2, "type": 5, "x": 0.7, "y": -3.0, "r": 0.5},
]


class LandingArcModeTests(unittest.TestCase):
    def tearDown(self):
        _reload(None)

    def test_default_is_box_model(self):
        ab = _reload(None)
        self.assertEqual(ab.landing_arc_mode(), 0)

    def test_modes_parse(self):
        for raw, want in (("0", 0), ("1", 1), ("2", 2), ("3", 3), ("deadline", 3), ("contact", 2), ("off", 0)):
            ab = _reload(raw)
            self.assertEqual(ab.landing_arc_mode(), want, raw)

    def test_offset_drop_lands_lower_under_arc(self):
        """横にずれた落下は、円弧モデルでは相手の真上より低く止まる。"""
        box = _reload("0")
        radii = box.build_deadline_radii({})
        y_box, _ = box.get_landing_info(0.05, 0.5, PIECES, radii, 5)
        arc = _reload("1")
        y_arc, _ = arc.get_landing_info(0.05, 0.5, PIECES, radii, 5)
        self.assertLess(y_arc, y_box)

    def test_deadline_mode_lowers_deadline_landing_only(self):
        """mode 3 は deadline 着地だけ下げ、通常の着地 (併合判定に使う) は変えない。"""
        box = _reload("0")
        radii = box.build_deadline_radii({})
        y_box, hit_box = box.get_landing_info(0.05, 0.5, PIECES, radii, 5)
        dl_box = box.get_deadline_landing_y(0.05, 0.5, PIECES, radii, 5)
        dl = _reload("3")
        y_dl, hit_dl = dl.get_landing_info(0.05, 0.5, PIECES, radii, 5)
        dl_dl = dl.get_deadline_landing_y(0.05, 0.5, PIECES, radii, 5)
        self.assertAlmostEqual(y_dl, y_box, places=6)
        self.assertEqual(hit_dl, hit_box)
        self.assertLess(dl_dl, dl_box)

    def test_centered_drop_unchanged_under_arc(self):
        """真上 (dx=0) からの落下は円弧でも高さが変わらない。"""
        box = _reload("0")
        radii = box.build_deadline_radii({})
        centered = [{"id": 1, "type": 5, "x": 0.0, "y": -3.0, "r": 0.5}]
        dl_box = box.get_deadline_landing_y(0.0, 0.5, centered, radii, 5)
        arc = _reload("3")
        dl_arc = arc.get_deadline_landing_y(0.0, 0.5, centered, radii, 5)
        self.assertAlmostEqual(dl_arc, dl_box, places=6)


if __name__ == "__main__":
    unittest.main()
