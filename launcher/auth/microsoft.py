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
from launcher.config import LauncherConfig


class MicrosoftAuthError(RuntimeError):
    pass


def _session_from_login(data: dict) -> tuple[GameSession, str]:
    name = data["name"]
    uid = data["id"]
    token = data["access_token"]
    refresh = data.get("refresh_token", "")
    session = GameSession(username=name, uuid=uid.replace("-", ""), access_token=token, offline=False)
    return session, refresh


def refresh_microsoft_session(cfg: LauncherConfig) -> GameSession:
    client_id = (cfg.azure_client_id or "").strip()
    if not client_id:
        raise MicrosoftAuthError(
            "Configure um Azure Client ID nas configurações para renovar a sessão Microsoft."
        )
    if not cfg.microsoft_refresh_token:
        raise MicrosoftAuthError("Nenhum refresh token salvo. Faça login Microsoft novamente.")
    redirect_url = f"http://127.0.0.1:{int(cfg.redirect_port or 27845)}"
    try:
        data = complete_refresh(client_id, None, redirect_url, cfg.microsoft_refresh_token)
    except AzureAppNotPermitted as exc:
        raise MicrosoftAuthError(
            "Seu Azure App ainda não tem permissão Xbox Live / Minecraft. "
            "Use modo Offline ou importe a conta do Minecraft Launcher oficial."
        ) from exc
    except Exception as exc:
        raise MicrosoftAuthError(f"Falha ao renovar token Microsoft: {exc}") from exc
    session, refresh = _session_from_login(data)
    persist_microsoft_session(cfg, session, refresh_token=refresh or cfg.microsoft_refresh_token)
    return session


def login_microsoft_browser(
    cfg: LauncherConfig,
    on_status: Optional[Callable[[str], None]] = None,
) -> GameSession:
    """Open browser OAuth and capture redirect on localhost."""
    client_id = (cfg.azure_client_id or "").strip()
    if not client_id:
        raise MicrosoftAuthError(
            "Para login Microsoft direto, cole um Azure Application (client) ID em Configurações.\n"
            "Alternativas: modo Offline, ou use uma conta já logada no Minecraft Launcher oficial."
        )

    port = int(cfg.redirect_port or 27845)
    redirect_url = f"http://127.0.0.1:{port}"
    login_url, state, code_verifier = get_secure_login_data(client_id, redirect_url)

    result: dict = {"code": None, "error": None}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            full = f"{redirect_url}{self.path}"
            if url_contains_auth_code(full):
                qs = parse_qs(parsed.query)
                if qs.get("state", [None])[0] != state:
                    result["error"] = "State OAuth inválido."
                else:
                    result["code"] = qs.get("code", [None])[0]
                body = (
                    "<html><body style='font-family:sans-serif;background:#0b1c18;color:#e8f5e9;"
                    "display:flex;align-items:center;justify-content:center;height:100vh'>"
                    "<div><h2>Login concluído</h2><p>Pode voltar ao PUTs Launcher.</p></div>"
                    "</body></html>"
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

    if on_status:
        on_status("Abrindo navegador para login Microsoft…")

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    webbrowser.open(login_url)

    if not done.wait(timeout=300):
        server.server_close()
        raise MicrosoftAuthError("Tempo esgotado aguardando login Microsoft.")

    server.server_close()
    if result["error"]:
        raise MicrosoftAuthError(result["error"])
    if not result["code"]:
        raise MicrosoftAuthError("Código de autorização não recebido.")

    if on_status:
        on_status("Trocando código por token Minecraft…")

    try:
        data = complete_login(client_id, None, redirect_url, result["code"], code_verifier)
    except AzureAppNotPermitted as exc:
        raise MicrosoftAuthError(
            "Azure App sem permissão para Xbox Live/Minecraft.\n"
            "Para SMPs privados, use Offline. Para premium, importe a conta do launcher oficial."
        ) from exc
    except Exception as exc:
        raise MicrosoftAuthError(f"Falha no login Microsoft: {exc}") from exc

    session, refresh = _session_from_login(data)
    persist_microsoft_session(cfg, session, refresh_token=refresh)
    return session
