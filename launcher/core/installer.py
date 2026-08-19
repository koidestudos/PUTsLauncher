from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

import minecraft_launcher_lib as mll

from launcher.config import (
    JVM_RUNTIME,
    LauncherConfig,
    logs_dir,
    minecraft_dir,
    puts_home,
)
from launcher.core.loaders import (
    active_forge_target,
    active_loader_target,
    forge_profile_id,
    install_loader,
    loader_display_name,
    normalize_forge_install_version,
    normalize_loader_name,
)
from launcher.core.progress import ProgressTracker

# Re-export for existing imports
__all__ = [
    "CancelledError",
    "active_forge_target",
    "active_loader_target",
    "clear_game_artifacts",
    "clean_incomplete_forge",
    "clean_version_natives",
    "ensure_forge",
    "ensure_java",
    "ensure_loader",
    "forge_installed",
    "forge_profile_id",
    "is_game_ready",
    "java_runtime_path",
    "normalize_forge_install_version",
    "prepare_game",
    "reinstall_game",
    "resolve_java",
    "uninstall_game",
    "write_launch_log",
]


def forge_installed(mc_dir: Optional[Path] = None, profile: Optional[str] = None) -> bool:
    root = Path(mc_dir or minecraft_dir())
    if not profile:
        _, _, _, profile = active_loader_target()
    return (root / "versions" / profile / f"{profile}.json").exists()


loader_installed = forge_installed


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


def clean_version_natives(
    mc_dir: Optional[Path] = None,
    mc_version: str = "",
    forge_install: str = "",
    profile: str = "",
) -> None:
    """
    Remove extracted natives folders that often break on Windows (WinError 183)
    when a file/dir name collide under natives/META-INF/...
    """
    root = Path(mc_dir or minecraft_dir())
    versions = root / "versions"
    if not versions.exists():
        return
    if not (mc_version and forge_install and profile):
        _loader, mc_version, forge_install, profile = active_loader_target()
    targets = {profile, mc_version, forge_install}
    for child in versions.iterdir():
        if not child.is_dir():
            continue
        low = child.name.lower()
        if (
            child.name in targets
            or "forge" in low
            or "fabric" in low
            or "neoforge" in low
            or "quilt" in low
            or child.name.startswith(mc_version)
        ):
            _wipe_path(child / "natives")


def clean_incomplete_forge(mc_dir: Optional[Path] = None) -> None:
    """If loader profile folder exists without version json, wipe it for a clean reinstall."""
    root = Path(mc_dir or minecraft_dir())
    _loader, mc_version, forge_install, profile = active_loader_target()
    forge_dir = root / "versions" / profile
    if forge_dir.exists() and not (forge_dir / f"{profile}.json").exists():
        _wipe_path(forge_dir)
    clean_version_natives(root, mc_version, forge_install, profile)


def java_runtime_path(mc_dir: Optional[Path] = None) -> Optional[str]:
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


def ensure_loader(
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
    java: Optional[str] = None,
) -> str:
    """Install Forge / Fabric / NeoForge / Quilt for the active instance."""
    mc_path = minecraft_dir()
    mc = str(mc_path)
    loader, mc_version, loader_ver, profile = active_loader_target()
    label = loader_display_name(loader)

    if forge_installed(mc_path, profile=profile):
        if tracker:
            tracker.set_phase("forge", f"{label} {loader_ver} já instalado")
            tracker.complete_phase(f"{label} pronto")
        return profile

    if tracker:
        tracker.set_phase("forge", f"Baixando Minecraft {mc_version} + {label} {loader_ver}…")
    callback = tracker.as_mll_callback("forge", cancel_event=cancel_event) if tracker else None
    clean_incomplete_forge(mc_path)

    last_err: Optional[BaseException] = None
    for attempt in range(2):
        try:
            install_loader(
                loader=loader,
                mc_version=mc_version,
                loader_version=loader_ver,
                minecraft_directory=mc,
                java=java,
                callback=callback,
            )
            last_err = None
            break
        except CancelledError:
            raise
        except BaseException as exc:
            last_err = exc
            if attempt == 0 and _is_already_exists_error(exc):
                if tracker:
                    tracker.set_detail("Limpando natives corrompidos (Windows)…")
                clean_version_natives(mc_path, mc_version, loader_ver, profile)
                forge_dir = mc_path / "versions" / profile
                if forge_dir.exists() and not (forge_dir / f"{profile}.json").exists():
                    _wipe_path(forge_dir)
                continue
            raise

    if last_err is not None:
        raise last_err

    if not forge_installed(mc_path, profile=profile):
        raise RuntimeError(
            f"{label} {loader_ver} não foi instalado corretamente "
            f"(perfil {profile} ausente). Tente de novo."
        )
    if tracker:
        tracker.complete_phase(f"{label} {loader_ver} instalado")
    return profile


