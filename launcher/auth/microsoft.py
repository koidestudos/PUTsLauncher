from __future__ import annotations

import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from minecraft_launcher_lib.microsoft_account import (
    AzureAppNotPermitted,
    complete_login,
    complete_refresh,
    get_secure_login_data,
    url_contains_auth_code,
)

from launcher.auth.session import GameSession, persist_microsoft_session
from launcher.config import (
    MS_CLIENT_ID,
    MS_LOCAL_CLIENT_ID,
    MS_LOCAL_PORT,
    MS_REDIRECT_URI,
    LauncherConfig,
)


class MicrosoftAuthError(RuntimeError):
    pass


StatusCb = Callable[[str], None]
# Called when automatic localhost capture isn't available — UI should ask user to paste URL.
AskUrlCb = Callable[[str], Optional[str]]


def _session_from_login(data: dict) -> tuple[GameSession, str]:
    name = data["name"]
    uid = data["id"]
    token = data["access_token"]
    refresh = data.get("refresh_token", "")
    session = GameSession(username=name, uuid=uid.replace("-", ""), access_token=token, offline=False)
    return session, refresh


def refresh_microsoft_session(cfg: LauncherConfig) -> GameSession:
    refresh = (cfg.microsoft_refresh_token or "").strip()
    if not refresh:
        raise MicrosoftAuthError("Nenhum refresh token. Faça login Microsoft de novo.")

    # Try local client first, then Prism native client.
    attempts = [
        (MS_LOCAL_CLIENT_ID, f"http://127.0.0.1:{MS_LOCAL_PORT}/authenticated"),
        (MS_CLIENT_ID, MS_REDIRECT_URI),
    ]
    if cfg.azure_client_id.strip():
        attempts.insert(0, (cfg.azure_client_id.strip(), f"http://127.0.0.1:{int(cfg.redirect_port or MS_LOCAL_PORT)}"))

    last_err: Exception | None = None
    for client_id, redirect in attempts:
        try:
            data = complete_refresh(client_id, None, redirect, refresh)
            session, new_refresh = _session_from_login(data)
            persist_microsoft_session(cfg, session, refresh_token=new_refresh or refresh)
            return session
        except AzureAppNotPermitted as exc:
            last_err = exc
            continue
        except Exception as exc:
            last_err = exc
            continue
    raise MicrosoftAuthError(f"Falha ao renovar sessão Microsoft: {last_err}")


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
                if state and qs.get("state", [state])[0] not in (None, state):
                    result["error"] = "State OAuth inválido."
                else:
                    result["code"] = qs.get("code", [None])[0]
                body = (
                    "<html><body style='margin:0;font-family:Segoe UI,sans-serif;"
                    "background:linear-gradient(160deg,#120e08,#2a1f0a);color:#f6efc8;"
                    "display:flex;align-items:center;justify-content:center;height:100vh'>"
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
        raise MicrosoftAuthError("Tempo esgotado aguardando login no navegador.")
    server.server_close()
    if result["error"]:
        raise MicrosoftAuthError(result["error"])
    if not result["code"]:
        raise MicrosoftAuthError("Código de autorização não recebido.")
    return result["code"]


def _finish_login(client_id: str, redirect: str, code: str, verifier: str, cfg: LauncherConfig) -> GameSession:
    try:
        data = complete_login(client_id, None, redirect, code, verifier)
    except AzureAppNotPermitted as exc:
        raise MicrosoftAuthError(
            "Este app Azure ainda não tem permissão Xbox Live/Minecraft."
        ) from exc
    except Exception as exc:
        raise MicrosoftAuthError(f"Falha no login Microsoft: {exc}") from exc
    session, refresh = _session_from_login(data)
    persist_microsoft_session(cfg, session, refresh_token=refresh)
    return session


def login_microsoft_browser(
    cfg: LauncherConfig,
    on_status: Optional[StatusCb] = None,
    ask_redirect_url: Optional[AskUrlCb] = None,
) -> GameSession:
    """
    Microsoft login that actually works for end users:

    1) Try HMCL public client + localhost callback (automatic)
    2) Fall back to Prism public client + nativeclient (paste URL once)
    """
    # --- Attempt 1: automatic localhost ---
    if on_status:
        on_status("Abrindo login Microsoft…")

    local_redirect = f"http://127.0.0.1:{MS_LOCAL_PORT}/authenticated"
    try:
        login_url, state, verifier = get_secure_login_data(MS_LOCAL_CLIENT_ID, local_redirect)
        webbrowser.open(login_url)
        if on_status:
            on_status("Faça login na janela do navegador…")
        code = _capture_localhost(MS_LOCAL_PORT, state, timeout=120.0)
        if on_status:
            on_status("Validando conta Minecraft…")
        return _finish_login(MS_LOCAL_CLIENT_ID, local_redirect, code, verifier, cfg)
    except MicrosoftAuthError as exc:
        # Port busy / timeout / etc → fall through to paste flow
        local_err = str(exc)
    except OSError as exc:
        local_err = str(exc)
    except Exception as exc:
        local_err = str(exc)

    # --- Attempt 2: Prism nativeclient + paste URL ---
    if on_status:
        on_status("Abrindo login Microsoft (modo alternativo)…")
    login_url, state, verifier = get_secure_login_data(MS_CLIENT_ID, MS_REDIRECT_URI)
    webbrowser.open(login_url)

    if ask_redirect_url is None:
        raise MicrosoftAuthError(
            f"Login automático falhou ({local_err}). "
            "Reinicie o launcher e tente de novo."
        )

    prompt = (
        "Depois de entrar na Microsoft, a página fica em branco ou mostra um erro de localhost.\n"
        "Copie a URL completa da barra de endereço do navegador e cole abaixo."
    )
    pasted = ask_redirect_url(prompt)
    if not pasted:
        raise MicrosoftAuthError("Login cancelado.")

    pasted = pasted.strip()
    if not url_contains_auth_code(pasted) and "code=" not in pasted:
        raise MicrosoftAuthError("URL inválida — precisa conter code=…")

    qs = parse_qs(urlparse(pasted).query)
    if qs.get("state", [None])[0] not in (None, state) and qs.get("state", [None])[0] != state:
        # Some nativeclient responses omit matching — only fail on explicit mismatch
        got = qs.get("state", [None])[0]
        if got and got != state:
            raise MicrosoftAuthError("State OAuth inválido. Tente de novo.")
    code = qs.get("code", [None])[0]
    if not code:
        raise MicrosoftAuthError("Não achei o código na URL colada.")

    if on_status:
        on_status("Validando conta Minecraft…")
    return _finish_login(MS_CLIENT_ID, MS_REDIRECT_URI, code, verifier, cfg)
