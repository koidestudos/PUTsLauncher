from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable, Optional

import minecraft_launcher_lib as mll

from launcher.config import FORGE_PROFILE, FORGE_VERSION, MC_VERSION, LauncherConfig, minecraft_dir


StatusCb = Callable[[str], None]
ProgressCb = Callable[[int, int, str], None]


def _callback(on_status: Optional[StatusCb], on_progress: Optional[ProgressCb]):
    max_val = {"v": 0}

    def set_status(status: str) -> None:
        if on_status:
            on_status(status)

    def set_progress(value: int) -> None:
        if on_progress:
            on_progress(value, max(max_val["v"], 1), "download")

    def set_max(value: int) -> None:
        max_val["v"] = value

    return {
        "setStatus": set_status,
        "setProgress": set_progress,
        "setMax": set_max,
    }


def forge_installed(mc_dir: Optional[Path] = None) -> bool:
    root = Path(mc_dir or minecraft_dir())
    versions = root / "versions" / FORGE_PROFILE
    return (versions / f"{FORGE_PROFILE}.json").exists()


def ensure_java(cfg: LauncherConfig, on_status: Optional[StatusCb] = None) -> str:
    if cfg.java_path and Path(cfg.java_path).exists():
        return cfg.java_path

    # Prefer Minecraft JVM runtimes managed by the library when available.
    java = mll.utils.get_java_executable()
    if java and Path(java).exists():
        return java

    for candidate in ("javaw", "java"):
        found = shutil.which(candidate)
        if found:
            return found

    raise RuntimeError(
        "Java não encontrado. Instale Java 17+ (Temurin/Adoptium) ou defina o caminho em Configurações."
    )


def ensure_forge(
    on_status: Optional[StatusCb] = None,
    on_progress: Optional[ProgressCb] = None,
) -> str:
    mc_dir = str(minecraft_dir())
    if forge_installed():
        if on_status:
            on_status(f"Forge {FORGE_VERSION} já instalado.")
        return FORGE_PROFILE

    if on_status:
        on_status(f"Instalando Minecraft {MC_VERSION} + Forge {FORGE_VERSION}…")

    callback = _callback(on_status, on_progress)
    if not mll.forge.supports_automatic_install(FORGE_VERSION):
        raise RuntimeError(f"Instalação automática não suportada para Forge {FORGE_VERSION}.")

    mll.forge.install_forge_version(FORGE_VERSION, mc_dir, callback=callback)
    if on_status:
        on_status("Forge instalado com sucesso.")
    return FORGE_PROFILE


def ensure_runtime_assets(
    on_status: Optional[StatusCb] = None,
    on_progress: Optional[ProgressCb] = None,
) -> None:
    """Ensure version assets/libraries exist (forge install usually covers this)."""
    mc_dir = str(minecraft_dir())
    if forge_installed():
        return
    callback = _callback(on_status, on_progress)
    if on_status:
        on_status(f"Baixando Minecraft {MC_VERSION}…")
    mll.install.install_minecraft_version(MC_VERSION, mc_dir, callback=callback)
