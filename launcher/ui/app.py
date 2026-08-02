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
from launcher.config import FORGE_VERSION, MC_VERSION, LauncherConfig, mods_source_dir, puts_home
from launcher.core import (
    DEFAULT_PHASES,
    ProgressState,
    ProgressTracker,
    forge_installed,
    list_bundled_mods,
    prepare_and_launch,
)
from launcher.ui.theme import COLORS, FONTS


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")


class PUTsLauncherApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = LauncherConfig.load()
        self.title(f"{__app_name__}")
        self.geometry("780x560")
        self.minsize(720, 520)
        self.configure(fg_color=COLORS["bg0"])
        self._busy = False

        self._build()
        self.after(150, self._refresh_meta)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        root = ctk.CTkFrame(self, fg_color=COLORS["bg1"], corner_radius=0)
        root.grid(row=0, column=0, sticky="nsew")
        root.grid_columnconfigure(0, weight=1)

        pad = ctk.CTkFrame(root, fg_color="transparent")
        pad.grid(row=0, column=0, sticky="nsew", padx=40, pady=32)
        pad.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            pad,
            text="PUTs",
            font=FONTS["display"],
            text_color=COLORS["accent"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            pad,
            text="Launcher do SMP  ·  Forge + mods prontos",
            font=FONTS["body"],
            text_color=COLORS["muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(0, 18))

        self.path_label = ctk.CTkLabel(
            pad,
            text=f"Pasta: {puts_home()}",
            font=FONTS["small"],
            text_color=COLORS["stroke"],
            anchor="w",
        )
        self.path_label.grid(row=2, column=0, sticky="w")

        self.meta_label = ctk.CTkLabel(
            pad,
            text=f"Minecraft {MC_VERSION}  ·  Forge {FORGE_VERSION}",
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
        )
        self.meta_label.grid(row=3, column=0, sticky="w", pady=(2, 18))

        # Auth mode
        mode_row = ctk.CTkFrame(pad, fg_color="transparent")
        mode_row.grid(row=4, column=0, sticky="ew")
        mode_row.grid_columnconfigure((0, 1), weight=1)
        self.auth_mode = ctk.StringVar(
            value=self.cfg.auth_mode if self.cfg.auth_mode in ("offline", "microsoft") else "offline"
        )
        self.btn_offline = ctk.CTkButton(
            mode_row,
            text="Jogar Offline",
            command=lambda: self._set_mode("offline"),
            height=36,
            corner_radius=8,
            font=FONTS["body_bold"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_dim"],
            text_color=COLORS["accent_text"],
        )
        self.btn_offline.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.btn_ms = ctk.CTkButton(
            mode_row,
            text="Conta Microsoft",
            command=lambda: self._set_mode("microsoft"),
            height=36,
            corner_radius=8,
            font=FONTS["body_bold"],
            fg_color=COLORS["panel"],
            hover_color=COLORS["stroke"],
            text_color=COLORS["text"],
        )
        self.btn_ms.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.nick_label = ctk.CTkLabel(
            pad, text="Nickname", font=FONTS["small"], text_color=COLORS["muted"], anchor="w"
        )
        self.nick_label.grid(row=5, column=0, sticky="w", pady=(14, 4))
        self.nick_entry = ctk.CTkEntry(
            pad,
            height=42,
            corner_radius=8,
            font=FONTS["body"],
            fg_color=COLORS["input_bg"],
            border_color=COLORS["input_border"],
            text_color=COLORS["text"],
            placeholder_text="Seu nick",
        )
        self.nick_entry.grid(row=6, column=0, sticky="ew")
        self.nick_entry.insert(0, self.cfg.username or "Steve")

        self.ms_row = ctk.CTkFrame(pad, fg_color="transparent")
        self.ms_row.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        self.ms_row.grid_columnconfigure((0, 1), weight=1)
        self.ms_status = ctk.CTkLabel(
            self.ms_row,
            text=self._ms_status_text(),
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
        )
        self.ms_status.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ctk.CTkButton(
            self.ms_row,
            text="Login no navegador",
            command=self._login_microsoft,
            height=34,
            corner_radius=8,
            font=FONTS["small"],
            fg_color=COLORS["panel_alt"],
            hover_color=COLORS["stroke"],
            text_color=COLORS["text"],
        ).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            self.ms_row,
            text="Importar launcher oficial",
            command=self._import_official,
            height=34,
            corner_radius=8,
            font=FONTS["small"],
            fg_color=COLORS["panel_alt"],
            hover_color=COLORS["stroke"],
            text_color=COLORS["text"],
        ).grid(row=1, column=1, sticky="ew", padx=(6, 0))

        # RAM
        ctk.CTkLabel(pad, text="RAM", font=FONTS["small"], text_color=COLORS["muted"], anchor="w").grid(
            row=8, column=0, sticky="w", pady=(16, 4)
        )
        ram_row = ctk.CTkFrame(pad, fg_color="transparent")
        ram_row.grid(row=9, column=0, sticky="ew")
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
        self.ram_value = ctk.CTkLabel(
            ram_row,
            text=f"{int(self.cfg.ram_gb or 4)} GB",
            width=56,
            font=FONTS["body_bold"],
            text_color=COLORS["accent"],
        )
        self.ram_value.grid(row=0, column=1, padx=(10, 0))

        # Progress block
        progress_box = ctk.CTkFrame(pad, fg_color=COLORS["panel"], corner_radius=12)
        progress_box.grid(row=10, column=0, sticky="ew", pady=(20, 8))
        progress_box.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(progress_box, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        head.grid_columnconfigure(0, weight=1)
        self.progress_title = ctk.CTkLabel(
            head, text="Pronto para jogar", font=FONTS["body_bold"], text_color=COLORS["text"], anchor="w"
        )
        self.progress_title.grid(row=0, column=0, sticky="w")
        self.percent_label = ctk.CTkLabel(
            head, text="0%", font=FONTS["body_bold"], text_color=COLORS["accent"], anchor="e"
        )
        self.percent_label.grid(row=0, column=1, sticky="e")

        self.progress = ctk.CTkProgressBar(
            progress_box,
            height=16,
            corner_radius=8,
            progress_color=COLORS["accent"],
            fg_color=COLORS["bg0"],
        )
        self.progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.progress.set(0)

        self.detail_label = ctk.CTkLabel(
            progress_box,
            text="Clique em JOGAR para baixar Java, Minecraft e Forge se precisar.",
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self.detail_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))

        self.eta_label = ctk.CTkLabel(
            progress_box,
            text="Tempo restante: —",
            font=FONTS["small"],
            text_color=COLORS["stroke"],
            anchor="w",
        )
        self.eta_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))

        self.play_btn = ctk.CTkButton(
            pad,
            text="JOGAR",
            command=self._play,
            height=52,
            corner_radius=10,
            font=("Georgia", 20, "bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_dim"],
            text_color=COLORS["accent_text"],
        )
        self.play_btn.grid(row=11, column=0, sticky="ew", pady=(8, 4))

        self.status = ctk.CTkLabel(
            pad,
            text=f"v{__version__}",
            font=FONTS["small"],
            text_color=COLORS["stroke"],
            anchor="w",
        )
        self.status.grid(row=12, column=0, sticky="w", pady=(8, 0))

        self._apply_mode_visibility()
        self._set_mode(self.auth_mode.get())

    # ------------------------------------------------------------------ UI helpers
    def _set_progress_ui(self, state: ProgressState) -> None:
        self.progress.set(max(0.0, min(1.0, state.percent / 100.0)))
        self.percent_label.configure(text=f"{int(state.percent)}%")
        self.progress_title.configure(text=state.phase.upper() if state.phase else "…")
        self.detail_label.configure(text=state.detail or "")
        self.eta_label.configure(text=f"Tempo restante: {state.eta_text}")

    def _set_status(self, text: str, ok: bool = True) -> None:
        self.status.configure(text=text, text_color=COLORS["ok"] if ok else COLORS["danger"])

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
            self.ms_row.grid_remove()
        else:
            self.nick_label.grid_remove()
            self.nick_entry.grid_remove()
            self.ms_row.grid()

    def _on_ram(self, value: float) -> None:
        self.ram_value.configure(text=f"{int(round(value))} GB")

    def _ms_status_text(self) -> str:
        if self.cfg.microsoft_name:
            return f"Logado: {self.cfg.microsoft_name}"
        return "Faça login ou importe a conta do Minecraft oficial."

    def _refresh_meta(self) -> None:
        mods = list_bundled_mods()
        installed = "instalado" if forge_installed() else "vai baixar no 1º play"
        self.meta_label.configure(
            text=f"Minecraft {MC_VERSION}  ·  Forge {FORGE_VERSION} ({installed})  ·  {len(mods)} mods"
        )
        self.path_label.configure(text=f"Pasta: {puts_home()}")
        if not mods_source_dir().exists():
            self._set_status(f"Pasta mods não encontrada: {mods_source_dir()}", ok=False)

    def _save_form(self) -> None:
        self.cfg.auth_mode = self.auth_mode.get()
        self.cfg.username = self.nick_entry.get().strip() or "Steve"
        self.cfg.ram_gb = int(round(self.ram_slider.get()))
        self.cfg.save()

    def _run_bg(self, fn, on_done=None) -> None:
        if self._busy:
            return

        def worker():
            self._busy = True
            self.after(0, lambda: self.play_btn.configure(state="disabled", text="CARREGANDO…"))
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

    def _login_microsoft(self) -> None:
        self._save_form()

        def job():
            return login_microsoft_browser(
                self.cfg,
                on_status=lambda msg: self.after(0, lambda: self.detail_label.configure(text=msg)),
            )

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
                "Nenhuma conta no launcher oficial. Use Offline ou Login no navegador.",
                ok=False,
            )
            return
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
        self._set_status(f"Importado: {session.username}")

    def _resolve_session(self) -> GameSession:
        if self.auth_mode.get() == "offline":
            return offline_session(self.nick_entry.get())
        if self.cfg.microsoft_refresh_token and self.cfg.azure_client_id:
            try:
                return refresh_microsoft_session(self.cfg)
            except MicrosoftAuthError:
                pass
        saved = session_from_config_microsoft(self.cfg)
        if saved and saved.access_token and saved.access_token != "0":
            return saved
        raise MicrosoftAuthError("Sem sessão Microsoft. Faça login, importe, ou use Offline.")

    def _play(self) -> None:
        self._save_form()

        def on_progress(state: ProgressState) -> None:
            self.after(0, lambda s=state: self._set_progress_ui(s))

        def job():
            tracker = ProgressTracker(DEFAULT_PHASES, on_update=on_progress)
            session = self._resolve_session()
            return prepare_and_launch(self.cfg, session, tracker=tracker)

        def done(_proc, err):
            if err:
                self._set_status(str(err), ok=False)
                self.progress_title.configure(text="ERRO")
                self.detail_label.configure(text=str(err))
                self.eta_label.configure(text="Tempo restante: —")
                return
            self._set_progress_ui(
                ProgressState(percent=100, phase="pronto", detail="Minecraft iniciado!", eta_seconds=0)
            )
            self._set_status("Minecraft iniciado!")
            self._refresh_meta()
            if self.cfg.close_launcher_on_start:
                self.after(1200, self.destroy)

        self._run_bg(job, done)


def run_app() -> None:
    app = PUTsLauncherApp()
    app.mainloop()
