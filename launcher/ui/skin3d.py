from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageTk


@dataclass
class Face:
    verts: list[tuple[float, float, float]]
    uv: tuple[int, int, int, int]  # u0,v0,uw,vh
    shade: float = 1.0


def _normalize_skin(texture: Image.Image) -> tuple[Image.Image, int]:
    tex = texture.convert("RGBA")
    w, h = tex.size
    if h == 32 and w == 64:
        padded = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        padded.paste(tex, (0, 0))
        tex = padded
    grid = max(1, tex.size[0] // 64)
    return tex, grid


def _rot_y(p: tuple[float, float, float], a: float) -> tuple[float, float, float]:
    x, y, z = p
    c, s = math.cos(a), math.sin(a)
    return (x * c + z * s, y, -x * s + z * c)


def _rot_x(p: tuple[float, float, float], a: float) -> tuple[float, float, float]:
    x, y, z = p
    c, s = math.cos(a), math.sin(a)
    return (x, y * c - z * s, y * s + z * c)


def _box(
    cx: float,
    cy: float,
    cz: float,
    sx: float,
    sy: float,
    sz: float,
    uvs: dict[str, tuple[int, int, int, int]],
) -> list[Face]:
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    corners = {
        "front": [  # +Z
            (cx - hx, cy + hy, cz + hz),
            (cx + hx, cy + hy, cz + hz),
            (cx + hx, cy - hy, cz + hz),
            (cx - hx, cy - hy, cz + hz),
        ],
        "back": [  # -Z
            (cx + hx, cy + hy, cz - hz),
            (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy - hy, cz - hz),
            (cx + hx, cy - hy, cz - hz),
        ],
        "right": [  # +X
            (cx + hx, cy + hy, cz + hz),
            (cx + hx, cy + hy, cz - hz),
            (cx + hx, cy - hy, cz - hz),
            (cx + hx, cy - hy, cz + hz),
        ],
        "left": [  # -X
            (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy + hy, cz + hz),
            (cx - hx, cy - hy, cz + hz),
            (cx - hx, cy - hy, cz - hz),
        ],
        "top": [
            (cx - hx, cy + hy, cz - hz),
            (cx + hx, cy + hy, cz - hz),
            (cx + hx, cy + hy, cz + hz),
            (cx - hx, cy + hy, cz + hz),
        ],
        "bottom": [
            (cx - hx, cy - hy, cz + hz),
            (cx + hx, cy - hy, cz + hz),
            (cx + hx, cy - hy, cz - hz),
            (cx - hx, cy - hy, cz - hz),
        ],
    }
    shades = {"front": 1.0, "back": 0.5, "left": 0.75, "right": 0.75, "top": 1.08, "bottom": 0.4}
    return [Face(corners[name], uvs[name], shades[name]) for name in uvs]


def _steve_model() -> list[Face]:
    """Feet at y=0. UVs follow skinview3d / Mojang 64×64 layout."""
    faces: list[Face] = []
    # Right leg — setSkinUVs(0,16,4,12,4); player's right = -X
    faces += _box(-2, 6, 0, 4, 12, 4, {
        "top": (4, 16, 4, 4), "bottom": (8, 16, 4, 4),
        "left": (0, 20, 4, 12), "front": (4, 20, 4, 12),
        "right": (8, 20, 4, 12), "back": (12, 20, 4, 12),
    })
    # Left leg — setSkinUVs(16,48,4,12,4)
    faces += _box(2, 6, 0, 4, 12, 4, {
        "top": (20, 48, 4, 4), "bottom": (24, 48, 4, 4),
        "left": (16, 52, 4, 12), "front": (20, 52, 4, 12),
        "right": (24, 52, 4, 12), "back": (28, 52, 4, 12),
    })
    # Body — setSkinUVs(16,16,8,12,4)
    faces += _box(0, 18, 0, 8, 12, 4, {
        "top": (20, 16, 8, 4), "bottom": (28, 16, 8, 4),
        "left": (16, 20, 4, 12), "front": (20, 20, 8, 12),
        "right": (28, 20, 4, 12), "back": (32, 20, 8, 12),
    })
    # Right arm — setSkinUVs(40,16,4,12,4)
    faces += _box(-6, 18, 0, 4, 12, 4, {
        "top": (44, 16, 4, 4), "bottom": (48, 16, 4, 4),
        "left": (40, 20, 4, 12), "front": (44, 20, 4, 12),
        "right": (48, 20, 4, 12), "back": (52, 20, 4, 12),
    })
    # Left arm — setSkinUVs(32,48,4,12,4)
    faces += _box(6, 18, 0, 4, 12, 4, {
        "top": (36, 48, 4, 4), "bottom": (40, 48, 4, 4),
        "left": (32, 52, 4, 12), "front": (36, 52, 4, 12),
        "right": (40, 52, 4, 12), "back": (44, 52, 4, 12),
    })
    # Head — setSkinUVs(0,0,8,8,8)
    faces += _box(0, 28, 0, 8, 8, 8, {
        "top": (8, 0, 8, 8), "bottom": (16, 0, 8, 8),
        "left": (0, 8, 8, 8), "front": (8, 8, 8, 8),
        "right": (16, 8, 8, 8), "back": (24, 8, 8, 8),
    })
    # Hat — setSkinUVs(32,0,8,8,8)
    faces += _box(0, 28, 0, 8.7, 8.7, 8.7, {
        "top": (40, 0, 8, 8), "bottom": (48, 0, 8, 8),
        "left": (32, 8, 8, 8), "front": (40, 8, 8, 8),
        "right": (48, 8, 8, 8), "back": (56, 8, 8, 8),
    })
    return faces


def _normal(vs: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    ax, ay, az = vs[1][0] - vs[0][0], vs[1][1] - vs[0][1], vs[1][2] - vs[0][2]
    bx, by, bz = vs[3][0] - vs[0][0], vs[3][1] - vs[0][1], vs[3][2] - vs[0][2]
    nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
    L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / L, ny / L, nz / L


def _edge(a, b, p) -> float:
    return (p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])


def _draw_textured_quad(
    dest: Image.Image,
    face_img: Image.Image,
    quad: list[tuple[float, float]],
) -> None:
    """Rasterize a textured quad (TL,TR,BR,BL) with bilinear UV sampling."""
    xs = [q[0] for q in quad]
    ys = [q[1] for q in quad]
    minx, maxx = int(math.floor(min(xs))), int(math.ceil(max(xs)))
    miny, maxy = int(math.floor(min(ys))), int(math.ceil(max(ys)))
    minx = max(0, minx)
    miny = max(0, miny)
    maxx = min(dest.size[0], maxx)
    maxy = min(dest.size[1], maxy)
    if maxx <= minx or maxy <= miny:
        return

    fw, fh = face_img.size
    # Split into two triangles: TL-TR-BR and TL-BR-BL
    tris = (
        (quad[0], quad[1], quad[2], (0.0, 0.0), (1.0, 0.0), (1.0, 1.0)),
        (quad[0], quad[2], quad[3], (0.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
    )
    dp = dest.load()
    fp = face_img.load()

    for (a, b, c, uva, uvb, uvc) in tris:
        area = _edge(a, b, c)
        if abs(area) < 1e-6:
            continue
        for y in range(miny, maxy):
            for x in range(minx, maxx):
                p = (x + 0.5, y + 0.5)
                w0 = _edge(b, c, p) / area
                w1 = _edge(c, a, p) / area
                w2 = _edge(a, b, p) / area
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                u = w0 * uva[0] + w1 * uvb[0] + w2 * uvc[0]
                v = w0 * uva[1] + w1 * uvb[1] + w2 * uvc[1]
                sx = min(fw - 1, max(0, int(u * (fw - 1))))
                sy = min(fh - 1, max(0, int(v * (fh - 1))))
                pr, pg, pb, pa = fp[sx, sy]
                if pa < 8:
                    continue
                if pa >= 250:
                    dp[x, y] = (pr, pg, pb, 255)
                else:
                    dr, dg, db, da = dp[x, y]
                    inv = 1 - pa / 255
                    dp[x, y] = (
                        int(pr * pa / 255 + dr * inv),
                        int(pg * pa / 255 + dg * inv),
                        int(pb * pa / 255 + db * inv),
                        min(255, da + pa),
                    )


def render_skin_frame(texture: Image.Image, yaw_deg: float = 35.0, scale: int = 11) -> Image.Image:
    tex, grid = _normalize_skin(texture)
    yaw = math.radians(yaw_deg)
    pitch = math.radians(-14)

    prepared = []
    for face in _steve_model():
        tv = [_rot_x(_rot_y(v, yaw), pitch) for v in face.verts]
        n = _normal(tv)
        # After rotations, camera looks along -Z; faces with +Z normal point at camera
        if n[2] <= 0.05:
            continue
        depth = sum(p[2] for p in tv) / 4.0
        shade = face.shade * (0.5 + 0.5 * n[2])
        prepared.append((depth, tv, face.uv, shade))
    prepared.sort(key=lambda t: t[0])  # far → near

    W = int(22 * scale)
    H = int(36 * scale)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cx = W / 2
    cy = H - 2 * scale

    for _d, tv, uv, shade in prepared:
        u0, v0, uw, vh = uv
        face_img = tex.crop((u0 * grid, v0 * grid, (u0 + uw) * grid, (v0 + vh) * grid))
        # Upscale a bit for sharper NEAREST look when sampling
        face_img = face_img.resize((uw * 8, vh * 8), Image.Resampling.NEAREST)
        if abs(shade - 1.0) > 0.02:
            face_img = ImageEnhance.Brightness(face_img).enhance(max(0.25, min(1.25, shade)))
        quad = [(cx + p[0] * scale, cy - p[1] * scale) for p in tv]
        _draw_textured_quad(canvas, face_img, quad)

    return canvas


class Skin3DViewer(tk.Canvas):
    def __init__(self, master, width: int = 280, height: int = 440, bg: str = "#221c12", **kwargs):
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
        self.after(40, self._tick)

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
        scale = max(8, int(self._height * 0.82 / 36))
        frame = render_skin_frame(self._texture, yaw_deg=self._yaw, scale=scale)
        canvas_img = Image.new("RGBA", (self._width, self._height), (0, 0, 0, 0))
        x = (self._width - frame.size[0]) // 2
        y = (self._height - frame.size[1]) // 2
        canvas_img.alpha_composite(frame, (max(0, x), max(0, y)))
        self._photo = ImageTk.PhotoImage(canvas_img)
        self.delete("all")
        self.create_image(self._width // 2, self._height // 2, image=self._photo)
        self.create_text(
            self._width // 2, self._height - 14, text="arraste para girar", fill="#6e5f45", font=("Segoe UI", 9)
        )

    def _on_press(self, event) -> None:
        self._drag_x = event.x
        self._auto = False

    def _on_drag(self, event) -> None:
        if self._drag_x is None:
            return
        self._yaw = (self._yaw + (event.x - self._drag_x) * 0.9) % 360
        self._drag_x = event.x
        self._redraw()

    def _on_release(self, _event) -> None:
        self._drag_x = None

    def _tick(self) -> None:
        if self._auto and self._texture is not None:
            self._yaw = (self._yaw + 2.0) % 360
            self._redraw()
        self._job = self.after(70, self._tick)

    def destroy(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        super().destroy()
