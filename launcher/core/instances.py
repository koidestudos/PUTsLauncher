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
    source: str = "local"  # local | r2 | bundled
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
    return _active_id or "puts-smp"


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
    """Migrate legacy ~/MinecraftPUTS/minecraft into instances/puts-smp if needed."""
    from datetime import datetime, timezone

    root = instances_root()
    default_id = "puts-smp"
    default_root = root / default_id
    legacy_mc = puts_home() / "minecraft"

    if not default_root.exists():
        default_root.mkdir(parents=True, exist_ok=True)
        # Move legacy install if present and instances empty of this id
        if legacy_mc.exists() and not (default_root / "minecraft").exists():
            try:
                shutil.move(str(legacy_mc), str(default_root / "minecraft"))
            except Exception:
                # Fallback: copy tree reference by renaming attempt failed — leave legacy
                (default_root / "minecraft").mkdir(parents=True, exist_ok=True)
        inst = GameInstance(
            id=default_id,
            name="PUTs SMP",
            source="bundled",
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        inst.ensure_dirs()
        inst.save_meta()
        return inst

    inst = GameInstance.load(default_id)
    if inst is None:
        inst = GameInstance(id=default_id, name="PUTs SMP", source="bundled")
        inst.ensure_dirs()
        inst.save_meta()
    else:
        inst.ensure_dirs()
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
    fv = forge_version or FORGE_VERSION
    inst = GameInstance(
        id=iid,
        name=name or iid,
        mc_version=mv,
        forge_version=fv,
        forge_profile=_forge_profile(mv, fv),
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
    """Map forge version string → launcher profile id (e.g. 1.18.2-forge-40.3.11)."""
    fv = (forge_version or FORGE_VERSION).strip()
    mv = (mc_version or MC_VERSION).strip()
    if "-forge-" in fv:
        return fv
    if fv.startswith(mv + "-"):
        return f"{mv}-forge-{fv[len(mv) + 1 :]}"
    if fv == FORGE_VERSION:
        return FORGE_PROFILE
    return f"{mv}-forge-{fv}"


def delete_instance(instance_id: str) -> None:
    path = instances_root() / instance_id
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    if get_active_id() == instance_id:
        ensure_default_instance()
        activate_instance("puts-smp")


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
