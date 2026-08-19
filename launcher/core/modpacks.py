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
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from launcher.config import FORGE_VERSION, MC_VERSION, cache_dir
from launcher.core.installer import CancelledError
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


def is_trusted_url(url: str) -> bool:
    """
    HTTPS, or plain HTTP on loopback (used by local test catalogs).

    A catalog served over HTTP can be swapped in transit, and whoever swaps it
    also picks the pack URLs — so the transport of the index matters as much as
    the transport of the zip.
    """
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    if parsed.scheme == "https":
        return True
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "http" and host in {"127.0.0.1", "::1", "localhost"}


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


# Catalog ids/versions become folder and file names, so they must stay a single
# harmless path component (no separators, no "..", no drive letters).
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")


def is_safe_component(value: str) -> bool:
    text = (value or "").strip()
    return bool(_SAFE_COMPONENT.fullmatch(text)) and ".." not in text


def _require_safe(value: str, field: str) -> str:
    text = (value or "").strip()
    if not is_safe_component(text):
        raise ValueError(
            f"{field} inválido no catálogo: {value!r} — use apenas letras, números, ponto, - e _"
        )
    return text


def _contained(path: Path, base: Path) -> Path:
    """Confirm ``path`` really lives under ``base`` after resolving symlinks/``..``."""
    resolved = path.resolve()
    base_resolved = base.resolve()
    if resolved != base_resolved and base_resolved not in resolved.parents:
        raise ValueError(f"Caminho fora de {base_resolved}: {resolved}")
    return resolved


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


