"""Build the desktop app: frontend bundle -> PyInstaller -> one zip in dist/.

    pip install -e "backend[desktop]"
    python packaging/build.py
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def run(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def version() -> str:
    """Release builds take the version from the tag (v0.1.0-beta.1 -> 0.1.0-beta.1)."""
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if os.environ.get("GITHUB_REF_TYPE") == "tag" and tag.startswith("v"):
        return tag[1:]
    with open(ROOT / "backend" / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def target() -> str:
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower().replace("amd64", "x64").replace("x86_64", "x64")
    return f"{system}-{machine}"


def main() -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("npm not found: install Node.js 20+")
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules").is_dir():
        run([npm, "ci"], frontend)
    run([npm, "run", "build"], frontend)

    run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "tcourses.spec"),
         "--noconfirm", "--clean", "--distpath", str(DIST),
         "--workpath", str(ROOT / "build" / "pyinstaller")], ROOT)

    if sys.platform == "darwin":
        shutil.rmtree(DIST / "TCourses", ignore_errors=True)  # the .app holds everything
        stage = DIST / "TCourses-macOS"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        shutil.move(str(DIST / "TCourses.app"), stage / "TCourses.app")
        demo = stage / "TCourses Demo.command"
        demo.write_text('#!/bin/sh\nopen -n "$(dirname "$0")/TCourses.app" --args --demo\n')
        demo.chmod(0o755)
        folder = stage
    else:
        folder = DIST / "TCourses"

    archive = DIST / f"TCourses-{version()}-{target()}.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            info = zipfile.ZipInfo.from_file(path, Path("TCourses") / path.relative_to(folder))
            if path.is_dir():
                zf.writestr(info, "")
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    size = archive.stat().st_size / 1e6
    print(f"built {archive.relative_to(ROOT)} ({size:.0f} MB)")


if __name__ == "__main__":
    main()
