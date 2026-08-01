"""Typer command line for individual resumable stages and full runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from .artifacts import ProjectLayout, read_model
from .config import AppConfig, load_config, sanitize_runtime_paths, save_config
from .errors import AIStereoError
from .media.probe import inspect_media
from .pipeline import AIStereoPipeline

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Offline shot-aware stereo conversion"
)


def _print(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _project_settings(work_dir: Path, config: Path | None = None) -> AppConfig:
    if config is not None:
        return load_config(config)
    layout = ProjectLayout.at(work_dir).ensure()
    if layout.config.is_file():
        return read_model(layout.config, AppConfig)
    return AppConfig()


def _persisted_pipeline(work_dir: Path, settings: AppConfig) -> AIStereoPipeline:
    layout = ProjectLayout.at(work_dir).ensure()
    save_config(settings, layout.config)
    return AIStereoPipeline(layout.root, config=settings)


def _existing_pipeline(work_dir: Path, config: Path | None = None) -> AIStereoPipeline:
    settings = sanitize_runtime_paths(_project_settings(work_dir, config))
    if config is not None:
        return _persisted_pipeline(work_dir, settings)
    return AIStereoPipeline(work_dir, config=settings)


def _handle_error(exc: AIStereoError) -> None:
    typer.echo(json.dumps({"error": exc.as_dict()}, ensure_ascii=False), err=True)
    raise typer.Exit(1)


@app.command()
def inspect(
    input_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    ffprobe: str = typer.Option("ffprobe", help="FFprobe executable path"),
) -> None:
    """Inspect a video without creating a project."""
    try:
        _print(inspect_media(input_path, ffprobe_path=ffprobe))
    except AIStereoError as exc:
        _handle_error(exc)


@app.command()
def normalize(
    input_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    work_dir: Path = typer.Option(..., "--work-dir"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    try:
        settings = sanitize_runtime_paths(load_config(config)) if config is not None else None
        pipeline = AIStereoPipeline.create(work_dir, input_path, config=settings)
        _print(pipeline.normalize())
    except AIStereoError as exc:
        _handle_error(exc)


@app.command("detect-shots")
def detect_shots_command(
    input_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    work_dir: Path = typer.Option(..., "--work-dir"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    try:
        settings = sanitize_runtime_paths(load_config(config)) if config is not None else None
        pipeline = AIStereoPipeline.create(work_dir, input_path, config=settings)
        _print(pipeline.detect_shots())
    except AIStereoError as exc:
        _handle_error(exc)


@app.command("estimate-depth")
def estimate_depth_command(
    work_dir: Path = typer.Option(..., "--work-dir"),
    backend: str | None = typer.Option(None, "--backend"),
    cache_dir: Path | None = typer.Option(None, "--cache-dir", file_okay=False),
    device: str | None = typer.Option(None, "--device"),
    config: Path | None = typer.Option(None, "--config"),
    allow_fallback: bool = typer.Option(False, "--allow-fallback"),
) -> None:
    try:
        settings = _project_settings(work_dir, config)
        if backend:
            settings.depth.backend = backend
        if device:
            settings.depth.device = device  # type: ignore[assignment]
        settings = sanitize_runtime_paths(settings)
        pipeline = _persisted_pipeline(work_dir, settings)
        _print(
            pipeline.estimate_depth(
                cache_dir=cache_dir,
                allow_fallback=allow_fallback,
            )
        )
    except (AIStereoError, ValueError) as exc:
        if isinstance(exc, AIStereoError):
            _handle_error(exc)
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def direct(
    work_dir: Path = typer.Option(..., "--work-dir"),
    director: str = typer.Option("rules", "--director"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    try:
        _print(_existing_pipeline(work_dir, config).direct(director_name=director))
    except AIStereoError as exc:
        _handle_error(exc)


@app.command("preview-shot")
def preview_shot(
    work_dir: Path = typer.Option(..., "--work-dir"),
    shot: int = typer.Option(..., "--shot", min=1),
    output: Path | None = typer.Option(None, "--output"),
    mode: str = typer.Option("anaglyph", "--output-mode"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    try:
        layout = ProjectLayout.at(work_dir)
        destination = output or layout.previews_dir / f"shot_{shot:04d}.mp4"
        result = _existing_pipeline(work_dir, config).render(
            destination,
            shot_id=shot,
            output_mode=mode,  # type: ignore[arg-type]
        )
        _print({"preview_path": str(result)})
    except AIStereoError as exc:
        _handle_error(exc)


@app.command()
def render(
    work_dir: Path = typer.Option(..., "--work-dir"),
    output: Path = typer.Option(..., "--output"),
    anaglyph_mode: str = typer.Option("calibrated", "--anaglyph-mode"),
    swap_eyes: bool = typer.Option(False, "--swap-eyes"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    try:
        settings = _project_settings(work_dir, config)
        settings.render.anaglyph_mode = anaglyph_mode  # type: ignore[assignment]
        settings.render.swap_eyes = swap_eyes
        settings = sanitize_runtime_paths(settings)
        result = _persisted_pipeline(work_dir, settings).render(output)
        _print({"output_path": str(result)})
    except (AIStereoError, ValueError) as exc:
        if isinstance(exc, AIStereoError):
            _handle_error(exc)
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def qc(
    work_dir: Path = typer.Option(..., "--work-dir"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    try:
        _print(_existing_pipeline(work_dir, config).qc())
    except AIStereoError as exc:
        _handle_error(exc)


@app.command()
def run(
    input_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path = typer.Option(..., "--output"),
    work_dir: Path | None = typer.Option(None, "--work-dir"),
    resolution: str = typer.Option("720p", "--resolution"),
    director: str = typer.Option("rules", "--director"),
    backend: str | None = typer.Option(None, "--backend"),
    anaglyph_mode: str = typer.Option("calibrated", "--anaglyph-mode"),
    resume: bool = typer.Option(True, "--resume/--no-resume"),
    force_stage: list[str] | None = typer.Option(None, "--force-stage"),
    device: str = typer.Option("auto", "--device"),
    depth_resolution: str | None = typer.Option(None, "--depth-resolution", help="WIDTHxHEIGHT"),
    swap_eyes: bool = typer.Option(False, "--swap-eyes"),
    max_popout: float | None = typer.Option(None, "--max-popout"),
    max_background_depth: float | None = typer.Option(None, "--max-background-depth"),
    preview_only: bool = typer.Option(False, "--preview-only"),
    keep_intermediates: bool = typer.Option(True, "--keep-intermediates/--discard-intermediates"),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    del keep_intermediates  # Intermediates are currently required for reproducible resume.
    try:
        project_dir = work_dir or output.parent / f"{output.stem}.aistereo"
        settings = _project_settings(project_dir, config)
        heights = {"720p": 720, "1080p": 1080}
        if resolution not in heights:
            raise typer.BadParameter("resolution must be 720p or 1080p")
        settings.media.target_height = heights[resolution]
        if backend:
            settings.depth.backend = backend
        settings.render.anaglyph_mode = anaglyph_mode  # type: ignore[assignment]
        settings.render.swap_eyes = swap_eyes
        settings.depth.device = device  # type: ignore[assignment]
        if depth_resolution:
            width, height = depth_resolution.lower().split("x", 1)
            settings.depth.width, settings.depth.height = int(width), int(height)
        if max_popout is not None:
            settings.comfort.max_popout_disparity_norm = max_popout
        if max_background_depth is not None:
            settings.comfort.max_background_disparity_norm = max_background_depth
        settings = sanitize_runtime_paths(settings)
        pipeline = AIStereoPipeline.create(project_dir, input_path, config=settings)
        pipeline.resume = resume
        pipeline.force_stages = set(force_stage or [])
        if preview_only:
            pipeline.inspect()
            pipeline.normalize()
            manifest = pipeline.detect_shots()
            pipeline.estimate_depth()
            pipeline.extract_features()
            pipeline.direct(director_name=director)
            first = manifest.shots[0].shot_id
            result = pipeline.render(output, shot_id=first)
        else:
            result = pipeline.run(output, director_name=director)
        _print({"output_path": str(result), "project_dir": str(project_dir)})
    except (AIStereoError, ValueError) as exc:
        if isinstance(exc, AIStereoError):
            _handle_error(exc)
        raise typer.BadParameter(str(exc)) from exc


def main() -> None:
    app()


if __name__ == "__main__":
    main()
