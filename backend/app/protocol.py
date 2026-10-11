"""Incremental parser for the existing firmware's JSON framing."""

import json
import struct

AUDIO_START = b"AUD_START:"
AUDIO_END = b":AUD_END"
START = b"SMS_START:"
END = b":SMS_END"


class FrameParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        messages = []
        while True:
            positions = [(self.buffer.find(marker), marker) for marker in (START, AUDIO_START)]
            positions = [(offset, marker) for offset, marker in positions if offset >= 0]
            if not positions:
                self.buffer = self.buffer[-9:]
                break
            start, marker = min(positions, key=lambda item: item[0])
            if start:
                del self.buffer[:start]
            if marker == AUDIO_START:
                header = len(AUDIO_START) + 36 + 8
                if len(self.buffer) < header:
                    break
                length, rate, sequence = struct.unpack_from(
                    "<HHI", self.buffer, len(AUDIO_START) + 36
                )
                if length > 4096 or length % 2 or rate not in (8000, 16000):
                    del self.buffer[: len(AUDIO_START)]
                    continue
                size = header + length + len(AUDIO_END)
                if len(self.buffer) < size:
                    break
                if self.buffer[header + length : size] != AUDIO_END:
                    del self.buffer[: len(AUDIO_START)]
                    continue
                request_id = bytes(self.buffer[len(AUDIO_START) : len(AUDIO_START) + 36]).decode(
                    "ascii", errors="replace"
                )
                messages.append(
                    {
                        "type": "call_pcm",
                        "request_id": request_id,
                        "sample_rate": rate,
                        "sequence": sequence,
                        "pcm": bytes(self.buffer[header : header + length]),
                    }
                )
                del self.buffer[:size]
                continue
            end = self.buffer.find(END, len(START))
            if end < 0:
                if len(self.buffer) > 256_000:
                    del self.buffer[: len(START)]
                    continue
                break
            payload = bytes(self.buffer[len(START) : end])
            del self.buffer[: end + len(END)]
            try:
                item = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(item, dict):
                messages.append(item)
        return messages


def encode_command(command: dict) -> bytes:
    return ("CMD_START:" + json.dumps(command, ensure_ascii=False) + ":CMD_END\r\n").encode()


def encode_audio_input(request_id: str, sequence: int, pcm: bytes) -> bytes:
    return (
        b"PCM_START:"
        + request_id.encode("ascii")
        + struct.pack("<HI", len(pcm), sequence)
        + pcm
        + b":PCM_END"
    )