def _packs_from_index_payload(
    data: Any,
    asset_urls: Optional[dict[str, str]] = None,
    base_url: str = "",
) -> list[ModpackInfo]:
    items = data.get("modpacks") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError('Índice inválido: esperado { "modpacks": [ ... ] }')
    packs: list[ModpackInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pack = ModpackInfo.from_dict(item)
        # Resolve bare asset filenames against release assets
        if pack.download_url and "://" not in pack.download_url:
            key = Path(pack.download_url).name
            resolved = (asset_urls or {}).get(key) or (asset_urls or {}).get(pack.download_url)
            if not resolved and base_url:
                resolved = urljoin(base_url, pack.download_url)
            pack.download_url = resolved or pack.download_url
        if not pack.id or not pack.download_url:
            continue
        if not is_safe_component(pack.id) or not is_safe_component(pack.version):
            continue  # id/version virariam nome de pasta/arquivo — entrada descartada
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


def _is_stable_modpacks_release(rel: dict[str, Any]) -> bool:
    """True for the fixed catalog Release (tag/name Modpacks)."""
    tag = str(rel.get("tag_name") or "").strip().lower()
    name = str(rel.get("name") or "").strip().lower()
    return tag in {"modpacks", "modpack"} or name in {"modpacks", "modpack"}


def _releases_catalog_order(releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer the stable Modpacks release, then GitHub's newest-first order."""
    preferred: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        if _is_stable_modpacks_release(rel):
            preferred.append(rel)
        else:
            rest.append(rel)
    return preferred + rest


def fetch_github_releases_catalog(owner: str, repo: str, timeout: int = 30) -> list[ModpackInfo]:
    """
    Load modpacks from GitHub Releases.

    Preference order:
      1) index.json / modpacks.json / catalog.json on the stable **Modpacks** release
      2) same catalog JSON on the newest other release that has one
      3) otherwise every .zip asset across releases becomes a modpack entry
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

    ordered = _releases_catalog_order(releases)

    # Prefer explicit catalog JSON (Modpacks release first)
    for rel in ordered:
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
            "Publique no Release Modpacks com index.json ou arquivos .zip."
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
        if not is_trusted_url(text):
            raise ValueError(
                "Catálogo por http:// não é aceito — use https:// (ou dono/repo do GitHub). "
                "Por http qualquer um na rede pode trocar a lista de modpacks."
            )
        raw = _http_get(text, timeout=timeout, accept="application/json,text/plain,*/*")
        data = json.loads(raw.decode("utf-8"))
        # Bare asset names in a direct index resolve next to the index itself.
        return _packs_from_index_payload(data, base_url=text)

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


def _check_cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("Cancelado.")


def download_file(
    url: str,
    dest: Path,
    tracker: Optional[ProgressTracker] = None,
    timeout: int = 120,
    cancel_event=None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream,*/*"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with dest.open("wb") as out:
                while True:
                    _check_cancel(cancel_event)
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if tracker and total > 0:
                        tracker.set_counts(downloaded, total, f"Baixando pack… {downloaded // (1024 * 1024)} MB")
    except BaseException:
        # A half-written zip must never be mistaken for a cached pack.
        dest.unlink(missing_ok=True)
        raise
    return dest


PACK_TOP_LEVEL = {
    "mods",
    "config",
    "defaultconfigs",
    "resourcepacks",
    "shaderpacks",
    "options.txt",
    "optionsof.txt",
    "servers.dat",
}
PACK_LOOSE_FILES = {"pack.meta.json", "manifest.json", "options.txt"}


def _pack_entries(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str]]:
    """
    Files the launcher takes from a pack zip, as (member, path relative to the
    minecraft folder). Junk from archivers is ignored so a pack stored inside a
    single folder is still recognised.
    """
    junk = ("__MACOSX/", ".DS_Store", "Thumbs.db")

    def is_junk(name: str) -> bool:
        return name.startswith("__MACOSX/") or Path(name).name in junk

    names = [n.replace("\\", "/") for n in zf.namelist() if not is_junk(n.replace("\\", "/"))]
    tops = {n.split("/")[0] for n in names if n and not n.endswith("/")}
    strip_root = ""
    roots_with_mods = [
        top
        for top in tops
        if any(n.startswith(top + "/mods/") for n in names)
    ]
    if len(roots_with_mods) == 1 and not any(n.startswith("mods/") for n in names):
        strip_root = roots_with_mods[0] + "/"

    entries: list[tuple[zipfile.ZipInfo, str]] = []
    for info in zf.infolist():
        raw = info.filename.replace("\\", "/")
        if info.filename.endswith("/") or is_junk(raw):
            continue
        name = raw[len(strip_root):] if strip_root and raw.startswith(strip_root) else raw
        if not name or ".." in Path(name).parts:
            continue
        top = name.split("/", 1)[0]
        if top not in PACK_TOP_LEVEL and name not in PACK_LOOSE_FILES:
            continue
        entries.append((info, name))
    return entries


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_instance_dir(path: Path, root: Path) -> Path:
    """
    A directory inside the instance that is really inside it — a symlink or
    junction here would make the mods wipe delete somebody else's folder.
    """
    if path.is_symlink():
        raise ValueError(f"{path} é um atalho/symlink — recuse por segurança.")
    if path.exists() and not path.is_dir():
        raise ValueError(f"{path} deveria ser uma pasta.")
    return _contained(path, root)


def install_modpack_zip(
    zip_path: Path,
    instance: GameInstance,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> None:
    """
    Extract pack into instance minecraft folder.
    Accepts zip rooted at mods/ or with a single top-level folder.

    Files are unpacked to a staging folder first: a failure halfway through must
    not leave the instance with the old mods deleted and the new ones missing.
    """
    mc = instance.minecraft_path
    mc.mkdir(parents=True, exist_ok=True)
    mc = _safe_instance_dir(mc, instance.root)
    if tracker:
        tracker.set_phase("mods", "Extraindo modpack…")

    staging = mc / ".puts-staging"
    backup = mc / ".puts-backup"
    for work in (staging, backup):
        if work.is_symlink():
            raise ValueError(f"{work} é um atalho/symlink — recuse por segurança.")
        shutil.rmtree(work, ignore_errors=True)
        if work.exists() or work.is_symlink():
            work.unlink(missing_ok=True)
    staging = _safe_instance_dir(staging, mc)
    backup = _safe_instance_dir(backup, mc)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            entries = _pack_entries(zf)
            if not any(name.startswith("mods/") and name.endswith(".jar") for _info, name in entries):
                raise ValueError(
                    "O zip do modpack não tem nenhum mod em mods/ — "
                    "instalação recusada para não apagar os mods atuais."
                )
            total = max(len(entries), 1)
            staged: dict[Path, Path] = {}
            for i, (info, name) in enumerate(entries, start=1):
                _check_cancel(cancel_event)
                try:
                    final = _contained(mc / name, mc)
                    temp = _contained(staging / name, staging)
                except ValueError:
                    continue
                temp.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, temp.open("wb") as out:
                    shutil.copyfileobj(src, out)
                # Um zip pode repetir o mesmo caminho; vale a última cópia.
                staged[final] = temp
                if tracker:
                    tracker.set_counts(i, total, f"Extraindo: {Path(name).name}")

        # Everything unpacked — commit, keeping the old pack aside until the end.
        mods_dir = _safe_instance_dir(mc / "mods", mc)
        mods_dir.mkdir(parents=True, exist_ok=True)
        backup.mkdir(parents=True, exist_ok=True)
        moved_out: list[tuple[Path, Path]] = []
        replaced: list[tuple[Path, Path]] = []
        created_new: list[Path] = []
        try:
            for jar in sorted(mods_dir.glob("*.jar")):
                if jar.is_symlink():
                    continue
                kept = backup / jar.name
                shutil.move(str(jar), str(kept))
                moved_out.append((kept, jar))
            for final, temp in staged.items():
                final.parent.mkdir(parents=True, exist_ok=True)
                if final.exists() or final.is_symlink():
                    kept = backup / f"replaced-{len(replaced)}-{final.name}"
                    shutil.move(str(final), str(kept))
                    replaced.append((kept, final))
                else:
                    created_new.append(final)
                shutil.move(str(temp), str(final))
        except BaseException:
            # Put the previous pack back before giving up.
            for new_file in created_new:
                try:
                    new_file.unlink(missing_ok=True)
                except OSError:
                    pass
            for kept, original in moved_out + replaced:
                try:
                    if original.exists():
                        original.unlink()
                    shutil.move(str(kept), str(original))
                except OSError:
                    pass
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)

    instance.save_meta()
    try:
        from launcher.core.skins_mod import ensure_elyby_skins_mod

        ensure_elyby_skins_mod(mc, mc_version=instance.mc_version or "", tracker=tracker)
    except Exception as exc:
        if tracker:
            tracker.set_detail(f"Aviso: não deu pra instalar skins Ely.by ({exc})")
    if tracker:
        tracker.complete_phase("Modpack extraído")


def verify_modpack_files(
    pack: "ModpackInfo",
    instance: GameInstance,
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
    repair: bool = True,
) -> dict[str, Any]:
    """
    Compare every file the pack ships with what is on disk (sha256) and put back
    the ones that are missing or changed — like Steam's "verify integrity".
    Files the player added (extra mods, worlds, configs) are never touched.
    """
    zip_path = ensure_pack_zip(pack, tracker=tracker, cancel_event=cancel_event)
    mc = instance.minecraft_path
    mc.mkdir(parents=True, exist_ok=True)
    if tracker:
        tracker.set_phase("mods", f"Verificando arquivos de {pack.name}…")

    missing: list[str] = []
    changed: list[str] = []
    repaired: list[str] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        entries = _pack_entries(zf)
        total = max(len(entries), 1)
        for i, (info, name) in enumerate(entries, start=1):
            _check_cancel(cancel_event)
            try:
                target = _contained(mc / name, mc)
            except ValueError:
                continue
            expected = zf.read(info)
            if not target.exists():
                missing.append(name)
            elif target.stat().st_size != len(expected) or _sha256_file(target) != _sha256_bytes(expected):
                changed.append(name)
            else:
                if tracker:
                    tracker.set_counts(i, total, f"Verificando: {Path(name).name}")
                continue
            if repair:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(expected)
                repaired.append(name)
            if tracker:
                tracker.set_counts(i, total, f"Restaurando: {Path(name).name}")

    report = {
        "checked": len(entries),
        "missing": missing,
        "changed": changed,
        "repaired": repaired,
        "ok": not missing and not changed,
    }
    if tracker:
        tracker.complete_phase(
            "Arquivos íntegros"
            if report["ok"]
            else f"{len(repaired)} arquivo(s) restaurado(s)"
        )
    return report


def pack_identity(pack: "ModpackInfo") -> tuple[str, str, str]:
    """(catalog id, version, instance id) after validating the catalog strings."""
    pack_id = _require_safe(pack.id, "id do modpack")
    pack_version = _require_safe(pack.version or "1.0.0", "versão do modpack")
    # create_instance() slugifies ids, so look up and create with the same
    # canonical form — otherwise "Demo.Pack" would spawn demo-pack-2 on update.
    return pack_id, pack_version, _slug(pack_id)


def installed_instance_for(pack: "ModpackInfo", catalog_origin: str = "") -> Optional[GameInstance]:
    """
    The instance this catalog entry is already installed into, if any.

    Matching is by ``modpack_id`` across every instance — never by folder name
    alone: a pack called "default" must not take over the local default
    instance, and a pack that landed on "demo-pack-2" after a slug collision
    must be found again on the next update.
    """
    try:
        pack_id, _version, _canonical = pack_identity(pack)
    except ValueError:
        return None
    from launcher.core.instances import list_instances

    origin = (catalog_origin or "").strip().lower()
    for inst in list_instances():
        if (inst.modpack_id or "") != pack_id:
            continue
        known = str(inst.extra.get("catalog_origin") or "").strip().lower()
        if origin and known and origin != known:
            continue  # mesmo id vindo de outro catálogo — instalação separada
        return inst
    return None


SUPPORTED_LOADERS = {"", "forge", "fabric", "neoforge", "quilt"}


def _require_supported_loader(pack: "ModpackInfo") -> None:
    from launcher.core.loaders import normalize_loader_name

    loader = normalize_loader_name(pack.loader or "forge")
    if loader not in SUPPORTED_LOADERS - {""}:
        raise ValueError(
            f"O modpack {pack.name or pack.id} usa {pack.loader}, "
            "e este launcher instala Forge, Fabric, NeoForge ou Quilt."
        )


def ensure_pack_zip(
    pack: "ModpackInfo",
    tracker: Optional[ProgressTracker] = None,
    cancel_event=None,
) -> Path:
    """
    Local copy of the pack zip: reuse the cached one when the catalog pins a
    digest and it matches, download otherwise. Always digest-checked when the
    catalog provides a sha256.
    """
    pack_id, pack_version, canonical_id = pack_identity(pack)
    _require_supported_loader(pack)
    if not pack.download_url:
        raise ValueError(f"Modpack {pack_id} sem download_url")
    # The zip becomes executable code (mod jars). Plain HTTP is only acceptable
    # when the catalog also pins a digest we can verify after downloading.
    if not is_trusted_url(pack.download_url) and not pack.sha256:
        raise ValueError(
            f"Download inseguro para {pack_id}: use https:// ou publique o sha256 do zip no catálogo."
        )

    cache = cache_dir() / "modpacks"
    cache.mkdir(parents=True, exist_ok=True)
    zip_path = _contained(cache / f"{canonical_id}-{_slug(pack_version)}.zip", cache)

    if pack.sha256 and zip_path.exists() and _sha256_file(zip_path) == pack.sha256.lower():
        if tracker:
            tracker.set_detail(f"Usando {pack.name} do cache (sha256 confere)")
        return zip_path

    if tracker:
        tracker.set_phase("mods", f"Baixando {pack.name}…")
    _check_cancel(cancel_event)
    download_file(pack.download_url, zip_path, tracker=tracker, cancel_event=cancel_event)

    if pack.sha256:
        digest = _sha256_file(zip_path)
        if digest != pack.sha256.lower():
            zip_path.unlink(missing_ok=True)
            raise RuntimeError(f"SHA256 inválido para {pack_id} (esperado {pack.sha256[:12]}…)")
    return zip_path


def install_modpack(
    pack: ModpackInfo,
    tracker: Optional[ProgressTracker] = None,
    instance_name: str = "",
    cancel_event=None,
    catalog_origin: str = "",
) -> GameInstance:
    """Download pack zip (GitHub Releases asset) and create a local instance."""
    pack_id, pack_version, canonical_id = pack_identity(pack)
    zip_path = ensure_pack_zip(pack, tracker=tracker, cancel_event=cancel_event)

    from dataclasses import asdict

    from launcher.core.instances import instances_root

    existing = installed_instance_for(pack, catalog_origin)
    from launcher.core.loaders import (
        loader_profile_id,
        normalize_loader_name,
        normalize_loader_version,
    )

    loader_name = normalize_loader_name(pack.loader or "forge")
    created = existing is None
    if existing is not None:
        inst = existing
        previous_meta = asdict(inst)
    else:
        inst = create_instance(
            instance_name or pack.name,
            modpack_id=pack_id,
            modpack_version=pack_version,
            mc_version=pack.mc_version or MC_VERSION,
            forge_version=pack.loader_version or FORGE_VERSION,
            loader=loader_name,
            server_ip=pack.server_ip,
            server_port=pack.server_port,
            source="github",
            instance_id=canonical_id,
        )
        previous_meta = asdict(inst)

    _contained(inst.minecraft_path, instances_root())
    _check_cancel(cancel_event)
    try:
        install_modpack_zip(zip_path, inst, tracker=tracker, cancel_event=cancel_event)
    except BaseException:
        # A failed update must not leave instance.json claiming the new version.
        if created:
            shutil.rmtree(inst.root, ignore_errors=True)
        else:
            for key, value in previous_meta.items():
                setattr(inst, key, value)
            inst.save_meta()
        raise

    # Files are in place — only now this instance really is the new version.
    inst.name = instance_name or pack.name or inst.name
    inst.modpack_id = pack_id
    inst.modpack_version = pack_version
    inst.mc_version = pack.mc_version or inst.mc_version
    inst.loader = loader_name
    inst.forge_version = normalize_loader_version(
        loader_name, inst.mc_version, pack.loader_version or inst.forge_version
    )
    inst.forge_profile = loader_profile_id(loader_name, inst.mc_version, inst.forge_version)
    inst.server_ip = pack.server_ip
    inst.server_port = pack.server_port
    inst.source = "github"
    if catalog_origin:
        inst.extra["catalog_origin"] = catalog_origin
    inst.ensure_dirs()
    inst.save_meta()
    try:
        from launcher.core.cache_cleanup import cleanup_cache

        cleanup_cache()
    except Exception:
        pass
    return inst


# Back-compat alias (old R2-era name)
install_modpack_from_r2 = install_modpack
