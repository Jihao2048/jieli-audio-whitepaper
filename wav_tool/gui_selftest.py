#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Headless smoke test for the tkinter front end.

Builds the whole window, exercises the widgets and runs one real conversion
through the same threaded path the GUI uses, then tears everything down. Run
it to confirm the UI is healthy without a human clicking anything.
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk

# The Windows console defaults to a legacy code page; force UTF-8 so the
# test can print the same glyphs the GUI log uses.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from wav_tool import dpi, service
from wav_tool.gui import ConverterApp


def _make_test_container(root):
    """Produce a small decodable container to exercise the pipeline.

    Prefers the user's own sample when present, otherwise builds a tiny
    ``.a`` file in the system temp directory so the test is self-contained
    and works from a copy of the toolkit that ships without sample data.
    """
    user = os.path.join(root,
                        "h6itam,n7san7os,MC Menor do Alvorada - "
                        "MONTAGEM ALQUIMIA.f1a")
    if os.path.isfile(user):
        return user

    for cand in (os.path.join(root, "_work", "verify", "my16k.f1a"),
                 os.path.join(root, "_work", "samples", "s16_16k.f1a")):
        if os.path.isfile(cand):
            return cand

    # Build one: a 44-byte zero preamble plus an IMA-ADPCM nibble stream.
    import math
    import struct
    import tempfile

    sys.path.insert(0, os.path.join(root, "wav_tool"))
    import abe

    rate = 8000
    samples = [int(9000 * math.sin(2 * math.pi * 440 * i / rate))
               for i in range(4000)]
    body = abe.encode_payload(samples)
    out = os.path.join(tempfile.gettempdir(), "jl_selftest_sample.a")
    with open(out, "wb") as fh:
        fh.write(b"\x00" * abe.AB_PREAMBLE + body)
    return out


def main():
    print("=" * 66)
    print("GUI smoke test")
    print("=" * 66)

    aware = dpi.enable_dpi_awareness()
    print("DPI awareness :", aware)

    root = tk.Tk()
    app = ConverterApp(root)
    root.update()

    print("scale         :", app.scale.describe())
    print("geometry      :", root.winfo_geometry())
    print("min size      :", root.minsize())
    print()

    checks = [
        ("文件列表", app.tree),
        ("日志", app.txt),
        ("进度条", app.progress),
        ("开始按钮", app.btn_start),
        ("取消按钮", app.btn_cancel),
        ("采样率下拉", app.cmb_rate),
        ("输出目录框", app.ent_out),
    ]
    for name, w in checks:
        assert w is not None
        print("  OK  %s" % name)

    # --- file list population ---
    sample = _make_test_container(_ROOT)
    print()
    print("test input    :", sample)

    app._add_paths([sample])
    root.update()
    rows = app.tree.get_children()
    print("list rows     :", len(rows))
    assert len(rows) == 1, "file list did not populate"
    print("row values    :", app.tree.item(rows[0], "values"))

    # --- run a real conversion through the threaded path ---
    import tempfile
    outdir = os.path.join(tempfile.gettempdir(), "jl_gui_selftest_out")
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        try:
            os.remove(os.path.join(outdir, f))
        except OSError:
            pass

    app.var_outdir.set(outdir)
    app.var_rate.set("自动")
    root.update()

    print()
    print("starting threaded conversion ...")
    app.start_conversion()

    deadline = time.time() + 180
    while app.worker and app.worker.is_alive() and time.time() < deadline:
        root.update()
        time.sleep(0.05)
    for _ in range(40):
        root.update()
        time.sleep(0.05)

    produced = [f for f in os.listdir(outdir) if f.lower().endswith(".wav")]
    print("produced      :", produced)
    assert produced, "conversion produced no WAV"

    for f in produced:
        info = service.probe_wav(os.path.join(outdir, f))
        print("  %s -> %d Hz, %d frames, %.2f s"
              % (f, info["rate"], info["frames"],
                 info["frames"] / info["rate"]))

    log = app.txt.get("1.0", "end").strip()
    print()
    print("log tail:")
    for line in log.splitlines()[-5:]:
        print("   ", line)

    root.update()
    root.destroy()
    print()
    print("=" * 66)
    print("GUI smoke test PASSED")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
