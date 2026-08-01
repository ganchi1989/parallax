from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest

from aistereo.config import DepthConfig
from aistereo.depth import (
    SyntheticDepthBackend,
    load_depth_shot,
    normalize_depth_shot,
    save_depth_shot,
)
from aistereo.errors import ArtifactError
from aistereo.features.depth_stats import depth_statistics
from aistereo.features.extractor import extract_shot_features
from aistereo.features.motion import motion_score
from aistereo.models import Shot


def test_shot_depth_normalization_uses_one_global_scale() -> None:
    raw = np.stack(
        [np.linspace(0, 1, 16).reshape(4, 4), np.linspace(1, 2, 16).reshape(4, 4)]
    ).astype(np.float32)
    config = DepthConfig(
        temporal_alpha=0, spatial_smoothing=0, lower_percentile=0, upper_percentile=100
    )
    normalized, metadata = normalize_depth_shot(raw, config)
    assert normalized[0].max() == pytest.approx(0.5)
    assert normalized[1].min() == pytest.approx(0.5)
    assert metadata["reliability"] == 1


def test_depth_normalization_repairs_invalid_values() -> None:
    raw = np.array([[[0.0, np.nan], [np.inf, 1.0]]], dtype=np.float32)
    normalized, metadata = normalize_depth_shot(raw, DepthConfig(spatial_smoothing=0))
    assert np.all(np.isfinite(normalized))
    assert metadata["invalid_fraction"] == 0.5
    assert metadata["reliability"] == 0


def test_depth_npz_round_trip_does_not_require_pickle(tmp_path: Path) -> None:
    depth = np.linspace(0, 1, 24, dtype=np.float32).reshape(2, 3, 4)
    path = save_depth_shot(tmp_path / "shot.npz", depth, raw=depth * 2)
    loaded = load_depth_shot(path)
    assert loaded.shape == depth.shape
    assert np.allclose(loaded, depth, atol=1e-3)


def test_depth_loader_maps_late_zip_integrity_failures(tmp_path: Path, monkeypatch) -> None:
    path = save_depth_shot(tmp_path / "shot.npz", np.zeros((1, 2, 3), dtype=np.float32))

    def corrupt_read(*args, **kwargs):
        del args, kwargs
        raise zipfile.BadZipFile("CRC mismatch")

    monkeypatch.setattr(np.lib.format, "read_array", corrupt_read)
    with pytest.raises(ArtifactError, match="Could not load depth artifact"):
        load_depth_shot(path)


def test_prefer_raw_fallback_still_validates_normalized_range(tmp_path: Path) -> None:
    path = tmp_path / "invalid-normalized.npz"
    np.savez_compressed(path, normalized=np.full((1, 2, 3), 7.0, dtype=np.float32))

    with pytest.raises(ArtifactError, match=r"outside the \[0, 1\] range"):
        load_depth_shot(path, prefer_raw=True)


def _eocd_offset(path: Path) -> int:
    offset = path.read_bytes().rfind(b"PK\x05\x06")
    assert offset >= 0
    return offset


@pytest.mark.parametrize(
    "field_offset,value,message",
    [
        (4, 1, "Multi-disk"),
        (10, 0xFFFF, "Zip64"),
    ],
)
def test_depth_loader_rejects_multidisk_and_zip64_end_records(
    tmp_path: Path,
    field_offset: int,
    value: int,
    message: str,
) -> None:
    path = save_depth_shot(tmp_path / "shot.npz", np.zeros((1, 2, 3), dtype=np.float32))
    payload = bytearray(path.read_bytes())
    struct.pack_into("<H", payload, _eocd_offset(path) + field_offset, value)
    path.write_bytes(payload)

    with pytest.raises(ArtifactError, match=message):
        load_depth_shot(path)


def test_depth_loader_rejects_ambiguous_end_records(tmp_path: Path) -> None:
    path = save_depth_shot(tmp_path / "shot.npz", np.zeros((1, 2, 3), dtype=np.float32))
    payload = bytearray(path.read_bytes())
    offset = _eocd_offset(path)
    original_eocd = bytes(payload[offset : offset + 22])
    struct.pack_into("<H", payload, offset + 20, len(original_eocd))
    payload.extend(original_eocd)
    path.write_bytes(payload)

    with pytest.raises(ArtifactError, match="ambiguous or invalid"):
        load_depth_shot(path)


def test_depth_loader_rejects_invalid_central_directory_bounds(tmp_path: Path) -> None:
    path = save_depth_shot(tmp_path / "shot.npz", np.zeros((1, 2, 3), dtype=np.float32))
    payload = bytearray(path.read_bytes())
    offset = _eocd_offset(path)
    central_offset = struct.unpack_from("<L", payload, offset + 16)[0]
    struct.pack_into("<L", payload, offset + 16, central_offset + 1)
    path.write_bytes(payload)

    with pytest.raises(ArtifactError, match="central-directory bounds"):
        load_depth_shot(path)


def test_depth_loader_rejects_excessive_member_count(tmp_path: Path) -> None:
    path = tmp_path / "too-many-members.npz"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in ("normalized.npy", "metadata_json.npy", "raw.npy", "extra.npy"):
            archive.writestr(name, b"not-read")

    with pytest.raises(ArtifactError, match="too many ZIP members"):
        load_depth_shot(path)


def test_depth_loader_rejects_excessive_central_directory(tmp_path: Path) -> None:
    array = io.BytesIO()
    np.save(array, np.zeros((1, 2, 3), dtype=np.float32), allow_pickle=False)
    path = tmp_path / "oversized-directory.npz"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("normalized.npy", array.getvalue())
        archive.writestr("x" * 17_000, b"unused")

    with pytest.raises(ArtifactError, match="central directory exceeds"):
        load_depth_shot(path)


def test_synthetic_backend_is_deterministic() -> None:
    backend = SyntheticDepthBackend()
    frames = [np.zeros((2, 2, 3), dtype=np.uint8)] * 3
    config = DepthConfig(width=32, height=32)
    first = backend.estimate(frames, shot_id=2, config=config)
    second = backend.estimate(frames, shot_id=2, config=config)
    assert np.array_equal(first, second)


def test_motion_and_feature_extraction_are_bounded() -> None:
    still = np.zeros((24, 32, 3), dtype=np.uint8)
    changed = np.full_like(still, 255)
    assert motion_score([still, still]) == 0
    assert motion_score([still, changed]) == 1
    shot = Shot(shot_id=1, start_frame=0, end_frame=1, start_time=0, end_time=1)
    depth = np.stack([np.tile(np.linspace(0, 1, 32), (24, 1))] * 2)
    result = extract_shot_features(
        shot, [still, changed], depth, speech_intervals=[(0.25, 0.75)], depth_reliability=0.8
    )
    assert result.speech_ratio == 0.5
    assert 0 <= result.motion_score <= 1
    assert result.depth_reliability == 0.8


def test_depth_statistics_do_not_cross_shot_boundaries() -> None:
    depth = np.stack([np.zeros((2, 2)), np.ones((2, 2))])
    spread, foreground, temporal = depth_statistics(depth)
    assert spread == 1
    assert foreground == 0.5
    assert temporal == 1
