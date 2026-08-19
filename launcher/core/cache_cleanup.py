from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from launcher.config import cache_dir

# Soft cap for ~/MinecraftPUTS/cache — packs are big; keep only what's useful.
DEFAULT_CACHE_BUDGET = 350 * 1024 * 1024  # 350 MiB
# Drop imported pack archives soon after install; keep catalog zips a bit longer.
IMPORTED_MAX_AGE_SEC = 2 * 60 * 60  # 2h
CATALOG_MAX_AGE_SEC = 7 * 24 * 60 * 60  # 7d
SKINS_MAX_AGE_SEC = 30 * 24 * 60 * 60  # 30d


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            out.append(path)
    return out


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def cache_usage_bytes(root: Optional[Path] = None) -> int:
    base = Path(root or cache_dir())
    return sum(_file_size(p) for p in _iter_files(base))


def forget_cache_file(path: Path) -> bool:
    """Delete one cache file if it lives under cache_dir()."""
    try:
        cache = cache_dir().resolve()
        target = path.resolve()
        if cache not in target.parents and target != cache:
            return False
        if target.is_file():
            target.unlink(missing_ok=True)
            return True
    except OSError:
        return False
    return False


def cleanup_cache(
    *,
    budget: int = DEFAULT_CACHE_BUDGET,
    now: Optional[float] = None,
) -> dict:
    """
    Shrink ~/MinecraftPUTS/cache:

    1. Drop empty / tiny junk files
    2. Age out imported packs and old catalog zips
    3. If still over ``budget``, delete oldest files first (LRU by mtime)

    Returns a small report: ``{"removed": N, "freed_bytes": N, "remaining_bytes": N}``.
    """
    base = cache_dir()
    base.mkdir(parents=True, exist_ok=True)
    stamp = now if now is not None else time.time()
    removed = 0
    freed = 0

    def drop(path: Path) -> None:
        nonlocal removed, freed
        size = _file_size(path)
        try:
            path.unlink(missing_ok=True)
            removed += 1
            freed += size
        except OSError:
            pass

    files = _iter_files(base)
    survivors: list[tuple[float, int, Path]] = []

    for path in files:
        size = _file_size(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        age = stamp - mtime
        rel = path.relative_to(base).as_posix()

        # Junk / incomplete
        if size <= 0:
            drop(path)
            continue

        # Imported CF/MR archives — disposable after install
        if rel.startswith("imported_packs/"):
            if age >= IMPORTED_MAX_AGE_SEC or size > 80 * 1024 * 1024:
                drop(path)
                continue

        # Catalog zip cache
        if rel.startswith("modpacks/"):
            if age >= CATALOG_MAX_AGE_SEC:
                drop(path)
                continue

        # Skin jars: keep recent, drop ancient
        if rel.startswith("skins_mod/") and age >= SKINS_MAX_AGE_SEC:
            drop(path)
            continue

        # Legacy texture dumps
        if path.name.startswith(("texture_", "head_")) and age > 86400:
            drop(path)
            continue

        survivors.append((mtime, size, path))

    # Enforce budget (oldest first)
    total = sum(s for _m, s, _p in survivors)
    if total > budget:
        survivors.sort(key=lambda t: t[0])  # oldest first
        for _mtime, size, path in survivors:
            if total <= budget:
                break
            drop(path)
            total -= size

    # Remove empty directories left behind
    for folder in sorted(base.rglob("*"), reverse=True):
        if folder.is_dir():
            try:
                next(folder.iterdir())
            except StopIteration:
                try:
                    folder.rmdir()
                except OSError:
                    pass
            except OSError:
                pass

    return {
        "removed": removed,
        "freed_bytes": freed,
        "remaining_bytes": cache_usage_bytes(base),
    }
