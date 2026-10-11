"""Incremental parser for the existing firmware's JSON framing."""

import json

START = b"SMS_START:"
END = b":SMS_END"


class FrameParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        messages = []
        while True:
            start = self.buffer.find(START)
            if start < 0:
                self.buffer = self.buffer[-(len(START) - 1) :]
                break
            if start:
                del self.buffer[:start]
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
