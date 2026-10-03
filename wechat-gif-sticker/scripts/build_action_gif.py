#!/usr/bin/env python3
"""Build a transparent GIF from two or more action keyframe images."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence

from build_gif import (
    add_outline,
    draw_caption,
    format_color,
    resolve_caption_style,
    select_font_path,
    to_gif_palette,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine illustrated action keyframes without moving the whole sticker."
    )
    parser.add_argument(
        "--input", action="append", required=True, type=Path, help="Ordered transparent keyframe; repeat at least twice"
    )
    parser.add_argument("--output", required=True, type=Path, help="Destination GIF")
    parser.add_argument("--preview", type=Path, help="Optional first-frame PNG")
    parser.add_argument("--contact-sheet", type=Path, help="Optional PNG showing action endpoints")
    parser.add_argument("--text", default="", help="Exact caption text")
    parser.add_argument("--size", type=int, default=512, help="Square output size")
    parser.add_argument("--colors", type=int, default=128, help="GIF palette colors, 16-255")
    parser.add_argument("--outline", type=int, help="White outline width")
    parser.add_argument("--font", type=Path, help="Chinese-capable .ttf/.ttc font")
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
    parser.add_argument("--hold-ms", type=int, default=320, help="Duration of each action endpoint")
    parser.add_argument("--tween-frames", type=int, default=1, help="Short crossfade frames per transition")
    parser.add_argument("--tween-ms", type=int, default=80, help="Duration of each crossfade frame")
    parser.add_argument("--loop-mode", choices=("ping-pong", "cycle"), default="ping-pong")
    return parser.parse_args()


def union_bbox(images: list[Image.Image]) -> tuple[int, int, int, int]:
    boxes = [image.getchannel("A").getbbox() for image in images]
    if any(box is None for box in boxes):
        raise ValueError("Every input keyframe must contain visible pixels")
    valid = [box for box in boxes if box is not None]
    return (
        min(box[0] for box in valid),
        min(box[1] for box in valid),
        max(box[2] for box in valid),
        max(box[3] for box in valid),
    )


def expand_bbox(
    bbox: tuple[int, int, int, int], image_size: tuple[int, int], padding: int
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bbox
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(image_size[0], right + padding),
        min(image_size[1], bottom + padding),
    )


def prepare_keyframes(
    args: argparse.Namespace,
) -> tuple[
    list[Image.Image],
    tuple[int, int, int, int],
    tuple[int, int, int, int],
    tuple[int, int, int, int],
    tuple[int, int, int, int],
]:
    images = [Image.open(path).convert("RGBA") for path in args.input]
    base_size = images[0].size
    images = [
        image if image.size == base_size else image.resize(base_size, Image.Resampling.LANCZOS)
        for image in images
    ]
    padding = max(2, round(max(base_size) * 0.02))
    crop_box = expand_bbox(union_bbox(images), base_size, padding)

    outline = args.outline if args.outline is not None else max(4, args.size // 48)
    caption_band = int(args.size * 0.20) if args.text else int(args.size * 0.06)
    safe_padding = max(outline * 3, int(args.size * 0.05))
    available_width = args.size - safe_padding * 2
    available_height = args.size - caption_band - safe_padding * 2
    crop_width = crop_box[2] - crop_box[0]
    crop_height = crop_box[3] - crop_box[1]
    scale = min(available_width / crop_width, available_height / crop_height)
    target_size = (max(1, round(crop_width * scale)), max(1, round(crop_height * scale)))

    font_path = select_font_path(args.font) if args.text else ""
    font_size = args.font_size or max(28, args.size // 6)
    text_fill, text_stroke, outer_stroke, highlight_fill, text_shadow = resolve_caption_style(
        images[0],
        args.text_fill,
        args.text_stroke,
        args.text_outer_stroke,
        args.highlight_fill,
        args.text_shadow,
    )
    prepared: list[Image.Image] = []
    for image in images:
        subject = image.crop(crop_box).resize(target_size, Image.Resampling.LANCZOS)
        subject = add_outline(subject, outline)
        canvas = Image.new("RGBA", (args.size, args.size), (0, 0, 0, 0))
        x = (args.size - subject.width) // 2
        if args.text and args.text_position == "top":
            y = caption_band + (args.size - caption_band - subject.height) // 2
        else:
            y = (args.size - caption_band - subject.height) // 2
        canvas.alpha_composite(subject, (x, y))
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
        prepared.append(canvas)
    return prepared, text_fill, text_stroke, outer_stroke, highlight_fill


def transition_path(count: int, mode: str) -> list[int]:
    if mode == "ping-pong" and count > 2:
        return list(range(count)) + list(range(count - 2, 0, -1))
    return list(range(count))


def build_timeline(
    keyframes: list[Image.Image], mode: str, hold_ms: int, tween_frames: int, tween_ms: int
) -> tuple[list[Image.Image], list[int]]:
    path = transition_path(len(keyframes), mode)
    frames: list[Image.Image] = []
    durations: list[int] = []
    for position, index in enumerate(path):
        current = keyframes[index]
        next_index = path[position + 1] if position + 1 < len(path) else 0
        target = keyframes[next_index]
        frames.append(current)
        durations.append(hold_ms)
        for step in range(1, tween_frames + 1):
            amount = step / (tween_frames + 1)
            frames.append(Image.blend(current, target, amount))
            durations.append(tween_ms)
    return frames, durations


def save_contact_sheet(keyframes: list[Image.Image], path: Path) -> None:
    thumb_size = min(320, keyframes[0].width)
    columns = min(4, len(keyframes))
    rows = math.ceil(len(keyframes) / columns)
    sheet = Image.new("RGBA", (columns * thumb_size, rows * thumb_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for index, frame in enumerate(keyframes):
        thumb = frame.resize((thumb_size, thumb_size), Image.Resampling.LANCZOS)
        x = (index % columns) * thumb_size
        y = (index // columns) * thumb_size
        sheet.alpha_composite(thumb, (x, y))
        draw.text((x + 8, y + 8), str(index + 1), fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def main() -> None:
    args = parse_args()
    if len(args.input) < 2:
        raise ValueError("Repeat --input with at least two action keyframes")
    if args.size < 64:
        raise ValueError("--size must be at least 64")
    if not 16 <= args.colors <= 255:
        raise ValueError("--colors must be between 16 and 255")
    if args.hold_ms <= 0 or args.tween_ms <= 0 or args.tween_frames < 0:
        raise ValueError("Timing values must be positive and --tween-frames cannot be negative")
    if args.text_stroke_width is not None and args.text_stroke_width < 0:
        raise ValueError("--text-stroke-width cannot be negative")
    if args.text_outer_stroke_width is not None and args.text_outer_stroke_width < 0:
        raise ValueError("--text-outer-stroke-width cannot be negative")
    if args.text_shadow_offset is not None and args.text_shadow_offset < 0:
        raise ValueError("--text-shadow-offset cannot be negative")
    if args.caption_lift is not None and args.caption_lift < 0:
        raise ValueError("--caption-lift cannot be negative")

    keyframes, text_fill, text_stroke, outer_stroke, highlight_fill = prepare_keyframes(args)
    timeline, durations = build_timeline(
        keyframes, args.loop_mode, args.hold_ms, args.tween_frames, args.tween_ms
    )
    paletted = [to_gif_palette(frame, args.colors) for frame in timeline]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    paletted[0].save(
        args.output,
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        transparency=255,
        disposal=2,
        optimize=True,
    )
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        keyframes[0].save(args.preview)
    if args.contact_sheet:
        save_contact_sheet(keyframes, args.contact_sheet)

    with Image.open(args.output) as result:
        actual_frames = getattr(result, "n_frames", 1)
        total_ms = sum(frame.info.get("duration", 0) for frame in ImageSequence.Iterator(result))
    style_info = (
        f" | text {format_color(text_fill)} / {format_color(text_stroke)} / "
        f"{format_color(outer_stroke)}"
        f" | highlight {args.highlight_text or 'none'} {format_color(highlight_fill)}"
        if args.text
        else ""
    )
    print(
        f"Saved {args.output} | {args.size}x{args.size} | {actual_frames} encoded frames | "
        f"{total_ms} ms | {args.output.stat().st_size} bytes{style_info}"
    )


if __name__ == "__main__":
    main()
