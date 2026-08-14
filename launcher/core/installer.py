from __future__ import annotations

import errno
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Tuple

import minecraft_launcher_lib as mll

from launcher.config import (
    FORGE_PROFILE,
    FORGE_VERSION,
    JVM_RUNTIME,
    MC_VERSION,
    LauncherConfig,
    logs_dir,
    minecraft_dir,
    puts_home,
)
from launcher.core.progress import ProgressTracker


def normalize_forge_install_version(mc_version: str, forge_version: str) -> str:
    """
    minecraft-launcher-lib expects ids like ``1.20.1-47.4.10``
    (not ``1.20.1-forge-47.4.10`` and not bare ``47.4.10``).
    """
    mv = (mc_version or MC_VERSION).strip()
    fv = (forge_version or FORGE_VERSION).strip()
    if not fv:
        return FORGE_VERSION
    if "-forge-" in fv:
        left, right = fv.split("-forge-", 1)
        return f"{left}-{right}"
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)?-\d+\.\d+\.\d+", fv):
        return fv
    if fv.startswith(mv + "-"):
        return fv
    # bare build: 47.4.10
    if re.fullmatch(r"\d+\.\d+\.\d+", fv):
        return f"{mv}-{fv}"
    return fv


def forge_profile_id(mc_version: str, forge_version: str) -> str:
    install = normalize_forge_install_version(mc_version, forge_version)
    try:
        return mll.forge.forge_to_installed_version(install)
    except Exception:
        mv = (mc_version or MC_VERSION).strip()
        if install.startswith(mv + "-"):
            return f"{mv}-forge-{install[len(mv) + 1 :]}"
        return f"{mv}-forge-{install}"


def active_forge_target() -> Tuple[str, str, str]:
    """
    Return (mc_version, forge_install_version, forge_profile) for the active instance.
    Falls back to launcher defaults (1.18.2).
    """
    try:
        from launcher.core.instances import GameInstance, get_active_id

        inst = GameInstance.load(get_active_id())
    except Exception:
        inst = None

    if not inst:
        return MC_VERSION, FORGE_VERSION, FORGE_PROFILE

    mv = (inst.mc_version or MC_VERSION).strip() or MC_VERSION
    install = normalize_forge_install_version(mv, inst.forge_version or FORGE_VERSION)
    profile = forge_profile_id(mv, install)

    # Keep instance.json honest if catalog used short/long forge ids
    dirty = False
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

    return mv, install, profile


def forge_installed(mc_dir: Optional[Path] = None, profile: Optional[str] = None) -> bool:
    root = Path(mc_dir or minecraft_dir())
    if not profile:
        _, _, profile = active_forge_target()
    return (root / "versions" / profile / f"{profile}.json").exists()


def _wipe_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            shutil.rmtree(path, ignore_errors=True)


def _is_already_exists_error(exc: BaseException) -> bool:
    """Windows WinError 183 / FileExistsError during natives extract."""
    if isinstance(exc, FileExistsError):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) == 183:
            return True
        if exc.errno in {errno.EEXIST, getattr(errno, "EISDIR", 21)}:
            return True
    msg = str(exc).lower()
    return "já existente" in msg or "already exists" in msg


def clean_version_natives(mc_dir: Optional[Path] = None, mc_version: str = "", forge_install: str = "", profile: str = "") -> None:
    """
    Remove extracted natives folders that often break on Windows (WinError 183)
    when a file/dir name collide under natives/META-INF/...
    """
    root = Path(mc_dir or minecraft_dir())
    versions = root / "versions"
    if not versions.exists():
        return
    if not (mc_version and forge_install and profile):
        mc_version, forge_install, profile = active_forge_target()
    targets = {profile, mc_version, forge_install}
    for child in versions.iterdir():
        if not child.is_dir():
            continue
        if child.name in targets or "forge" in child.name.lower() or child.name.startswith(mc_version):
            _wipe_path(child / "natives")


