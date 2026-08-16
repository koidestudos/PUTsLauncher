from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from launcher.config import FORGE_VERSION, MC_VERSION, cache_dir
from launcher.core.installer import CancelledError, forge_profile_id, normalize_forge_install_version
from launcher.core.instances import GameInstance, create_instance, instances_root
from launcher.core.modpacks import (
    USER_AGENT,
    _contained,
    _require_safe,
    _safe_instance_dir,
    _sha256_file,
    _slug,
    download_file,
    is_trusted_url,
)
from launcher.core.progress import ProgressTracker

# Public CurseForge API proxy (no personal API key required).
CURSE_API = "https://api.curse.tools/v1/cf"
MODRINTH_API = "https://api.modrinth.com/v2"


@dataclass
class ImportRef:
    platform: str  # modrinth | curseforge
    slug: str
    version_hint: str = ""  # version id / number / file id
    raw_url: str = ""


def _http_json(url: str, timeout: int = 30, headers: Optional[dict[str, str]] = None) -> Any:
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    }
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} em {url}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Falha de rede: {exc.reason}") from exc


def _check_cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("Cancelado.")


def parse_pack_url(url: str) -> ImportRef:
    """
    Accept common CurseForge / Modrinth modpack page URLs.

    Examples:
      https://modrinth.com/modpack/better-mc-forge-bmc4
      https://modrinth.com/modpack/slug/version/1.0.0
      https://www.curseforge.com/minecraft/modpacks/all-the-mods-9
      https://www.curseforge.com/minecraft/modpacks/all-the-mods-9/files/7097953
    """
    text = (url or "").strip()
    if not text:
        raise ValueError("Cole um link de modpack do CurseForge ou Modrinth.")
    if not is_trusted_url(text):
        raise ValueError("Use um link https:// do CurseForge ou Modrinth.")

    parsed = urlparse(text)
    host = (parsed.netloc or "").lower()
    parts = [unquote(p) for p in (parsed.path or "").split("/") if p]

    if host in {"modrinth.com", "www.modrinth.com"}:
        # /modpack/{slug}[/version/{ver}]
        if len(parts) >= 2 and parts[0] == "modpack":
            slug = parts[1]
            version = ""
            if len(parts) >= 4 and parts[2] in {"version", "versions"}:
                version = parts[3]
            return ImportRef("modrinth", slug, version, text)
        raise ValueError("Link Modrinth inválido. Ex.: https://modrinth.com/modpack/nome-do-pack")

    if "curseforge.com" in host:
        # /minecraft/modpacks/{slug}[/files/{id}|/download/{id}]
        if len(parts) >= 3 and parts[0] == "minecraft" and parts[1] == "modpacks":
            slug = parts[2]
            file_id = ""
            if len(parts) >= 5 and parts[3] in {"files", "download"}:
                file_id = parts[4]
            return ImportRef("curseforge", slug, file_id, text)
        raise ValueError(
            "Link CurseForge inválido. Ex.: https://www.curseforge.com/minecraft/modpacks/nome-do-pack"
        )

    raise ValueError(
        "Link não reconhecido. Cole um URL do Modrinth (/modpack/…) "
        "ou CurseForge (/minecraft/modpacks/…)."
    )


def _forge_from_mr_deps(deps: dict[str, Any], mc_fallback: str = "") -> tuple[str, str, str]:
    """Return (mc_version, forge_install, loader_name)."""
    mc = str(deps.get("minecraft") or mc_fallback or MC_VERSION).strip()
    loader = "forge"
    forge_raw = ""
    for key in ("forge", "neoforge", "fabric-loader", "quilt-loader"):
        if key in deps and deps[key]:
            loader = key.replace("-loader", "").replace("fabric", "fabric").replace("quilt", "quilt")
            if key == "fabric-loader":
                loader = "fabric"
            elif key == "quilt-loader":
                loader = "quilt"
            elif key == "neoforge":
                loader = "neoforge"
            else:
                loader = "forge"
            forge_raw = str(deps[key]).strip()
            break
    if loader != "forge":
        raise ValueError(
            f"Este pack usa {loader}, e o PUTs Launcher só instala Forge por enquanto."
        )
    # deps forge can be "47.2.0" or "1.20.1-47.2.0"
    install = normalize_forge_install_version(mc, forge_raw or FORGE_VERSION)
    return mc, install, "forge"


