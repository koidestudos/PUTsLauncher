from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from launcher.config import mods_source_dir, minecraft_dir
from launcher.core.progress import ProgressTracker


def instance_mods_dir() -> Path:
    path = minecraft_dir() / "mods"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_bundled_mods() -> list[Path]:
    src = mods_source_dir()
    if not src.exists():
        return []
    return sorted(p for p in src.glob("*.jar") if p.is_file())


def sync_mods(tracker: Optional[ProgressTracker] = None) -> int:
    """Mirror bundled mods/ into MinecraftPUTS/minecraft/mods."""
    src = mods_source_dir()
    dst = instance_mods_dir()
    if not src.exists():
        raise FileNotFoundError(
            f"Pasta de mods não encontrada ao lado do launcher:\n{src}"
        )

    bundled = {p.name: p for p in list_bundled_mods()}
    total = max(len(bundled), 1)
    if tracker:
        tracker.set_phase("mods", f"Sincronizando {len(bundled)} mods…")

    for existing in list(dst.glob("*.jar")):
        if existing.name not in bundled:
            existing.unlink(missing_ok=True)

    copied = 0
    for i, (name, src_path) in enumerate(bundled.items(), start=1):
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
        if tracker:
            tracker.set_counts(i, total, f"Mods: {name}")

    if tracker:
        tracker.complete_phase(f"Mods prontos ({len(bundled)}, {copied} novos/atualizados)")
    return len(bundled)
