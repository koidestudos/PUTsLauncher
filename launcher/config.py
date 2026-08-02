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

# Azure public client that accepts personal Microsoft accounts (consumers).
# Override via launcher_config.json → azure_client_id if you register your own app.
MS_CLIENT_ID = "90890812-00d1-48a8-8d3f-38465ef43b58"
MS_LOCAL_PORT = 28562
MS_REDIRECT_URI = f"http://127.0.0.1:{MS_LOCAL_PORT}"

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
    saved_accounts: list = field(default_factory=list)  # [{name,uuid,access_token,refresh_token}]
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
            cfg = cls(**filtered)
            # Drop broken / revoked public client IDs from older builds
            broken = {
                "c36a9f36-b8ae-43c3-a484-b3064db1af32",
                "e19dd415-8236-4e44-b81b-88591a5c88e5",
            }
            if (cfg.azure_client_id or "").strip() in broken:
                cfg.azure_client_id = ""
                cfg.save()
            return cfg
        except Exception:
            return cls()

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
