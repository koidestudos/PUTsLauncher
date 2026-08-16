from __future__ import annotations

import os
import subprocess
import threading
import traceback
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageTk
from tkinter import filedialog, messagebox

from launcher import __app_name__, __version__
from launcher.auth import (
    MicrosoftAuthError,
    login_microsoft_browser,
    offline_session,
    refresh_microsoft_session,
    session_from_config_microsoft,
)
from launcher.auth.session import GameSession, logout_microsoft, switch_account
from launcher.auth.skin import (
    bust_skin_caches,
    cache_local_skin,
    fetch_head_avatar,
    fetch_skin_texture,
    load_local_skin,
    purge_legacy_skin_files,
    upload_skin,
)
from launcher.config import (
    FORGE_VERSION,
    MC_VERSION,
    LauncherConfig,
    asset_path,
    bootstrap_instances,
    puts_home,
)
from launcher.core import (
    DEFAULT_PHASES,
    CancelledError,
    ProgressState,
    ProgressTracker,
    activate_instance,
    fetch_modpack_index,
    install_from_url,
    install_modpack,
    installed_instance_for,
    is_game_ready,
    list_instance_mods,
    list_instances,
    parse_pack_url,
    prepare_and_launch,
    prepare_game,
    reinstall_game,
    sync_mods,
    uninstall_game,
    verify_modpack_files,
)
from launcher.core.instances import apply_instance_to_config, delete_instance
from launcher.core.system import MIN_RAM_GB, clamp_ram_gb, max_ram_gb, total_ram_gb
from launcher.ui.skin3d import Skin3DViewer
from launcher.ui.theme import COLORS, FONTS, register_fonts


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


def _load_ctk_image(path: Path, size: tuple[int, int]) -> Optional[ctk.CTkImage]:
    if not path.exists():
        return None
    try:
        return _ctk_image(Image.open(path).convert("RGBA"), size)
    except Exception:
        return None


def _ctk_image(image: Image.Image, size: tuple[int, int]) -> Optional[ctk.CTkImage]:
    try:
        return ctk.CTkImage(light_image=image, dark_image=image, size=size)
    except Exception:
        return None


