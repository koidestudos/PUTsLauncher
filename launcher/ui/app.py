from __future__ import annotations

import threading
import traceback
from typing import Optional

import customtkinter as ctk

from launcher import __app_name__, __version__
from launcher.auth import (
    MicrosoftAuthError,
    list_official_launcher_accounts,
    login_microsoft_browser,
    offline_session,
    persist_microsoft_session,
    refresh_microsoft_session,
    session_from_config_microsoft,
)
from launcher.auth.session import GameSession
from launcher.config import FORGE_VERSION, MC_VERSION, LauncherConfig, mods_source_dir
from launcher.core import forge_installed, list_bundled_mods, prepare_and_launch
from launcher.ui.theme import COLORS, FONTS


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")


class PUTsLauncherApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = LauncherConfig.load()
        self.title(f"{__app_name__}  ·  SMP")
        self.geometry("920x620")
        self.minsize(860, 560)
        self.configure(fg_color=COLORS["bg0"])
        self._busy = False
        self._fade_widgets: list[ctk.CTkBaseClass] = []

        self._build()
        self.after(80, self._intro_motion)
        self.after(200, self._refresh_mod_count)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        shell = ctk.CTkFrame(self, fg_color=COLORS["bg0"], corner_radius=0)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.grid_columnconfigure(0, weight=5)
        shell.grid_columnconfigure(1, weight=4)
        shell.grid_rowconfigure(0, weight=1)

        # Left: brand / atmosphere
        hero = ctk.CTkFrame(shell, fg_color=COLORS["bg1"], corner_radius=0)
        hero.grid(row=0, column=0, sticky="nsew")
        hero.grid_rowconfigure(0, weight=1)
        hero.grid_columnconfigure(0, weight=1)

        hero_inner = ctk.CTkFrame(hero, fg_color="transparent")
        hero_inner.grid(row=0, column=0, sticky="nsew", padx=42, pady=42)
        hero_inner.grid_columnconfigure(0, weight=1)
        hero_inner.grid_rowconfigure(3, weight=1)

        brand = ctk.CTkLabel(
            hero_inner,
            text="PUTs",
            font=FONTS["display"],
            text_color=COLORS["accent"],
            anchor="w",
        )
        brand.grid(row=0, column=0, sticky="w")
        self._fade_widgets.append(brand)

        subtitle = ctk.CTkLabel(
            hero_inner,
            text="LAUNCHER",
            font=FONTS["title"],
            text_color=COLORS["text"],
            anchor="w",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(0, 18))
        self._fade_widgets.append(subtitle)

        tagline = ctk.CTkLabel(
            hero_inner,
            text="Entre no SMP com o pack certo.\nForge, mods e conta — sem gambiarra.",
            font=FONTS["body"],
            text_color=COLORS["muted"],
            justify="left",
            anchor="w",
        )
        tagline.grid(row=2, column=0, sticky="w")
        self._fade_widgets.append(tagline)

        meta = ctk.CTkFrame(hero_inner, fg_color=COLORS["panel"], corner_radius=12)
        meta.grid(row=4, column=0, sticky="ew", pady=(24, 0))
        self.mod_label = ctk.CTkLabel(
            meta,
            text="Mods do pack: …",
            font=FONTS["small"],
            text_color=COLORS["text"],
            anchor="w",
        )
        self.mod_label.pack(fill="x", padx=16, pady=(14, 4))
        self.forge_label = ctk.CTkLabel(
            meta,
            text=f"Minecraft {MC_VERSION}  ·  Forge {FORGE_VERSION}",
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
        )
        self.forge_label.pack(fill="x", padx=16, pady=(0, 14))
        self._fade_widgets.append(meta)

        # Right: controls
        panel = ctk.CTkFrame(shell, fg_color=COLORS["bg2"], corner_radius=0)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(0, weight=1)

        form = ctk.CTkFrame(panel, fg_color="transparent")
        form.grid(row=0, column=0, sticky="nsew", padx=36, pady=36)
        form.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            form,
            text="Conta",
            font=FONTS["title"],
            text_color=COLORS["text"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        self.auth_mode = ctk.StringVar(value=self.cfg.auth_mode if self.cfg.auth_mode in ("offline", "microsoft") else "offline")
        modes = ctk.CTkFrame(form, fg_color="transparent")
        modes.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        modes.grid_columnconfigure((0, 1), weight=1)

        self.btn_offline = ctk.CTkButton(
            modes,
            text="Offline",
            command=lambda: self._set_mode("offline"),
            height=36,
            corner_radius=8,
            font=FONTS["body_bold"],
            fg_color=COLORS["accent"] if self.auth_mode.get() == "offline" else COLORS["panel"],
            hover_color=COLORS["accent_dim"],
            text_color=COLORS["accent_text"] if self.auth_mode.get() == "offline" else COLORS["text"],
        )
        self.btn_offline.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_ms = ctk.CTkButton(
            modes,
            text="Microsoft",
            command=lambda: self._set_mode("microsoft"),
            height=36,
            corner_radius=8,
            font=FONTS["body_bold"],
            fg_color=COLORS["accent"] if self.auth_mode.get() == "microsoft" else COLORS["panel"],
            hover_color=COLORS["accent_dim"],
            text_color=COLORS["accent_text"] if self.auth_mode.get() == "microsoft" else COLORS["text"],
        )
        self.btn_ms.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.nick_label = ctk.CTkLabel(form, text="Nickname", font=FONTS["small"], text_color=COLORS["muted"], anchor="w")
        self.nick_label.grid(row=2, column=0, sticky="w", pady=(14, 4))
        self.nick_entry = ctk.CTkEntry(
            form,
            height=40,
            corner_radius=8,
            font=FONTS["body"],
            fg_color=COLORS["input_bg"],
            border_color=COLORS["input_border"],
            text_color=COLORS["text"],
            placeholder_text="Seu nick no SMP",
        )
        self.nick_entry.grid(row=3, column=0, sticky="ew")
        self.nick_entry.insert(0, self.cfg.username or "Steve")

        self.ms_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.ms_frame.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.ms_frame.grid_columnconfigure(0, weight=1)

        self.ms_status = ctk.CTkLabel(
            self.ms_frame,
            text=self._ms_status_text(),
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
            justify="left",
        )
        self.ms_status.grid(row=0, column=0, sticky="ew")

        ms_actions = ctk.CTkFrame(self.ms_frame, fg_color="transparent")
        ms_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ms_actions.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            ms_actions,
            text="Login Microsoft",
            command=self._login_microsoft,
            height=34,
            corner_radius=8,
            font=FONTS["small"],
            fg_color=COLORS["panel_alt"],
            hover_color=COLORS["stroke"],
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            ms_actions,
            text="Importar oficial",
            command=self._import_official,
            height=34,
            corner_radius=8,
            font=FONTS["small"],
            fg_color=COLORS["panel_alt"],
            hover_color=COLORS["stroke"],
            text_color=COLORS["text"],
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # RAM
        ctk.CTkLabel(form, text="Memória (RAM)", font=FONTS["small"], text_color=COLORS["muted"], anchor="w").grid(
            row=5, column=0, sticky="w", pady=(18, 4)
        )
        ram_row = ctk.CTkFrame(form, fg_color="transparent")
        ram_row.grid(row=6, column=0, sticky="ew")
        ram_row.grid_columnconfigure(0, weight=1)
        self.ram_slider = ctk.CTkSlider(
            ram_row,
            from_=2,
            to=16,
            number_of_steps=14,
            command=self._on_ram,
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_dim"],
            fg_color=COLORS["panel"],
        )
        self.ram_slider.set(float(self.cfg.ram_gb or 4))
        self.ram_slider.grid(row=0, column=0, sticky="ew")
        self.ram_value = ctk.CTkLabel(ram_row, text=f"{int(self.cfg.ram_gb or 4)} GB", width=56, font=FONTS["body_bold"], text_color=COLORS["accent"])
        self.ram_value.grid(row=0, column=1, padx=(10, 0))

        # Optional server
        ctk.CTkLabel(form, text="IP do servidor (opcional)", font=FONTS["small"], text_color=COLORS["muted"], anchor="w").grid(
            row=7, column=0, sticky="w", pady=(16, 4)
        )
        self.server_entry = ctk.CTkEntry(
            form,
            height=36,
            corner_radius=8,
            font=FONTS["body"],
            fg_color=COLORS["input_bg"],
            border_color=COLORS["input_border"],
            text_color=COLORS["text"],
            placeholder_text="ex: play.meusmp.com",
        )
        self.server_entry.grid(row=8, column=0, sticky="ew")
        if self.cfg.server_ip:
            self.server_entry.insert(0, self.cfg.server_ip)

        self.play_btn = ctk.CTkButton(
            form,
            text="JOGAR",
            command=self._play,
            height=52,
            corner_radius=10,
            font=("Georgia", 20, "bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_dim"],
            text_color=COLORS["accent_text"],
        )
        self.play_btn.grid(row=9, column=0, sticky="ew", pady=(22, 8))

        self.status = ctk.CTkLabel(
            form,
            text="Pronto.",
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
            justify="left",
            wraplength=340,
        )
        self.status.grid(row=10, column=0, sticky="ew")

        links = ctk.CTkFrame(form, fg_color="transparent")
        links.grid(row=11, column=0, sticky="ew", pady=(18, 0))
        links.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            links,
            text=f"v{__version__}  ·  pasta mods ao lado do exe",
            font=FONTS["small"],
            text_color=COLORS["stroke"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            links,
            text="Config",
            width=70,
            height=28,
            corner_radius=6,
            font=FONTS["small"],
            fg_color=COLORS["panel"],
            hover_color=COLORS["stroke"],
            text_color=COLORS["muted"],
            command=self._open_settings,
        ).grid(row=0, column=1, sticky="e")

        self._apply_mode_visibility()

    def _open_settings(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Configurações")
        win.geometry("460x320")
        win.configure(fg_color=COLORS["bg1"])
        win.transient(self)
        win.grab_set()

        frame = ctk.CTkFrame(win, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(frame, text="Azure Client ID (Microsoft)", font=FONTS["small"], text_color=COLORS["muted"], anchor="w").pack(fill="x")
        client = ctk.CTkEntry(frame, height=36, fg_color=COLORS["input_bg"], border_color=COLORS["input_border"], text_color=COLORS["text"])
        client.pack(fill="x", pady=(4, 12))
        client.insert(0, self.cfg.azure_client_id or "")

        ctk.CTkLabel(frame, text="Caminho do Java (opcional)", font=FONTS["small"], text_color=COLORS["muted"], anchor="w").pack(fill="x")
        java = ctk.CTkEntry(frame, height=36, fg_color=COLORS["input_bg"], border_color=COLORS["input_border"], text_color=COLORS["text"])
        java.pack(fill="x", pady=(4, 12))
        java.insert(0, self.cfg.java_path or "")

        close_var = ctk.BooleanVar(value=bool(self.cfg.close_launcher_on_start))
        ctk.CTkCheckBox(
            frame,
            text="Fechar launcher ao abrir o jogo",
            variable=close_var,
            font=FONTS["small"],
            text_color=COLORS["text"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_dim"],
        ).pack(anchor="w", pady=(0, 16))

        def save():
            self.cfg.azure_client_id = client.get().strip()
            self.cfg.java_path = java.get().strip()
            self.cfg.close_launcher_on_start = bool(close_var.get())
            self.cfg.save()
            self._set_status("Configurações salvas.")
            win.destroy()

        ctk.CTkButton(
            frame,
            text="Salvar",
            command=save,
            height=40,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_dim"],
            text_color=COLORS["accent_text"],
            font=FONTS["body_bold"],
        ).pack(fill="x")

    def _intro_motion(self) -> None:
        # Soft staggered fade-in for brand presence.
        for i, widget in enumerate(self._fade_widgets):
            self.after(i * 90, lambda w=widget: self._pulse(w))

    def _pulse(self, widget) -> None:
        try:
            widget.configure(text_color=COLORS["accent"] if widget == self._fade_widgets[0] else COLORS["text"])
        except Exception:
            pass

    # ------------------------------------------------------------------ helpers
    def _set_status(self, text: str, ok: bool = True) -> None:
        color = COLORS["ok"] if ok else COLORS["danger"]
        self.status.configure(text=text, text_color=color)

    def _set_mode(self, mode: str) -> None:
        self.auth_mode.set(mode)
        offline = mode == "offline"
        self.btn_offline.configure(
            fg_color=COLORS["accent"] if offline else COLORS["panel"],
            text_color=COLORS["accent_text"] if offline else COLORS["text"],
        )
        self.btn_ms.configure(
            fg_color=COLORS["accent"] if not offline else COLORS["panel"],
            text_color=COLORS["accent_text"] if not offline else COLORS["text"],
        )
        self._apply_mode_visibility()

    def _apply_mode_visibility(self) -> None:
        offline = self.auth_mode.get() == "offline"
        if offline:
            self.nick_label.grid()
            self.nick_entry.grid()
            self.ms_frame.grid_remove()
        else:
            self.nick_label.grid_remove()
            self.nick_entry.grid_remove()
            self.ms_frame.grid()

    def _on_ram(self, value: float) -> None:
        self.ram_value.configure(text=f"{int(round(value))} GB")

    def _ms_status_text(self) -> str:
        if self.cfg.microsoft_name:
            return f"Logado: {self.cfg.microsoft_name}"
        return "Sem sessão Microsoft salva."

    def _refresh_mod_count(self) -> None:
        mods = list_bundled_mods()
        installed = "Forge OK" if forge_installed() else "Forge será instalado no 1º play"
        self.mod_label.configure(text=f"Mods do pack: {len(mods)}")
        self.forge_label.configure(text=f"Minecraft {MC_VERSION}  ·  Forge {FORGE_VERSION}  ·  {installed}")
        if not mods_source_dir().exists():
            self._set_status(f"Pasta mods não encontrada em:\n{mods_source_dir()}", ok=False)

    def _save_form(self) -> None:
        self.cfg.auth_mode = self.auth_mode.get()
        self.cfg.username = self.nick_entry.get().strip() or "Steve"
        self.cfg.ram_gb = int(round(self.ram_slider.get()))
        self.cfg.server_ip = self.server_entry.get().strip()
        self.cfg.save()

    def _run_bg(self, fn, on_done=None) -> None:
        if self._busy:
            return

        def worker():
            self._busy = True
            self.play_btn.configure(state="disabled", text="…")
            err = None
            result = None
            try:
                result = fn()
            except Exception as exc:
                err = exc
                traceback.print_exc()
            finally:
                self._busy = False

                def finish():
                    self.play_btn.configure(state="normal", text="JOGAR")
                    if on_done:
                        on_done(result, err)

                self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ auth actions
    def _login_microsoft(self) -> None:
        self._save_form()

        def job():
            def status(msg: str):
                self.after(0, lambda: self._set_status(msg))

            return login_microsoft_browser(self.cfg, on_status=status)

        def done(session: Optional[GameSession], err):
            if err:
                self._set_status(str(err), ok=False)
                return
            self.cfg = LauncherConfig.load()
            self.ms_status.configure(text=self._ms_status_text(), text_color=COLORS["ok"])
            self._set_status(f"Microsoft OK — {session.username}")

        self._run_bg(job, done)

    def _import_official(self) -> None:
        accounts = list_official_launcher_accounts()
        if not accounts:
            self._set_status(
                "Nenhuma conta encontrada no Minecraft Launcher oficial. "
                "Faça login lá uma vez, ou use Offline / Azure Client ID.",
                ok=False,
            )
            return

        # Prefer the first account with a usable access token.
        acc = next((a for a in accounts if a.get("access_token")), accounts[0])
        session = GameSession(
            username=acc["name"],
            uuid=(acc.get("uuid") or "").replace("-", ""),
            access_token=acc.get("access_token") or "0",
            offline=False,
        )
        persist_microsoft_session(self.cfg, session, refresh_token=acc.get("refresh_token") or "")
        self.cfg = LauncherConfig.load()
        self.ms_status.configure(text=self._ms_status_text(), text_color=COLORS["ok"])
        self._set_status(f"Importado: {session.username} ({acc.get('source', '')})")

    def _resolve_session(self) -> GameSession:
        mode = self.auth_mode.get()
        if mode == "offline":
            return offline_session(self.nick_entry.get())

        # Try refresh, then saved access token, then fail clearly.
        if self.cfg.microsoft_refresh_token and self.cfg.azure_client_id:
            try:
                return refresh_microsoft_session(self.cfg)
            except MicrosoftAuthError:
                pass

        saved = session_from_config_microsoft(self.cfg)
        if saved and saved.access_token and saved.access_token != "0":
            return saved

        raise MicrosoftAuthError(
            "Sem sessão Microsoft válida. Clique em Login Microsoft, Importar oficial, ou use Offline."
        )

    def _play(self) -> None:
        self._save_form()

        def job():
            def status(msg: str):
                self.after(0, lambda: self._set_status(msg))

            session = self._resolve_session()
            return prepare_and_launch(self.cfg, session, on_status=status)

        def done(_proc, err):
            if err:
                self._set_status(str(err), ok=False)
                return
            self._set_status("Minecraft iniciado. Bom SMP!")
            self._refresh_mod_count()
            if self.cfg.close_launcher_on_start:
                self.after(900, self.destroy)

        self._run_bg(job, done)


def run_app() -> None:
    app = PUTsLauncherApp()
    app.mainloop()
