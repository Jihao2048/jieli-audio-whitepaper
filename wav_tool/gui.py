#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Graphical front end: JieLi audio container -> WAV.

A single window with a file list, an options panel and a conversion log.
Everything is laid out in logical units and scaled to the monitor DPI by
:mod:`wav_tool.dpi`, which also declares per-monitor DPI awareness through
``ctypes`` so text stays sharp on scaled displays.

Run with::

    python wav_tool/gui.py
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from wav_tool import common, dpi, service
    from wav_tool.service import TaskOptions, DECODE_EXT
except ImportError:
    import common                              # type: ignore
    import dpi                                 # type: ignore
    import service                             # type: ignore
    from service import TaskOptions, DECODE_EXT   # type: ignore


APP_TITLE = "杰理音频转 WAV"
APP_VERSION = "1.0"

# ── palette ───────────────────────────────────────────────────────────────
BG = "#1e2229"
BG_ALT = "#262b34"
BG_INPUT = "#2f3540"
FG = "#e6e9ef"
FG_DIM = "#98a2b3"
ACCENT = "#4a9eff"
ACCENT_DK = "#357abd"
OK_COLOR = "#3fb950"
ERR_COLOR = "#f85149"
WARN_COLOR = "#d29922"


class ConverterApp:
    """The main window."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.scale = dpi.Scaler(root)

        self.files: list[str] = []
        self.worker: threading.Thread | None = None
        self.msg_queue: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()

        self.var_outdir = tk.StringVar(value="")
        self.var_rate = tk.StringVar(value="自动")
        self.var_rawdelay = tk.BooleanVar(value=False)
        self.var_overwrite = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="就绪")

        self._setup_window()
        self._build_styles()
        self._build_menu()
        self._build_layout()
        self._bind_events()

        self._log("%s v%s" % (APP_TITLE, APP_VERSION), "info")
        self._log("显示缩放：%s" % self.scale.describe(), "dim")
        self._log("支持格式：%s" % " ".join(DECODE_EXT), "dim")
        self._log("", "dim")

        self.root.after(80, self._poll_queue)

    # ── window chrome ─────────────────────────────────────────────────────
    def _setup_window(self):
        s = self.scale
        self.root.title("%s %s" % (APP_TITLE, APP_VERSION))
        self.root.configure(bg=BG)
        self.root.minsize(s.px(720), s.px(500))

        w, h = s.px(860), s.px(620)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d"
                           % (w, h, (sw - w) // 2,
                              max(0, (sh - h) // 2 - s.px(20))))

        dpi.enable_windows_dark_titlebar(self.root)
        try:
            self.root.tk.call("tk", "scaling", s.factor * 1.3333)
        except Exception:
            pass

    def _build_styles(self):
        s = self.scale
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass

        base = s.font(10)
        st.configure(".", background=BG, foreground=FG, font=base,
                     bordercolor=BG_INPUT, focuscolor=ACCENT)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=BG_ALT)
        st.configure("TLabel", background=BG, foreground=FG, font=base)
        st.configure("Card.TLabel", background=BG_ALT, foreground=FG,
                     font=base)
        st.configure("Dim.TLabel", background=BG, foreground=FG_DIM,
                     font=s.font(9))
        st.configure("CardDim.TLabel", background=BG_ALT, foreground=FG_DIM,
                     font=s.font(9))
        st.configure("Head.TLabel", background=BG, foreground=FG,
                     font=s.font(12, "bold"))
        st.configure("CardHead.TLabel", background=BG_ALT, foreground=FG,
                     font=s.font(10, "bold"))

        st.configure("TCheckbutton", background=BG_ALT, foreground=FG,
                     font=base)
        st.map("TCheckbutton", background=[("active", BG_ALT)])

        st.configure("TButton", background=BG_INPUT, foreground=FG, font=base,
                     borderwidth=0, padding=(s.px(12), s.px(6)))
        st.map("TButton",
               background=[("active", "#3b4250"), ("disabled", BG_ALT)],
               foreground=[("disabled", FG_DIM)])

        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                     font=s.font(11, "bold"), borderwidth=0,
                     padding=(s.px(20), s.px(10)))
        st.map("Accent.TButton",
               background=[("active", ACCENT_DK), ("disabled", "#39414f")],
               foreground=[("disabled", FG_DIM)])

        st.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_INPUT,
                     foreground=FG, arrowcolor=FG, borderwidth=0,
                     padding=(s.px(6), s.px(4)))
        st.map("TCombobox",
               fieldbackground=[("readonly", BG_INPUT)],
               foreground=[("readonly", FG)],
               selectbackground=[("readonly", BG_INPUT)],
               selectforeground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", BG_INPUT)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.font", base)

        st.configure("TProgressbar", background=ACCENT, troughcolor=BG_INPUT,
                     borderwidth=0, thickness=s.px(6))

        st.configure("Treeview", background=BG_INPUT, fieldbackground=BG_INPUT,
                     foreground=FG, borderwidth=0, rowheight=s.px(24),
                     font=s.font(9))
        st.configure("Treeview.Heading", background=BG_ALT, foreground=FG_DIM,
                     font=s.font(9, "bold"), borderwidth=0,
                     padding=(s.px(6), s.px(4)))
        st.map("Treeview.Heading", background=[("active", BG_INPUT)])
        st.map("Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "#ffffff")])
        st.configure("Vertical.TScrollbar", background=BG_INPUT,
                     troughcolor=BG, borderwidth=0, arrowcolor=FG_DIM)

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0, bg=BG_ALT, fg=FG,
                    activebackground=ACCENT, activeforeground="#ffffff",
                    borderwidth=0)
        fm = tk.Menu(m, tearoff=0, bg=BG_ALT, fg=FG, activebackground=ACCENT,
                     activeforeground="#ffffff", borderwidth=0)
        fm.add_command(label="添加文件…", accelerator="Ctrl+O",
                       command=self.add_files)
        fm.add_command(label="添加文件夹…", command=self.add_folder)
        fm.add_separator()
        fm.add_command(label="清空列表", command=self.clear_files)
        fm.add_separator()
        fm.add_command(label="退出", command=self.root.destroy)
        m.add_cascade(label="文件", menu=fm)

        hm = tk.Menu(m, tearoff=0, bg=BG_ALT, fg=FG, activebackground=ACCENT,
                     activeforeground="#ffffff", borderwidth=0)
        hm.add_command(label="关于", command=self._about)
        m.add_cascade(label="帮助", menu=hm)
        self.root.config(menu=m)

    # ── layout ────────────────────────────────────────────────────────────
    def _build_layout(self):
        s = self.scale

        outer = ttk.Frame(self.root, padding=s.px(12))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        # ── header ──
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew", pady=(0, s.px(10)))
        ttk.Label(head, text=APP_TITLE, style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="   把杰理音频容器转成标准 WAV",
                  style="Dim.TLabel").pack(side="left")

        # ── options card ──
        opts = ttk.Frame(outer, style="Card.TFrame", padding=s.px(12))
        opts.grid(row=1, column=0, sticky="ew", pady=(0, s.px(10)))
        opts.columnconfigure(1, weight=1)

        ttk.Label(opts, text="输出目录", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", pady=s.px(4))
        self.ent_out = tk.Entry(opts, textvariable=self.var_outdir, bg=BG_INPUT,
                                fg=FG, insertbackground=FG, relief="flat",
                                font=s.font(9), highlightthickness=s.px(1),
                                highlightbackground=BG_INPUT,
                                highlightcolor=ACCENT)
        self.ent_out.grid(row=0, column=1, sticky="ew", padx=s.px(8),
                          pady=s.px(4), ipady=s.px(4))
        ttk.Button(opts, text="浏览…", command=self.choose_outdir).grid(
            row=0, column=2, sticky="w", padx=s.px(8))

        ttk.Label(opts, text="采样率", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=s.px(4))
        self.cmb_rate = ttk.Combobox(
            opts, textvariable=self.var_rate, state="readonly", width=16,
            values=("自动", "8000", "11025", "12000", "16000",
                    "22050", "24000", "32000", "44100"))
        self.cmb_rate.grid(row=1, column=1, sticky="w", padx=s.px(8),
                           pady=s.px(4))

        chk = ttk.Frame(opts, style="Card.TFrame")
        chk.grid(row=2, column=0, columnspan=3, sticky="w", pady=(s.px(8), 0))
        ttk.Checkbutton(chk, text="覆盖已存在的文件",
                        variable=self.var_overwrite).pack(side="left")
        ttk.Checkbutton(chk, text="保留编码器预热样本（不自动对齐）",
                        variable=self.var_rawdelay).pack(
            side="left", padx=(s.px(18), 0))

        self.lbl_hint = ttk.Label(
            opts, style="CardDim.TLabel", justify="left",
            wraplength=s.px(720),
            text="F1A 文件里没有可靠的采样率字段，官方解码器又固定写 16000，"
                 "所以采样率默认按 8000 处理——若音调不对，请在上方手动选择"
                 "其它采样率。")
        self.lbl_hint.grid(row=3, column=0, columnspan=3, sticky="w",
                           pady=(s.px(8), 0))

        # ── files card ──
        files_card = ttk.Frame(outer, style="Card.TFrame", padding=s.px(12))
        files_card.grid(row=2, column=0, sticky="nsew", pady=(0, s.px(10)))
        files_card.columnconfigure(0, weight=1)
        files_card.rowconfigure(1, weight=1)

        fhead = ttk.Frame(files_card, style="Card.TFrame")
        fhead.grid(row=0, column=0, columnspan=2, sticky="ew",
                   pady=(0, s.px(6)))
        ttk.Label(fhead, text="文件列表", style="CardHead.TLabel").pack(
            side="left")
        self.lbl_count = ttk.Label(fhead, text="（0 个）",
                                   style="CardDim.TLabel")
        self.lbl_count.pack(side="left", padx=s.px(6))

        cols = ("name", "size", "kind")
        self.tree = ttk.Treeview(files_card, columns=cols, show="headings",
                                 selectmode="extended")
        self.tree.heading("name", text="文件名")
        self.tree.heading("size", text="大小")
        self.tree.heading("kind", text="格式")
        self.tree.column("name", anchor="w", width=s.px(410), stretch=True)
        self.tree.column("size", anchor="e", width=s.px(100), stretch=False)
        self.tree.column("kind", anchor="center", width=s.px(80),
                         stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew")

        sb = ttk.Scrollbar(files_card, orient="vertical",
                           command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)

        btns = ttk.Frame(files_card, style="Card.TFrame")
        btns.grid(row=2, column=0, columnspan=2, sticky="ew",
                  pady=(s.px(8), 0))
        ttk.Button(btns, text="添加文件", command=self.add_files).pack(
            side="left")
        ttk.Button(btns, text="添加文件夹", command=self.add_folder).pack(
            side="left", padx=s.px(6))
        ttk.Button(btns, text="移除选中", command=self.remove_selected).pack(
            side="left")
        ttk.Button(btns, text="清空", command=self.clear_files).pack(
            side="left", padx=s.px(6))

        # ── action row ──
        actions = ttk.Frame(outer)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, s.px(8)))
        actions.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(actions, mode="determinate",
                                        maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, s.px(10)))

        self.btn_start = ttk.Button(actions, text="开始转换",
                                    style="Accent.TButton",
                                    command=self.start_conversion)
        self.btn_start.grid(row=0, column=1)

        self.btn_cancel = ttk.Button(actions, text="取消",
                                     command=self.cancel_conversion,
                                     state="disabled")
        self.btn_cancel.grid(row=0, column=2, padx=(s.px(6), 0))

        # ── log ──
        logcard = ttk.Frame(outer, style="Card.TFrame", padding=s.px(12))
        logcard.grid(row=4, column=0, sticky="ew")
        logcard.columnconfigure(0, weight=1)

        lhead = ttk.Frame(logcard, style="Card.TFrame")
        lhead.grid(row=0, column=0, sticky="ew")
        ttk.Label(lhead, text="日志", style="CardHead.TLabel").pack(side="left")
        ttk.Button(lhead, text="清除日志", command=self._clear_log).pack(
            side="right")

        self.txt = tk.Text(logcard, height=7, bg=BG_INPUT, fg=FG,
                           insertbackground=FG, relief="flat",
                           font=("Consolas", 8, "normal"), wrap="word",
                           state="disabled", highlightthickness=0,
                           padx=s.px(8), pady=s.px(6))
        self.txt.grid(row=1, column=0, sticky="ew", pady=(s.px(6), 0))
        self.txt.tag_configure("info", foreground=FG)
        self.txt.tag_configure("ok", foreground=OK_COLOR)
        self.txt.tag_configure("err", foreground=ERR_COLOR)
        self.txt.tag_configure("warn", foreground=WARN_COLOR)
        self.txt.tag_configure("dim", foreground=FG_DIM)

        # ── status bar ──
        status = ttk.Frame(outer)
        status.grid(row=5, column=0, sticky="ew", pady=(s.px(6), 0))
        ttk.Label(status, textvariable=self.var_status,
                  style="Dim.TLabel").pack(side="left")

    def _bind_events(self):
        self.root.bind("<Control-o>", lambda e: self.add_files())
        self.root.bind("<F5>", lambda e: self.start_conversion())
        self.root.bind("<Delete>", lambda e: self.remove_selected())
        self._try_enable_dnd()

    def _try_enable_dnd(self):
        try:
            from tkinterdnd2 import DND_FILES         # type: ignore
        except Exception:
            return
        try:
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind("<<Drop>>", self._on_drop)
            self._log("已启用拖放文件支持", "dim")
        except Exception:
            pass

    def _on_drop(self, event):
        self._add_paths(self.root.tk.splitlist(event.data))

    # ── file list ─────────────────────────────────────────────────────────
    def add_files(self):
        pattern = " ".join("*" + e for e in DECODE_EXT)
        paths = filedialog.askopenfilenames(
            title="选择要转换的文件",
            filetypes=[("杰理音频格式", pattern), ("所有文件", "*.*")])
        self._add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择文件夹")
        if not folder:
            return
        found = [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                 if n.lower().endswith(DECODE_EXT)]
        self._add_paths(found)

    def _add_paths(self, paths):
        added = 0
        for p in paths:
            p = os.path.abspath(p)
            if os.path.isdir(p):
                for n in sorted(os.listdir(p)):
                    if n.lower().endswith(DECODE_EXT):
                        if self._append(p, n):
                            added += 1
                continue
            if self._append(os.path.dirname(p), os.path.basename(p)):
                added += 1
        if added:
            self._log("添加了 %d 个文件" % added, "info")
        self._refresh_list()

    def _append(self, folder, name):
        full = os.path.join(folder, name)
        if full in self.files or not os.path.isfile(full):
            return False
        self.files.append(full)
        return True

    def remove_selected(self):
        sel = set(self.tree.selection())
        if not sel:
            return
        keep = [f for i, f in enumerate(self.files) if str(i) not in sel]
        removed = len(self.files) - len(keep)
        self.files = keep
        self._refresh_list()
        self._log("移除了 %d 个文件" % removed, "dim")

    def clear_files(self):
        n = len(self.files)
        self.files = []
        self._refresh_list()
        if n:
            self._log("已清空列表（%d 个）" % n, "dim")

    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, f in enumerate(self.files):
            self.tree.insert("", "end", iid=str(i),
                             values=(os.path.basename(f),
                                     _human(os.path.getsize(f)),
                                     os.path.splitext(f)[1].lower()))
        self.lbl_count.configure(text="（%d 个）" % len(self.files))

    # ── options ───────────────────────────────────────────────────────────
    def choose_outdir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.var_outdir.set(d)

    def _selected_rate(self):
        v = self.var_rate.get()
        return int(v) if v.isdigit() else None

    # ── conversion ────────────────────────────────────────────────────────
    def start_conversion(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "请先添加要转换的文件。")
            return

        outdir = self.var_outdir.get().strip()
        if not outdir:
            outdir = os.path.join(os.path.dirname(self.files[0]), "converted")
            self.var_outdir.set(outdir)
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, "无法创建输出目录：\n%s" % exc)
            return

        opts = TaskOptions(out_dir=outdir, rate=self._selected_rate(),
                           raw_delay=self.var_rawdelay.get(),
                           overwrite=self.var_overwrite.get())

        self.cancel_flag.clear()
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress.configure(value=0, maximum=len(self.files))

        self._log("", "dim")
        self._log("开始转换：%d 个文件 → %s" % (len(self.files), outdir),
                  "info")

        self.worker = threading.Thread(target=self._work,
                                       args=(list(self.files), opts),
                                       daemon=True)
        self.worker.start()

    def _work(self, files, opts):
        ok = fail = 0
        # Resolve stem collisions (voice.a / voice.b / voice.e) up front so
        # no conversion silently overwrites another.
        plan = {}
        groups = {}
        for f in files:
            groups.setdefault(
                os.path.splitext(os.path.basename(f))[0].lower(), []).append(f)
        for members in groups.values():
            for f in members:
                base = os.path.splitext(os.path.basename(f))[0]
                if len(members) > 1:
                    base = "%s_%s" % (base, os.path.splitext(f)[1].lstrip("."))
                plan[f] = os.path.join(opts.out_dir, base + ".wav")

        for i, path in enumerate(files, 1):
            if self.cancel_flag.is_set():
                self.msg_queue.put(("log", "已取消，剩余文件未处理", "warn"))
                break
            self.msg_queue.put(("status", "正在处理 %d/%d：%s"
                                % (i, len(files), os.path.basename(path))))
            try:
                res = service.run_task(path, opts, dst=plan[path])
            except Exception:
                res = None
                self.msg_queue.put(("log", "内部错误：\n%s"
                                    % traceback.format_exc(), "err"))
            if res and res.ok:
                ok += 1
                self.msg_queue.put(("log", "✓ %s  →  %s   (%s)"
                                    % (os.path.basename(res.source),
                                       os.path.basename(res.output),
                                       res.message), "ok"))
            elif res:
                fail += 1
                lines = res.message.splitlines() or ["未知错误"]
                self.msg_queue.put(("log", "✗ %s  —  %s"
                                    % (os.path.basename(path), lines[0]), "err"))
                for line in lines[1:]:
                    self.msg_queue.put(("log", "    " + line, "dim"))
            self.msg_queue.put(("progress", i))

        self.msg_queue.put(("done", (ok, fail)))

    def cancel_conversion(self):
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self._log("已请求取消…", "warn")

    def _poll_queue(self):
        try:
            while True:
                kind, payload, *rest = self.msg_queue.get_nowait()
                if kind == "log":
                    self._log(payload, rest[0] if rest else "info")
                elif kind == "status":
                    self.var_status.set(payload)
                elif kind == "progress":
                    self.progress.configure(value=payload)
                elif kind == "done":
                    ok, fail = payload
                    self.btn_start.configure(state="normal")
                    self.btn_cancel.configure(state="disabled")
                    self.var_status.set("完成：成功 %d，失败 %d" % (ok, fail))
                    self._log("", "dim")
                    self._log("完成：成功 %d 个，失败 %d 个" % (ok, fail),
                              "ok" if fail == 0 else "warn")
                    if fail:
                        messagebox.showwarning(
                            APP_TITLE,
                            "完成，但有 %d 个文件失败。\n详情请看日志。" % fail)
                    else:
                        messagebox.showinfo(
                            APP_TITLE, "全部 %d 个文件转换成功。\n\n输出目录：\n%s"
                            % (ok, self.var_outdir.get()))
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    # ── log ───────────────────────────────────────────────────────────────
    def _log(self, text, tag="info"):
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n", tag)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _about(self):
        messagebox.showinfo(
            APP_TITLE,
            "%s %s\n\n"
            "把杰理（JieLi）AD14N / SH50 系列的音频文件转成标准 WAV。\n\n"
            "支持格式：%s\n\n"
            "显示缩放：%s\n"
            % (APP_TITLE, APP_VERSION, " ".join(DECODE_EXT),
               self.scale.describe()))


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" % (n, unit) if unit == "B"
                    else "%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)


def main():
    dpi.enable_dpi_awareness()
    root = tk.Tk()
    try:
        ConverterApp(root)
        root.mainloop()
    except Exception:
        traceback.print_exc()
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
