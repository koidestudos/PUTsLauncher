from __future__ import annotations

import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

import requests
from minecraft_launcher_lib.microsoft_account import (
    AccountNotOwnMinecraft,
    AzureAppNotPermitted,
    authenticate_with_minecraft,
    authenticate_with_xbl,
    authenticate_with_xsts,
    complete_login,
    complete_refresh,
    get_profile,
    get_secure_login_data,
    get_user_agent,
    url_contains_auth_code,
)

from launcher.auth.session import GameSession, persist_microsoft_session
from launcher.config import MS_CLIENT_ID, MS_LOCAL_PORT, MS_REDIRECT_URI, LauncherConfig


class MicrosoftAuthError(RuntimeError):
    pass


StatusCb = Callable[[str], None]
# UI shows the device code to the user (user_code, verification_uri).
DeviceCodeCb = Callable[[str, str], None]


TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
SCOPE = "XboxLive.signin offline_access"


def _session_from_login(data: dict) -> tuple[GameSession, str]:
    name = data["name"]
    uid = data["id"]
    token = data["access_token"]
    refresh = data.get("refresh_token", "")
    session = GameSession(username=name, uuid=uid.replace("-", ""), access_token=token, offline=False)
    return session, refresh


def _client_id(cfg: LauncherConfig) -> str:
    custom = (cfg.azure_client_id or "").strip()
    return custom or MS_CLIENT_ID


def _redirect_uri(cfg: LauncherConfig) -> str:
    if (cfg.azure_client_id or "").strip():
        port = int(cfg.redirect_port or MS_LOCAL_PORT)
        return f"http://127.0.0.1:{port}"
    return MS_REDIRECT_URI


def _finish_from_ms_token(ms_access_token: str, ms_refresh_token: str, cfg: LauncherConfig) -> GameSession:
    """Xbox Live → XSTS → Minecraft profile, starting from a Microsoft access token."""
    try:
        xbl = authenticate_with_xbl(ms_access_token)
        xbl_token = xbl["Token"]
        userhash = xbl["DisplayClaims"]["xui"][0]["uhs"]
        xsts = authenticate_with_xsts(xbl_token)
        account = authenticate_with_minecraft(userhash, xsts["Token"])
        if "access_token" not in account:
            raise AzureAppNotPermitted()
        profile = get_profile(account["access_token"])
        if "error" in profile and profile["error"] == "NOT_FOUND":
            raise AccountNotOwnMinecraft()
        profile["access_token"] = account["access_token"]
        profile["refresh_token"] = ms_refresh_token
    except AzureAppNotPermitted as exc:
        raise MicrosoftAuthError(
            "Sem permissão para a API do Minecraft nesta conta/app Azure."
        ) from exc
    except AccountNotOwnMinecraft as exc:
        raise MicrosoftAuthError("Essa conta Microsoft não tem Minecraft Java.") from exc
    except MicrosoftAuthError:
        raise
    except Exception as exc:
        raise MicrosoftAuthError(f"Falha ao validar Minecraft: {exc}") from exc

    session, refresh = _session_from_login(profile)
    # Remember which client id minted this refresh token
    cfg.azure_client_id = _client_id(cfg)
    persist_microsoft_session(cfg, session, refresh_token=refresh or ms_refresh_token)
    return session


def refresh_microsoft_session(cfg: LauncherConfig) -> GameSession:
    refresh = (cfg.microsoft_refresh_token or "").strip()
    if not refresh:
        raise MicrosoftAuthError("Nenhum refresh token. Faça login Microsoft de novo.")

    client_id = _client_id(cfg)
    redirect = _redirect_uri(cfg)
    try:
        data = complete_refresh(client_id, None, redirect, refresh)
        session, new_refresh = _session_from_login(data)
        persist_microsoft_session(cfg, session, refresh_token=new_refresh or refresh)
        return session
    except AzureAppNotPermitted as exc:
        raise MicrosoftAuthError("App Azure sem permissão Minecraft.") from exc
    except Exception as exc:
        raise MicrosoftAuthError(f"Falha ao renovar sessão: {exc}") from exc


