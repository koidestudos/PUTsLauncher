from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


MC_VERSION = "1.18.2"
FORGE_VERSION = "1.18.2-40.3.11"
FORGE_PROFILE = "1.18.2-forge-40.3.11"
JVM_RUNTIME = "java-runtime-gamma"

# Public Azure app used by Prism Launcher (GPL) — approved for Xbox Live / Minecraft.
# Redirect must match the app registration exactly.
MS_CLIENT_ID = "c36a9f36-b8ae-43c3-a484-b3064db1af32"
MS_REDIRECT_URI = "https://login.microsoftonline.com/common/oauth2/nativeclient"
# Alternate automatic localhost app (HMCL) — tried first for better UX.
MS_LOCAL_CLIENT_ID = "e19dd415-8236-4e44-b81b-88591a5c88e5"
MS_LOCAL_PORT = 29116

INSTANCE_FOLDER_NAME = "MinecraftPUTS"


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def asset_path(*parts: str) -> Path:
    rel = Path("launcher") / "assets" / Path(*parts)
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        bundled = meipass / rel
        if bundled.exists():
            return bundled
        beside = Path(sys.executable).resolve().parent / "assets" / Path(*parts)
        if beside.exists():
            return beside
    return Path(__file__).resolve().parent / "assets" / Path(*parts)


def mods_source_dir() -> Path:
    return app_root() / "mods"


def puts_home() -> Path:
    path = Path.home() / INSTANCE_FOLDER_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_instance_dir() -> Path:
    return puts_home()


def config_path() -> Path:
    return puts_home() / "launcher_config.json"


def minecraft_dir() -> Path:
    path = puts_home() / "minecraft"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = puts_home() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = puts_home() / "cache"
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
    azure_client_id: str = ""
    redirect_port: int = MS_LOCAL_PORT
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
