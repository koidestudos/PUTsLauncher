from __future__ import annotations

import json
import re
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from launcher.config import FORGE_VERSION, MC_VERSION, cache_dir
from launcher.core.installer import CancelledError
from launcher.core.instances import GameInstance, create_instance, instances_root
from launcher.core.loaders import (
    loader_profile_id,
    normalize_loader_name,
    normalize_loader_version,
    require_supported_loader,
)
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

# Parallel install: many small jars — sequential was the 30‑min bottleneck.
DOWNLOAD_WORKERS = 32
META_WORKERS = 20
DOWNLOAD_RETRIES = 5
META_RETRIES = 4

SUPPORTED_MR_LOADERS = ("forge", "neoforge", "fabric", "quilt")
# Preference when a pack publishes several loaders for the same version page.
LOADER_PREFERENCE = ("forge", "neoforge", "fabric", "quilt")


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
    last: Optional[BaseException] = None
    for attempt in range(META_RETRIES):
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
            last = RuntimeError(f"HTTP {exc.code} em {url}: {body or exc.reason}")
            # Don't hammer 404s
            if exc.code in {400, 401, 403, 404}:
                raise last from exc
        except URLError as exc:
            last = RuntimeError(f"Falha de rede: {exc.reason}")
        except TimeoutError as exc:
            last = RuntimeError(f"Timeout em {url}")
            last.__cause__ = exc
        time.sleep(min(0.35 * (2**attempt), 3.0))
    raise RuntimeError(str(last) if last else f"Falha em {url}")


def _check_cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("Cancelado.")


