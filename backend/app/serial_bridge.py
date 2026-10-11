"""A single worker owns serial reads AND writes; reconnects never replay commands."""

import queue
import threading
import time
from collections.abc import Callable

import serial
from serial.tools import list_ports

from .protocol import FrameParser, encode_command


def available_ports() -> list[dict]:
    return [
        {"port": p.device, "description": p.description, "vid": p.vid, "pid": p.pid}
        for p in list_ports.comports()
        if p.device.startswith("/dev/cu.")
    ]


class SerialBridge:
    def __init__(self, port: str | None, emit: Callable[[dict], None]):
        self.port = port
        self.emit = emit
        self.commands = queue.Queue(maxsize=32)
        self.audio = queue.Queue(maxsize=6)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="air780-serial", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)

    def send(self, command: dict):
        self.commands.put_nowait(command)

    def send_audio(self, frame: bytes):
        dropped = False
        if self.audio.full():
            try:
                self.audio.get_nowait()
                dropped = True
            except queue.Empty:
                pass
        self.audio.put_nowait(frame)
        return dropped

    def clear_audio(self):
        while True:
            try:
                self.audio.get_nowait()
            except queue.Empty:
                break

    def discover(self) -> str | None:
        if self.port:
            return self.port
        for port in list_ports.comports():
            if (
                port.vid == 0x19D1
                and port.device.startswith("/dev/cu.")
                and port.device.endswith("17")
            ):
                return port.device
        return None

    def run(self):
        while not self.stop.is_set():
            port = self.discover()
            if not port:
                self.emit({"type": "transport", "connected": False, "error": "device_not_found"})
                self.stop.wait(2)
                continue
            try:
                with serial.Serial(
                    port, 115200, timeout=0.01, write_timeout=2, exclusive=True
                ) as conn:
                    parser = FrameParser()
                    self.emit({"type": "transport", "connected": True, "port": port})
                    conn.write(encode_command({"action": "get_status"}))
                    conn.write(encode_command({"action": "get_inbox"}))
                    next_status = time.monotonic() + 10
                    next_inbox = time.monotonic() + 60
                    last_frame = time.monotonic()
                    while not self.stop.is_set():
                        try:
                            command = self.commands.get_nowait()
                        except queue.Empty:
                            command = None
                        if command:
                            conn.write(encode_command(command))
                            conn.flush()
                            self.emit(
                                {"type": "command_written", "request_id": command.get("request_id")}
                            )
                        for _ in range(4):
                            try:
                                conn.write(self.audio.get_nowait())
                            except queue.Empty:
                                break
                        now = time.monotonic()
                        if now >= next_status:
                            conn.write(encode_command({"action": "get_status"}))
                            next_status = now + 10
                        if now >= next_inbox:
                            conn.write(encode_command({"action": "get_inbox"}))
                            next_inbox = now + 60
                        for frame in parser.feed(conn.read(conn.in_waiting or 1)):
                            last_frame = time.monotonic()
                            self.emit(frame)
                        if time.monotonic() - last_frame > 35:
                            raise serial.SerialException("device_response_timeout")
            except (OSError, serial.SerialException, ValueError) as exc:
                while True:
                    try:
                        self.commands.get_nowait()
                    except queue.Empty:
                        break
                while not self.audio.empty():
                    self.audio.get_nowait()
                self.emit(
                    {"type": "transport", "connected": False, "port": port, "error": str(exc)}
                )
                self.stop.wait(2)
