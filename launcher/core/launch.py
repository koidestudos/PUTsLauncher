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


DEFAULT_PHASES = {
    "java": 0.18,
    "forge": 0.62,
    "mods": 0.15,
    "launch": 0.05,
}


def _jvm_arguments(cfg: LauncherConfig) -> list[str]:
    ram_gb = max(1, min(int(cfg.ram_gb or 4), 32))
    min_gb = max(1, ram_gb // 2) if getattr(cfg, "allocate_min_half_ram", True) else 1
    args = [
        f"-Xmx{ram_gb}G",
        f"-Xms{min_gb}G",
        "-Djava.net.preferIPv4Stack=true",
        "-Dfml.ignoreInvalidMinecraftCertificates=true",
        "-Dfml.ignorePatchDiscrepancies=true",
    ]

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

    if getattr(cfg, "disable_vsync", False):
        args.append("-Dorg.lwjgl.opengl.Display.enableHighDPI=false")

    extra = (getattr(cfg, "extra_jvm_args", "") or "").strip()
    if extra:
        try:
            args.extend(shlex.split(extra, posix=os.name != "nt"))
        except Exception:
            args.extend(extra.split())

    return args


def build_launch_command(cfg: LauncherConfig, session: GameSession, java: str) -> list[str]:
    mc_dir = str(minecraft_dir())
    try:
        from launcher.core.instances import GameInstance, get_active_id

        inst = GameInstance.load(get_active_id())
        profile = (inst.forge_profile if inst else None) or FORGE_PROFILE
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
        "jvmArguments": _jvm_arguments(cfg),
    }

    if cfg.window_width and cfg.window_height and not getattr(cfg, "fullscreen", False):
        options["customResolution"] = True
        options["resolutionWidth"] = str(cfg.window_width)
        options["resolutionHeight"] = str(cfg.window_height)

    # Prefer instance server, then config
    server_ip = cfg.server_ip
    server_port = cfg.server_port or 25565
    try:
        from launcher.core.instances import GameInstance, get_active_id

        inst = GameInstance.load(get_active_id())
        if inst and inst.server_ip:
            server_ip = inst.server_ip
            server_port = int(inst.server_port or 25565)
    except Exception:
        pass

    if server_ip:
        options["server"] = server_ip
        options["port"] = str(server_port)

    return mll.command.get_minecraft_command(profile, mc_dir, options)


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
    profile = Path(mc_dir) / "versions" / FORGE_PROFILE / f"{FORGE_PROFILE}.json"
    if not profile.exists():
        raise RuntimeError(f"Perfil Forge ausente: {profile}")

    proc = subprocess.Popen(command, **popen_kwargs)
    tracker.complete_phase("Minecraft iniciado")
    tracker.set_phase_fraction(1.0, "Pronto!")
    return proc