def clean_incomplete_forge(mc_dir: Optional[Path] = None) -> None:
    """If Forge profile folder exists without version json, wipe it for a clean reinstall."""
    root = Path(mc_dir or minecraft_dir())
    mc_version, forge_install, profile = active_forge_target()
    forge_dir = root / "versions" / profile
    if forge_dir.exists() and not (forge_dir / f"{profile}.json").exists():
        _wipe_path(forge_dir)
    clean_version_natives(root, mc_version, forge_install, profile)


def java_runtime_path(mc_dir: Optional[Path] = None) -> Optional[str]:
    # Prefer shared runtime, then instance dir
    for root in (puts_home() / "shared", Path(mc_dir or minecraft_dir())):
        path = mll.runtime.get_executable_path(JVM_RUNTIME, str(root))
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


def ensure_java(cfg: LauncherConfig, tracker: Optional[ProgressTracker] = None, cancel_event=None) -> str:
    """Download Mojang Java 17 into MinecraftPUTS/shared (shared across instances)."""
    shared = puts_home() / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    # A Java the user pointed at in the config wins — downloading Mojang's on top
    # of it would ignore an explicit choice (and fail on a machine without net).
    chosen = (cfg.java_path or "").strip()
    if chosen and Path(chosen).exists():
        if tracker:
            tracker.set_phase("java", f"Usando Java configurado: {chosen}")
            tracker.complete_phase("Java pronto")
        return chosen
    bundled = java_runtime_path(shared)
    if bundled:
        if tracker:
            tracker.set_phase("java", "Java 17 já instalado")
            tracker.complete_phase("Java pronto")
        return bundled

    if tracker:
        tracker.set_phase("java", "Baixando Java 17 (Mojang)…")
    callback = tracker.as_mll_callback("java", cancel_event=cancel_event) if tracker else None
    mll.runtime.install_jvm_runtime(JVM_RUNTIME, str(shared), callback=callback)
    path = java_runtime_path(shared)
    if not path:
        fallback = resolve_java(cfg, shared)
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


def ensure_forge(tracker: Optional[ProgressTracker] = None, cancel_event=None) -> str:
    mc_path = minecraft_dir()
    mc = str(mc_path)
    mc_version, forge_install, profile = active_forge_target()

    if forge_installed(mc_path, profile=profile):
        if tracker:
            tracker.set_phase("forge", f"Forge {forge_install} já instalado")
            tracker.complete_phase("Forge pronto")
        return profile

    if not mll.forge.is_forge_version_valid(forge_install):
        raise RuntimeError(
            f"Forge {forge_install} não existe no Maven do Forge.\n"
            f"Confira mc_version/forge_version no index.json do modpack."
        )
    if not mll.forge.supports_automatic_install(forge_install):
        raise RuntimeError(f"Instalação automática não suportada para Forge {forge_install}.")

    if tracker:
        tracker.set_phase("forge", f"Baixando Minecraft {mc_version} + Forge {forge_install}…")
    callback = tracker.as_mll_callback("forge", cancel_event=cancel_event) if tracker else None

    # Clear leftover natives from a previous failed install (common WinError 183).
    clean_incomplete_forge(mc_path)

    last_err: Optional[BaseException] = None
    for attempt in range(2):
        try:
            mll.forge.install_forge_version(forge_install, mc, callback=callback)
            last_err = None
            break
        except CancelledError:
            raise
        except BaseException as exc:
            last_err = exc
            if attempt == 0 and _is_already_exists_error(exc):
                if tracker:
                    tracker.set_detail("Limpando natives corrompidos (Windows)…")
                clean_version_natives(mc_path, mc_version, forge_install, profile)
                forge_dir = mc_path / "versions" / profile
                if forge_dir.exists() and not (forge_dir / f"{profile}.json").exists():
                    _wipe_path(forge_dir)
                continue
            raise

    if last_err is not None:
        raise last_err

    if not forge_installed(mc_path, profile=profile):
        raise RuntimeError(
            f"Forge {forge_install} não foi instalado corretamente "
            f"(perfil {profile} ausente). Tente de novo."
        )
    if tracker:
        tracker.complete_phase(f"Forge {forge_install} instalado")
    return profile


