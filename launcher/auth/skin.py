from __future__ import annotations

import math
import urllib.request
from pathlib import Path
from typing import Optional

import requests

from launcher.config import cache_dir


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_")[:64] or "player"


def texture_cache_path(key: str) -> Path:
    return cache_dir() / f"texture_{_safe(key)}.png"


def head_cache_path(key: str) -> Path:
    return cache_dir() / f"head_{_safe(key)}.png"


def fetch_skin_texture(uuid: str = "", name: str = "") -> Optional[Path]:
    """Download the raw 64x64 (or 64x64 HD) skin texture PNG."""
    uid = (uuid or "").replace("-", "").strip()
    urls: list[str] = []
    if uid:
        urls.extend(
            [
                f"https://mc-heads.net/skin/{uid}",
                f"https://crafatar.com/skins/{uid}",
                f"https://sessionserver.mojang.com/session/minecraft/profile/{uid}",
            ]
        )
    if name:
        urls.extend(
            [
                f"https://mc-heads.net/skin/{name}",
                f"https://minotar.net/skin/{name}",
            ]
        )

    out = texture_cache_path(uid or name or "steve")
    for url in urls:
        try:
            if "sessionserver.mojang.com" in url:
                # Resolve texture URL from profile JSON
                req = urllib.request.Request(url, headers={"User-Agent": "PUTsLauncher/1.3"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    import base64
                    import json

                    profile = json.loads(resp.read().decode("utf-8"))
                for prop in profile.get("properties") or []:
                    if prop.get("name") == "textures":
                        raw = json.loads(base64.b64decode(prop["value"]).decode("utf-8"))
                        skin_url = raw.get("textures", {}).get("SKIN", {}).get("url")
                        if skin_url:
                            url = skin_url
                            break
                else:
                    continue
            req = urllib.request.Request(url, headers={"User-Agent": "PUTsLauncher/1.3"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            if len(data) < 200:
                continue
            out.write_bytes(data)
            return out
        except Exception:
            continue
    return out if out.exists() else None


def fetch_head_avatar(uuid: str = "", name: str = "", size: int = 64) -> Optional[Path]:
    uid = (uuid or "").replace("-", "").strip()
    urls = []
    if uid:
        urls.append(f"https://mc-heads.net/avatar/{uid}/{size}")
    if name:
        urls.append(f"https://mc-heads.net/avatar/{name}/{size}")
    out = head_cache_path(uid or name or "steve")
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PUTsLauncher/1.3"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            if len(data) < 100:
                continue
            out.write_bytes(data)
            return out
        except Exception:
            continue
    return out if out.exists() else None


def upload_skin(access_token: str, skin_path: Path, variant: str = "classic") -> None:
    """Upload a new skin to the Microsoft/Minecraft account."""
    if not access_token or access_token == "0":
        raise RuntimeError("Precisa estar logado na Microsoft para mudar a skin.")
    if not skin_path.exists():
        raise FileNotFoundError(skin_path)
    variant = "slim" if variant.lower() in {"slim", "alex"} else "classic"
    with skin_path.open("rb") as fh:
        files = {"file": (skin_path.name, fh, "image/png")}
        data = {"variant": variant}
        resp = requests.post(
            "https://api.minecraftservices.com/minecraft/profile/skins",
            headers={"Authorization": f"Bearer {access_token}"},
            files=files,
            data=data,
            timeout=60,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Falha ao mudar skin ({resp.status_code}): {resp.text[:200]}")


# Backwards-compatible alias used by older code paths
def fetch_skin_render(uuid: str = "", name: str = "", size: int = 256) -> Optional[Path]:
    return fetch_skin_texture(uuid=uuid, name=name)
