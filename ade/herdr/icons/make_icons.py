#!/usr/bin/env python3
"""Draw the profile icons (a coloured tile with a short label) into the local state folder, never into the repo.

  python3 icons/make_icons.py [--out DIR]     default DIR: <state dir>/icons (~/.local/state/ai_env/herdr/icons)

The profiles (names, icon labels, colours) come from the local profile table, the same file herdr_profile.py reads
($HERDR_PROFILES_CONFIG or ~/.config/ai_env/herdr-profiles.json; start from ../profiles.example.json).
Needs Pillow (`pip install pillow`), only for this drawing; herdr_profile.py itself is stdlib-only and runs this
script on demand (`desktop`) when an icon is missing or older than the table.
"""
import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import herdr_profile  # noqa: E402

FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Helvetica.ttc")


def font_for(size: int) -> ImageFont.ImageFont:
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw(label: str, colour: str, size: int) -> Image.Image:
    scale = 4  # draw large, shrink for smooth edges
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=colour)
    font = font_for(int(big * (0.46 if len(label) == 2 else 0.34)))
    box = d.textbbox((0, 0), label, font=font)
    d.text(((big - (box[2] - box[0])) / 2 - box[0], (big - (box[3] - box[1])) / 2 - box[1]), label, font=font, fill="white")
    return img.resize((size, size), Image.LANCZOS)


def draw_all(config: herdr_profile.Config, out: Path) -> None:
    """One PNG per profile and size, named like herdr_profile.icon_path()."""
    out.mkdir(parents=True, exist_ok=True)
    for profile in config.profiles:
        for size in herdr_profile.ICON_SIZES:
            target = out / herdr_profile.icon_path(config, profile, size).name
            draw(profile.label, profile.color, size).save(target)
            print(target)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="output folder (default: <state dir>/icons)")
    args = ap.parse_args(argv)
    try:
        config = herdr_profile.Config.from_env()
    except herdr_profile.ProfileError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    draw_all(config, Path(args.out) if args.out else config.icons_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
