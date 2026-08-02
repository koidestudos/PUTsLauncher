from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageTk


def _face(tex: Image.Image, u0: int, v0: int, uw: int, vh: int, unit: int, grid: int, shade: float) -> Image.Image:
    box = (u0 * grid, v0 * grid, (u0 + uw) * grid, (v0 + vh) * grid)
    img = tex.crop(box).resize((uw * unit, vh * unit), Image.Resampling.NEAREST)
    if shade < 0.99:
        img = ImageEnhance.Brightness(img).enhance(shade)
    return img


def render_skin_frame(texture: Image.Image, yaw_deg: float = 35.0, scale: int = 8) -> Image.Image:
    """Orthographic turntable render of a Minecraft skin (correct aspect, no stretch)."""
    tex = texture.convert("RGBA")
    w, h = tex.size
    if h == 32 and w == 64:
        padded = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        padded.paste(tex, (0, 0))
        tex = padded
        w, h = 64, 64
    grid = max(1, w // 64)

    yaw = math.radians(yaw_deg)
    show_right = math.cos(yaw) >= 0
    side_amt = abs(math.sin(yaw))
    front_shade = 1.0
    side_shade = 0.70

    unit = scale
    W = 20 * unit
    H = 34 * unit
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cx = W // 2

    def blit(img: Image.Image, xy: tuple[int, int]) -> None:
        canvas.alpha_composite(img, xy)

    def part(u, v, ou, ov, pw, ph, shade, xy):
        blit(_face(tex, u, v, pw, ph, unit, grid, shade), xy)
        blit(_face(tex, ou, ov, pw, ph, unit, grid, shade), xy)

    # Legs
    leg_y = 20 * unit
    part(16, 48, 16, 32, 4, 12, front_shade, (cx - 4 * unit, leg_y))
    part(32, 48, 48, 48, 4, 12, front_shade, (cx, leg_y))

    # Body
    body_xy = (cx - 4 * unit, 8 * unit)
    part(20, 20, 20, 36, 8, 12, front_shade, body_xy)

    # Arms
    arm_y = 8 * unit
    left_arm = (36, 52, 52, 52)
    right_arm = (44, 20, 44, 36)
    if show_right:
        part(*right_arm, 4, 12, side_shade if side_amt > 0.25 else front_shade, (cx + 4 * unit, arm_y))
        part(*left_arm, 4, 12, front_shade, (cx - 8 * unit, arm_y))
    else:
        part(*left_arm, 4, 12, side_shade if side_amt > 0.25 else front_shade, (cx - 8 * unit, arm_y))
        part(*right_arm, 4, 12, front_shade, (cx + 4 * unit, arm_y))

    # Head
    head_xy = (cx - 4 * unit, 0)
    part(8, 8, 40, 8, 8, 8, front_shade, head_xy)
    if side_amt > 0.12:
        side = _face(tex, 16 if show_right else 0, 8, 8, 8, unit, grid, side_shade)
        squash = max(unit, int(round(4 * unit * side_amt)))
        side = side.resize((squash, 8 * unit), Image.Resampling.NEAREST)
        sx = (cx + 4 * unit - 2) if show_right else (cx - 4 * unit - squash + 2)
        blit(side, (sx, 0))

    if side_amt > 0.65:
        body_side = _face(tex, 16 if show_right else 28, 20, 4, 12, unit, grid, side_shade)
        body_side = body_side.resize((max(unit, int(2.5 * unit)), 12 * unit), Image.Resampling.NEAREST)
        bx = (cx + 4 * unit - unit) if show_right else (cx - 4 * unit - body_side.size[0] + unit)
        blit(body_side, (bx, 8 * unit))

    return canvas


class Skin3DViewer(tk.Canvas):
    """Drag horizontally to rotate the skin; auto-spins when idle."""

    def __init__(self, master, width: int = 220, height: int = 360, bg: str = "#221c12", **kwargs):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0, **kwargs)
        self._width = width
        self._height = height
        self._yaw = 28.0
        self._texture: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._drag_x: Optional[int] = None
        self._auto = True
        self._job = None
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda _e: setattr(self, "_auto", False))
        self.bind("<Leave>", lambda _e: setattr(self, "_auto", True))
        self.after(50, self._tick)

    def set_texture(self, path: Optional[Path]) -> None:
        if not path or not Path(path).exists():
            self._texture = None
            self.delete("all")
            self.create_text(self._width // 2, self._height // 2, text="Sem skin", fill="#b7a88a", font=("Segoe UI", 12))
            return
        try:
            self._texture = Image.open(path).convert("RGBA")
        except Exception:
            self._texture = None
            return
        self._redraw()

    def _redraw(self) -> None:
        if self._texture is None:
            return
        scale = max(5, min(self._width, self._height) // 42)
        frame = render_skin_frame(self._texture, yaw_deg=self._yaw, scale=scale)
        canvas_img = Image.new("RGBA", (self._width, self._height), (0, 0, 0, 0))
        x = (self._width - frame.size[0]) // 2
        y = (self._height - frame.size[1]) // 2 + unit_pad(scale)
        canvas_img.alpha_composite(frame, (x, max(0, y)))
        self._photo = ImageTk.PhotoImage(canvas_img)
        self.delete("all")
        self.create_image(self._width // 2, self._height // 2, image=self._photo)
        self.create_text(
            self._width // 2,
            self._height - 14,
            text="arraste para girar",
            fill="#6e5f45",
            font=("Segoe UI", 9),
        )

    def _on_press(self, event) -> None:
        self._drag_x = event.x
        self._auto = False

    def _on_drag(self, event) -> None:
        if self._drag_x is None:
            return
        self._yaw = (self._yaw + (event.x - self._drag_x) * 0.75) % 360
        self._drag_x = event.x
        self._redraw()

    def _on_release(self, _event) -> None:
        self._drag_x = None

    def _tick(self) -> None:
        if self._auto and self._texture is not None:
            self._yaw = (self._yaw + 0.9) % 360
            self._redraw()
        self._job = self.after(55, self._tick)

    def destroy(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        super().destroy()


def unit_pad(scale: int) -> int:
    return scale  # slight downward bias so feet aren't clipped
