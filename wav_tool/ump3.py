#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UMP3 container support.

A ``.ump3`` file is a **plain MPEG Audio Layer III elementary stream**,
optionally prefixed with a standard ID3v2 tag. There is no JieLi wrapper,
no proprietary header and no encryption - only the filename extension
differs from ``.mp3``.

Confirmed by encoding the same source with the same parameters and writing
it once as ``.ump3`` and once as ``.mp3``: the two files are byte-identical

    smp_16k_64k.ump3  sha256=07c338a1f8eb2cedb3f945b59902127d0d687cbc...
    ctl_16k_64k.mp3   sha256=07c338a1f8eb2cedb3f945b59902127d0d687cbc...
    byte-identical: True

Decoding therefore only requires an MP3 decoder. This backend prefers a
pure-Python/installed decoder when one is available and otherwise falls
back to the FFmpeg build bundled under ``tools/``.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import wave
from typing import List, Optional, Tuple

try:
    from .common import PcmData, read_wav, resource_path
except ImportError:                        # executed as a top-level module
    from common import PcmData, read_wav, resource_path   # type: ignore


class Ump3Error(RuntimeError):
    """Raised when a UMP3 stream cannot be decoded."""


#: Bundled decoders, in order of preference.
_BUNDLED_FFMPEG = ("ump3_ffmpeg.exe", "ffmpeg.exe")


# --------------------------------------------------------------------------
# MP3 structure inspection
# --------------------------------------------------------------------------

#: MPEG audio version IDs, indexed by the 2-bit field in the frame header.
_VERSION = {0: 2.5, 2: 2.0, 3: 1.0}

#: Layer IDs, indexed by the 2-bit field.
_LAYER = {1: 3, 2: 2, 3: 1}

#: Sample rates, keyed by (version id, rate index).
_SAMPLE_RATES = {
    0: (11025, 12000, 8000),      # MPEG 2.5
    2: (22050, 24000, 16000),     # MPEG 2
    3: (44100, 48000, 32000),     # MPEG 1
}

#: Bitrate tables (kbps) for Layer III, keyed by version id.
_BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192,
                   224, 256, 320)
_BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112,
                   128, 144, 160)


def has_id3(data: bytes) -> bool:
    """True when the stream starts with an ID3v2 tag."""
    return data[:3] == b"ID3"


def id3_size(data: bytes) -> int:
    """Total size of a leading ID3v2 tag, or 0 when there is none."""
    if not has_id3(data) or len(data) < 10:
        return 0
    # Syncsafe 28-bit integer.
    size = 0
    for b in data[6:10]:
        size = (size << 7) | (b & 0x7F)
    flags = data[5]
    total = 10 + size
    if flags & 0x10:                      # footer present
        total += 10
    return total


def find_first_frame(data: bytes) -> int:
    """Offset of the first MPEG audio frame, or -1 when none is found."""
    start = id3_size(data)
    for i in range(start, min(len(data) - 4, start + 0x20000)):
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            hdr = _parse_frame_header(data, i)
            if hdr is not None:
                return i
    return -1