def ensure_forge(tracker: Optional[ProgressTracker] = None, cancel_event=None) -> str:
    return ensure_loader(tracker=tracker, cancel_event=cancel_event)


def _run_loader_with_java(
    java_path: str, tracker: Optional[ProgressTracker] = None, cancel_event=None
) -> str:
    loader, mc_version, loader_ver, profile = active_loader_target()
    label = loader_display_name(loader)
    if forge_installed(profile=profile):
        if tracker:
            tracker.set_phase("forge", f"{label} {loader_ver} já instalado")
            tracker.complete_phase(f"{label} pronto")
        return profile

    if tracker:
        tracker.set_phase("forge", f"Baixando Minecraft {mc_version} + {label} {loader_ver}…")

    java_bin = Path(java_path)
    java_home = java_bin.parent.parent if java_bin.parent.name in {"bin", "Bin"} else java_bin.parent
    env = os.environ.copy()
    env["JAVA_HOME"] = str(java_home)
    path_sep = ";" if os.name == "nt" else ":"
    env["PATH"] = str(java_bin.parent) + path_sep + env.get("PATH", "")

    old_env = os.environ.copy()
    try:
        os.environ.update(env)
        return ensure_loader(tracker=tracker, cancel_event=cancel_event, java=java_path)
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def _run_forge_with_java(java_path: str, tracker: Optional[ProgressTracker] = None, cancel_event=None) -> str:
    return _run_loader_with_java(java_path, tracker=tracker, cancel_event=cancel_event)


def is_game_ready(mc_dir: Optional[Path] = None) -> bool:
    """True when Java + loader profile are already installed (Baixar → Jogar)."""
    root = Path(mc_dir or minecraft_dir())
    _, _, _, profile = active_loader_target()
    if not forge_installed(root, profile=profile):
        return False
    if not java_runtime_path(root):
        if not shutil.which("javaw") and not shutil.which("java"):
            return False
    return True


def uninstall_game() -> None:
    """Remove every file of the active instance (Desinstalar)."""
    root = minecraft_dir()
    if not root.exists():
        return
    for child in list(root.iterdir()):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


REINSTALLABLE_ENTRIES = ("versions", "libraries", "assets", "runtime", "natives", "bin")


def clear_game_artifacts(mc_dir: Optional[Path] = None) -> list[str]:
    """Drop only re-downloadable Minecraft/loader artifacts of an instance."""
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
    """Rebuild Minecraft + loader for the active instance, keeping player data."""
    removed = clear_game_artifacts()
    if tracker:
        tracker.set_detail(
            "Reinstalando Minecraft + loader (mundos, mods e configs preservados)"
            + (f" · limpo: {', '.join(removed)}" if removed else "")
        )
    return prepare_game(cfg, tracker=tracker, cancel_event=cancel_event)


class CancelledError(RuntimeError):
    pass


def prepare_game(cfg: LauncherConfig, tracker: Optional[ProgressTracker] = None, cancel_event=None) -> str:
    """
    Full bootstrap into the active instance:
      1) Java 17 (shared)
      2) Minecraft + Forge/Fabric/NeoForge/Quilt for this instance
    Returns path to java executable.
    """
    puts = minecraft_dir()
    loader, _mc, loader_ver, profile = active_loader_target()
    label = loader_display_name(loader)
    if tracker:
        tracker.set_detail(f"Pasta: {puts}  ·  {profile}")

    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Download cancelado.")

    check_cancel()
    java = ensure_java(cfg, tracker=tracker, cancel_event=cancel_event)
    check_cancel()
    installed = _run_loader_with_java(java, tracker=tracker, cancel_event=cancel_event)
    if tracker:
        tracker.set_detail(f"{label} pronto: {installed} (pedido {loader_ver})")
    check_cancel()

    # Pack archives are disposable once the instance is installed.
    try:
        from launcher.core.cache_cleanup import cleanup_cache

        cleanup_cache()
    except Exception:
        pass

    return java


def write_launch_log(lines: list[str]) -> Path:
    path = logs_dir() / "last_launch.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
