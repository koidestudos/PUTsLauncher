from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Optional

from launcher.config import cache_dir


def skin_cache_path(uuid_or_name: str) -> Path:
    safe = "".join(c for c in uuid_or_name if c.isalnum() or c in "-_")[:64] or "player"
    return cache_dir() / f"skin_{safe}.png"


def fetch_skin_render(uuid: str = "", name: str = "", size: int = 256) -> Optional[Path]:
    """
    Download a 3D body render for the player.
    Tries UUID first (Microsoft), then name (offline/premium lookup).
    """
    uid = (uuid or "").replace("-", "").strip()
    urls: list[str] = []
    if uid:
        urls.extend(
            [
                f"https://mc-heads.net/body/{uid}/{size}",
                f"https://crafatar.com/renders/body/{uid}?overlay=true&scale=6",
                f"https://visage.surgeplay.com/full/{size}/{uid}",
            ]
        )
    if name:
        urls.extend(
            [
                f"https://mc-heads.net/body/{name}/{size}",
                f"https://mc-heads.net/body/{name}",
            ]
        )

    out = skin_cache_path(uid or name or "steve")
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PUTsLauncher/1.2"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            if len(data) < 200:
                continue
            out.write_bytes(data)
            return out
        except Exception:
            continue
    return out if out.exists() else None
