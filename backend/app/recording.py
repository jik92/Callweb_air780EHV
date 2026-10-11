"""Local, bounded WAV tracks; valid headers survive an interrupted service."""

import struct
import time
import wave
from pathlib import Path


class Recording:
    def __init__(self, directory: Path, rate: int):
        if rate not in (8000, 16000):
            raise ValueError("invalid recording rate")
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.rate = rate
        self.origin = time.monotonic()
        self.writers = {}
        self.files = {}
        self.frames = {"up": 0, "down": 0}
        try:
            for channel in self.frames:
                file = (directory / f"{channel}.wav").open("wb")
                self.files[channel] = file
                writer = wave.open(file, "wb")
                self.writers[channel] = writer
                writer.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                writer.writeframes(b"")
        except OSError:
            self.close()
            raise

    def write(self, channel: str, pcm: bytes):
        writer = self.writers[channel]
        count = len(pcm) // 2
        position = max(
            self.frames[channel], int((time.monotonic() - self.origin) * self.rate) - count
        )
        limit = self.rate * 100
        if position >= limit:
            return
        gap = position - self.frames[channel]
        if gap:
            writer.writeframes(b"\0\0" * gap)
        pcm = pcm[: (limit - position) * 2]
        writer.writeframes(pcm)
        self.files[channel].flush()
        self.frames[channel] = position + len(pcm) // 2

    def close(self):
        for writer in self.writers.values():
            writer.close()
        self.writers.clear()
        for file in self.files.values():
            file.close()
        self.files.clear()


def mix(directory: Path) -> Path | None:
    """Left = browser audio, right = device audio; gaps remain silence."""
    destination = directory / "both.wav"
    if destination.exists():
        return destination
    paths = [directory / "up.wav", directory / "down.wav"]
    if not all(path.exists() for path in paths):
        return None
    with wave.open(str(paths[0]), "rb") as up, wave.open(str(paths[1]), "rb") as down:
        if max(up.getnframes(), down.getnframes()) == 0:
            return None
        rate = up.getframerate()
        if rate != down.getframerate() or rate not in (8000, 16000):
            raise ValueError("recording rate mismatch")
        temporary = directory / "both.tmp"
        with wave.open(str(temporary), "wb") as output:
            output.setparams((2, 2, rate, 0, "NONE", "not compressed"))
            remaining = max(up.getnframes(), down.getnframes())
            while remaining:
                size = min(4096, remaining)
                left = up.readframes(size).ljust(size * 2, b"\0")
                right = down.readframes(size).ljust(size * 2, b"\0")
                stereo = bytearray(size * 4)
                for i, (a, b) in enumerate(
                    zip(
                        struct.iter_unpack("<h", left), struct.iter_unpack("<h", right), strict=True
                    )
                ):
                    struct.pack_into("<hh", stereo, i * 4, a[0], b[0])
                output.writeframes(stereo)
                remaining -= size
        temporary.replace(destination)
    return destination
