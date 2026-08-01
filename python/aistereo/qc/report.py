"""QC report aggregation and a dependency-free human-readable HTML export."""

from __future__ import annotations

import html
from pathlib import Path

from ..artifacts import write_text_atomic
from ..models import QCReport
from .metrics import QCAccumulator


def build_qc_report(
    accumulator: QCAccumulator,
    *,
    expected_frame_count: int,
    rendered_frame_count: int,
    audio_duration: float | None = None,
    video_duration: float | None = None,
    guard_actions: dict[int, list[str]] | None = None,
    fallback_shots: set[int] | None = None,
    depth_model_failures: set[int] | None = None,
    duplicated_frames: int = 0,
    depth_backend: str = "unknown",
    synthetic_depth: bool | None = None,
    additional_warnings: list[str] | None = None,
) -> QCReport:
    shots = accumulator.shot_summaries(guard_actions=guard_actions, fallback_shots=fallback_shots)
    dropped = max(0, expected_frame_count - rendered_frame_count)
    duration_difference = (
        abs(audio_duration - video_duration)
        if audio_duration is not None and video_duration is not None
        else None
    )
    warnings: list[str] = []
    if dropped:
        warnings.append(f"{dropped} expected frames were not rendered")
    if duplicated_frames:
        warnings.append(f"{duplicated_frames} duplicated frames were detected")
    if duration_difference is not None and duration_difference > 0.050:
        warnings.append("Audio/video duration differs by more than 50 ms")
    total_edge = sum(item.edge_violations for item in shots)
    if total_edge:
        warnings.append("Potential stereo window-edge violations were detected")
    if any(item.largest_hole_pixels > 256 for item in shots):
        warnings.append("Large disocclusion holes remain in one or more shots")
    uses_synthetic = depth_backend == "synthetic" if synthetic_depth is None else synthetic_depth
    if uses_synthetic:
        warnings.append("Render used synthetic demo depth and is not a production deliverable")
    warnings.extend(additional_warnings or [])
    return QCReport(
        frame_count=rendered_frame_count,
        expected_frame_count=expected_frame_count,
        dropped_frames=dropped,
        duplicated_frames=max(0, duplicated_frames),
        audio_video_duration_difference=duration_difference,
        max_popout_disparity_norm=max(
            (item.max_popout_disparity_norm for item in shots), default=0.0
        ),
        max_background_disparity_norm=max(
            (item.max_background_disparity_norm for item in shots), default=0.0
        ),
        disparity_histogram=accumulator.histogram,
        disparity_histogram_edges=[float(value) for value in accumulator.histogram_edges],
        edge_violations=total_edge,
        hole_pixels=sum(item.hole_pixels for item in shots),
        largest_hole_pixels=max((item.largest_hole_pixels for item in shots), default=0),
        depth_temporal_change=(
            sum(item.depth_temporal_change * item.frame_count for item in shots)
            / max(1, sum(item.frame_count for item in shots))
        ),
        shots_with_comfort_overrides=[item.shot_id for item in shots if item.comfort_overrides],
        shots_using_fallback=sorted(fallback_shots or set()),
        depth_model_failures=sorted(depth_model_failures or set()),
        shots=shots,
        warnings=warnings,
        depth_backend=depth_backend,
        synthetic_depth=uses_synthetic,
    )


def write_qc_html(report: QCReport, path: str | Path) -> Path:
    rows = "".join(
        "<tr>"
        f"<td>{shot.shot_id}</td><td>{shot.frame_count}</td>"
        f"<td>{shot.max_popout_disparity_norm:.4f}</td>"
        f"<td>{shot.max_background_disparity_norm:.4f}</td>"
        f"<td>{shot.edge_violations}</td><td>{shot.hole_pixels}</td>"
        "</tr>"
        for shot in report.shots
    )
    warnings = "".join(f"<li>{html.escape(item)}</li>" for item in report.warnings)
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AI Stereo Director QC</title>
<style>body{{font:16px system-ui;max-width:960px;margin:2rem auto;padding:0 1rem;color:#17202a}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd1d1;padding:.45rem;text-align:right}}
th:first-child,td:first-child{{text-align:left}}.warning{{color:#922b21}}</style></head>
<body><h1>Quality-control report</h1>
<p>Rendered {report.frame_count} of {report.expected_frame_count} expected frames.</p>
<ul class="warning">{warnings}</ul>
<table><thead><tr><th>Shot</th><th>Frames</th><th>Max pop-out</th><th>Max background</th><th>Edge pixels</th><th>Hole pixels</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""
    return write_text_atomic(path, document)
