from __future__ import annotations

import sys
from pathlib import Path

from launcher.config import asset_path

# Maracujá / dusk tropical — gold pulp on deep cocoa, not generic purple-AI.
COLORS = {
    "bg0": "#0a0907",
    "bg1": "#12100c",
    "bg2": "#1a160f",
    "panel": "#221c12",
    "panel_soft": "#2a2216",
    "stroke": "#4a3c22",
    "text": "#f7f1dc",
    "muted": "#b7a88a",
    "accent": "#f0d24a",
    "accent_hot": "#ffb81c",
    "accent_dim": "#c49a28",
    "accent_text": "#1a1406",
    "berry": "#8b3a2a",
    "cream": "#fff6d6",
    "danger": "#e07060",
    "ok": "#8bcf7a",
    "input_bg": "#100e0a",
    "input_border": "#5a4a28",
    "ms_blue": "#2f2f2f",
    "disabled": "#3a3428",
    "disabled_text": "#7a6e55",
}

FONTS = {
    "display": ("Georgia", 48, "bold"),
    "title": ("Georgia", 22, "bold"),
    "body": ("Segoe UI", 14),
    "body_bold": ("Segoe UI", 14, "bold"),
    "small": ("Segoe UI", 12),
    "tiny": ("Segoe UI", 11),
    "button": ("Georgia", 18, "bold"),
}


ACCENTED_SAMPLE = "çãõéíáêóú"


def font_covers(font_path: Path, sample: str = ACCENTED_SAMPLE) -> bool:
    """
    True when the TTF has a glyph for every character in ``sample``.

    The brand font only carries ASCII, so titles like “Opções” would render with
    missing glyphs — we check the cmap instead of guessing.
    """
    try:
        from fontTools.ttLib import TTFont  # type: ignore

        with TTFont(str(font_path), fontNumber=0, lazy=True) as tt:
            cmap = tt.getBestCmap()
        return all(ord(ch) in cmap for ch in sample)
    except Exception:
        pass
    try:
        from PIL import ImageFont

        pil = ImageFont.truetype(str(font_path), 24)
        notdef = pil.getmask("￾").getbbox()
        for ch in sample:
            if pil.getmask(ch).getbbox() == notdef:
                return False
        return True
    except Exception:
        return False


def register_fonts(root=None) -> str:
    """Register Merchant Copy Doublesize for brand titles. Returns family name used."""
    global FONTS
    font_path = asset_path("MerchantCopy.ttf")
    family = "Georgia"
    if not font_path.exists():
        return family

    if sys.platform == "win32":
        try:
            import ctypes

            # FR_PRIVATE = 0x10 — load for this process only
            ctypes.windll.gdi32.AddFontResourceExW(str(font_path), 0x10, 0)
            family = "Merchant Copy Doublesize"
        except Exception:
            family = "Georgia"
    else:
        try:
            if root is not None:
                name = "PUTsMerchant"
                try:
                    root.tk.call("font", "delete", name)
                except Exception:
                    pass
                root.tk.call("font", "create", name, "-file", str(font_path))
                family = name
            else:
                family = "Georgia"
        except Exception:
            family = "Georgia"

    # "PUTs" is ASCII, so the brand font is always safe for the wordmark.
    FONTS["display"] = (family, 48, "bold")
    # Titles carry Portuguese text ("Opções", "Instância"): only use the brand
    # font when it actually has those glyphs, otherwise stay on Georgia.
    if family != "Georgia" and font_covers(font_path):
        FONTS["title"] = (family, 24, "bold")
    else:
        FONTS["title"] = ("Georgia", 22, "bold")
    return family
