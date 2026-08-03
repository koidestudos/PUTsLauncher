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

    FONTS["display"] = (family, 48, "bold")
    FONTS["title"] = (family, 24, "bold")
    return family
