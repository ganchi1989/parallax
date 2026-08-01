"""Generate the deterministic Parallax Forge application icon set.

Pillow is a development-only tool and is not linked into the desktop binary.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


CANVAS = 1024
ICON_DIR = Path(__file__).resolve().parents[1] / "icons"


def build_master() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Deep-blue forged tile with a restrained cool highlight.
    draw.rounded_rectangle(
        (40, 40, 984, 984), radius=224, fill=(8, 16, 31, 255), outline=(44, 62, 84, 255), width=18
    )
    draw.rounded_rectangle(
        (74, 74, 950, 950), radius=196, outline=(72, 92, 116, 160), width=5
    )

    # Two offset optical apertures form both a stereo pair and a forge spark.
    for offset, color in ((-92, (246, 69, 91, 238)), (92, (35, 222, 231, 238))):
        center_x = CANVAS // 2 + offset
        box = (center_x - 236, 276, center_x + 236, 748)
        draw.ellipse(box, outline=color, width=86)

    # Screen plane / convergence mark.
    draw.rounded_rectangle((476, 214, 548, 810), radius=34, fill=(232, 239, 247, 232))
    draw.polygon(
        ((512, 164), (570, 244), (536, 244), (536, 326), (488, 326), (488, 244), (454, 244)),
        fill=(255, 194, 84, 255),
    )
    return image


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    master = build_master()
    resampling = Image.Resampling.LANCZOS

    for name, size in (("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256)):
        master.resize((size, size), resampling).save(ICON_DIR / name, optimize=True)

    master.resize((256, 256), resampling).save(
        ICON_DIR / "icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