def _device_code_login(
    cfg: LauncherConfig,
    on_status: Optional[StatusCb] = None,
    on_device_code: Optional[DeviceCodeCb] = None,
) -> GameSession:
    client_id = _client_id(cfg)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": get_user_agent(),
    }
    start = requests.post(
        DEVICE_CODE_URL,
        data={"client_id": client_id, "scope": SCOPE},
        headers=headers,
        timeout=30,
    )
    payload = start.json()
    if "device_code" not in payload:
        raise MicrosoftAuthError(
            f"Não foi possível iniciar login Microsoft: {payload.get('error_description') or payload}"
        )

    user_code = payload["user_code"]
    device_code = payload["device_code"]
    verify_uri = payload.get("verification_uri") or "https://www.microsoft.com/link"
    interval = max(int(payload.get("interval") or 5), 3)
    expires = int(payload.get("expires_in") or 900)

    if on_status:
        on_status(f"Código: {user_code} — abra {verify_uri}")
    if on_device_code:
        on_device_code(user_code, verify_uri)

    # Open the link page; user types the code there.
    try:
        webbrowser.open(verify_uri)
    except Exception:
        pass

    deadline = time.time() + expires
    while time.time() < deadline:
        time.sleep(interval)
        poll = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            },
            headers=headers,
            timeout=30,
        )
        data = poll.json()
        if "access_token" in data:
            if on_status:
                on_status("Conta Microsoft OK — validando Minecraft…")
            return _finish_from_ms_token(data["access_token"], data.get("refresh_token", ""), cfg)

        err = data.get("error")
        if err in ("authorization_pending", "slow_down"):
            if err == "slow_down":
                interval += 2
            continue
        if err == "expired_token":
            raise MicrosoftAuthError("Código expirou. Clique em login de novo.")
        if err == "access_denied":
            raise MicrosoftAuthError("Login cancelado na Microsoft.")
        raise MicrosoftAuthError(data.get("error_description") or f"Erro OAuth: {err}")

    raise MicrosoftAuthError("Tempo esgotado. Tente o login de novo.")


def _capture_localhost(port: int, state: str, timeout: float = 180.0) -> str:
    result: dict = {"code": None, "error": None}
    done = threading.Event()
    redirect_base = f"http://127.0.0.1:{port}"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            full = f"{redirect_base}{self.path}"
            parsed = urlparse(self.path)
            if url_contains_auth_code(full) or "code=" in self.path:
                qs = parse_qs(parsed.query)
                got_state = qs.get("state", [None])[0]
                if state and got_state and got_state != state:
                    result["error"] = "State OAuth inválido."
                else:
                    result["code"] = qs.get("code", [None])[0]
                body = (
                    "<html><body style='margin:0;font-family:Segoe UI,sans-serif;"
                    "background:#120e08;color:#f6efc8;display:flex;align-items:center;"
                    "justify-content:center;height:100vh'>"
                    "<div style='text-align:center'><h1 style='color:#f0d24a'>Login OK</h1>"
                    "<p>Pode voltar ao PUTs Launcher.</p></div></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(400)
                self.end_headers()
            done.set()

        def log_message(self, format, *args):  # noqa: A003
            return

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    if not done.wait(timeout=timeout):
        server.server_close()
        raise MicrosoftAuthError("Tempo esgotado aguardando o navegador.")
    server.server_close()
    if result["error"]:
        raise MicrosoftAuthError(result["error"])
    if not result["code"]:
        raise MicrosoftAuthError("Código de autorização não recebido.")
    return result["code"]


def _browser_redirect_login(cfg: LauncherConfig, on_status: Optional[StatusCb] = None) -> GameSession:
    client_id = _client_id(cfg)
    redirect = _redirect_uri(cfg)
    port = urlparse(redirect).port or MS_LOCAL_PORT
    login_url, state, verifier = get_secure_login_data(client_id, redirect)
    if on_status:
        on_status("Abrindo Microsoft no navegador…")
    webbrowser.open(login_url)
    code = _capture_localhost(port, state, timeout=180.0)
    if on_status:
        on_status("Validando conta Minecraft…")
    try:
        data = complete_login(client_id, None, redirect, code, verifier)
    except AzureAppNotPermitted as exc:
        raise MicrosoftAuthError("App Azure sem permissão Minecraft.") from exc
    except AccountNotOwnMinecraft as exc:
        raise MicrosoftAuthError("Essa conta Microsoft não tem Minecraft Java.") from exc
    except Exception as exc:
        raise MicrosoftAuthError(f"Falha no login Microsoft: {exc}") from exc
    session, refresh = _session_from_login(data)
    cfg.azure_client_id = client_id
    persist_microsoft_session(cfg, session, refresh_token=refresh)
    return session


def login_microsoft_browser(
    cfg: LauncherConfig,
    on_status: Optional[StatusCb] = None,
    ask_redirect_url=None,  # kept for API compat; unused
    on_device_code: Optional[DeviceCodeCb] = None,
) -> GameSession:
    """
    Microsoft login that works with personal (@outlook/@hotmail) accounts.

    Primary: device code (microsoft.com/link) — no Azure redirect issues.
    Fallback: localhost browser redirect with the same client id.
    """
    try:
        return _device_code_login(cfg, on_status=on_status, on_device_code=on_device_code)
    except MicrosoftAuthError as device_err:
        if on_status:
            on_status(f"Device code falhou ({device_err}). Tentando navegador…")
        try:
            return _browser_redirect_login(cfg, on_status=on_status)
        except Exception as browser_err:
            raise MicrosoftAuthError(
                f"Login Microsoft falhou.\nDevice code: {device_err}\nNavegador: {browser_err}"
            ) from browser_err
