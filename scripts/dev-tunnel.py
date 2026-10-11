#!/usr/bin/env python3
"""Manage the isolated FRP test tunnel using private local configuration."""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from manage_console import identity

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data/frp/processes.json"


def alive(item):
    return bool(item["identity"]) and identity(item["pid"]) == item["identity"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--frpc", default=str(ROOT / "tools/frp/frpc"))
    args = parser.parse_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if args.action == "status":
        print(json.dumps({k: {**v, "running": alive(v)} for k, v in state.items()}, indent=2))
        return
    if args.action == "stop":
        for item in state.values():
            if alive(item):
                os.killpg(item["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while any(alive(v) for v in state.values()) and time.monotonic() < deadline:
            time.sleep(0.1)
        if any(alive(v) for v in state.values()):
            raise SystemExit("Tunnel still stopping; check its logs")
        STATE.unlink(missing_ok=True)
        print("Tunnel stopped")
        return
    if any(alive(v) for v in state.values()):
        raise SystemExit("Tunnel already running")
    if not (ROOT / "data/frp/frpc.toml").exists() or not Path(args.frpc).is_file():
        raise SystemExit("Provide private data/frp/frpc.toml and --frpc binary path")
    commands = {
        "ssh": [
            "ssh",
            "-NT",
            "-oBatchMode=yes",
            "-oExitOnForwardFailure=yes",
            "-oServerAliveInterval=15",
            "-oServerAliveCountMax=3",
            "-L127.0.0.1:9701:127.0.0.1:9700",
            "ECS-PROD-01",
        ],
        "frpc": [args.frpc, "-c", str(ROOT / "data/frp/frpc.toml")],
    }
    state = {}
    for name, command in commands.items():
        with (ROOT / "logs" / f"tunnel-{name}.log").open("a") as log:
            process = subprocess.Popen(
                command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
        state[name] = {"pid": process.pid, "identity": identity(process.pid)}
        STATE.write_text(json.dumps(state, indent=2) + "\n")
        STATE.chmod(0o600)
        time.sleep(0.3)
    if not all(alive(v) for v in state.values()):
        raise SystemExit("Tunnel failed; inspect logs/tunnel-*.log and run stop")
    print("Tunnel started")


if __name__ == "__main__":
    main()
