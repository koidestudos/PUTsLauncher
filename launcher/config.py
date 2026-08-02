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

# Default Azure public-client ID can be set by the SMP owner.
# Leave empty to rely on offline mode and/or official launcher account import.
DEFAULT_AZURE_CLIENT_ID = ""
DEFAULT_REDIRECT_PORT = 27845


def app_root() -> Path:
    """Directory that contains the launcher + bundled mods folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # launcher/config.py -> repo root
    return Path(__file__).resolve().parent.parent


def mods_source_dir() -> Path:
    return app_root() / "mods"


def default_instance_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".local" / "share"
    return base / "PUTsLauncher"


def config_path() -> Path:
    return default_instance_dir() / "launcher_config.json"


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
        payload = asdict(self)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def minecraft_dir() -> Path:
    """Isolated game directory used by this SMP launcher."""
    path = default_instance_dir() / "minecraft"
    path.mkdir(parents=True, exist_ok=True)
    return path
