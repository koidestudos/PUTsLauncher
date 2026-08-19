from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional

import minecraft_launcher_lib as mll

from launcher.auth.session import GameSession
from launcher.config import FORGE_PROFILE, LauncherConfig, logs_dir, minecraft_dir
from launcher.core.installer import prepare_game, resolve_java, write_launch_log
from launcher.core.mods import sync_mods
from launcher.core.progress import ProgressTracker
from launcher.core.system import clamp_ram_gb


DEFAULT_PHASES = {
    "java": 0.18,
    "forge": 0.62,
    "mods": 0.15,
    "launch": 0.05,
}


def _jvm_arguments(cfg: LauncherConfig, loader: str = "forge") -> list[str]:
    # Never hand the JVM more memory than the machine has, even if an old
    # config (or a hand-edited one) asks for it.
    from launcher.core.loaders import uses_fml_flags

    ram_gb = clamp_ram_gb(cfg.ram_gb or 4)
    min_gb = max(1, ram_gb // 2) if getattr(cfg, "allocate_min_half_ram", True) else 1
    args = [
        f"-Xmx{ram_gb}G",
        f"-Xms{min_gb}G",
        "-Djava.net.preferIPv4Stack=true",
    ]
    if uses_fml_flags(loader):
        args.extend(
            [
                "-Dfml.ignoreInvalidMinecraftCertificates=true",
                "-Dfml.ignorePatchDiscrepancies=true",
            ]
        )

    if getattr(cfg, "use_g1gc", True):
        args.extend(
            [
                "-XX:+UseG1GC",
                "-XX:+ParallelRefProcEnabled",
                "-XX:MaxGCPauseMillis=200",
                "-XX:+UnlockExperimentalVMOptions",
                "-XX:+DisableExplicitGC",
                "-XX:G1NewSizePercent=30",
                "-XX:G1MaxNewSizePercent=40",
                "-XX:G1HeapRegionSize=8M",
                "-XX:G1ReservePercent=20",
                "-XX:InitiatingHeapOccupancyPercent=15",
            ]
        )

    if getattr(cfg, "use_modern_jvm_flags", True):
        args.extend(
            [
                "-XX:+AlwaysPreTouch",
                "-XX:+PerfDisableSharedMem",
                "-XX:MaxTenuringThreshold=1",
            ]
        )

    if getattr(cfg, "use_g1gc", True) and getattr(cfg, "use_string_dedup", False):
        args.append("-XX:+UseStringDeduplication")

    # Sodium / Iris / canvas-friendly: request Vulkan via LWJGL (1.18.2 + modern drivers)
    if getattr(cfg, "use_vulkan", False):
        args.append("-Dorg.lwjgl.vulkan=true")
        args.append("-Dorg.lwjgl.opengl.Display.enableNativeFullscreen=false")

    extra = (getattr(cfg, "extra_jvm_args", "") or "").strip()
    if extra:
        try:
            args.extend(shlex.split(extra, posix=os.name != "nt"))
        except Exception:
            args.extend(extra.split())

    return args


def apply_video_options(mc_dir: Path, fullscreen: bool, vsync: bool) -> None:
    """
    Keep the video switches of the launcher in sync with options.txt.

    ``--fullscreen`` already covers the launch itself, but writing the setting
    makes it stick for loaders that build their own argument list — and VSync
    only exists as a game option, there is no JVM flag for it.
    """
    options_file = Path(mc_dir) / "options.txt"
    if not options_file.exists():
        return
    wanted = {
        "fullscreen": "true" if fullscreen else "false",
        "enableVsync": "true" if vsync else "false",
    }
    try:
        lines = options_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return

    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.split(":", 1)[0]
        if key in wanted:
            out.append(f"{key}:{wanted[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in wanted.items():
        if key not in seen:
            out.append(f"{key}:{value}")
    if out != lines:
        try:
            options_file.write_text("\n".join(out) + "\n", encoding="utf-8")
        except OSError:
            pass


def build_launch_command(cfg: LauncherConfig, session: GameSession, java: str) -> list[str]:
    mc_dir = str(minecraft_dir())
    loader_name = "forge"
    try:
        from launcher.core.installer import active_loader_target
        from launcher.core.instances import GameInstance, get_active_id

        loader_name, _, _, profile = active_loader_target()
        inst = GameInstance.load(get_active_id())
        if inst and inst.forge_profile:
            profile = inst.forge_profile
        if inst and getattr(inst, "loader", None):
            loader_name = inst.loader
    except Exception:
        profile = FORGE_PROFILE

    options: mll.types.MinecraftOptions = {
        "username": session.username,
        "uuid": session.uuid,
        "token": session.access_token,
        "launcherName": "PUTsLauncher",
        "launcherVersion": "1.4.0",
        "gameDirectory": mc_dir,
        "executablePath": java,
        "jvmArguments": _jvm_arguments(cfg, loader_name),
    }

    fullscreen = bool(getattr(cfg, "fullscreen", False))
    if cfg.window_width and cfg.window_height and not fullscreen:
        options["customResolution"] = True
        options["resolutionWidth"] = str(cfg.window_width)
        options["resolutionHeight"] = str(cfg.window_height)

    # Instance server wins; the address in Opções is the global fallback
    server_ip = (cfg.server_ip or "").strip()
    server_port = cfg.server_port or 25565
    try:
        from launcher.core.instances import GameInstance, effective_server, get_active_id

        server_ip, server_port = effective_server(cfg, GameInstance.load(get_active_id()))
    except Exception:
        pass

    if server_ip:
        options["server"] = server_ip
        options["port"] = str(server_port)

    apply_video_options(Path(mc_dir), fullscreen, not bool(getattr(cfg, "disable_vsync", False)))

    command = mll.command.get_minecraft_command(profile, mc_dir, options)
    if fullscreen and "--fullscreen" not in command:
        # minecraft-launcher-lib has no fullscreen option; net.minecraft.client.main.Main
        # accepts the flag directly (and ignores unknown ones).
        command.append("--fullscreen")
    return command


def prepare_and_launch(
    cfg: LauncherConfig,
    session: GameSession,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> subprocess.Popen:
    from launcher.core.installer import (
        CancelledError,
        _is_already_exists_error,
        clean_version_natives,
    )

    if tracker is None:
        tracker = ProgressTracker(DEFAULT_PHASES)

    java = prepare_game(cfg, tracker=tracker, cancel_event=cancel_event)
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("Cancelado.")
    sync_mods(tracker=tracker)
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("Cancelado.")

    tracker.set_phase("launch", f"Abrindo Minecraft como {session.username}…")
    try:
        command = build_launch_command(cfg, session, java)
    except BaseException as exc:
        # Native extract can hit WinError 183; wipe natives and rebuild once.
        if not _is_already_exists_error(exc):
            raise
        clean_version_natives()
        command = build_launch_command(cfg, session, java)
    mc_dir = str(minecraft_dir())

    log_path = logs_dir() / "minecraft_stdout.log"
    write_launch_log(
        [
            f"user={session.username}",
            f"offline={session.offline}",
            f"java={java}",
            f"cwd={mc_dir}",
            f"cmd={' '.join(command[:8])} … ({len(command)} args)",
        ]
    )

    stdout = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 — kept open for child lifetime
    popen_kwargs: dict = {
        "cwd": mc_dir,
        "stdout": stdout,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    else:
        popen_kwargs["start_new_session"] = True

    if not Path(java).exists():
        raise RuntimeError(f"Java não encontrado: {java}")
    from launcher.core.installer import active_forge_target

    _, _, profile_id = active_forge_target()
    profile = Path(mc_dir) / "versions" / profile_id / f"{profile_id}.json"
    if not profile.exists():
        raise RuntimeError(
            f"Perfil Forge ausente: {profile_id}\n"
            "Clique em BAIXAR de novo para instalar a versão certa desta instância."
        )

    proc = subprocess.Popen(command, **popen_kwargs)
    tracker.complete_phase("Minecraft iniciado")
    tracker.set_phase_fraction(1.0, "Pronto!")
    return proc
