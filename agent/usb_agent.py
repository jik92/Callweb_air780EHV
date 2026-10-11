#!/usr/bin/env python3
"""One USB device per process; API owns persistence, commands and voice services."""

import argparse
import asyncio
import contextlib
import ipaddress
import json
import logging
import queue
import signal
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit

import websockets

# These two shared modules have no API/framework dependency.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.protocol import AUDIO_END, AUDIO_START  # noqa: E402
from app.serial_bridge import SerialBridge  # noqa: E402

LOG = logging.getLogger("usb-agent")
ACTIONS = {"get_status", "get_inbox", "send_sms", "call_live", "call_answer", "call_hangup"}


def load_config(path):
    data = json.loads(Path(path).read_text())
    url = urlsplit(data.get("server_url", ""))
    if (
        url.scheme not in ("ws", "wss")
        or not url.hostname
        or url.path != "/ws/device"
        or url.query
        or url.fragment
        or url.username
        or url.password
    ):
        raise ValueError("server_url must be ws(s)://host/ws/device")
    if not all(isinstance(data.get(k), str) and data[k] for k in ("device_id", "token")):
        raise ValueError("device_id and token are required")
    port = data.get("serial_port")
    if port is not None and not isinstance(port, str):
        raise ValueError("serial_port must be a string or null")
    if data.get("connect_host"):
        ipaddress.ip_address(data["connect_host"])
    return data


def decode_command(packet):
    if not isinstance(packet, bytes) or len(packet) > 65536:
        raise ValueError("Invalid binary command")
    if packet.startswith(b"CMD_START:") and packet.endswith(b":CMD_END\r\n"):
        value = json.loads(packet[10:-10])
        if not isinstance(value, dict) or value.get("action") not in ACTIONS:
            raise ValueError("Unsupported device command")
        return value
    if packet.startswith(b"PCM_START:") and packet.endswith(b":PCM_END"):
        if len(packet) < 60:
            raise ValueError("Invalid audio frame")
        length = struct.unpack_from("<H", packet, 46)[0]
        if length > 1280 or length % 2 or len(packet) != 60 + length:
            raise ValueError("Invalid audio length")
        return packet
    raise ValueError("Unknown frame")


def encode_event(event):
    if event.get("type") == "call_pcm":
        pcm = event["pcm"]
        return (
            AUDIO_START
            + event["request_id"].encode("ascii")
            + struct.pack("<HHI", len(pcm), event["sample_rate"], event["sequence"])
            + pcm
            + AUDIO_END
        )
    return b"SMS_START:" + json.dumps(event, ensure_ascii=False).encode() + b":SMS_END\r\n"


async def session(config, bridge_factory=SerialBridge):
    loop = asyncio.get_running_loop()
    controls, audio = asyncio.Queue(maxsize=128), asyncio.Queue(maxsize=6)
    changed, failed = asyncio.Event(), asyncio.Event()
    usb_online = False
    active_call = None
    hangup_written = asyncio.Event()

    def event(item):
        nonlocal usb_online, active_call
        if item.get("type") == "transport":
            usb_online = item.get("connected", False)
            if not usb_online:
                failed.set()
            return
        if item.get("type") == "command_written":
            if item.get("action") == "call_hangup":
                hangup_written.set()
            return
        if item.get("type") == "call_result" and item.get("request_id") == active_call:
            active_call = None
        target = audio if item.get("type") == "call_pcm" else controls
        if target.full():
            if target is controls:
                failed.set()
                return
            target.get_nowait()
        target.put_nowait(encode_event(item))
        changed.set()

    bridge = bridge_factory(
        config.get("serial_port"), lambda item: loop.call_soon_threadsafe(event, item)
    )
    port = bridge.discover()
    if not port:
        LOG.info("Waiting for USB device")
        await asyncio.sleep(3)
        return
    bridge.port = port
    tasks = []
    try:
        options = {"host": config["connect_host"]} if config.get("connect_host") else {}
        async with websockets.connect(
            config["server_url"],
            proxy=None,
            open_timeout=15,
            ping_interval=10,
            ping_timeout=10,
            max_size=65536,
            **options,
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "device_login",
                        "device_id": config["device_id"],
                        "token": config["token"],
                        "serial_port": port,
                    }
                )
            )
            login = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if (
                login.get("type") != "device_login_ok"
                or login.get("device_id") != config["device_id"]
            ):
                raise ValueError("API did not authenticate the device")
            bridge.start()
            LOG.info("API connected; bridging USB %s", port)

            async def send():
                while True:
                    changed.clear()
                    if not controls.empty():
                        packet = controls.get_nowait()
                    elif not audio.empty():
                        packet = audio.get_nowait()
                    else:
                        await changed.wait()
                        continue
                    await asyncio.wait_for(ws.send(packet), 3)

            async def receive():
                nonlocal active_call
                async for packet in ws:
                    command = decode_command(packet)
                    if not usb_online and not (
                        isinstance(command, dict)
                        and command["action"] in {"get_status", "get_inbox"}
                    ):
                        raise ValueError("USB is not ready")
                    if isinstance(command, dict):
                        bridge.send(command)
                        if command["action"] in {"call_live", "call_answer"}:
                            active_call = command["request_id"]
                    else:
                        bridge.send_audio(command)

            tasks = [
                asyncio.create_task(send()),
                asyncio.create_task(receive()),
                asyncio.create_task(failed.wait()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Cancel queued work; only end a call already assigned to this USB device.
        while True:
            try:
                bridge.commands.get_nowait()
            except queue.Empty:
                break
        bridge.clear_audio()
        if usb_online and active_call:
            hangup_written.clear()
            bridge.send({"action": "call_hangup", "request_id": active_call})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(hangup_written.wait(), 1.5)
        # Stop the single serial owner before any reconnection creates another worker.
        if bridge.thread.ident is not None:
            await asyncio.to_thread(bridge.close)
            await asyncio.to_thread(bridge.thread.join)


async def run(config):
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    while True:
        try:
            await session(config)
        except (OSError, ValueError, queue.Full, TimeoutError, websockets.WebSocketException):
            LOG.warning("USB/API disconnected; reconnecting without replaying commands")
        await asyncio.sleep(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Private device-agent.json from API binding")
    parser.add_argument("--list-ports", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.list_ports:
        from app.serial_bridge import available_ports

        print(json.dumps(available_ports(), ensure_ascii=False, indent=2))
        return
    if not args.config:
        parser.error("--config is required unless --list-ports is used")
    with contextlib.suppress(KeyboardInterrupt, asyncio.CancelledError):
        asyncio.run(run(load_config(args.config)))


if __name__ == "__main__":
    main()
