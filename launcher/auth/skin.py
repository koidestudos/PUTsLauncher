from __future__ import annotations

import json
import time
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


def bust_skin_caches(uuid: str = "", name: str = "") -> None:
    for key in {uuid, name, (uuid or "").replace("-", "")}:
        if not key:
            continue
        texture_cache_path(key).unlink(missing_ok=True)
        head_cache_path(key).unlink(missing_ok=True)


def fetch_skin_texture(uuid: str = "", name: str = "", bust: bool = False) -> Optional[Path]:
    """Download the raw 64x64 (or 64x64 HD) skin texture PNG."""
    uid = (uuid or "").replace("-", "").strip()
    if bust:
        bust_skin_caches(uid, name)

    urls: list[str] = []
    # Cache-buster query helps after a fresh upload
    stamp = str(int(time.time())) if bust else ""
    q = f"?t={stamp}" if stamp else ""

    if uid:
        urls.extend(
            [
                f"https://sessionserver.mojang.com/session/minecraft/profile/{uid}",
                f"https://mc-heads.net/skin/{uid}{q}",
                f"https://crafatar.com/skins/{uid}{q}",
            ]
        )
    if name:
        urls.extend(
            [
                f"https://mc-heads.net/skin/{name}{q}",
                f"https://minotar.net/skin/{name}{q}",
            ]
        )

    out = texture_cache_path(uid or name or "steve")
    for url in urls:
        try:
            if "sessionserver.mojang.com" in url:
                req = urllib.request.Request(url, headers={"User-Agent": "PUTsLauncher/1.3"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    import base64

                    profile = json.loads(resp.read().decode("utf-8"))
                for prop in profile.get("properties") or []:
                    if prop.get("name") == "textures":
                        raw = json.loads(base64.b64decode(prop["value"]).decode("utf-8"))
                        skin_url = raw.get("textures", {}).get("SKIN", {}).get("url")
                        if skin_url:
                            url = skin_url + (f"{'&' if '?' in skin_url else '?'}t={stamp}" if stamp else "")
                            break
                else:
                    continue
            req = urllib.request.Request(url, headers={"User-Agent": "PUTsLauncher/1.3", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            if len(data) < 200:
                continue
            out.write_bytes(data)
            return out
        except Exception:
            continue
    return out if out.exists() else None


def fetch_head_avatar(uuid: str = "", name: str = "", size: int = 64, bust: bool = False) -> Optional[Path]:
    uid = (uuid or "").replace("-", "").strip()
    if bust:
        bust_skin_caches(uid, name)
    stamp = str(int(time.time())) if bust else ""
    q = f"?t={stamp}" if stamp else ""
    urls = []
    if uid:
        urls.append(f"https://mc-heads.net/avatar/{uid}/{size}{q}")
    if name:
        urls.append(f"https://mc-heads.net/avatar/{name}/{size}{q}")
    out = head_cache_path(uid or name or "steve")
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PUTsLauncher/1.3", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            if len(data) < 100:
                continue
            out.write_bytes(data)
            return out
        except Exception:
            pass
    return out if out.exists() else None


def upload_skin(access_token: str, skin_path: Path, variant: str = "classic") -> dict:
    """Upload a new skin to the Microsoft/Minecraft account. Returns profile JSON on success."""
    if not access_token or access_token == "0":
        raise RuntimeError("Precisa estar logado na Microsoft para mudar a skin.")
    if not skin_path.exists():
        raise FileNotFoundError(skin_path)
    variant = "slim" if variant.lower() in {"slim", "alex"} else "classic"
    raw = skin_path.read_bytes()
    if len(raw) < 200:
        raise RuntimeError("Arquivo de skin inválido.")

    # Send variant as a form field alongside the file (more reliable than data= + files=)
    files = {
        "variant": (None, variant),
        "file": (skin_path.name or "skin.png", raw, "image/png"),
    }
    resp = requests.post(
        "https://api.minecraftservices.com/minecraft/profile/skins",
        headers={"Authorization": f"Bearer {access_token}"},
        files=files,
        timeout=60,
    )

    # Success: 200 with profile, sometimes empty 204
    if 200 <= resp.status_code < 300:
        try:
            return resp.json() if resp.content else {"ok": True}
        except Exception:
            return {"ok": True}

    # Some gateways return an error envelope even after applying — if skins[] is present, treat as OK
    try:
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("skins"):
            return payload
    except Exception:
        payload = None

    detail = (resp.text or "")[:240]
    if resp.status_code == 401:
        raise PermissionError(f"Token expirado ({resp.status_code}): {detail}")
    raise RuntimeError(f"Falha ao mudar skin ({resp.status_code}): {detail}")


def cache_local_skin(skin_path: Path, uuid: str = "", name: str = "") -> Path:
    """Copy a local PNG into the launcher texture cache so the preview updates immediately."""
    dest = texture_cache_path((uuid or "").replace("-", "") or name or "steve")
    dest.write_bytes(skin_path.read_bytes())
    return dest


# Backwards-compatible alias used by older code paths
def fetch_skin_render(uuid: str = "", name: str = "", size: int = 256) -> Optional[Path]:
    return fetch_skin_texture(uuid=uuid, name=name)
