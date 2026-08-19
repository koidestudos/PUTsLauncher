from __future__ import annotations

import re
from typing import Optional, Tuple

import minecraft_launcher_lib as mll
from minecraft_launcher_lib import mod_loader

from launcher.config import FORGE_PROFILE, FORGE_VERSION, MC_VERSION

# Loaders the launcher can install via minecraft-launcher-lib.
SUPPORTED_LOADERS = frozenset({"forge", "fabric", "neoforge", "quilt"})

# Keep forge helpers' names for callers that still import them.
_FORGE_INSTALL_RE = re.compile(r"\d+\.\d+(?:\.\d+)?-\d+\.\d+\.\d+")


def normalize_loader_name(loader: str) -> str:
    name = (loader or "forge").strip().lower()
    if name in {"", "forge"}:
        return "forge"
    if name in {"fabric", "fabric-loader"}:
        return "fabric"
    if name in {"neoforge", "neo-forge", "neo_forge"}:
        return "neoforge"
    if name in {"quilt", "quilt-loader"}:
        return "quilt"
    return name


def require_supported_loader(loader: str) -> str:
    name = normalize_loader_name(loader)
    if name not in SUPPORTED_LOADERS:
        raise ValueError(
            f"Loader “{loader}” não suportado. Use Forge, Fabric, NeoForge ou Quilt."
        )
    return name


def normalize_forge_install_version(mc_version: str, forge_version: str) -> str:
    """minecraft-launcher-lib Forge ids: ``1.20.1-47.4.10``."""
    mv = (mc_version or MC_VERSION).strip()
    fv = (forge_version or FORGE_VERSION).strip()
    if not fv:
        return FORGE_VERSION
    if "-forge-" in fv:
        left, right = fv.split("-forge-", 1)
        return f"{left}-{right}"
    if _FORGE_INSTALL_RE.fullmatch(fv):
        return fv
    if fv.startswith(mv + "-"):
        return fv
    if re.fullmatch(r"\d+\.\d+\.\d+", fv):
        return f"{mv}-{fv}"
    return fv


def _forge_install_mc_prefix(install: str) -> str:
    match = re.match(r"^(\d+\.\d+(?:\.\d+)?)-", (install or "").strip())
    return match.group(1) if match else ""


def latest_loader_version(loader: str, mc_version: str) -> str:
    """Newest loader build for this Minecraft version."""
    name = normalize_loader_name(loader)
    mv = (mc_version or MC_VERSION).strip() or MC_VERSION
    if name == "forge":
        try:
            ver = mod_loader.get_mod_loader("forge").get_latest_loader_version(mv)
            return normalize_forge_install_version(mv, ver)
        except Exception:
            if FORGE_VERSION.startswith(mv + "-"):
                return FORGE_VERSION
            raise RuntimeError(
                f"Não achei Forge para Minecraft {mv}. "
                "Confira a conexão ou escolha outra versão do jogo."
            ) from None
    try:
        return mod_loader.get_mod_loader(name).get_latest_loader_version(mv)
    except Exception as exc:
        raise RuntimeError(
            f"Não achei {loader_display_name(name)} para Minecraft {mv}."
        ) from exc


def normalize_loader_version(loader: str, mc_version: str, version: str) -> str:
    """
    Canonical loader version string stored on the instance.

    - forge → ``1.20.1-47.2.0``
    - fabric/quilt → bare loader build (``0.16.0``)
    - neoforge → bare build (``20.2.93``)
    """
    name = normalize_loader_name(loader)
    raw = (version or "").strip()
    mv = (mc_version or MC_VERSION).strip() or MC_VERSION

    if name == "forge":
        if not raw:
            return latest_loader_version("forge", mv)
        install = normalize_forge_install_version(mv, raw)
        prefix = _forge_install_mc_prefix(install)
        # Stale default e.g. keeping 1.18.2-40.3.11 on a 1.20.1 pack
        if prefix and prefix != mv:
            return latest_loader_version("forge", mv)
        return install

    if name == "fabric":
        if not raw:
            return latest_loader_version("fabric", mv)
        raw = re.sub(r"^(fabric-loader-|fabric-)", "", raw, flags=re.I)
        if raw.startswith("loader-"):
            raw = raw[7:]
        parts = raw.split("-")
        if len(parts) >= 2 and re.fullmatch(r"\d+\.\d+(?:\.\d+)*", parts[0]):
            return parts[0]
        return raw

    if name == "quilt":
        if not raw:
            return latest_loader_version("quilt", mv)
        raw = re.sub(r"^(quilt-loader-|quilt-)", "", raw, flags=re.I)
        parts = raw.split("-")
        if len(parts) >= 2 and re.fullmatch(r"\d+\.\d+(?:\.\d+)*", parts[0]):
            return parts[0]
        return raw

    if name == "neoforge":
        if not raw:
            return latest_loader_version("neoforge", mv)
        raw = re.sub(r"^neoforge-", "", raw, flags=re.I)
        return raw

    return raw


