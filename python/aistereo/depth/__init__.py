"""Pluggable video-depth backends and shot-level post-processing."""

from .base import DepthBackend, create_depth_backend, load_depth_shot, save_depth_shot
from .cached import CachedDepthBackend
from .monocular import MonocularCuesDepthBackend
from .normalize import normalize_depth_shot
from .synthetic import SyntheticDepthBackend
from .video_depth_anything import VideoDepthAnythingBackend

__all__ = [
    "CachedDepthBackend",
    "DepthBackend",
    "MonocularCuesDepthBackend",
    "SyntheticDepthBackend",
    "VideoDepthAnythingBackend",
    "create_depth_backend",
    "load_depth_shot",
    "normalize_depth_shot",
    "save_depth_shot",
]
