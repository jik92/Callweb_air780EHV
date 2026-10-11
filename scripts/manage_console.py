#!/usr/bin/env python3
"""Start/stop only this console's background processes on dedicated local ports."""

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "console-processes.json"


def identity(pid):
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return result.rsplit(None, 1)[0] if result and not result.split()[-1].startswith("Z") else None


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def alive(item):
    return identity(item["pid"]) == item["identity"]


def stop():
    for item in load().values():
        if alive(item):
            os.killpg(item["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 8
    while any(alive(item) for item in load().values()) and time.monotonic() < deadline:
        time.sleep(0.2)
    for item in load().values():
        if alive(item):
            raise SystemExit(f"进程 {item['pid']} 尚未退出，请检查后再启动")
    STATE.unlink(missing_ok=True)
    print("控制台服务已停止")


def start():
    if any(alive(item) for item in load().values()):
        raise SystemExit("控制台已运行，使用 status 查看或 restart 重启")
    for port in (9010, 9011):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"端口 {port} 已被占用，未停止其他程序")
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    commands = {
        "backend": [
            str(ROOT / "backend/.venv/bin/python"),
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            str(ROOT / "backend"),
            "--host",
            "127.0.0.1",
            "--port",
            "9010",
        ],
        "frontend": [
            "node",
            str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
            "--host",
            "127.0.0.1",
            "--port",
            "9011",
            "--strictPort",
        ],
    }
    state = {}
    for name, command in commands.items():
        with (ROOT / "logs" / f"console-{name}.log").open("a") as log:
            process = subprocess.Popen(
                command,
                cwd=ROOT / "frontend" if name == "frontend" else ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        state[name] = {"pid": process.pid, "identity": identity(process.pid)}
        STATE.write_text(json.dumps(state, indent=2) + "\n")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not all(alive(item) for item in state.values()):
            raise SystemExit("服务启动失败，请检查 logs/console-*.log，并执行 stop 清理")
        try:
            with urlopen("http://127.0.0.1:9010/api/health", timeout=1) as response:
                response.read()
            with urlopen("http://127.0.0.1:9011", timeout=1) as response:
                response.read()
            print("前端 http://127.0.0.1:9011  后端 http://127.0.0.1:9010")
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("服务启动超时，请检查日志")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["start", "stop", "restart", "status"], default="start", nargs="?"
    )
    action = parser.parse_args().action
    if action in ("stop", "restart"):
        stop()
    if action in ("start", "restart"):
        start()
    if action == "status":
        print(
            json.dumps(
                {name: {**item, "running": alive(item)} for name, item in load().items()},
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
