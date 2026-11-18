# PyInstaller spec for the desktop build. Run through packaging/build.py.
#
# Windows / Linux: one folder with two executables sharing the same libraries,
#   "TCourses" and "TCourses Demo".
# macOS: TCourses.app; build.py adds a "TCourses Demo.command" launcher next to it.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
BACKEND = ROOT / "backend"
WEB = ROOT / "frontend" / "dist"
ICON = ROOT / "packaging" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")

if not (WEB / "index.html").is_file():
    raise SystemExit("frontend/dist is missing: run `npm run build` in frontend/ first")

hidden = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + ["aiosqlite", "sqlalchemy.dialects.sqlite.aiosqlite", "greenlet"]
)


def analysis(script):
    return Analysis(
        [str(ROOT / "packaging" / script)],
        pathex=[str(BACKEND)],
        datas=[(str(WEB), "web")],
        hiddenimports=hidden,
        # Pillow is only for packaging/make_icon.py, never at runtime
        excludes=["tkinter", "pytest", "ruff", "PIL"],
    )


def exe(a, name):
    return EXE(
        PYZ(a.pure),
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=False,
        upx=False,
        icon=str(ICON),
    )


main = analysis("launch.py")
main_exe = exe(main, "TCourses")

if sys.platform == "darwin":
    coll = COLLECT(main_exe, main.binaries, main.datas, name="TCourses")
    app = BUNDLE(coll, name="TCourses.app", icon=str(ICON), bundle_identifier="io.github.tcourses")
else:
    demo = analysis("launch_demo.py")
    demo_exe = exe(demo, "TCourses Demo")
    coll = COLLECT(
        main_exe, main.binaries, main.datas,
        demo_exe, demo.binaries, demo.datas,
        name="TCourses",
    )
