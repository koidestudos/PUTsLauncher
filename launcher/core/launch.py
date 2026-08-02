from __future__ import annotations

import os
import subprocess

import minecraft_launcher_lib as mll

from launcher.auth.session import GameSession
from launcher.config import FORGE_PROFILE, LauncherConfig, minecraft_dir
from launcher.core.installer import ensure_forge, ensure_java
from launcher.core.mods import sync_mods


def build_launch_command(cfg: LauncherConfig, session: GameSession) -> list[str]:
    mc_dir = str(minecraft_dir())
    java = ensure_java(cfg)
    ram_gb = max(1, min(int(cfg.ram_gb or 4), 32))

    options: mll.types.MinecraftOptions = {
        "username": session.username,
        "uuid": session.uuid,
        "token": session.access_token,
        "launcherName": "PUTsLauncher",
        "launcherVersion": "1.0.0",
        "gameDirectory": mc_dir,
        "jvmArguments": [
            f"-Xmx{ram_gb}G",
            f"-Xms{max(1, ram_gb // 2)}G",
            "-Djava.net.preferIPv4Stack=true",
        ],
    }

    if cfg.window_width and cfg.window_height:
        options["customResolution"] = True
        options["resolutionWidth"] = str(cfg.window_width)
        options["resolutionHeight"] = str(cfg.window_height)

    if cfg.server_ip:
        options["server"] = cfg.server_ip
        options["port"] = str(cfg.server_port or 25565)

    # Executable override
    if java:
        options["executablePath"] = java

    command = mll.command.get_minecraft_command(FORGE_PROFILE, mc_dir, options)
    return command


def prepare_and_launch(
    cfg: LauncherConfig,
    session: GameSession,
    on_status=None,
) -> subprocess.Popen:
    if on_status:
        on_status("Preparando instalação Forge…")
    ensure_forge(on_status=on_status)
    sync_mods(on_status=on_status)
    if on_status:
        on_status(f"Abrindo Minecraft como {session.username}…")

    command = build_launch_command(cfg, session)
    mc_dir = str(minecraft_dir())

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )

    popen_kwargs = {
        "cwd": mc_dir,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(command, **popen_kwargs)
