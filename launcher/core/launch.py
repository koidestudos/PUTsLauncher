from __future__ import annotations

import os
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


def build_launch_command(cfg: LauncherConfig, session: GameSession, java: str) -> list[str]:
    mc_dir = str(minecraft_dir())
    ram_gb = max(1, min(int(cfg.ram_gb or 4), 32))

    options: mll.types.MinecraftOptions = {
        "username": session.username,
        "uuid": session.uuid,
        "token": session.access_token,
        "launcherName": "PUTsLauncher",
        "launcherVersion": "1.1.0",
        "gameDirectory": mc_dir,
        "executablePath": java,
        "jvmArguments": [
            f"-Xmx{ram_gb}G",
            f"-Xms{max(1, ram_gb // 2)}G",
            "-Djava.net.preferIPv4Stack=true",
            "-Dfml.ignoreInvalidMinecraftCertificates=true",
            "-Dfml.ignorePatchDiscrepancies=true",
        ],
    }

    if cfg.window_width and cfg.window_height:
        options["customResolution"] = True
        options["resolutionWidth"] = str(cfg.window_width)
        options["resolutionHeight"] = str(cfg.window_height)

    if cfg.server_ip:
        options["server"] = cfg.server_ip
        options["port"] = str(cfg.server_port or 25565)

    return mll.command.get_minecraft_command(FORGE_PROFILE, mc_dir, options)


def prepare_and_launch(
    cfg: LauncherConfig,
    session: GameSession,
    tracker: Optional[ProgressTracker] = None,
) -> subprocess.Popen:
    if tracker is None:
        tracker = ProgressTracker(DEFAULT_PHASES)

    java = prepare_game(cfg, tracker=tracker)
    sync_mods(tracker=tracker)

    tracker.set_phase("launch", f"Abrindo Minecraft como {session.username}…")
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

    # Quick sanity: java + forge profile must exist
    if not Path(java).exists():
        raise RuntimeError(f"Java não encontrado: {java}")
    profile = Path(mc_dir) / "versions" / FORGE_PROFILE / f"{FORGE_PROFILE}.json"
    if not profile.exists():
        raise RuntimeError(f"Perfil Forge ausente: {profile}")

    proc = subprocess.Popen(command, **popen_kwargs)
    tracker.complete_phase("Minecraft iniciado")
    tracker.set_phase_fraction(1.0, "Pronto!")
    return proc
