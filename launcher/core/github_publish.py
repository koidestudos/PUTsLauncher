from __future__ import annotations

import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from launcher.config import LauncherConfig, cache_dir
from launcher.core.modpacks import USER_AGENT, _slug, parse_github_repo

GITHUB_API = "https://api.github.com"

# Stable catalog release — one Release holds index.json + every pack zip.
MODPACKS_RELEASE_TAG = "modpacks"
MODPACKS_RELEASE_NAME = "Modpacks"


@dataclass
class GitHubUser:
    login: str
    name: str = ""
    avatar_url: str = ""


@dataclass
class GitHubRepo:
    full_name: str  # owner/repo
    name: str
    private: bool = False
    html_url: str = ""
    default_branch: str = "main"


class GitHubError(RuntimeError):
    pass


def _gh_request(
    method: str,
    path: str,
    token: str,
    *,
    data: Optional[dict | bytes] = None,
    content_type: str = "application/json",
    timeout: int = 60,
) -> Any:
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    body: Optional[bytes] = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            body = bytes(data)
            headers["Content-Type"] = content_type
        else:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "json" in ctype or raw[:1] in (b"{", b"["):
                return json.loads(raw.decode("utf-8"))
            return raw
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        raise GitHubError(f"GitHub HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise GitHubError(f"Falha de rede GitHub: {exc.reason}") from exc


def validate_github_token(token: str) -> GitHubUser:
    token = (token or "").strip()
    if not token:
        raise GitHubError("Cole um Personal Access Token do GitHub.")
    data = _gh_request("GET", "/user", token)
    if not isinstance(data, dict) or not data.get("login"):
        raise GitHubError("Token inválido.")
    return GitHubUser(
        login=str(data["login"]),
        name=str(data.get("name") or ""),
        avatar_url=str(data.get("avatar_url") or ""),
    )


def list_user_repos(token: str, *, limit: int = 60) -> list[GitHubRepo]:
    out: list[GitHubRepo] = []
    page = 1
    while len(out) < limit:
        batch = _gh_request(
            "GET",
            f"/user/repos?per_page=30&page={page}&sort=updated&affiliation=owner,collaborator",
            token,
        )
        if not isinstance(batch, list) or not batch:
            break
        for repo in batch:
            if not isinstance(repo, dict):
                continue
            out.append(
                GitHubRepo(
                    full_name=str(repo.get("full_name") or ""),
                    name=str(repo.get("name") or ""),
                    private=bool(repo.get("private")),
                    html_url=str(repo.get("html_url") or ""),
                    default_branch=str(repo.get("default_branch") or "main"),
                )
            )
            if len(out) >= limit:
                break
        if len(batch) < 30:
            break
        page += 1
    return out


def create_repo(token: str, name: str, *, private: bool = False, description: str = "") -> GitHubRepo:
    slug = _slug(name) or "puts-modpacks"
    data = _gh_request(
        "POST",
        "/user/repos",
        token,
        data={
            "name": slug,
            "private": private,
            "description": description or "Modpacks publicados pelo PUTs Launcher",
            "auto_init": True,
        },
    )
    if not isinstance(data, dict):
        raise GitHubError("Não consegui criar o repositório.")
    return GitHubRepo(
        full_name=str(data.get("full_name") or f"{data.get('owner', {}).get('login')}/{slug}"),
        name=str(data.get("name") or slug),
        private=bool(data.get("private")),
        html_url=str(data.get("html_url") or ""),
        default_branch=str(data.get("default_branch") or "main"),
    )


def save_github_session(cfg: LauncherConfig, token: str, user: GitHubUser, repo: str = "") -> None:
    cfg.github_token = token.strip()
    cfg.github_login = user.login
    if repo:
        cfg.github_publish_repo = repo.strip()
    cfg.save()


def clear_github_session(cfg: LauncherConfig) -> None:
    cfg.github_token = ""
    cfg.github_login = ""
    # keep publish repo preference
    cfg.save()


def build_modpack_zip(
    *,
    instance_minecraft: Path,
    pack_name: str,
    pack_id: str,
    version: str,
    mc_version: str,
    loader: str,
    loader_version: str,
    dest_zip: Path,
) -> Path:
    """Zip mods/ (+ light metadata) for a GitHub Release asset."""
    mods = instance_minecraft / "mods"
    if not mods.is_dir():
        raise GitHubError("A instância não tem pasta mods/.")
    jars = sorted(p for p in mods.glob("*.jar") if p.is_file())
    if not jars:
        raise GitHubError("Nenhum .jar em mods/ para publicar.")

    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": pack_id,
        "name": pack_name,
        "version": version,
        "mc_version": mc_version,
        "loader": loader,
        "loader_version": loader_version,
        "forge_version": loader_version,
    }
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("pack.meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
        for jar in jars:
            zf.write(jar, arcname=f"mods/{jar.name}")
    return dest_zip


def _release_asset_url(owner: str, repo: str, tag: str, filename: str) -> str:
    return f"https://github.com/{owner}/{repo}/releases/download/{tag}/{quote(filename)}"


def _is_modpacks_release(rel: dict) -> bool:
    tag = str(rel.get("tag_name") or "").strip().lower()
    name = str(rel.get("name") or "").strip().lower()
    return tag in {MODPACKS_RELEASE_TAG, "modpack"} or name in {
        MODPACKS_RELEASE_NAME.lower(),
        "modpack",
    }


def merge_catalog_index(existing: Any, entry: dict[str, Any]) -> dict[str, Any]:
    """Replace or append a pack entry by id inside ``{ "modpacks": [...] }``."""
    packs: list[Any] = []
    if isinstance(existing, dict) and isinstance(existing.get("modpacks"), list):
        packs = list(existing["modpacks"])
    elif isinstance(existing, list):
        packs = list(existing)
    pack_id = str(entry.get("id") or "").strip()
    kept: list[Any] = []
    for item in packs:
        if not isinstance(item, dict):
            continue
        if pack_id and str(item.get("id") or "").strip() == pack_id:
            continue
        kept.append(item)
    kept.append(entry)
    return {"modpacks": kept}


def _load_release_index(release: dict, token: str) -> dict[str, Any]:
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "").lower() != "index.json":
            continue
        url = str(asset.get("browser_download_url") or "").strip()
        if not url:
            continue
        try:
            raw = _gh_request("GET", url, token, timeout=60)
        except GitHubError:
            continue
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, (bytes, bytearray)):
            try:
                data = json.loads(bytes(raw).decode("utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                return data
    return {"modpacks": []}


def _get_or_create_modpacks_release(owner: str, repo: str, token: str) -> dict[str, Any]:
    release: Any = None
    try:
        release = _gh_request(
            "GET", f"/repos/{owner}/{repo}/releases/tags/{MODPACKS_RELEASE_TAG}", token
        )
    except GitHubError:
        release = None

    if isinstance(release, dict) and release.get("id"):
        # Keep the display name as "Modpacks" even if an older publish renamed it.
        if str(release.get("name") or "") != MODPACKS_RELEASE_NAME:
            try:
                release = _gh_request(
                    "PATCH",
                    f"/repos/{owner}/{repo}/releases/{release['id']}",
                    token,
                    data={"name": MODPACKS_RELEASE_NAME},
                )
            except GitHubError:
                pass
        if isinstance(release, dict):
            return release

    release = _gh_request(
        "POST",
        f"/repos/{owner}/{repo}/releases",
        token,
        data={
            "tag_name": MODPACKS_RELEASE_TAG,
            "name": MODPACKS_RELEASE_NAME,
            "body": (
                "Catálogo de modpacks do **PUTs Launcher**.\n\n"
                "Este Release é atualizado a cada publicação — não crie um Release "
                "por versão do pack.\n\n"
                f"- No launcher: Opções → Catálogo = `{owner}/{repo}`\n"
                "- Assets: `index.json` + zips dos packs"
            ),
            "draft": False,
            "prerelease": False,
        },
    )
    if not isinstance(release, dict):
        raise GitHubError("Não consegui criar o Release Modpacks.")
    return release


def _delete_assets_named(owner: str, repo: str, release: dict, token: str, names: set[str]) -> None:
    wanted = {n.lower() for n in names}
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "").lower() not in wanted:
            continue
        try:
            _gh_request("DELETE", f"/repos/{owner}/{repo}/releases/assets/{asset['id']}", token)
        except GitHubError:
            pass


def _upload_asset(
    upload_base: str,
    token: str,
    filename: str,
    payload: bytes,
    content_type: str,
    *,
    timeout: int = 300,
) -> None:
    _gh_request(
        "POST",
        f"{upload_base}?name={quote(filename)}",
        token,
        data=payload,
        content_type=content_type,
        timeout=timeout,
    )


def publish_modpack_release(
    cfg: LauncherConfig,
    *,
    instance_minecraft: Path,
    pack_name: str,
    pack_id: str,
    version: str,
    mc_version: str,
    loader: str,
    loader_version: str,
    repo_full_name: Optional[str] = None,
    set_as_catalog: bool = True,
) -> dict[str, str]:
    """
    Publish (or update) a pack on the stable GitHub Release named **Modpacks**.

    Merges into existing ``index.json`` so several packs share one release.
    Returns dict with catalog, release_url, zip_url, tag.
    """
    token = (cfg.github_token or "").strip()
    if not token:
        raise GitHubError("Conecte o GitHub antes de publicar (token em Opções / Criar modpack).")

    repo_ref = (repo_full_name or cfg.github_publish_repo or "").strip()
    parsed = parse_github_repo(repo_ref) if repo_ref else None
    if not parsed:
        raise GitHubError("Escolha um repositório no formato dono/repo.")
    owner, repo = parsed
    cfg.github_publish_repo = f"{owner}/{repo}"

    tag = MODPACKS_RELEASE_TAG
    zip_name = f"{_slug(pack_id) or 'pack'}-{_slug(version) or '1.0.0'}.zip"
    staging = cache_dir() / "publish"
    staging.mkdir(parents=True, exist_ok=True)
    zip_path = staging / zip_name
    build_modpack_zip(
        instance_minecraft=instance_minecraft,
        pack_name=pack_name,
        pack_id=pack_id,
        version=version,
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        dest_zip=zip_path,
    )

    release = _get_or_create_modpacks_release(owner, repo, token)
    release_id = release.get("id")
    upload_url_tmpl = str(release.get("upload_url") or "")
    upload_base = upload_url_tmpl.split("{", 1)[0]
    if not upload_base or not release_id:
        raise GitHubError("Release Modpacks sem URL de upload.")

    existing_index = _load_release_index(release, token)
    entry = {
        "id": pack_id,
        "name": pack_name,
        "version": version,
        "mc_version": mc_version,
        "loader": loader,
        "loader_version": loader_version,
        "forge_version": loader_version,
        "description": f"Publicado via PUTs Launcher · {time.strftime('%Y-%m-%d')}",
        "download_url": zip_name,
    }
    index = merge_catalog_index(existing_index, entry)

    # Replace only this pack's zip + the catalog index; leave other pack zips alone.
    _delete_assets_named(owner, repo, release, token, {zip_name, "index.json"})

    zip_bytes = zip_path.read_bytes()
    _upload_asset(upload_base, token, zip_name, zip_bytes, "application/zip", timeout=300)

    index_bytes = json.dumps(index, indent=2, ensure_ascii=False).encode("utf-8")
    _upload_asset(upload_base, token, "index.json", index_bytes, "application/json", timeout=120)

    # Refresh release notes with pack count
    try:
        names = [
            str(p.get("name") or p.get("id") or "?")
            for p in index.get("modpacks") or []
            if isinstance(p, dict)
        ]
        body = (
            "Catálogo de modpacks do **PUTs Launcher**.\n\n"
            f"Packs neste Release ({len(names)}):\n"
            + "".join(f"- {n}\n" for n in names)
            + f"\nNo launcher: Opções → Catálogo = `{owner}/{repo}`\n"
        )
        _gh_request(
            "PATCH",
            f"/repos/{owner}/{repo}/releases/{release_id}",
            token,
            data={"name": MODPACKS_RELEASE_NAME, "body": body},
        )
    except GitHubError:
        pass

    zip_url = _release_asset_url(owner, repo, tag, zip_name)
    catalog = f"{owner}/{repo}"
    if set_as_catalog:
        cfg.modpack_catalog = catalog
        cfg.modpack_index_url = ""
    cfg.save()

    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass

    release_url = str(
        release.get("html_url") or f"https://github.com/{owner}/{repo}/releases/tag/{tag}"
    )
    return {
        "catalog": catalog,
        "tag": tag,
        "zip_url": zip_url,
        "release_url": release_url,
        "share_hint": (
            f"O pack está no Release **Modpacks**. "
            f"Em Opções → Catálogo de modpacks use `{catalog}`, "
            f"depois abra **+ Modpack**. "
            f"Link: {release_url}"
        ),
    }
