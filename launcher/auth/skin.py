from __future__ import annotations

import base64
import io
import json
import threading
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from launcher.config import cache_dir

# Minecraft skins are 64x64 (modern) or 64x32 (legacy). Anything bigger is
# refused: the preview never needs it and it keeps a hostile URL from feeding
# the launcher a huge image.
SKIN_SIZES = ((64, 64), (64, 32))
MAX_SKIN_BYTES = 128 * 1024
MAX_HEAD_BYTES = 128 * 1024

# Textures live in RAM only — nothing is written to the user's folder.
_CACHE_MAX = 32
_CACHE_TTL = 600.0  # segundos
_MISS_TTL = 60.0
_cache: "OrderedDict[str, tuple[float, Optional[Image.Image]]]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(kind: str, uuid: str = "", name: str = "", extra: str = "") -> str:
    # Keys are memory-only, so the nickname goes in verbatim (lowercased):
    # no sanitising means no two different nicks can collide on one entry.
    uid = (uuid or "").replace("-", "").strip().lower()
    return f"{kind}:{uid or (name or '').strip().lower()}:{extra}"


def _cache_get(key: str) -> tuple[bool, Optional[Image.Image]]:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return False, None
        stamp, image = entry
        ttl = _CACHE_TTL if image is not None else _MISS_TTL
        if now - stamp > ttl:
            _cache.pop(key, None)
            return False, None
        _cache.move_to_end(key)
        return True, image


def _cache_put(key: str, image: Optional[Image.Image]) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), image)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def clear_skin_cache() -> None:
    with _cache_lock:
        _cache.clear()


def bust_skin_caches(uuid: str = "", name: str = "") -> None:
    """Forget the in-memory textures of a player (after a skin upload)."""
    keys = {
        _cache_key(kind, uuid=uuid, name=name, extra=extra)
        for kind in ("texture", "head")
        for extra in ("", "64")
    }
    keys |= {_cache_key(kind, name=name, extra=extra) for kind in ("texture", "head") for extra in ("", "64")}
    with _cache_lock:
        for key in keys:
            _cache.pop(key, None)


def purge_legacy_skin_files() -> int:
    """
    Delete skin/head PNGs that older builds left in the launcher cache folder.
    Textures are memory-only now, so these files are pure garbage.
    """
    removed = 0
    try:
        root = cache_dir()
    except Exception:
        return 0
    for pattern in ("texture_*.png", "head_*.png"):
        for path in root.glob(pattern):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def decode_skin_bytes(data: bytes, *, max_bytes: int = MAX_SKIN_BYTES, sizes=SKIN_SIZES) -> Image.Image:
    """
    Turn PNG bytes into an image, refusing anything that is not a Minecraft-sized
    PNG. Dimensions come from the header, so an oversized image is rejected
    before any pixel is decoded.
    """
    if len(data) > max_bytes:
        raise ValueError(f"Imagem maior que o limite de {max_bytes // 1024} KB.")
    img = Image.open(io.BytesIO(data))
    if (img.format or "").upper() != "PNG":
        raise ValueError(f"Formato {img.format or 'desconhecido'} não aceito — use PNG.")
    if img.size not in sizes:
        allowed = " ou ".join(f"{w}x{h}" for w, h in sizes)
        raise ValueError(f"Skin {img.size[0]}x{img.size[1]} fora do padrão do Minecraft ({allowed}).")
    return img.convert("RGBA")


