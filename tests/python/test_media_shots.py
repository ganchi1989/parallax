from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import aistereo.media.normalize as normalize_module
import aistereo.media.probe as probe_module
import aistereo.media.remux as remux_module
from aistereo.config import MediaConfig
from aistereo.errors import ExternalToolError
from aistereo.media.normalize import build_extract_audio_command, build_normalize_video_command
from aistereo.media.probe import build_ffprobe_command, inspect_media, parse_ffprobe
from aistereo.media.remux import build_remux_command, remux_audio
from aistereo.models import MediaInfo
from aistereo.shots.detector import _boundaries_to_manifest


def test_parse_ffprobe_handles_vfr_rotation_and_audio() -> None:
    data = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24000/1001",
                "r_frame_rate": "30/1",
                "duration": "2.002",
                "nb_read_frames": "48",
                "pix_fmt": "yuv420p",
                "side_data_list": [{"rotation": -90}],
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "sample_rate": "48000",
                "duration": "1.99",
                "tags": {"language": "eng"},
            },
        ],
        "format": {"duration": "2.002"},
    }
    result = parse_ffprobe(data, "movie.mp4")
    assert result.frame_count == 48
    assert result.variable_frame_rate is True
    assert result.rotation_degrees == 270
    assert result.audio_streams[0].language == "eng"
    assert result.audio_streams[0].duration_seconds == 1.99


def test_commands_are_argument_vectors_without_shell_fragments(tmp_path: Path) -> None:
    config = MediaConfig(target_height=720, target_fps=24)
    source = tmp_path / "source with spaces.mp4"
    output = tmp_path / "normalized.mp4"
    media = MediaInfo(
        path=str(source),
        width=1920,
        height=1080,
        frame_rate=24,
        duration_seconds=1,
        frame_count=24,
    )
    probe = build_ffprobe_command(source)
    normalize = build_normalize_video_command(source, output, config, media)
    audio = build_extract_audio_command(source, tmp_path / "audio.mka", config)
    remux = build_remux_command(output, tmp_path / "final.mp4", audio_path=tmp_path / "audio.mka")
    assert str(source.resolve()) in probe
    assert "-vsync" in normalize and "cfr" in normalize
    assert normalize[normalize.index("-preset") + 1] == "veryfast"
    assert audio[audio.index("-map") : audio.index("-map") + 2] == ["-map", "0:a"]
    assert "1:a?" in remux
    assert "1:a:0?" not in remux
    assert all(";" not in part for part in normalize)


