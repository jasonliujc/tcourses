"""桌面版启动器：后端和打包好的前端跑在一个进程里，开一个原生窗口。

    python -m app.desktop            # 正常使用：数据在用户目录，Canvas 连接在设置页里填
    python -m app.desktop --demo     # 演示：模拟的 Canvas + 生成的学期，一次性数据
    python -m app.desktop --demo --headless --port 8766   # 只起服务，不开窗口（截图、测试用）

打包后的 TCourses / TCourses Demo 两个可执行文件就是这两条命令。

- 数据目录：Windows `%LOCALAPPDATA%/TCourses`，macOS `~/Library/Application Support/TCourses`，
  Linux `~/.local/share/TCourses`。演示每次用一个新的临时目录，关掉就删
- 同一个数据目录只跑一份：再开一次会直接把已经在跑的那份窗口打开
- 窗口用系统自带的 WebView（pywebview）；没有的话退回默认浏览器
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "TCourses"
PORT = 8765
DEMO_PORT = 8766


def user_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def bundle_dir() -> Path:
    """PyInstaller 的解包目录；从源码跑时是仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def static_dir() -> Path | None:
    for candidate in (bundle_dir() / "web", bundle_dir() / "frontend" / "dist"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def pick_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("no free port")


def healthy(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return resp.status == 200
    except OSError:
        return False


def open_window(url: str, title: str) -> bool:
    """阻塞到窗口关掉。没有可用的 WebView 就返回 False。"""
    try:
        import webview
    except Exception:  # noqa: BLE001 没装或者平台不支持
        return False
    try:
        webview.create_window(title, url, width=1320, height=860, min_size=(960, 640))
        webview.start(gui="edgechromium" if sys.platform == "win32" else None)
    except Exception:  # noqa: BLE001 比如 Windows 上没有 WebView2
        return False
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--demo", action="store_true", help="run with a generated sample semester")
    parser.add_argument("--data-dir", help="where the database and downloads live")
    parser.add_argument("--port", type=int)
    parser.add_argument("--browser", action="store_true", help="open in the default browser")
    parser.add_argument("--headless", action="store_true", help="serve only, open nothing")
    args = parser.parse_args(argv)

    if args.demo:
        # 上次没删干净的（Windows 上文件还被占着时删不掉）顺手清掉；正在用的会删失败，正好留着
        for stale in Path(tempfile.gettempdir()).glob(f"{APP_NAME}-demo-*"):
            shutil.rmtree(stale, ignore_errors=True)
        root = Path(tempfile.mkdtemp(prefix=f"{APP_NAME}-demo-"))
    else:
        root = Path(args.data_dir) if args.data_dir else user_data_dir()
    root.mkdir(parents=True, exist_ok=True)

    # 打包后没有控制台：日志写进数据目录，否则 sys.stderr 是 None，第一行日志就崩
    if getattr(sys, "frozen", False):
        log = open(root / "tcourses.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = log

    lock = root / "instance.json"
    if not args.demo and lock.exists():
        try:
            running = json.loads(lock.read_text(encoding="utf-8"))["port"]
        except (OSError, ValueError, KeyError):
            running = None
        if running and healthy(running):
            url = f"http://127.0.0.1:{running}"
            if args.headless:
                print(f"{APP_NAME} is already running at {url}", flush=True)
            elif not open_window(url, APP_NAME):
                webbrowser.open(url)
            return

    port = args.port or pick_port(DEMO_PORT if args.demo else PORT)
    static = static_dir()
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{(root / 'tcourses.db').as_posix()}",
            "STORAGE_ROOT": str(root / "storage"),
            "DEMO_MODE": "true" if args.demo else "false",
            # 桌面版只认设置页里存的连接，不读开发用的 backend/.env 里的令牌
            "CANVAS_ACCESS_TOKEN": "",
            "STATIC_DIR": str(static) if static else "",
            "APP_ENV": "desktop",
        }
    )

    # 环境变量必须在导入 app 之前设好：配置在导入时就定型了
    import uvicorn

    from app.main import app

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 120
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            sys.exit("TCourses failed to start; see tcourses.log in the data folder")
        time.sleep(0.1)

    if not args.demo:
        lock.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    url = f"http://127.0.0.1:{port}"
    title = f"{APP_NAME} Demo" if args.demo else APP_NAME
    try:
        if args.headless or args.browser or not open_window(url, title):
            if not args.headless:
                webbrowser.open(url)
            print(f"{title} is running at {url}. Press Ctrl+C to quit.", flush=True)
            while thread.is_alive():
                thread.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if args.demo:
            shutil.rmtree(root, ignore_errors=True)
        else:
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
