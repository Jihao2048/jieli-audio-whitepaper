# jieli-audio-whitepaper

**A white paper and toolkit for the JieLi (杰理) audio formats — turn AD14N / SH50 chip audio files back into WAV.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-3776ab.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078d4.svg)]()

[中文说明](README.zh-CN.md)

> ### 🤖 Built by DeepSeek
>
> This entire project — the reverse engineering of the JieLi audio
> containers, the format white paper under `docs/`, the Python decoder, the
> tkinter GUI, the test suites and this documentation — was produced by
> **DeepSeek** (DeepSeek Harness agent) working from the vendor's own tool
> package. Every claim in the white paper is backed by a reproducible
> experiment; the methods that did *not* work are documented too.

---

## ⬇️ Downloads — the vendor tools are hard to find, so they are archived here

The original vendor tool is no longer easy to obtain. Both it and the
command line tools extracted from it are attached to the
[**v1.0.0 release**](https://github.com/Jihao2048/jieli-audio-whitepaper/releases/tag/v1.0.0):

| Archive | Size | Contents |
|---|---|---|
| **`jieLi-vendor-tools-1.2.2.zip`** | 27.3 MB | The original `音频文件转换工具_1.2.2.exe` plus its Chinese manual |
| **`jieLi-cli-tools.zip`** | 22.4 MB | All 18 command line tools extracted from it, including two FFmpeg builds |

You **do not need these** to convert files back to WAV — the repository's own
toolkit does that. Download them if you want the vendor encoder, the FFmpeg
builds, or the original manual.

> ⚠️ The vendor GUI only converts *into* the chip formats. It cannot convert
> back out. Use this project's toolkit for that.

---

## What is this?

If you own a Bluetooth speaker, a toy, or any voice-prompt device built on a
**JieLi (杰理) audio chip** — common families are AD14N, SH50 and SH52 — you
may have run into files like these:

```
music.f1a     prompt.a     background.ump3
```

They are the sound files those chips play. **No ordinary player can open
them.** The vendor ships exactly one Windows GUI that only converts *into*
these formats and cannot convert back out.

**This project takes them apart, documents every byte, and provides a
double-clickable converter that turns them back into WAV.**

```diff
- music.f1a    ← no player opens it, vendor tool only goes one way
+ music.wav    ← play it, edit it, use it anywhere
```

---

## 🚀 Quick start

**Nothing to install, no command line needed.**

1. Download or clone this repository
2. Double-click **`启动转换工具.bat`** (*launch converter*)
3. Drop in your `.f1a` / `.a` / `.b` / `.e` / `.ump3` files
4. Press **开始转换** (*start conversion*)

> Requires Python 3.8+ for Windows (from python.org — tick *Add to PATH*).
> The GUI uses tkinter, which ships with Python; nothing else to install.

![GUI](docs/screenshot.png)

---

## 📖 How it works — in one paragraph

The headline finding is that **two of the three format families are not
proprietary at all**:

| Format | What it really is | Difficulty |
|---|---|---|
| `.ump3` | **An ordinary MP3 file** with a different extension | ⭐ |
| `.a` `.b` `.e` | **Standard IMA ADPCM, 4-bit** — textbook algorithm | ⭐⭐ |
| `.f1a` `.f1b` `.f1c` | Genuinely proprietary spectral coder | ⭐⭐⭐⭐⭐ |

### How each claim was proven

Reverse engineering is easy to get *almost* right, so every conclusion here
comes with a reproducible hard check.

**`.ump3` is MP3** — the same source, encoder and parameters written to two
extensions produce **byte-identical files**:

```
smp_16k_64k.ump3  sha256 = 07c338a1f8eb2cedb3f945b59902127d...
ctl_16k_64k.mp3   sha256 = 07c338a1f8eb2cedb3f945b59902127d...
byte-identical: True
```

**`.a` / `.e` are standard IMA ADPCM** — proven by a **byte-exact re-encode
round trip**. ADPCM is deterministic, so decoding a vendor file and encoding
it again must reproduce the original bytes. It does, with a match of
**1.000000** on every sample tested:

```
amp_0.a        re-encode byte-identical (1.000000, 3883 bytes)
zz_counter.a   re-encode byte-identical (1.000000, 3883 bytes)
...  12 samples, all 1.000000
```

This is far stronger than a correlation score, because it pins down the
nibble order, both lookup tables *and* the initial state simultaneously.

**`.f1a`'s container layout** — established by mutation experiments:

```
sil16k.f1a  is only 19 bytes:
  cb 80 bf f0 06 67 72 aa 66 17 f7 | 80 00 00 00 00 00 00 00
  <-------- 11-byte header --------> <--- 8-byte payload --->
  The payload holds a single set bit yet decodes to 8704 silent
  samples, which proves a zero-run escape code exists.
```

📄 **The full derivation is in [docs/FORMAT.md](docs/FORMAT.md)** — UPX
unpacking, recovery of the 18 embedded tools, the provenance of every byte,
and the hypotheses that turned out to be *wrong*.

---

## 🎯 Support matrix

| Extension | To WAV | Notes |
|---|:---:|---|
| `.f1a` `.f1b` `.f1c` | ✅ | Proprietary spectral coder; uses the bundled vendor decoder |
| `.a` `.b` | ✅ | Pure Python, byte-exact verified |
| `.e` | ✅ | Pure Python, rate auto-detected |
| `.ump3` | ✅ | Plain MP3 via FFmpeg |

**Decoding only, no encoding.** Producing these containers is the vendor
tool's job — only its encoder knows the proprietary spectral coder `.f1a`
needs.

---

## ⚠️ Two things you must know

### 1. F1A sample rate must be judged by ear

An F1A file **stores no usable sample-rate field**, and the vendor decoder
makes it worse: it **writes a hardcoded 16000 into the WAV header**
regardless of the actual stream.

This tool therefore defaults to **8000 Hz**, verified three independent ways:

| Check | Result |
|---|---|
| Listening test | 8000 Hz sounds correct |
| Tempo autocorrelation | **130.4 BPM** — squarely in the Funk range |
| Implied bitrate | 7,943 bit/s — normal for 8 kHz low-rate music |

**If the pitch sounds wrong, change the sample rate in the GUI dropdown**
or pass `-r 16000` on the command line.

### 2. Some F1A files have no header at all

Some `.f1a` files carry **no 11-byte header** — the coded payload starts at
byte 0. (That is exactly what `add_f1a_head.exe` in the vendor bundle exists
to repair, for the chip side.)

This tool detects and decodes them correctly **without adding a header**.
Adding one would double the duration, because the decoder derives length
from the file size.

---

## 📚 Documentation

| Document | Contents |
|---|---|
| [docs/FORMAT.md](docs/FORMAT.md) | **Format white paper** — byte-level analysis and evidence |
| [docs/USAGE.md](docs/USAGE.md) | **User guide** — GUI and command line |
| [docs/FAQ.md](docs/FAQ.md) | **Troubleshooting** — wrong pitch, failures |

---

## 💻 Command line

```bash
python wav_tool/jie2wav.py music.f1a                  # one file
python wav_tool/jie2wav.py *.f1a *.a *.ump3 -o out    # batch
python wav_tool/jie2wav.py --info music.f1a           # inspect only
python wav_tool/jie2wav.py music.f1a -r 16000         # force sample rate
```

---

## ✅ Self-test

Two suites, both fully headless:

```bash
python wav_tool/selftest.py       # backend: 36 checks
python wav_tool/gui_selftest.py   # GUI: builds the window and converts for real
```

The backend suite generates its own test containers when the vendor corpus
is absent, so it runs on any machine:

```
with vendor corpus : 36 passed, 0 failed
without            : 15 passed, 0 failed
GUI                : PASSED
```

---

## 📦 Repository layout

```
jieli-audio-whitepaper/
├── README.md / README.zh-CN.md
├── LICENSE                      MIT (see note on bundled binaries)
├── docs/
│   ├── FORMAT.md                format white paper
│   ├── USAGE.md                 user guide
│   └── FAQ.md                   troubleshooting
├── wav_tool/                    the toolkit
│   ├── gui.py                   tkinter front end
│   ├── dpi.py                   ctypes HiDPI scaling
│   ├── jie2wav.py               CLI entry point
│   ├── service.py               conversion dispatch
│   ├── common.py                WAV I/O, format sniffing
│   ├── f1a.py, f1a_decoder.py   F1A container and decode backend
│   ├── abe.py                   .a/.b/.e pure-Python IMA ADPCM
│   ├── ump3.py                  .ump3 (MP3) parsing and decode
│   ├── selftest.py              backend checks
│   ├── gui_selftest.py          GUI checks
│   └── tools/                   bundled helper binaries
├── samples/
│   └── example.f1a              example input
└── 启动转换工具.bat               double-click launcher
```

---

## 📦 Bundled binaries

`wav_tool/tools/` carries helpers so the converter works offline:

| File | Purpose | Committed? |
|---|---|---|
| `testf1a_dec.exe` | JieLi F1A decoder from the vendor package | yes (53 KB) |
| `msvcr100.dll` | VC++ runtime it needs | yes (757 KB) |
| `ump3_ffmpeg.exe` | MP3 decoding (34 MB) | **no** — see below |

`ump3_ffmpeg.exe` is **not** committed: it is a large third-party artifact.
`.ump3` decoding therefore looks for, in order:

1. the `JL_FFMPEG` environment variable
2. an `ffmpeg` on `PATH`
3. `wav_tool/tools/ump3_ffmpeg.exe`

So any system FFmpeg makes `.ump3` work; only F1A genuinely needs the
bundled decoder, and that one is small enough to ship.

---

## 🙏 Scope and intent

- This project **only converts formats**, to help users recover audio data
  from hardware they own.
- The bundled `testf1a_dec.exe` comes from the vendor's own tool package and
  is used solely to decode the user's own files.
- No firmware was modified and no licensing mechanism was bypassed.

## Credits

| Role | |
|---|---|
| **Reverse engineering, code, documentation** | **DeepSeek** (DeepSeek Harness agent) |
| Repository owner / maintainer | [Jihao2048](https://github.com/Jihao2048) |
| F1A decoding | `testf1a_dec.exe` from the JieLi vendor package |
| MP3 decoding | FFmpeg (LGPL/GPL) |

## License

Code is MIT — see [LICENSE](LICENSE). Bundled binaries remain the property
of their respective owners.
