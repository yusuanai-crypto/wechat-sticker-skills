#!/usr/bin/env python3
"""Build a transparent, lightly animated sticker GIF from one RGBA master image."""

from __future__ import annotations

import argparse
import colorsys
import math
import os
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


FONT_CANDIDATES = (
    r"C:\Windows\Fonts\STXINGKA.TTF",
    r"C:\Windows\Fonts\STXINWEI.TTF",
    r"C:\Windows\Fonts\STHUPO.TTF",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simyou.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add exact, image-matched text, a white subject outline, and a subtle loop."
    )
    parser.add_argument("--input", required=True, type=Path, help="Transparent source image")
    parser.add_argument("--output", required=True, type=Path, help="Destination GIF")
    parser.add_argument("--preview", type=Path, help="Optional first-frame PNG for review")
    parser.add_argument("--text", default="", help="Exact caption text")
    parser.add_argument(
        "--animation",
        choices=("bob", "bounce", "pulse", "shake", "sway", "none"),
        default="bob",
    )
    parser.add_argument("--size", type=int, default=512, help="Square output size in pixels")
    parser.add_argument("--frames", type=int, default=12, help="Frame count")
    parser.add_argument("--duration-ms", type=int, default=100, help="Duration per frame")
    parser.add_argument("--colors", type=int, default=96, help="GIF palette colors, 16-255")
    parser.add_argument("--outline", type=int, help="White outline width in output pixels")
    parser.add_argument("--motion", type=float, default=1.0, help="Motion intensity multiplier")
    parser.add_argument("--font", type=Path, help="Font file with Chinese glyph support")
    parser.add_argument("--font-size", type=int, help="Starting caption size")
    parser.add_argument("--text-position", choices=("top", "bottom"), default="bottom")
    parser.add_argument(
        "--text-fill", default="#FFFFFF", help="Main caption fill: auto or #RRGGBB/#RRGGBBAA"
    )
    parser.add_argument(
        "--text-stroke", default="#181818", help="Inner caption stroke: auto or #RRGGBB/#RRGGBBAA"
    )
    parser.add_argument("--text-stroke-width", type=int, help="Inner caption stroke width in pixels")
    parser.add_argument(
        "--text-outer-stroke", default="#FFFFFF", help="Outer sticker stroke color"
    )
    parser.add_argument("--text-outer-stroke-width", type=int, help="Outer sticker stroke width")
    parser.add_argument("--highlight-text", default="", help="Exact caption substring to highlight")
    parser.add_argument("--highlight-fill", default="#FFB21A", help="Highlight fill color")
    parser.add_argument("--text-shadow", default="#626262A0", help="Down-right shadow color")
    parser.add_argument("--text-shadow-offset", type=int, help="Shadow offset in pixels")
    parser.add_argument("--caption-lift", type=int, help="Raise bottom caption toward the subject")
    return parser.parse_args()


def select_font_path(explicit: Path | None) -> str:
    if explicit:
        if not explicit.is_file():
            raise FileNotFoundError(f"Font not found: {explicit}")
        return str(explicit)
    for candidate in FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "No CJK font found. Pass --font with a Chinese-capable .ttf/.ttc file."
    )


def parse_color(value: str, option: str) -> tuple[int, int, int, int] | None:
    normalized = value.strip()
    if normalized.lower() == "auto":
        return None
    if normalized.startswith("#"):
        normalized = normalized[1:]
    if len(normalized) not in (6, 8):
        raise ValueError(f"{option} must be auto, #RRGGBB, or #RRGGBBAA")
    try:
        channels = tuple(int(normalized[index : index + 2], 16) for index in range(0, len(normalized), 2))
    except ValueError as exc:
        raise ValueError(f"{option} contains an invalid hex color") from exc
    if len(channels) == 3:
        return channels + (255,)
    return channels


def color_luminance(color: tuple[int, int, int, int]) -> float:
    def channel(value: int) -> float:
        normalized = value / 255.0
        return normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4

    red, green, blue, _ = color
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def pixel_data(image: Image.Image):
    flattened = getattr(image, "get_flattened_data", None)
    return flattened() if flattened is not None else image.getdata()


