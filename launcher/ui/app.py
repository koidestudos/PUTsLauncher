from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageTk

from launcher import __app_name__, __version__
from launcher.auth import (
    MicrosoftAuthError,
    fetch_skin_render,
    login_microsoft_browser,
    offline_session,
    refresh_microsoft_session,
    session_from_config_microsoft,
)
from launcher.auth.session import GameSession
from launcher.config import (
    FORGE_VERSION,
    MC_VERSION,
    LauncherConfig,
    asset_path,
    mods_source_dir,
    puts_home,
)
from launcher.core import (
    DEFAULT_PHASES,
    ProgressState,
    ProgressTracker,
    is_game_ready,
    list_bundled_mods,
    prepare_and_launch,
    prepare_game,
    sync_mods,
)
from launcher.ui.theme import COLORS, FONTS


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


def _load_ctk_image(path: Path, size: tuple[int, int]) -> Optional[ctk.CTkImage]:
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("RGBA")
        return ctk.CTkImage(light_image=img, dark_image=img, size=size)
    except Exception:
        return None


def _make_glow_backdrop(width: int, height: int) -> Image.Image:
    """Warm cocoa gradient with soft gold bloom — atmosphere, not flat fill."""
    img = Image.new("RGB", (width, height), (10, 9, 7))
    draw = ImageDraw.Draw(img, "RGBA")
    # Top-left gold wash
    for i in range(28):
        alpha = max(0, 40 - i)
        draw.ellipse(
            [-width // 3 + i * 8, -height // 4 + i * 6, width // 2 - i * 4, height // 2 - i * 3],
            fill=(240, 210, 74, alpha),
        )
    # Bottom berry warmth
    for i in range(22):
        alpha = max(0, 28 - i)
        draw.ellipse(
            [width // 3 + i * 5, height // 2 + i * 4, width + width // 4 - i * 3, height + height // 3],
            fill=(139, 58, 42, alpha),
        )
    return img.filter(ImageFilter.GaussianBlur(28))


class PUTsLauncherApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = LauncherConfig.load()
        self.title(f"{__app_name__}")
        self.geometry("980x640")
        self.minsize(900, 600)
        self.configure(fg_color=COLORS["bg0"])
        self._busy = False
        self._downloading = False
        self._skin_image: Optional[ctk.CTkImage] = None
        self._logo_image = _load_ctk_image(asset_path("logo_circle.png"), (86, 86))
        self._ms_image = _load_ctk_image(asset_path("microsoft.png"), (18, 18))
        self._backdrop_label: Optional[ctk.CTkLabel] = None
        self._backdrop_photo = None

        self._build()
        self.after(80, self._paint_backdrop)
        self.after(120, self._refresh_ready_state)
        self.after(200, self._refresh_skin)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.shell = ctk.CTkFrame(self, fg_color=COLORS["bg0"], corner_radius=0)
        self.shell.grid(row=0, column=0, sticky="nsew")
        self.shell.grid_columnconfigure(0, weight=3)
        self.shell.grid_columnconfigure(1, weight=2)
        self.shell.grid_rowconfigure(0, weight=1)

        # LEFT — brand + controls
        left = ctk.CTkFrame(self.shell, fg_color="transparent", corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(6, weight=1)

        brand = ctk.CTkFrame(left, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=42, pady=(36, 8))
        brand.grid_columnconfigure(1, weight=1)

        if self._logo_image:
            ctk.CTkLabel(brand, text="", image=self._logo_image).grid(row=0, column=0, rowspan=2, padx=(0, 16))

        ctk.CTkLabel(
            brand,
            text="PUTs",
            font=FONTS["display"],
            text_color=COLORS["accent"],
            anchor="w",
        ).grid(row=0, column=1, sticky="sw")

        ctk.CTkLabel(
            brand,
            text="SMP Launcher  ·  maracujá edition",
            font=FONTS["body"],
            text_color=COLORS["muted"],
            anchor="w",
        ).grid(row=1, column=1, sticky="nw")

        self.meta_label = ctk.CTkLabel(
            left,
            text=f"Minecraft {MC_VERSION}  ·  Forge {FORGE_VERSION}",
            font=FONTS["small"],
            text_color=COLORS["stroke"],
            anchor="w",
        )
        self.meta_label.grid(row=1, column=0, sticky="ew", padx=42, pady=(4, 18))

        # Mode pills
        modes = ctk.CTkFrame(left, fg_color="transparent")
        modes.grid(row=2, column=0, sticky="ew", padx=42)
        modes.grid_columnconfigure((0, 1), weight=1)
        self.auth_mode = ctk.StringVar(
            value=self.cfg.auth_mode if self.cfg.auth_mode in ("offline", "microsoft") else "offline"
        )
        self.btn_offline = ctk.CTkButton(
            modes,
            text="Offline",
            command=lambda: self._set_mode("offline"),
            height=38,
            corner_radius=10,
            font=FONTS["body_bold"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_dim"],
            text_color=COLORS["accent_text"],
        )
        self.btn_offline.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.btn_ms_mode = ctk.CTkButton(
            modes,
            text="Microsoft",
            command=lambda: self._set_mode("microsoft"),
            height=38,
            corner_radius=10,
            font=FONTS["body_bold"],
            fg_color=COLORS["panel"],
            hover_color=COLORS["panel_soft"],
            text_color=COLORS["text"],
            border_width=1,
            border_color=COLORS["stroke"],
        )
        self.btn_ms_mode.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        # Offline nick
        self.nick_wrap = ctk.CTkFrame(left, fg_color="transparent")
        self.nick_wrap.grid(row=3, column=0, sticky="ew", padx=42, pady=(16, 0))
        self.nick_wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.nick_wrap, text="Nickname", font=FONTS["small"], text_color=COLORS["muted"], anchor="w"
        ).grid(row=0, column=0, sticky="w")
        self.nick_entry = ctk.CTkEntry(
            self.nick_wrap,
            height=44,
            corner_radius=10,
            font=FONTS["body"],
            fg_color=COLORS["input_bg"],
            border_color=COLORS["input_border"],
            text_color=COLORS["text"],
            placeholder_text="Seu nick no SMP",
        )
        self.nick_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.nick_entry.insert(0, self.cfg.username or "Steve")
        self.nick_entry.bind("<KeyRelease>", lambda _e: self.after(400, self._refresh_skin))

        # Microsoft login button (with logo)
        self.ms_wrap = ctk.CTkFrame(left, fg_color="transparent")
        self.ms_wrap.grid(row=4, column=0, sticky="ew", padx=42, pady=(16, 0))
        self.ms_wrap.grid_columnconfigure(0, weight=1)
        self.ms_status = ctk.CTkLabel(
            self.ms_wrap,
            text=self._ms_status_text(),
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
        )
        self.ms_status.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.btn_ms_login = ctk.CTkButton(
            self.ms_wrap,
            text="  Login no navegador",
            image=self._ms_image,
            compound="left",
            command=self._login_microsoft,
            height=44,
            corner_radius=10,
            font=FONTS["body_bold"],
            fg_color="#2b2b2b",
            hover_color="#3a3a3a",
            text_color=COLORS["text"],
            border_width=1,
            border_color="#555555",
        )
        self.btn_ms_login.grid(row=1, column=0, sticky="ew")

        # RAM
        ram = ctk.CTkFrame(left, fg_color="transparent")
        ram.grid(row=5, column=0, sticky="ew", padx=42, pady=(18, 0))
        ram.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(ram, text="Memória RAM", font=FONTS["small"], text_color=COLORS["muted"], anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        row = ctk.CTkFrame(ram, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        row.grid_columnconfigure(0, weight=1)
        self.ram_slider = ctk.CTkSlider(
            row,
            from_=2,
            to=16,
            number_of_steps=14,
            command=self._on_ram,
            progress_color=COLORS["accent"],
            button_color=COLORS["accent_hot"],
            button_hover_color=COLORS["accent"],
            fg_color=COLORS["panel"],
        )
        self.ram_slider.set(float(self.cfg.ram_gb or 4))
        self.ram_slider.grid(row=0, column=0, sticky="ew")
        self.ram_value = ctk.CTkLabel(
            row, text=f"{int(self.cfg.ram_gb or 4)} GB", width=58, font=FONTS["body_bold"], text_color=COLORS["accent"]
        )
        self.ram_value.grid(row=0, column=1, padx=(12, 0))

        # Action + progress (progress hidden by default)
        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.grid(row=7, column=0, sticky="ew", padx=42, pady=(10, 28))
        actions.grid_columnconfigure(0, weight=1)

        self.progress_box = ctk.CTkFrame(actions, fg_color=COLORS["panel"], corner_radius=14)
        self.progress_box.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.progress_box.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(self.progress_box, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        head.grid_columnconfigure(0, weight=1)
        self.progress_title = ctk.CTkLabel(
            head, text="Baixando", font=FONTS["body_bold"], text_color=COLORS["text"], anchor="w"
        )
        self.progress_title.grid(row=0, column=0, sticky="w")
        self.percent_label = ctk.CTkLabel(
            head, text="0%", font=FONTS["body_bold"], text_color=COLORS["accent"], anchor="e"
        )
        self.percent_label.grid(row=0, column=1, sticky="e")
        self.progress = ctk.CTkProgressBar(
            self.progress_box, height=14, corner_radius=8, progress_color=COLORS["accent"], fg_color=COLORS["bg0"]
        )
        self.progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        self.progress.set(0)
        self.detail_label = ctk.CTkLabel(
            self.progress_box,
            text="",
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
            wraplength=480,
            justify="left",
        )
        self.detail_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 2))
        self.eta_label = ctk.CTkLabel(
            self.progress_box, text="Tempo restante: —", font=FONTS["tiny"], text_color=COLORS["stroke"], anchor="w"
        )
        self.eta_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        self.progress_box.grid_remove()

        self.action_btn = ctk.CTkButton(
            actions,
            text="BAIXAR",
            command=self._on_action,
            height=54,
            corner_radius=12,
            font=FONTS["button"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hot"],
            text_color=COLORS["accent_text"],
        )
        self.action_btn.grid(row=1, column=0, sticky="ew")

        self.status = ctk.CTkLabel(
            actions,
            text=f"Pasta: {puts_home()}   ·   v{__version__}",
            font=FONTS["tiny"],
            text_color=COLORS["stroke"],
            anchor="w",
        )
        self.status.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        # RIGHT — skin stage
        right = ctk.CTkFrame(self.shell, fg_color=COLORS["bg2"], corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            right,
            text="Sua skin",
            font=FONTS["title"],
            text_color=COLORS["cream"],
            anchor="center",
        ).grid(row=0, column=0, pady=(48, 8))

        self.skin_stage = ctk.CTkFrame(right, fg_color=COLORS["panel"], corner_radius=24)
        self.skin_stage.grid(row=1, column=0, sticky="nsew", padx=36, pady=(0, 16))
        self.skin_stage.grid_rowconfigure(0, weight=1)
        self.skin_stage.grid_columnconfigure(0, weight=1)

        self.skin_label = ctk.CTkLabel(
            self.skin_stage,
            text="Entre com Microsoft\nou digite um nick",
            font=FONTS["body"],
            text_color=COLORS["muted"],
            justify="center",
        )
        self.skin_label.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        self.skin_name = ctk.CTkLabel(
            right,
            text="",
            font=FONTS["body_bold"],
            text_color=COLORS["accent"],
            anchor="center",
        )
        self.skin_name.grid(row=2, column=0, pady=(0, 40))

        self._set_mode(self.auth_mode.get())
        self._refresh_ready_state()

    def _paint_backdrop(self) -> None:
        try:
            import tkinter as tk

            w = max(self.winfo_width(), 980)
            h = max(self.winfo_height(), 640)
            img = _make_glow_backdrop(w, h)
            self._backdrop_photo = ImageTk.PhotoImage(img)
            if self._backdrop_label is None:
                self._backdrop_label = tk.Label(self.shell, image=self._backdrop_photo, borderwidth=0)
                self._backdrop_label.place(x=0, y=0, relwidth=1, relheight=1)
                self._backdrop_label.lower()
            else:
                self._backdrop_label.configure(image=self._backdrop_photo)
        except Exception:
            pass

    # ------------------------------------------------------------------ state
    def _ms_status_text(self) -> str:
        if self.cfg.microsoft_name:
            return f"Conectado: {self.cfg.microsoft_name}"
        return "Entre com sua conta Microsoft"

    def _set_mode(self, mode: str) -> None:
        self.auth_mode.set(mode)
        offline = mode == "offline"
        self.btn_offline.configure(
            fg_color=COLORS["accent"] if offline else COLORS["panel"],
            text_color=COLORS["accent_text"] if offline else COLORS["text"],
            border_width=0 if offline else 1,
            border_color=COLORS["stroke"],
        )
        self.btn_ms_mode.configure(
            fg_color=COLORS["accent"] if not offline else COLORS["panel"],
            text_color=COLORS["accent_text"] if not offline else COLORS["text"],
            border_width=0 if not offline else 1,
            border_color=COLORS["stroke"],
        )
        if offline:
            self.nick_wrap.grid()
            self.ms_wrap.grid_remove()
        else:
            self.nick_wrap.grid_remove()
            self.ms_wrap.grid()
        self._refresh_skin()

    def _on_ram(self, value: float) -> None:
        self.ram_value.configure(text=f"{int(round(value))} GB")

    def _set_status(self, text: str, ok: bool = True) -> None:
        self.status.configure(text=text, text_color=COLORS["ok"] if ok else COLORS["danger"])

    def _refresh_ready_state(self) -> None:
        ready = is_game_ready()
        mods = list_bundled_mods()
        self.meta_label.configure(
            text=f"Minecraft {MC_VERSION}  ·  Forge {FORGE_VERSION}  ·  {len(mods)} mods"
            + ("  ·  pronto" if ready else "  ·  precisa baixar")
        )
        if self._downloading:
            return
        if ready:
            self.action_btn.configure(text="JOGAR", fg_color=COLORS["accent"], hover_color=COLORS["accent_hot"])
            self.progress_box.grid_remove()
        else:
            self.action_btn.configure(text="BAIXAR", fg_color=COLORS["accent_hot"], hover_color=COLORS["accent"])
            self.progress_box.grid_remove()
        if not mods_source_dir().exists():
            self._set_status(f"Pasta mods não encontrada: {mods_source_dir()}", ok=False)

    def _show_progress(self, show: bool) -> None:
        if show:
            self.progress_box.grid()
        else:
            self.progress_box.grid_remove()

    def _set_progress_ui(self, state: ProgressState) -> None:
        self._show_progress(True)
        self.progress.set(max(0.0, min(1.0, state.percent / 100.0)))
        self.percent_label.configure(text=f"{int(state.percent)}%")
        titles = {
            "java": "Baixando Java",
            "forge": "Baixando Minecraft + Forge",
            "mods": "Copiando mods",
            "launch": "Abrindo o jogo",
        }
        self.progress_title.configure(text=titles.get(state.phase, state.phase.capitalize() or "Baixando"))
        self.detail_label.configure(text=state.detail or "")
        self.eta_label.configure(text=f"Tempo restante: {state.eta_text}")

    def _save_form(self) -> None:
        self.cfg.auth_mode = self.auth_mode.get()
        self.cfg.username = self.nick_entry.get().strip() or "Steve"
        self.cfg.ram_gb = int(round(self.ram_slider.get()))
        self.cfg.save()

    def _run_bg(self, fn, on_done=None, busy_text: str = "…") -> None:
        if self._busy:
            return

        def worker():
            self._busy = True
            self.after(0, lambda: self.action_btn.configure(state="disabled", text=busy_text))
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
                    self.action_btn.configure(state="normal")
                    self._refresh_ready_state()
                    if on_done:
                        on_done(result, err)

                self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ skin
    def _refresh_skin(self) -> None:
        def job():
            uuid = ""
            name = ""
            if self.auth_mode.get() == "microsoft" and self.cfg.microsoft_name:
                uuid = self.cfg.microsoft_uuid
                name = self.cfg.microsoft_name
            else:
                name = self.nick_entry.get().strip() or "Steve"
            path = fetch_skin_render(uuid=uuid, name=name, size=280)
            return path, name

        def worker():
            try:
                path, name = job()
            except Exception:
                return

            def apply():
                if not path:
                    return
                img = _load_ctk_image(Path(path), (180, 280))
                if not img:
                    return
                self._skin_image = img
                self.skin_label.configure(text="", image=img)
                self.skin_name.configure(text=name)

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ auth
    def _show_device_code(self, user_code: str, verify_uri: str) -> None:
        """Show Microsoft device-code instructions on the main thread."""

        def open_dialog():
            win = ctk.CTkToplevel(self)
            win.title("Login Microsoft")
            win.geometry("480x280")
            win.configure(fg_color=COLORS["bg1"])
            win.transient(self)
            try:
                win.grab_set()
            except Exception:
                pass
            frame = ctk.CTkFrame(win, fg_color="transparent")
            frame.pack(fill="both", expand=True, padx=24, pady=24)
            ctk.CTkLabel(
                frame,
                text="Entre com sua conta Microsoft",
                font=FONTS["title"],
                text_color=COLORS["accent"],
            ).pack(anchor="w")
            ctk.CTkLabel(
                frame,
                text=f"1. Abra {verify_uri}\n2. Digite o código abaixo\n3. Autorize e volte aqui",
                font=FONTS["small"],
                text_color=COLORS["muted"],
                justify="left",
                anchor="w",
            ).pack(fill="x", pady=(10, 16))
            ctk.CTkLabel(
                frame,
                text=user_code,
                font=("Consolas", 36, "bold"),
                text_color=COLORS["accent"],
            ).pack()
            ctk.CTkButton(
                frame,
                text="Abrir página de login",
                command=lambda: __import__("webbrowser").open(verify_uri),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_dim"],
                text_color=COLORS["accent_text"],
                height=40,
            ).pack(fill="x", pady=(20, 0))
            self._device_code_window = win

        self.after(0, open_dialog)

    def _close_device_code(self) -> None:
        win = getattr(self, "_device_code_window", None)
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            self._device_code_window = None

    def _login_microsoft(self) -> None:
        self._save_form()

        def job():
            return login_microsoft_browser(
                self.cfg,
                on_status=lambda msg: self.after(
                    0, lambda: self.ms_status.configure(text=msg, text_color=COLORS["accent"])
                ),
                on_device_code=self._show_device_code,
            )

        def done(session: Optional[GameSession], err):
            self._close_device_code()
            if err:
                self._set_status(str(err), ok=False)
                self.ms_status.configure(text=str(err), text_color=COLORS["danger"])
                return
            self.cfg = LauncherConfig.load()
            self.ms_status.configure(text=self._ms_status_text(), text_color=COLORS["ok"])
            self._set_status(f"Login OK — {session.username}")
            self._refresh_skin()

        self._run_bg(job, done, busy_text="LOGIN…")

    def _resolve_session(self) -> GameSession:
        if self.auth_mode.get() == "offline":
            return offline_session(self.nick_entry.get())
        if self.cfg.microsoft_refresh_token:
            try:
                return refresh_microsoft_session(self.cfg)
            except MicrosoftAuthError:
                pass
        saved = session_from_config_microsoft(self.cfg)
        if saved and saved.access_token and saved.access_token != "0":
            return saved
        raise MicrosoftAuthError("Faça login Microsoft antes de jogar.")

    # ------------------------------------------------------------------ actions
    def _on_action(self) -> None:
        if is_game_ready() and not self._downloading:
            self._play()
        else:
            self._download()

    def _download(self) -> None:
        self._save_form()
        self._downloading = True
        self._show_progress(True)
        self.progress.set(0)
        self.percent_label.configure(text="0%")
        self.progress_title.configure(text="Preparando")
        self.detail_label.configure(text="Iniciando download…")
        self.eta_label.configure(text="Tempo restante: calculando…")

        def on_progress(state: ProgressState) -> None:
            self.after(0, lambda s=state: self._set_progress_ui(s))

        def job():
            tracker = ProgressTracker(
                {"java": 0.20, "forge": 0.65, "mods": 0.15},
                on_update=on_progress,
            )
            prepare_game(self.cfg, tracker=tracker)
            sync_mods(tracker=tracker)
            return True

        def done(_ok, err):
            self._downloading = False
            if err:
                self._set_status(str(err), ok=False)
                self.progress_title.configure(text="Erro no download")
                self.detail_label.configure(text=str(err))
                return
            self.progress.set(1)
            self.percent_label.configure(text="100%")
            self.progress_title.configure(text="Download concluído")
            self.detail_label.configure(text="Tudo pronto — clique em JOGAR")
            self.eta_label.configure(text="Tempo restante: 0s")
            self._set_status("Download concluído!")
            self.after(800, lambda: self._show_progress(False))
            self._refresh_ready_state()

        self._run_bg(job, done, busy_text="BAIXANDO…")

    def _play(self) -> None:
        self._save_form()

        def job():
            # Silent ensure (already downloaded) + launch
            tracker = ProgressTracker(DEFAULT_PHASES)
            session = self._resolve_session()
            return prepare_and_launch(self.cfg, session, tracker=tracker)

        def done(_proc, err):
            if err:
                self._set_status(str(err), ok=False)
                # If somehow incomplete, flip back to Baixar
                if not is_game_ready():
                    self._refresh_ready_state()
                return
            self._set_status("Minecraft iniciado. Bom SMP!")
            if self.cfg.close_launcher_on_start:
                self.after(900, self.destroy)

        self._run_bg(job, done, busy_text="ABRINDO…")


def run_app() -> None:
    app = PUTsLauncherApp()
    app.mainloop()
