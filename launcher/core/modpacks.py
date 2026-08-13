from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from launcher.config import FORGE_VERSION, MC_VERSION, cache_dir
from launcher.core.instances import GameInstance, create_instance
from launcher.core.progress import ProgressTracker

USER_AGENT = "PUTsLauncher/1.4"
INDEX_ASSET_NAMES = ("index.json", "modpacks.json", "catalog.json")


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
            extra={
                k: v
                for k, v in data.items()
                if k
                not in {
                    "id",
                    "slug",
                    "name",
                    "version",
                    "mc_version",
                    "minecraft",
                    "loader",
                    "loader_version",
                    "forge_version",
                    "description",
                    "icon_url",
                    "download_url",
                    "url",
                    "sha256",
                    "server_ip",
                    "server_port",
                }
            },
        )


def _http_get(url: str, timeout: int = 30, accept: str = "application/json") -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Cache-Control": "no-cache",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:48] or "modpack")


def parse_github_repo(source: str) -> Optional[tuple[str, str]]:
    """
    Accept:
      owner/repo
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      https://api.github.com/repos/owner/repo/...
    """
    text = (source or "").strip().rstrip("/")
    if not text:
        return None
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        owner, repo = text.split("/", 1)
        return owner, repo.removesuffix(".git")

    try:
        parsed = urlparse(text)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    parts = [p for p in (parsed.path or "").split("/") if p]
    if host in {"github.com", "www.github.com"} and len(parts) >= 2:
        return parts[0], parts[1].removesuffix(".git")
    if host == "api.github.com" and len(parts) >= 3 and parts[0] == "repos":
        return parts[1], parts[2].removesuffix(".git")
    return None


def _packs_from_index_payload(data: Any, asset_urls: Optional[dict[str, str]] = None) -> list[ModpackInfo]:
    items = data.get("modpacks") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError('Índice inválido: esperado { "modpacks": [ ... ] }')
    packs: list[ModpackInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pack = ModpackInfo.from_dict(item)
        # Resolve bare asset filenames against release assets
        if asset_urls and pack.download_url and "://" not in pack.download_url:
            key = Path(pack.download_url).name
            pack.download_url = asset_urls.get(key) or asset_urls.get(pack.download_url) or pack.download_url
        if pack.id and pack.download_url:
            packs.append(pack)
    return packs


def _packs_from_release_zips(releases: list[dict[str, Any]]) -> list[ModpackInfo]:
    packs: list[ModpackInfo] = []
    seen: set[str] = set()
    for rel in releases:
        tag = str(rel.get("tag_name") or "").strip()
        name = str(rel.get("name") or tag or "Modpack").strip()
        body = str(rel.get("body") or "").strip()
        # Optional YAML-ish / JSON block in body is ignored; keep short description
        description = body.split("\n\n", 1)[0][:280] if body else ""
        assets = rel.get("assets") or []
        if not isinstance(assets, list):
            continue
        zips = [
            a
            for a in assets
            if isinstance(a, dict) and str(a.get("name") or "").lower().endswith(".zip")
        ]
        for asset in zips:
            asset_name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "").strip()
            if not url:
                continue
            stem = Path(asset_name).stem
            pack_id = _slug(stem if len(zips) > 1 else (tag or stem))
            if pack_id in seen:
                pack_id = _slug(f"{tag}-{stem}")
            seen.add(pack_id)
            packs.append(
                ModpackInfo(
                    id=pack_id,
                    name=name if len(zips) == 1 else f"{name} ({stem})",
                    version=tag.lstrip("v") or "1.0.0",
                    description=description,
                    download_url=url,
                    extra={"github_tag": tag, "asset_name": asset_name},
                )
            )
    return packs


def fetch_github_releases_catalog(owner: str, repo: str, timeout: int = 30) -> list[ModpackInfo]:
    """
    Load modpacks from GitHub Releases.

    Preference order:
      1) index.json / modpacks.json / catalog.json asset on the newest release that has one
      2) otherwise every .zip asset across releases becomes a modpack entry
    """
    api = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=40"
    try:
        raw = _http_get(api, timeout=timeout, accept="application/vnd.github+json")
    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError(f"Repositório não encontrado: {owner}/{repo}") from exc
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Falha ao falar com o GitHub: {exc.reason}") from exc

    releases = json.loads(raw.decode("utf-8"))
    if not isinstance(releases, list):
        raise ValueError("Resposta inválida da GitHub Releases API")
    if not releases:
        raise ValueError(f"Nenhum release em {owner}/{repo}")

    # Prefer explicit catalog JSON on the newest matching release
    for rel in releases:
        assets = rel.get("assets") or []
        if not isinstance(assets, list):
            continue
        by_name = {
            str(a.get("name") or "").lower(): a
            for a in assets
            if isinstance(a, dict) and a.get("browser_download_url")
        }
        index_asset = None
        for wanted in INDEX_ASSET_NAMES:
            if wanted in by_name:
                index_asset = by_name[wanted]
                break
        if not index_asset:
            continue
        asset_urls = {
            str(a.get("name") or ""): str(a.get("browser_download_url") or "")
            for a in assets
            if isinstance(a, dict)
        }
        idx_url = str(index_asset["browser_download_url"])
        idx_raw = _http_get(idx_url, timeout=timeout, accept="application/json,text/plain,*/*")
        data = json.loads(idx_raw.decode("utf-8"))
        packs = _packs_from_index_payload(data, asset_urls=asset_urls)
        if packs:
            return packs

    packs = _packs_from_release_zips(releases)
    if not packs:
        raise ValueError(
            f"Nenhum modpack em {owner}/{repo}. "
            "Publique um release com index.json ou arquivos .zip."
        )
    return packs