def _forge_from_cf_manifest(manifest: dict[str, Any]) -> tuple[str, str, str]:
    mc_block = manifest.get("minecraft") or {}
    mc = str(mc_block.get("version") or MC_VERSION).strip()
    loaders = mc_block.get("modLoaders") or []
    forge_raw = ""
    for entry in loaders:
        if not isinstance(entry, dict):
            continue
        lid = str(entry.get("id") or "")
        low = lid.lower()
        if low.startswith("forge-"):
            forge_raw = lid.split("-", 1)[1]
            break
        if "fabric" in low or "quilt" in low or "neoforge" in low:
            raise ValueError(
                f"Este pack CurseForge usa {lid}, e o launcher só instala Forge."
            )
    if not forge_raw and loaders:
        # unknown loader
        raise ValueError("Não achei Forge no manifest deste modpack CurseForge.")
    install = normalize_forge_install_version(mc, forge_raw or FORGE_VERSION)
    return mc, install, "forge"


def resolve_modrinth_version(ref: ImportRef) -> dict[str, Any]:
    """Fetch project + pick a Forge .mrpack version."""
    proj = _http_json(f"{MODRINTH_API}/project/{ref.slug}")
    if str(proj.get("project_type") or "") != "modpack":
        raise ValueError(f"“{ref.slug}” no Modrinth não é um modpack (é {proj.get('project_type')}).")

    versions = _http_json(
        f"{MODRINTH_API}/project/{ref.slug}/version?loaders=%5B%22forge%22%5D"
    )
    if not isinstance(versions, list) or not versions:
        # try without loader filter then filter client-side
        versions = _http_json(f"{MODRINTH_API}/project/{ref.slug}/version")
    if not isinstance(versions, list) or not versions:
        raise ValueError(f"Nenhuma versão encontrada para {ref.slug} no Modrinth.")

    picked = None
    hint = (ref.version_hint or "").strip()
    if hint:
        for v in versions:
            if str(v.get("id") or "") == hint or str(v.get("version_number") or "") == hint:
                picked = v
                break
        if picked is None:
            raise ValueError(f"Versão “{hint}” não encontrada em {ref.slug}.")
    else:
        for v in versions:
            loaders = [str(x).lower() for x in (v.get("loaders") or [])]
            if "forge" in loaders:
                picked = v
                break
        if picked is None:
            picked = versions[0]

    loaders = [str(x).lower() for x in (picked.get("loaders") or [])]
    if loaders and "forge" not in loaders:
        raise ValueError(
            f"A versão {picked.get('version_number')} usa {', '.join(loaders)} — "
            "este launcher só instala Forge."
        )

    files = picked.get("files") or []
    mrpack = None
    for f in files:
        name = str(f.get("filename") or "")
        if name.endswith(".mrpack") or f.get("primary"):
            mrpack = f
            if name.endswith(".mrpack"):
                break
    if mrpack is None and files:
        mrpack = files[0]
    if not mrpack or not mrpack.get("url"):
        raise ValueError("Versão Modrinth sem arquivo .mrpack para baixar.")

    return {
        "project": proj,
        "version": picked,
        "file": mrpack,
    }


def resolve_curseforge_file(ref: ImportRef) -> dict[str, Any]:
    """Resolve CurseForge modpack project + file via curse.tools proxy."""
    search = _http_json(
        f"{CURSE_API}/mods/search?gameId=432&classId=4471&slug={ref.slug}&pageSize=5"
    )
    mods = search.get("data") if isinstance(search, dict) else None
    if not mods:
        # fallback: search without classId
        search = _http_json(f"{CURSE_API}/mods/search?gameId=432&slug={ref.slug}&pageSize=5")
        mods = search.get("data") if isinstance(search, dict) else None
    if not mods:
        raise ValueError(f"Modpack “{ref.slug}” não encontrado no CurseForge.")
    mod = mods[0]
    # Prefer exact slug match
    for m in mods:
        if str(m.get("slug") or "").lower() == ref.slug.lower():
            mod = m
            break
    mid = int(mod["id"])

    file_data = None
    hint = (ref.version_hint or "").strip()
    if hint.isdigit():
        file_data = _http_json(f"{CURSE_API}/mods/{mid}/files/{hint}").get("data")
        if not file_data:
            raise ValueError(f"Arquivo CurseForge #{hint} não encontrado.")
    else:
        files = _http_json(f"{CURSE_API}/mods/{mid}/files?pageSize=20").get("data") or []
        for f in files:
            gvs = [str(x).lower() for x in (f.get("gameVersions") or [])]
            if "forge" in gvs:
                file_data = f
                break
        if file_data is None and files:
            file_data = files[0]
        if file_data is None:
            raise ValueError(f"Nenhum arquivo publicado em {ref.slug}.")

    fid = int(file_data["id"])
    download_url = file_data.get("downloadUrl")
    if not download_url:
        du = _http_json(f"{CURSE_API}/mods/{mid}/files/{fid}/download-url")
        download_url = du.get("data") if isinstance(du, dict) else None
    if not download_url:
        # forgecdn fallback pattern
        download_url = (
            f"https://edge.forgecdn.net/files/{fid // 1000}/{fid % 1000:03d}/"
            f"{file_data.get('fileName')}"
        )

    return {"mod": mod, "file": file_data, "download_url": download_url}


