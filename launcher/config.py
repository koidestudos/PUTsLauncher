from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


MC_VERSION = "1.18.2"
FORGE_VERSION = "1.18.2-40.3.11"
FORGE_PROFILE = "1.18.2-forge-40.3.11"
# Mojang Java 17 runtime (required by Forge 1.18.2)
JVM_RUNTIME = "java-runtime-gamma"

DEFAULT_AZURE_CLIENT_ID = ""
DEFAULT_REDIRECT_PORT = 27845
INSTANCE_FOLDER_NAME = "MinecraftPUTS"


def app_root() -> Path:
    """Directory that contains the launcher + bundled mods folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def mods_source_dir() -> Path:
    return app_root() / "mods"


def puts_home() -> Path:
    """
    User-owned install root:
      Windows: %USERPROFILE%\\MinecraftPUTS
      Linux/mac: ~/MinecraftPUTS
    """
    path = Path.home() / INSTANCE_FOLDER_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_instance_dir() -> Path:
    """Alias kept for older imports — same as puts_home()."""
    return puts_home()


def config_path() -> Path:
    return puts_home() / "launcher_config.json"


def minecraft_dir() -> Path:
    """Game files live in MinecraftPUTS/minecraft (versions, libs, assets, mods, runtime)."""
    path = puts_home() / "minecraft"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = puts_home() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class LauncherConfig:
    username: str = "Steve"
    auth_mode: str = "offline"  # offline | microsoft
    ram_gb: int = 4
    java_path: str = ""
    server_ip: str = ""
    server_port: int = 25565
    azure_client_id: str = DEFAULT_AZURE_CLIENT_ID
    redirect_port: int = DEFAULT_REDIRECT_PORT
    microsoft_refresh_token: str = ""
    microsoft_uuid: str = ""
    microsoft_name: str = ""
    microsoft_access_token: str = ""
    window_width: int = 854
    window_height: int = 480
    close_launcher_on_start: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "LauncherConfig":
        path = config_path()
        if not path.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
            filtered = {k: v for k, v in data.items() if k in known}
            return cls(**filtered)
        except Exception:
            return cls()

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
