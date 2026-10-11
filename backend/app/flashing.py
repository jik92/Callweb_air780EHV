"""Prepare the pinned EHV firmware; never open a hardware port here."""

import base64
import hashlib
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

PROJECT = Path(os.getenv("AIR780_PROJECT_DIR", str(Path(__file__).resolve().parents[2])))
FIRMWARE = PROJECT / "firmware/LuatOS-SoC_V2052_Air780EHV_13.soc"


class FlashRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["full", "script"] = "script"


def image(name, address, identifier, data):
    return {
        "name": name,
        "address": address,
        "identifier": identifier,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": base64.b64encode(data).decode(),
    }


def parse_binpkg(data: bytes):
    if len(data) < 0x1D8 or data[0x38:0x3F] != b"pkgmode":
        raise ValueError("不支持的 binpkg 格式")
    chip = data[0x190:0x1A0].split(b"\0")[0].decode()
    if not chip.startswith("EC718HM"):
        raise ValueError("固件芯片与 Air780EHV 不匹配")
    entries = []
    cursor = 0x1D8
    while cursor < len(data):
        if cursor + 364 > len(data):
            raise ValueError("固件分区信息不完整")
        meta = data[cursor : cursor + 364]
        name = meta[:64].split(b"\0")[0].decode()
        address, capacity, _, size = struct.unpack_from("<IIII", meta, 64)
        kind = meta[336:352].split(b"\0")[0].decode()
        cursor += 364
        if not size or size > capacity or cursor + size > len(data):
            raise ValueError("固件分区长度无效")
        identifiers = {"BL": 0x4F424D49, "AP": 0x444B4249, "CP": 0x43504249}
        if kind not in identifiers or not 0x800000 <= address < 0x1000000:
            raise ValueError("不支持的固件分区")
        if kind == "CP":
            address -= 0x800000
        entries.append(image(name, address, identifiers[kind], data[cursor : cursor + size]))
        cursor += size
    if not entries:
        raise ValueError("固件没有可刷写的分区")
    return chip, entries


def prepare(mode: str):
    cli = PROJECT / "tools/luatos-cli"
    with tempfile.TemporaryDirectory(prefix="air780-webflash-") as temp:
        directory = Path(temp)
        subprocess.run(
            [str(cli), "soc", "unpack", str(FIRMWARE), "-o", str(directory / "soc")],
            check=True,
            capture_output=True,
            timeout=60,
        )
        info = json.loads((directory / "soc/info.json").read_text())
        chip, entries = parse_binpkg((directory / "soc/luatos.binpkg").read_bytes())
        subprocess.run(
            [
                str(cli),
                "build",
                "filesystem",
                "--src",
                str(PROJECT / "lua"),
                "--luac",
                "--output",
                str(directory / "script.bin"),
                "--max-size-kb",
                "512",
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        script = (directory / "script.bin").read_bytes()
        address = int(info["download"]["script_addr"], 16) + 0x800000
        if not script or len(script) > 512 * 1024 or address + len(script) > 0x1000000:
            raise ValueError("脚本分区长度无效")
        script_entry = image("script", address, 0x464C5849, script)
        agent = (PROJECT / "backend/resources/ec718m_usb.bin").read_bytes()
        if (
            len(agent) != 40696
            or hashlib.sha256(agent).hexdigest()
            != "92a596c9a2f339e1427ec6f688115846983e99f8f7a2f835457f762fa6077a51"
        ):
            raise ValueError("USB 下载代理校验失败")
        return {
            "firmware": FIRMWARE.name,
            "chip": chip,
            "mode": mode,
            "baud": int(info["download"]["force_br"]),
            "agent": image("AgentBoot", 0, 0x4F424D49, agent),
            "entries": [*entries, script_entry] if mode == "full" else [script_entry],
        }
