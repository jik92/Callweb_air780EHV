#!/usr/bin/env python3
"""Manage a separate USB Agent per binding, without stopping unrelated processes."""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from manage_console import identity

ROOT = Path(__file__).resolve().parents[1]


def alive(item):
    return bool(item.get("identity")) and identity(item["pid"]) == item["identity"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "restart", "status"])
    parser.add_argument("--config", default=str(ROOT / "data/usb-agent/device-agent.json"))
    args = parser.parse_args()
    config = Path(args.config).resolve()
    device_id = json.loads(config.read_text())["device_id"]
    from uuid import UUID

    device_id = str(UUID(device_id))
    state_path = ROOT / "data/usb-agent" / (device_id + ".process.json")
    item = json.loads(state_path.read_text()) if state_path.exists() else {}
    if args.action == "status":
        print(json.dumps({**item, "running": alive(item)}, indent=2))
        return
    if args.action in ("stop", "restart"):
        if alive(item):
            os.killpg(item["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 8
            while alive(item) and time.monotonic() < deadline:
                time.sleep(0.1)
            if alive(item):
                raise SystemExit("Agent is still stopping; inspect its log")
        state_path.unlink(missing_ok=True)
        print("USB Agent stopped")
    if args.action in ("start", "restart"):
        if alive(item):
            raise SystemExit("USB Agent already running")
        state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        (ROOT / "logs").mkdir(exist_ok=True)
        with (ROOT / "logs" / ("usb-agent-" + device_id + ".log")).open("a") as log:
            process = subprocess.Popen(
                [
                    str(ROOT / "agent/.venv/bin/python"),
                    str(ROOT / "agent/usb_agent.py"),
                    "--config",
                    str(config),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        item = {"pid": process.pid, "identity": identity(process.pid)}
        state_path.write_text(json.dumps(item) + "\n")
        state_path.chmod(0o600)
        time.sleep(0.3)
        if not alive(item):
            raise SystemExit("USB Agent failed; inspect its log")
        print("USB Agent started")


if __name__ == "__main__":
    main()
