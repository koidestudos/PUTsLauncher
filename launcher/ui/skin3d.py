from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageTk

# Rotate around torso center so pitch/yaw don't orbit the feet
_CENTER_Y = 16.0


def _normalize_skin(texture: Image.Image) -> Image.Image:
    tex = texture.convert("RGBA")
    w, h = tex.size
    if h == 32 and w == 64:
        padded = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        padded.paste(tex, (0, 0))
        return padded
    if w >= 64 and w == h and w % 64 == 0 and w != 64:
        return tex.resize((64, 64), Image.Resampling.NEAREST)
    if w != 64 or h != 64:
        return tex.resize((64, 64), Image.Resampling.NEAREST)
    return tex


def _rot_y(x, y, z, c, s):
    return x * c + z * s, y, -x * s + z * c


def _rot_x(x, y, z, c, s):
    return x, y * c - z * s, y * s + z * c


def _uvs(u: int, v: int, w: int, h: int, d: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        "top": (u + d, v, w, d),
        "bottom": (u + w + d, v, w, d),
        "left": (u, v + d, d, h),
        "front": (u + d, v + d, w, h),
        "right": (u + w + d, v + d, d, h),
        "back": (u + w + d * 2, v + d, w, h),
    }


# Face UV corners TL→BL→BR→TR
_FACE_UV = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))


