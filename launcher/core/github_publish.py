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
    Create (or reuse) a GitHub Release with the pack zip + index.json.
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

    tag = f"v{_slug(version) or '1.0.0'}"
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

    # Create or get release
    release = None
    try:
        release = _gh_request("GET", f"/repos/{owner}/{repo}/releases/tags/{tag}", token)
    except GitHubError:
        release = None
    if not isinstance(release, dict):
        release = _gh_request(
            "POST",
            f"/repos/{owner}/{repo}/releases",
            token,
            data={
                "tag_name": tag,
                "name": f"{pack_name} {version}",
                "body": (
                    f"Modpack **{pack_name}** publicado pelo PUTs Launcher.\n\n"
                    f"- Minecraft `{mc_version}`\n"
                    f"- Loader `{loader}` `{loader_version}`\n"
                    f"- Use `owner/repo` = `{owner}/{repo}` no catálogo do launcher."
                ),
                "draft": False,
                "prerelease": False,
            },
        )
    if not isinstance(release, dict):
        raise GitHubError("Não consegui criar o Release.")

    release_id = release.get("id")
    upload_url_tmpl = str(release.get("upload_url") or "")
    # upload_url looks like …/assets{?name,label}
    upload_base = upload_url_tmpl.split("{", 1)[0]
    if not upload_base or not release_id:
        raise GitHubError("Release sem URL de upload.")

    # Delete existing assets with same names (update)
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "") in {zip_name, "index.json"}:
            try:
                _gh_request("DELETE", f"/repos/{owner}/{repo}/releases/assets/{asset['id']}", token)
            except GitHubError:
                pass

    zip_bytes = zip_path.read_bytes()
    _gh_request(
        "POST",
        f"{upload_base}?name={quote(zip_name)}",
        token,
        data=zip_bytes,
        content_type="application/zip",
        timeout=300,
    )

    zip_url = _release_asset_url(owner, repo, tag, zip_name)
    index = {
        "modpacks": [
            {
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
        ]
    }
    index_bytes = json.dumps(index, indent=2, ensure_ascii=False).encode("utf-8")
    _gh_request(
        "POST",
        f"{upload_base}?name=index.json",
        token,
        data=index_bytes,
        content_type="application/json",
        timeout=120,
    )

    catalog = f"{owner}/{repo}"
    if set_as_catalog:
        cfg.modpack_catalog = catalog
        cfg.modpack_index_url = ""
    cfg.save()

    # Cleanup staging zip (cache budget handles the rest)
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass

    return {
        "catalog": catalog,
        "tag": tag,
        "zip_url": zip_url,
        "release_url": str(release.get("html_url") or f"https://github.com/{owner}/{repo}/releases/tag/{tag}"),
        "share_hint": (
            f"Peça para os outros colocarem `{catalog}` em Opções → Catálogo de modpacks, "
            f"ou usem o Release: {release.get('html_url')}"
        ),
    }