def test_ffprobe_output_is_file_backed_and_bounded_before_json(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    class Process:
        def __init__(self, _command: list[str], *, stdout, stderr, **_kwargs: object) -> None:
            assert stdout != subprocess.PIPE
            assert stderr != subprocess.PIPE
            self.stdout = stdout
            self.returncode = 0

        def wait(self, *, timeout: float) -> int:
            del timeout
            self.stdout.write(b"x" * (probe_module.MAX_FFPROBE_STDOUT_BYTES + 1))
            self.stdout.flush()
            return self.returncode

    monkeypatch.setattr(probe_module.subprocess, "Popen", Process)

    with pytest.raises(ExternalToolError, match="bounded size limit") as captured:
        inspect_media(source)
    assert captured.value.details["bytes"] == probe_module.MAX_FFPROBE_STDOUT_BYTES + 1


def test_ffprobe_kills_active_writer_when_capture_exceeds_limit(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    process_state = {"killed": False, "normal_exit": False, "waits": 0}

    class Process:
        def __init__(self, _command: list[str], *, stdout, **_kwargs: object) -> None:
            self.stdout = stdout
            self.returncode = None

        def wait(self, *, timeout: float) -> int:
            process_state["waits"] += 1
            if process_state["killed"]:
                self.returncode = -9
                return self.returncode
            if process_state["waits"] == 1:
                self.stdout.write(b"x" * (probe_module.MAX_FFPROBE_STDOUT_BYTES + 1))
                self.stdout.flush()
                raise subprocess.TimeoutExpired("ffprobe", timeout)
            process_state["normal_exit"] = True
            self.returncode = 0
            return self.returncode

        def kill(self) -> None:
            process_state["killed"] = True

    monkeypatch.setattr(probe_module.subprocess, "Popen", Process)

    with pytest.raises(ExternalToolError, match="bounded size limit"):
        inspect_media(source)
    assert process_state == {"killed": True, "normal_exit": False, "waits": 2}


def test_ffprobe_timeout_kills_and_reaps_process(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    process_state = {"killed": False, "waits": 0}

    class Process:
        def __init__(self, _command: list[str], **_kwargs: object) -> None:
            self.returncode = None

        def wait(self, *, timeout: float) -> int:
            del timeout
            process_state["waits"] += 1
            if not process_state["killed"]:
                raise subprocess.TimeoutExpired("ffprobe", 0.01)
            self.returncode = -9
            return self.returncode

        def kill(self) -> None:
            process_state["killed"] = True

    monkeypatch.setattr(probe_module.subprocess, "Popen", Process)

    with pytest.raises(ExternalToolError, match="timed out"):
        inspect_media(source, timeout_seconds=0.01)
    assert process_state == {"killed": True, "waits": 2}


def test_ffprobe_parses_bounded_file_backed_json(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    response = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "width": 32,
                "height": 24,
                "avg_frame_rate": "24/1",
                "nb_read_frames": "1",
            }
        ],
        "format": {"duration": "0.0416667"},
    }

    class Process:
        def __init__(self, _command: list[str], *, stdout, **_kwargs: object) -> None:
            stdout.write(json.dumps(response).encode("utf-8"))
            stdout.flush()
            self.returncode = 0

        def wait(self, *, timeout: float) -> int:
            del timeout
            return self.returncode

    monkeypatch.setattr(probe_module.subprocess, "Popen", Process)

    media = inspect_media(source)
    assert (media.width, media.height, media.frame_count) == (32, 24, 1)


def test_mp4_remux_retries_incompatible_audio_as_aac(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mka"
    output = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    commands: list[list[str]] = []

    class Process:
        def __init__(self, command: list[str], *, stderr, **_kwargs: object) -> None:
            commands.append(command)
            self.returncode = 1 if command[command.index("-c:a") + 1] == "copy" else 0
            if self.returncode:
                stderr.write(b"audio codec is not supported by this container")
            else:
                Path(command[-1]).write_bytes(b"remuxed")

        def poll(self) -> int:
            return self.returncode

    monkeypatch.setattr(remux_module.subprocess, "Popen", Process)

    assert remux_audio(video, output, audio_path=audio) == output.resolve()
    assert output.read_bytes() == b"remuxed"
    assert [command[command.index("-c:a") + 1] for command in commands] == ["copy", "aac"]
    assert "192k" in commands[1]


def test_silent_normalization_removes_audio_from_a_prior_source(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.mp4"
    video = tmp_path / "normalized.mp4"
    audio = tmp_path / "audio.mka"
    source.write_bytes(b"source")
    audio.write_bytes(b"stale audio")
    media = MediaInfo(
        path=str(source),
        width=32,
        height=24,
        frame_rate=24,
        duration_seconds=1,
        frame_count=24,
    )

    def fake_run(command: list[str], _cancel: object) -> None:
        Path(command[-1]).write_bytes(b"normalized")

    monkeypatch.setattr(normalize_module, "_run_process", fake_run)
    normalized, produced_audio = normalize_module.normalize_media(
        source, video, audio, media, MediaConfig()
    )
    assert normalized == video.resolve()
    assert produced_audio is None
    assert not audio.exists()


def test_shot_boundaries_are_inclusive_contiguous_and_cover_source(tmp_path: Path) -> None:
    manifest = _boundaries_to_manifest(tmp_path / "x.mp4", [10, 20, 20, -1, 100], 30, 10)
    assert [(s.start_frame, s.end_frame) for s in manifest.shots] == [(0, 9), (10, 19), (20, 29)]
    assert manifest.shots[0].transition == "start"
    assert manifest.shots[1].start_time == 1.0