def loader_profile_id(loader: str, mc_version: str, loader_version: str) -> str:
    """Minecraft versions/ folder name for this loader install."""
    name = normalize_loader_name(loader)
    mv = (mc_version or MC_VERSION).strip() or MC_VERSION
    ver = normalize_loader_version(name, mv, loader_version)

    if name == "forge":
        try:
            return mll.forge.forge_to_installed_version(ver)
        except Exception:
            if ver.startswith(mv + "-"):
                return f"{mv}-forge-{ver[len(mv) + 1 :]}"
            return f"{mv}-forge-{ver}"

    try:
        return mod_loader.get_mod_loader(name).get_installed_version(mv, ver)
    except Exception:
        if name == "fabric":
            return f"fabric-loader-{ver}-{mv}"
        if name == "quilt":
            return f"quilt-loader-{ver}-{mv}"
        if name == "neoforge":
            return f"neoforge-{ver}"
        return f"{name}-{ver}-{mv}"


def forge_profile_id(mc_version: str, forge_version: str) -> str:
    return loader_profile_id("forge", mc_version, forge_version)


def active_loader_target() -> Tuple[str, str, str, str]:
    """
    Return ``(loader, mc_version, loader_version, profile)`` for the active instance.
    """
    try:
        from launcher.core.instances import GameInstance, get_active_id

        inst = GameInstance.load(get_active_id())
    except Exception:
        inst = None

    if not inst:
        return "forge", MC_VERSION, FORGE_VERSION, FORGE_PROFILE

    loader = normalize_loader_name(getattr(inst, "loader", None) or "forge")
    mv = (inst.mc_version or MC_VERSION).strip() or MC_VERSION
    install = normalize_loader_version(loader, mv, inst.forge_version or FORGE_VERSION)
    profile = loader_profile_id(loader, mv, install)

    dirty = False
    if getattr(inst, "loader", "forge") != loader:
        inst.loader = loader
        dirty = True
    if inst.forge_version != install:
        inst.forge_version = install
        dirty = True
    if inst.forge_profile != profile:
        inst.forge_profile = profile
        dirty = True
    if dirty:
        try:
            inst.save_meta()
        except Exception:
            pass

    return loader, mv, install, profile


def active_forge_target() -> Tuple[str, str, str]:
    """Back-compat: ``(mc_version, forge_install, profile)``."""
    _loader, mv, install, profile = active_loader_target()
    return mv, install, profile


def loader_display_name(loader: str) -> str:
    return {
        "forge": "Forge",
        "fabric": "Fabric",
        "neoforge": "NeoForge",
        "quilt": "Quilt",
    }.get(normalize_loader_name(loader), (loader or "Forge").title())


def uses_fml_flags(loader: str) -> bool:
    return normalize_loader_name(loader) in {"forge", "neoforge"}


def install_loader(
    *,
    loader: str,
    mc_version: str,
    loader_version: str,
    minecraft_directory: str,
    java: Optional[str] = None,
    callback=None,
) -> str:
    """
    Install the mod loader into ``minecraft_directory``.
    Returns the installed profile id.
    """
    name = require_supported_loader(loader)
    mv = (mc_version or MC_VERSION).strip() or MC_VERSION
    ver = normalize_loader_version(name, mv, loader_version)
    profile = loader_profile_id(name, mv, ver)

    if name == "forge":
        if not mll.forge.is_forge_version_valid(ver):
            raise RuntimeError(
                f"Forge {ver} não existe no Maven do Forge.\n"
                f"Confira mc_version/loader_version no index.json do modpack."
            )
        if not mll.forge.supports_automatic_install(ver):
            raise RuntimeError(f"Instalação automática não suportada para Forge {ver}.")
        mll.forge.install_forge_version(ver, minecraft_directory, callback=callback)
        return profile

    ml = mod_loader.get_mod_loader(name)
    if not ml.is_minecraft_version_supported(mv):
        raise RuntimeError(
            f"{loader_display_name(name)} não tem build para Minecraft {mv}."
        )
    ml.install(
        mv,
        minecraft_directory,
        loader_version=ver,
        callback=callback,
        java=java,
    )
    return profile
