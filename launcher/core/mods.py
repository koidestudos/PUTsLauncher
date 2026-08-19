from __future__ import annotations

from pathlib import Path
from typing import Optional

from launcher.config import mods_source_dir, minecraft_dir
from launcher.core.progress import ProgressTracker


def instance_mods_dir() -> Path:
    path = minecraft_dir() / "mods"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_instance_mods() -> list[Path]:
    return sorted(p for p in instance_mods_dir().glob("*.jar") if p.is_file())


def list_bundled_mods() -> list[Path]:
    """Optional local /mods next to the EXE (dev only). Not shipped anymore."""
    src = mods_source_dir()
    if not src.exists():
        return []
    return sorted(p for p in src.glob("*.jar") if p.is_file())


def sync_mods(tracker: Optional[ProgressTracker] = None) -> int:
    """
    Ensure the active instance has mods.

    Packs come from GitHub Releases (+ Modpack). The launcher no longer ships
    a puts-smp /mods folder in the download.
    Always injects CustomSkinLoader (Ely.by) for offline skins.
    """
    try:
        from launcher.core.instances import GameInstance, get_active_id

        inst = GameInstance.load(get_active_id())
    except Exception:
        inst = None

    dst = instance_mods_dir()
    jars = list(dst.glob("*.jar"))
    n = len(jars)

    # GitHub / remote pack already extracted into the instance
    if inst and inst.source in {"github", "r2", "modrinth", "curseforge", "custom"}:
        if tracker:
            tracker.set_phase("mods", f"Modpack — {n} mods")
        if n == 0:
            raise FileNotFoundError(
                "Esta instância não tem mods.\n"
                "Instale de novo pelo + Modpack (link CurseForge/Modrinth ou catálogo GitHub)."
            )
        try:
            from launcher.core.skins_mod import ensure_elyby_skins_mod

            ensure_elyby_skins_mod(minecraft_dir(), mc_version=inst.mc_version or "", tracker=tracker)
        except Exception as exc:
            if tracker:
                tracker.set_detail(f"Aviso skins Ely.by: {exc}")
        n = len(list(dst.glob("*.jar")))
        if tracker:
            tracker.complete_phase(f"Mods do pack ({n})")
        return n

    if n:
        try:
            from launcher.core.skins_mod import ensure_elyby_skins_mod

            ensure_elyby_skins_mod(
                minecraft_dir(),
                mc_version=(inst.mc_version if inst else "") or "",
                tracker=tracker,
            )
        except Exception:
            pass
        n = len(list(dst.glob("*.jar")))
        if tracker:
            tracker.set_phase("mods", f"Usando {n} mods da instância")
            tracker.complete_phase("Mods OK")
        return n

    # Optional leftover: local /mods beside EXE for manual testing only
    bundled = list_bundled_mods()
    if bundled:
        import shutil

        if tracker:
            tracker.set_phase("mods", f"Copiando {len(bundled)} mods locais…")
        for i, src_path in enumerate(bundled, start=1):
            shutil.copy2(src_path, dst / src_path.name)
            if tracker:
                tracker.set_counts(i, len(bundled), f"Mods: {src_path.name}")
        try:
            from launcher.core.skins_mod import ensure_elyby_skins_mod

            ensure_elyby_skins_mod(minecraft_dir(), tracker=tracker)
        except Exception:
            pass
        if tracker:
            tracker.complete_phase(f"Mods locais ({len(list(dst.glob('*.jar')))})")
        return len(list(dst.glob("*.jar")))

    raise FileNotFoundError(
        "Nenhum modpack instalado nesta instância.\n"
        "Clique em + Modpack e instale pelo catálogo do GitHub Releases."
    )
