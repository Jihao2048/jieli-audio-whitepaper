#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JieLi F1A family decoder backend.

The F1A bitstream is a proprietary spectral codec (MDCT-like transform +
scale factors + spectral run-length coding). The vendor ships a working
decoder, ``testf1a_dec.exe``, which was extracted from the vendor tool
bundle and is redistributed next to this package under ``tools/``.

This backend drives that decoder to obtain a bit-exact result, and performs
all the container handling (validation, staging, output normalisation) in
Python so the CLI and the rest of the toolkit stay pure Python.

The decoder interface, established by experiment:

    testf1a_dec.exe <input.f1a> <output.wav>

It reads a file whose leading 11-byte header matches the F1A fingerprint and
writes a canonical 44-byte RIFF/WAVE, mono, 16-bit file. It also creates a
``rec.txt`` trace file in the working directory, which we clean up.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional

try:
    from . import f1a as f1a_mod
    from .common import PcmData, read_wav, resource_path
except ImportError:                        # executed as a top-level module
    import f1a as f1a_mod                    # type: ignore
    from common import PcmData, read_wav, resource_path   # type: ignore


class DecodeError(RuntimeError):
    """Raised when the vendor decoder cannot produce output."""


#: Name of the vendor decoder binary inside the bundled ``tools`` directory.
DECODER_NAME = "testf1a_dec.exe"

#: Runtime DLLs the decoder needs, copied alongside it.
REQUIRED_DLLS = ("msvcr100.dll", "msvcr100d.dll")


def decoder_path() -> str:
    """Absolute path of the bundled F1A decoder."""
    return resource_path("tools", DECODER_NAME)


def decoder_available() -> bool:
    """True when the bundled decoder binary is present."""
    return os.path.isfile(decoder_path())


def _stage_decoder(tmpdir: str) -> str:
    """Copy the decoder and its DLLs into a scratch directory.

    The vendor binaries load ``msvcr100.dll`` from their own directory, so
    they must be staged together to run reliably from an arbitrary CWD.
    """
    src_dir = os.path.dirname(decoder_path())
    target = os.path.join(tmpdir, DECODER_NAME)
    shutil.copy2(decoder_path(), target)

    for dll in REQUIRED_DLLS:
        candidate = os.path.join(src_dir, dll)
        if os.path.isfile(candidate):
            shutil.copy2(candidate, os.path.join(tmpdir, dll))

    # Windows resolves DLLs from the executable's directory, but also honour
    # a system-wide copy when the bundle does not carry one.
    return target


def decode_file(path: str, timeout: int = 180,
                sample_rate: Optional[int] = None) -> PcmData:
    """Decode an F1A/F1B/F1C file to linear PCM using the vendor decoder.

    Two container shapes are accepted:

    * **Headed** - the normal 11-byte header (``CB xx`` + codec fingerprint).
    * **Headerless** - a bare coded payload. ``add_f1a_head.exe`` exists to
      repair such files, but adding a header is *not* needed to decode one:
      the vendor decoder derives the output duration from the file size, and
      prepending a synthetic header would roughly double the reported
      duration. Headerless payloads are therefore passed through unchanged.

    The vendor decoder always stamps ``16000`` into the WAV template it
    writes, independent of the actual stream, and for headerless input it
    also writes a wrong byte-rate field (the sample rate instead of
    ``rate * 2``). Both are corrected here: only the sample *data* is taken
    from the decoder and the returned :class:`PcmData` carries ``sample_rate``,
    defaulting to :data:`f1a.F1A_DEFAULT_SAMPLE_RATE`.
    """
    with open(path, "rb") as fh:
        data = fh.read()

    if not decoder_available():
        raise DecodeError(
            "bundled F1A decoder not found at %s" % decoder_path())

    # Work next to the input rather than in %TEMP%. The decoder is an external
    # process and confined environments can deny it access to the system temp
    # directory; the input's own folder is writable in practice.
    workdir = os.path.dirname(os.path.abspath(path))
    tmpdir = tempfile.mkdtemp(prefix=".jl_f1a_", dir=workdir)
    try:
        exe = _stage_decoder(tmpdir)
        src = os.path.join(tmpdir, "in.f1a")
        out_wav = os.path.join(tmpdir, "out.wav")

        with open(src, "wb") as fh:
            fh.write(data)

        proc = subprocess.run(
            [exe, src, out_wav],
            cwd=tmpdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )

        if not os.path.isfile(out_wav) or os.path.getsize(out_wav) == 0:
            detail = proc.stdout.decode("utf-8", "replace").strip()
            raise DecodeError(
                "F1A decoder produced no output (exit %s)%s"
                % (proc.returncode, (": " + detail) if detail else ""))

        # The decoder's WAV header is unreliable; only the sample data is used.
        decoded = read_wav(out_wav)

        # Sanity check. The codec targets at most 16 kbit/s, so a genuine
        # stream cannot expand beyond about 2 bits per decoded sample. A far
        # lower ratio means the input was not really an F1A payload and the
        # decoder produced garbage from data it never validates.
        if data:
            bits_per_sample = len(data) * 8.0 / max(1, decoded.frames)
            if bits_per_sample < 0.2:
                raise DecodeError(
                    "implausible F1A stream: %d bytes decoded to %d samples "
                    "(%.3f bits/sample); the payload does not look like a "
                    "real coded stream" % (len(data), decoded.frames,
                                           bits_per_sample))

        rate = sample_rate if sample_rate else f1a_mod.F1A_DEFAULT_SAMPLE_RATE
        return PcmData(decoded.samples, rate, 1)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def decode_bytes(data: bytes, timeout: int = 180,
                 sample_rate: Optional[int] = None) -> PcmData:
    """Decode an in-memory F1A stream."""
    tmpdir = tempfile.mkdtemp(prefix="jl_f1a_in_")
    try:
        src = os.path.join(tmpdir, "in.f1a")
        with open(src, "wb") as fh:
            fh.write(data)
        return decode_file(src, timeout=timeout, sample_rate=sample_rate)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
