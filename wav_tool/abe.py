#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JieLi A / B / E container support (IMA-ADPCM 4-bit).

These are the "first family" formats from the vendor manual: lowest
compression effort, smallest memory footprint, intended for voice and
simple music playback.

Format facts, all verified experimentally
-----------------------------------------

**The codec is textbook IMA ADPCM.** There is no JieLi-specific variant:

* 4 bits per sample
* **high nibble first** (``nib0 = byte >> 4``)
* the standard 89-entry step table
* the standard index-adjust table, indexed with ``nibble & 7``
* initial predictor 0, initial step index 0
* **no per-block header, no periodic reset, no amplitude rescaling**

Containers
----------

===========  ==========================================  ==========================
Extension    Header                                      Decoded sample i maps to
===========  ==========================================  ==========================
``.a``      44 bytes, entirely zero                      input sample ``i + 234``
``.b``      44 bytes, entirely zero (same as ``.a``)     input sample ``i + 234``
``.e``      4 bytes: ``"WAV"`` + rate code byte          input sample ``i - 22``
===========  ==========================================  ==========================

The ``.a`` preamble is provably empty: every input ever tried (silence, DC,
sine, ramp, full-scale noise, step, counter) produced 44 zero bytes, and an
impulse swept across all 8000 input positions never touched it.

Rate codes for ``.e``: 8000 -> 0x00, 16000 -> 0x01, 32000 -> 0x02,
12000 -> 0x03, 24000 -> 0x04; other rates use the 0x77 sentinel.

Evidence of correctness
-----------------------

ADPCM is deterministic, so decoding a vendor-produced payload and re-encoding
it must reproduce the payload byte for byte. It does: re-encode equality is
**1.000000** on every sample tested (counter, ramp, sine, noise, step,
``.a`` and ``.e`` alike). That pins down the nibble order, both tables and
the initial state simultaneously - a far stronger check than correlation.

The encoder's 234-sample warm-up was located by sweeping a single impulse
across the input and recording the first differing payload byte; the measured
offsets follow ``(k - 236) // 2 + 1`` exactly, with no discontinuity, which
also rules out any per-block reset.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional

try:
    from .common import PcmData
except ImportError:                        # executed as a top-level module
    from common import PcmData             # type: ignore


# --------------------------------------------------------------------------
# IMA ADPCM tables (standard, as published in the IMA/DVI reference)
# --------------------------------------------------------------------------

STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707,
    1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)

INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)

MAX_INDEX = 88

#: ASCII magic of the ``.e`` container.
E_MAGIC = b"WAV"

#: Rate code byte of the ``.e`` container -> sample rate.
E_RATES = {
    0x00: 8000,
    0x01: 16000,
    0x02: 32000,
    0x03: 12000,
    0x04: 24000,
}

#: Sentinel written for rates that have no dedicated code.
E_RATE_SENTINEL = 0x77

#: Rate assumed when the container carries the sentinel.
#:
#: The encoder writes 0x77 for 44100 / 22050 / 11025 / 48000 and also when it
#: is given a zero rate argument, so the sentinel alone does not identify the
#: rate. 16000 is the most common choice for this family and matches the
#: default the vendor decoder stamps into its own WAV output; callers that
#: know better should pass an explicit rate.
E_RATE_FALLBACK = 16000

#: Preamble length of the ``.a`` / ``.b`` container.
AB_PREAMBLE = 44

#: Number of input samples the encoder discards as warm-up (``.a`` / ``.b``).
ENCODER_WARMUP_AB = 234

#: ``.e`` codes 256 extra warm-up nibbles, i.e. it starts 22 samples early.
ENCODER_WARMUP_E = -22


@dataclass
class AbeHeader:
    """Parsed header of a `.a` / `.b` / `.e` file."""
    kind: str                 # 'a', 'b' or 'e'
    data_offset: int          # where the nibble stream starts
    sample_rate: int
    magic: bytes = b""
    rate_code: Optional[int] = None

    def describe(self) -> str:
        parts = [".%s container" % self.kind,
                 "payload at 0x%X" % self.data_offset,
                 "%d Hz" % self.sample_rate]
        if self.magic:
            parts.append("magic %s" % self.magic.hex())
        if self.rate_code is not None:
            parts.append("rate code 0x%02X" % self.rate_code)
        return ", ".join(parts)


def header_size(data: bytes) -> int:
    """Header length of an A/B/E container (4 for `.e`, else 44)."""
    return 4 if data[:3] == E_MAGIC else AB_PREAMBLE


