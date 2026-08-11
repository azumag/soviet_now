#!/usr/bin/env python3
"""Pure acceptance calculations for the OBS/direct-stream benchmark."""

from __future__ import annotations

from typing import Any


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _reduction(before: Any, after: Any) -> float | None:
    if not _number(before) or before <= 0 or not _number(after):
        return None
    return (before - after) / before * 100


def build_comparison(
    obs_profile: dict[str, Any],
    direct_profile: dict[str, Any],
    direct_profile_name: str,
) -> dict[str, Any]:
    """Return the short benchmark metrics and acceptance decisions.

    Issue #96 accepts a 20% reduction in streaming-related CPU time.  The
    directly comparable processes are OBS in the baseline and FFmpeg in the
    direct profile; system-wide busy reduction is useful supporting evidence
    but is not required when the process reduction already passes.
    """

    system_reduction = _reduction(
        obs_profile.get("system_busy_pct"), direct_profile.get("system_busy_pct")
    )
    process_reduction = _reduction(
        obs_profile.get("obs_cpu_pct"), direct_profile.get("encoder_cpu_pct")
    )
    direct_game_fps = direct_profile.get("game_fps_mean")
    direct_drop = direct_profile.get("drop_frames")
    direct_dup = direct_profile.get("dup_frames")
    direct_frames = direct_profile.get("encoded_frames")

    comparison = {
        "direct_profile": direct_profile_name,
        "system_busy_reduction_pct": (
            round(system_reduction, 3) if system_reduction is not None else None
        ),
        "stream_process_cpu_reduction_pct": (
            round(process_reduction, 3) if process_reduction is not None else None
        ),
        "cpu_acceptance_20pct": (
            (system_reduction is not None and system_reduction >= 20)
            or (process_reduction is not None and process_reduction >= 20)
        ),
        "output_720p30_acceptance": (
            _number(direct_profile.get("ffmpeg_fps"))
            and direct_profile["ffmpeg_fps"] >= 29.5
            and _number(direct_profile.get("ffmpeg_speed"))
            and direct_profile["ffmpeg_speed"] >= 0.97
            and direct_profile.get("exit_code") == 0
        ),
        "content_30fps_acceptance": (
            _number(direct_game_fps) and direct_game_fps >= 29.0
        ),
        "drop_dup_1pct_acceptance": (
            isinstance(direct_frames, int)
            and not isinstance(direct_frames, bool)
            and direct_frames > 0
            and isinstance(direct_drop, int)
            and not isinstance(direct_drop, bool)
            and isinstance(direct_dup, int)
            and not isinstance(direct_dup, bool)
            and direct_drop <= max(3, direct_frames // 100)
            and direct_dup <= max(3, direct_frames // 100)
        ),
    }
    comparison["direct_720p30_acceptance"] = all(
        (
            comparison["output_720p30_acceptance"],
            comparison["content_30fps_acceptance"],
            comparison["drop_dup_1pct_acceptance"],
        )
    )
    comparison["short_benchmark_acceptance"] = all(
        (
            comparison["direct_720p30_acceptance"],
            comparison["cpu_acceptance_20pct"],
        )
    )
    return comparison
