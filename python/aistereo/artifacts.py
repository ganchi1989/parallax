"""Project layout and crash-safe JSON artifact helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .errors import ArtifactError

ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_JSON_ARTIFACT_BYTES = 16 * 1024 * 1024
_FULL_CONTENT_FINGERPRINT_LIMIT = 64 * 1024 * 1024
_CONTENT_FINGERPRINT_SUFFIXES = {
    ".ckpt",
    ".json",
    ".npz",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
    ".torchscript",
}
_FINGERPRINT_CHUNK_BYTES = 1024 * 1024
_WINDOWS_REPLACE_RETRY_SECONDS = (0.01, 0.02, 0.04, 0.08, 0.16)


def replace_atomic(source: str | Path, destination: str | Path) -> None:
    """Replace an artifact, tolerating brief Windows sharing violations.

    Antivirus and indexers can momentarily open a just-flushed temporary file
    without delete sharing. Retrying only the known Windows sharing/access
    errors keeps the write atomic while preserving immediate failures on other
    platforms and for persistent permission problems.
    """

    for delay in (*_WINDOWS_REPLACE_RETRY_SECONDS, None):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            # ``winerror`` is present only for Windows system errors; POSIX
            # permission failures therefore remain immediate.
            retryable = getattr(exc, "winerror", None) in {5, 32}
            if not retryable or delay is None:
                raise
            time.sleep(delay)


def prepare_output_path(
    path: str | Path,
    *,
    allowed_root: str | Path | None = None,
) -> Path:
    """Prepare a write target without following a pre-existing file symlink."""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = Path(os.path.abspath(candidate))
    try:
        root = (
            Path(allowed_root).expanduser().resolve(strict=True)
            if allowed_root is not None
            else None
        )
        if candidate.is_symlink():
            raise ArtifactError("Output path may not be a symbolic link")
        if root is not None:
            # Resolve the closest component that already exists before mkdir.
            # This catches a nested symlink escape without first creating the
            # requested missing directories through that link.
            existing_ancestor = candidate.parent
            while not existing_ancestor.exists() and not existing_ancestor.is_symlink():
                parent = existing_ancestor.parent
                if parent == existing_ancestor:
                    break
                existing_ancestor = parent
            resolved_ancestor = existing_ancestor.resolve(strict=True)
            if resolved_ancestor != root and root not in resolved_ancestor.parents:
                raise ArtifactError("Output path resolves outside its allowed root")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if candidate.is_symlink():
            raise ArtifactError("Output path may not be a symbolic link")
        parent = candidate.parent.resolve(strict=True)
        if root is not None and parent != root and root not in parent.parents:
            raise ArtifactError("Output path resolves outside its allowed root")
    except ArtifactError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ArtifactError(
            "Could not prepare output path", details={"path": str(candidate), "reason": str(exc)}
        ) from exc
    destination = parent / candidate.name
    return destination


def write_text_atomic(path: str | Path, text: str) -> Path:
    destination = prepare_output_path(path)
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            replace_atomic(temporary_name, destination)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary_name)
            raise
    except (OSError, TypeError, ValueError) as exc:
        raise ArtifactError(
            f"Could not write artifact: {destination}", details={"reason": str(exc)}
        ) from exc
    return destination


def write_json_atomic(path: str | Path, value: BaseModel | Mapping[str, Any] | list[Any]) -> Path:
    destination = prepare_output_path(path)
    if isinstance(value, BaseModel):
        data: Any = value.model_dump(mode="json")
    else:
        data = value
    try:
        encoded = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            replace_atomic(temporary_name, destination)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary_name)
            raise
    except (OSError, TypeError, ValueError) as exc:
        raise ArtifactError(
            f"Could not write artifact: {destination}", details={"reason": str(exc)}
        ) from exc
    return destination


def read_json(path: str | Path, *, max_bytes: int = MAX_JSON_ARTIFACT_BYTES) -> Any:
    source = Path(path).expanduser().resolve()
    try:
        size = source.stat().st_size
        if size > max_bytes:
            raise ArtifactError(
                "JSON artifact exceeds the bounded size limit",
                details={"path": str(source), "bytes": size, "max_bytes": max_bytes},
            )
        with source.open("r", encoding="utf-8") as handle:
            text = handle.read(max_bytes + 1)
        if len(text.encode("utf-8")) > max_bytes:
            raise ArtifactError(
                "JSON artifact exceeds the bounded size limit",
                details={"path": str(source), "max_bytes": max_bytes},
            )
        return json.loads(text)
    except ArtifactError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(
            f"Could not read artifact: {source}", details={"reason": str(exc)}
        ) from exc


def read_model(path: str | Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate(read_json(path))
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError(
            f"Artifact does not match {model.__name__}", details={"reason": str(exc)}
        ) from exc


FileIdentity = tuple[int, int, int, int, int]


def stable_file_identity(stat: os.stat_result) -> FileIdentity:
    """Return an identity stable across path and open-handle stat calls."""

    # Python 3.12 on Windows can expose incompatible st_ctime_ns semantics for
    # stat() and fstat(). Birth time identifies the same file in both results;
    # Python 3.11 falls back to its then-consistent st_ctime_ns behavior.
    identity_time_ns = (
        int(getattr(stat, "st_birthtime_ns", stat.st_ctime_ns))
        if os.name == "nt"
        else stat.st_ctime_ns
    )
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        identity_time_ns,
    )


def content_fingerprint(path: str | Path) -> str:
    """Hash one file through a pinned handle and reject concurrent replacement."""

    source = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            while chunk := handle.read(_FINGERPRINT_CHUNK_BYTES):
                digest.update(chunk)
            finished = os.fstat(handle.fileno())
        current = source.stat()
    except OSError as exc:
        raise ArtifactError(
            "Could not fingerprint artifact",
            details={"path": str(source), "reason": str(exc)},
        ) from exc
    if stable_file_identity(opened) != stable_file_identity(finished) or stable_file_identity(
        opened
    ) != stable_file_identity(current):
        raise ArtifactError(
            "Artifact changed while it was being fingerprinted",
            details={"path": str(source)},
        )
    return digest.hexdigest()


def fingerprint(parts: Iterable[str | Path | bytes | int | float | bool | None]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, Path):
            resolved = part.expanduser().resolve()
            digest.update(str(resolved).encode())
            try:
                stat = resolved.stat()
            except OSError:
                digest.update(b"missing")
            else:
                digest.update(":".join(str(value) for value in stable_file_identity(stat)).encode())
                if resolved.is_file() and (
                    stat.st_size <= _FULL_CONTENT_FINGERPRINT_LIMIT
                    or resolved.suffix.lower() in _CONTENT_FINGERPRINT_SUFFIXES
                ):
                    digest.update(b":sha256:")
                    digest.update(content_fingerprint(resolved).encode())
        elif isinstance(part, bytes):
            digest.update(part)
        else:
            digest.update(json.dumps(part, sort_keys=True, default=str).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def resolve_project_artifact(
    project_root: str | Path,
    relative_path: str | Path,
    *,
    subdirectory: str | None = None,
    require_file: bool = True,
) -> Path:
    """Resolve an existing artifact without allowing absolute paths or project escape."""

    root = Path(project_root).expanduser().resolve()
    supplied = Path(relative_path)
    if supplied.is_absolute() or ".." in supplied.parts:
        raise ArtifactError("Project artifact paths must be relative and traversal-free")
    allowed_root = (root / subdirectory).resolve() if subdirectory else root
    try:
        resolved = (root / supplied).resolve(strict=True)
    except OSError as exc:
        raise ArtifactError(
            "Project artifact does not exist", details={"path": str(supplied)}
        ) from exc
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ArtifactError("Project artifact resolves outside its allowed directory")
    if require_file and not resolved.is_file():
        raise ArtifactError("Project artifact is not a file")
    return resolved


@dataclass(frozen=True)
class ProjectLayout:
    root: Path

    @classmethod
    def at(cls, root: str | Path) -> ProjectLayout:
        return cls(Path(root).expanduser().resolve())

    @property
    def project(self) -> Path:
        return self.root / "project.json"

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def source_dir(self) -> Path:
        return self.root / "source"

    @property
    def media(self) -> Path:
        return self.source_dir / "media.json"

    @property
    def normalized_media(self) -> Path:
        return self.source_dir / "normalized_media.json"

    @property
    def normalized_video(self) -> Path:
        return self.source_dir / "normalized.mp4"

    @property
    def audio(self) -> Path:
        return self.source_dir / "audio.mka"

    @property
    def shots_dir(self) -> Path:
        return self.root / "shots"

    @property
    def shots(self) -> Path:
        return self.shots_dir / "shots.json"

    @property
    def depth_dir(self) -> Path:
        return self.root / "depth"

    @property
    def depth_metadata(self) -> Path:
        return self.depth_dir / "metadata.json"

    @property
    def features_dir(self) -> Path:
        return self.root / "features"

    @property
    def features(self) -> Path:
        return self.features_dir / "features.json"

    @property
    def director_dir(self) -> Path:
        return self.root / "director"

    @property
    def stereo_script(self) -> Path:
        return self.director_dir / "stereo_script.json"

    @property
    def draft_analysis(self) -> Path:
        return self.director_dir / "draft_analysis.json"

    @property
    def previews_dir(self) -> Path:
        return self.root / "previews"

    @property
    def renders_dir(self) -> Path:
        return self.root / "renders"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def qc_dir(self) -> Path:
        return self.root / "qc"

    @property
    def qc_report(self) -> Path:
        return self.qc_dir / "report.json"

    @property
    def pipeline_state(self) -> Path:
        return self.root / "pipeline_state.json"

    def ensure(self) -> ProjectLayout:
        self.root.mkdir(parents=True, exist_ok=True)
        root = self.root.resolve()
        for directory in (
            self.source_dir,
            self.shots_dir,
            self.depth_dir,
            self.features_dir,
            self.director_dir,
            self.previews_dir,
            self.renders_dir,
            self.logs_dir,
            self.qc_dir,
        ):
            if directory.exists():
                resolved = directory.resolve()
                if resolved != root and root not in resolved.parents:
                    raise ArtifactError("Project directory resolves outside the project root")
            directory.mkdir(parents=True, exist_ok=True)
        for artifact in (
            self.project,
            self.config,
            self.pipeline_state,
            self.media,
            self.normalized_media,
            self.normalized_video,
            self.audio,
            self.shots,
            self.depth_metadata,
            self.features,
            self.stereo_script,
            self.draft_analysis,
            self.qc_report,
            self.qc_dir / "report.html",
        ):
            if artifact.exists():
                resolved = artifact.resolve()
                if resolved != root and root not in resolved.parents:
                    raise ArtifactError("Project artifact resolves outside the project root")
        return self
