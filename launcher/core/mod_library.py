from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from launcher.config import MC_VERSION
from launcher.core.instances import GameInstance, create_instance
from launcher.core.loaders import (
    loader_profile_id,
    normalize_loader_name,
    normalize_loader_version,
    require_supported_loader,
)
from launcher.core.modpacks import USER_AGENT, _contained, _safe_instance_dir, _slug, is_trusted_url
from launcher.core.pack_import import (
    CURSE_API,
    MODRINTH_API,
    _forgecdn_urls,
    _parallel_download_jobs,
    _wipe_instance_mods,
)
from launcher.core.progress import ProgressTracker

# CurseForge modLoaderType enum
_CF_LOADER_TYPE = {
    "forge": 1,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}


@dataclass
class LibraryMod:
    """A searchable mod from CurseForge or Modrinth."""

    platform: str  # modrinth | curseforge
    project_id: str
    slug: str
    name: str
    description: str = ""
    icon_url: str = ""
    downloads: int = 0
    loaders: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelectedMod:
    platform: str
    project_id: str
    slug: str
    name: str
    version_id: str = ""
    file_name: str = ""
    download_urls: list[str] = field(default_factory=list)


def _http_json(url: str, timeout: int = 30) -> Any:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} em {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Falha de rede: {exc.reason}") from exc


def search_modrinth_mods(
    query: str,
    *,
    mc_version: str,
    loader: str,
    limit: int = 20,
) -> list[LibraryMod]:
    loader = normalize_loader_name(loader)
    facets = [
        ["project_type:mod"],
        [f"versions:{mc_version}"],
        [f"categories:{loader}"],
    ]
    params = urlencode(
        {
            "query": (query or "").strip() or " ",
            "limit": str(max(1, min(limit, 40))),
            "index": "relevance",
            "facets": json.dumps(facets, separators=(",", ":")),
        }
    )
    data = _http_json(f"{MODRINTH_API}/search?{params}")
    hits = data.get("hits") if isinstance(data, dict) else None
    out: list[LibraryMod] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        out.append(
            LibraryMod(
                platform="modrinth",
                project_id=str(hit.get("project_id") or hit.get("slug") or ""),
                slug=str(hit.get("slug") or ""),
                name=str(hit.get("title") or hit.get("slug") or ""),
                description=str(hit.get("description") or "")[:180],
                icon_url=str(hit.get("icon_url") or ""),
                downloads=int(hit.get("downloads") or 0),
                loaders=[normalize_loader_name(str(x)) for x in (hit.get("categories") or []) if x],
            )
        )
    return out


def search_curseforge_mods(
    query: str,
    *,
    mc_version: str,
    loader: str,
    limit: int = 20,
) -> list[LibraryMod]:
    loader = normalize_loader_name(loader)
    params = {
        "gameId": "432",
        "classId": "6",  # Mods
        "searchFilter": (query or "").strip(),
        "gameVersion": mc_version,
        "pageSize": str(max(1, min(limit, 40))),
        "sortField": "2",  # Popularity
        "sortOrder": "desc",
    }
    lt = _CF_LOADER_TYPE.get(loader)
    if lt:
        params["modLoaderType"] = str(lt)
    data = _http_json(f"{CURSE_API}/mods/search?{urlencode(params)}")
    mods = data.get("data") if isinstance(data, dict) else None
    out: list[LibraryMod] = []
    for mod in mods or []:
        if not isinstance(mod, dict):
            continue
        logo = ""
        if isinstance(mod.get("logo"), dict):
            logo = str(mod["logo"].get("thumbnailUrl") or mod["logo"].get("url") or "")
        out.append(
            LibraryMod(
                platform="curseforge",
                project_id=str(mod.get("id") or ""),
                slug=str(mod.get("slug") or ""),
                name=str(mod.get("name") or mod.get("slug") or ""),
                description=str(mod.get("summary") or "")[:180],
                icon_url=logo,
                downloads=int((mod.get("downloadCount") or 0)),
                loaders=[loader],
                extra={"id": mod.get("id")},
            )
        )
    return out


def search_mods(
    query: str,
    *,
    mc_version: str,
    loader: str,
    sources: Optional[list[str]] = None,
    limit: int = 16,
) -> list[LibraryMod]:
    """Search Modrinth + CurseForge in parallel and merge (Modrinth first)."""
    sources = sources or ["modrinth", "curseforge"]
    results: list[LibraryMod] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {}
        if "modrinth" in sources:
            futs[pool.submit(search_modrinth_mods, query, mc_version=mc_version, loader=loader, limit=limit)] = "mr"
        if "curseforge" in sources:
            futs[pool.submit(search_curseforge_mods, query, mc_version=mc_version, loader=loader, limit=limit)] = "cf"
        by_src: dict[str, list[LibraryMod]] = {}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                by_src[key] = fut.result()
            except Exception:
                by_src[key] = []
    # Interleave a bit so both catalogs show up
    mr = by_src.get("mr") or []
    cf = by_src.get("cf") or []
    seen: set[str] = set()
    i = j = 0
    while i < len(mr) or j < len(cf):
        if i < len(mr):
            m = mr[i]
            i += 1
            key = f"mr:{m.slug}"
            if key not in seen:
                seen.add(key)
                results.append(m)
        if j < len(cf):
            m = cf[j]
            j += 1
            key = f"cf:{m.slug}"
            if key not in seen:
                seen.add(key)
                results.append(m)
    return results[: max(limit * 2, 20)]


def resolve_modrinth_mod_file(
    project_id: str,
    *,
    mc_version: str,
    loader: str,
) -> SelectedMod:
    loader = normalize_loader_name(loader)
    versions = _http_json(
        f"{MODRINTH_API}/project/{quote(project_id)}/version?"
        + urlencode(
            {
                "game_versions": json.dumps([mc_version]),
                "loaders": json.dumps([loader]),
            }
        )
    )
    if not isinstance(versions, list) or not versions:
        versions = _http_json(f"{MODRINTH_API}/project/{quote(project_id)}/version")
    if not isinstance(versions, list) or not versions:
        raise ValueError(f"Sem versões Modrinth para {project_id}")

    picked = None
    for v in versions:
        loaders = [normalize_loader_name(str(x)) for x in (v.get("loaders") or [])]
        gvs = [str(x) for x in (v.get("game_versions") or [])]
        if loader in loaders and mc_version in gvs:
            picked = v
            break
    if picked is None:
        picked = versions[0]

    files = picked.get("files") or []
    primary = next((f for f in files if f.get("primary")), None) or (files[0] if files else None)
    if not primary or not primary.get("url"):
        raise ValueError(f"Versão Modrinth sem arquivo: {project_id}")
    proj = _http_json(f"{MODRINTH_API}/project/{quote(project_id)}")
    return SelectedMod(
        platform="modrinth",
        project_id=str(proj.get("id") or project_id),
        slug=str(proj.get("slug") or project_id),
        name=str(proj.get("title") or project_id),
        version_id=str(picked.get("id") or ""),
        file_name=str(primary.get("filename") or "mod.jar"),
        download_urls=[str(primary["url"])],
    )


def resolve_curseforge_mod_file(
    project_id: str,
    *,
    mc_version: str,
    loader: str,
) -> SelectedMod:
    loader = normalize_loader_name(loader)
    mid = int(project_id)
    params = {
        "gameVersion": mc_version,
        "pageSize": "20",
    }
    lt = _CF_LOADER_TYPE.get(loader)
    if lt:
        params["modLoaderType"] = str(lt)
    files = _http_json(f"{CURSE_API}/mods/{mid}/files?{urlencode(params)}").get("data") or []
    if not files:
        files = _http_json(f"{CURSE_API}/mods/{mid}/files?pageSize=20").get("data") or []
    if not files:
        raise ValueError(f"Sem arquivos CurseForge para #{mid}")

    preferred = {loader, "forge", "fabric", "neoforge", "quilt"}
    file_data = None
    for f in files:
        gvs = {str(x).lower() for x in (f.get("gameVersions") or [])}
        if mc_version in gvs and (gvs & preferred):
            file_data = f
            break
    if file_data is None:
        file_data = files[0]

    fid = int(file_data["id"])
    fname = str(file_data.get("fileName") or f"{fid}.jar")
    urls: list[str] = []
    if file_data.get("downloadUrl"):
        urls.append(str(file_data["downloadUrl"]))
    urls.extend(_forgecdn_urls(fid, fname))
    mod = _http_json(f"{CURSE_API}/mods/{mid}").get("data") or {}
    return SelectedMod(
        platform="curseforge",
        project_id=str(mid),
        slug=str(mod.get("slug") or mid),
        name=str(mod.get("name") or fname),
        version_id=str(fid),
        file_name=fname,
        download_urls=[u for u in urls if u],
    )


def resolve_library_mod(
    mod: LibraryMod,
    *,
    mc_version: str,
    loader: str,
) -> SelectedMod:
    if mod.platform == "modrinth":
        return resolve_modrinth_mod_file(mod.project_id or mod.slug, mc_version=mc_version, loader=loader)
    return resolve_curseforge_mod_file(mod.project_id, mc_version=mc_version, loader=loader)


def create_custom_modpack(
    *,
    name: str,
    mc_version: str,
    loader: str,
    mods: list[SelectedMod],
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> GameInstance:
    """
    Create a local instance and download the selected mods into mods/.
    """
    if not mods:
        raise ValueError("Adicione pelo menos um mod ao pack.")
    loader_name = require_supported_loader(loader)
    mv = (mc_version or MC_VERSION).strip() or MC_VERSION
    # Let install_loader pick latest when version blank
    loader_ver = normalize_loader_version(loader_name, mv, "")

    pack_id = _slug(name) or "custom-pack"
    inst = create_instance(
        name.strip() or pack_id,
        modpack_id=pack_id,
        modpack_version="custom",
        mc_version=mv,
        forge_version=loader_ver,
        loader=loader_name,
        source="custom",
        instance_id=pack_id,
    )
    mc = _safe_instance_dir(inst.minecraft_path, inst.root)
    mc.mkdir(parents=True, exist_ok=True)
    _wipe_instance_mods(mc)

    if tracker:
        tracker.set_phase("mods", f"Baixando {len(mods)} mods…")

    # Resolve any that still need URLs
    resolved: list[SelectedMod] = []
    for mod in mods:
        if cancel_event is not None and cancel_event.is_set():
            from launcher.core.installer import CancelledError

            raise CancelledError("Cancelado.")
        if mod.download_urls:
            resolved.append(mod)
            continue
        if mod.platform == "modrinth":
            resolved.append(
                resolve_modrinth_mod_file(mod.project_id or mod.slug, mc_version=mv, loader=loader_name)
            )
        else:
            resolved.append(
                resolve_curseforge_mod_file(mod.project_id, mc_version=mv, loader=loader_name)
            )

    jobs: list[tuple[list[str], Path]] = []
    for mod in resolved:
        fname = Path(mod.file_name or f"{mod.slug}.jar").name
        if not fname.lower().endswith(".jar"):
            fname = f"{fname}.jar"
        dest = _contained(mc / "mods" / fname, mc)
        urls = [u for u in mod.download_urls if is_trusted_url(u)]
        if not urls:
            raise ValueError(f"Sem download HTTPS para {mod.name}")
        jobs.append((urls, dest))

    _parallel_download_jobs(jobs, tracker=tracker, cancel_event=cancel_event, label="Mods")

    try:
        from launcher.core.skins_mod import ensure_elyby_skins_mod

        ensure_elyby_skins_mod(mc, mc_version=mv, loader=loader_name, tracker=tracker)
    except Exception as exc:
        if tracker:
            tracker.set_detail(f"Aviso skins Ely.by: {exc}")

    inst.loader = loader_name
    inst.mc_version = mv
    inst.forge_version = loader_ver
    inst.forge_profile = loader_profile_id(loader_name, mv, loader_ver)
    inst.source = "custom"
    inst.extra["custom_mods"] = [
        {
            "platform": m.platform,
            "project_id": m.project_id,
            "slug": m.slug,
            "name": m.name,
            "version_id": m.version_id,
            "file_name": m.file_name,
        }
        for m in resolved
    ]
    inst.save_meta()

    try:
        from launcher.core.cache_cleanup import cleanup_cache

        cleanup_cache()
    except Exception:
        pass

    if tracker:
        tracker.complete_phase(f"Pack pronto — {len(resolved)} mods")
    return inst
