from __future__ import annotations

import zipfile

import pytest

from launcher.auth.session import offline_session
from launcher.config import FORGE_PROFILE, MC_VERSION, mods_source_dir, puts_home
from launcher.core.mods import list_bundled_mods, sync_mods
from launcher.core.progress import ProgressTracker


def test_offline_session_stable_uuid():
    a = offline_session("Puts")
    b = offline_session("Puts")
    assert a.uuid == b.uuid
    assert a.username == "Puts"
    assert a.offline is True


def test_offline_session_trims_and_limits():
    s = offline_session("  VeryLongNicknameXYZ  ")
    assert len(s.username) <= 16


def test_launcher_defaults_without_bundled_mods():
    # mods/ is no longer shipped with the launcher download
    assert list_bundled_mods() == []
    assert mods_source_dir().name == "mods"
    assert MC_VERSION == "1.18.2"
    assert "forge" in FORGE_PROFILE
    assert puts_home().name == "MinecraftPUTS"


def test_sync_mods_from_github_instance(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.instances as instances
    import launcher.core.mods as mods_mod

    home = tmp_path / "MinecraftPUTS"
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(instances, "puts_home", lambda: home)
    instances._active_mc = None
    instances._active_id = ""

    inst = instances.create_instance("PUTs SMP", source="github", instance_id="puts-smp", modpack_id="puts-smp")
    instances.activate_instance(inst.id)
    monkeypatch.setattr(mods_mod, "minecraft_dir", instances.get_active_minecraft_dir)

    jar = inst.minecraft_path / "mods" / "demo.jar"
    jar.parent.mkdir(parents=True, exist_ok=True)
    jar.write_bytes(b"jar")

    # Avoid network: stub Ely.by injector
    monkeypatch.setattr(
        "launcher.core.skins_mod.ensure_elyby_skins_mod",
        lambda *a, **k: jar,
    )

    assert sync_mods() == 1


def test_sync_mods_requires_catalog_pack(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.instances as instances
    import launcher.core.mods as mods_mod

    home = tmp_path / "MinecraftPUTS"
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(instances, "puts_home", lambda: home)
    instances._active_mc = None
    instances._active_id = ""
    instances.ensure_default_instance()
    instances.activate_instance("default")
    monkeypatch.setattr(mods_mod, "minecraft_dir", instances.get_active_minecraft_dir)
    monkeypatch.setattr(mods_mod, "mods_source_dir", lambda: tmp_path / "no-mods-here")

    with pytest.raises(FileNotFoundError, match="Modpack"):
        sync_mods()


def test_is_game_ready_false_on_empty(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.installer as inst

    monkeypatch.setattr(config, "minecraft_dir", lambda: tmp_path / "mc")
    monkeypatch.setattr(inst, "minecraft_dir", lambda: tmp_path / "mc")
    assert inst.is_game_ready(tmp_path / "mc") is False


def test_progress_tracker_reaches_100():
    seen = []
    t = ProgressTracker({"a": 0.5, "b": 0.5}, on_update=lambda s: seen.append(s.percent))
    t.set_phase("a", "A")
    t.set_phase_fraction(1.0)
    t.complete_phase()
    t.set_phase("b", "B")
    t.set_phase_fraction(1.0)
    t.complete_phase()
    assert seen[-1] >= 99.0


def test_instances_create_and_activate(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.instances as instances

    home = tmp_path / "MinecraftPUTS"
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(instances, "puts_home", lambda: home)
    instances._active_mc = None
    instances._active_id = ""

    default = instances.ensure_default_instance()
    assert default.id == "default"
    assert (home / "instances" / "default" / "minecraft").is_dir()

    other = instances.create_instance("Meu Pack", source="local")
    assert other.id == "meu-pack"
    assert other.forge_profile == FORGE_PROFILE

    instances.activate_instance(other.id)
    assert instances.get_active_id() == "meu-pack"
    assert instances.get_active_minecraft_dir() == other.minecraft_path


def test_modpack_index_parse():
    from launcher.core.modpacks import ModpackInfo

    raw = {
        "modpacks": [
            {
                "id": "demo",
                "name": "Demo",
                "version": "1.0",
                "download_url": "https://example.com/demo.zip",
                "forge_version": "1.18.2-40.3.11",
            },
            {"name": "broken"},  # missing id/url → skipped
        ]
    }
    packs = [ModpackInfo.from_dict(x) for x in raw["modpacks"]]
    packs = [p for p in packs if p.id and p.download_url]
    assert len(packs) == 1
    assert packs[0].id == "demo"
    assert packs[0].loader_version == "1.18.2-40.3.11"


def test_install_modpack_zip(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.instances as instances
    from launcher.core.modpacks import install_modpack_zip

    home = tmp_path / "MinecraftPUTS"
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(instances, "puts_home", lambda: home)
    instances._active_mc = None
    instances._active_id = ""

    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    inst = instances.create_instance("Zip Pack", source="github", instance_id="zip-pack")
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("mods/example-mod.jar", b"fake-jar")
        zf.writestr("config/foo.toml", b"bar=1\n")
        zf.writestr("readme.txt", b"ignore me")

    install_modpack_zip(zpath, inst)
    assert (inst.minecraft_path / "mods" / "example-mod.jar").exists()
    assert (inst.minecraft_path / "config" / "foo.toml").exists()
    assert not (inst.minecraft_path / "readme.txt").exists()


def test_forge_profile_helper():
    from launcher.core.installer import forge_profile_id, normalize_forge_install_version
    from launcher.core.instances import _forge_profile

    assert normalize_forge_install_version("1.18.2", "1.18.2-40.3.11") == "1.18.2-40.3.11"
    assert normalize_forge_install_version("1.20.1", "47.4.10") == "1.20.1-47.4.10"
    assert normalize_forge_install_version("1.20.1", "1.20.1-forge-47.4.10") == "1.20.1-47.4.10"
    assert forge_profile_id("1.20.1", "1.20.1-47.4.10") == "1.20.1-forge-47.4.10"
    assert _forge_profile("1.18.2", "1.18.2-40.3.11") == "1.18.2-forge-40.3.11"
    assert _forge_profile("1.18.2", "1.18.2-forge-40.3.11") == "1.18.2-forge-40.3.11"
    assert _forge_profile("1.18.2", "40.3.11") == "1.18.2-forge-40.3.11"
    assert _forge_profile("1.20.1", "47.4.10") == "1.20.1-forge-47.4.10"


def test_parse_github_repo():
    from launcher.core.modpacks import parse_github_repo

    assert parse_github_repo("koidestudos/PUTsModpacks") == ("koidestudos", "PUTsModpacks")
    assert parse_github_repo("https://github.com/koidestudos/PUTsModpacks") == (
        "koidestudos",
        "PUTsModpacks",
    )
    assert parse_github_repo("https://github.com/koidestudos/PUTsModpacks.git") == (
        "koidestudos",
        "PUTsModpacks",
    )
    assert parse_github_repo("") is None


def test_packs_from_release_zips():
    from launcher.core.modpacks import _packs_from_release_zips

    releases = [
        {
            "tag_name": "v1.2.0",
            "name": "PUTs SMP",
            "body": "Pack oficial.\n\nDetalhes longos…",
            "assets": [
                {
                    "name": "puts-smp.zip",
                    "browser_download_url": "https://github.com/o/r/releases/download/v1.2.0/puts-smp.zip",
                }
            ],
        }
    ]
    packs = _packs_from_release_zips(releases)
    assert len(packs) == 1
    assert packs[0].version == "1.2.0"
    assert packs[0].download_url.endswith("puts-smp.zip")
    assert "Pack oficial" in packs[0].description


def test_packs_from_index_resolves_asset_names():
    from launcher.core.modpacks import _packs_from_index_payload

    data = {
        "modpacks": [
            {
                "id": "lite",
                "name": "Lite",
                "download_url": "lite.zip",
            }
        ]
    }
    packs = _packs_from_index_payload(
        data,
        asset_urls={"lite.zip": "https://example.com/lite.zip"},
    )
    assert packs[0].download_url == "https://example.com/lite.zip"


def test_already_exists_error_helper():
    import errno

    from launcher.core.installer import _is_already_exists_error

    e = OSError(errno.EEXIST, "exists")
    e.winerror = 183
    assert _is_already_exists_error(e)
    assert _is_already_exists_error(FileExistsError("x"))
    assert not _is_already_exists_error(ValueError("nope"))


def test_clean_version_natives(tmp_path, monkeypatch):
    import launcher.core.installer as inst
    from launcher.config import FORGE_PROFILE
    from launcher.core.installer import clean_version_natives

    mc = tmp_path / "mc"
    natives = mc / "versions" / FORGE_PROFILE / "natives" / "META-INF"
    natives.mkdir(parents=True)
    (natives / "versions").write_text("conflict", encoding="utf-8")
    monkeypatch.setattr(inst, "minecraft_dir", lambda: mc)
    clean_version_natives(mc)
    assert not (mc / "versions" / FORGE_PROFILE / "natives").exists()


def test_resolve_skins_mod_versions():
    from launcher.core.skins_mod import resolve_skins_mod

    a = resolve_skins_mod("1.18.2")
    assert "ForgeV2" in a.filename
    b = resolve_skins_mod("1.20.1")
    assert "ForgeV2" in b.filename
    c = resolve_skins_mod("1.21.1")
    assert "Universal" in c.filename


def test_elyby_config_priority():
    from launcher.core.skins_mod import elyby_priority_config

    cfg = elyby_priority_config()
    types = [x["type"] for x in cfg["loadlist"]]
    assert "ElyByAPI" in types
    # ElyBy before Mojang so offline nicks resolve on ely.by
    assert types.index("ElyByAPI") < types.index("MojangAPI")


def test_ensure_elyby_skins_mod_copies_jar(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.skins_mod as skins

    home = tmp_path / "MinecraftPUTS"
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(skins, "cache_dir", lambda: home / "cache")

    fake = tmp_path / "fake-csl.jar"
    fake.write_bytes(b"x" * 20_000)

    def fake_cached(artifact):
        dest = home / "cache" / "skins_mod" / artifact.filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fake.read_bytes())
        return dest

    monkeypatch.setattr(skins, "cached_skins_mod_jar", fake_cached)

    mc = tmp_path / "instance" / "minecraft"
    (mc / "mods").mkdir(parents=True)
    out = skins.ensure_elyby_skins_mod(mc, mc_version="1.20.1")
    assert out.exists()
    assert out.name.startswith("CustomSkinLoader_ForgeV2")
    cfg = mc / "CustomSkinLoader" / "CustomSkinLoader.json"
    assert cfg.exists()
    import json

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert any(x.get("type") == "ElyByAPI" for x in data["loadlist"])


# --------------------------------------------------------------------- regressions


def _isolate_home(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.instances as instances

    home = tmp_path / "MinecraftPUTS"
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(instances, "puts_home", lambda: home)
    instances._active_mc = None
    instances._active_id = ""
    return home


def test_modpack_id_with_traversal_is_rejected(tmp_path, monkeypatch):
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")

    pack = ModpackInfo(
        id="../../../../../../tmp/pwned",
        name="Evil",
        version="../../evil",
        download_url="https://example.invalid/evil.zip",
    )

    def boom(*a, **k):
        raise AssertionError("download must not start for an unsafe pack id")

    monkeypatch.setattr(modpacks, "download_file", boom)

    with pytest.raises(ValueError, match="inválido"):
        install_modpack(pack)


def test_catalog_skips_unsafe_ids():
    from launcher.core.modpacks import _packs_from_index_payload

    data = {
        "modpacks": [
            {"id": "../escape", "name": "Evil", "download_url": "https://example.invalid/e.zip"},
            {"id": "ok-pack", "name": "Ok", "download_url": "https://example.invalid/ok.zip"},
        ]
    }
    packs = _packs_from_index_payload(data)
    assert [p.id for p in packs] == ["ok-pack"]


def test_instance_path_rejects_traversal(tmp_path, monkeypatch):
    import launcher.core.instances as instances

    _isolate_home(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError):
        instances.instance_path("../outside")
    instances.delete_instance("../outside")
    assert (outside / "keep.txt").exists()


def test_reinstall_keeps_player_data(tmp_path, monkeypatch):
    import launcher.core.installer as installer

    mc = tmp_path / "mc"
    for rel in ("saves/world/level.dat", "screenshots/a.png", "mods/x.jar", "config/y.toml", "options.txt"):
        p = mc / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("data", encoding="utf-8")
    for rel in ("versions/1.18.2/1.18.2.json", "libraries/net/foo.jar", "assets/indexes/1.18.json"):
        p = mc / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("artifact", encoding="utf-8")

    monkeypatch.setattr(installer, "minecraft_dir", lambda: mc)
    monkeypatch.setattr(installer, "prepare_game", lambda *a, **k: "java")
    installer.clear_game_artifacts()

    assert (mc / "saves" / "world" / "level.dat").exists()
    assert (mc / "screenshots" / "a.png").exists()
    assert (mc / "mods" / "x.jar").exists()
    assert (mc / "config" / "y.toml").exists()
    assert (mc / "options.txt").exists()
    assert not (mc / "versions").exists()
    assert not (mc / "libraries").exists()
    assert not (mc / "assets").exists()


def test_instance_without_server_does_not_inherit_previous(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.config import LauncherConfig

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(LauncherConfig, "save", lambda self: None)

    with_server = instances.create_instance(
        "Pack SMP", instance_id="pack-smp", server_ip="smp.exemplo.com", server_port=25566
    )
    plain = instances.create_instance("Local", instance_id="local")

    cfg = LauncherConfig()
    instances.apply_instance_to_config(cfg, with_server)
    assert cfg.server_ip == ""  # endereço do pack não vaza para a config global
    instances.apply_instance_to_config(cfg, plain)

    assert instances.effective_server(cfg, plain) == ("", 25565)
    assert instances.effective_server(cfg, with_server) == ("smp.exemplo.com", 25566)


def test_user_global_server_is_preserved(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.config import LauncherConfig

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(LauncherConfig, "save", lambda self: None)

    plain = instances.create_instance("Local", instance_id="local")
    cfg = LauncherConfig(server_ip="meu.servidor.com", server_port=25577)
    instances.apply_instance_to_config(cfg, plain)

    assert instances.effective_server(cfg, plain) == ("meu.servidor.com", 25577)


def test_legacy_pack_server_migrates_once(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.config import LauncherConfig

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(LauncherConfig, "save", lambda self: None)
    instances.create_instance(
        "Pack SMP", instance_id="pack-smp", server_ip="smp.exemplo.com", server_port=25566
    )

    cfg = LauncherConfig(server_ip="smp.exemplo.com", server_port=25566)
    assert instances.migrate_inherited_server(cfg) is True
    assert cfg.server_ip == ""

    # depois da migração o valor global é do usuário e nunca mais é mexido
    cfg.server_ip = "smp.exemplo.com"
    cfg.server_port = 25566
    assert instances.migrate_inherited_server(cfg) is False
    assert cfg.server_ip == "smp.exemplo.com"


def test_migration_keeps_user_server_on_other_port(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.config import LauncherConfig

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(LauncherConfig, "save", lambda self: None)
    instances.create_instance(
        "Pack SMP", instance_id="pack-smp", server_ip="smp.exemplo.com", server_port=25566
    )

    cfg = LauncherConfig(server_ip="smp.exemplo.com", server_port=25599)
    instances.migrate_inherited_server(cfg)
    assert (cfg.server_ip, cfg.server_port) == ("smp.exemplo.com", 25599)


def test_reinstalling_pack_reuses_the_same_instance(tmp_path, monkeypatch):
    import zipfile as _zipfile

    import launcher.core.instances as instances
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")

    src = tmp_path / "pack.zip"
    with _zipfile.ZipFile(src, "w") as zf:
        zf.writestr("mods/demo.jar", b"jar")

    def fake_download(url, dest, tracker=None, timeout=120, cancel_event=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return dest

    monkeypatch.setattr(modpacks, "download_file", fake_download)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    pack = ModpackInfo(id="Demo.Pack", name="Demo", version="1.0.0",
                       download_url="https://example.invalid/demo.zip")
    first = install_modpack(pack)
    pack.version = "1.1.0"
    second = install_modpack(pack)

    assert first.id == second.id == "demo-pack"
    assert [p.name for p in (home / "instances").iterdir() if p.is_dir()] .count("demo-pack-2") == 0
    assert instances.GameInstance.load("demo-pack").modpack_version == "1.1.0"


def test_fullscreen_flag_goes_to_command(monkeypatch):
    import launcher.core.launch as launch
    from launcher.auth.session import offline_session
    from launcher.config import LauncherConfig

    captured = {}

    def fake_command(profile, mc_dir, options):
        captured["options"] = options
        return ["java", "-cp", "x", "net.minecraft.client.main.Main", "--username", "Puts"]

    monkeypatch.setattr(launch.mll.command, "get_minecraft_command", fake_command)
    monkeypatch.setattr(launch, "minecraft_dir", lambda: "/tmp/mc")

    cfg = LauncherConfig(fullscreen=True, window_width=1280, window_height=720)
    cmd = launch.build_launch_command(cfg, offline_session("Puts"), "/usr/bin/java")
    assert "--fullscreen" in cmd
    assert "customResolution" not in captured["options"]

    cfg2 = LauncherConfig(fullscreen=False, window_width=1280, window_height=720)
    cmd2 = launch.build_launch_command(cfg2, offline_session("Puts"), "/usr/bin/java")
    assert "--fullscreen" not in cmd2
    assert captured["options"]["resolutionWidth"] == "1280"


# --------------------------------------------------------------------- skins em memória


def _png_bytes(size, color=(10, 20, 30, 255)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_skin_download_stays_in_memory(tmp_path, monkeypatch):
    import launcher.auth.skin as skin
    import launcher.config as config

    home = tmp_path / "MinecraftPUTS"
    (home / "cache").mkdir(parents=True)
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(skin, "cache_dir", lambda: home / "cache")
    skin.clear_skin_cache()

    calls = []

    def fake_download(url, limit):
        calls.append(url)
        return _png_bytes((64, 64))

    monkeypatch.setattr(skin, "_download", fake_download)

    first = skin.fetch_skin_texture(name="Puts")
    assert first is not None and first.size == (64, 64)
    # nada foi escrito na pasta do usuário
    assert list((home / "cache").iterdir()) == []

    # segunda busca do mesmo nick não baixa de novo
    again = skin.fetch_skin_texture(name="Puts")
    assert again is first
    assert len(calls) == 1


def test_skin_bigger_than_minecraft_is_refused(tmp_path, monkeypatch):
    import launcher.auth.skin as skin
    import launcher.config as config

    home = tmp_path / "MinecraftPUTS"
    (home / "cache").mkdir(parents=True)
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(skin, "cache_dir", lambda: home / "cache")
    skin.clear_skin_cache()

    with pytest.raises(ValueError, match="fora do padrão"):
        skin.decode_skin_bytes(_png_bytes((512, 512)))

    monkeypatch.setattr(skin, "_download", lambda url, limit: _png_bytes((256, 256)))
    assert skin.fetch_skin_texture(name="Gigante") is None
    assert list((home / "cache").iterdir()) == []


def test_skin_download_respects_byte_limit(monkeypatch):
    import launcher.auth.skin as skin

    skin.clear_skin_cache()
    huge = b"\x89PNG\r\n\x1a\n" + b"0" * (skin.MAX_SKIN_BYTES + 10)
    with pytest.raises(ValueError, match="limite"):
        skin.decode_skin_bytes(huge)


def test_legacy_skin_files_are_purged(tmp_path, monkeypatch):
    import launcher.auth.skin as skin
    import launcher.config as config

    home = tmp_path / "MinecraftPUTS"
    cache = home / "cache"
    cache.mkdir(parents=True)
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(skin, "cache_dir", lambda: cache)

    (cache / "texture_Puts.png").write_bytes(b"x")
    (cache / "head_Puts.png").write_bytes(b"x")
    keep = cache / "modpacks"
    keep.mkdir()

    assert skin.purge_legacy_skin_files() == 2
    assert list(cache.iterdir()) == [keep]


def test_local_skin_validation(tmp_path):
    from launcher.auth.skin import load_local_skin

    ok = tmp_path / "ok.png"
    ok.write_bytes(_png_bytes((64, 64)))
    assert load_local_skin(ok).size == (64, 64)

    legacy = tmp_path / "legacy.png"
    legacy.write_bytes(_png_bytes((64, 32)))
    assert load_local_skin(legacy).size == (64, 32)

    big = tmp_path / "big.png"
    big.write_bytes(_png_bytes((128, 128)))
    with pytest.raises(ValueError):
        load_local_skin(big)


def test_skin_cache_is_per_player_and_bustable(monkeypatch):
    import launcher.auth.skin as skin

    skin.clear_skin_cache()
    served = {"n": 0}

    def fake_download(url, limit):
        served["n"] += 1
        shade = (served["n"] * 7) % 255
        return _png_bytes((64, 64), (shade, shade, shade, 255))

    monkeypatch.setattr(skin, "_download", fake_download)

    notch = skin.fetch_skin_texture(name="Notch")
    steve = skin.fetch_skin_texture(name="Steve")
    assert notch is not None and steve is not None
    assert notch is not steve
    assert served["n"] == 2

    # trocar de conta (uuid) não reaproveita a textura do nick
    by_uuid = skin.fetch_skin_texture(uuid="0123456789abcdef0123456789abcdef", name="Notch")
    assert by_uuid is not notch
    assert served["n"] == 3

    # bust força novo download do mesmo jogador
    again = skin.fetch_skin_texture(name="Notch", bust=True)
    assert again is not notch
    assert served["n"] == 4


def test_skin_miss_is_cached_briefly(monkeypatch):
    import launcher.auth.skin as skin

    skin.clear_skin_cache()
    calls = []

    def boom(url, limit):
        calls.append(url)
        raise OSError("404")

    monkeypatch.setattr(skin, "_download", boom)
    assert skin.fetch_skin_texture(name="NickInexistente") is None
    tries = len(calls)
    assert skin.fetch_skin_texture(name="NickInexistente") is None
    assert len(calls) == tries  # nick sem skin não é rebaixado a cada tecla


# --------------------------------------------------------------------- achados do Codex


def _install_two_packs(tmp_path, monkeypatch, id_a, id_b):
    import zipfile as _zipfile

    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    def make_zip(jar_name):
        path = tmp_path / f"{jar_name}.zip"
        with _zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"mods/{jar_name}.jar", b"jar")
        return path

    def download_for(jar_name):
        src = make_zip(jar_name)

        def fake(url, dest, tracker=None, timeout=120, cancel_event=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            return dest

        return fake

    monkeypatch.setattr(modpacks, "download_file", download_for("alpha"))
    first = install_modpack(ModpackInfo(id=id_a, name="Alpha", download_url="https://x.invalid/a.zip"))
    monkeypatch.setattr(modpacks, "download_file", download_for("beta"))
    second = install_modpack(ModpackInfo(id=id_b, name="Beta", download_url="https://x.invalid/b.zip"))
    return home, first, second


def test_colliding_catalog_ids_get_separate_instances(tmp_path, monkeypatch):
    home, first, second = _install_two_packs(tmp_path, monkeypatch, "Demo.Pack", "demo-pack")

    assert first.id != second.id
    assert (first.minecraft_path / "mods" / "alpha.jar").exists()
    assert (second.minecraft_path / "mods" / "beta.jar").exists()


def test_ids_differing_after_48_chars_get_separate_instances(tmp_path, monkeypatch):
    prefix = "p" * 48
    home, first, second = _install_two_packs(tmp_path, monkeypatch, prefix + "-um", prefix + "-dois")

    assert first.id != second.id
    assert (first.minecraft_path / "mods" / "alpha.jar").exists()
    assert (second.minecraft_path / "mods" / "beta.jar").exists()


def test_migration_keeps_a_recoverable_backup(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.config import LauncherConfig

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(LauncherConfig, "save", lambda self: None)
    instances.create_instance("Pack SMP", instance_id="pack-smp", server_ip="smp.exemplo.com", server_port=25566)

    cfg = LauncherConfig(server_ip="smp.exemplo.com", server_port=25566)
    instances.migrate_inherited_server(cfg)

    assert cfg.server_ip == ""
    assert instances.migration_server_backup(cfg) == ("smp.exemplo.com", 25566)

    instances.clear_migration_server_backup(cfg)
    assert instances.migration_server_backup(cfg) is None


def test_http_pack_download_requires_digest(tmp_path, monkeypatch):
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")

    def boom(*a, **k):
        raise AssertionError("não pode baixar zip por http sem digest")

    monkeypatch.setattr(modpacks, "download_file", boom)

    plain = ModpackInfo(id="pack", name="Pack", download_url="http://exemplo.invalido/pack.zip")
    with pytest.raises(ValueError, match="inseguro"):
        install_modpack(plain)


# --------------------------------------------------------------------- itens novos


def _pack_zip(path, files):
    import zipfile as _zipfile

    with _zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def test_verify_modpack_restores_only_pack_files(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack_zip, verify_modpack_files

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    zpath = _pack_zip(
        tmp_path / "pack.zip",
        {"mods/a.jar": b"jar-a", "mods/b.jar": b"jar-b", "config/x.toml": b"k=1"},
    )
    inst = instances.create_instance("Pack", instance_id="pack", source="github")
    install_modpack_zip(zpath, inst)

    mc = inst.minecraft_path
    (mc / "mods" / "a.jar").write_bytes(b"corrompido")   # arquivo do pack alterado
    (mc / "mods" / "b.jar").unlink()                      # arquivo do pack sumiu
    (mc / "mods" / "meu-mod.jar").write_bytes(b"do jogador")
    (mc / "saves").mkdir()
    (mc / "saves" / "level.dat").write_bytes(b"mundo")

    pack = ModpackInfo(id="pack", name="Pack", download_url="https://x.invalid/p.zip")
    monkeypatch.setattr(modpacks, "ensure_pack_zip", lambda *a, **k: zpath)

    report = verify_modpack_files(pack, inst)
    assert report["ok"] is False
    assert report["changed"] == ["mods/a.jar"]
    assert report["missing"] == ["mods/b.jar"]
    assert sorted(report["repaired"]) == ["mods/a.jar", "mods/b.jar"]
    assert (mc / "mods" / "a.jar").read_bytes() == b"jar-a"
    assert (mc / "mods" / "b.jar").read_bytes() == b"jar-b"
    # nada do jogador foi tocado
    assert (mc / "mods" / "meu-mod.jar").read_bytes() == b"do jogador"
    assert (mc / "saves" / "level.dat").read_bytes() == b"mundo"

    again = verify_modpack_files(pack, inst)
    assert again["ok"] is True and again["repaired"] == []


def test_pack_zip_with_root_folder_and_junk(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.core.modpacks import install_modpack_zip

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    zpath = _pack_zip(
        tmp_path / "pack.zip",
        {
            "MeuPack/mods/a.jar": b"jar",
            "MeuPack/config/x.toml": b"k=1",
            "__MACOSX/._a.jar": b"junk",
            "LEIAME.txt": b"junk",
        },
    )
    inst = instances.create_instance("Pack", instance_id="pack", source="github")
    install_modpack_zip(zpath, inst)

    assert (inst.minecraft_path / "mods" / "a.jar").exists()
    assert (inst.minecraft_path / "config" / "x.toml").exists()
    assert not (inst.minecraft_path / "LEIAME.txt").exists()


def test_install_can_be_cancelled(tmp_path, monkeypatch):
    import threading

    import launcher.core.modpacks as modpacks
    from launcher.core.installer import CancelledError
    from launcher.core.modpacks import ModpackInfo, install_modpack

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")

    cancel = threading.Event()
    cancel.set()

    def never(*a, **k):
        raise AssertionError("cancelado antes de baixar")

    monkeypatch.setattr(modpacks, "download_file", never)
    with pytest.raises(CancelledError):
        install_modpack(
            ModpackInfo(id="pack", name="Pack", download_url="https://x.invalid/p.zip"),
            cancel_event=cancel,
        )


def test_download_cancel_removes_partial_file(tmp_path, monkeypatch):
    import threading

    import launcher.core.modpacks as modpacks
    from launcher.core.installer import CancelledError

    cancel = threading.Event()

    class FakeResp:
        headers = {"Content-Length": "1000"}

        def read(self, _n):
            cancel.set()  # cancela no meio do download
            return b"x" * 100

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(modpacks, "urlopen", lambda *a, **k: FakeResp())
    dest = tmp_path / "cache" / "pack.zip"
    with pytest.raises(CancelledError):
        modpacks.download_file("https://x.invalid/p.zip", dest, cancel_event=cancel)
    assert not dest.exists()


def test_ram_limit_follows_machine(monkeypatch):
    import launcher.core.system as system

    monkeypatch.setattr(system, "total_ram_gb", lambda: 8)
    assert system.max_ram_gb() == 8
    assert system.clamp_ram_gb(16) == 8
    assert system.clamp_ram_gb(4) == 4
    assert system.clamp_ram_gb(1) == system.MIN_RAM_GB
    assert system.clamp_ram_gb("lixo") == 4

    monkeypatch.setattr(system, "total_ram_gb", lambda: 0)
    assert system.max_ram_gb() == 8  # máquina desconhecida → teto conservador


def test_jvm_args_never_exceed_machine_ram(monkeypatch):
    import launcher.core.launch as launch
    import launcher.core.system as system
    from launcher.config import LauncherConfig

    monkeypatch.setattr(system, "total_ram_gb", lambda: 8)
    args = launch._jvm_arguments(LauncherConfig(ram_gb=64))
    assert "-Xmx8G" in args


def test_brand_font_is_not_used_for_accented_titles():
    from launcher.config import asset_path
    from launcher.ui.theme import font_covers

    brand = asset_path("MerchantCopy.ttf")
    assert brand.exists()
    # A fonte da marca não tem ç/õ — por isso os títulos não podem usá-la.
    assert font_covers(brand) is False


def test_http_catalog_is_refused(monkeypatch):
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import fetch_modpack_index, is_trusted_url

    assert is_trusted_url("https://exemplo.com/index.json") is True
    assert is_trusted_url("http://127.0.0.1:8765/index.json") is True
    assert is_trusted_url("http://exemplo.com/index.json") is False

    def boom(*a, **k):
        raise AssertionError("não pode buscar catálogo por http")

    monkeypatch.setattr(modpacks, "_http_get", boom)
    with pytest.raises(ValueError, match="http"):
        fetch_modpack_index("http://exemplo.com/index.json")


def test_direct_index_resolves_relative_asset(monkeypatch):
    import json as _json

    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import fetch_modpack_index

    payload = {"modpacks": [{"id": "lite", "name": "Lite", "download_url": "lite.zip"}]}
    monkeypatch.setattr(
        modpacks, "_http_get", lambda *a, **k: _json.dumps(payload).encode("utf-8")
    )
    packs = fetch_modpack_index("https://exemplo.com/releases/download/v1/index.json")
    assert packs[0].download_url == "https://exemplo.com/releases/download/v1/lite.zip"


def test_remote_pack_never_hijacks_a_local_instance(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.core.modpacks import ModpackInfo, installed_instance_for

    _isolate_home(tmp_path, monkeypatch)
    instances.ensure_default_instance()

    pack = ModpackInfo(id="default", name="Pack malicioso", download_url="https://x.invalid/p.zip")
    assert installed_instance_for(pack) is None

    mine = instances.create_instance("Meu", instance_id="meu", modpack_id="default")
    assert installed_instance_for(pack).id == mine.id


def test_second_colliding_pack_updates_its_own_instance(tmp_path, monkeypatch):
    home, first, second = _install_two_packs(tmp_path, monkeypatch, "Demo.Pack", "demo-pack")

    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack

    src = tmp_path / "again.zip"
    _pack_zip(src, {"mods/beta.jar": b"jar-beta-v2"})

    def fake(url, dest, tracker=None, timeout=120, cancel_event=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return dest

    monkeypatch.setattr(modpacks, "download_file", fake)
    again = install_modpack(
        ModpackInfo(id="demo-pack", name="Beta", version="2.0.0", download_url="https://x.invalid/b.zip")
    )
    assert again.id == second.id
    assert not (home / "instances" / "demo-pack-3").exists()


def test_failed_extraction_keeps_previous_mods(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.core.modpacks import install_modpack_zip

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    inst = instances.create_instance("Pack", instance_id="pack", source="github")
    (inst.minecraft_path / "mods").mkdir(parents=True, exist_ok=True)
    (inst.minecraft_path / "mods" / "antigo.jar").write_bytes(b"mod antigo")

    zpath = _pack_zip(tmp_path / "pack.zip", {"mods/novo.jar": b"novo", "mods/outro.jar": b"outro"})

    import shutil as _shutil

    real_copy = _shutil.copyfileobj
    state = {"n": 0}

    def flaky(src, dst, *a, **k):
        state["n"] += 1
        if state["n"] == 2:
            raise OSError("disco cheio")
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr("launcher.core.modpacks.shutil.copyfileobj", flaky)
    with pytest.raises(OSError):
        install_modpack_zip(zpath, inst)

    # o pack antigo continua utilizável e nada de lixo ficou pra trás
    assert (inst.minecraft_path / "mods" / "antigo.jar").read_bytes() == b"mod antigo"
    assert not (inst.minecraft_path / ".puts-staging").exists()


def test_mods_symlink_is_refused(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.core.modpacks import install_modpack_zip

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    inst = instances.create_instance("Pack", instance_id="pack", source="github")
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "importante.jar").write_bytes(b"nao pode sumir")
    mods = inst.minecraft_path / "mods"
    mods.rmdir()
    mods.symlink_to(outside, target_is_directory=True)

    zpath = _pack_zip(tmp_path / "pack.zip", {"mods/novo.jar": b"novo"})
    with pytest.raises(ValueError, match="symlink"):
        install_modpack_zip(zpath, inst)
    assert (outside / "importante.jar").exists()


def test_empty_pack_zip_does_not_wipe_mods(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.core.modpacks import install_modpack_zip

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    inst = instances.create_instance("Pack", instance_id="pack", source="github")
    (inst.minecraft_path / "mods").mkdir(parents=True, exist_ok=True)
    (inst.minecraft_path / "mods" / "antigo.jar").write_bytes(b"mod antigo")

    vazio = _pack_zip(tmp_path / "vazio.zip", {"LEIAME.txt": b"nada util"})
    with pytest.raises(ValueError, match="mods/"):
        install_modpack_zip(vazio, inst)
    assert (inst.minecraft_path / "mods" / "antigo.jar").read_bytes() == b"mod antigo"


def test_failed_update_keeps_previous_metadata(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    inst = instances.create_instance(
        "Pack", instance_id="pack", source="github", modpack_id="pack", modpack_version="1.0.0"
    )
    (inst.minecraft_path / "mods" / "antigo.jar").write_bytes(b"mod antigo")

    ruim = _pack_zip(tmp_path / "ruim.zip", {"LEIAME.txt": b"sem mods"})
    monkeypatch.setattr(modpacks, "ensure_pack_zip", lambda *a, **k: ruim)

    with pytest.raises(ValueError):
        install_modpack(ModpackInfo(id="pack", name="Pack", version="2.0.0",
                                    download_url="https://x.invalid/p.zip"))

    reloaded = instances.GameInstance.load("pack")
    assert reloaded.modpack_version == "1.0.0"   # não finge que atualizou
    assert (inst.minecraft_path / "mods" / "antigo.jar").exists()


def test_failed_first_install_leaves_no_instance(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")
    ruim = _pack_zip(tmp_path / "ruim.zip", {"LEIAME.txt": b"sem mods"})
    monkeypatch.setattr(modpacks, "ensure_pack_zip", lambda *a, **k: ruim)

    with pytest.raises(ValueError):
        install_modpack(ModpackInfo(id="novo", name="Novo", download_url="https://x.invalid/p.zip"))

    assert instances.GameInstance.load("novo") is None
    assert not (home / "instances" / "novo").exists()


def test_same_id_from_another_catalog_is_a_separate_install(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.core.modpacks import ModpackInfo, installed_instance_for

    _isolate_home(tmp_path, monkeypatch)
    inst = instances.create_instance("Pack", instance_id="pack", modpack_id="pack")
    inst.extra["catalog_origin"] = "dono/repo-a"
    inst.save_meta()

    pack = ModpackInfo(id="pack", name="Pack", download_url="https://x.invalid/p.zip")
    assert installed_instance_for(pack, "dono/repo-a").id == "pack"
    assert installed_instance_for(pack, "outro/repo-b") is None
    assert installed_instance_for(pack).id == "pack"  # sem origem conhecida, mantém o casamento


def test_legacy_skin_gets_both_limbs():
    from PIL import Image

    from launcher.ui.skin3d import _normalize_skin

    legacy = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    for x in range(0, 16):       # perna direita
        for y in range(16, 32):
            legacy.putpixel((x, y), (200, 30, 30, 255))
    for x in range(40, 56):      # braço direito
        for y in range(16, 32):
            legacy.putpixel((x, y), (30, 30, 200, 255))

    out = _normalize_skin(legacy)
    assert out.size == (64, 64)
    assert out.getpixel((20, 55))[3] == 255   # perna esquerda preenchida
    assert out.getpixel((36, 55))[3] == 255   # braço esquerdo preenchido


def test_non_forge_loader_is_refused(tmp_path, monkeypatch):
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, install_modpack

    home = _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr(modpacks, "cache_dir", lambda: home / "cache")
    monkeypatch.setattr(modpacks, "download_file", lambda *a, **k: pytest.fail("não pode baixar"))

    weird = ModpackInfo(
        id="rift",
        name="Rift Pack",
        loader="rift",
        download_url="https://x.invalid/f.zip",
    )
    with pytest.raises(ValueError, match="loader|Loader|rift"):
        install_modpack(weird)
    assert not (home / "instances" / "rift").exists()


def test_fabric_loader_is_accepted_by_catalog_gate(tmp_path, monkeypatch):
    import launcher.core.modpacks as modpacks
    from launcher.core.modpacks import ModpackInfo, _require_supported_loader

    _require_supported_loader(ModpackInfo(id="fab", name="Fab", loader="fabric"))
    _require_supported_loader(ModpackInfo(id="neo", name="Neo", loader="neoforge"))
    with pytest.raises(ValueError):
        _require_supported_loader(ModpackInfo(id="x", name="X", loader="rift"))


def test_cancel_stops_java_download_midway():
    import threading

    from launcher.core.installer import CancelledError
    from launcher.core.progress import ProgressTracker

    cancel = threading.Event()
    tracker = ProgressTracker({"java": 1.0})
    cb = tracker.as_mll_callback("java", cancel_event=cancel)
    cb["setMax"](100)
    cb["setProgress"](10)          # antes do cancelamento, segue normal
    cancel.set()
    with pytest.raises(CancelledError):
        cb["setProgress"](20)      # o download para no meio
    with pytest.raises(CancelledError):
        cb["setStatus"]("baixando…")


def test_offline_uuid_matches_minecraft():
    from launcher.auth.session import offline_session, offline_uuid

    # Valor canônico do Minecraft para UUID.nameUUIDFromBytes("OfflinePlayer:Notch")
    assert str(offline_uuid("Notch")) == "b50ad385-829d-3141-a216-7e7d7539ba7f"
    assert str(offline_uuid("jeb_")) == "a762f560-4fce-3236-812a-b80efff0b62b"
    assert offline_session("Notch").uuid == "b50ad385829d3141a2167e7d7539ba7f"
    assert offline_uuid("Notch").version == 3


def test_second_skin_layer_is_rendered():
    import numpy as np
    from PIL import Image

    from launcher.ui.skin3d import _prep_faces

    # skin em que a camada base é vazia e tudo está na segunda camada
    tex = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(20, 28):      # jaqueta (corpo, 2ª camada)
        for y in range(52, 64):
            tex.putpixel((x, y), (10, 200, 10, 255))
    arr = np.asarray(tex, dtype=np.uint8)

    faces = _prep_faces(arr)
    assert faces, "a segunda camada precisa gerar geometria mesmo com a base vazia"

    # nenhuma face totalmente transparente é preparada
    for _verts, face, _shade, _uv in faces:
        assert bool((face[..., 3] > 8).any())


def test_transparent_faces_are_dropped():
    import numpy as np
    from PIL import Image

    from launcher.ui.skin3d import _prep_faces

    vazio = np.asarray(Image.new("RGBA", (64, 64), (0, 0, 0, 0)), dtype=np.uint8)
    assert _prep_faces(vazio) == []

    cheio = np.asarray(Image.new("RGBA", (64, 64), (200, 30, 30, 255)), dtype=np.uint8)
    # 12 caixas × 6 faces com a textura inteira opaca
    assert len(_prep_faces(cheio)) == 72


def test_video_options_sync_fullscreen_and_vsync(tmp_path):
    from launcher.core.launch import apply_video_options

    mc = tmp_path / "mc"
    mc.mkdir()
    opts = mc / "options.txt"
    opts.write_text("version:2860\nfullscreen:false\nenableVsync:true\nfov:0.5\n", encoding="utf-8")

    apply_video_options(mc, fullscreen=True, vsync=False)
    linhas = opts.read_text(encoding="utf-8").splitlines()
    assert "fullscreen:true" in linhas
    assert "enableVsync:false" in linhas
    assert "fov:0.5" in linhas          # o resto do arquivo fica intacto
    assert len(linhas) == 4             # sem duplicar chaves

    # chave ausente é acrescentada
    opts.write_text("fov:0.5\n", encoding="utf-8")
    apply_video_options(mc, fullscreen=False, vsync=True)
    linhas = opts.read_text(encoding="utf-8").splitlines()
    assert linhas == ["fov:0.5", "fullscreen:false", "enableVsync:true"]

    # sem options.txt o launcher não cria um do nada
    (mc / "options.txt").unlink()
    apply_video_options(mc, fullscreen=True, vsync=True)
    assert not (mc / "options.txt").exists()


def test_configured_java_is_used_instead_of_downloading(tmp_path, monkeypatch):
    import launcher.core.installer as installer
    from launcher.config import LauncherConfig

    home = tmp_path / "MinecraftPUTS"
    home.mkdir()
    monkeypatch.setattr(installer, "puts_home", lambda: home)
    monkeypatch.setattr(installer.mll.runtime, "install_jvm_runtime",
                        lambda *a, **k: pytest.fail("não pode baixar Java com um configurado"))

    java = tmp_path / "meu-java"
    java.write_text("#!/bin/sh\n", encoding="utf-8")
    cfg = LauncherConfig(java_path=str(java))
    assert installer.ensure_java(cfg) == str(java)


def test_duplicate_zip_entries_do_not_break_install(tmp_path, monkeypatch):
    import zipfile as _zipfile

    import launcher.core.instances as instances
    from launcher.core.modpacks import install_modpack_zip

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    zpath = tmp_path / "dup.zip"
    with _zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("mods/a.jar", b"primeira")
        zf.writestr("mods/a.jar", b"segunda")

    inst = instances.create_instance("Pack", instance_id="pack", source="github")
    install_modpack_zip(zpath, inst)
    assert (inst.minecraft_path / "mods" / "a.jar").read_bytes() == b"segunda"


def test_parse_pack_url_modrinth_and_curseforge():
    from launcher.core.pack_import import parse_pack_url

    mr = parse_pack_url("https://modrinth.com/modpack/better-mc-forge-bmc4")
    assert mr.platform == "modrinth"
    assert mr.slug == "better-mc-forge-bmc4"
    assert mr.version_hint == ""

    mrv = parse_pack_url("https://modrinth.com/modpack/foo/version/1.2.3")
    assert mrv.version_hint == "1.2.3"

    cf = parse_pack_url("https://www.curseforge.com/minecraft/modpacks/all-the-mods-9")
    assert cf.platform == "curseforge"
    assert cf.slug == "all-the-mods-9"

    cff = parse_pack_url(
        "https://www.curseforge.com/minecraft/modpacks/all-the-mods-9/files/7097953"
    )
    assert cff.version_hint == "7097953"

    with pytest.raises(ValueError, match="não reconhecido"):
        parse_pack_url("https://example.com/pack/foo")
    with pytest.raises(ValueError, match="Modrinth"):
        parse_pack_url("https://modrinth.com/mod/not-a-pack")
    with pytest.raises(ValueError, match="Cole um link"):
        parse_pack_url("   ")


def test_forge_deps_from_mr_and_cf_manifest():
    from launcher.core.pack_import import _forge_from_cf_manifest, _forge_from_mr_deps

    mc, forge, loader = _forge_from_mr_deps({"minecraft": "1.20.1", "forge": "47.2.0"})
    assert mc == "1.20.1"
    assert forge == "1.20.1-47.2.0"
    assert loader == "forge"

    mc, ver, loader = _forge_from_mr_deps({"minecraft": "1.20.1", "fabric-loader": "0.15.0"})
    assert loader == "fabric"
    assert ver == "0.15.0"

    mc, forge, loader = _forge_from_cf_manifest(
        {
            "minecraft": {
                "version": "1.20.1",
                "modLoaders": [{"id": "forge-47.2.0", "primary": True}],
            }
        }
    )
    assert mc == "1.20.1"
    assert forge == "1.20.1-47.2.0"
    assert loader == "forge"

    mc, ver, loader = _forge_from_cf_manifest(
        {
            "minecraft": {
                "version": "1.20.1",
                "modLoaders": [{"id": "fabric-0.15.0", "primary": True}],
            }
        }
    )
    assert loader == "fabric"
    assert ver == "0.15.0"


def test_loader_profile_ids():
    from launcher.core.loaders import latest_loader_version, loader_profile_id, normalize_loader_version

    assert normalize_loader_version("forge", "1.20.1", "47.2.0") == "1.20.1-47.2.0"
    assert normalize_loader_version("fabric", "1.20.1", "0.16.0") == "0.16.0"
    assert normalize_loader_version("neoforge", "1.21.1", "neoforge-21.1.77") == "21.1.77"
    assert "forge" in loader_profile_id("forge", "1.20.1", "47.2.0")
    assert loader_profile_id("fabric", "1.20.1", "0.16.0").startswith("fabric-loader-")
    assert loader_profile_id("neoforge", "1.21.1", "21.1.77").startswith("neoforge-")

    # Empty / stale 1.18.2 default must not stick on a 1.20.1 instance
    fixed = normalize_loader_version("forge", "1.20.1", "")
    assert fixed.startswith("1.20.1-")
    stale = normalize_loader_version("forge", "1.20.1", "1.18.2-40.3.11")
    assert stale.startswith("1.20.1-")
    assert latest_loader_version("forge", "1.18.2").startswith("1.18.2-")


def test_cache_cleanup_drops_imported_and_respects_budget(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.cache_cleanup as cc

    home = tmp_path / "MinecraftPUTS"
    cache = home / "cache"
    monkeypatch.setattr(config, "puts_home", lambda: home)
    monkeypatch.setattr(cc, "cache_dir", lambda: cache)

    imported = cache / "imported_packs"
    imported.mkdir(parents=True)
    big = imported / "old.mrpack"
    big.write_bytes(b"x" * 1000)
    # age it
    import os
    import time

    old = time.time() - cc.IMPORTED_MAX_AGE_SEC - 10
    os.utime(big, (old, old))

    modpacks = cache / "modpacks"
    modpacks.mkdir(parents=True)
    keep = modpacks / "fresh.zip"
    keep.write_bytes(b"y" * 500)

    report = cc.cleanup_cache(budget=10_000)
    assert not big.exists()
    assert keep.exists()
    assert report["removed"] >= 1


def test_install_mrpack_downloads_and_overrides(tmp_path, monkeypatch):
    import zipfile as _zipfile
    from pathlib import Path

    import launcher.core.instances as instances
    from launcher.core.pack_import import install_mrpack

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    calls: list[str] = []

    def fake_download(url, dest, tracker=None, cancel_event=None, timeout=120):
        calls.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"mod-bytes")

    monkeypatch.setattr("launcher.core.pack_import.download_file", fake_download)

    index = {
        "dependencies": {"minecraft": "1.18.2", "forge": "40.2.0"},
        "files": [
            {
                "path": "mods/demo.jar",
                "downloads": ["https://cdn.modrinth.com/data/demo/demo.jar"],
                "hashes": {"sha512": "aa"},
                "env": {"client": "required"},
            }
        ],
    }
    mr = tmp_path / "pack.mrpack"
    with _zipfile.ZipFile(mr, "w") as zf:
        zf.writestr("modrinth.index.json", __import__("json").dumps(index))
        zf.writestr("overrides/config/demo.toml", b"ok=true\n")
        zf.writestr("client-overrides/options.txt", b"fov:1\n")

    inst = instances.create_instance("MR", instance_id="mr-pack", source="modrinth")
    mc_ver, forge_ver, loader = install_mrpack(mr, inst)
    assert mc_ver == "1.18.2"
    assert forge_ver.startswith("1.18.2-")
    assert loader == "forge"
    assert (inst.minecraft_path / "mods" / "demo.jar").read_bytes() == b"mod-bytes"
    assert (inst.minecraft_path / "config" / "demo.toml").read_text() == "ok=true\n"
    assert (inst.minecraft_path / "options.txt").read_text() == "fov:1\n"
    assert calls and "modrinth.com" in calls[0]


def test_install_curseforge_zip_uses_manifest(tmp_path, monkeypatch):
    import json
    import zipfile as _zipfile
    from pathlib import Path

    import launcher.core.instances as instances
    from launcher.core.pack_import import install_curseforge_zip

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    def fake_http_json(url, timeout=30, headers=None):
        if url.endswith("/files/1002"):
            return {
                "data": {
                    "id": 1002,
                    "fileName": "mod.jar",
                    "downloadUrl": "https://edge.forgecdn.net/files/1/002/mod.jar",
                }
            }
        if url.endswith("/download-url"):
            return {"data": "https://edge.forgecdn.net/files/1/002/mod.jar"}
        raise AssertionError(url)

    def fake_download(url, dest, tracker=None, cancel_event=None, timeout=120):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"cf-mod")

    monkeypatch.setattr("launcher.core.pack_import._http_json", fake_http_json)
    monkeypatch.setattr("launcher.core.pack_import.download_file", fake_download)

    manifest = {
        "minecraft": {
            "version": "1.20.1",
            "modLoaders": [{"id": "forge-47.2.0", "primary": True}],
        },
        "files": [{"projectID": 123, "fileID": 1002, "required": True}],
        "overrides": "overrides",
    }
    zpath = tmp_path / "cf.zip"
    with _zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("overrides/mods/bundled.jar", b"bundled")
        zf.writestr("overrides/config/a.cfg", b"x=1\n")

    inst = instances.create_instance("CF", instance_id="cf-pack", source="curseforge")
    mc_ver, forge_ver, loader = install_curseforge_zip(zpath, inst)
    assert mc_ver == "1.20.1"
    assert forge_ver == "1.20.1-47.2.0"
    assert loader == "forge"
    assert (inst.minecraft_path / "mods" / "mod.jar").read_bytes() == b"cf-mod"
    assert (inst.minecraft_path / "mods" / "bundled.jar").read_bytes() == b"bundled"
    assert (inst.minecraft_path / "config" / "a.cfg").read_text() == "x=1\n"


def test_parallel_download_jobs_retries_and_completes(tmp_path, monkeypatch):
    from pathlib import Path

    from launcher.core.pack_import import _parallel_download_jobs

    attempts: dict[str, int] = {}

    def flaky_download(url, dest, tracker=None, cancel_event=None, timeout=120):
        name = Path(dest).name
        attempts[name] = attempts.get(name, 0) + 1
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        if name == "b.jar" and attempts[name] < 2:
            raise RuntimeError("flaky")
        Path(dest).write_bytes(name.encode())

    monkeypatch.setattr("launcher.core.pack_import.download_file", flaky_download)

    jobs = [
        (["https://cdn.modrinth.com/a.jar"], tmp_path / "a.jar"),
        (["https://cdn.modrinth.com/b.jar"], tmp_path / "b.jar"),
        (["https://cdn.modrinth.com/c.jar"], tmp_path / "c.jar"),
    ]
    _parallel_download_jobs(jobs, workers=3)
    assert (tmp_path / "a.jar").read_bytes() == b"a.jar"
    assert (tmp_path / "b.jar").read_bytes() == b"b.jar"
    assert (tmp_path / "c.jar").read_bytes() == b"c.jar"
    assert attempts["b.jar"] >= 2


def test_selected_from_local_jar_and_custom_install(tmp_path, monkeypatch):
    import launcher.core.instances as instances
    from launcher.core.mod_library import create_custom_modpack, selected_from_local_jar

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("launcher.core.skins_mod.ensure_elyby_skins_mod", lambda *a, **k: None)

    jar = tmp_path / "MeuMod.jar"
    jar.write_bytes(b"custom-jar-bytes")
    sel = selected_from_local_jar(jar)
    assert sel.platform == "local"
    assert sel.file_name == "MeuMod.jar"

    with pytest.raises(ValueError):
        selected_from_local_jar(tmp_path / "nope.txt")

    inst = create_custom_modpack(
        name="Custom Local",
        mc_version="1.20.1",
        loader="forge",
        mods=[sel],
    )
    assert (inst.minecraft_path / "mods" / "MeuMod.jar").read_bytes() == b"custom-jar-bytes"
    assert inst.source == "custom"


def test_build_modpack_zip_for_github(tmp_path, monkeypatch):
    from launcher.core.github_publish import build_modpack_zip

    mc = tmp_path / "minecraft"
    mods = mc / "mods"
    mods.mkdir(parents=True)
    (mods / "a.jar").write_bytes(b"aaa")
    (mods / "b.jar").write_bytes(b"bbb")
    dest = tmp_path / "out.zip"
    build_modpack_zip(
        instance_minecraft=mc,
        pack_name="Demo",
        pack_id="demo",
        version="1.0.0",
        mc_version="1.20.1",
        loader="forge",
        loader_version="1.20.1-47.2.0",
        dest_zip=dest,
    )
    assert dest.is_file() and dest.stat().st_size > 0
    import zipfile

    with zipfile.ZipFile(dest) as zf:
        names = zf.namelist()
        assert "mods/a.jar" in names
        assert "mods/b.jar" in names
        assert "pack.meta.json" in names


def test_search_mods_respects_source_filter(monkeypatch):
    from launcher.core import mod_library as lib

    def fake_mr(query, *, mc_version, loader, limit=20):
        return [
            lib.LibraryMod(
                platform="modrinth",
                project_id="1",
                slug="sodium",
                name="Sodium",
                downloads=1000,
            )
        ]

    def fake_cf(query, *, mc_version, loader, limit=20):
        return [
            lib.LibraryMod(
                platform="curseforge",
                project_id="2",
                slug="jei",
                name="JEI",
                downloads=5000,
            )
        ]

    monkeypatch.setattr(lib, "search_modrinth_mods", fake_mr)
    monkeypatch.setattr(lib, "search_curseforge_mods", fake_cf)

    both = lib.search_mods("", mc_version="1.20.1", loader="forge", sources=["modrinth", "curseforge"])
    assert [m.slug for m in both] == ["jei", "sodium"]  # downloads desc

    only_mr = lib.search_mods("x", mc_version="1.20.1", loader="forge", sources=["modrinth"])
    assert len(only_mr) == 1 and only_mr[0].platform == "modrinth"
