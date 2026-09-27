#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JieLi F1A / F1B / F1C container parser.

The F1A family header is 11 bytes. Byte 0 is a fixed sync value (0xCB) and
bytes 2..10 are a fixed 9-byte codec fingerprint; only byte 1 varies between
files in the samples that were measured.

Everything in this module is derived from files produced by the vendor
encoder and from the vendor decoder's observable behaviour. See
_work/re/REPORT.md for the full evidence trail.

Layout (measured on four independently encoded samples):

    off  size  value                meaning
    ---  ----  -------------------  ----------------------------------------
    0x00   1   0xCB                 sync
    0x01   1   0x80 | 0x81          mode / flags (bit0 = audio present)
    0x02   9   BF F0 06 67 72 AA    fixed codec fingerprint
               66 17 F7
    0x0B   ..                       MSB-first coded bitstream

There is no stored length, no frame count and no checksum in the header; the
reference decoder derives the output duration from the file size.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from .common import F1A_HEADER_SIZE, F1A_HEADER_TAIL, F1A_SYNC, F1A_FRAME_SAMPLES


#: Byte 1 bit 0 - set when the stream carries real (non-silent) audio.
MODE_HAS_AUDIO = 0x01

#: Byte 1 bit 7 - always set in every measured sample.
MODE_HIGH_BIT = 0x80

#: Default output sample rate for the F1A family.
#:
#: The vendor decoder always writes 16000 into its WAV template, but that
#: value is a constant in the decoder binary rather than something it reads
#: from the stream, so it cannot be trusted. The real rate was established by
#: listening tests plus an independent tempo measurement:
#:
#:   * The track decodes to 769664 samples. At 8000 Hz that is 96.21 s, and
#:     the energy-envelope autocorrelation peaks at 130.4 BPM - squarely in
#:     the 125-135 BPM band of the genre. At 16000 Hz the same audio plays at
#:     260 BPM, which is implausible for the material.
#:   * The implied bitrate at 8000 Hz is 7943 bit/s, a normal target for a
#:     low-rate 8 kHz music stream. F1A output is VBR (measured 279 to
#:     23250 bit/s on generated samples), so 16 kbps is only an *input* cap
#:     on the WMA the encoder accepts and does not pin the output rate.
F1A_DEFAULT_SAMPLE_RATE = 8000


@dataclass
class F1AHeader:
    """Parsed 11-byte F1A family header."""
    raw: bytes
    sync: int
    mode: int
    fingerprint: bytes

    @property
    def has_audio(self) -> bool:
        return bool(self.mode & MODE_HAS_AUDIO)

    @property
    def variant(self) -> str:
        """Best-effort variant name for this header."""
        return "f1a"

    @property
    def sample_rate(self) -> int:
        """Sample rate of the coded stream.

        The header carries no sample-rate field that could be isolated: every
        file the vendor encoder can produce (8000 or 16000 Hz WMA input) has
        byte-identical header bytes 2..10, and the only varying byte is the
        mode byte. The vendor decoder compounds this by always stamping 16000
        into its WAV template regardless of the stream.

        The rate is therefore taken from :data:`F1A_DEFAULT_SAMPLE_RATE`,
        which was determined by listening plus an independent tempo check;
        see the comment on that constant. Pass ``rate`` to override it when a
        specific file is known to differ.
        """
        return F1A_DEFAULT_SAMPLE_RATE

    def describe(self) -> str:
        return (
            "F1A header: sync=0x%02X mode=0x%02X audio=%s fingerprint=%s"
            % (self.sync, self.mode, self.has_audio, self.fingerprint.hex())
        )


class F1AFormatError(ValueError):
    """Raised when a file is not a recognisable F1A stream."""


def parse_header(data: bytes, strict: bool = True) -> F1AHeader:
    """Parse and validate the 11-byte F1A header.

    With ``strict=False`` only the sync byte is required, which keeps the
    parser usable on vendor files whose fingerprint differs from the samples
    that were measured.
    """
    if len(data) < F1A_HEADER_SIZE:
        raise F1AFormatError(
            "file too short for an F1A header (%d bytes)" % len(data))
    if data[0] != F1A_SYNC:
        raise F1AFormatError(
            "bad sync byte 0x%02X (expected 0x%02X)" % (data[0], F1A_SYNC))
    if strict and data[2:11] != F1A_HEADER_TAIL:
        raise F1AFormatError(
            "codec fingerprint mismatch: %s (expected %s)"
            % (data[2:11].hex(), F1A_HEADER_TAIL.hex()))
    return F1AHeader(
        raw=bytes(data[:F1A_HEADER_SIZE]),
        sync=data[0],
        mode=data[1],
        fingerprint=bytes(data[2:11]),
    )


def payload(data: bytes) -> bytes:
    """Return the coded bitstream that follows the header."""
    return data[F1A_HEADER_SIZE:]


def is_f1a(data: bytes) -> bool:
    """Cheap test for the F1A family container."""
    return (len(data) >= F1A_HEADER_SIZE
            and data[0] == F1A_SYNC
            and data[2:11] == F1A_HEADER_TAIL)


def estimate_frames(data: bytes, bits_per_frame: int = 256) -> int:
    """Rough frame-count estimate from the payload size.

    The reference decoder stops on whole 256-sample frames, so this is only a
    hint - the authoritative length is produced by the decoder itself.
    """
    bits = len(payload(data)) * 8
    return bits // max(1, bits_per_frame)


def strip_trailing_byte(data: bytes) -> bytes:
    """Drop the single trailing flush byte the vendor encoder appends.

    The encoder writes its stream to the scratch file ``tmpf1a.a`` and then
    copies it to the requested output while appending exactly one extra byte
    (measured: the output equals the scratch file plus one byte). Removing it
    recovers the raw coded stream.
    """
    return data[:-1] if data else data
