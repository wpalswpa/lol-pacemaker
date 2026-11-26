import plistlib

from src.autostart import APP_LABEL, default_launch_command, is_autostart_enabled, set_autostart


def test_default_launch_command_uses_background_flag():
    cmd = default_launch_command()
    assert "--background" in cmd


def test_mac_launch_agent_roundtrip(tmp_path):
    plist = tmp_path / f"{APP_LABEL}.plist"
    assert is_autostart_enabled(plist_path=plist) is False

    ok = set_autostart(True, command=["/Applications/LoLPacemaker.app/Contents/MacOS/LoLPacemaker", "--background"], plist_path=plist)
    assert ok is True
    assert is_autostart_enabled(plist_path=plist) is True

    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == APP_LABEL
    assert data["RunAtLoad"] is True
    assert "--background" in data["ProgramArguments"]

    assert set_autostart(False, plist_path=plist) is True
    assert is_autostart_enabled(plist_path=plist) is False
    assert not plist.exists()