def _fit_window(win, min_w: int, min_h: int, pad: int = 60) -> None:
    """
    Size a popup to its own content and keep it on screen, centred on the app.
    Fixed geometries clipped titles and buttons on smaller screens / bigger fonts.
    """
    try:
        win.update_idletasks()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        width = max(min_w, win.winfo_reqwidth())
        height = max(min_h, win.winfo_reqheight())
        width = min(width, screen_w - 40)
        height = min(height, screen_h - pad)
        parent = win.master
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
        except Exception:
            px = py = 0
            pw, ph = screen_w, screen_h
        x = max(0, min(px + (pw - width) // 2, screen_w - width))
        y = max(0, min(py + (ph - height) // 2, screen_h - height))
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(min(min_w, width), min(min_h, height))
    except Exception:
        pass


def _make_glow_backdrop(width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), (10, 9, 7))
    draw = ImageDraw.Draw(img, "RGBA")
    for i in range(28):
        alpha = max(0, 40 - i)
        draw.ellipse(
            [-width // 3 + i * 8, -height // 4 + i * 6, width // 2 - i * 4, height // 2 - i * 3],
            fill=(240, 210, 74, alpha),
        )
    for i in range(22):
        alpha = max(0, 28 - i)
        draw.ellipse(
            [width // 3 + i * 5, height // 2 + i * 4, width + width // 4 - i * 3, height + height // 3],
            fill=(139, 58, 42, alpha),
        )
    return img.filter(ImageFilter.GaussianBlur(28))


def _open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys_platform() == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def sys_platform() -> str:
    import sys

    return sys.platform


class PUTsLauncherApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = bootstrap_instances(LauncherConfig.load())
        purge_legacy_skin_files()  # skins agora vivem só na RAM
        self.title(f"{__app_name__}")
        self.geometry("1020x680")
        self.minsize(940, 620)
        self.configure(fg_color=COLORS["bg0"])
        register_fonts(self)
        self._accounts_open = False
        self._skin_loading = False
        self._skin_request = 0
        self._head_request = 0
        self._options_win = None
        self._variant_card = None
        self._busy = False
        self._downloading = False
        self._cancel = threading.Event()
        self._game_proc = None
        self._head_image: Optional[ctk.CTkImage] = None
        self._logo_image = _load_ctk_image(asset_path("logo_transparent.png"), (88, 88)) or _load_ctk_image(
            asset_path("logo_square.png"), (88, 88)
        )
        self._ms_image = _load_ctk_image(asset_path("microsoft.png"), (18, 18))
        self._backdrop_label = None
        self._backdrop_photo = None
        self._menu_popup = None
        self._accounts_popup = None
        self._device_code_window = None

        self._build()
        self.after(80, self._paint_backdrop)
        self.after(120, self._refresh_ready_state)
        self.after(180, self._refresh_ms_profile)
        self.after(220, self._refresh_skin)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.shell = ctk.CTkFrame(self, fg_color=COLORS["bg0"], corner_radius=0)
        self.shell.grid(row=0, column=0, sticky="nsew")
        self.shell.grid_columnconfigure(0, weight=3)
        self.shell.grid_columnconfigure(1, weight=2)
        self.shell.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self.shell, fg_color="transparent", corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(7, weight=1)

        brand = ctk.CTkFrame(left, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=42, pady=(32, 6))
        brand.grid_columnconfigure(1, weight=1)

        # Logo = open MinecraftPUTS folder (transparent fruit, no crop mask)
        self.logo_btn = ctk.CTkButton(
            brand,
            text="",
            image=self._logo_image,
            width=92,
            height=92,
            corner_radius=18,
            fg_color="transparent",
            hover_color=COLORS["panel"],
            command=lambda: _open_path(puts_home()),
        )
        self.logo_btn.grid(row=0, column=0, rowspan=2, padx=(0, 14))

        ctk.CTkLabel(brand, text="PUTs", font=FONTS["display"], text_color=COLORS["accent"], anchor="w").grid(
            row=0, column=1, sticky="sw"
        )
        ctk.CTkLabel(
            brand,
            text="Minecraft Launcher  ·  maracujá edition",
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
        self.meta_label.grid(row=1, column=0, sticky="ew", padx=42, pady=(2, 6))

        # Instance switcher (CurseForge-style)
        inst_row = ctk.CTkFrame(left, fg_color="transparent")
        inst_row.grid(row=2, column=0, sticky="ew", padx=42, pady=(0, 10))
        inst_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(inst_row, text="Instância", font=FONTS["tiny"], text_color=COLORS["muted"], anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self.instance_var = ctk.StringVar(value="")
        self.instance_menu = ctk.CTkOptionMenu(
            inst_row,
            variable=self.instance_var,
            values=["PUTs SMP"],
            command=self._on_instance_chosen,
            height=36,
            font=FONTS["body_bold"],
            fg_color=COLORS["panel"],
            button_color=COLORS["accent_dim"],
            button_hover_color=COLORS["accent"],
            dropdown_fg_color=COLORS["panel"],
            dropdown_hover_color=COLORS["panel_soft"],
            text_color=COLORS["text"],
            dropdown_text_color=COLORS["text"],
        )
        self.instance_menu.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.btn_add_pack = ctk.CTkButton(
            inst_row,
            text="+ Modpack",
            width=110,
            height=36,
            corner_radius=10,
            font=FONTS["small"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hot"],
            text_color=COLORS["accent_text"],
            command=self._open_modpack_catalog,
        )
        self.btn_add_pack.grid(row=1, column=1, padx=(0, 6))
        self.btn_del_inst = ctk.CTkButton(
            inst_row,
            text="✕",
            width=36,
            height=36,
            corner_radius=10,
            font=FONTS["small"],
            fg_color=COLORS["panel"],
            hover_color=COLORS["danger"],
            text_color=COLORS["muted"],
            command=self._delete_active_instance,
        )
        self.btn_del_inst.grid(row=1, column=2)

        # Mode pills
        modes = ctk.CTkFrame(left, fg_color="transparent")
        modes.grid(row=3, column=0, sticky="ew", padx=42)
        modes.grid_columnconfigure((0, 1), weight=1)
        self.auth_mode = ctk.StringVar(
            value=self.cfg.auth_mode if self.cfg.auth_mode in ("offline", "microsoft") else "offline"
        )
        self.btn_offline = ctk.CTkButton(
            modes,
            text="Offline",
            command=lambda: self._set_mode("offline"),
            height=36,
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
            height=36,
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
        self.nick_wrap.grid(row=4, column=0, sticky="ew", padx=42, pady=(14, 0))
        self.nick_wrap.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.nick_wrap, text="Nickname", font=FONTS["small"], text_color=COLORS["muted"], anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.nick_entry = ctk.CTkEntry(
            self.nick_wrap,
            height=42,
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

        # Microsoft area: login OR profile chip
        self.ms_wrap = ctk.CTkFrame(left, fg_color="transparent")
        self.ms_wrap.grid(row=5, column=0, sticky="ew", padx=42, pady=(14, 0))
        self.ms_wrap.grid_columnconfigure(0, weight=1)

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
        self.btn_ms_login.grid(row=0, column=0, sticky="ew")

        self.profile_chip = ctk.CTkFrame(self.ms_wrap, fg_color=COLORS["panel"], corner_radius=14)
        self.profile_chip.grid(row=1, column=0, sticky="ew")
        self.profile_chip.grid_columnconfigure(0, weight=1)
        self.profile_chip.grid_remove()

        # Real button so the whole left area always receives clicks (CTk labels eat binds)
        self.profile_btn = ctk.CTkButton(
            self.profile_chip,
            text="Conta",
            image=None,
            compound="left",
            anchor="w",
            height=48,
            corner_radius=12,
            font=FONTS["body_bold"],
            fg_color="transparent",
            hover_color=COLORS["panel_soft"],
            text_color=COLORS["text"],
            command=self._toggle_accounts_menu,
        )
        self.profile_btn.grid(row=0, column=0, sticky="ew", padx=(4, 0), pady=4)

        self.btn_logout = ctk.CTkButton(
            self.profile_chip,
            text="Log out",
            width=78,
            height=34,
            corner_radius=10,
            font=FONTS["small"],
            fg_color=COLORS["berry"],
            hover_color="#a04838",
            text_color=COLORS["cream"],
            command=self._logout,
        )
        self.btn_logout.grid(row=0, column=1, padx=(4, 10), pady=8)

        # Expandable accounts list under profile chip (reliable vs floating popup)
        self.accounts_panel = ctk.CTkFrame(self.ms_wrap, fg_color=COLORS["panel_soft"], corner_radius=14)
        self.accounts_panel.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.accounts_panel.grid_remove()

        # Options (RAM + performance) replaces inline RAM slider
        opts = ctk.CTkFrame(left, fg_color="transparent")
        opts.grid(row=6, column=0, sticky="ew", padx=42, pady=(16, 0))
        opts.grid_columnconfigure(0, weight=1)
        self.btn_options = ctk.CTkButton(
            opts,
            text="⚙  Opções",
            command=self._open_options,
            height=44,
            corner_radius=12,
            font=FONTS["body_bold"],
            fg_color=COLORS["panel"],
            hover_color=COLORS["panel_soft"],
            text_color=COLORS["text"],
            border_width=1,
            border_color=COLORS["stroke"],
            anchor="w",
        )
        self.btn_options.grid(row=0, column=0, sticky="ew")

        # Actions
        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.grid(row=8, column=0, sticky="ew", padx=42, pady=(10, 24))
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
            self.progress_box, text="", font=FONTS["small"], text_color=COLORS["muted"], anchor="w", wraplength=480
        )
        self.detail_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 2))
        self.eta_label = ctk.CTkLabel(
            self.progress_box, text="Tempo restante: —", font=FONTS["tiny"], text_color=COLORS["stroke"], anchor="w"
        )
        self.eta_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        self.progress_box.grid_remove()

        btn_row = ctk.CTkFrame(actions, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)

        self.action_btn = ctk.CTkButton(
            btn_row,
            text="BAIXAR",
            command=self._on_action,
            height=52,
            corner_radius=12,
            font=FONTS["button"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hot"],
            text_color=COLORS["accent_text"],
        )
        self.action_btn.grid(row=0, column=0, sticky="ew")

        self.menu_btn = ctk.CTkButton(
            btn_row,
            text="▾",
            width=48,
            height=52,
            corner_radius=12,
            font=FONTS["button"],
            fg_color=COLORS["accent_dim"],
            hover_color=COLORS["accent_hot"],
            text_color=COLORS["accent_text"],
            command=self._toggle_action_menu,
        )
        self.menu_btn.grid(row=0, column=1, padx=(8, 0))

        self.cancel_btn = ctk.CTkButton(
            actions,
            text="CANCELAR",
            command=self._cancel_action,
            height=40,
            corner_radius=10,
            font=FONTS["body_bold"],
            fg_color=COLORS["berry"],
            hover_color="#a04838",
            text_color=COLORS["cream"],
        )
        self.cancel_btn.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.cancel_btn.grid_remove()

        self.footer = ctk.CTkLabel(
            actions,
            text=f"v{__version__}  ·  clique no maracujá para abrir a pasta",
            font=FONTS["tiny"],
            text_color=COLORS["stroke"],
            anchor="w",
        )
        self.footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        # RIGHT — 3D skin
        right = ctk.CTkFrame(self.shell, fg_color=COLORS["bg2"], corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.skin_title = ctk.CTkLabel(right, text="Sua skin", font=FONTS["title"], text_color=COLORS["cream"])
        self.skin_title.grid(row=0, column=0, pady=(40, 8))
        self.right_panel = right

        self.skin_stage = ctk.CTkFrame(right, fg_color=COLORS["panel"], corner_radius=24)
        self.skin_stage.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 12))
        self.skin_stage.grid_rowconfigure(0, weight=1)
        self.skin_stage.grid_columnconfigure(0, weight=1)

        self.skin_viewer = Skin3DViewer(self.skin_stage, width=320, height=520, bg=COLORS["panel"])
        self.skin_viewer.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        # Loading / variant overlays sit on top of the skin stage
        self.skin_load_overlay = ctk.CTkFrame(self.skin_stage, fg_color=COLORS["panel"], corner_radius=20)
        self.skin_load_label = ctk.CTkLabel(
            self.skin_load_overlay,
            text="Carregando Skin…",
            font=FONTS["title"],
            text_color=COLORS["accent"],
        )
        self.skin_load_label.place(relx=0.5, rely=0.5, anchor="center")

        self.btn_change_skin = ctk.CTkButton(
            right,
            text="Mudar skin",
            command=self._change_skin,
            height=38,
            corner_radius=10,
            font=FONTS["body_bold"],
            fg_color=COLORS["panel"],
            hover_color=COLORS["panel_soft"],
            text_color=COLORS["text"],
            border_width=1,
            border_color=COLORS["stroke"],
        )
        self.btn_change_skin.grid(row=2, column=0, sticky="ew", padx=48, pady=(0, 36))

        self._set_mode(self.auth_mode.get())
        self._refresh_instance_menu()
        self._refresh_ready_state()

    def _paint_backdrop(self) -> None:
        try:
            import tkinter as tk

            w = max(self.winfo_width(), 1020)
            h = max(self.winfo_height(), 680)
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

    # ------------------------------------------------------------------ helpers
    def _ms_logged_in(self) -> bool:
        return bool(self.cfg.microsoft_name and self.cfg.microsoft_access_token)

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
            self._refresh_ms_profile()
        self._refresh_skin()

    def _on_ram(self, value: float) -> None:
        if getattr(self, "ram_value", None) is not None:
            self.ram_value.configure(text=f"{int(round(value))} GB")

    def _refresh_ready_state(self) -> None:
        ready = is_game_ready()
        try:
            from launcher.core.instances import GameInstance, get_active_id

            inst = GameInstance.load(get_active_id())
        except Exception:
            inst = None
        try:
            n_mods = len(list_instance_mods())
        except Exception:
            n_mods = 0
        if inst:
            pack = f"  ·  {inst.modpack_id}@{inst.modpack_version}" if inst.modpack_id else ""
            mods = f"  ·  {n_mods} mods" if n_mods else "  ·  sem mods"
            hint = "pronto" if ready else "baixar Java/Forge"
            self.meta_label.configure(
                text=f"{inst.name}  ·  MC {inst.mc_version}  ·  Forge {inst.forge_version}{pack}{mods}  ·  {hint}"
            )
        else:
            self.meta_label.configure(text="Instale um modpack em + Modpack")
        if self._downloading or self._busy:
            return
        if ready:
            self.action_btn.configure(text="JOGAR", fg_color=COLORS["accent"], hover_color=COLORS["accent_hot"])
        else:
            self.action_btn.configure(text="BAIXAR", fg_color=COLORS["accent_hot"], hover_color=COLORS["accent"])
        self.progress_box.grid_remove()
        self.cancel_btn.grid_remove()

    def _show_progress(self, show: bool) -> None:
        if show:
            self.progress_box.grid()
            self.cancel_btn.grid()
        else:
            self.progress_box.grid_remove()
            self.cancel_btn.grid_remove()

    def _set_progress_ui(self, state: ProgressState) -> None:
        self._show_progress(True)
        self.progress.set(max(0.0, min(1.0, state.percent / 100.0)))
        self.percent_label.configure(text=f"{int(state.percent)}%")
        titles = {
            "java": "Baixando Java",
            "forge": "Baixando Minecraft + Forge",
            "mods": "Mods do pack",
            "launch": "Abrindo o jogo",
        }
        self.progress_title.configure(text=titles.get(state.phase, state.phase.capitalize() or "Baixando"))
        self.detail_label.configure(text=state.detail or "")
        self.eta_label.configure(text=f"Tempo restante: {state.eta_text}")

    def _save_form(self) -> None:
        self.cfg.auth_mode = self.auth_mode.get()
        self.cfg.username = self.nick_entry.get().strip() or "Steve"
        if getattr(self, "ram_slider", None) is not None:
            self.cfg.ram_gb = clamp_ram_gb(round(self.ram_slider.get()))
        self.cfg.save()

    def _run_bg(self, fn, on_done=None, busy_text: str = "…") -> None:
        # Claim the slot synchronously: two fast clicks must not both start.
        if self._busy:
            return
        self._busy = True

        def worker():
            def mark_busy():
                if busy_text is None:
                    self.action_btn.configure(state="disabled")
                else:
                    self.action_btn.configure(state="disabled", text=busy_text)
                self.menu_btn.configure(state="disabled")
            self.after(0, mark_busy)
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
                    self.menu_btn.configure(state="normal")
                    self._refresh_ready_state()
                    if on_done:
                        on_done(result, err)

                self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ instances / modpacks
    def _refresh_instance_menu(self) -> None:
        insts = list_instances()
        if not insts:
            return
        labels = []
        self._instance_by_label = {}
        active = None
        for i in insts:
            label = i.name
            # Disambiguate duplicate names
            if label in self._instance_by_label:
                label = f"{i.name} ({i.id})"
            self._instance_by_label[label] = i.id
            labels.append(label)
            if i.id == (self.cfg.active_instance_id or ""):
                active = label
        self.instance_menu.configure(values=labels)
        chosen = active or labels[0]
        self.instance_var.set(chosen)
        self._update_instance_meta()

    def _update_instance_meta(self) -> None:
        try:
            from launcher.core.instances import GameInstance, get_active_id

            inst = GameInstance.load(get_active_id())
        except Exception:
            inst = None
        if inst:
            pack = f"  ·  {inst.modpack_id}@{inst.modpack_version}" if inst.modpack_id else ""
            self.meta_label.configure(
                text=f"{inst.name}  ·  MC {inst.mc_version}  ·  Forge {inst.forge_version}{pack}"
            )
        else:
            self.meta_label.configure(text=f"Minecraft {MC_VERSION}  ·  Forge {FORGE_VERSION}")

    def _on_instance_chosen(self, label: str) -> None:
        iid = getattr(self, "_instance_by_label", {}).get(label)
        if not iid:
            return
        if self._busy:
            # Switching mid-install would point the installer at another folder.
            messagebox.showinfo("Instância", "Espere a tarefa atual terminar para trocar de instância.")
            self._refresh_instance_menu()
            return
        inst = activate_instance(iid)
        apply_instance_to_config(self.cfg, inst)
        self.cfg = LauncherConfig.load()
        self._update_instance_meta()
        self._refresh_ready_state()
        self._refresh_skin()

    def _delete_active_instance(self) -> None:
        from launcher.core.instances import get_active_id, list_instances

        if self._busy:
            messagebox.showinfo("Instância", "Espere a tarefa atual terminar para remover a instância.")
            return
        iid = get_active_id()
        if len(list_instances()) <= 1:
            messagebox.showinfo(
                "Instância",
                "Não dá para remover a única instância.\n"
                "Instale outro modpack antes, ou use Desinstalar para limpar os arquivos.",
            )
            return
        if not messagebox.askyesno("Remover instância", f"Apagar a instância “{iid}” do disco?"):
            return
        delete_instance(iid)
        self.cfg = bootstrap_instances(LauncherConfig.load())
        self._refresh_instance_menu()
        self._refresh_ready_state()

    def _open_modpack_catalog(self) -> None:
        if self._busy:
            messagebox.showinfo("Modpacks", "Espere a tarefa atual terminar antes de abrir o catálogo.")
            return

        catalog_url = self.cfg.catalog_source()

        win = ctk.CTkToplevel(self)
        win.title("Modpacks")
        win.geometry("540x620")
        win.minsize(480, 480)
        win.configure(fg_color=COLORS["bg1"])
        win.transient(self)

        head = ctk.CTkFrame(win, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(head, text="Modpacks", font=FONTS["title"], text_color=COLORS["accent"]).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text="Cole um link do CurseForge ou Modrinth, ou use o catálogo GitHub.",
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
            wraplength=480,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # --- Import by URL ---
        import_box = ctk.CTkFrame(win, fg_color=COLORS["panel"], corner_radius=14)
        import_box.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(
            import_box,
            text="Importar por link",
            font=FONTS["body_bold"],
            text_color=COLORS["text"],
            anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(
            import_box,
            text="Ex.: modrinth.com/modpack/…  ·  curseforge.com/minecraft/modpacks/…",
            font=FONTS["tiny"],
            text_color=COLORS["muted"],
            anchor="w",
        ).pack(fill="x", padx=14, pady=(2, 6))
        link_var = ctk.StringVar()
        link_entry = ctk.CTkEntry(
            import_box,
            textvariable=link_var,
            height=36,
            corner_radius=10,
            font=FONTS["small"],
            placeholder_text="https://modrinth.com/modpack/…",
        )
        link_entry.pack(fill="x", padx=14, pady=(0, 8))

        def do_import():
            raw = (link_var.get() or "").strip()
            if not raw:
                messagebox.showinfo("Importar", "Cole o link do modpack primeiro.", parent=win)
                return
            try:
                parse_pack_url(raw)
            except ValueError as exc:
                messagebox.showerror("Importar", str(exc), parent=win)
                return
            self._install_from_pack_url(raw, win)

        ctk.CTkButton(
            import_box,
            text="Criar instância",
            height=36,
            corner_radius=10,
            font=FONTS["body_bold"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hot"],
            text_color=COLORS["accent_text"],
            command=do_import,
        ).pack(fill="x", padx=14, pady=(0, 12))
        link_entry.bind("<Return>", lambda _e: do_import())

        # --- GitHub catalog ---
        gh_head = ctk.CTkFrame(win, fg_color="transparent")
        gh_head.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(
            gh_head,
            text="Catálogo GitHub",
            font=FONTS["body_bold"],
            text_color=COLORS["text"],
            anchor="w",
        ).pack(anchor="w")
        status = ctk.CTkLabel(
            gh_head,
            text="Carregando…" if catalog_url else "Não configurado — use Opções ou o link acima.",
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
            wraplength=480,
            justify="left",
        )
        status.pack(anchor="w", pady=(2, 0))

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=8)

        if not catalog_url:
            tip = ctk.CTkFrame(scroll, fg_color=COLORS["panel"], corner_radius=14)
            tip.pack(fill="x", pady=6)
            ctk.CTkLabel(
                tip,
                text="Sem repositório de releases. Em Opções você pode apontar "
                "dono/repo (ex.: koidestudos/PUTsModpacks) para listar packs aqui.",
                font=FONTS["small"],
                text_color=COLORS["muted"],
                wraplength=460,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=14, pady=14)
            ctk.CTkButton(
                tip,
                text="Abrir Opções",
                height=32,
                corner_radius=10,
                font=FONTS["small"],
                fg_color="transparent",
                hover_color=COLORS["stroke"],
                text_color=COLORS["muted"],
                border_width=1,
                border_color=COLORS["stroke"],
                command=lambda: (win.destroy(), self._open_options()),
            ).pack(fill="x", padx=14, pady=(0, 12))
            _fit_window(win, 540, 520, pad=80)
            return

        def render(packs, err=None):
            for child in scroll.winfo_children():
                child.destroy()
            if err:
                status.configure(text=str(err), text_color=COLORS["danger"])
                return
            status.configure(text=f"{len(packs)} modpack(s) no GitHub", text_color=COLORS["muted"])
            if not packs:
                ctk.CTkLabel(scroll, text="Nenhum modpack no catálogo.", text_color=COLORS["muted"]).pack()
                return
            for pack in packs:
                card = ctk.CTkFrame(scroll, fg_color=COLORS["panel"], corner_radius=14)
                card.pack(fill="x", pady=6)
                ctk.CTkLabel(
                    card, text=pack.name, font=FONTS["body_bold"], text_color=COLORS["text"], anchor="w"
                ).pack(fill="x", padx=14, pady=(12, 0))
                ctk.CTkLabel(
                    card,
                    text=f"v{pack.version}  ·  MC {pack.mc_version}  ·  {pack.loader} {pack.loader_version}",
                    font=FONTS["tiny"],
                    text_color=COLORS["muted"],
                    anchor="w",
                ).pack(fill="x", padx=14)
                if pack.description:
                    ctk.CTkLabel(
                        card,
                        text=pack.description,
                        font=FONTS["small"],
                        text_color=COLORS["stroke"],
                        anchor="w",
                        wraplength=440,
                        justify="left",
                    ).pack(fill="x", padx=14, pady=(4, 0))

                installed = installed_instance_for(pack, catalog_url)
                same_version = installed is not None and (
                    (installed.modpack_version or "") == (pack.version or "")
                )
                if installed is not None:
                    ctk.CTkLabel(
                        card,
                        text=(
                            f"Instalado como “{installed.name}”"
                            + (
                                "  ·  atualizado"
                                if same_version
                                else f"  ·  você tem a v{installed.modpack_version or '?'}"
                            )
                        ),
                        font=FONTS["tiny"],
                        text_color=COLORS["ok"] if same_version else COLORS["accent_hot"],
                        anchor="w",
                    ).pack(fill="x", padx=14, pady=(6, 0))

                def make_action(p=pack, inst=installed, same=same_version):
                    def run():
                        if inst is None:
                            self._install_catalog_pack(p, win, origin=catalog_url)
                        elif same:
                            self._verify_catalog_pack(p, inst, win)
                        else:
                            self._install_catalog_pack(p, win, existing=inst, origin=catalog_url)

                    return run

                if installed is None:
                    action_text = "Instalar instância"
                elif same_version:
                    action_text = "Verificar arquivos"
                else:
                    action_text = f"Atualizar para v{pack.version}"

                ctk.CTkButton(
                    card,
                    text=action_text,
                    height=36,
                    corner_radius=10,
                    font=FONTS["body_bold"],
                    fg_color=COLORS["accent"],
                    hover_color=COLORS["accent_hot"],
                    text_color=COLORS["accent_text"],
                    command=make_action(),
                ).pack(fill="x", padx=14, pady=(10, 6 if installed is not None else 12))

                if installed is not None:
                    def make_reinstall(p=pack, inst=installed):
                        return lambda: self._install_catalog_pack(p, win, existing=inst, origin=catalog_url)

                    ctk.CTkButton(
                        card,
                        text="Reinstalar do zero",
                        height=30,
                        corner_radius=10,
                        font=FONTS["small"],
                        fg_color="transparent",
                        hover_color=COLORS["stroke"],
                        text_color=COLORS["muted"],
                        border_width=1,
                        border_color=COLORS["stroke"],
                        command=make_reinstall(),
                    ).pack(fill="x", padx=14, pady=(0, 12))

            _fit_window(win, 540, 620, pad=120)

        def job():
            return fetch_modpack_index(catalog_url)

        def done(packs, err):
            render(packs or [], err)

        self._run_bg(job, done, busy_text=None)

    def _install_from_pack_url(self, url: str, catalog_win=None) -> None:
        if catalog_win is not None:
            try:
                catalog_win.destroy()
            except Exception:
                pass

        self._cancel.clear()
        self._show_progress(True)
        self.progress_title.configure(text="Importar pack")
        self.detail_label.configure(text="Baixando modpack…")

        def job():
            tracker = ProgressTracker({"mods": 0.55, "java": 0.2, "forge": 0.25})
            tracker.on_update = lambda s: self.after(0, lambda: self._set_progress_ui(s))
            inst = install_from_url(url, tracker=tracker, cancel_event=self._cancel)
            activate_instance(inst.id)
            apply_instance_to_config(self.cfg, inst)
            self.cfg = LauncherConfig.load()
            prepare_game(self.cfg, tracker=tracker, cancel_event=self._cancel)
            sync_mods(tracker=tracker)
            return inst

        def done(inst, err):
            self._show_progress(False)
            if isinstance(err, CancelledError) or self._cancel.is_set():
                self.detail_label.configure(text="Importação cancelada.")
                self._refresh_instance_menu()
                self._refresh_ready_state()
                return
            if err:
                messagebox.showerror("Importar modpack", str(err))
                return
            self.cfg = LauncherConfig.load()
            self._refresh_instance_menu()
            self._refresh_ready_state()
            messagebox.showinfo("Importar modpack", f"Instância pronta: {inst.name}")

        self._run_bg(job, done, busy_text="PACK…")

    def _install_catalog_pack(self, pack, catalog_win=None, existing=None, origin: str = "") -> None:
        if existing is not None:
            if not messagebox.askyesno(
                "Reinstalar modpack",
                f"“{existing.name}” já está instalada com a v{existing.modpack_version or '?'}.\n\n"
                f"Reinstalar troca os arquivos do pack pela v{pack.version}. "
                "Mundos e screenshots ficam, mas mods e configurações do pack voltam ao original.\n\n"
                "Quer continuar?",
            ):
                return
        if catalog_win is not None:
            try:
                catalog_win.destroy()
            except Exception:
                pass

        self._cancel.clear()
        self._show_progress(True)
        self.progress_title.configure(text="Modpack")
        self.detail_label.configure(text=f"Instalando {pack.name}…")

        def job():
            tracker = ProgressTracker({"mods": 0.55, "java": 0.2, "forge": 0.25})
            tracker.on_update = lambda s: self.after(0, lambda: self._set_progress_ui(s))
            inst = install_modpack(
                pack, tracker=tracker, cancel_event=self._cancel, catalog_origin=origin
            )
            activate_instance(inst.id)
            apply_instance_to_config(self.cfg, inst)
            # Install Java/Forge into the new instance game dir
            self.cfg = LauncherConfig.load()
            prepare_game(self.cfg, tracker=tracker, cancel_event=self._cancel)
            sync_mods(tracker=tracker)
            return inst

        def done(inst, err):
            self._show_progress(False)
            if isinstance(err, CancelledError) or self._cancel.is_set():
                self.detail_label.configure(text="Instalação cancelada.")
                self._refresh_instance_menu()
                self._refresh_ready_state()
                return
            if err:
                messagebox.showerror("Modpack", str(err))
                return
            self.cfg = LauncherConfig.load()
            self._refresh_instance_menu()
            self._refresh_ready_state()
            messagebox.showinfo("Modpack", f"Instância pronta: {inst.name}")

        self._run_bg(job, done, busy_text="PACK…")

    def _verify_catalog_pack(self, pack, instance, catalog_win=None) -> None:
        """Steam-style integrity check: compare hashes and put back what is off."""
        if catalog_win is not None:
            try:
                catalog_win.destroy()
            except Exception:
                pass

        self._cancel.clear()
        self._show_progress(True)
        self.progress_title.configure(text="Verificando")
        self.detail_label.configure(text=f"Conferindo arquivos de {pack.name}…")

        def job():
            tracker = ProgressTracker({"mods": 1.0})
            tracker.on_update = lambda s: self.after(0, lambda: self._set_progress_ui(s))
            return verify_modpack_files(pack, instance, tracker=tracker, cancel_event=self._cancel)

        def done(report, err):
            self._show_progress(False)
            if isinstance(err, CancelledError) or self._cancel.is_set():
                self.detail_label.configure(text="Verificação cancelada.")
                return
            if err:
                messagebox.showerror("Verificar arquivos", str(err))
                return
            if report["ok"]:
                messagebox.showinfo(
                    "Verificar arquivos",
                    f"{report['checked']} arquivo(s) do pack conferidos — está tudo no lugar.",
                )
            else:
                faltando = len(report["missing"])
                trocados = len(report["changed"])
                messagebox.showinfo(
                    "Verificar arquivos",
                    f"{report['checked']} arquivo(s) conferidos.\n"
                    f"{faltando} faltando, {trocados} diferentes — "
                    f"{len(report['repaired'])} restaurado(s) do pack.",
                )
            self._refresh_ready_state()

        self._run_bg(job, done, busy_text="CONFERINDO…")

    # ------------------------------------------------------------------ profile / accounts
    def _refresh_ms_profile(self) -> None:
        if self.auth_mode.get() != "microsoft":
            return
        if self._ms_logged_in():
            self.btn_ms_login.grid_remove()
            self.profile_chip.grid()
            self.profile_btn.configure(text=f"  {self.cfg.microsoft_name}   ▾")
            self._load_head()
            self.btn_change_skin.configure(state="normal")
        else:
            self.profile_chip.grid_remove()
            self.btn_ms_login.grid()
            self.btn_change_skin.configure(state="disabled")

    def _load_head(self, bust: bool = False) -> None:
        uuid = self.cfg.microsoft_uuid
        name = self.cfg.microsoft_name
        self._head_request += 1
        request = self._head_request

        def worker():
            head = fetch_head_avatar(uuid=uuid, name=name, size=64, bust=bust)
            if head is None:
                return
            img = _ctk_image(head, (32, 32))
            if img:
                def apply():
                    # Account may have changed, or a newer request already won.
                    if request != self._head_request:
                        return
                    if (self.cfg.microsoft_uuid, self.cfg.microsoft_name) != (uuid, name):
                        return
                    self._head_image = img
                    self.profile_btn.configure(image=img, text=f"  {name}   ▾")

                self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_accounts_menu(self) -> None:
        if self._accounts_open:
            self._close_accounts_menu()
            return
        self._rebuild_accounts_panel()
        self.accounts_panel.grid()
        self._accounts_open = True
        self.accounts_panel.lift()

    def _rebuild_accounts_panel(self) -> None:
        for child in self.accounts_panel.winfo_children():
            child.destroy()

        accounts = list(self.cfg.saved_accounts or [])
        if self.cfg.microsoft_name and not any((a.get("name") == self.cfg.microsoft_name) for a in accounts):
            accounts = [
                {
                    "name": self.cfg.microsoft_name,
                    "uuid": self.cfg.microsoft_uuid,
                    "access_token": self.cfg.microsoft_access_token,
                    "refresh_token": self.cfg.microsoft_refresh_token,
                }
            ] + accounts

        ctk.CTkLabel(
            self.accounts_panel,
            text="Contas",
            font=FONTS["small"],
            text_color=COLORS["muted"],
            anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 4))

        for acc in accounts:
            name = acc.get("name") or "?"
            is_current = name == self.cfg.microsoft_name

            def make_cmd(a=acc):
                return lambda: self._select_account(a)

            ctk.CTkButton(
                self.accounts_panel,
                text=("●  " if is_current else "○  ") + name,
                anchor="w",
                height=36,
                corner_radius=10,
                fg_color=COLORS["panel"] if is_current else "transparent",
                hover_color=COLORS["stroke"],
                text_color=COLORS["accent"] if is_current else COLORS["text"],
                command=make_cmd(),
            ).pack(fill="x", padx=8, pady=2)

        ctk.CTkButton(
            self.accounts_panel,
            text="+  Adicionar conta",
            anchor="center",
            height=42,
            corner_radius=12,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hot"],
            text_color=COLORS["accent_text"],
            font=FONTS["body_bold"],
            command=self._add_account,
        ).pack(fill="x", padx=10, pady=(8, 12))

    def _on_root_click_accounts(self, event) -> None:
        return

    def _close_accounts_menu(self) -> None:
        self._accounts_open = False
        try:
            self.accounts_panel.grid_remove()
        except Exception:
            pass
        self._accounts_popup = None

    def _fade_out_accounts(self) -> None:
        self._close_accounts_menu()

    def _select_account(self, account: dict) -> None:
        self._fade_out_accounts()
        switch_account(self.cfg, account)
        self.cfg = LauncherConfig.load()
        self._refresh_ms_profile()
        self._refresh_skin()

    def _add_account(self) -> None:
        self._close_accounts_menu()
        self._login_microsoft()

    def _logout(self) -> None:
        self._close_accounts_menu()
        logout_microsoft(self.cfg, remove_current=True)
        self.cfg = LauncherConfig.load()
        if self._ms_logged_in():
            self._refresh_ms_profile()
            self._refresh_skin()
        else:
            self._refresh_ms_profile()
            self._set_mode("offline")

    # ------------------------------------------------------------------ skin
    def _skin_identity(self) -> tuple[str, str]:
        """(uuid, nick) the preview should be showing right now."""
        if self.auth_mode.get() == "microsoft" and self.cfg.microsoft_name:
            return self.cfg.microsoft_uuid, self.cfg.microsoft_name
        return "", (self.nick_entry.get().strip() or "Steve")

    def _refresh_skin(self, bust: bool = False) -> None:
        self._skin_request += 1
        request = self._skin_request
        uuid, name = self._skin_identity()

        def worker():
            texture = fetch_skin_texture(uuid=uuid, name=name, bust=bust)

            def apply():
                # A slower earlier request must not repaint over a newer nick/account.
                if request != self._skin_request:
                    return
                self._apply_skin_texture(texture)

            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_skin_texture(self, texture, slim: Optional[bool] = None) -> None:
        """Show a texture that lives in memory (nothing is written to disk)."""
        if texture is None:
            self.skin_viewer.set_texture(None)
            return
        self.skin_viewer.set_texture_image(texture, slim=slim)

    def _change_skin(self) -> None:
        if not self._ms_logged_in():
            messagebox.showinfo("Mudar skin", "Faça login Microsoft para mudar a skin.")
            return
        path = filedialog.askopenfilename(
            title="Escolha a skin (.png)",
            filetypes=[("PNG", "*.png"), ("Todos", "*.*")],
        )
        if not path:
            return
        try:
            texture = load_local_skin(Path(path))
        except Exception as exc:
            messagebox.showerror("Mudar skin", f"{exc}")
            return
        self._show_skin_variant_picker(Path(path), texture)

    def _show_skin_variant_picker(self, local: Path, texture=None) -> None:
        """Animated Classic/Slim card inside the skin panel (semi-transparent backdrop)."""
        self._close_variant_picker()
        try:
            self._apply_skin_texture(texture if texture is not None else load_local_skin(local), slim=False)
        except Exception:
            pass

        # Dim skin stage (card sits over a darkened panel so the figure still peeks around)
        overlay = ctk.CTkFrame(self.skin_stage, fg_color="#0c0a07", corner_radius=20)
        self._variant_overlay = overlay
        overlay.place(relx=0.04, rely=0.04, relwidth=0.92, relheight=0.92)
        overlay.lift()

        card = ctk.CTkFrame(
            overlay,
            fg_color=COLORS["panel"],
            corner_radius=18,
            border_width=1,
            border_color=COLORS["accent_dim"],
            width=300,
            height=280,
        )
        self._variant_card = card
        card.place(relx=0.5, rely=1.2, anchor="center")  # start below, animate up

        # Close X
        ctk.CTkButton(
            card,
            text="✕",
            width=34,
            height=34,
            corner_radius=10,
            fg_color="transparent",
            hover_color=COLORS["berry"],
            text_color=COLORS["muted"],
            font=FONTS["body_bold"],
            command=self._close_variant_picker,
        ).place(relx=1.0, rely=0.0, x=-10, y=10, anchor="ne")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.52, anchor="center", relwidth=0.88)

        ctk.CTkLabel(inner, text="Modelo", font=FONTS["title"], text_color=COLORS["accent"]).pack(anchor="w")
        ctk.CTkLabel(
            inner,
            text="Classic (Steve) ou Slim (Alex)",
            font=FONTS["small"],
            text_color=COLORS["muted"],
        ).pack(anchor="w", pady=(2, 14))

        variant = ctk.StringVar(value="Classic")
        tabs = ctk.CTkSegmentedButton(
            inner,
            values=["Classic", "Slim"],
            variable=variant,
            font=FONTS["body_bold"],
            height=40,
            selected_color=COLORS["accent"],
            selected_hover_color=COLORS["accent_hot"],
            unselected_color=COLORS["panel_soft"],
            unselected_hover_color=COLORS["stroke"],
            text_color=COLORS["text"],
            fg_color=COLORS["bg1"],
            command=lambda v: self.skin_viewer.set_slim(str(v).lower() == "slim"),
        )
        tabs.pack(fill="x")
        tabs.set("Classic")

        ctk.CTkLabel(inner, text=local.name, font=FONTS["tiny"], text_color=COLORS["stroke"]).pack(
            anchor="w", pady=(12, 0)
        )

        def confirm():
            chosen = "slim" if variant.get().lower() == "slim" else "classic"
            self._close_variant_picker()
            self._upload_skin_file(local, chosen)

        ctk.CTkButton(
            inner,
            text="Enviar skin",
            height=42,
            corner_radius=12,
            font=FONTS["body_bold"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hot"],
            text_color=COLORS["accent_text"],
            command=confirm,
        ).pack(fill="x", pady=(18, 0))

        # Slide + fade-ish animation
        self._animate_variant_card(0.0)

    def _animate_variant_card(self, t: float) -> None:
        card = getattr(self, "_variant_card", None)
        overlay = getattr(self, "_variant_overlay", None)
        if not card or not card.winfo_exists():
            return
        # ease-out cubic
        t = max(0.0, min(1.0, t))
        e = 1 - (1 - t) ** 3
        rely = 1.15 - 0.65 * e  # 1.15 → 0.50
        try:
            card.place_configure(relx=0.5, rely=rely, anchor="center")
        except Exception:
            return
        if t < 1.0:
            self.after(16, lambda: self._animate_variant_card(t + 0.07))

    def _close_variant_picker(self) -> None:
        for attr in ("_variant_overlay", "_variant_card"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _set_skin_loading(self, loading: bool) -> None:
        self._skin_loading = loading
        if loading:
            self.skin_load_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.skin_load_overlay.lift()
            self.action_btn.configure(
                state="disabled",
                fg_color=COLORS["disabled"],
                text_color=COLORS["disabled_text"],
                hover_color=COLORS["disabled"],
            )
            self.menu_btn.configure(state="disabled")
        else:
            try:
                self.skin_load_overlay.place_forget()
            except Exception:
                pass
            self.action_btn.configure(
                state="normal",
                fg_color=COLORS["accent"],
                text_color=COLORS["accent_text"],
                hover_color=COLORS["accent_hot"],
            )
            self.menu_btn.configure(state="normal")
            self._refresh_ready_state()

    def _upload_skin_file(self, local: Path, variant: str) -> None:
        self._set_skin_loading(True)
        # Any refresh started before the upload is stale from here on.
        self._skin_request += 1
        request = self._skin_request
        identity = (self.cfg.microsoft_uuid, self.cfg.microsoft_name)

        def job():
            token = self.cfg.microsoft_access_token
            if self.cfg.microsoft_refresh_token:
                try:
                    session = refresh_microsoft_session(self.cfg)
                    self.cfg = LauncherConfig.load()
                    token = session.access_token
                except Exception:
                    pass

            try:
                upload_skin(token, local, variant=variant)
            except PermissionError:
                session = refresh_microsoft_session(self.cfg)
                self.cfg = LauncherConfig.load()
                upload_skin(session.access_token, local, variant=variant)

            bust_skin_caches(self.cfg.microsoft_uuid, self.cfg.microsoft_name)
            cached = cache_local_skin(local, uuid=self.cfg.microsoft_uuid, name=self.cfg.microsoft_name)
            return cached, variant

        def done(result, err):
            self._set_skin_loading(False)
            if request != self._skin_request or (self.cfg.microsoft_uuid, self.cfg.microsoft_name) != identity:
                return  # conta trocou durante o upload — não pinta a skin errada
            cached_texture = None
            variant_used = variant
            if result and isinstance(result, tuple):
                cached_texture, variant_used = result
            if err:
                try:
                    self._apply_skin_texture(load_local_skin(local), slim=variant_used == "slim")
                except Exception:
                    pass
                messagebox.showwarning(
                    "Mudar skin",
                    f"{err}\n\nSe a skin mudou no Minecraft, ignore este aviso — "
                    "o preview do launcher já tentou atualizar com o arquivo local.",
                )
                return
            if cached_texture is not None:
                self._skin_request += 1  # a textura recém-enviada é a atual
                self._apply_skin_texture(cached_texture, slim=variant_used == "slim")
            else:
                self._refresh_skin(bust=True)
            self._load_head(bust=True)

        self._run_bg(job, done, busy_text=None)

    def _server_hint(self) -> str:
        """Explain which address the game will actually join."""
        try:
            from launcher.core.instances import GameInstance, get_active_id, migration_server_backup

            inst = GameInstance.load(get_active_id())
            backup = migration_server_backup(self.cfg)
        except Exception:
            inst = None
            backup = None
        if backup:
            return (
                f"Removemos {backup[0]}:{backup[1]} daqui — esse endereço vinha do modpack e agora "
                "fica só na instância. Se você tinha digitado ele de propósito, é só escrever de novo."
            )
        if inst is not None and (inst.server_ip or "").strip():
            return (
                f"“{inst.name}” já entra em {inst.server_ip}:{inst.server_port or 25565} "
                "(definido pelo modpack). Este endereço só vale para instâncias sem servidor próprio."
            )
        return "Usado pelas instâncias que não trazem servidor próprio. Deixe vazio para abrir no menu do jogo."

    def _open_options(self) -> None:
        if self._options_win and self._options_win.winfo_exists():
            self._options_win.lift()
            return

        win = ctk.CTkToplevel(self)
        self._options_win = win
        win.title("Opções")
        win.geometry("460x620")
        win.minsize(420, 420)
        win.configure(fg_color=COLORS["bg1"])
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(scroll, text="Opções", font=FONTS["title"], text_color=COLORS["accent"]).pack(anchor="w")
        ctk.CTkLabel(
            scroll,
            text="Memória, desempenho e comportamento do launcher.",
            font=FONTS["small"],
            text_color=COLORS["muted"],
        ).pack(anchor="w", pady=(4, 16))

        # RAM — never offer more than the machine actually has
        ram_max = max_ram_gb()
        ram_now = clamp_ram_gb(self.cfg.ram_gb or 4)
        ctk.CTkLabel(scroll, text="Memória RAM", font=FONTS["body_bold"], text_color=COLORS["text"]).pack(anchor="w")
        ram_row = ctk.CTkFrame(scroll, fg_color="transparent")
        ram_row.pack(fill="x", pady=(6, 4))
        ram_row.grid_columnconfigure(0, weight=1)
        self.ram_slider = ctk.CTkSlider(
            ram_row,
            from_=MIN_RAM_GB,
            to=ram_max,
            number_of_steps=max(1, ram_max - MIN_RAM_GB),
            command=self._on_ram,
            progress_color=COLORS["accent"],
            button_color=COLORS["accent_hot"],
            button_hover_color=COLORS["accent"],
            fg_color=COLORS["panel"],
        )
        self.ram_slider.set(float(ram_now))
        self.ram_slider.grid(row=0, column=0, sticky="ew")
        self.ram_value = ctk.CTkLabel(
            ram_row,
            text=f"{ram_now} GB",
            width=58,
            font=FONTS["body_bold"],
            text_color=COLORS["accent"],
        )
        self.ram_value.grid(row=0, column=1, padx=(12, 0))
        total = total_ram_gb()
        ctk.CTkLabel(
            scroll,
            text=(
                f"Máximo {ram_max} GB — a RAM total deste PC."
                if total
                else f"Máximo {ram_max} GB (não deu pra ler a RAM total deste PC)."
            ),
            font=FONTS["tiny"],
            text_color=COLORS["muted"],
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(0, 14))

        def switch(label, attr, tip):
            row = ctk.CTkFrame(scroll, fg_color=COLORS["panel"], corner_radius=12)
            row.pack(fill="x", pady=5)
            var = ctk.BooleanVar(value=bool(getattr(self.cfg, attr, False)))

            def on_toggle():
                setattr(self.cfg, attr, bool(var.get()))
                self.cfg.save()

            sw = ctk.CTkSwitch(
                row,
                text=label,
                variable=var,
                command=on_toggle,
                font=FONTS["body_bold"],
                text_color=COLORS["text"],
                progress_color=COLORS["accent"],
                button_color=COLORS["cream"],
                fg_color=COLORS["stroke"],
            )
            sw.pack(anchor="w", padx=14, pady=(12, 2))
            ctk.CTkLabel(row, text=tip, font=FONTS["tiny"], text_color=COLORS["muted"], wraplength=380, justify="left").pack(
                anchor="w", padx=14, pady=(0, 12)
            )
            return var

        keep_row = ctk.CTkFrame(scroll, fg_color=COLORS["panel"], corner_radius=12)
        keep_row.pack(fill="x", pady=5)
        keep_var = ctk.BooleanVar(value=not bool(self.cfg.close_launcher_on_start))

        def on_keep():
            self.cfg.close_launcher_on_start = not bool(keep_var.get())
            self.cfg.save()

        ctk.CTkSwitch(
            keep_row,
            text="Manter launcher em segundo plano",
            variable=keep_var,
            command=on_keep,
            font=FONTS["body_bold"],
            text_color=COLORS["text"],
            progress_color=COLORS["accent"],
            button_color=COLORS["cream"],
            fg_color=COLORS["stroke"],
        ).pack(anchor="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            keep_row,
            text="Não fecha o launcher ao jogar — dá pra mudar skin enquanto o Minecraft tá aberto.",
            font=FONTS["tiny"],
            text_color=COLORS["muted"],
            wraplength=380,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 12))

        switch("Usar G1 garbage collector", "use_g1gc", "GC moderno — menos stutter com mods.")
        switch("Flags JVM modernas", "use_modern_jvm_flags", "AlwaysPreTouch e afins pra alocar RAM mais estável.")
        switch("Reservar metade da RAM no início", "allocate_min_half_ram", "Xms = metade do Xmx — evita hiccups ao crescer o heap.")
        switch("Tentar Vulkan (LWJGL)", "use_vulkan", "Pede Vulkan ao LWJGL. Ajuda com Sodium/Iris em PCs com driver bom.")
        switch("Tela cheia", "fullscreen", "Inicia em fullscreen quando o jogo suportar.")
        switch("Desativar VSync", "disable_vsync", "Escreve enableVsync no options.txt — útil se você limita FPS pelo mod/driver.")
        switch(
            "Deduplicar strings (JVM)",
            "use_string_dedup",
            "Pode reduzir uso de RAM com muitos mods (G1).",
        )

        ctk.CTkLabel(scroll, text="IP do servidor (opcional)", font=FONTS["body_bold"], text_color=COLORS["text"]).pack(
            anchor="w", pady=(12, 4)
        )
        srv_row = ctk.CTkFrame(scroll, fg_color="transparent")
        srv_row.pack(fill="x")
        ip_var = ctk.StringVar(value=self.cfg.server_ip or "")
        port_var = ctk.StringVar(value=str(self.cfg.server_port or 25565))
        ctk.CTkEntry(
            srv_row, textvariable=ip_var, placeholder_text="play.seuservidor.com",
            fg_color=COLORS["input_bg"], border_color=COLORS["input_border"],
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkEntry(
            srv_row, textvariable=port_var, width=80, placeholder_text="25565",
            fg_color=COLORS["input_bg"], border_color=COLORS["input_border"],
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            scroll,
            text=self._server_hint(),
            font=FONTS["tiny"],
            text_color=COLORS["muted"],
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(scroll, text="Resolução da janela", font=FONTS["body_bold"], text_color=COLORS["text"]).pack(
            anchor="w", pady=(12, 4)
        )
        res_row = ctk.CTkFrame(scroll, fg_color="transparent")
        res_row.pack(fill="x")
        w_var = ctk.StringVar(value=str(self.cfg.window_width or 854))
        h_var = ctk.StringVar(value=str(self.cfg.window_height or 480))
        ctk.CTkEntry(res_row, textvariable=w_var, width=90, fg_color=COLORS["input_bg"], border_color=COLORS["input_border"]).pack(
            side="left"
        )
        ctk.CTkLabel(res_row, text="×", text_color=COLORS["muted"]).pack(side="left", padx=8)
        ctk.CTkEntry(res_row, textvariable=h_var, width=90, fg_color=COLORS["input_bg"], border_color=COLORS["input_border"]).pack(
            side="left"
        )

        ctk.CTkLabel(scroll, text="Catálogo de modpacks (GitHub Releases)", font=FONTS["body_bold"], text_color=COLORS["text"]).pack(
            anchor="w", pady=(14, 4)
        )
        idx_var = ctk.StringVar(value=self.cfg.catalog_source())
        ctk.CTkEntry(
            scroll,
            textvariable=idx_var,
            placeholder_text="dono/repo  ou  URL do index.json no Release",
            fg_color=COLORS["input_bg"],
            border_color=COLORS["input_border"],
            text_color=COLORS["text"],
        ).pack(fill="x")
        ctk.CTkLabel(
            scroll,
            text="Ex.: koidestudos/PUTsModpacks — lista releases/zips, ou um index.json anexado ao Release.",
            font=FONTS["tiny"],
            text_color=COLORS["muted"],
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(scroll, text="Argumentos JVM extras", font=FONTS["body_bold"], text_color=COLORS["text"]).pack(
            anchor="w", pady=(14, 4)
        )
        jvm_box = ctk.CTkEntry(
            scroll,
            placeholder_text="-XX:…  (opcional)",
            fg_color=COLORS["input_bg"],
            border_color=COLORS["input_border"],
            text_color=COLORS["text"],
        )
        jvm_box.pack(fill="x")
        if self.cfg.extra_jvm_args:
            jvm_box.insert(0, self.cfg.extra_jvm_args)

        def save_and_close():
            try:
                self.cfg.window_width = max(640, int(w_var.get()))
                self.cfg.window_height = max(480, int(h_var.get()))
            except Exception:
                pass
            self.cfg.server_ip = ip_var.get().strip()
            try:
                self.cfg.server_port = int(port_var.get() or 25565)
            except Exception:
                self.cfg.server_port = 25565
            # Notice was shown in this window; the choice made here is final.
            from launcher.core.instances import clear_migration_server_backup

            clear_migration_server_backup(self.cfg)
            self.cfg.extra_jvm_args = jvm_box.get().strip()
            self.cfg.modpack_catalog = idx_var.get().strip()
            self.cfg.modpack_index_url = ""  # legacy cleared after migrate
            if getattr(self, "ram_slider", None) is not None:
                self.cfg.ram_gb = clamp_ram_gb(round(self.ram_slider.get()))
            self.cfg.save()
            win.destroy()
            self._options_win = None

        ctk.CTkButton(
            scroll,
            text="Salvar",
            height=44,
            corner_radius=12,
            font=FONTS["body_bold"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hot"],
            text_color=COLORS["accent_text"],
            command=save_and_close,
        ).pack(fill="x", pady=(20, 8))

        win.protocol("WM_DELETE_WINDOW", save_and_close)
        _fit_window(win, 460, 620, pad=120)

    # ------------------------------------------------------------------ auth
    def _show_device_code(self, user_code: str, verify_uri: str) -> None:
        def open_dialog():
            win = ctk.CTkToplevel(self)
            win.title("Login Microsoft")
            win.geometry("520x320")
            win.minsize(460, 300)
            win.configure(fg_color=COLORS["bg1"])
            win.transient(self)
            frame = ctk.CTkFrame(win, fg_color="transparent")
            frame.pack(fill="both", expand=True, padx=24, pady=24)
            ctk.CTkLabel(
                frame,
                text="Entre com sua conta Microsoft",
                font=FONTS["title"],
                text_color=COLORS["accent"],
                anchor="w",
                justify="left",
                wraplength=440,
            ).pack(fill="x")
            ctk.CTkLabel(
                frame,
                text=f"1. Abra {verify_uri}\n2. Digite o código abaixo\n3. Autorize e volte aqui",
                font=FONTS["small"],
                text_color=COLORS["muted"],
                justify="left",
                anchor="w",
                wraplength=440,
            ).pack(fill="x", pady=(10, 16))
            ctk.CTkLabel(frame, text=user_code, font=("Consolas", 36, "bold"), text_color=COLORS["accent"]).pack()
            ctk.CTkButton(
                frame,
                text="Abrir página de login",
                command=lambda: __import__("webbrowser").open(verify_uri),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_dim"],
                text_color=COLORS["accent_text"],
                height=40,
            ).pack(fill="x", pady=(20, 0))
            _fit_window(win, 520, 320)
            self._device_code_window = win

        self.after(0, open_dialog)

    def _close_device_code(self) -> None:
        win = self._device_code_window
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
                on_status=lambda _msg: None,
                on_device_code=self._show_device_code,
            )

        def done(session: Optional[GameSession], err):
            self._close_device_code()
            if err:
                messagebox.showerror("Microsoft", str(err))
                return
            self.cfg = LauncherConfig.load()
            self.auth_mode.set("microsoft")
            self._set_mode("microsoft")
            self._refresh_ms_profile()
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

    # ------------------------------------------------------------------ menus / actions
    def _toggle_action_menu(self) -> None:
        if self._menu_popup and self._menu_popup.winfo_exists():
            self._menu_popup.destroy()
            self._menu_popup = None
            return
        pop = ctk.CTkToplevel(self)
        pop.withdraw()
        pop.overrideredirect(True)
        pop.configure(fg_color=COLORS["panel"])
        self._menu_popup = pop
        frame = ctk.CTkFrame(pop, fg_color=COLORS["panel"], corner_radius=10)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        ctk.CTkButton(
            frame,
            text="Reinstalar",
            anchor="w",
            height=38,
            fg_color="transparent",
            hover_color=COLORS["stroke"],
            text_color=COLORS["text"],
            command=self._reinstall,
        ).pack(fill="x", padx=6, pady=4)
        ctk.CTkButton(
            frame,
            text="Desinstalar",
            anchor="w",
            height=38,
            fg_color="transparent",
            hover_color=COLORS["stroke"],
            text_color=COLORS["danger"],
            command=self._uninstall,
        ).pack(fill="x", padx=6, pady=(0, 6))
        self.update_idletasks()
        x = self.menu_btn.winfo_rootx() - 120
        y = self.menu_btn.winfo_rooty() - 90
        pop.geometry(f"170x95+{x}+{y}")
        pop.deiconify()
        pop.focus_force()
        pop.bind("<FocusOut>", lambda _e: pop.destroy())

    def _on_action(self) -> None:
        if is_game_ready() and not self._downloading:
            self._play()
        else:
            self._download()

    def _cancel_action(self) -> None:
        self._cancel.set()
        if self._game_proc and self._game_proc.poll() is None:
            try:
                self._game_proc.terminate()
            except Exception:
                pass
        self.detail_label.configure(text="Cancelando…")

    def _download(self, force_reinstall: bool = False) -> None:
        if self._busy:
            messagebox.showinfo("Baixar", "Já tem uma tarefa em andamento.")
            return
        self._save_form()
        self._cancel.clear()
        self._downloading = True
        self._show_progress(True)
        self.progress.set(0)
        self.percent_label.configure(text="0%")
        self.progress_title.configure(text="Preparando")
        self.detail_label.configure(text="Iniciando download…")
        self.eta_label.configure(text="Tempo restante: calculando…")

        def on_progress(state: ProgressState) -> None:
            if self._cancel.is_set():
                return
            self.after(0, lambda s=state: self._set_progress_ui(s))

        def job():
            tracker = ProgressTracker({"java": 0.25, "forge": 0.75}, on_update=on_progress)
            if force_reinstall:
                reinstall_game(self.cfg, tracker=tracker, cancel_event=self._cancel)
            else:
                prepare_game(self.cfg, tracker=tracker, cancel_event=self._cancel)
            if self._cancel.is_set():
                raise CancelledError("Download cancelado.")
            # Mods vêm do + Modpack (link ou GitHub), não da pasta do EXE
            return True

        def done(_ok, err):
            self._downloading = False
            if isinstance(err, CancelledError) or self._cancel.is_set():
                self.progress_title.configure(text="Cancelado")
                self.detail_label.configure(text="Download cancelado.")
                self.after(600, lambda: self._show_progress(False))
                self._refresh_ready_state()
                return
            if err:
                messagebox.showerror("Download", str(err))
                self.progress_title.configure(text="Erro")
                self.detail_label.configure(text=str(err))
                self._refresh_ready_state()
                return
            self.progress.set(1)
            self.percent_label.configure(text="100%")
            self.progress_title.configure(text="Download concluído")
            self.detail_label.configure(text="Pronto — clique em JOGAR")
            self.eta_label.configure(text="Tempo restante: 0s")
            self.after(700, lambda: self._show_progress(False))
            self._refresh_ready_state()

        self._run_bg(job, done, busy_text="BAIXANDO…")

    def _play(self) -> None:
        if self._busy:
            messagebox.showinfo("Jogar", "Já tem uma tarefa em andamento.")
            return
        self._save_form()
        self._cancel.clear()
        self._show_progress(True)
        self.progress_title.configure(text="Abrindo")
        self.detail_label.configure(text="Preparando jogo…")
        self.percent_label.configure(text="…")
        self.eta_label.configure(text="")

        def job():
            tracker = ProgressTracker(DEFAULT_PHASES)
            session = self._resolve_session()
            return prepare_and_launch(self.cfg, session, tracker=tracker, cancel_event=self._cancel)

        def done(proc, err):
            if isinstance(err, CancelledError) or self._cancel.is_set():
                if proc is not None and proc.poll() is None:
                    # Nasceu entre o clique em CANCELAR e este callback.
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                self._show_progress(False)
                return
            if err:
                messagebox.showerror("Jogar", str(err))
                self._show_progress(False)
                if not is_game_ready():
                    self._refresh_ready_state()
                return
            self._game_proc = proc
            self._show_progress(False)
            if self.cfg.close_launcher_on_start:
                self.after(700, self.destroy)

        self._run_bg(job, done, busy_text="ABRINDO…")

    def _reinstall(self) -> None:
        if self._menu_popup:
            self._menu_popup.destroy()
            self._menu_popup = None
        if not messagebox.askyesno(
            "Reinstalar",
            "Baixar de novo Minecraft/Forge desta instância?\n\n"
            "Mundos, mods, configurações e screenshots são preservados.",
        ):
            return
        self._download(force_reinstall=True)

    def _uninstall(self) -> None:
        if self._menu_popup:
            self._menu_popup.destroy()
            self._menu_popup = None
        from launcher.config import minecraft_dir

        mc = minecraft_dir()
        if not messagebox.askyesno(
            "Desinstalar",
            f"Apagar os arquivos da instância ativa?\n{mc}",
        ):
            return

        if self._busy:
            messagebox.showinfo("Desinstalar", "Já tem uma tarefa em andamento.")
            return

        def job():
            uninstall_game()
            return True

        def done(_ok, err):
            if err:
                messagebox.showerror("Desinstalar", str(err))
                return
            self._refresh_ready_state()

        self._run_bg(job, done, busy_text="…")


def run_app() -> None:
    app = PUTsLauncherApp()
    app.mainloop()
