"""Cross-platform OS login autostart (Windows Registry Run / macOS LaunchAgent).

Registers LoL Pacemaker with `--background` so it starts silently at login,
uses ~0% CPU while idle, and pops up automatically when a LoL match begins.
"""
from __future__ import annotations

import plistlib
import shlex
import sys
from pathlib import Path

APP_LABEL = "com.lolpacemaker.app"
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WIN_VALUE_NAME = "LoLPacemaker"


def default_launch_command() -> list[str]:
    """Build the command to launch LoL Pacemaker in background auto-popup mode."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--background"]
    return [sys.executable, "-m", "src.main", "--background"]


def mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{APP_LABEL}.plist"


def is_autostart_enabled(plist_path: Path | None = None) -> bool:
    """Return True if OS login autostart is currently registered."""
    if sys.platform == "darwin" or plist_path is not None:
        return (plist_path or mac_plist_path()).exists()
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, WIN_VALUE_NAME)
                return bool(val)
        except OSError:
            return False
    return False


def set_autostart(
    enabled: bool,
    command: list[str] | None = None,
    plist_path: Path | None = None,
) -> bool:
    """Enable or disable OS login autostart. Returns True if the operation succeeded."""
    cmd = list(command) if command is not None else default_launch_command()
    if sys.platform == "darwin" or plist_path is not None:
        target = plist_path or mac_plist_path()
        try:
            if enabled:
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "Label": APP_LABEL,
                    "ProgramArguments": cmd,
                    "RunAtLoad": True,
                    "KeepAlive": False,
                }
                target.write_bytes(plistlib.dumps(payload))
            elif target.exists():
                target.unlink()
            return True
        except OSError:
            return False

    if sys.platform == "win32":
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    # Quote each arg safely for Windows CreateProcess command line
                    cmdline = " ".join(f'"{a}"' if " " in a else a for a in cmd)
                    winreg.SetValueEx(key, WIN_VALUE_NAME, 0, winreg.REG_SZ, cmdline)
                else:
                    try:
                        winreg.DeleteValue(key, WIN_VALUE_NAME)
                    except FileNotFoundError:
                        pass
            return True
        except OSError:
            return False

    return False


def format_command_preview(command: list[str] | None = None) -> str:
    return shlex.join(command or default_launch_command())