def auto_caption_fill(image: Image.Image) -> tuple[int, int, int, int]:
    sample = image.convert("RGBA")
    sample.thumbnail((128, 128), Image.Resampling.LANCZOS)
    visible = [pixel[:3] for pixel in pixel_data(sample) if pixel[3] >= 96]
    if not visible:
        return (35, 35, 38, 255)

    strip = Image.new("RGB", (len(visible), 1))
    strip.putdata(visible)
    quantized = strip.quantize(colors=12, method=Image.Quantize.MEDIANCUT).convert("RGB")
    palette_counts = Counter(pixel_data(quantized))
    candidates: list[tuple[float, tuple[int, int, int]]] = []
    fallbacks: list[tuple[int, tuple[int, int, int]]] = []
    for rgb, count in palette_counts.items():
        red, green, blue = (channel / 255.0 for channel in rgb)
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        luminance = color_luminance((*rgb, 255))
        if 0.08 < luminance < 0.90:
            fallbacks.append((count, rgb))
        if saturation < 0.20 or value < 0.20 or value > 0.96:
            continue
        skin_tone = 0.02 <= hue <= 0.13 and 0.18 <= saturation <= 0.68 and value >= 0.52
        skin_penalty = 0.30 if skin_tone else 1.0
        middle_value_bonus = 1.0 - abs(value - 0.68) * 0.35
        score = count * (0.55 + 1.75 * saturation) * middle_value_bonus * skin_penalty
        candidates.append((score, rgb))

    if candidates:
        selected = max(candidates, key=lambda item: item[0])[1]
    elif fallbacks:
        selected = max(fallbacks, key=lambda item: item[0])[1]
    else:
        selected = (35, 35, 38)
    return (*selected, 255)


def resolve_caption_colors(
    image: Image.Image, fill_value: str, stroke_value: str
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    fill = parse_color(fill_value, "--text-fill") or auto_caption_fill(image)
    stroke = parse_color(stroke_value, "--text-stroke")
    if stroke is None:
        stroke = (255, 250, 242, 255) if color_luminance(fill) < 0.46 else (35, 31, 32, 255)
    return fill, stroke


def resolve_caption_style(
    image: Image.Image,
    fill_value: str,
    stroke_value: str,
    outer_stroke_value: str,
    highlight_value: str,
    shadow_value: str,
) -> tuple[
    tuple[int, int, int, int],
    tuple[int, int, int, int],
    tuple[int, int, int, int],
    tuple[int, int, int, int],
    tuple[int, int, int, int],
]:
    fill, stroke = resolve_caption_colors(image, fill_value, stroke_value)
    outer_stroke = parse_color(outer_stroke_value, "--text-outer-stroke") or (255, 255, 255, 255)
    highlight = parse_color(highlight_value, "--highlight-fill") or auto_caption_fill(image)
    shadow = parse_color(shadow_value, "--text-shadow") or (98, 98, 98, 160)
    return fill, stroke, outer_stroke, highlight, shadow


def format_color(color: tuple[int, int, int, int]) -> str:
    red, green, blue, alpha = color
    return f"#{red:02X}{green:02X}{blue:02X}" if alpha == 255 else f"#{red:02X}{green:02X}{blue:02X}{alpha:02X}"


def default_text_stroke_width(font_size: int) -> int:
    return max(2, round(font_size * 0.11))


def default_outer_stroke_width(font_size: int) -> int:
    return max(1, round(font_size * 0.06))


def load_font(
    font_path: str,
    start_size: int,
    text: str,
    max_width: int,
    stroke_width: int | None = None,
    outer_stroke_width: int | None = None,
) -> ImageFont.FreeTypeFont:
    size = start_size
    probe = Image.new("L", (8, 8))
    draw = ImageDraw.Draw(probe)
    while size >= 12:
        font = ImageFont.truetype(font_path, size=size)
        inner_stroke = stroke_width if stroke_width is not None else default_text_stroke_width(size)
        outer_stroke = (
            outer_stroke_width
            if outer_stroke_width is not None
            else default_outer_stroke_width(size)
        )
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=inner_stroke + outer_stroke)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(font_path, size=12)


