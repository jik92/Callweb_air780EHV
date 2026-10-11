import base64
import struct
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app.flashing import parse_binpkg, prepare


def package(size=4):
    data = bytearray(0x1D8 + 364 + 4)
    data[0x38:0x3F] = b"pkgmode"
    data[0x190:0x19C] = b"EC718HM_PRD\0"
    data[0x1D8:0x1DA] = b"ap"
    struct.pack_into("<IIII", data, 0x1D8 + 64, 0x803000, 4, 0, size)
    data[0x1D8 + 336 : 0x1D8 + 338] = b"AP"
    data[-4:] = b"test"
    return bytes(data)


def test_binpkg_rejects_truncation_wrong_chip_and_size():
    chip, entries = parse_binpkg(package())
    assert chip == "EC718HM_PRD"
    assert base64.b64decode(entries[0]["data"]) == b"test"
    for data in (b"", package()[:-1], package(99)):
        with pytest.raises(ValueError):
            parse_binpkg(data)
    data = bytearray(package())
    data[0x190:0x196] = b"EC618!"
    with pytest.raises(ValueError, match="芯片"):
        parse_binpkg(bytes(data))


def test_prepare_project_without_serial():
    script = prepare("script")
    assert script["chip"] == "EC718HM_PRD"
    assert [entry["name"] for entry in script["entries"]] == ["script"]
    assert script["entries"][0]["address"] == 0xD6E000
    assert script["agent"]["size"] == 40696
    assert script["agent"]["sha256"] == (
        "92a596c9a2f339e1427ec6f688115846983e99f8f7a2f835457f762fa6077a51"
    )
    full = prepare("full")
    assert [entry["name"] for entry in full["entries"]] == [
        "ap_bootloader",
        "ap",
        "cp-demo-flash",
        "script",
    ]
    assert full["entries"][2]["address"] == 0x2D000


def test_bridge_pause_blocks_busy_and_rejects_origin(monkeypatch):
    import asyncio

    paused, released = threading.Event(), threading.Event()
    bridge = SimpleNamespace(paused=paused, released=released)
    service = SimpleNamespace(
        bridge=bridge,
        calls=SimpleNamespace(active=lambda: True),
        device=None,
        store=SimpleNamespace(list=lambda: []),
        network_waiter=None,
        location=SimpleNamespace(waiter=None),
        usb_network=SimpleNamespace(waiter=None),
    )
    monkeypatch.setattr(main.app.state, "service", service, raising=False)
    request = Request({"type": "http", "headers": [(b"origin", b"http://127.0.0.1:9011")]})
    with pytest.raises(HTTPException) as busy:
        asyncio.run(main.flash_bridge_control(request, "pause"))
    assert busy.value.status_code == 409
    assert not paused.is_set()
    with pytest.raises(HTTPException) as denied:
        asyncio.run(main.flash_bridge_control(Request({"type": "http", "headers": []}), "pause"))
    assert denied.value.status_code == 403
    paused.set()
    assert asyncio.run(main.flash_bridge_control(request, "resume")) == {"paused": False}


def test_pause_acknowledges_only_after_port_closes(monkeypatch):
    from app import serial_bridge

    opened, closed = threading.Event(), threading.Event()

    class Port:
        in_waiting = 0

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            opened.set()
            return self

        def __exit__(self, *args):
            closed.set()

        def write(self, data):
            pass

        def read(self, size):
            threading.Event().wait(0.002)
            return b""

        def flush(self):
            pass

    monkeypatch.setattr(serial_bridge.serial, "Serial", Port)
    bridge = serial_bridge.SerialBridge("mock", lambda event: None)
    bridge.start()
    try:
        assert opened.wait(1)
        bridge.paused.set()
        assert bridge.released.wait(1)
        assert closed.is_set()
        with pytest.raises(ValueError, match="暂停"):
            bridge.send({"action": "send_sms"})
    finally:
        bridge.close()
