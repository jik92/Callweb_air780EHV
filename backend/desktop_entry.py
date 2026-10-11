"""Frozen macOS service entry; stdin EOF shuts down only this owned process."""

import argparse
import os
import sys
import threading
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9012)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--serial-port")
    args = parser.parse_args()
    if not 9000 <= args.port <= 9999:
        parser.error("port must be in the 9xxx range")
    os.environ["AIR780_PROJECT_DIR"] = str(args.project_dir.resolve())
    os.environ["AIR780_DATA_DIR"] = str(args.data_dir.resolve())
    os.environ["AIR780_DESKTOP_ORIGIN"] = f"http://127.0.0.1:{args.port}"
    os.environ["AIR780_DESKTOP_INSTANCE"] = args.instance
    if args.serial_port:
        os.environ["AIR780_SERIAL_PORT"] = args.serial_port
    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1))

    def monitor_parent():
        try:
            while sys.stdin.buffer.read(1):
                pass
        finally:
            server.should_exit = True

    threading.Thread(target=monitor_parent, name="desktop-parent", daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
