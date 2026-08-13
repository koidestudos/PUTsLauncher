from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from launcher.config import cache_dir, minecraft_dir
from launcher.core.progress import ProgressTracker

USER_AGENT = "PUTsLauncher/1.4"


@dataclass(frozen=True)
class SkinsModArtifact:
    """CustomSkinLoader build that includes Ely.by (ElyByAPI) by default."""

    filename: str
    url: str
    label: str


# Forge 1.17.1–1.20.4 (covers PUTs 1.18.2 and Dreamshift 1.20.1)
_CSL_FORGE_V2 = SkinsModArtifact(
    filename="CustomSkinLoader_ForgeV2-14.28.jar",
    url="https://cdn.modrinth.com/data/idMHQ4n2/versions/Rcbx2QhV/CustomSkinLoader_ForgeV2-14.28.jar",
    label="ForgeV2 14.28 (1.17–1.20.4)",
)

# Universal bootstrap — fallback for newer/older packs
_CSL_UNIVERSAL = SkinsModArtifact(
    filename="CustomSkinLoader_Universal-15.0.1.jar",
    url="https://cdn.modrinth.com/data/idMHQ4n2/versions/OLaesh5y/CustomSkinLoader_Universal-15.0.1.jar",
    label="Universal 15.0.1",
)

# Prefer Modrinth CDN; GitHub mirror when the release publishes the same file
_MIRRORS = {
    "CustomSkinLoader_ForgeV2-14.28.jar": [
        "https://cdn.modrinth.com/data/idMHQ4n2/versions/Rcbx2QhV/CustomSkinLoader_ForgeV2-14.28.jar",
    ],
    "CustomSkinLoader_Universal-15.0.1.jar": [
        "https://cdn.modrinth.com/data/idMHQ4n2/versions/OLaesh5y/CustomSkinLoader_Universal-15.0.1.jar",
        "https://github.com/xfl03/MCCustomSkinLoader/releases/download/v15.0.1/CustomSkinLoader_Universal-15.0.1.jar",
    ],
}


def _parse_mc_tuple(mc_version: str) -> tuple[int, int, int]:
    parts = re.findall(r"\d+", mc_version or "")
    nums = [int(x) for x in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def resolve_skins_mod(mc_version: str) -> SkinsModArtifact:
    """Pick CustomSkinLoader jar for the instance Minecraft version."""
    major, minor, patch = _parse_mc_tuple(mc_version)
    # 1.17.1 .. 1.20.4 → ForgeV2
    if (major, minor) >= (1, 17) and (major, minor, patch) <= (1, 20, 4):
        return _CSL_FORGE_V2
    return _CSL_UNIVERSAL


def elyby_priority_config() -> dict:
    """
    Config with Ely.by first so offline / 'pirata' nicks load skins from ely.by.
    Players: create free account on https://ely.by and use the same nick in the launcher.
    """
    return {
        "version": "14.28",
        "buildNumber": 0,
        "loadlist": [
            {"name": "GameProfile", "type": "GameProfile"},
            {
                "name": "ElyBy",
                "type": "ElyByAPI",
                "root": "http://skinsystem.ely.by/",
            },
            {"name": "Mojang", "type": "MojangAPI"},
            {
                "name": "LocalSkin",
                "type": "Legacy",
                "checkPNG": False,
                "model": "auto",
                "localSkinRoot": "LocalSkin",
                "localCapeRoot": "LocalSkin",
                "localElytraRoot": "LocalSkin",
            },
        ],
        "enableDynamicSkull": True,
        "enableTransparentSkin": True,
        "forceLoadAllTextures": True,
        "enableCape": True,
        "threadPoolSize": 8,
        "enableLogStdOut": False,
        "cacheExpiry": 30,
    }


def _download(url: str, dest: Path, timeout: int = 90) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream,*/*"})
    with urlopen(req, timeout=timeout) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


def cached_skins_mod_jar(artifact: SkinsModArtifact) -> Path:
    cache = cache_dir() / "skins_mod"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / artifact.filename
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest
    urls = _MIRRORS.get(artifact.filename, [artifact.url])
    last_err: Optional[BaseException] = None
    for url in urls:
        try:
            tmp = dest.with_suffix(".part")
            _download(url, tmp)
            tmp.replace(dest)
            return dest
        except BaseException as exc:
            last_err = exc
            continue
    raise RuntimeError(f"Falha ao baixar CustomSkinLoader ({artifact.label}): {last_err}")


def _remove_old_csl_jars(mods_dir: Path, keep_name: str) -> None:
    for jar in mods_dir.glob("CustomSkinLoader*.jar"):
        if jar.name != keep_name:
            jar.unlink(missing_ok=True)


def write_elyby_config(mc_dir: Path, *, force: bool = False) -> Path:
    """Write CustomSkinLoader.json with Ely.by prioritized (creates folder if needed)."""
    cfg_dir = mc_dir / "CustomSkinLoader"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "CustomSkinLoader.json"
    if path.exists() and not force:
        # Ensure ElyBy is present and near the top without wiping user tweaks entirely
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            loadlist = data.get("loadlist") if isinstance(data, dict) else None
            if isinstance(loadlist, list):
                has_ely = any(
                    isinstance(x, dict) and str(x.get("type") or "") == "ElyByAPI" and "ely" in str(x.get("name") or "").lower()
                    for x in loadlist
                )
                if has_ely:
                    # Move first ElyByAPI named ElyBy to index 1 (after GameProfile if present)
                    ely_idx = next(
                        i
                        for i, x in enumerate(loadlist)
                        if isinstance(x, dict) and str(x.get("type")) == "ElyByAPI"
                    )
                    ely = loadlist.pop(ely_idx)
                    insert_at = 1 if loadlist and isinstance(loadlist[0], dict) and loadlist[0].get("type") == "GameProfile" else 0
                    loadlist.insert(insert_at, ely)
                    data["loadlist"] = loadlist
                    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    return path
        except Exception:
            pass
    path.write_text(json.dumps(elyby_priority_config(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def ensure_elyby_skins_mod(
    mc_dir: Optional[Path] = None,
    mc_version: str = "",
    tracker: Optional[ProgressTracker] = None,
) -> Path:
    """
    Install CustomSkinLoader into the instance mods folder and prioritize Ely.by skins.
    Called for every modpack / instance so offline players get skins from ely.by.
    """
    root = Path(mc_dir or minecraft_dir())
    if not mc_version:
        try:
            from launcher.core.instances import GameInstance, get_active_id

            inst = GameInstance.load(get_active_id())
            mc_version = (inst.mc_version if inst else "") or ""
        except Exception:
            mc_version = ""

    artifact = resolve_skins_mod(mc_version)
    if tracker:
        tracker.set_detail(f"Skins Ely.by — {artifact.label}")

    cached = cached_skins_mod_jar(artifact)
    mods = root / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    _remove_old_csl_jars(mods, artifact.filename)
    target = mods / artifact.filename
    if not target.exists() or target.stat().st_size != cached.stat().st_size:
        shutil.copy2(cached, target)

    write_elyby_config(root, force=False)
    return target
