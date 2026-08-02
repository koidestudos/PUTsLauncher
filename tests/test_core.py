from __future__ import annotations

from launcher.auth.session import offline_session
from launcher.config import FORGE_PROFILE, MC_VERSION, mods_source_dir
from launcher.core.mods import list_bundled_mods, sync_mods


def test_offline_session_stable_uuid():
    a = offline_session("Puts")
    b = offline_session("Puts")
    assert a.uuid == b.uuid
    assert a.username == "Puts"
    assert a.offline is True


def test_offline_session_trims_and_limits():
    s = offline_session("  VeryLongNicknameXYZ  ")
    assert len(s.username) <= 16


def test_pack_has_forge_mods():
    mods = list_bundled_mods()
    assert len(mods) >= 100
    assert mods_source_dir().name == "mods"
    assert MC_VERSION == "1.18.2"
    assert "forge" in FORGE_PROFILE


def test_sync_mods_roundtrip(tmp_path, monkeypatch):
    import launcher.config as config
    import launcher.core.mods as mods_mod

    monkeypatch.setattr(config, "default_instance_dir", lambda: tmp_path / "instance")
    monkeypatch.setattr(mods_mod, "minecraft_dir", lambda: tmp_path / "instance" / "minecraft")

    n = sync_mods()
    assert n == len(list_bundled_mods())
    assert len(list((tmp_path / "instance" / "minecraft" / "mods").glob("*.jar"))) == n
