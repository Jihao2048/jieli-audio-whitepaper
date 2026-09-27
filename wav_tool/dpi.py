#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HiDPI awareness and UI scaling for the tkinter front end.

Tk on Windows renders at a fixed pixel size unless the process declares
itself DPI aware. Without it, Windows bitmap-stretches the whole window on a
scaled display, which makes text blurry. This module declares per-monitor
DPI awareness through ``shcore``/``user32`` via ``ctypes`` and derives a
scale factor that every dimension in the UI is multiplied by.

The scale factor is computed from the *effective* DPI reported by the window
manager, so it stays correct when the window is dragged between monitors of
different DPI.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# --------------------------------------------------------------------------
# DPI awareness constants
# --------------------------------------------------------------------------

#: shcore.dll SetProcessDpiAwareness values.
PROCESS_DPI_AWARENESS_INVALID = -1
PROCESS_SYSTEM_DPI_AWARE = 1
PROCESS_PER_MONITOR_DPI_AWARE = 2

#: user32.dll SetProcessDpiAwarenessContext handles.
DPI_AWARENESS_CONTEXT_UNAWARE = ctypes.c_void_p(-1)
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = ctypes.c_void_p(-2)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = ctypes.c_void_p(-3)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

#: The DPI value that represents 100% scaling in Windows.
BASELINE_DPI = 96.0

IS_WINDOWS = sys.platform.startswith("win")


# --------------------------------------------------------------------------
# Process DPI awareness
# --------------------------------------------------------------------------

def enable_dpi_awareness() -> str:
    """Declare this process DPI aware.

    Tries the modern per-monitor-v2 context first and falls back through the
    older APIs, so the call succeeds on anything from Windows 7 upwards.

    Returns a short human-readable description of what was achieved.
    """
    if not IS_WINDOWS:
        return "not Windows (no DPI handling needed)"

    # --- Windows 10 1703+: per-monitor v2 ---
    try:
        user32 = ctypes.windll.user32
        set_ctx = user32.SetProcessDpiAwarenessContext
        set_ctx.argtypes = [ctypes.c_void_p]
        set_ctx.restype = wintypes.BOOL
        if set_ctx(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "per-monitor v2"
    except Exception:
        pass

    # --- Windows 8.1+: per-monitor ---
    try:
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return "per-monitor"
    except Exception:
        pass

    # --- Vista+: system aware ---
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass

    return "none (fallback)"


def system_dpi() -> float:
    """DPI of the primary display, or the baseline when unavailable."""
    if not IS_WINDOWS:
        return BASELINE_DPI
    try:
        # 0 = DPI_AWARENESS_CONTEXT_UNAWARE is the only pre-Win10 way to read
        # the true system DPI even when the process is already aware.
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            return float(dpi)
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return float(dpi)
    except Exception:
        pass
    return BASELINE_DPI


def window_dpi(window) -> float:
    """Effective DPI of the monitor a Tk window currently sits on."""
    if not IS_WINDOWS:
        return BASELINE_DPI
    try:
        hwnd = window.winfo_id()
        # Walk up to the real toplevel; Tk's winfo_id can be a child window.
        try:
            root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2)   # GA_ROOT
            if root_hwnd:
                hwnd = root_hwnd
        except Exception:
            pass
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        if dpi:
            return float(dpi)
    except Exception:
        pass
    return system_dpi()


# --------------------------------------------------------------------------
# Scaling helper
# --------------------------------------------------------------------------

class Scaler:
    """Translates logical UI units into device pixels for the current DPI.

    All layout numbers in the UI are written in logical units, where 96 DPI
    (100% scaling) is the reference. :meth:`px` converts such a number to
    whole device pixels using the scale factor captured at construction.
    """

    def __init__(self, window=None, dpi: float | None = None):
        self._dpi = float(dpi) if dpi else (window_dpi(window) if window
                                            else system_dpi())
        # Clamp to a sane band so a bogus DPI reading cannot produce an
        # unusable layout.
        self._dpi = max(72.0, min(480.0, self._dpi))

    @property
    def dpi(self) -> float:
        return self._dpi

    @property
    def factor(self) -> float:
        """Scale factor relative to 96 DPI (1.0 == 100%)."""
        return self._dpi / BASELINE_DPI

    @property
    def percent(self) -> int:
        return int(round(self.factor * 100))

    def px(self, value: float) -> int:
        """Convert a logical length to whole device pixels (at least 1)."""
        return max(1, int(round(value * self.factor)))

    def px0(self, value: float) -> int:
        """Convert a logical length to device pixels, allowing 0."""
        return max(0, int(round(value * self.factor)))

    def font(self, size: int, weight: str = "normal") -> tuple:
        """A Tk font tuple sized for the current DPI.

        ``size`` is given in points at 100% scaling. Tk font sizes in points
        are already DPI-scaled by Tk itself when the process is DPI aware, so
        the point size is passed through and only the family/weight are set.
        """
        return ("Microsoft YaHei UI", size, weight)

    def apply_tk_scaling(self, window) -> None:
        """Keep Tk's own metric scaling consistent with the DPI we measured."""
        try:
            window.tk.call("tk", "scaling", self.factor * 1.3333)
        except Exception:
            pass

    def describe(self) -> str:
        return ("%d DPI, %.0f%% scaling" % (round(self._dpi),
                                            self.factor * 100))


def enable_windows_dark_titlebar(window) -> bool:
    """Ask DWM to draw the title bar in dark mode.

    Purely cosmetic and entirely optional; failure is ignored.
    """
    if not IS_WINDOWS:
        return False
    try:
        hwnd = window.winfo_id()
        root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2)
        if root_hwnd:
            hwnd = root_hwnd
        value = ctypes.c_int(1)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 1903+), 19 on older builds
        for attr in (20, 19):
            res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), ctypes.c_uint(attr),
                ctypes.byref(value), ctypes.sizeof(value))
            if res == 0:
                return True
    except Exception:
        pass
    return False
