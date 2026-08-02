from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from launcher.config import mods_source_dir, minecraft_dir


StatusCb = Callable[[str], None]


def instance_mods_dir() -> Path:
    path = minecraft_dir() / "mods"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_bundled_mods() -> list[Path]:
    src = mods_source_dir()
    if not src.exists():
        return []
    return sorted(p for p in src.glob("*.jar") if p.is_file())


def sync_mods(on_status: Optional[StatusCb] = None) -> int:
    """
    Mirror bundled mods/ into the isolated instance mods folder.
    Removes jars that are no longer in the pack so the SMP stays consistent.
    """
    src = mods_source_dir()
    dst = instance_mods_dir()
    if not src.exists():
        raise FileNotFoundError(f"Pasta de mods não encontrada: {src}")

    bundled = {p.name: p for p in list_bundled_mods()}
    if on_status:
        on_status(f"Sincronizando {len(bundled)} mods do SMP…")

    # Remove stale jars
    for existing in list(dst.glob("*.jar")):
        if existing.name not in bundled:
            existing.unlink(missing_ok=True)

    copied = 0
    for name, src_path in bundled.items():
        target = dst / name
        need_copy = True
        if target.exists():
            try:
                if target.stat().st_size == src_path.stat().st_size:
                    need_copy = False
            except OSError:
                need_copy = True
        if need_copy:
            shutil.copy2(src_path, target)
            copied += 1
    if on_status:
        on_status(f"Mods prontos ({len(bundled)} no pack, {copied} atualizados).")
    return len(bundled)