def _wipe_instance_mods(mc: Path) -> None:
    mods = mc / "mods"
    if mods.is_dir() and not mods.is_symlink():
        for jar in mods.glob("*.jar"):
            if not jar.is_symlink():
                jar.unlink(missing_ok=True)
    mods.mkdir(parents=True, exist_ok=True)


def _extract_overrides(zf: zipfile.ZipFile, prefix: str, mc: Path) -> int:
    """Copy ``prefix/...`` zip members into the minecraft folder. Returns count."""
    prefix = prefix.replace("\\", "/").rstrip("/") + "/"
    count = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or not name.startswith(prefix):
            continue
        rel = name[len(prefix) :]
        if not rel or ".." in Path(rel).parts:
            continue
        # Only allow known gameplay content under overrides
        top = rel.split("/", 1)[0]
        if top not in {
            "mods",
            "config",
            "defaultconfigs",
            "resourcepacks",
            "shaderpacks",
            "datapacks",
            "options.txt",
            "optionsof.txt",
            "servers.dat",
            "scripts",
            "kubejs",
        } and not rel.endswith((".toml", ".json", ".cfg", ".txt", ".properties", ".snbt", ".nbt", ".zip", ".jar", ".png", ".mcmeta")):
            # still allow nested configs under common folders; skip odd binaries at root
            if "/" not in rel and not rel.endswith((".txt", ".json", ".cfg", ".properties", ".toml")):
                continue
        target = _contained(mc / rel, mc)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)
        count += 1
    return count


