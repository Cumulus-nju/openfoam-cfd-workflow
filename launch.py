"""
UrbanWind CFD Launcher — handles startup, health check, and browser launch.

Usage:
    python launch.py
    (or double-click 启动UrbanWind.bat which calls this)
"""
import subprocess
import sys
import time
import urllib.request
import os
import io
from pathlib import Path

# ── Fix encoding on Windows: force UTF-8 stdout ──────────────────────────
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

PROJECT_DIR = Path(__file__).parent
URL = "http://127.0.0.1:8765"


def print_banner():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║       UrbanWind CFD — 城市微风场建模前端        ║")
    print("╚══════════════════════════════════════════════════╝")
    print()
    print(f"  目标地址: {URL}")
    print()


def is_server_ready() -> bool:
    try:
        urllib.request.urlopen(f"{URL}/api/health", timeout=2)
        return True
    except Exception:
        return False


def open_browser():
    os.startfile(URL)


def main():
    os.chdir(PROJECT_DIR)
    os.environ["PYTHONIOENCODING"] = "utf-8"

    print_banner()
    print("  正在启动服务器...")

    # Start server as a detached subprocess
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "frontend.main"],
        cwd=str(PROJECT_DIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )

    # Wait for server to be ready (up to 30 seconds)
    print("  等待服务器就绪...", end="", flush=True)
    for _ in range(30):
        if is_server_ready():
            print(" ✓")
            break
        time.sleep(1)
        print(".", end="", flush=True)
    else:
        print(" ✗")
        print()
        print("  [错误] 服务器启动超时，请检查:")
        print("    1. 端口 8765 是否被占用")
        print("    2. Python 依赖是否安装完整")
        print()
        input("  按回车退出...")
        server_proc.kill()
        sys.exit(1)

    # Open browser
    open_browser()
    print(f"  [OK] 服务器已启动，浏览器已打开！")
    print()
    print("  ─────────────────────────────────────────────")
    print("  停止方式: 关闭 UrbanWind-Server 窗口")
    print("  或者在该窗口内按 Ctrl+C")
    print("  ─────────────────────────────────────────────")
    print()
    print("  按 Ctrl+C 或回车退出此面板（不影响服务器）...")

    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass

    print()
    print("  此面板已退出，服务器仍在运行。")
    print(f"  浏览器访问 {URL} 可继续使用。")


if __name__ == "__main__":
    main()
