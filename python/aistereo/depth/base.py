"""Depth backend interface, registry, and NPZ artifact IO."""

from __future__ import annotations

import json
import math
import os
import struct
import tempfile
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from ..artifacts import prepare_output_path, replace_atomic
from ..config import DepthConfig
from ..errors import ArtifactError, ValidationError
from .limits import MAX_DEPTH_ARCHIVE_BYTES, MAX_DEPTH_ARRAY_BYTES

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]
MAX_DEPTH_ARCHIVE_MEMBERS = 3
MAX_DEPTH_CENTRAL_DIRECTORY_BYTES = 16 * 1024
MAX_DEPTH_ZIP_COMMENT_BYTES = 1024
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_EOCD_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_ZIP_EOCD = struct.Struct("<4s4H2LH")
_ZIP_EOCD_MAX_SEARCH_BYTES = _ZIP_EOCD.size + 65_535


class DepthBackend(ABC):
    """A backend returns unnormalised relative depth, high values being nearer."""

    name: str

    @abstractmethod
    def estimate(
        self,
        frames: Sequence[np.ndarray],
        *,
        shot_id: int,
        config: DepthConfig,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
    ) -> np.ndarray:
        """Return ``[frames, height, width]`` finite or partially-finite depth."""


def save_depth_shot(
    path: str | Path,
    normalized: np.ndarray,
    *,
    raw: np.ndarray | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    destination = prepare_output_path(path)
    if normalized.ndim != 3:
        raise ValidationError("Depth array must have shape [frames, height, width]")
    if normalized.size * np.dtype(np.float32).itemsize > MAX_DEPTH_ARRAY_BYTES:
        raise ValidationError(
            "Depth array exceeds the bounded artifact limit",
            details={"max_bytes": MAX_DEPTH_ARRAY_BYTES},
        )
    payload: dict[str, Any] = {
        "normalized": np.asarray(normalized, dtype=np.float16),
        "metadata_json": np.asarray(json.dumps(metadata or {}, sort_keys=True)),
    }
    if raw is not None:
        payload["raw"] = np.asarray(raw, dtype=np.float16)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            np.savez_compressed(handle, **payload)
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomic(temporary, destination)
    except (OSError, ValueError) as exc:
        with suppress(OSError):
            os.close(descriptor)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise ArtifactError("Could not save depth artifact", details={"reason": str(exc)}) from exc
    return destination


def _validate_depth_zip_eocd(handle: BinaryIO, size: int) -> int:
    """Validate a small, single-disk, non-Zip64 NPZ before ZipFile allocates."""

    if size < _ZIP_EOCD.size:
        raise ArtifactError("Depth artifact has no valid ZIP end record")
    tail_size = min(size, _ZIP_EOCD_MAX_SEARCH_BYTES)
    handle.seek(size - tail_size)
    tail = handle.read(tail_size)
    candidates: list[tuple[int, tuple[bytes, int, int, int, int, int, int, int]]] = []
    search_from = 0
    while True:
        relative_offset = tail.find(_ZIP_EOCD_SIGNATURE, search_from)
        if relative_offset < 0:
            break
        if relative_offset + _ZIP_EOCD.size <= len(tail):
            fields = _ZIP_EOCD.unpack_from(tail, relative_offset)
            absolute_offset = size - tail_size + relative_offset
            comment_length = fields[-1]
            if absolute_offset + _ZIP_EOCD.size + comment_length == size:
                candidates.append((absolute_offset, fields))
        search_from = relative_offset + 1
    if len(candidates) != 1:
        raise ArtifactError(
            "Depth artifact has an ambiguous or invalid ZIP end record",
            details={"candidate_count": len(candidates)},
        )

    eocd_offset, fields = candidates[0]
    (
        _signature,
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        central_directory_size,
        central_directory_offset,
        comment_length,
    ) = fields
    if (
        disk_number == 0xFFFF
        or central_directory_disk == 0xFFFF
        or entries_on_disk == 0xFFFF
        or total_entries == 0xFFFF
        or central_directory_size == 0xFFFFFFFF
        or central_directory_offset == 0xFFFFFFFF
    ):
        raise ArtifactError("Zip64 depth artifacts are not supported")
    if eocd_offset >= 20:
        handle.seek(eocd_offset - 20)
        if handle.read(4) == _ZIP64_EOCD_LOCATOR_SIGNATURE:
            raise ArtifactError("Zip64 depth artifacts are not supported")
    if disk_number != 0 or central_directory_disk != 0 or entries_on_disk != total_entries:
        raise ArtifactError("Multi-disk depth artifacts are not supported")
    if not 1 <= total_entries <= MAX_DEPTH_ARCHIVE_MEMBERS:
        raise ArtifactError(
            "Depth artifact has too many ZIP members",
            details={"members": total_entries, "max_members": MAX_DEPTH_ARCHIVE_MEMBERS},
        )
    if comment_length > MAX_DEPTH_ZIP_COMMENT_BYTES:
        raise ArtifactError(
            "Depth artifact ZIP comment exceeds the bounded limit",
            details={"bytes": comment_length, "max_bytes": MAX_DEPTH_ZIP_COMMENT_BYTES},
        )
    if not 1 <= central_directory_size <= MAX_DEPTH_CENTRAL_DIRECTORY_BYTES:
        raise ArtifactError(
            "Depth artifact central directory exceeds the bounded limit",
            details={
                "bytes": central_directory_size,
                "max_bytes": MAX_DEPTH_CENTRAL_DIRECTORY_BYTES,
            },
        )
    central_directory_end = central_directory_offset + central_directory_size
    if (
        central_directory_offset < 0
        or central_directory_offset >= eocd_offset
        or central_directory_end != eocd_offset
        or central_directory_end > size
    ):
        raise ArtifactError("Depth artifact has invalid central-directory bounds")
    handle.seek(central_directory_offset)
    if handle.read(4) != _ZIP_CENTRAL_DIRECTORY_SIGNATURE:
        raise ArtifactError("Depth artifact central directory is invalid")
    handle.seek(0)
    return total_entries


def _inspect_depth_archive(
    archive: zipfile.ZipFile,
    *,
    expected_members: int,
    prefer_raw: bool,
    expected_shape: tuple[int, int, int] | None,
    max_array_bytes: int,
) -> tuple[tuple[int, int, int], zipfile.ZipInfo, str]:
    members = archive.infolist()
    if len(members) != expected_members:
        raise ArtifactError(
            "Depth artifact ZIP member count does not match its end record",
            details={"members": len(members), "declared_members": expected_members},
        )
    if not 1 <= len(members) <= MAX_DEPTH_ARCHIVE_MEMBERS:
        raise ArtifactError(
            "Depth artifact has too many ZIP members",
            details={"members": len(members), "max_members": MAX_DEPTH_ARCHIVE_MEMBERS},
        )
    preferred_name = "raw.npy" if prefer_raw else "normalized.npy"
    matches = [item for item in members if item.filename == preferred_name]
    if prefer_raw and not matches:
        preferred_name = "normalized.npy"
        matches = [item for item in members if item.filename == preferred_name]
    if len(matches) != 1:
        raise ArtifactError("Depth artifact must contain one normalized depth array")
    info = matches[0]
    if info.file_size > max_array_bytes + 1024 * 1024:
        raise ArtifactError(
            "Depth artifact exceeds the uncompressed size limit",
            details={"max_bytes": max_array_bytes},
        )
    with archive.open(info) as handle:
        version = np.lib.format.read_magic(handle)  # type: ignore[no-untyped-call]
        if version == (1, 0):
            shape, _fortran, dtype = np.lib.format.read_array_header_1_0(  # type: ignore[no-untyped-call]
                handle
            )
        elif version == (2, 0):
            shape, _fortran, dtype = np.lib.format.read_array_header_2_0(  # type: ignore[no-untyped-call]
                handle
            )
        else:
            raise ArtifactError(
                "Depth artifact uses an unsupported array-header version",
                details={"version": list(version)},
            )
    if len(shape) != 3 or any(not isinstance(value, int) or value <= 0 for value in shape):
        raise ArtifactError("Depth artifact has an invalid shape", details={"shape": list(shape)})
    if dtype.hasobject or dtype.kind != "f":
        raise ArtifactError(
            "Depth artifact must contain a floating-point array",
            details={"dtype": str(dtype)},
        )
    normalized_shape = (int(shape[0]), int(shape[1]), int(shape[2]))
    converted_itemsize = np.dtype(np.float32).itemsize
    allocation_itemsize = dtype.itemsize + (
        0 if dtype == np.dtype(np.float32) else converted_itemsize
    )
    allocation_bytes = math.prod(normalized_shape) * allocation_itemsize
    if allocation_bytes > max_array_bytes:
        raise ArtifactError(
            "Depth artifact exceeds the bounded allocation limit",
            details={"bytes": allocation_bytes, "max_bytes": max_array_bytes},
        )
    if expected_shape is not None and normalized_shape != expected_shape:
        raise ArtifactError(
            "Depth artifact shape does not match its manifest",
            details={
                "expected_shape": list(expected_shape),
                "actual_shape": list(normalized_shape),
            },
        )
    return normalized_shape, info, preferred_name


def _validate_archive_size(size: int) -> None:
    if size > MAX_DEPTH_ARCHIVE_BYTES:
        raise ArtifactError(
            "Depth artifact exceeds the compressed size limit",
            details={"max_bytes": MAX_DEPTH_ARCHIVE_BYTES},
        )


def inspect_depth_shot(
    path: str | Path,
    *,
    prefer_raw: bool = False,
    expected_shape: tuple[int, int, int] | None = None,
    max_array_bytes: int = MAX_DEPTH_ARRAY_BYTES,
) -> tuple[int, int, int]:
    """Read the bounded NPY header from an NPZ before allocating its array."""

    source = Path(path).expanduser().resolve()
    try:
        with source.open("rb") as source_handle:
            size = os.fstat(source_handle.fileno()).st_size
            _validate_archive_size(size)
            expected_members = _validate_depth_zip_eocd(source_handle, size)
            with zipfile.ZipFile(source_handle) as archive:
                shape, _info, _selected_name = _inspect_depth_archive(
                    archive,
                    expected_members=expected_members,
                    prefer_raw=prefer_raw,
                    expected_shape=expected_shape,
                    max_array_bytes=max_array_bytes,
                )
    except ArtifactError:
        raise
    except (OSError, ValueError, KeyError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ArtifactError(
            "Could not inspect depth artifact",
            details={"path": str(source), "reason": str(exc)},
        ) from exc
    return shape


def load_depth_shot(
    path: str | Path,
    *,
    prefer_raw: bool = False,
    expected_shape: tuple[int, int, int] | None = None,
    max_array_bytes: int = MAX_DEPTH_ARRAY_BYTES,
) -> np.ndarray:
    source = Path(path).expanduser().resolve()
    try:
        with source.open("rb") as source_handle:
            size = os.fstat(source_handle.fileno()).st_size
            _validate_archive_size(size)
            expected_members = _validate_depth_zip_eocd(source_handle, size)
            with zipfile.ZipFile(source_handle) as archive:
                inspected_shape, info, selected_name = _inspect_depth_archive(
                    archive,
                    expected_members=expected_members,
                    prefer_raw=prefer_raw,
                    expected_shape=expected_shape,
                    max_array_bytes=max_array_bytes,
                )
                with archive.open(info) as array_handle:
                    loaded = np.lib.format.read_array(  # type: ignore[no-untyped-call]
                        array_handle, allow_pickle=False
                    )
                value = np.asarray(loaded, dtype=np.float32)
    except ArtifactError:
        raise
    except (OSError, ValueError, KeyError, EOFError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ArtifactError(
            "Could not load depth artifact", details={"path": str(source), "reason": str(exc)}
        ) from exc
    if value.ndim != 3 or value.shape != inspected_shape:
        raise ArtifactError(
            "Depth artifact has an invalid shape", details={"shape": list(value.shape)}
        )
    if value.nbytes > max_array_bytes:
        raise ArtifactError(
            "Depth artifact exceeds the bounded allocation limit",
            details={"bytes": value.nbytes, "max_bytes": max_array_bytes},
        )
    if not np.all(np.isfinite(value)):
        raise ArtifactError("Depth artifact contains non-finite values")
    if selected_name == "normalized.npy" and (
        float(np.min(value)) < 0.0 or float(np.max(value)) > 1.0
    ):
        raise ArtifactError("Normalized depth artifact is outside the [0, 1] range")
    return value


def create_depth_backend(name: str, **kwargs: Any) -> DepthBackend:
    normalized = name.strip().lower().replace("_", "-")
    if normalized == "synthetic":
        from .synthetic import SyntheticDepthBackend

        return SyntheticDepthBackend(**kwargs)
    if normalized in {"cached", "precomputed"}:
        from .cached import CachedDepthBackend

        return CachedDepthBackend(**kwargs)
    if normalized in {"monocular-cues", "monocular", "image-analysis"}:
        from .monocular import MonocularCuesDepthBackend

        return MonocularCuesDepthBackend(**kwargs)
    if normalized in {"video-depth-anything-small", "vda-small", "video-depth-anything"}:
        from .video_depth_anything import VideoDepthAnythingBackend

        return VideoDepthAnythingBackend(**kwargs)
    raise ValidationError(
        "Unknown depth backend",
        details={
            "backend": name,
            "available": [
                "synthetic",
                "cached",
                "monocular-cues",
                "video-depth-anything-small",
            ],
        },
    )
