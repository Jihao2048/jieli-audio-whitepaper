#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wav_tool - convert JieLi (杰理 AD14N / SH50) audio containers back to WAV.

Supported inputs
----------------
``.f1a`` / ``.f1b`` / ``.f1c``   proprietary spectral codec
``.a``   / ``.b``   / ``.e``     IMA-ADPCM derived low-rate formats
``.ump3``                        MP3-based music container

The package ships the vendor decoder binaries it needs under ``tools/`` so
that conversions work offline with no external dependencies.

Credit
------
Reverse engineering, implementation and documentation by DeepSeek
(DeepSeek Harness agent), 2026. See the repository README for details.
"""

from .common import PcmData, read_wav, write_wav, sniff_format, hexdump
from . import f1a

__all__ = [
    "PcmData", "read_wav", "write_wav", "sniff_format", "hexdump", "f1a",
    "__version__",
]

__version__ = "1.0.0"