def _forgecdn_urls(file_id: int, file_name: str) -> list[str]:
    """Build CDN candidates (padded + unpadded, edge + mediafilez)."""
    name = quote(Path(file_name).name, safe="._-+()[]")
    a, b = file_id // 1000, file_id % 1000
    paths = [f"{a}/{b:03d}/{name}", f"{a}/{b}/{name}"]
    hosts = ("https://edge.forgecdn.net/files/", "https://mediafilez.forgecdn.net/files/")
    out: list[str] = []
    for host in hosts:
        for p in paths:
            out.append(host + p)
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def _download_one(
    urls: list[str],
    dest: Path,
    *,
    cancel_event=None,
    timeout: int = 180,
) -> None:
    """Try each URL with retries until the file lands on disk."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[BaseException] = None
    for attempt in range(DOWNLOAD_RETRIES):
        _check_cancel(cancel_event)
        for url in urls:
            _check_cancel(cancel_event)
            if not is_trusted_url(url):
                continue
            try:
                download_file(url, dest, tracker=None, cancel_event=cancel_event, timeout=timeout)
                if dest.is_file() and dest.stat().st_size > 0:
                    return
                dest.unlink(missing_ok=True)
                last_err = RuntimeError(f"Arquivo vazio: {dest.name}")
            except CancelledError:
                dest.unlink(missing_ok=True)
                raise
            except BaseException as exc:
                dest.unlink(missing_ok=True)
                last_err = exc
        time.sleep(min(0.4 * (2**attempt), 5.0))
    raise RuntimeError(f"Não baixou {dest.name}: {last_err}") from last_err


def _parallel_download_jobs(
    jobs: list[tuple[list[str], Path]],
    *,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
    label: str = "Mods",
    workers: int = DOWNLOAD_WORKERS,
) -> None:
    """
    Download many files at once. ``jobs`` is a list of (url_list, dest).
    """
    if not jobs:
        return
    total = len(jobs)
    done = 0
    lock = threading.Lock()
    errors: list[str] = []

    if tracker:
        tracker.set_counts(0, total, f"{label}: 0/{total}")

    def work(urls: list[str], dest: Path) -> str:
        _download_one(urls, dest, cancel_event=cancel_event)
        return dest.name

    with ThreadPoolExecutor(max_workers=max(1, min(workers, total))) as pool:
        futures = {pool.submit(work, urls, dest): dest for urls, dest in jobs}
        try:
            for fut in as_completed(futures):
                _check_cancel(cancel_event)
                dest = futures[fut]
                try:
                    name = fut.result()
                except CancelledError:
                    for other in futures:
                        other.cancel()
                    raise
                except Exception as exc:
                    errors.append(f"{dest.name}: {exc}")
                    name = dest.name
                with lock:
                    done += 1
                    if tracker:
                        tracker.set_counts(done, total, f"{label}: {done}/{total} · {name}")
        except CancelledError:
            for fut in futures:
                fut.cancel()
            raise

    if errors:
        sample = "; ".join(errors[:3])
        more = f" (+{len(errors) - 3} outros)" if len(errors) > 3 else ""
        raise RuntimeError(f"{len(errors)} arquivo(s) falharam no download. {sample}{more}")


def _resolve_cf_file(project_id: int, file_id: int) -> tuple[str, list[str]]:
    """Return (fileName, download url candidates) for one CurseForge file."""
    meta = _http_json(f"{CURSE_API}/mods/{project_id}/files/{file_id}").get("data") or {}
    fname = str(meta.get("fileName") or f"{file_id}.jar")
    urls: list[str] = []
    direct = meta.get("downloadUrl")
    if direct:
        urls.append(str(direct))
    urls.extend(_forgecdn_urls(file_id, fname))
    # unique
    seen: set[str] = set()
    uniq: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return fname, uniq


def _parallel_resolve_cf(
    entries: list[dict[str, Any]],
    *,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> list[tuple[str, list[str]]]:
    """Resolve CurseForge file metadata in parallel → [(fileName, urls), ...]."""
    total = len(entries)
    results: list[Optional[tuple[str, list[str]]]] = [None] * total
    errors: list[str] = []
    done = 0
    lock = threading.Lock()

    if tracker:
        tracker.set_detail(f"Resolvendo {total} arquivos CurseForge…")

    def work(idx: int, entry: dict[str, Any]) -> tuple[int, str, list[str]]:
        _check_cancel(cancel_event)
        project_id = int(entry["projectID"])
        file_id = int(entry["fileID"])
        fname, urls = _resolve_cf_file(project_id, file_id)
        return idx, fname, urls

    with ThreadPoolExecutor(max_workers=max(1, min(META_WORKERS, total))) as pool:
        futures = {pool.submit(work, i, e): i for i, e in enumerate(entries)}
        for fut in as_completed(futures):
            _check_cancel(cancel_event)
            try:
                idx, fname, urls = fut.result()
                results[idx] = (fname, urls)
            except CancelledError:
                for other in futures:
                    other.cancel()
                raise
            except Exception as exc:
                errors.append(str(exc))
            with lock:
                done += 1
                if tracker and (done % 5 == 0 or done == total):
                    tracker.set_counts(done, total, f"Metadados: {done}/{total}")

    if errors and any(r is None for r in results):
        sample = "; ".join(errors[:2])
        raise RuntimeError(f"Falha ao resolver arquivos CurseForge ({len(errors)}). {sample}")
    return [r for r in results if r is not None]

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


def _loader_from_mr_deps(deps: dict[str, Any], mc_fallback: str = "") -> tuple[str, str, str]:
    """Return (mc_version, loader_version, loader_name)."""
    mc = str(deps.get("minecraft") or mc_fallback or MC_VERSION).strip()
    loader = ""
    raw = ""
    for key, name in (
        ("forge", "forge"),
        ("neoforge", "neoforge"),
        ("fabric-loader", "fabric"),
        ("quilt-loader", "quilt"),
    ):
        if key in deps and deps[key]:
            loader = name
            raw = str(deps[key]).strip()
            break
    if not loader:
        raise ValueError("modrinth.index.json sem loader (forge/fabric/neoforge/quilt).")
    require_supported_loader(loader)
    install = normalize_loader_version(loader, mc, raw or FORGE_VERSION)
    return mc, install, loader


def _forge_from_mr_deps(deps: dict[str, Any], mc_fallback: str = "") -> tuple[str, str, str]:
    """Back-compat alias → (mc, loader_version, loader)."""
    return _loader_from_mr_deps(deps, mc_fallback)


def _loader_from_cf_manifest(manifest: dict[str, Any]) -> tuple[str, str, str]:
    """Return (mc_version, loader_version, loader_name) from CurseForge manifest."""
    mc_block = manifest.get("minecraft") or {}
    mc = str(mc_block.get("version") or MC_VERSION).strip()
    loaders = mc_block.get("modLoaders") or []
    found: list[tuple[str, str, bool]] = []
    for entry in loaders:
        if not isinstance(entry, dict):
            continue
        lid = str(entry.get("id") or "")
        low = lid.lower()
        primary = bool(entry.get("primary"))
        if low.startswith("forge-"):
            found.append(("forge", lid.split("-", 1)[1], primary))
        elif low.startswith("neoforge-"):
            found.append(("neoforge", lid.split("-", 1)[1], primary))
        elif low.startswith("fabric-"):
            found.append(("fabric", lid.split("-", 1)[1], primary))
        elif low.startswith("quilt-"):
            found.append(("quilt", lid.split("-", 1)[1], primary))
    if not found:
        if loaders:
            ids = ", ".join(str((e or {}).get("id") or "?") for e in loaders if isinstance(e, dict))
            raise ValueError(f"Loader não suportado no manifest CurseForge: {ids}")
        raise ValueError("Não achei Forge/Fabric/NeoForge no manifest deste modpack.")

    primary = next(((n, v) for n, v, p in found if p), None)
    if primary:
        loader, raw = primary
    else:
        by_name = {n: v for n, v, _p in found}
        loader = next((n for n in LOADER_PREFERENCE if n in by_name), found[0][0])
        raw = by_name[loader]
    require_supported_loader(loader)
    install = normalize_loader_version(loader, mc, raw or FORGE_VERSION)
    return mc, install, loader


def _forge_from_cf_manifest(manifest: dict[str, Any]) -> tuple[str, str, str]:
    return _loader_from_cf_manifest(manifest)


def resolve_modrinth_version(ref: ImportRef) -> dict[str, Any]:
    """Fetch project + pick a supported .mrpack version (Forge/Fabric/NeoForge/Quilt)."""
    proj = _http_json(f"{MODRINTH_API}/project/{ref.slug}")
    if str(proj.get("project_type") or "") != "modpack":
        raise ValueError(f"“{ref.slug}” no Modrinth não é um modpack (é {proj.get('project_type')}).")

    versions = _http_json(f"{MODRINTH_API}/project/{ref.slug}/version")
    if not isinstance(versions, list) or not versions:
        raise ValueError(f"Nenhuma versão encontrada para {ref.slug} no Modrinth.")

    def score(v: dict[str, Any]) -> int:
        loaders = [normalize_loader_name(str(x)) for x in (v.get("loaders") or [])]
        for i, pref in enumerate(LOADER_PREFERENCE):
            if pref in loaders:
                return i
        return 99

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
        supported = [
            v
            for v in versions
            if any(
                normalize_loader_name(str(x)) in SUPPORTED_MR_LOADERS
                for x in (v.get("loaders") or [])
            )
        ]
        pool = supported or versions
        pool = sorted(pool, key=score)
        picked = pool[0]

    loaders = [normalize_loader_name(str(x)) for x in (picked.get("loaders") or [])]
    if loaders and not any(x in SUPPORTED_MR_LOADERS for x in loaders):
        raise ValueError(
            f"A versão {picked.get('version_number')} usa {', '.join(loaders)} — "
            "este launcher instala Forge, Fabric, NeoForge ou Quilt."
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
        preferred = {"forge", "neoforge", "fabric", "quilt"}
        for f in files:
            gvs = {str(x).lower() for x in (f.get("gameVersions") or [])}
            if gvs & preferred:
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
) -> tuple[str, str, str]:
    """
    Install a Modrinth .mrpack into the instance.
    Returns (mc_version, loader_version, loader_name).
    """
    mc = _safe_instance_dir(instance.minecraft_path, instance.root)
    mc.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(mrpack_path, "r") as zf:
        if "modrinth.index.json" not in zf.namelist():
            raise ValueError("Arquivo .mrpack sem modrinth.index.json")
        index = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
        mc_ver, forge_ver, loader = _loader_from_mr_deps(index.get("dependencies") or {})
        files = index.get("files") or []
        if not files:
            raise ValueError("modrinth.index.json sem lista de arquivos")

        if tracker:
            tracker.set_phase("mods", f"Baixando {len(files)} mods do Modrinth…")
        _wipe_instance_mods(mc)

        jobs: list[tuple[list[str], Path]] = []
        for entry in files:
            _check_cancel(cancel_event)
            if not isinstance(entry, dict):
                continue
            env = entry.get("env") or {}
            if str(env.get("client") or "required").lower() == "unsupported":
                continue
            rel = str(entry.get("path") or "").replace("\\", "/")
            if not rel or ".." in Path(rel).parts:
                continue
            downloads = [str(u) for u in (entry.get("downloads") or []) if u]
            downloads = [u for u in downloads if is_trusted_url(u)]
            if not downloads:
                continue
            dest = _contained(mc / rel, mc)
            dest.parent.mkdir(parents=True, exist_ok=True)
            want_size = int(entry.get("fileSize") or 0)
            if dest.exists() and want_size and dest.stat().st_size == want_size:
                continue
            jobs.append((downloads, dest))

        _parallel_download_jobs(
            jobs, tracker=tracker, cancel_event=cancel_event, label="Mods Modrinth"
        )

        if tracker:
            tracker.set_detail("Aplicando overrides…")
        _extract_overrides(zf, "overrides", mc)
        _extract_overrides(zf, "client-overrides", mc)

    try:
        from launcher.core.skins_mod import ensure_elyby_skins_mod

        ensure_elyby_skins_mod(mc, mc_version=mc_ver, loader=loader, tracker=tracker)
    except Exception as exc:
        if tracker:
            tracker.set_detail(f"Aviso skins Ely.by: {exc}")

    if tracker:
        tracker.complete_phase("Modpack Modrinth instalado")
    return mc_ver, forge_ver, loader


def install_curseforge_zip(
    zip_path: Path,
    instance: GameInstance,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> tuple[str, str, str]:
    """
    Install a CurseForge modpack zip (manifest.json + overrides).
    Returns (mc_version, loader_version, loader_name).
    """
    mc = _safe_instance_dir(instance.minecraft_path, instance.root)
    mc.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]
        manifest_name = next((n for n in names if n.endswith("manifest.json") and n.count("/") <= 1), None)
        if not manifest_name:
            raise ValueError("Zip CurseForge sem manifest.json")
        manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
        mc_ver, forge_ver, loader = _loader_from_cf_manifest(manifest)
        files = [f for f in (manifest.get("files") or []) if isinstance(f, dict) and f.get("required", True)]
        if not files:
            raise ValueError("manifest.json sem arquivos de mods")

        if tracker:
            tracker.set_phase("mods", f"Baixando {len(files)} mods do CurseForge…")
        _wipe_instance_mods(mc)

        resolved = _parallel_resolve_cf(files, tracker=tracker, cancel_event=cancel_event)
        jobs: list[tuple[list[str], Path]] = []
        for fname, urls in resolved:
            dest = _contained(mc / "mods" / Path(fname).name, mc)
            dest.parent.mkdir(parents=True, exist_ok=True)
            safe_urls = [u for u in urls if is_trusted_url(u)]
            if not safe_urls:
                raise ValueError(f"Download inseguro CurseForge: {fname}")
            jobs.append((safe_urls, dest))

        _parallel_download_jobs(
            jobs, tracker=tracker, cancel_event=cancel_event, label="Mods CurseForge"
        )

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

        ensure_elyby_skins_mod(mc, mc_version=mc_ver, loader=loader, tracker=tracker)
    except Exception as exc:
        if tracker:
            tracker.set_detail(f"Aviso skins Ely.by: {exc}")

    if tracker:
        tracker.complete_phase("Modpack CurseForge instalado")
    return mc_ver, forge_ver, loader


def _prepare_instance(
    *,
    name: str,
    pack_id: str,
    pack_version: str,
    mc_version: str,
    forge_version: str,
    source: str,
    origin: str,
    loader: str = "forge",
) -> GameInstance:
    pack_id = _require_safe(_slug(pack_id), "id do modpack")
    pack_version = _require_safe(_slug(pack_version) or "1.0.0", "versão do modpack")
    from launcher.core.modpacks import installed_instance_for
    from launcher.core.modpacks import ModpackInfo

    loader_name = normalize_loader_name(loader)
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
            loader=loader_name,
            source=source,
            instance_id=pack_id,
        )
    inst.name = name
    inst.modpack_id = pack_id
    inst.modpack_version = pack_version
    inst.mc_version = mc_version
    inst.loader = loader_name
    inst.forge_version = normalize_loader_version(loader_name, mc_version, forge_version)
    inst.forge_profile = loader_profile_id(loader_name, inst.mc_version, inst.forge_version)
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

        # Peek deps before creating instance so loader/mc are right
        with zipfile.ZipFile(mr_path, "r") as zf:
            index = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
        mc_ver, forge_ver, loader = _loader_from_mr_deps(index.get("dependencies") or {})

        inst = _prepare_instance(
            name=name,
            pack_id=pack_id,
            pack_version=pack_version,
            mc_version=mc_ver,
            forge_version=forge_ver,
            source="modrinth",
            origin=f"modrinth:{pack_id}",
            loader=loader,
        )
        mc_ver, forge_ver, loader = install_mrpack(
            mr_path, inst, tracker=tracker, cancel_event=cancel_event
        )
        inst.mc_version = mc_ver
        inst.loader = loader
        inst.forge_version = forge_ver
        inst.forge_profile = loader_profile_id(loader, mc_ver, forge_ver)
        inst.modpack_version = pack_version
        inst.source = "modrinth"
        inst.extra["catalog_origin"] = f"modrinth:{pack_id}"
        inst.extra["import_url"] = ref.raw_url
        inst.save_meta()
        try:
            from launcher.core.cache_cleanup import cleanup_cache, forget_cache_file

            forget_cache_file(mr_path)
            cleanup_cache()
        except Exception:
            pass
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
        mc_ver, forge_ver, loader = _loader_from_cf_manifest(manifest)

        inst = _prepare_instance(
            name=name,
            pack_id=pack_id,
            pack_version=pack_version,
            mc_version=mc_ver,
            forge_version=forge_ver,
            source="curseforge",
            origin=f"curseforge:{pack_id}",
            loader=loader,
        )
        mc_ver, forge_ver, loader = install_curseforge_zip(
            zip_path, inst, tracker=tracker, cancel_event=cancel_event
        )
        inst.mc_version = mc_ver
        inst.loader = loader
        inst.forge_version = forge_ver
        inst.forge_profile = loader_profile_id(loader, mc_ver, forge_ver)
        inst.modpack_version = pack_version
        inst.source = "curseforge"
        inst.extra["catalog_origin"] = f"curseforge:{pack_id}"
        inst.extra["import_url"] = ref.raw_url
        inst.save_meta()
        try:
            from launcher.core.cache_cleanup import cleanup_cache, forget_cache_file

            forget_cache_file(zip_path)
            cleanup_cache()
        except Exception:
            pass
        return inst

    raise ValueError(f"Plataforma não suportada: {ref.platform}")
