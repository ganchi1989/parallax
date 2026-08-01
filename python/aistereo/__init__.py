"""AI Stereo Director processing engine.

The package deliberately keeps large inference dependencies optional.  Every
rendering and directing primitive can be exercised with NumPy and synthetic
depth, which makes the safety-critical parts deterministic and testable.
"""

from __future__ import annotations

__version__ = "0.1.0"
