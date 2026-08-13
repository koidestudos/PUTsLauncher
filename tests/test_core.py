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