def parse_header(data: bytes, rate: Optional[int] = None) -> AbeHeader:
    """Identify and parse a `.a` / `.b` / `.e` header.

    ``rate`` overrides the rate taken from the container, which is needed for
    ``.e`` files that carry the ``0x77`` sentinel.
    """
    if len(data) >= 4 and data[:3] == E_MAGIC:
        code = data[3]
        resolved = rate if rate else E_RATES.get(code, E_RATE_FALLBACK)
        return AbeHeader("e", 4, resolved, bytes(data[:4]), code)

    if len(data) > AB_PREAMBLE:
        return AbeHeader("a", AB_PREAMBLE, rate or 8000, b"", None)

    raise ValueError("not an A/B/E container")


# --------------------------------------------------------------------------
# Codec
# --------------------------------------------------------------------------

def decode_payload(body: bytes,
                   predictor: int = 0,
                   index: int = 0) -> List[int]:
    """Decode a raw IMA ADPCM nibble stream (high nibble first)."""
    out: List[int] = []
    append = out.append
    step_table = STEP_TABLE
    index_table = INDEX_TABLE

    for byte in body:
        for nibble in (byte >> 4, byte & 0x0F):
            step = step_table[index]

            diff = step >> 3
            if nibble & 1:
                diff += step >> 2
            if nibble & 2:
                diff += step >> 1
            if nibble & 4:
                diff += step
            if nibble & 8:
                diff = -diff

            predictor += diff
            if predictor > 32767:
                predictor = 32767
            elif predictor < -32768:
                predictor = -32768

            # Note: the index table is indexed with the low three bits only.
            index += index_table[nibble & 7]
            if index < 0:
                index = 0
            elif index > MAX_INDEX:
                index = MAX_INDEX

            append(predictor)

    return out


def encode_payload(samples: List[int],
                   predictor: int = 0,
                   index: int = 0) -> bytes:
    """Encode samples to an IMA ADPCM nibble stream (high nibble first).

    This is the inverse of :func:`decode_payload` and is used by the self
    test to prove the decoder is byte-exact.
    """
    nibbles: List[int] = []
    for sample in samples:
        step = STEP_TABLE[index]

        diff = sample - predictor
        nibble = 8 if diff < 0 else 0
        if diff < 0:
            diff = -diff

        delta = step >> 3
        if diff >= step:
            nibble |= 4
            delta += step
            diff -= step
        if diff >= step >> 1:
            nibble |= 2
            delta += step >> 1
            diff -= step >> 1
        if diff >= step >> 2:
            nibble |= 1
            delta += step >> 2
            diff -= step >> 2

        predictor = predictor - delta if (nibble & 8) else predictor + delta
        if predictor > 32767:
            predictor = 32767
        elif predictor < -32768:
            predictor = -32768

        index = max(0, min(MAX_INDEX, index + INDEX_TABLE[nibble & 7]))
        nibbles.append(nibble)

    out = bytearray()
    for i in range(0, len(nibbles) - 1, 2):
        out.append(((nibbles[i] & 0x0F) << 4) | (nibbles[i + 1] & 0x0F))
    return bytes(out)


# --------------------------------------------------------------------------
# Container level
# --------------------------------------------------------------------------

def decode_bytes(data: bytes, kind: Optional[str] = None) -> PcmData:
    """Decode an in-memory `.a` / `.b` / `.e` stream to PCM."""
    hdr = parse_header(data)
    if kind is not None:
        hdr.kind = kind

    body = data[hdr.data_offset:]
    samples = decode_payload(body)
    return PcmData(samples, hdr.sample_rate, 1)


def decode_file(path: str) -> PcmData:
    """Decode a `.a` / `.b` / `.e` file to PCM."""
    with open(path, "rb") as fh:
        data = fh.read()
    return decode_bytes(data)


def trim_alignment(pcm: PcmData, kind: str) -> PcmData:
    """Shift the sample list so it lines up with the original source audio.

    The encoder consumes a fixed warm-up before it starts writing, so the
    decoded stream is offset relative to the input:

    * ``.a`` / ``.b``: decoded sample ``i`` is input sample ``i + 234``,
      so the first 234 input samples are simply absent.
    * ``.e``: the stream starts 22 samples *before* the input, i.e. it
      carries 22 samples of pre-roll that the caller may want to drop.

    Dropping the pre-roll makes the output start at the original first
    sample, which is what a user converting to WAV normally expects.
    """
    if kind == "e":
        if len(pcm.samples) > ENCODER_WARMUP_E and ENCODER_WARMUP_E < 0:
            return PcmData(pcm.samples[-ENCODER_WARMUP_E:],
                           pcm.sample_rate, pcm.channels)
    return pcm
