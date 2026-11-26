"""Build the desktop app with PyInstaller.

    uv sync --group build && uv run python scripts/build.py

Windows -> dist/LoLPacemaker.exe (onefile, windowed)
macOS   -> dist/LoLPacemaker.app (windowed app bundle; a onefile GUI build is deprecated on macOS)
Output is then zipped as dist/LoLPacemaker-<windows|macos>.zip for the release.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "LoLPacemaker"


def build() -> Path:
    sep = os.pathsep  # ';' on Windows, ':' elsewhere (PyInstaller --add-data syntax)
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", NAME,
        "--add-data", f"{ROOT / 'locales'}{sep}locales",
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--hidden-import", "pynput.keyboard._darwin" if sys.platform == "darwin" else "pynput.keyboard._win32",
        # training-only stacks are never imported by the app; keep the binary small
        "--exclude-module", "sklearn", "--exclude-module", "skl2onnx", "--exclude-module", "scipy",
        "--exclude-module", "pandas", "--exclude-module", "matplotlib",
    ]
    if sys.platform == "win32":
        args.append("--onefile")
    args.append(str(ROOT / "pacemaker.py"))
    subprocess.run(args, cwd=ROOT, check=True)

    dist = ROOT / "dist"
    zip_path = dist / f"{NAME}-{'windows' if sys.platform == 'win32' else 'macos'}.zip"
    if sys.platform == "win32":
        artifact = dist / f"{NAME}.exe"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(artifact, artifact.name)
    else:
        app = dist / f"{NAME}.app"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(zip_path)], check=True)
        shutil.rmtree(dist / NAME, ignore_errors=True)  # PyInstaller's intermediate onedir folder
    return zip_path


if __name__ == "__main__":
    print(f"platform={platform.platform()}")
    print(f"built: {build()}")
