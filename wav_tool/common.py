#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JieLi (AD14N / SH50) audio format converter - common utilities.

This module holds the shared helpers used by the format handlers:
WAV reading/writing, format sniffing and small binary helpers.

Author: reverse-engineered from the vendor tool bundle
        "音频文件转换工具_1.2.2.exe" (media_trans.exe).
"""

from __future__ import annotations

import os
import struct
import wave
from dataclasses import dataclass, field
from typing import List, Optional


# --------------------------------------------------------------------------
# WAV I/O
# --------------------------------------------------------------------------

@dataclass
class PcmData:
    """Decoded linear PCM audio."""
    samples: List[int]          # signed 16-bit sample values
    sample_rate: int
    channels: int = 1

    @property
    def frames(self) -> int:
        return len(self.samples) // self.channels

    @property
    def duration(self) -> float:
        return self.frames / float(self.sample_rate) if self.sample_rate else 0.0


def read_wav(path: str) -> PcmData:
    """Read a RIFF/WAVE file and return 16-bit linear PCM."""
    with wave.open(path, "rb") as w:
        n_frames = w.getnframes()
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(n_frames)

    if width == 1:
        # 8-bit WAV is unsigned
        vals = [(b - 128) << 8 for b in raw]
    elif width == 2:
        vals = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
    elif width == 4:
        vals = [v >> 16 for v in struct.unpack("<%di" % (len(raw) // 4), raw)]
    else:
        raise ValueError("unsupported sample width: %d bytes" % width)

    return PcmData(vals, rate, channels)


def write_wav(path: str, pcm: PcmData) -> None:
    """Write 16-bit linear PCM to a RIFF/WAVE file."""
    data = struct.pack("<%dh" % len(pcm.samples), *pcm.samples)
    with wave.open(path, "wb") as w:
        w.setnchannels(pcm.channels)
        w.setsampwidth(2)
        w.setframerate(pcm.sample_rate)
        w.writeframes(data)


def wav_header(sample_rate: int, data_size: int, channels: int = 1) -> bytes:
    """Build a canonical 44-byte RIFF/WAVE header for 16-bit PCM."""
    byte_rate = sample_rate * channels * 2
    return b"".join([
        b"RIFF", struct.pack("<I", data_size + 36), b"WAVE",
        b"fmt ", struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                             byte_rate, channels * 2, 16),
        b"data", struct.pack("<I", data_size),
    ])


# --------------------------------------------------------------------------
# Format detection
# --------------------------------------------------------------------------

#: F1A family sync byte, always the first byte of the 11-byte header.
F1A_SYNC = 0xCB

#: Fixed bytes 2..10 of the F1A family header (measured on real samples).
F1A_HEADER_TAIL = bytes([0xBF, 0xF0, 0x06, 0x67, 0x72, 0xAA, 0x66, 0x17, 0xF7])

#: F1A header length in bytes.
F1A_HEADER_SIZE = 11

#: Samples per F1A codec frame.
F1A_FRAME_SAMPLES = 256


def sniff_format(data: bytes, filename: str = "") -> Optional[str]:
    """Identify a JieLi audio container from its leading bytes.

    ``filename`` is an optional hint: the ``.a`` / ``.b`` containers carry no
    magic at all (they start with a 44-byte zero preamble), so the extension
    is the only reliable discriminator for them.

    Returns one of: 'f1a', 'f1b', 'f1c', 'ump3', 'a', 'b', 'e', or None.
    """
    if len(data) < 4:
        return None

    # --- F1A family: 0xCB sync + fixed 9-byte codec fingerprint at 2..10 ---
    if len(data) >= F1A_HEADER_SIZE and data[0] == F1A_SYNC:
        if data[2:11] == F1A_HEADER_TAIL:
            mode = data[1]
            # The mode byte selects the variant and the sample rate.
            return F1A_VARIANTS.get(mode, "f1a")

    # --- .e family: ASCII "WAV" + rate selector byte ---
    # The code is 0x00..0x04 for the rates that have a dedicated value and
    # 0x77 for "no dedicated code" (the encoder writes this for 44100, 22050,
    # 11025, 48000 and for a zero rate argument). The sentinel is a normal
    # value, so the whole byte range is accepted rather than a whitelist.
    if data[:3] == b"WAV":
        return "e"

    # --- UMP3 / MP3 ---
    if data[:3] == b"ID3":
        return "ump3"
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "ump3"

    # --- .a / .b: no magic; a 44-byte zero preamble followed by data ---
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if ext in ("a", "b") and len(data) > 44 and not any(data[:44]):
        return ext

    # --- headerless F1A payload: no magic at all, only the extension hints ---
    # ``add_f1a_head.exe`` exists precisely because these occur in practice:
    # the coded payload is stored without its 11-byte header. The vendor
    # decoder happily decodes such a file (it derives the length from the
    # file size and does not validate the header), so accept it here too.
    if ext in ("f1a", "f1b", "f1c") and len(data) >= 64:
        return ext

    return None


#: Mode byte / header byte 1 -> variant name.
#: Byte 1 bit 0 signals "contains audio"; the remaining bits carry the rate.
F1A_VARIANTS = {
    0x80: "f1a",   # silence / skip-only stream
    0x81: "f1a",   # standard audio, 16000 Hz
    0x82: "f1a",
    0x83: "f1a",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def hexdump(data: bytes, count: int = 64, base: int = 0) -> str:
    """Return a classic hex+ASCII dump string."""
    lines = []
    for off in range(0, min(count, len(data)), 16):
        row = data[off:off + 16]
        hexpart = " ".join("%02x" % b for b in row)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append("%08x  %-47s  %s" % (base + off, hexpart, asciipart))
    return "\n".join(lines)


def resource_path(*parts: str) -> str:
    """Path to a bundled helper binary shipped next to this package."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)
