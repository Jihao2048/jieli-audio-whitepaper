#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Conversion service used by the GUI and the CLI.

Its single job is **decoding**: turning a JieLi container into a 16-bit PCM
WAV. Encoding is deliberately out of scope - producing these containers is
the vendor tool's responsibility, and only its encoder knows the proprietary
spectral coder that ``.f1a`` needs.

Supported inputs:

===========  ==========================================  ====================
Extension    Container                                   Backend
===========  ==========================================  ====================
``.f1a``     11-byte header + spectral bitstream         bundled decoder
``.f1b``     same family                                 bundled decoder
``.f1c``     same family                                 bundled decoder
``.a``       44-byte zero preamble + IMA ADPCM           pure Python
``.b``       same as ``.a``                              pure Python
``.e``       ``"WAV"`` + rate byte + IMA ADPCM           pure Python
``.ump3``    plain MPEG Layer III                        FFmpeg
===========  ==========================================  ====================
"""

from __future__ import annotations

import os
import subprocess
import wave
from dataclasses import dataclass
from typing import Callable, List, Optional

try:
    from . import common, f1a, abe, ump3, f1a_decoder
except ImportError:                        # executed as a top-level module
    import common                          # type: ignore
    import f1a                             # type: ignore
    import abe                             # type: ignore
    import ump3                            # type: ignore
    import f1a_decoder                     # type: ignore


class ConvertError(RuntimeError):
    """A conversion failed for a reportable reason."""


#: Containers this toolkit can read.
DECODE_EXT = (".f1a", ".f1b", ".f1c", ".a", ".b", ".e", ".ump3")


# --------------------------------------------------------------------------
# Task description
# --------------------------------------------------------------------------

@dataclass
class TaskOptions:
    """Everything the user can choose in the GUI."""
    out_dir: str = "."
    rate: Optional[int] = None         # None = per-format default
    raw_delay: bool = False            # keep codec warm-up samples
    overwrite: bool = True


@dataclass
class TaskResult:
    source: str
    output: Optional[str] = None
    ok: bool = False
    message: str = ""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def probe_wav(path: str) -> dict:
    """Read basic properties of a WAV file."""
    with wave.open(path, "rb") as w:
        return {
            "frames": w.getnframes(),
            "rate": w.getframerate(),
            "channels": w.getnchannels(),
            "width": w.getsampwidth(),
        }


def output_path_for(src: str, out_dir: str) -> str:
    """Where the WAV for ``src`` should be written."""
    stem = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(out_dir, stem + ".wav")


# --------------------------------------------------------------------------
# Decode
# --------------------------------------------------------------------------

def decode_file(path: str, opts: TaskOptions):
    """Decode one container to linear PCM."""
    with open(path, "rb") as fh:
        head = fh.read(64)

    kind = common.sniff_format(head, os.path.basename(path))

    if kind in ("f1a", "f1b", "f1c"):
        return f1a_decoder.decode_file(path, sample_rate=opts.rate)

    if kind in ("a", "b", "e"):
        pcm = abe.decode_file(path)
        if opts.rate:
            pcm.sample_rate = opts.rate
        if not opts.raw_delay:
            pcm = abe.trim_alignment(pcm, kind)
        return pcm

    if kind == "ump3":
        pcm = ump3.decode_file(path)
        if opts.rate:
            pcm.sample_rate = opts.rate
        return pcm

    raise ConvertError(
        "无法识别的格式：文件开头是 %s。\n"
        "支持的格式：.f1a .f1b .f1c .a .b .e .ump3"
        % head[:8].hex())


def run_task(path: str, opts: TaskOptions,
             dst: Optional[str] = None) -> TaskResult:
    """Decode one file and describe the outcome.

    ``dst`` overrides the derived output path, which callers use to resolve
    stem collisions (``voice.a`` and ``voice.e`` both wanting ``voice.wav``).
    """
    try:
        if dst is None:
            dst = output_path_for(path, opts.out_dir)
        if not opts.overwrite and os.path.exists(dst):
            raise ConvertError("目标文件已存在：%s" % os.path.basename(dst))

        pcm = decode_file(path, opts)
        common.write_wav(dst, pcm)

        return TaskResult(
            path, dst, True,
            "%d Hz, %d 帧, %.2f 秒" % (pcm.sample_rate, pcm.frames,
                                       pcm.duration))
    except Exception as exc:                       # noqa: BLE001
        return TaskResult(path, None, False, str(exc))