def install_mrpack(
    mrpack_path: Path,
    instance: GameInstance,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> tuple[str, str]:
    """
    Install a Modrinth .mrpack into the instance.
    Returns (mc_version, forge_install_version).
    """
    mc = _safe_instance_dir(instance.minecraft_path, instance.root)
    mc.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(mrpack_path, "r") as zf:
        if "modrinth.index.json" not in zf.namelist():
            raise ValueError("Arquivo .mrpack sem modrinth.index.json")
        index = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
        mc_ver, forge_ver, _loader = _forge_from_mr_deps(index.get("dependencies") or {})
        files = index.get("files") or []
        if not files:
            raise ValueError("modrinth.index.json sem lista de arquivos")

        if tracker:
            tracker.set_phase("mods", f"Baixando {len(files)} mods do Modrinth…")
        _wipe_instance_mods(mc)

        total = max(len(files), 1)
        for i, entry in enumerate(files, start=1):
            _check_cancel(cancel_event)
            if not isinstance(entry, dict):
                continue
            env = entry.get("env") or {}
            if str(env.get("client") or "required").lower() == "unsupported":
                continue
            rel = str(entry.get("path") or "").replace("\\", "/")
            if not rel or ".." in Path(rel).parts:
                continue
            downloads = entry.get("downloads") or []
            if not downloads:
                continue
            url = str(downloads[0])
            if not is_trusted_url(url):
                raise ValueError(f"Download inseguro no mrpack: {url}")
            dest = _contained(mc / rel, mc)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # skip if hash matches
            want = str((entry.get("hashes") or {}).get("sha512") or "").lower()
            if dest.exists() and want:
                # optional: we only have sha256 helper; skip expensive sha512 for speed if size matches
                size = int(entry.get("fileSize") or 0)
                if size and dest.stat().st_size == size:
                    if tracker:
                        tracker.set_counts(i, total, f"Já tem: {Path(rel).name}")
                    continue
            download_file(url, dest, tracker=None, cancel_event=cancel_event, timeout=180)
            if tracker:
                tracker.set_counts(i, total, f"Mods: {Path(rel).name}")

        if tracker:
            tracker.set_detail("Aplicando overrides…")
        _extract_overrides(zf, "overrides", mc)
        _extract_overrides(zf, "client-overrides", mc)

    try:
        from launcher.core.skins_mod import ensure_elyby_skins_mod

        ensure_elyby_skins_mod(mc, mc_version=mc_ver, tracker=tracker)
    except Exception as exc:
        if tracker:
            tracker.set_detail(f"Aviso skins Ely.by: {exc}")

    if tracker:
        tracker.complete_phase("Modpack Modrinth instalado")
    return mc_ver, forge_ver


def install_curseforge_zip(
    zip_path: Path,
    instance: GameInstance,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> tuple[str, str]:
    """
    Install a CurseForge modpack zip (manifest.json + overrides).
    Returns (mc_version, forge_install_version).
    """
    mc = _safe_instance_dir(instance.minecraft_path, instance.root)
    mc.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]
        manifest_name = next((n for n in names if n.endswith("manifest.json") and n.count("/") <= 1), None)
        if not manifest_name:
            raise ValueError("Zip CurseForge sem manifest.json")
        manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
        mc_ver, forge_ver, _loader = _forge_from_cf_manifest(manifest)
        files = [f for f in (manifest.get("files") or []) if isinstance(f, dict) and f.get("required", True)]
        if not files:
            raise ValueError("manifest.json sem arquivos de mods")

        if tracker:
            tracker.set_phase("mods", f"Baixando {len(files)} mods do CurseForge…")
        _wipe_instance_mods(mc)

        total = max(len(files), 1)
        for i, entry in enumerate(files, start=1):
            _check_cancel(cancel_event)
            project_id = int(entry["projectID"])
            file_id = int(entry["fileID"])
            meta = _http_json(f"{CURSE_API}/mods/{project_id}/files/{file_id}").get("data") or {}
            fname = str(meta.get("fileName") or f"{file_id}.jar")
            url = meta.get("downloadUrl")
            if not url:
                du = _http_json(f"{CURSE_API}/mods/{project_id}/files/{file_id}/download-url")
                url = du.get("data") if isinstance(du, dict) else None
            if not url:
                url = f"https://edge.forgecdn.net/files/{file_id // 1000}/{file_id % 1000:03d}/{fname}"
            if not is_trusted_url(str(url)):
                raise ValueError(f"Download inseguro CurseForge: {url}")
            dest = _contained(mc / "mods" / Path(fname).name, mc)
            dest.parent.mkdir(parents=True, exist_ok=True)
            download_file(str(url), dest, tracker=None, cancel_event=cancel_event, timeout=180)
            if tracker:
                tracker.set_counts(i, total, f"Mods: {Path(fname).name}")

        overrides = str(manifest.get("overrides") or "overrides").strip() or "overrides"
        # If manifest is inside a folder, overrides are relative to that folder
        base = ""
        if "/" in manifest_name:
            base = manifest_name.rsplit("/", 1)[0] + "/"
        if tracker:
            tracker.set_detail("Aplicando overrides…")
        _extract_overrides(zf, base + overrides, mc)

    try:
        from launcher.core.skins_mod import ensure_elyby_skins_mod

        ensure_elyby_skins_mod(mc, mc_version=mc_ver, tracker=tracker)
    except Exception as exc:
        if tracker:
            tracker.set_detail(f"Aviso skins Ely.by: {exc}")

    if tracker:
        tracker.complete_phase("Modpack CurseForge instalado")
    return mc_ver, forge_ver


def _prepare_instance(
    *,
    name: str,
    pack_id: str,
    pack_version: str,
    mc_version: str,
    forge_version: str,
    source: str,
    origin: str,
) -> GameInstance:
    pack_id = _require_safe(_slug(pack_id), "id do modpack")
    pack_version = _require_safe(_slug(pack_version) or "1.0.0", "versão do modpack")
    from launcher.core.modpacks import installed_instance_for
    from launcher.core.modpacks import ModpackInfo

    probe = ModpackInfo(id=pack_id, name=name, version=pack_version)
    existing = installed_instance_for(probe, origin)
    if existing is not None:
        inst = existing
    else:
        inst = create_instance(
            name,
            modpack_id=pack_id,
            modpack_version=pack_version,
            mc_version=mc_version,
            forge_version=forge_version,
            source=source,
            instance_id=pack_id,
        )
    inst.name = name
    inst.modpack_id = pack_id
    inst.modpack_version = pack_version
    inst.mc_version = mc_version
    inst.forge_version = normalize_forge_install_version(mc_version, forge_version)
    inst.forge_profile = forge_profile_id(inst.mc_version, inst.forge_version)
    inst.source = source
    inst.extra["catalog_origin"] = origin
    inst.ensure_dirs()
    _contained(inst.minecraft_path, instances_root())
    inst.save_meta()
    return inst


def install_from_url(
    url: str,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> GameInstance:
    """Download a CurseForge/Modrinth pack from a page URL and create/update an instance."""
    ref = parse_pack_url(url)
    cache = cache_dir() / "imported_packs"
    cache.mkdir(parents=True, exist_ok=True)

    if ref.platform == "modrinth":
        if tracker:
            tracker.set_phase("mods", f"Resolvendo Modrinth: {ref.slug}…")
        resolved = resolve_modrinth_version(ref)
        proj = resolved["project"]
        ver = resolved["version"]
        file_info = resolved["file"]
        name = str(proj.get("title") or ref.slug)
        pack_id = _slug(str(proj.get("slug") or ref.slug))
        pack_version = _slug(str(ver.get("version_number") or ver.get("id") or "1.0.0"))
        dl = str(file_info["url"])
        mr_path = _contained(cache / f"mr-{pack_id}-{pack_version}.mrpack", cache)
        if tracker:
            tracker.set_phase("mods", f"Baixando {name}…")
        _check_cancel(cancel_event)
        download_file(dl, mr_path, tracker=tracker, cancel_event=cancel_event, timeout=300)

        # Peek deps before creating instance so forge/mc are right
        with zipfile.ZipFile(mr_path, "r") as zf:
            index = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
        mc_ver, forge_ver, _ = _forge_from_mr_deps(index.get("dependencies") or {})

        inst = _prepare_instance(
            name=name,
            pack_id=pack_id,
            pack_version=pack_version,
            mc_version=mc_ver,
            forge_version=forge_ver,
            source="modrinth",
            origin=f"modrinth:{pack_id}",
        )
        mc_ver, forge_ver = install_mrpack(mr_path, inst, tracker=tracker, cancel_event=cancel_event)
        inst.mc_version = mc_ver
        inst.forge_version = forge_ver
        inst.forge_profile = forge_profile_id(mc_ver, forge_ver)
        inst.modpack_version = pack_version
        inst.source = "modrinth"
        inst.extra["catalog_origin"] = f"modrinth:{pack_id}"
        inst.extra["import_url"] = ref.raw_url
        inst.save_meta()
        return inst

    if ref.platform == "curseforge":
        if tracker:
            tracker.set_phase("mods", f"Resolvendo CurseForge: {ref.slug}…")
        resolved = resolve_curseforge_file(ref)
        mod = resolved["mod"]
        file_data = resolved["file"]
        dl = str(resolved["download_url"])
        name = str(mod.get("name") or ref.slug)
        pack_id = _slug(str(mod.get("slug") or ref.slug))
        pack_version = (
            _slug(
                str(
                    file_data.get("displayName")
                    or file_data.get("fileName")
                    or file_data.get("id")
                    or "1.0.0"
                )
            )[:48]
            or "1.0.0"
        )
        zip_path = _contained(cache / f"cf-{pack_id}-{file_data['id']}.zip", cache)
        if tracker:
            tracker.set_phase("mods", f"Baixando {name}…")
        _check_cancel(cancel_event)
        download_file(dl, zip_path, tracker=tracker, cancel_event=cancel_event, timeout=600)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [n.replace("\\", "/") for n in zf.namelist()]
            manifest_name = next(
                (n for n in names if n.endswith("manifest.json") and n.count("/") <= 1), None
            )
            if not manifest_name:
                raise ValueError("Zip CurseForge sem manifest.json")
            manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
        mc_ver, forge_ver, _ = _forge_from_cf_manifest(manifest)

        inst = _prepare_instance(
            name=name,
            pack_id=pack_id,
            pack_version=pack_version,
            mc_version=mc_ver,
            forge_version=forge_ver,
            source="curseforge",
            origin=f"curseforge:{pack_id}",
        )
        mc_ver, forge_ver = install_curseforge_zip(
            zip_path, inst, tracker=tracker, cancel_event=cancel_event
        )
        inst.mc_version = mc_ver
        inst.forge_version = forge_ver
        inst.forge_profile = forge_profile_id(mc_ver, forge_ver)
        inst.modpack_version = pack_version
        inst.source = "curseforge"
        inst.extra["catalog_origin"] = f"curseforge:{pack_id}"
        inst.extra["import_url"] = ref.raw_url
        inst.save_meta()
        return inst

    raise ValueError(f"Plataforma não suportada: {ref.platform}")
