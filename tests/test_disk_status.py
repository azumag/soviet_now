import shutil
import unittest
from unittest import mock

from lib.disk_status import DiskStatus, compact_disk_label, read_disk_status


class DiskStatusTests(unittest.TestCase):
    def read(self, used_pct, warn=85.0, critical=95.0):
        total = 100 * 1024 ** 3
        used = int(total * used_pct / 100)
        free = total - used
        usage = shutil._ntuple_diskusage(total, used, free)
        with mock.patch("lib.disk_status.shutil.disk_usage", return_value=usage):
            return read_disk_status("/", warn_pct=warn, critical_pct=critical)

    def test_levels_follow_overlay_thresholds(self):
        self.assertEqual(self.read(84.9).level, "ok")
        self.assertEqual(self.read(85.0).level, "warning")
        self.assertEqual(self.read(94.9).level, "warning")
        self.assertEqual(self.read(95.0).level, "critical")
        self.assertEqual(self.read(98.0).level, "critical")

    def test_compact_label_contains_used_total_percent_and_free(self):
        status = DiskStatus(
            total_bytes=50 * 1024 ** 3,
            used_bytes=42 * 1024 ** 3,
            free_bytes=8 * 1024 ** 3,
            used_pct=84.0,
            level="ok",
        )
        self.assertEqual(compact_disk_label(status), "DISK 42.0/50.0 GiB (84%) · FREE 8.0 GiB")

    def test_invalid_thresholds_fail_closed(self):
        with self.assertRaises(ValueError):
            read_disk_status("/", warn_pct=95, critical_pct=85)


if __name__ == "__main__":
    unittest.main()