def _box_faces(cx, cy, cz, sx, sy, sz, u, v, w, h, d):
    """Cube faces, CCW from outside. UVs match vertex order."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    uv = _uvs(u, v, w, h, d)
    corners = {
        "front": [  # +Z
            (cx - hx, cy + hy, cz + hz),
            (cx - hx, cy - hy, cz + hz),
            (cx + hx, cy - hy, cz + hz),
            (cx + hx, cy + hy, cz + hz),
        ],
        "back": [  # -Z
            (cx + hx, cy + hy, cz - hz),
            (cx + hx, cy - hy, cz - hz),
            (cx - hx, cy - hy, cz - hz),
            (cx - hx, cy + hy, cz - hz),
        ],
        "right": [  # +X
            (cx + hx, cy + hy, cz + hz),
            (cx + hx, cy - hy, cz + hz),
            (cx + hx, cy - hy, cz - hz),
            (cx + hx, cy + hy, cz - hz),
        ],
        "left": [  # -X
            (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy - hy, cz - hz),
            (cx - hx, cy - hy, cz + hz),
            (cx - hx, cy + hy, cz + hz),
        ],
        "top": [  # +Y, CCW from outside (above)
            (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy + hy, cz + hz),
            (cx + hx, cy + hy, cz + hz),
            (cx + hx, cy + hy, cz - hz),
        ],
        "bottom": [  # -Y, CCW from outside (below)
            (cx - hx, cy - hy, cz + hz),
            (cx - hx, cy - hy, cz - hz),
            (cx + hx, cy - hy, cz - hz),
            (cx + hx, cy - hy, cz + hz),
        ],
    }
    shade = {"front": 1.0, "back": 0.58, "left": 0.8, "right": 0.8, "top": 1.08, "bottom": 0.45}
    # top verts backL,frontL,frontR,backR → v=0 at front (Minecraft)
    face_uv = {
        "front": _FACE_UV,
        "back": _FACE_UV,
        "left": _FACE_UV,
        "right": _FACE_UV,
        "top": ((0.0, 1.0), (0.0, 0.0), (1.0, 0.0), (1.0, 1.0)),
        "bottom": ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)),
    }
    return [(corners[n], uv[n], shade[n], face_uv[n]) for n in corners]


def _model_faces(slim: bool = False):
    """Steve (classic) or Alex (slim) proportions in Minecraft pixel units."""
    arm_w = 3 if slim else 4
    arm_uv_w = 3 if slim else 4
    return (
        _box_faces(-2, 6, 0, 4, 12, 4, 0, 16, 4, 12, 4)  # right leg
        + _box_faces(2, 6, 0, 4, 12, 4, 16, 48, 4, 12, 4)  # left leg
        + _box_faces(0, 18, 0, 8, 12, 4, 16, 16, 8, 12, 4)  # body
        + _box_faces(-(4 + arm_w / 2), 18, 0, arm_w, 12, 4, 40, 16, arm_uv_w, 12, 4)  # right arm
        + _box_faces(4 + arm_w / 2, 18, 0, arm_w, 12, 4, 32, 48, arm_uv_w, 12, 4)  # left arm
        + _box_faces(0, 28, 0, 8, 8, 8, 0, 0, 8, 8, 8)  # head
        + _box_faces(0, 28, 0, 8.5, 8.5, 8.5, 32, 0, 8, 8, 8)  # hat
    )


def _raster_tri(zbuf, color, p0, p1, p2, uv0, uv1, uv2, tex, shade: float) -> None:
    h, w = zbuf.shape
    minx = max(0, int(math.floor(min(p0[0], p1[0], p2[0]))))
    maxx = min(w - 1, int(math.ceil(max(p0[0], p1[0], p2[0]))))
    miny = max(0, int(math.floor(min(p0[1], p1[1], p2[1]))))
    maxy = min(h - 1, int(math.ceil(max(p0[1], p1[1], p2[1]))))
    if minx > maxx or miny > maxy:
        return

    area = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
    if abs(area) < 1e-6:
        return

    xs = np.arange(minx, maxx + 1, dtype=np.float32) + 0.5
    ys = np.arange(miny, maxy + 1, dtype=np.float32) + 0.5
    xx, yy = np.meshgrid(xs, ys, indexing="xy")

    def edge(ax, ay, bx, by, px, py):
        return (bx - ax) * (py - ay) - (by - ay) * (px - ax)

    w0 = edge(p1[0], p1[1], p2[0], p2[1], xx, yy) / area
    w1 = edge(p2[0], p2[1], p0[0], p0[1], xx, yy) / area
    w2 = edge(p0[0], p0[1], p1[0], p1[1], xx, yy) / area
    mask = (w0 >= -1e-5) & (w1 >= -1e-5) & (w2 >= -1e-5)
    if not np.any(mask):
        return

    z = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
    zview = zbuf[miny : maxy + 1, minx : maxx + 1]
    cview = color[miny : maxy + 1, minx : maxx + 1]
    closer = mask & (z >= zview)
    if not np.any(closer):
        return

    th, tw = tex.shape[:2]
    u = w0 * uv0[0] + w1 * uv1[0] + w2 * uv2[0]
    v = w0 * uv0[1] + w1 * uv1[1] + w2 * uv2[1]
    sx = np.clip((u * (tw - 1)).astype(np.int32), 0, tw - 1)
    sy = np.clip((v * (th - 1)).astype(np.int32), 0, th - 1)
    sampled = tex[sy, sx]
    visible = closer & (sampled[..., 3] > 8)
    if not np.any(visible):
        return

    rgb = np.clip(sampled[..., :3].astype(np.float32) * shade, 0, 255).astype(np.uint8)
    out = np.empty(sampled.shape, dtype=np.uint8)
    out[..., :3] = rgb
    out[..., 3] = 255
    zview[visible] = z[visible]
    cview[visible] = out[visible]


def _prep_faces(tex: np.ndarray, slim: bool = False) -> list[tuple]:
    prepared = []
    for verts, (u0, v0, uw, vh), shade, face_uv in _model_faces(slim=slim):
        face = tex[v0 : v0 + vh, u0 : u0 + uw]
        if face.size == 0:
            continue
        # Center verts once so every frame only rotates
        centered = [(x, y - _CENTER_Y, z) for x, y, z in verts]
        prepared.append((centered, face, shade, face_uv))
    return prepared


def render_skin_frame(
    faces: list[tuple],
    yaw_deg: float = 35.0,
    pitch_deg: float = -12.0,
    out_w: int = 280,
    out_h: int = 420,
    max_pix: int = 55_000,
) -> Image.Image:
    aspect = out_w / max(out_h, 1)
    H = int(math.sqrt(max_pix / max(aspect, 0.2)))
    W = max(48, int(H * aspect))
    H = max(72, H)
    if W * H > max_pix:
        f = math.sqrt(max_pix / (W * H))
        W = max(48, int(W * f))
        H = max(72, int(H * f))

    # Fixed orthographic scale — room for full 360° tumble without stretch/refit
    scale = min(W, H) * 0.50 / 16.0
    zbuf = np.full((H, W), -1e9, dtype=np.float32)
    color = np.zeros((H, W, 4), dtype=np.uint8)

    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cx_s = W * 0.5
    cy_s = H * 0.52

    for verts, face, shade, face_uv in faces:
        tv = []
        for x, y, z in verts:
            x, y, z = _rot_y(x, y, z, cy, sy)
            x, y, z = _rot_x(x, y, z, cp, sp)
            tv.append((x, y, z))

        ax, ay, az = tv[1][0] - tv[0][0], tv[1][1] - tv[0][1], tv[1][2] - tv[0][2]
        bx, by, bz = tv[3][0] - tv[0][0], tv[3][1] - tv[0][1], tv[3][2] - tv[0][2]
        nx = ay * bz - az * by
        nz = ax * by - ay * bx
        ln = math.sqrt(nx * nx + (az * bx - ax * bz) ** 2 + nz * nz) or 1.0
        nz /= ln
        if nz <= 0.01:
            continue

        pts = [(cx_s + p[0] * scale, cy_s - p[1] * scale, p[2]) for p in tv]
        lit = shade * (0.55 + 0.45 * max(0.0, nz))
        u0, u1, u2, u3 = face_uv
        _raster_tri(zbuf, color, pts[0], pts[1], pts[2], u0, u1, u2, face, lit)
        _raster_tri(zbuf, color, pts[0], pts[2], pts[3], u0, u2, u3, face, lit)

    frame = Image.fromarray(color, "RGBA")
    return frame.resize((max(1, out_w), max(1, out_h)), Image.Resampling.NEAREST)


class Skin3DViewer(tk.Canvas):
    def __init__(self, master, width: int = 320, height: int = 480, bg: str = "#221c12", **kwargs):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0, **kwargs)
        self._width = width
        self._height = height
        self._yaw = 28.0
        self._pitch = -12.0
        self._slim = False
        self._tex: Optional[np.ndarray] = None
        self._faces: list[tuple] = []
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._drag_x: Optional[int] = None
        self._drag_y: Optional[int] = None
        self._auto = True
        self._job = None
        self._drawing = False
        self._dirty = False
        self._dragging = False
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda _e: setattr(self, "_auto", False))
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", self._on_configure)
        self.after(120, self._tick)

    def _on_leave(self, _event) -> None:
        if not self._dragging:
            self._auto = True

    def _on_configure(self, event) -> None:
        w, h = max(80, event.width), max(120, event.height)
        if abs(w - self._width) > 4 or abs(h - self._height) > 4:
            self._width, self._height = w, h
            self._dirty = True

    def _rebuild_faces(self) -> None:
        if self._tex is None:
            self._faces = []
            return
        self._faces = _prep_faces(self._tex, slim=self._slim)

    def set_slim(self, slim: bool) -> None:
        slim = bool(slim)
        if slim == self._slim:
            return
        self._slim = slim
        self._rebuild_faces()
        self._redraw(force_hq=True)

    def set_texture(self, path: Optional[Path], slim: Optional[bool] = None) -> None:
        if slim is not None:
            self._slim = bool(slim)
        if not path or not Path(path).exists():
            self._tex = None
            self._faces = []
            self.delete("all")
            self.create_text(self._width // 2, self._height // 2, text="Sem skin", fill="#b7a88a", font=("Segoe UI", 12))
            return
        try:
            self._tex = np.asarray(_normalize_skin(Image.open(path)), dtype=np.uint8)
            self._rebuild_faces()
        except Exception:
            self._tex = None
            self._faces = []
            return
        self._redraw(force_hq=True)

    def set_texture_image(self, image: Image.Image, slim: Optional[bool] = None) -> None:
        if slim is not None:
            self._slim = bool(slim)
        try:
            self._tex = np.asarray(_normalize_skin(image), dtype=np.uint8)
            self._rebuild_faces()
        except Exception:
            self._tex = None
            self._faces = []
            return
        self._redraw(force_hq=True)

    def _redraw(self, force_hq: bool = False) -> None:
        if not self._faces or self._drawing:
            if self._faces:
                self._dirty = True
            return
        self._drawing = True
        self._dirty = False
        try:
            max_pix = 90_000 if force_hq else (28_000 if self._dragging else 40_000)
            frame = render_skin_frame(
                self._faces,
                yaw_deg=self._yaw,
                pitch_deg=self._pitch,
                out_w=self._width,
                out_h=self._height,
                max_pix=max_pix,
            )
            self._photo = ImageTk.PhotoImage(frame)
            self.delete("all")
            self.create_image(0, 0, image=self._photo, anchor="nw")
            self.create_text(
                self._width // 2,
                self._height - 14,
                text="arraste para girar",
                fill="#6e5f45",
                font=("Segoe UI", 9),
            )
        finally:
            self._drawing = False

    def _on_press(self, event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y
        self._dragging = True
        self._auto = False

    def _on_drag(self, event) -> None:
        if self._drag_x is None or self._drag_y is None:
            return
        self._yaw = (self._yaw + (event.x - self._drag_x) * 0.85) % 360
        # Full 360° vertical tumble (same freedom as horizontal)
        self._pitch = (self._pitch + (event.y - self._drag_y) * 0.85) % 360
        self._drag_x = event.x
        self._drag_y = event.y
        self._dirty = True
        if not self._drawing:
            self._redraw()

    def _on_release(self, _event) -> None:
        self._drag_x = None
        self._drag_y = None
        was = self._dragging
        self._dragging = False
        if was:
            self._redraw(force_hq=True)

    def _tick(self) -> None:
        if self._dirty and self._faces and not self._drawing:
            self._redraw()
        elif self._auto and self._faces and not self._drawing and not self._dragging:
            self._yaw = (self._yaw + 3.0) % 360
            self._redraw()
        self._job = self.after(180, self._tick)

    def destroy(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        super().destroy()
