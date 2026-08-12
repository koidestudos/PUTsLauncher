from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from launcher.config import LauncherConfig, puts_home


@dataclass
class GameSession:
    username: str
    uuid: str
    access_token: str
    offline: bool = True


def offline_session(username: str) -> GameSession:
    name = (username or "Steve").strip()[:16] or "Steve"
    # Offline UUIDs follow the offline player scheme used by CraftBukkit / Forge offline.
    offline_uuid = str(uuid.uuid3(uuid.NAMESPACE_DNS, f"OfflinePlayer:{name}"))
    return GameSession(
        username=name,
        uuid=offline_uuid.replace("-", ""),
        access_token="0",
        offline=True,
    )


def _candidate_account_files() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    appdata = Path(os_environ_appdata())
    candidates.extend(
        [
            appdata / ".minecraft" / "launcher_accounts.json",
            appdata / ".minecraft" / "launcher_accounts_microsoft_store.json",
            home / ".minecraft" / "launcher_accounts.json",
            puts_home() / "imported_accounts.json",
        ]
    )
    return candidates


def os_environ_appdata() -> str:
    import os

    if os.name == "nt":
        return os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return str(Path.home())


def list_official_launcher_accounts() -> list[dict[str, Any]]:
    """Read Microsoft profiles saved by the official Minecraft Launcher (if present)."""
    accounts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in _candidate_account_files():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw_accounts = data.get("accounts") or {}
        if isinstance(raw_accounts, dict):
            iterable = raw_accounts.values()
        elif isinstance(raw_accounts, list):
            iterable = raw_accounts
        else:
            continue
        for acc in iterable:
            if not isinstance(acc, dict):
                continue
            profile = acc.get("minecraftProfile") or acc.get("profile") or {}
            name = profile.get("name") or acc.get("minecraftProfileName") or ""
            uid = profile.get("id") or acc.get("minecraftProfileId") or ""
            refresh = (
                acc.get("accessToken")
                or (acc.get("local") or {}).get("refreshToken")
                or acc.get("refreshToken")
                or ""
            )
            # Official launcher stores MSA tokens nested differently across versions.
            msa = acc.get("msa") or {}
            refresh = refresh or msa.get("refreshToken") or ""
            access = acc.get("accessToken") or ""
            if not name:
                continue
            key = f"{name}:{uid}"
            if key in seen:
                continue
            seen.add(key)
            accounts.append(
                {
                    "name": name,
                    "uuid": uid,
                    "access_token": access,
                    "refresh_token": refresh,
                    "source": str(path),
                }
            )
    return accounts


def session_from_config_microsoft(cfg: LauncherConfig) -> Optional[GameSession]:
    if not cfg.microsoft_name or not cfg.microsoft_access_token:
        return None
    return GameSession(
        username=cfg.microsoft_name,
        uuid=(cfg.microsoft_uuid or "").replace("-", ""),
        access_token=cfg.microsoft_access_token,
        offline=False,
    )


def persist_microsoft_session(cfg: LauncherConfig, session: GameSession, refresh_token: str = "") -> None:
    cfg.auth_mode = "microsoft"
    cfg.microsoft_name = session.username
    cfg.microsoft_uuid = session.uuid
    cfg.microsoft_access_token = session.access_token
    if refresh_token:
        cfg.microsoft_refresh_token = refresh_token
    cfg.username = session.username

    # Upsert into saved accounts list for multi-account switcher
    accounts = list(cfg.saved_accounts or [])
    entry = {
        "name": session.username,
        "uuid": session.uuid,
        "access_token": session.access_token,
        "refresh_token": refresh_token or cfg.microsoft_refresh_token,
    }
    replaced = False
    for i, acc in enumerate(accounts):
        if (acc.get("uuid") or "").replace("-", "") == session.uuid.replace("-", "") or acc.get("name") == session.username:
            accounts[i] = entry
            replaced = True
            break
    if not replaced:
        accounts.append(entry)
    cfg.saved_accounts = accounts
    cfg.save()


def switch_account(cfg: LauncherConfig, account: dict[str, Any]) -> None:
    cfg.auth_mode = "microsoft"
    cfg.microsoft_name = account.get("name") or ""
    cfg.microsoft_uuid = account.get("uuid") or ""
    cfg.microsoft_access_token = account.get("access_token") or ""
    cfg.microsoft_refresh_token = account.get("refresh_token") or ""
    cfg.username = cfg.microsoft_name or cfg.username
    cfg.save()


def logout_microsoft(cfg: LauncherConfig, remove_current: bool = True) -> None:
    current_uuid = (cfg.microsoft_uuid or "").replace("-", "")
    if remove_current and cfg.saved_accounts:
        cfg.saved_accounts = [
            a
            for a in cfg.saved_accounts
            if (a.get("uuid") or "").replace("-", "") != current_uuid and a.get("name") != cfg.microsoft_name
        ]
    cfg.microsoft_name = ""
    cfg.microsoft_uuid = ""
    cfg.microsoft_access_token = ""
    cfg.microsoft_refresh_token = ""
    # If another saved account remains, switch to it
    if cfg.saved_accounts:
        switch_account(cfg, cfg.saved_accounts[0])
    else:
        cfg.auth_mode = "offline"
        cfg.save()
