from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageTk


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


def _box_faces(cx, cy, cz, sx, sy, sz, u, v, w, h, d):
    """Cube faces with CCW winding when viewed from outside (outward normals)."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    uv = _uvs(u, v, w, h, d)
    # Each face: TL, BL, BR, TR from the outside view → two tris share correct UV
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
        "top": [  # +Y
            (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy + hy, cz + hz),
            (cx + hx, cy + hy, cz + hz),
            (cx + hx, cy + hy, cz - hz),
        ],
        "bottom": [  # -Y
            (cx - hx, cy - hy, cz + hz),
            (cx - hx, cy - hy, cz - hz),
            (cx + hx, cy - hy, cz - hz),
            (cx + hx, cy - hy, cz + hz),
        ],
    }
    shade = {"front": 1.0, "back": 0.55, "left": 0.78, "right": 0.78, "top": 1.1, "bottom": 0.42}
    return [(corners[n], uv[n], shade[n]) for n in corners]


def _model_faces():
    # Minecraft pixel units; origin at feet, +Y up, +Z forward
    return (
        _box_faces(-2, 6, 0, 4, 12, 4, 0, 16, 4, 12, 4)  # right leg
        + _box_faces(2, 6, 0, 4, 12, 4, 16, 48, 4, 12, 4)  # left leg
        + _box_faces(0, 18, 0, 8, 12, 4, 16, 16, 8, 12, 4)  # body
        + _box_faces(-6, 18, 0, 4, 12, 4, 40, 16, 4, 12, 4)  # right arm
        + _box_faces(6, 18, 0, 4, 12, 4, 32, 48, 4, 12, 4)  # left arm
        + _box_faces(0, 28, 0, 8, 8, 8, 0, 0, 8, 8, 8)  # head
        + _box_faces(0, 28, 0, 8.75, 8.75, 8.75, 32, 0, 8, 8, 8)  # hat
    )


def _raster_tri(
    zbuf: np.ndarray,
    color: np.ndarray,
    p0,
    p1,
    p2,
    uv0,
    uv1,
    uv2,
    tex: np.ndarray,
    shade: float,
) -> None:
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
    xx, yy = np.meshgrid(xs, ys)

    def edge(ax, ay, bx, by, px, py):
        # (B-A) × (P-A) — must match area = (p1-p0)×(p2-p0)
        return (bx - ax) * (py - ay) - (by - ay) * (px - ax)

    w0 = edge(p1[0], p1[1], p2[0], p2[1], xx, yy) / area
    w1 = edge(p2[0], p2[1], p0[0], p0[1], xx, yy) / area
    w2 = edge(p0[0], p0[1], p1[0], p1[1], xx, yy) / area
    mask = (w0 >= -1e-5) & (w1 >= -1e-5) & (w2 >= -1e-5)
    if not np.any(mask):
        return

    # Larger camera-Z = closer to camera
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


# Face UV corners matching TL, BL, BR, TR vertex order
_FACE_UV = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))


def render_skin_frame(
    texture: Image.Image,
    yaw_deg: float = 35.0,
    out_w: int = 280,
    out_h: int = 420,
) -> Image.Image:
    tex_img = _normalize_skin(texture)
    tex = np.asarray(tex_img, dtype=np.uint8)

    # Render at panel size (cap pixels for FPS), then nearest-upscale to fill.
    max_pix = 120_000
    aspect = out_w / max(out_h, 1)
    H = int(math.sqrt(max_pix / max(aspect, 0.2)))
    W = max(64, int(H * aspect))
    H = max(96, H)
    if W * H > max_pix:
        f = math.sqrt(max_pix / (W * H))
        W = max(64, int(W * f))
        H = max(96, int(H * f))

    # Model spans ~16 wide × 32 tall; fill ~92% of the render buffer
    scale = min((W * 0.92) / 16.0, (H * 0.92) / 32.0)
    zbuf = np.full((H, W), -1e9, dtype=np.float32)
    color = np.zeros((H, W, 4), dtype=np.uint8)

    yaw = math.radians(yaw_deg)
    pitch = math.radians(-10)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cx_s = W * 0.5
    cy_s = H * 0.5 + scale * 16.0  # center body in frame

    for verts, (u0, v0, uw, vh), shade in _model_faces():
        tv = []
        for x, y, z in verts:
            x, y, z = _rot_y(x, y, z, cy, sy)
            x, y, z = _rot_x(x, y, z, cp, sp)
            tv.append((x, y, z))

        ax, ay, az = tv[1][0] - tv[0][0], tv[1][1] - tv[0][1], tv[1][2] - tv[0][2]
        bx, by, bz = tv[3][0] - tv[0][0], tv[3][1] - tv[0][1], tv[3][2] - tv[0][2]
        nx = ay * bz - az * by
        ny = az * bx - ax * bz
        nz = ax * by - ay * bx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nz /= ln
        # Camera looks toward -Z in view space after projection using +z as depth;
        # faces with nz <= 0 point away from camera.
        if nz <= 0.01:
            continue

        pts = [(cx_s + p[0] * scale, cy_s - p[1] * scale, p[2]) for p in tv]
        face = tex[v0 : v0 + vh, u0 : u0 + uw]
        if face.size == 0:
            continue
        # Upsample face texels once for smoother sampling without per-pixel cost blowup
        face_up = np.asarray(
            Image.fromarray(face, "RGBA").resize((max(uw * 3, 3), max(vh * 3, 3)), Image.Resampling.NEAREST),
            dtype=np.uint8,
        )
        lit = shade * (0.55 + 0.45 * max(0.0, nz))
        uvs = _FACE_UV
        # tris: TL-BL-BR and TL-BR-TR
        _raster_tri(zbuf, color, pts[0], pts[1], pts[2], uvs[0], uvs[1], uvs[2], face_up, lit)
        _raster_tri(zbuf, color, pts[0], pts[2], pts[3], uvs[0], uvs[2], uvs[3], face_up, lit)

    frame = Image.fromarray(color, "RGBA")
    # Crop transparent margins so the figure fills the panel
    alpha = color[..., 3]
    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return Image.new("RGBA", (max(1, out_w), max(1, out_h)), (0, 0, 0, 0))
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    pad = 2
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(W, x1 + pad), min(H, y1 + pad)
    cropped = frame.crop((x0, y0, x1, y1))
    out = Image.new("RGBA", (max(1, out_w), max(1, out_h)), (0, 0, 0, 0))
    fit = min(out_w / cropped.size[0], out_h / cropped.size[1]) * 0.96
    nw = max(1, int(cropped.size[0] * fit))
    nh = max(1, int(cropped.size[1] * fit))
    cropped = cropped.resize((nw, nh), Image.Resampling.NEAREST)
    out.alpha_composite(cropped, ((out_w - nw) // 2, (out_h - nh) // 2))
    return out


class Skin3DViewer(tk.Canvas):
    def __init__(self, master, width: int = 320, height: int = 480, bg: str = "#221c12", **kwargs):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0, **kwargs)
        self._width = width
        self._height = height
        self._yaw = 28.0
        self._texture: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._drag_x: Optional[int] = None
        self._auto = True
        self._job = None
        self._busy = False
        self._dirty = False
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda _e: setattr(self, "_auto", False))
        self.bind("<Leave>", lambda _e: setattr(self, "_auto", True))
        self.bind("<Configure>", self._on_configure)
        self.after(80, self._tick)

    def _on_configure(self, event) -> None:
        w, h = max(80, event.width), max(120, event.height)
        if abs(w - self._width) > 2 or abs(h - self._height) > 2:
            self._width, self._height = w, h
            self._dirty = True
            if self._texture is not None and not self._busy:
                self.after_idle(self._redraw)

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
        if self._texture is None or self._busy:
            return
        self._busy = True
        self._dirty = False
        try:
            frame = render_skin_frame(self._texture, yaw_deg=self._yaw, out_w=self._width, out_h=self._height)
            self._photo = ImageTk.PhotoImage(frame)
            self.delete("all")
            self.create_image(0, 0, image=self._photo, anchor="nw")
            self.create_text(
                self._width // 2, self._height - 14, text="arraste para girar", fill="#6e5f45", font=("Segoe UI", 9)
            )
        finally:
            self._busy = False

    def _on_press(self, event) -> None:
        self._drag_x = event.x
        self._auto = False

    def _on_drag(self, event) -> None:
        if self._drag_x is None:
            return
        self._yaw = (self._yaw + (event.x - self._drag_x) * 0.9) % 360
        self._drag_x = event.x
        if not self._busy:
            self._redraw()
        else:
            self._dirty = True

    def _on_release(self, _event) -> None:
        self._drag_x = None

    def _tick(self) -> None:
        if self._dirty and self._texture is not None and not self._busy:
            self._redraw()
        elif self._auto and self._texture is not None and not self._busy:
            self._yaw = (self._yaw + 4.0) % 360
            self._redraw()
        self._job = self.after(140, self._tick)

    def destroy(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        super().destroy()