def fetch_modpack_index(source: str, timeout: int = 30) -> list[ModpackInfo]:
    """
    Download catalog from GitHub Releases (owner/repo) or a direct index.json URL.
    """
    text = (source or "").strip()
    if not text:
        raise ValueError(
            "Catálogo vazio. Configure em Opções → GitHub Releases "
            "(ex.: dono/repo ou URL do index.json do release)."
        )

    repo = parse_github_repo(text)
    # Bare owner/repo, or github.com URL without a direct .json asset
    if repo and not text.lower().endswith(".json"):
        # github.com/owner/repo/releases/download/tag/index.json still ends with .json
        return fetch_github_releases_catalog(repo[0], repo[1], timeout=timeout)

    # Direct HTTPS JSON (release asset or raw)
    if text.startswith("http://") or text.startswith("https://"):
        raw = _http_get(text, timeout=timeout, accept="application/json,text/plain,*/*")
        data = json.loads(raw.decode("utf-8"))
        return _packs_from_index_payload(data)

    if repo:
        return fetch_github_releases_catalog(repo[0], repo[1], timeout=timeout)

    raise ValueError(
        "Fonte inválida. Use dono/repo (ex.: koidestudos/PUTsModpacks) "
        "ou a URL HTTPS do index.json no Release."
    )


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
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream,*/*"})
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
                    tracker.set_counts(downloaded, total, f"Baixando pack… {downloaded // (1024 * 1024)} MB")
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
        tops = {n.split("/")[0] for n in names if n and not n.endswith("/")}
        strip_root = ""
        if len(tops) == 1:
            root = next(iter(tops))
            if any(n.startswith(root + "/mods/") or n == root + "/mods" for n in names):
                strip_root = root + "/"

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
            top = name.split("/", 1)[0]
            if top not in {
                "mods",
                "config",
                "defaultconfigs",
                "resourcepacks",
                "shaderpacks",
                "options.txt",
                "optionsof.txt",
                "servers.dat",
            }:
                if not name.startswith("mods/") and name != "options.txt":
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


def install_modpack(
    pack: ModpackInfo,
    tracker: Optional[ProgressTracker] = None,
    instance_name: str = "",
) -> GameInstance:
    """Download pack zip (GitHub Releases asset) and create a local instance."""
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

    from launcher.core.instances import GameInstance

    existing = GameInstance.load(pack.id) if pack.id else None
    if existing:
        inst = existing
        inst.name = instance_name or pack.name or inst.name
        inst.modpack_id = pack.id
        inst.modpack_version = pack.version
        inst.mc_version = pack.mc_version or inst.mc_version
        inst.forge_version = pack.loader_version or inst.forge_version
        inst.server_ip = pack.server_ip
        inst.server_port = pack.server_port
        inst.source = "github"
        from launcher.core.installer import forge_profile_id, normalize_forge_install_version

        inst.forge_version = normalize_forge_install_version(inst.mc_version, inst.forge_version)
        inst.forge_profile = forge_profile_id(inst.mc_version, inst.forge_version)
        inst.ensure_dirs()
        inst.save_meta()
    else:
        inst = create_instance(
            instance_name or pack.name,
            modpack_id=pack.id,
            modpack_version=pack.version,
            mc_version=pack.mc_version or MC_VERSION,
            forge_version=pack.loader_version or FORGE_VERSION,
            server_ip=pack.server_ip,
            server_port=pack.server_port,
            source="github",
            instance_id=pack.id,
        )
    install_modpack_zip(zip_path, inst, tracker=tracker)
    inst.modpack_id = pack.id
    inst.modpack_version = pack.version
    inst.server_ip = pack.server_ip
    inst.server_port = pack.server_port
    inst.source = "github"
    inst.save_meta()
    return inst


# Back-compat alias (old R2-era name)
install_modpack_from_r2 = install_modpack