def _download(url: str, limit: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PUTsLauncher/1.4", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise ValueError(f"Imagem anunciada com {int(declared)} bytes — acima do limite.")
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Imagem acima do limite de tamanho.")
    return data


def _mojang_skin_url(uid: str, stamp: str) -> Optional[str]:
    req = urllib.request.Request(
        f"https://sessionserver.mojang.com/session/minecraft/profile/{uid}",
        headers={"User-Agent": "PUTsLauncher/1.4"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        profile = json.loads(resp.read(256 * 1024).decode("utf-8"))
    for prop in profile.get("properties") or []:
        if prop.get("name") == "textures":
            raw = json.loads(base64.b64decode(prop["value"]).decode("utf-8"))
            skin_url = raw.get("textures", {}).get("SKIN", {}).get("url")
            if skin_url and stamp:
                return skin_url + (f"{'&' if '?' in skin_url else '?'}t={stamp}")
            return skin_url
    return None


def fetch_skin_texture(uuid: str = "", name: str = "", bust: bool = False) -> Optional[Image.Image]:
    """
    Skin texture of a player, kept in RAM. Returns None when no source has it
    (or when every source served something outside the Minecraft skin format).
    """
    uid = (uuid or "").replace("-", "").strip()
    key = _cache_key("texture", uuid=uid, name=name)
    if bust:
        bust_skin_caches(uid, name)
    else:
        hit, cached = _cache_get(key)
        if hit:
            return cached

    stamp = str(int(time.time())) if bust else ""
    q = f"?t={stamp}" if stamp else ""
    urls: list[str] = []
    if uid:
        urls.extend(
            [
                f"mojang:{uid}",
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

    for url in urls:
        try:
            if url.startswith("mojang:"):
                resolved = _mojang_skin_url(url.split(":", 1)[1], stamp)
                if not resolved:
                    continue
                url = resolved
            image = decode_skin_bytes(_download(url, MAX_SKIN_BYTES))
        except Exception:
            continue
        _cache_put(key, image)
        return image

    _cache_put(key, None)
    return None


def fetch_head_avatar(uuid: str = "", name: str = "", size: int = 64, bust: bool = False) -> Optional[Image.Image]:
    """Small head render for the profile chip — also memory-only."""
    uid = (uuid or "").replace("-", "").strip()
    size = max(8, min(int(size or 64), 64))
    key = _cache_key("head", uuid=uid, name=name, extra=str(size))
    if bust:
        bust_skin_caches(uid, name)
    else:
        hit, cached = _cache_get(key)
        if hit:
            return cached

    stamp = str(int(time.time())) if bust else ""
    q = f"?t={stamp}" if stamp else ""
    urls = []
    if uid:
        urls.append(f"https://mc-heads.net/avatar/{uid}/{size}{q}")
    if name:
        urls.append(f"https://mc-heads.net/avatar/{name}/{size}{q}")

    for url in urls:
        try:
            image = decode_skin_bytes(
                _download(url, MAX_HEAD_BYTES),
                max_bytes=MAX_HEAD_BYTES,
                sizes=((size, size),),
            )
        except Exception:
            continue
        _cache_put(key, image)
        return image

    _cache_put(key, None)
    return None


def load_local_skin(skin_path: Path) -> Image.Image:
    """Read a PNG the user picked, applying the same size limits."""
    path = Path(skin_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.stat().st_size > MAX_SKIN_BYTES:
        raise ValueError(f"Arquivo maior que {MAX_SKIN_BYTES // 1024} KB — não é uma skin do Minecraft.")
    return decode_skin_bytes(path.read_bytes())


def cache_local_skin(skin_path: Path, uuid: str = "", name: str = "") -> Image.Image:
    """Put a local PNG in the memory cache so the preview updates immediately."""
    image = load_local_skin(skin_path)
    _cache_put(_cache_key("texture", uuid=uuid, name=name), image)
    return image


def upload_skin(access_token: str, skin_path: Path, variant: str = "classic") -> dict:
    """Upload a new skin to the Microsoft/Minecraft account. Returns profile JSON on success."""
    if not access_token or access_token == "0":
        raise RuntimeError("Precisa estar logado na Microsoft para mudar a skin.")
    skin_path = Path(skin_path)
    load_local_skin(skin_path)  # rejects non-PNG / oversized files before uploading
    variant = "slim" if variant.lower() in {"slim", "alex"} else "classic"
    raw = skin_path.read_bytes()

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
