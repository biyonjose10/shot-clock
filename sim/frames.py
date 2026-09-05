"""Renders the actual PNG plates the farm claims to be producing.

A *plate* is a single rendered frame handed to editorial. When a render goes
wrong, the failure is usually visible in the plate long before it shows up in a
log, which is the whole point of this module: it produces frames that a vision
model can be asked to review the way a VFX supervisor reviews dailies.

The frames are cheap 1280x720 matte-painting-ish compositions — gradient sky,
haze band, distant ridge line, foreground architecture silhouettes, atmospheric
falloff, film grain and a dailies burn-in. Nobody will hang one in a gallery,
but at a glance it reads as a rendered CG plate rather than a test pattern.

Three defects are supported, each a real artifact with a real cause:

    "black"            -- the render died before anything was written; the plate
                          comes back nearly black. Classic renderer crash.
    "fireflies"        -- scattered blown-out single-pixel speckles, the
                          signature of undersampled indirect light in Arnold.
    "missing_texture"  -- a large flat magenta region where a texture failed to
                          resolve on the node; magenta is the standard "this
                          file is missing" fill.

Output is deterministic: the same (shot_id, frame_no, defect) always produces
byte-identical pixels, so the demo video can be re-shot at will.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
FRAME_SIZE = (FRAME_WIDTH, FRAME_HEIGHT)

#: Where plates land. Matches the web app's static mount.
FRAMES_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "frames"

#: Defect names accepted by :func:`render_frame`. ``None`` means a clean plate.
DEFECTS: tuple[str, ...] = ("black", "fireflies", "missing_texture")

# Magenta is the industry's universal "texture not found" colour.
MISSING_TEXTURE_COLOR = (255, 0, 255)


def _seed_for(shot_id: str, frame_no: int, defect: str | None) -> int:
    """Stable seed for one plate.

    Uses a hash digest rather than ``hash()`` because Python's string hashing is
    randomised per process, and these frames must be reproducible across runs.
    """
    key = f"{shot_id}|{frame_no}|{defect or 'clean'}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def frame_path(shot_id: str, frame_no: int, defect: str | None = None) -> Path:
    """Where :func:`render_frame` will write this plate."""
    suffix = f"_{defect}" if defect else ""
    return FRAMES_DIR / f"{shot_id}_{frame_no:04d}{suffix}.png"


def _vertical_gradient(
    top: tuple[int, int, int], bottom: tuple[int, int, int], height: int
) -> Image.Image:
    """A full-width vertical ramp, built one pixel wide and stretched."""
    strip = Image.new("RGB", (1, height))
    pixels = strip.load()
    for y in range(height):
        t = y / max(1, height - 1)
        pixels[0, y] = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
    return strip.resize((FRAME_WIDTH, height), Image.BILINEAR)


def _ridge_points(
    rng: random.Random, baseline: int, amplitude: int, step: int
) -> list[tuple[int, int]]:
    """A jagged silhouette line across the frame, for distant terrain."""
    points: list[tuple[int, int]] = [(0, FRAME_HEIGHT)]
    height = baseline
    for x in range(0, FRAME_WIDTH + step, step):
        height += rng.randint(-amplitude, amplitude)
        height = max(baseline - amplitude * 3, min(baseline + amplitude * 3, height))
        points.append((x, height))
    points.append((FRAME_WIDTH, FRAME_HEIGHT))
    return points


def _draw_base_plate(rng: random.Random, frame_no: int) -> Image.Image:
    """The clean composition, before defects, grain and burn-in."""
    horizon = int(FRAME_HEIGHT * 0.62)

    # Sky: cold at the top, warm haze at the horizon. The palette drifts a
    # little per shot so a wall of plates does not look like one image.
    tint = rng.randint(-18, 18)
    sky_top = (28 + tint // 2, 46 + tint // 2, 84 + tint)
    sky_bottom = (196 + tint // 3, 168, 132)
    image = Image.new("RGB", FRAME_SIZE, sky_top)
    image.paste(_vertical_gradient(sky_top, sky_bottom, horizon), (0, 0))

    ground_top = (74, 62, 54)
    ground_bottom = (22, 19, 18)
    image.paste(
        _vertical_gradient(ground_top, ground_bottom, FRAME_HEIGHT - horizon),
        (0, horizon),
    )

    draw = ImageDraw.Draw(image, "RGBA")

    # Sun disc plus a soft bloom, drifting a few pixels per frame so a frame
    # range animates rather than repeating.
    sun_x = int(FRAME_WIDTH * 0.28) + (frame_no % 120)
    sun_y = int(horizon - FRAME_HEIGHT * 0.18)
    for radius, alpha in ((150, 26), (96, 34), (58, 52), (26, 200)):
        draw.ellipse(
            (sun_x - radius, sun_y - radius, sun_x + radius, sun_y + radius),
            fill=(255, 226, 178, alpha),
        )

    # Cloud bands: long flat ellipses, low contrast, parallaxing with frame.
    for _ in range(rng.randint(5, 9)):
        cy = rng.randint(30, horizon - 40)
        cx = rng.randint(-200, FRAME_WIDTH) + frame_no * 2
        w = rng.randint(220, 620)
        h = rng.randint(14, 40)
        alpha = rng.randint(18, 46)
        draw.ellipse((cx, cy, cx + w, cy + h), fill=(232, 226, 214, alpha))

    # Two ridge lines: the far one hazed back, the near one nearly black.
    far = Image.new("RGBA", FRAME_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(far).polygon(
        _ridge_points(rng, horizon - 46, 16, 64), fill=(96, 104, 122, 190)
    )
    image.paste(far, (0, 0), far)

    near = Image.new("RGBA", FRAME_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(near).polygon(
        _ridge_points(rng, horizon - 10, 24, 96), fill=(38, 40, 48, 235)
    )
    image.paste(near, (0, 0), near)

    # Foreground architecture: blocky towers on the horizon with a warm rim
    # light on the sun side, which is what sells these as rendered geometry.
    x = rng.randint(-60, 120)
    while x < FRAME_WIDTH:
        w = rng.randint(48, 150)
        h = rng.randint(60, 300)
        top = horizon - h
        draw.rectangle((x, top, x + w, horizon + 8), fill=(17, 18, 22, 255))
        draw.line((x, top, x, horizon), fill=(214, 178, 128, 150), width=2)
        draw.line((x, top, x + w, top), fill=(150, 132, 108, 110), width=2)
        # A few lit windows so the silhouettes read as buildings.
        for _ in range(rng.randint(0, 6)):
            wx = rng.randint(x + 6, max(x + 7, x + w - 10))
            wy = rng.randint(top + 8, max(top + 9, horizon - 12))
            draw.rectangle((wx, wy, wx + 4, wy + 6), fill=(255, 206, 128, 190))
        x += w + rng.randint(20, 90)

    # Ground haze where the geometry meets the floor.
    haze = Image.new("RGBA", FRAME_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(haze).rectangle(
        (0, horizon - 30, FRAME_WIDTH, horizon + 60), fill=(188, 172, 150, 60)
    )
    image.paste(haze, (0, 0), haze.filter(ImageFilter.GaussianBlur(14)))

    return image


def _apply_grain(image: Image.Image, rng: random.Random, strength: int = 6) -> Image.Image:
    """Add monochrome film grain. Larger ``strength`` means *less* grain."""
    noise_bytes = rng.randbytes(FRAME_WIDTH * FRAME_HEIGHT)
    noise = Image.frombytes("L", FRAME_SIZE, noise_bytes)
    noise = noise.point(lambda v: 128 + (v - 128) // strength)
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    # (base + noise) - 128, so the noise is signed around zero.
    return ImageChops.add(image, noise_rgb, scale=1, offset=-128)


def _apply_fireflies(image: Image.Image, rng: random.Random) -> Image.Image:
    """Scatter blown-out speckles: undersampled indirect light in Arnold.

    Sparse and isolated, which is what the artifact actually looks like. Dense
    speckle reads as a starfield, and a starfield is a plausible creative
    choice rather than a defect -- exactly the wrong call for the tech check.
    """
    draw = ImageDraw.Draw(image)
    for _ in range(260):
        x = rng.randrange(FRAME_WIDTH)
        y = rng.randrange(FRAME_HEIGHT)
        size = rng.choice((0, 0, 1, 1, 2))
        draw.ellipse((x, y, x + size, y + size), fill=(255, 255, 255))
    # A handful of larger, unmistakable hot pixels with a little bloom.
    glow = Image.new("RGB", FRAME_SIZE, (0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for _ in range(22):
        x = rng.randrange(FRAME_WIDTH)
        y = rng.randrange(FRAME_HEIGHT)
        glow_draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 255, 255))
    return ImageChops.add(image, glow.filter(ImageFilter.GaussianBlur(2)))


def _apply_missing_texture(image: Image.Image, rng: random.Random) -> Image.Image:
    """Fill a large region with flat magenta, as a lost texture map does.

    Applied after grain so the patch is perfectly flat — real missing-texture
    fills carry no shading, and that flatness is the giveaway.
    """
    draw = ImageDraw.Draw(image)
    left = rng.randint(80, 420)
    top = rng.randint(int(FRAME_HEIGHT * 0.22), int(FRAME_HEIGHT * 0.42))
    width = rng.randint(520, 760)
    height = rng.randint(240, 380)
    right = min(FRAME_WIDTH - 30, left + width)
    bottom = min(FRAME_HEIGHT - 20, top + height)

    # A blocky, geometry-shaped patch rather than a plain rectangle.
    notch = rng.randint(90, 220)
    draw.polygon(
        [
            (left, bottom),
            (left, top + notch),
            (left + notch, top),
            (right - notch // 2, top),
            (right, top + notch // 2),
            (right, bottom),
        ],
        fill=MISSING_TEXTURE_COLOR,
    )
    return image


def _draw_burn_in(image: Image.Image, shot_id: str, frame_no: int) -> None:
    """Dailies burn-in: shot id, frame number and slate, baked into the corner.

    The burn-in deliberately never names the defect. A vision model reviewing
    these plates has to look at the picture, not read the answer off the slate.
    """
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.load_default(size=20)
        small = ImageFont.load_default(size=16)
    except TypeError:  # very old Pillow: fixed-size bitmap font only
        font = small = ImageFont.load_default()

    draw.rectangle((0, FRAME_HEIGHT - 44, FRAME_WIDTH, FRAME_HEIGHT), fill=(0, 0, 0, 130))
    draw.text((18, FRAME_HEIGHT - 34), f"{shot_id}", font=font, fill=(235, 235, 235))
    draw.text(
        (FRAME_WIDTH - 150, FRAME_HEIGHT - 32),
        f"f{frame_no:04d}",
        font=font,
        fill=(235, 235, 235),
    )
    draw.text(
        (FRAME_WIDTH // 2 - 110, FRAME_HEIGHT - 30),
        "SHOT CLOCK / DAILIES",
        font=small,
        fill=(200, 200, 200),
    )


def render_frame(
    shot_id: str,
    frame_no: int,
    defect: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Render one plate to PNG and return its path.

    Args:
        shot_id: e.g. ``"RC_0410"``. Only used for the seed and the burn-in.
        frame_no: frame number within the shot.
        defect: ``None`` for a clean plate, or one of :data:`DEFECTS`.
        out_dir: override the output directory (defaults to :data:`FRAMES_DIR`).

    Raises:
        ValueError: if ``defect`` is not a known defect name.
    """
    if defect is not None and defect not in DEFECTS:
        raise ValueError(f"unknown defect {defect!r}; expected one of {DEFECTS}")

    # Two independent streams. The base plate is seeded WITHOUT the defect so a
    # clean render and a corrupt render of the same frame are the same shot --
    # only then does a side-by-side read as "this frame came back wrong" rather
    # than as two unrelated images. The defect overlay gets its own stream.
    base_rng = random.Random(_seed_for(shot_id, frame_no, None))
    defect_rng = random.Random(_seed_for(shot_id, frame_no, defect))
    image = _draw_base_plate(base_rng, frame_no)

    if defect == "black":
        # Crashed render: a nearly empty buffer, only sensor noise left.
        image = image.point(lambda v: int(v * 0.02))
        image = _apply_grain(image, base_rng, strength=24)
    else:
        if defect == "fireflies":
            image = _apply_fireflies(image, defect_rng)
        image = _apply_grain(image, base_rng)
        if defect == "missing_texture":
            image = _apply_missing_texture(image, defect_rng)

    _draw_burn_in(image, shot_id, frame_no)

    directory = out_dir or FRAMES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / frame_path(shot_id, frame_no, defect).name
    image.save(path, format="PNG", optimize=True)
    return path


def render_frames(
    shot_id: str,
    frame_numbers: list[int],
    defect: str | None = None,
    out_dir: Path | None = None,
) -> list[Path]:
    """Render several frames of one shot. Handy for building contact sheets."""
    return [render_frame(shot_id, n, defect, out_dir) for n in frame_numbers]