def _parse_frame_header(data: bytes, off: int) -> Optional[Tuple]:
    """Parse a 4-byte MPEG audio frame header.

    Returns ``(version, layer, bitrate_kbps, sample_rate, padding, channels,
    frame_length)`` or ``None`` when the bytes are not a valid header.
    """
    if off + 4 > len(data):
        return None
    b0, b1, b2, b3 = data[off], data[off + 1], data[off + 2], data[off + 3]
    if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
        return None

    version_id = (b1 >> 3) & 0x03
    layer_id = (b1 >> 1) & 0x03
    if version_id == 1 or layer_id == 0:
        return None                        # reserved

    version = _VERSION[version_id]
    layer = _LAYER[layer_id]

    bitrate_idx = (b2 >> 4) & 0x0F
    rate_idx = (b2 >> 2) & 0x03
    padding = (b2 >> 1) & 0x01
    if bitrate_idx in (0, 15) or rate_idx == 3:
        return None

    bitrate = (_BITRATES_V1_L3 if version == 1.0 else _BITRATES_V2_L3)[bitrate_idx]
    sample_rate = _SAMPLE_RATES[version_id][rate_idx]
    channels = 1 if ((b3 >> 6) & 0x03) == 3 else 2

    if layer == 1:
        frame_len = (12 * bitrate * 1000 // sample_rate + padding) * 4
    elif version == 1.0:
        frame_len = 144 * bitrate * 1000 // sample_rate + padding
    else:
        frame_len = 72 * bitrate * 1000 // sample_rate + padding

    return (version, layer, bitrate, sample_rate, padding, channels, frame_len)


def describe(data: bytes) -> dict:
    """Summarise an MP3 stream: first frame parameters and frame count."""
    off = find_first_frame(data)
    if off < 0:
        return {"valid": False, "id3": has_id3(data), "id3_size": id3_size(data)}

    info = _parse_frame_header(data, off)
    if info is None:
        return {"valid": False, "id3": has_id3(data), "id3_size": id3_size(data)}

    version, layer, bitrate, rate, padding, channels, frame_len = info

    # Walk the frames.
    count = 0
    pos = off
    while pos + 4 <= len(data):
        h = _parse_frame_header(data, pos)
        if h is None:
            break
        pos += h[6]
        count += 1

    return {
        "valid": True,
        "id3": has_id3(data),
        "id3_size": id3_size(data),
        "first_frame_offset": off,
        "mpeg_version": version,
        "layer": layer,
        "bitrate_kbps": bitrate,
        "sample_rate": rate,
        "channels": channels,
        "frame_count": count,
        "trailing_bytes": len(data) - pos,
    }


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------

def _bundled_ffmpeg() -> Optional[str]:
    """Path to a bundled FFmpeg binary, if present."""
    for name in _BUNDLED_FFMPEG:
        p = resource_path("tools", name)
        if os.path.isfile(p):
            return p
    return None


def _decode_with_ffmpeg(exe: str, path: str, timeout: int = 300) -> PcmData:
    """Decode via an FFmpeg binary, normalising the result to 16-bit PCM.

    Only ``-y``, ``-i`` and the output path are passed. Forcing the codec
    with ``-acodec pcm_s16le`` is deliberately avoided: a plain ``.wav``
    target already selects 16-bit PCM, and the extra option upsets input
    probing on the 2015 build that ships with the vendor tool.

    The scratch WAV is written **next to the input** rather than into the
    system temp directory. ffmpeg is an external process, and environments
    that confine file writes to the workspace can refuse it access to
    ``%TEMP%``; the input's own directory is always writable in practice.
    """
    out = os.path.join(os.path.dirname(os.path.abspath(path)),
                       ".jl_ump3_%d.wav" % os.getpid())
    try:
        proc = subprocess.run(
            [exe, "-y", "-i", os.path.abspath(path), out],
            cwd=os.path.dirname(os.path.abspath(path)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        if not os.path.isfile(out) or os.path.getsize(out) == 0:
            detail = proc.stdout.decode("utf-8", "replace").strip()
            raise Ump3Error(
                "ffmpeg produced no output (exit %s): %s"
                % (proc.returncode, detail[-400:]))
        return read_wav(out)
    finally:
        try:
            if os.path.isfile(out):
                os.remove(out)
        except OSError:
            pass


def _decode_with_pydub(path: str) -> Optional[PcmData]:
    """Try a locally installed pydub/ffmpeg combination."""
    try:
        from pydub import AudioSegment           # type: ignore
    except Exception:
        return None

    ff = find_ffmpeg()
    if ff:
        try:
            AudioSegment.converter = ff
            AudioSegment.ffmpeg = ff
        except Exception:
            pass

    try:
        seg = AudioSegment.from_file(path)
    except Exception:
        return None

    seg = seg.set_channels(1).set_sample_width(2)
    return PcmData(list(seg.get_array_of_samples()), seg.frame_rate, 1)


def find_ffmpeg() -> Optional[str]:
    """Locate an MP3-capable FFmpeg, cheapest option first.

    A system-wide ffmpeg on PATH is preferred over the bundled build so the
    toolkit does not have to ship a ~34 MB binary. Setting the environment
    variable ``JL_FFMPEG`` overrides both.
    """
    override = os.environ.get("JL_FFMPEG")
    if override and os.path.isfile(override):
        return override

    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff

    return _bundled_ffmpeg()


def decode_file(path: str, timeout: int = 300) -> PcmData:
    """Decode a ``.ump3`` (MP3) file to linear PCM."""
    with open(path, "rb") as fh:
        head = fh.read(4096)

    if find_first_frame(head) < 0 and not has_id3(head):
        raise Ump3Error("%s does not look like an MP3 stream"
                        % os.path.basename(path))

    ff = find_ffmpeg()
    if ff:
        return _decode_with_ffmpeg(ff, path, timeout=timeout)

    pcm = _decode_with_pydub(path)
    if pcm is not None:
        return pcm

    raise Ump3Error(
        "找不到 MP3 解码器。请安装 ffmpeg 并加入 PATH，"
        "或把 ffmpeg.exe 放到 tools/ 目录，"
        "或设置环境变量 JL_FFMPEG 指向 ffmpeg.exe。")