def _run_forge_with_java(java_path: str, tracker: Optional[ProgressTracker] = None, cancel_event=None) -> str:
    """
    Install Forge while forcing JAVA_HOME/PATH so the Forge processors use our Java 17.
    """
    mc_version, forge_install, profile = active_forge_target()
    if forge_installed(profile=profile):
        if tracker:
            tracker.set_phase("forge", f"Forge {forge_install} já instalado")
            tracker.complete_phase("Forge pronto")
        return profile

    if tracker:
        tracker.set_phase("forge", f"Baixando Minecraft {mc_version} + Forge {forge_install}…")

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
        return ensure_forge(tracker=tracker, cancel_event=cancel_event)
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def is_game_ready(mc_dir: Optional[Path] = None) -> bool:
    """True when Java + Forge profile are already installed (Baixar → Jogar)."""
    root = Path(mc_dir or minecraft_dir())
    _, _, profile = active_forge_target()
    if not forge_installed(root, profile=profile):
        return False
    if not java_runtime_path(root):
        # System java is acceptable if forge already exists
        if not shutil.which("javaw") and not shutil.which("java"):
            return False
    # Mods folder should exist; empty pack still counts as "downloaded" for forge/java
    return True


def uninstall_game() -> None:
    """Remove every file of the active instance (Desinstalar)."""
    root = minecraft_dir()
    # Keep folder itself; wipe contents
    if not root.exists():
        return
    for child in list(root.iterdir()):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


# Everything the launcher itself downloads and can rebuild. Player data
# (saves, screenshots, mods, config, options.txt, logs…) is never touched.
REINSTALLABLE_ENTRIES = ("versions", "libraries", "assets", "runtime", "natives", "bin")


def clear_game_artifacts(mc_dir: Optional[Path] = None) -> list[str]:
    """
    Drop only the re-downloadable Minecraft/Forge artifacts of an instance.
    Returns the names removed.
    """
    root = Path(mc_dir or minecraft_dir())
    if not root.exists():
        return []
    removed: list[str] = []
    for name in REINSTALLABLE_ENTRIES:
        child = root / name
        if child.exists() or child.is_symlink():
            _wipe_path(child)
            removed.append(name)
    return removed


def reinstall_game(cfg: LauncherConfig, tracker: Optional[ProgressTracker] = None, cancel_event=None) -> str:
    """Rebuild Minecraft/Forge for the active instance, keeping player data."""
    removed = clear_game_artifacts()
    if tracker:
        tracker.set_detail(
            "Reinstalando Minecraft/Forge (mundos, mods e configs preservados)"
            + (f" · limpo: {', '.join(removed)}" if removed else "")
        )
    return prepare_game(cfg, tracker=tracker, cancel_event=cancel_event)


class CancelledError(RuntimeError):
    pass


def prepare_game(cfg: LauncherConfig, tracker: Optional[ProgressTracker] = None, cancel_event=None) -> str:
    """
    Full bootstrap into the active instance:
      1) Java 17 (shared)
      2) Minecraft + Forge for this instance's versions
    Returns path to java executable.
    """
    puts = minecraft_dir()
    mc_version, forge_install, profile = active_forge_target()
    if tracker:
        tracker.set_detail(f"Pasta: {puts}  ·  {profile}")

    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Download cancelado.")

    check_cancel()
    java = ensure_java(cfg, tracker=tracker, cancel_event=cancel_event)
    check_cancel()
    installed = _run_forge_with_java(java, tracker=tracker, cancel_event=cancel_event)
    if tracker:
        tracker.set_detail(f"Forge pronto: {installed} (pedido {forge_install})")
    check_cancel()
    return java


def write_launch_log(lines: list[str]) -> Path:
    path = logs_dir() / "last_launch.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
