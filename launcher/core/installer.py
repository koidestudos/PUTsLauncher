from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import minecraft_launcher_lib as mll

from launcher.config import (
    FORGE_PROFILE,
    FORGE_VERSION,
    JVM_RUNTIME,
    MC_VERSION,
    LauncherConfig,
    logs_dir,
    minecraft_dir,
)
from launcher.core.progress import ProgressTracker


def forge_installed(mc_dir: Optional[Path] = None) -> bool:
    root = Path(mc_dir or minecraft_dir())
    return (root / "versions" / FORGE_PROFILE / f"{FORGE_PROFILE}.json").exists()


def java_runtime_path(mc_dir: Optional[Path] = None) -> Optional[str]:
    root = str(mc_dir or minecraft_dir())
    path = mll.runtime.get_executable_path(JVM_RUNTIME, root)
    if path and Path(path).exists():
        return path
    return None


def resolve_java(cfg: LauncherConfig, mc_dir: Optional[Path] = None) -> Optional[str]:
    if cfg.java_path and Path(cfg.java_path).exists():
        return cfg.java_path
    bundled = java_runtime_path(mc_dir)
    if bundled:
        return bundled
    for candidate in ("javaw", "java"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def ensure_java(cfg: LauncherConfig, tracker: Optional[ProgressTracker] = None) -> str:
    """Download Mojang Java 17 into MinecraftPUTS/minecraft/runtime if needed."""
    mc = minecraft_dir()
    existing = resolve_java(cfg, mc)
    # Prefer the bundled Mojang runtime when present; otherwise install it.
    bundled = java_runtime_path(mc)
    if bundled:
        if tracker:
            tracker.set_phase("java", "Java 17 já instalado")
            tracker.complete_phase("Java pronto")
        return bundled

    if tracker:
        tracker.set_phase("java", "Baixando Java 17 (Mojang)…")
    callback = tracker.as_mll_callback("java") if tracker else None
    mll.runtime.install_jvm_runtime(JVM_RUNTIME, str(mc), callback=callback)
    path = java_runtime_path(mc)
    if not path:
        # Fall back to system java if Mojang runtime failed to resolve
        fallback = resolve_java(cfg, mc)
        if fallback:
            if tracker:
                tracker.complete_phase(f"Usando Java do sistema: {fallback}")
            return fallback
        raise RuntimeError("Falha ao instalar o Java 17 dentro de MinecraftPUTS.")
    if tracker:
        tracker.complete_phase("Java 17 instalado")
    cfg.java_path = path
    cfg.save()
    return path


def ensure_forge(tracker: Optional[ProgressTracker] = None) -> str:
    mc = str(minecraft_dir())
    if forge_installed():
        if tracker:
            tracker.set_phase("forge", f"Forge {FORGE_VERSION} já instalado")
            tracker.complete_phase("Forge pronto")
        return FORGE_PROFILE

    if not mll.forge.supports_automatic_install(FORGE_VERSION):
        raise RuntimeError(f"Instalação automática não suportada para Forge {FORGE_VERSION}.")

    if tracker:
        tracker.set_phase("forge", f"Baixando Minecraft {MC_VERSION} + Forge…")
    callback = tracker.as_mll_callback("forge") if tracker else None

    # Forge installer needs a working Java on PATH / JAVA_HOME for processors.
    # Ensure we at least attempt with whatever we have; caller should install Java first.
    mll.forge.install_forge_version(FORGE_VERSION, mc, callback=callback)

    if not forge_installed():
        raise RuntimeError("Forge não foi instalado corretamente. Tente de novo.")
    if tracker:
        tracker.complete_phase("Forge instalado")
    return FORGE_PROFILE


def _run_forge_with_java(java_path: str, tracker: Optional[ProgressTracker] = None) -> str:
    """
    Install Forge while forcing JAVA_HOME/PATH so the Forge processors use our Java 17.
    """
    mc = str(minecraft_dir())
    if forge_installed():
        if tracker:
            tracker.set_phase("forge", f"Forge {FORGE_VERSION} já instalado")
            tracker.complete_phase("Forge pronto")
        return FORGE_PROFILE

    if tracker:
        tracker.set_phase("forge", f"Baixando Minecraft {MC_VERSION} + Forge…")

    java_bin = Path(java_path)
    java_home = java_bin.parent.parent if java_bin.parent.name in {"bin", "Bin"} else java_bin.parent
    env = os.environ.copy()
    env["JAVA_HOME"] = str(java_home)
    # Put our java first on PATH for child processes spawned by the Forge installer
    path_sep = ";" if os.name == "nt" else ":"
    env["PATH"] = str(java_bin.parent) + path_sep + env.get("PATH", "")

    old_env = os.environ.copy()
    try:
        os.environ.update(env)
        return ensure_forge(tracker=tracker)
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def prepare_game(cfg: LauncherConfig, tracker: Optional[ProgressTracker] = None) -> str:
    """
    Full bootstrap into ~/MinecraftPUTS:
      1) Java 17
      2) Minecraft + Forge
    Returns path to java executable.
    """
    puts = minecraft_dir()
    if tracker:
        tracker.set_detail(f"Pasta do jogo: {puts}")
    java = ensure_java(cfg, tracker=tracker)
    _run_forge_with_java(java, tracker=tracker)
    return java


def write_launch_log(lines: list[str]) -> Path:
    path = logs_dir() / "last_launch.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