def trim_transparency(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("Input image is fully transparent")
    return image.crop(bbox)


def fit_image(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    scale = min(max_width / image.width, max_height / image.height)
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def add_outline(image: Image.Image, width: int) -> Image.Image:
    if width <= 0:
        return image
    pad = width + 2
    padded = Image.new("RGBA", (image.width + 2 * pad, image.height + 2 * pad))
    padded.alpha_composite(image, (pad, pad))
    alpha = padded.getchannel("A")
    expanded = alpha.filter(ImageFilter.MaxFilter(2 * width + 1))
    outline = Image.new("RGBA", padded.size, (255, 255, 255, 0))
    outline.putalpha(expanded)
    outline.alpha_composite(padded)
    return outline


def transform_subject(
    subject: Image.Image, animation: str, phase: float, intensity: float
) -> tuple[Image.Image, int, int]:
    dx = dy = 0
    scale = 1.0
    angle = 0.0
    wave = math.sin(phase)

    if animation == "bob":
        dy = round(-5 * intensity * wave)
    elif animation == "bounce":
        lift = (1.0 - math.cos(phase)) / 2.0
        dy = round(-7 * intensity * lift)
        scale = 1.0 + 0.025 * intensity * lift
    elif animation == "pulse":
        scale = 1.0 + 0.025 * intensity * (1.0 - math.cos(phase)) / 2.0
    elif animation == "shake":
        dx = round(5 * intensity * math.sin(phase * 2.0))
        angle = 1.2 * intensity * math.sin(phase * 2.0)
    elif animation == "sway":
        angle = 1.5 * intensity * wave

    transformed = subject
    if scale != 1.0:
        transformed = transformed.resize(
            (max(1, round(subject.width * scale)), max(1, round(subject.height * scale))),
            Image.Resampling.LANCZOS,
        )
    if angle:
        transformed = transformed.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    return transformed, dx, dy


def draw_caption(
    canvas: Image.Image,
    text: str,
    font_path: str,
    start_size: int,
    position: str,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int],
    stroke_width: int | None = None,
    outer_stroke_fill: tuple[int, int, int, int] = (255, 255, 255, 255),
    outer_stroke_width: int | None = None,
    highlight_text: str = "",
    highlight_fill: tuple[int, int, int, int] = (255, 178, 26, 255),
    shadow_fill: tuple[int, int, int, int] = (98, 98, 98, 160),
    shadow_offset: int | None = None,
    caption_lift: int | None = None,
) -> None:
    if not text:
        return
    draw = ImageDraw.Draw(canvas)
    if highlight_text and (text.count(highlight_text) != 1 or "\n" in text):
        raise ValueError("--highlight-text must occur exactly once in a single-line caption")
    font = load_font(
        font_path,
        start_size,
        text,
        int(canvas.width * 0.88),
        stroke_width=stroke_width,
        outer_stroke_width=outer_stroke_width,
    )
    stroke = stroke_width if stroke_width is not None else default_text_stroke_width(font.size)
    outer_stroke = (
        outer_stroke_width
        if outer_stroke_width is not None
        else default_outer_stroke_width(font.size)
    )
    total_stroke = stroke + outer_stroke
    shadow_shift = shadow_offset if shadow_offset is not None else max(1, round(font.size * 0.06))
    lift = caption_lift if caption_lift is not None else max(0, canvas.width // 32)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=total_stroke)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (canvas.width - width) // 2 - bbox[0]
    margin = max(8, canvas.width // 40)
    if position == "top":
        y = margin - bbox[1]
    else:
        y = canvas.height - margin - height - bbox[1] - shadow_shift - lift
    draw.text(
        (x + shadow_shift, y + shadow_shift),
        text,
        font=font,
        fill=shadow_fill,
        stroke_width=total_stroke,
        stroke_fill=shadow_fill,
    )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=total_stroke,
        stroke_fill=outer_stroke_fill,
    )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke,
        stroke_fill=stroke_fill,
    )
    if highlight_text:
        prefix = text[: text.index(highlight_text)]
        highlight_x = x + round(draw.textlength(prefix, font=font))
        draw.text(
            (highlight_x, y),
            highlight_text,
            font=font,
            fill=highlight_fill,
            stroke_width=stroke,
            stroke_fill=stroke_fill,
        )


def to_gif_palette(frame: Image.Image, colors: int) -> Image.Image:
    alpha = frame.getchannel("A")
    rgb = Image.new("RGB", frame.size, (255, 255, 255))
    rgb.paste(frame.convert("RGB"), mask=alpha)
    paletted = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    transparent_index = 255
    mask = alpha.point(lambda value: 255 if value < 128 else 0, mode="1")
    paletted.paste(transparent_index, mask=mask)
    palette = paletted.getpalette() or []
    palette.extend([0] * (768 - len(palette)))
    paletted.putpalette(palette[:768])
    paletted.info["transparency"] = transparent_index
    paletted.info["disposal"] = 2
    return paletted


def main() -> None:
    args = parse_args()
    if args.size < 64:
        raise ValueError("--size must be at least 64")
    if args.frames < 1:
        raise ValueError("--frames must be at least 1")
    if not 16 <= args.colors <= 255:
        raise ValueError("--colors must be between 16 and 255")
    if args.motion <= 0:
        raise ValueError("--motion must be greater than 0")
    if args.text_stroke_width is not None and args.text_stroke_width < 0:
        raise ValueError("--text-stroke-width cannot be negative")
    if args.text_outer_stroke_width is not None and args.text_outer_stroke_width < 0:
        raise ValueError("--text-outer-stroke-width cannot be negative")
    if args.text_shadow_offset is not None and args.text_shadow_offset < 0:
        raise ValueError("--text-shadow-offset cannot be negative")
    if args.caption_lift is not None and args.caption_lift < 0:
        raise ValueError("--caption-lift cannot be negative")

    source = trim_transparency(Image.open(args.input))
    outline_width = args.outline if args.outline is not None else max(4, args.size // 48)
    font_path = select_font_path(args.font) if args.text else ""
    font_size = args.font_size or max(28, args.size // 6)
    text_fill, text_stroke, outer_stroke, highlight_fill, text_shadow = resolve_caption_style(
        source,
        args.text_fill,
        args.text_stroke,
        args.text_outer_stroke,
        args.highlight_fill,
        args.text_shadow,
    )
    caption_band = int(args.size * 0.20) if args.text else int(args.size * 0.06)
    safe_padding = max(outline_width * 3, int(args.size * 0.06))
    subject_height = args.size - caption_band - safe_padding * 2
    subject_width = args.size - safe_padding * 2
    subject = fit_image(source, subject_width, subject_height)
    subject = add_outline(subject, outline_width)

    frames_rgba: list[Image.Image] = []
    for index in range(args.frames):
        phase = 2.0 * math.pi * index / args.frames
        moving, dx, dy = transform_subject(subject, args.animation, phase, args.motion)
        canvas = Image.new("RGBA", (args.size, args.size), (0, 0, 0, 0))
        x = (args.size - moving.width) // 2 + dx
        if args.text and args.text_position == "top":
            base_y = caption_band + (args.size - caption_band - moving.height) // 2
        else:
            base_y = (args.size - caption_band - moving.height) // 2
        y = base_y + dy
        canvas.alpha_composite(moving, (x, y))
        draw_caption(
            canvas,
            args.text,
            font_path,
            font_size,
            args.text_position,
            text_fill,
            text_stroke,
            args.text_stroke_width,
            outer_stroke,
            args.text_outer_stroke_width,
            args.highlight_text,
            highlight_fill,
            text_shadow,
            args.text_shadow_offset,
            args.caption_lift,
        )
        frames_rgba.append(canvas)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames = [to_gif_palette(frame, args.colors) for frame in frames_rgba]
    frames[0].save(
        args.output,
        save_all=True,
        append_images=frames[1:],
        duration=args.duration_ms,
        loop=0,
        transparency=255,
        disposal=2,
        optimize=True,
    )
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        frames_rgba[0].save(args.preview)

    style_info = (
        f" | text {format_color(text_fill)} / {format_color(text_stroke)} / "
        f"{format_color(outer_stroke)}"
        f" | highlight {args.highlight_text or 'none'} {format_color(highlight_fill)}"
        if args.text
        else ""
    )
    print(
        f"Saved {args.output} | {args.size}x{args.size} | "
        f"{args.frames} frames | {args.output.stat().st_size} bytes{style_info}"
    )


if __name__ == "__main__":
    main()
