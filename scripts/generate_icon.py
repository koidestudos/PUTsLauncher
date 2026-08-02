from __future__ import annotations

"""Generate a simple ICO for the launcher (no external assets required)."""

from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    raise SystemExit("Pillow required: pip install Pillow")


def _make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (12, 23, 20, 255))
    draw = ImageDraw.Draw(img)
    margin = max(1, size // 10)
    # Pillow rounded_rectangle needs recent Pillow; fall back to rectangle.
    box = [margin, margin, size - margin - 1, size - margin - 1]
    try:
        draw.rounded_rectangle(box, radius=max(2, size // 6), fill=(226, 168, 74, 255))
    except Exception:
        draw.rectangle(box, fill=(226, 168, 74, 255))
    inset = max(2, size // 3)
    draw.rectangle([inset, inset, size - inset - 1, size - inset - 1], fill=(12, 23, 20, 255))
    return img


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "launcher" / "assets" / "icon.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    images = [_make(s) for s in sizes]
    # Save largest as master with appendages for other sizes
    images[-1].save(out, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[:-1])
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
