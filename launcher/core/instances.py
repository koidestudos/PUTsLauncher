from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from launcher.config import (
    FORGE_PROFILE,
    FORGE_VERSION,
    MC_VERSION,
    LauncherConfig,
    puts_home,
)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:48] or "instance")


def instances_root() -> Path:
    path = puts_home() / "instances"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class GameInstance:
    id: str
    name: str
    mc_version: str = MC_VERSION
    forge_version: str = FORGE_VERSION
    forge_profile: str = FORGE_PROFILE
    modpack_id: str = ""
    modpack_version: str = ""
    server_ip: str = ""
    server_port: int = 25565
    source: str = "local"  # local | github | bundled | r2 (legacy)
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return instances_root() / self.id

    @property
    def minecraft_path(self) -> Path:
        return self.root / "minecraft"

    def meta_path(self) -> Path:
        return self.root / "instance.json"

    def ensure_dirs(self) -> None:
        self.minecraft_path.mkdir(parents=True, exist_ok=True)
        (self.minecraft_path / "mods").mkdir(parents=True, exist_ok=True)

    def save_meta(self) -> None:
        self.ensure_dirs()
        self.meta_path().write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, instance_id: str) -> Optional["GameInstance"]:
        meta = instances_root() / instance_id / "instance.json"
        if not meta.exists():
            return None
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
            return cls(**{k: v for k, v in data.items() if k in known})
        except Exception:
            return None


# Process-wide active minecraft dir (set from config on startup / switch)
_active_mc: Optional[Path] = None
_active_id: str = ""


def get_active_id() -> str:
    return _active_id or "default"


def get_active_minecraft_dir() -> Path:
    global _active_mc
    if _active_mc is None:
        ensure_default_instance()
        activate_instance(get_active_id())
    assert _active_mc is not None
    return _active_mc


def list_instances() -> list[GameInstance]:
    ensure_default_instance()
    out: list[GameInstance] = []
    for child in sorted(instances_root().iterdir()):
        if not child.is_dir():
            continue
        inst = GameInstance.load(child.name)
        if inst is None:
            # Folder without meta — synthesize
            inst = GameInstance(id=child.name, name=child.name.replace("-", " ").title())
            inst.save_meta()
        out.append(inst)
    return out


def ensure_default_instance() -> GameInstance:
    """Ensure at least one instance exists. Migrate legacy ~/MinecraftPUTS/minecraft if needed."""
    from datetime import datetime, timezone

    root = instances_root()
    default_id = "default"
    legacy_mc = puts_home() / "minecraft"

    # Prefer any existing instance (e.g. puts-smp from an older install or GitHub pack)
    existing_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if existing_dirs:
        for child in existing_dirs:
            inst = GameInstance.load(child.name)
            if inst is not None:
                inst.ensure_dirs()
                return inst
            # Folder without meta — synthesize
            inst = GameInstance(id=child.name, name=child.name.replace("-", " ").title(), source="local")
            inst.ensure_dirs()
            inst.save_meta()
            return inst

    default_root = root / default_id
    default_root.mkdir(parents=True, exist_ok=True)
    if legacy_mc.exists() and not (default_root / "minecraft").exists():
        try:
            shutil.move(str(legacy_mc), str(default_root / "minecraft"))
        except Exception:
            (default_root / "minecraft").mkdir(parents=True, exist_ok=True)
    inst = GameInstance(
        id=default_id,
        name="Início",
        source="local",
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    inst.ensure_dirs()
    inst.save_meta()
    return inst


def activate_instance(instance_id: str) -> GameInstance:
    global _active_mc, _active_id
    inst = GameInstance.load(instance_id)
    if inst is None:
        inst = ensure_default_instance()
        instance_id = inst.id
    inst.ensure_dirs()
    _active_id = inst.id
    _active_mc = inst.minecraft_path
    return inst


def create_instance(
    name: str,
    *,
    modpack_id: str = "",
    modpack_version: str = "",
    mc_version: str = MC_VERSION,
    forge_version: str = FORGE_VERSION,
    server_ip: str = "",
    server_port: int = 25565,
    source: str = "local",
    instance_id: str = "",
) -> GameInstance:
    from datetime import datetime, timezone

    iid = instance_id or _slug(name)
    base = iid
    n = 2
    while (instances_root() / iid).exists():
        iid = f"{base}-{n}"
        n += 1

    mv = mc_version or MC_VERSION
    fv_raw = forge_version or FORGE_VERSION
    from launcher.core.installer import forge_profile_id, normalize_forge_install_version

    fv = normalize_forge_install_version(mv, fv_raw)
    inst = GameInstance(
        id=iid,
        name=name or iid,
        mc_version=mv,
        forge_version=fv,
        forge_profile=forge_profile_id(mv, fv),
        modpack_id=modpack_id,
        modpack_version=modpack_version,
        server_ip=server_ip,
        server_port=server_port or 25565,
        source=source,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    inst.ensure_dirs()
    inst.save_meta()
    return inst


def _forge_profile(mc_version: str, forge_version: str) -> str:
    """Map forge version string → launcher profile id (e.g. 1.20.1-forge-47.4.10)."""
    from launcher.core.installer import forge_profile_id, normalize_forge_install_version

    mv = (mc_version or MC_VERSION).strip()
    install = normalize_forge_install_version(mv, forge_version or FORGE_VERSION)
    return forge_profile_id(mv, install)


def delete_instance(instance_id: str) -> None:
    path = instances_root() / instance_id
    others = [p.name for p in instances_root().iterdir() if p.is_dir() and p.name != instance_id]
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    if get_active_id() == instance_id:
        if others:
            activate_instance(others[0])
        else:
            ensure_default_instance()
            activate_instance("default")


def apply_instance_to_config(cfg: LauncherConfig, inst: GameInstance) -> None:
    """Persist active instance + optional server override from pack."""
    cfg.active_instance_id = inst.id
    # Keep a mirror list for UI without scanning disk only
    entries = []
    for i in list_instances():
        entries.append({"id": i.id, "name": i.name, "modpack_id": i.modpack_id, "modpack_version": i.modpack_version})
    cfg.instances = entries
    if inst.server_ip:
        cfg.server_ip = inst.server_ip
        cfg.server_port = int(inst.server_port or 25565)
    cfg.save()
