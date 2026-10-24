"""Small filesystem-usage helper for broadcast status overlays."""

from __future__ import annotations

import shutil
from dataclasses import dataclass


GIB = 1024 ** 3


@dataclass(frozen=True)
class DiskStatus:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_pct: float
    level: str

    @property
    def total_gib(self) -> float:
        return self.total_bytes / GIB

    @property
    def used_gib(self) -> float:
        return self.used_bytes / GIB

    @property
    def free_gib(self) -> float:
        return self.free_bytes / GIB


def read_disk_status(path: str = "/", *, warn_pct: float = 85.0, critical_pct: float = 95.0) -> DiskStatus:
    """Read usage for *path* and classify it for a compact status display."""
    if not 0 <= warn_pct < critical_pct <= 100:
        raise ValueError("disk thresholds must satisfy 0 <= warn < critical <= 100")

    usage = shutil.disk_usage(path)
    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    used_pct = (used * 100.0 / total) if total else 0.0

    if used_pct >= critical_pct:
        level = "critical"
    elif used_pct >= warn_pct:
        level = "warning"
    else:
        level = "ok"

    return DiskStatus(
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
        used_pct=used_pct,
        level=level,
    )


def compact_disk_label(status: DiskStatus) -> str:
    """Return a short label that still exposes used/total, percent and free space."""
    return (
        f"DISK {status.used_gib:.1f}/{status.total_gib:.1f} GiB "
        f"({status.used_pct:.0f}%) · FREE {status.free_gib:.1f} GiB"
    )
