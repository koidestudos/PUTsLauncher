from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


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
    return puts_home() / "instances"


def config_path() -> Path:
    return puts_home() / "launcher_config.json"


def minecraft_dir() -> Path:
    """Active instance game directory (multi-instance aware)."""
    from launcher.core.instances import get_active_minecraft_dir

    return get_active_minecraft_dir()


def logs_dir() -> Path:
    path = puts_home() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = puts_home() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Default GitHub Releases catalog: "owner/repo" or HTTPS index.json URL.
DEFAULT_MODPACK_CATALOG = ""


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
    saved_accounts: list = field(default_factory=list)
    window_width: int = 854
    window_height: int = 480
    close_launcher_on_start: bool = True
    # Performance / JVM options
    use_vulkan: bool = False
    use_g1gc: bool = True
    use_modern_jvm_flags: bool = True
    allocate_min_half_ram: bool = True
    disable_vsync: bool = False
    fullscreen: bool = False
    use_string_dedup: bool = False
    render_distance: int = 0
    extra_jvm_args: str = ""
    # Instances + GitHub Releases modpacks
    active_instance_id: str = "default"
    instances: list = field(default_factory=list)  # [{id,name,...}] mirror
    # owner/repo  OR  https://github.com/.../releases/download/.../index.json
    modpack_catalog: str = ""
    # legacy key from R2 era — migrated on load
    modpack_index_url: str = ""
    # GitHub publish (Personal Access Token — never commit this file publicly)
    github_token: str = ""
    github_login: str = ""
    github_publish_repo: str = ""
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
            broken = {
                "c36a9f36-b8ae-43c3-a484-b3064db1af32",
                "e19dd415-8236-4e44-b81b-88591a5c88e5",
            }
            if (cfg.azure_client_id or "").strip() in broken:
                cfg.azure_client_id = ""
                cfg.save()
            # Migrate old R2 URL field → GitHub catalog
            if not (cfg.modpack_catalog or "").strip() and (cfg.modpack_index_url or "").strip():
                cfg.modpack_catalog = cfg.modpack_index_url.strip()
                cfg.modpack_index_url = ""
                cfg.save()
            return cfg
        except Exception:
            return cls()

    def catalog_source(self) -> str:
        return (self.modpack_catalog or self.modpack_index_url or DEFAULT_MODPACK_CATALOG or "").strip()

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")


def bootstrap_instances(cfg: Optional["LauncherConfig"] = None) -> "LauncherConfig":
    """Ensure instance layout exists and activate cfg.active_instance_id."""
    from launcher.core.instances import (
        activate_instance,
        apply_instance_to_config,
        ensure_default_instance,
        list_instances,
        migrate_inherited_server,
    )

    if cfg is None:
        cfg = LauncherConfig.load()
    ensure_default_instance()
    migrate_inherited_server(cfg)
    wanted = (cfg.active_instance_id or "default").strip() or "default"
    ids = {i.id for i in list_instances()}
    if wanted not in ids:
        wanted = "default" if "default" in ids else next(iter(ids))
    inst = activate_instance(wanted)
    apply_instance_to_config(cfg, inst)
    return cfg
