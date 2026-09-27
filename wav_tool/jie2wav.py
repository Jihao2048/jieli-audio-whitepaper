#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jie2wav - convert JieLi (杰理) audio files to WAV.

Usage
-----
    python jie2wav.py INPUT [INPUT ...] [-o OUTPUT_DIR] [-r RATE] [-v]

Supported inputs
----------------
    .f1a .f1b .f1c    proprietary spectral codec (bundled vendor decoder)
    .a   .b   .e      IMA-ADPCM 4-bit, pure Python
    .ump3             plain MP3 (FFmpeg backend)

Examples
--------
    python jie2wav.py voice.f1a
    python jie2wav.py *.f1a *.a *.ump3 -o out
    python jie2wav.py --info sample.f1a
    python jie2wav.py music.f1a -r 8000
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import traceback

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_PKG_DIR)
for _p in (_PARENT, _PKG_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from wav_tool import common, f1a, service
    from wav_tool.service import TaskOptions, DECODE_EXT
except ImportError:                        # run directly from inside the package
    import common                          # type: ignore
    import f1a                             # type: ignore
    import service                         # type: ignore
    from service import TaskOptions, DECODE_EXT   # type: ignore


def input_paths(patterns):
    """Expand shell-style patterns and directories into a file list."""
    files = []
    for pat in patterns:
        if os.path.isdir(pat):
            for name in sorted(os.listdir(pat)):
                if name.lower().endswith(DECODE_EXT):
                    files.append(os.path.join(pat, name))
            continue
        if any(ch in pat for ch in "*?["):
            files.extend(sorted(glob.glob(pat)))
            continue
        if os.path.isfile(pat):
            files.append(pat)
            continue
        print("warning: no such file: %s" % pat, file=sys.stderr)

    seen = set()
    out = []
    for f in files:
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def show_info(path):
    """Print a structural summary of one container."""
    with open(path, "rb") as fh:
        data = fh.read()

    kind = common.sniff_format(data, os.path.basename(path))
    print("=" * 68)
    print("file        : %s" % path)
    print("size        : %d bytes" % len(data))
    print("detected    : %s" % (kind or "unknown"))

    if kind in ("f1a", "f1b", "f1c"):
        try:
            hdr = f1a.parse_header(data, strict=False)
        except f1a.F1AFormatError as exc:
            print("header      : INVALID (%s)" % exc)
            return 1
        if hdr.fingerprint == f1a.F1A_HEADER_TAIL:
            print("header      : %s" % hdr.describe())
            print("payload     : %d bytes" % len(f1a.payload(data)))
        else:
            print("header      : none (headerless payload; "
                  "add_f1a_head.exe repairs these for the chip side)")
        print("sample rate : %d Hz (default; override with -r if needed)"
              % f1a.F1A_DEFAULT_SAMPLE_RATE)
    print()
    print("first 32 bytes:")
    print(common.hexdump(data, 32))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="jie2wav",
        description="Convert JieLi (杰理) audio files to WAV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("inputs", nargs="+", metavar="INPUT",
                   help="input files or glob patterns (%s)"
                        % " ".join(DECODE_EXT))
    p.add_argument("-o", "--outdir", default=".",
                   help="output directory (default: current directory)")
    p.add_argument("--info", action="store_true",
                   help="only print container information, do not convert")
    p.add_argument("-r", "--rate", type=int, default=None, metavar="HZ",
                   help="override the output sample rate. Needed for F1A: the "
                        "vendor decoder always writes 16000 into its WAV "
                        "template regardless of the stream, so the true rate "
                        "is not stored in the file. The default (8000) was "
                        "verified by listening plus a tempo measurement.")
    p.add_argument("--raw-delay", action="store_true",
                   help="keep the codec's warm-up samples instead of "
                        "aligning the output to the original start "
                        "(A/B/E only)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print exception tracebacks on failure")
    p.add_argument("--version", action="version", version="jie2wav 1.0.0")
    return p


def plan_outputs(files, out_dir):
    """Map each input to an output path, disambiguating stem collisions.

    Inputs that share a stem but differ in extension (``voice.a`` /
    ``voice.b`` / ``voice.e``) would otherwise overwrite each other, so the
    extension is appended to the stem for every member of a colliding group.
    """
    groups = {}
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        groups.setdefault(stem, []).append(path)

    plan = {}
    for members in groups.values():
        for path in members:
            base = os.path.splitext(os.path.basename(path))[0]
            if len(members) > 1:
                ext = os.path.splitext(path)[1].lstrip(".")
                base = "%s_%s" % (base, ext)
            plan[path] = os.path.join(out_dir, base + ".wav")
    return plan


def main(argv=None):
    args = build_parser().parse_args(argv)
    files = input_paths(args.inputs)

    if not files:
        print("error: no input files", file=sys.stderr)
        return 2

    if args.info:
        rc = 0
        for f in files:
            rc |= show_info(f)
        return rc

    os.makedirs(args.outdir, exist_ok=True)

    plan = plan_outputs(files, args.outdir)

    ok = failed = 0
    for f in files:
        opts = TaskOptions(out_dir=args.outdir, rate=args.rate,
                           raw_delay=args.raw_delay, overwrite=True)
        try:
            res = service.run_task(f, opts, dst=plan[f])
        except TypeError:
            # Older service signature without an explicit destination.
            res = service.run_task(f, opts)
        except Exception:
            if args.verbose:
                traceback.print_exc()
            print("FAIL  %-40s internal error" % os.path.basename(f))
            failed += 1
            continue

        if res.ok:
            ok += 1
            print("OK    %-40s -> %-28s %s"
                  % (os.path.basename(f), os.path.basename(res.output),
                     res.message))
        else:
            failed += 1
            lines = res.message.splitlines() or ["unknown error"]
            print("FAIL  %-40s %s" % (os.path.basename(f), lines[0]))
            if args.verbose:
                for line in lines[1:]:
                    print("        " + line)

    print()
    print("converted %d file(s), %d failed" % (ok, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
