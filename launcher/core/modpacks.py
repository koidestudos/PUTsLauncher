from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen

from launcher.config import FORGE_VERSION, MC_VERSION, cache_dir
from launcher.core.instances import GameInstance, create_instance
from launcher.core.progress import ProgressTracker


@dataclass
class ModpackInfo:
    id: str
    name: str
    version: str = "1.0.0"
    mc_version: str = MC_VERSION
    loader: str = "forge"
    loader_version: str = FORGE_VERSION
    description: str = ""
    icon_url: str = ""
    download_url: str = ""
    sha256: str = ""
    server_ip: str = ""
    server_port: int = 25565
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModpackInfo":
        return cls(
            id=str(data.get("id") or data.get("slug") or "").strip(),
            name=str(data.get("name") or data.get("id") or "Modpack").strip(),
            version=str(data.get("version") or "1.0.0").strip(),
            mc_version=str(data.get("mc_version") or data.get("minecraft") or MC_VERSION).strip(),
            loader=str(data.get("loader") or "forge").strip().lower(),
            loader_version=str(
                data.get("loader_version") or data.get("forge_version") or FORGE_VERSION
            ).strip(),
            description=str(data.get("description") or "").strip(),
            icon_url=str(data.get("icon_url") or "").strip(),
            download_url=str(data.get("download_url") or data.get("url") or "").strip(),
            sha256=str(data.get("sha256") or "").strip().lower(),
            server_ip=str(data.get("server_ip") or "").strip(),
            server_port=int(data.get("server_port") or 25565),
            extra={k: v for k, v in data.items() if k not in {
                "id", "slug", "name", "version", "mc_version", "minecraft", "loader",
                "loader_version", "forge_version", "description", "icon_url",
                "download_url", "url", "sha256", "server_ip", "server_port",
            }},
        )


def fetch_modpack_index(index_url: str, timeout: int = 30) -> list[ModpackInfo]:
    """Download catalog JSON from Cloudflare R2 (or any HTTPS URL)."""
    url = (index_url or "").strip()
    if not url:
        raise ValueError("URL do catálogo R2 vazia. Configure em Opções → Catálogo de modpacks.")
    req = Request(url, headers={"User-Agent": "PUTsLauncher/1.4", "Cache-Control": "no-cache"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    items = data.get("modpacks") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Índice inválido: esperado { \"modpacks\": [ ... ] }")
    packs = [ModpackInfo.from_dict(x) for x in items if isinstance(x, dict)]
    packs = [p for p in packs if p.id and p.download_url]
    return packs


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: Path, tracker: Optional[ProgressTracker] = None, timeout: int = 120) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "PUTsLauncher/1.4"})
    with urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        with dest.open("wb") as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if tracker and total > 0:
                    tracker.set_counts(downloaded, total, f"Baixando pack… {downloaded // (1024*1024)} MB")
    return dest


def install_modpack_zip(zip_path: Path, instance: GameInstance, tracker: Optional[ProgressTracker] = None) -> None:
    """
    Extract pack into instance minecraft folder.
    Accepts zip rooted at mods/ or with a single top-level folder.
    """
    mc = instance.minecraft_path
    mc.mkdir(parents=True, exist_ok=True)
    if tracker:
        tracker.set_phase("mods", "Extraindo modpack…")

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]
        # Detect single root folder
        tops = {n.split("/")[0] for n in names if n and not n.endswith("/")}
        strip_root = ""
        if len(tops) == 1:
            root = next(iter(tops))
            # If zip is "MyPack/mods/..." strip MyPack
            if any(n.startswith(root + "/mods/") or n == root + "/mods" for n in names):
                strip_root = root + "/"

        # Clear old mods when installing a pack (fresh sync)
        mods_dir = mc / "mods"
        if mods_dir.exists():
            for jar in mods_dir.glob("*.jar"):
                jar.unlink(missing_ok=True)
        mods_dir.mkdir(parents=True, exist_ok=True)

        members = [i for i in zf.infolist() if not i.filename.endswith("/")]
        total = max(len(members), 1)
        for i, info in enumerate(members, start=1):
            name = info.filename.replace("\\", "/")
            if strip_root and name.startswith(strip_root):
                name = name[len(strip_root) :]
            if not name or ".." in Path(name).parts:
                continue
            # Only extract gameplay content
            top = name.split("/", 1)[0]
            if top not in {"mods", "config", "defaultconfigs", "resourcepacks", "shaderpacks", "options.txt", "optionsof.txt", "servers.dat"}:
                # Allow files directly under mods-like paths already checked
                if not name.startswith("mods/") and name != "options.txt":
                    # Also allow pack.meta.json at root for bookkeeping
                    if name not in {"pack.meta.json", "manifest.json"}:
                        continue
            target = (mc / name).resolve()
            if not str(target).startswith(str(mc.resolve())):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            if tracker:
                tracker.set_counts(i, total, f"Extraindo: {Path(name).name}")

    instance.save_meta()
    if tracker:
        tracker.complete_phase("Modpack extraído")


def install_modpack_from_r2(
    pack: ModpackInfo,
    tracker: Optional[ProgressTracker] = None,
    instance_name: str = "",
) -> GameInstance:
    """Download pack zip from R2 and create/update a local instance."""
    if not pack.download_url:
        raise ValueError(f"Modpack {pack.id} sem download_url")

    if tracker:
        tracker.set_phase("mods", f"Baixando {pack.name}…")

    cache = cache_dir() / "modpacks"
    cache.mkdir(parents=True, exist_ok=True)
    zip_path = cache / f"{pack.id}-{pack.version}.zip"
    download_file(pack.download_url, zip_path, tracker=tracker)

    if pack.sha256:
        digest = _sha256_file(zip_path)
        if digest != pack.sha256.lower():
            zip_path.unlink(missing_ok=True)
            raise RuntimeError(f"SHA256 inválido para {pack.id} (esperado {pack.sha256[:12]}…)")

    inst = create_instance(
        instance_name or pack.name,
        modpack_id=pack.id,
        modpack_version=pack.version,
        mc_version=pack.mc_version or MC_VERSION,
        forge_version=pack.loader_version or FORGE_VERSION,
        server_ip=pack.server_ip,
        server_port=pack.server_port,
        source="r2",
        instance_id=pack.id,
    )
    install_modpack_zip(zip_path, inst, tracker=tracker)
    inst.modpack_id = pack.id
    inst.modpack_version = pack.version
    inst.server_ip = pack.server_ip
    inst.server_port = pack.server_port
    inst.save_meta()
    return inst
