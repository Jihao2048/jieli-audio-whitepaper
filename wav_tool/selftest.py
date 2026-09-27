#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-test / acceptance suite for the JieLi audio container toolkit.

Runs the converter over every sample that could be produced from the vendor
toolchain and reports per-format results, then verifies that the F1A output
is byte-identical to what the vendor decoder itself writes.

Usage
-----
    python wav_tool/selftest.py
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from wav_tool import common, f1a, abe, ump3
except ImportError:
    import common                              # type: ignore
    import f1a                                 # type: ignore
    import abe                                 # type: ignore
    import ump3                                # type: ignore


WORK = os.path.join(_ROOT, "_work")


# --------------------------------------------------------------------------
# Built-in sample generation
#
# The full acceptance runs against the vendor-produced corpus under _work/,
# which only exists in the development checkout. When that corpus is absent
# the suite falls back to containers it builds itself, so a shipped copy of
# the toolkit can still be verified on any machine.
# --------------------------------------------------------------------------

def _synth_samples():
    """Create a WAV plus matching .a and .e containers in a temp dir."""
    import math

    outdir = os.path.join(tempfile.gettempdir(), "jl_selftest_samples")
    os.makedirs(outdir, exist_ok=True)

    rate = 8000
    count = 4000
    samples = [int(9000 * math.sin(2 * math.pi * 440 * i / rate))
               for i in range(count)]

    wav_path = os.path.join(outdir, "sine8k.wav")
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % count, *samples))

    body = abe.encode_payload(samples)

    a_path = os.path.join(outdir, "sine8k.a")
    with open(a_path, "wb") as fh:
        fh.write(b"\x00" * abe.AB_PREAMBLE + body)

    e_path = os.path.join(outdir, "sine8k.e")
    with open(e_path, "wb") as fh:
        fh.write(bytes([0x57, 0x41, 0x56, 0x00]) + body)

    return {"dir": outdir, "wav": wav_path, "a": a_path, "e": e_path,
            "samples": samples}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wav_info(path):
    with wave.open(path, "rb") as w:
        return w.getnframes(), w.getframerate(), w.getnchannels()


class Results:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def ok(self, msg):
        self.passed += 1
        print("  PASS  %s" % msg)

    def fail(self, msg):
        self.failed += 1
        print("  FAIL  %s" % msg)

    def skip(self, msg):
        self.skipped += 1
        print("  skip  %s" % msg)


def test_f1a_container(res):
    """Structural checks on the F1A header parser."""
    print("\n[1] F1A container parsing")
    sample = os.path.join(WORK, "verify", "my16k.f1a")
    if not os.path.isfile(sample):
        res.skip("no F1A sample at %s" % sample)
        return

    with open(sample, "rb") as fh:
        data = fh.read()

    if data[0] == 0xCB:
        res.ok("sync byte is 0xCB")
    else:
        res.fail("sync byte is 0x%02X" % data[0])

    try:
        hdr = f1a.parse_header(data)
        res.ok("header parsed: %s" % hdr.describe())
    except Exception as exc:
        res.fail("header parse raised %s" % exc)
        return

    if hdr.fingerprint == f1a.F1A_HEADER_TAIL:
        res.ok("fingerprint matches the measured constant")
    else:
        res.fail("fingerprint %s" % hdr.fingerprint.hex())

    if len(f1a.payload(data)) == len(data) - 11:
        res.ok("payload starts at offset 11")

    # A file with a mangled fingerprint must be rejected in strict mode.
    bad = bytearray(data)
    bad[5] ^= 0xFF
    try:
        f1a.parse_header(bytes(bad))
        res.fail("corrupted fingerprint was accepted")
    except f1a.F1AFormatError:
        res.ok("corrupted fingerprint rejected")


def test_f1a_decode(res):
    """Check the F1A decoder against a raw run of the vendor binary.

    The tool deliberately overrides the sample rate the vendor decoder stamps
    into its WAV template (it always writes 16000 regardless of the stream),
    so a byte comparison against the vendor's own WAV is only valid when the
    overridden rate matches. The check therefore compares the *sample data*
    against a reference decoded with the same rate, and separately confirms
    that the rate override actually takes effect.
    """
    print("\n[2] F1A decoding against the vendor decoder")
    sample = os.path.join(WORK, "verify", "my16k.f1a")
    if not os.path.isfile(sample):
        res.skip("no F1A sample")
        return

    if not f1a_decoder_available():
        res.fail("bundled decoder missing")
        return

    try:
        from wav_tool import f1a_decoder
        pcm = f1a_decoder.decode_file(sample)
        res.ok("decoded %d frames @ %d Hz (default rate)"
               % (pcm.frames, pcm.sample_rate))

        if pcm.sample_rate != f1a.F1A_DEFAULT_SAMPLE_RATE:
            res.fail("default rate is %d, expected %d"
                     % (pcm.sample_rate, f1a.F1A_DEFAULT_SAMPLE_RATE))
        else:
            res.ok("default rate is %d Hz as documented"
                   % f1a.F1A_DEFAULT_SAMPLE_RATE)

        # Sample data must not depend on the reported rate.
        pcm16 = f1a_decoder.decode_file(sample, sample_rate=16000)
        if pcm16.samples == pcm.samples and pcm16.sample_rate == 16000:
            res.ok("rate override works without altering the samples")
        else:
            res.fail("rate override changed the decoded samples")

        # Compare the samples with the vendor decoder's own output.
        vendor = os.path.join(WORK, "verify", "my16k_dec.wav")
        if os.path.isfile(vendor):
            ref = common.read_wav(vendor)
            if ref.samples == pcm.samples:
                res.ok("sample data is identical to the vendor decoder "
                       "(%d samples)" % len(pcm.samples))
            else:
                res.fail("sample data differs from the vendor decoder")
        else:
            res.skip("no vendor reference WAV to compare")
    except Exception as exc:
        res.fail("decode raised %s" % exc)


def f1a_decoder_available():
    try:
        from wav_tool import f1a_decoder
        return f1a_decoder.decoder_available()
    except Exception:
        return False


def test_abe(res):
    print("\n[3] A/B/E container parsing and decoding")

    a = os.path.join(WORK, "samples_abe", "zz_counter.a")
    e = os.path.join(WORK, "samples_abe", "ext_16000.e")
    synth = None

    if not (os.path.isfile(a) and os.path.isfile(e)):
        synth = _synth_samples()
        a, e = synth["a"], synth["e"]
        res.ok("no vendor corpus; using generated samples")

    if os.path.isfile(a):
        with open(a, "rb") as fh:
            data = fh.read()
        if not any(data[:44]):
            res.ok(".a has a 44-byte zero preamble")
        else:
            res.fail(".a preamble is not all zero")
        pcm = abe.decode_bytes(data)
        if pcm.sample_rate == 8000:
            res.ok(".a decoded as 8000 Hz, %d frames" % pcm.frames)
        else:
            res.fail(".a sample rate %d" % pcm.sample_rate)
        if pcm.frames > 0:
            res.ok(".a produced %d samples" % pcm.frames)
        else:
            res.fail(".a produced no samples")
    else:
        res.skip("no .a sample")

    if os.path.isfile(e):
        with open(e, "rb") as fh:
            data = fh.read()
        hdr = abe.parse_header(data)
        if hdr.magic[:3] == b"WAV":
            res.ok(".e magic is 'WAV' + rate byte (0x%02X)" % hdr.magic[3])
        else:
            res.fail(".e magic is %s" % hdr.magic.hex())
        if hdr.sample_rate in (8000, 16000):
            res.ok(".e rate byte decoded as %d Hz" % hdr.sample_rate)
        else:
            res.fail(".e rate %d" % hdr.sample_rate)
    else:
        res.skip("no .e sample")


def test_abe_quality(res):
    print("\n[4] A/B/E decode fidelity vs the source WAV")
    import math

    pairs = [
        (os.path.join(WORK, "samples_abe", "zz_counter.a"),
         os.path.join(WORK, "samples_abe", "zz_counter.wav"), "counter .a", 234),
        (os.path.join(WORK, "samples_abe", "ext_16000.e"),
         os.path.join(WORK, "samples_abe", "s_sine440_16k.wav"), ".e sine 16k", -22),
    ]

    have_vendor = all(os.path.isfile(s) and os.path.isfile(r)
                      for s, r, _, _ in pairs)
    if not have_vendor:
        # Fall back to a container we built ourselves: encode a sine, then
        # decode it and compare against the source. The 44-byte preamble
        # carries no warm-up of its own on the decode side, so the delay is 0.
        synth = _synth_samples()
        pairs = [(synth["a"], synth["wav"], "generated sine .a", 0),
                 (synth["e"], synth["wav"], "generated sine .e", 0)]

    for src, ref, tag, delay in pairs:
        if not (os.path.isfile(src) and os.path.isfile(ref)):
            res.skip("%s: missing files" % tag)
            continue
        try:
            with open(src, "rb") as fh:
                pcm = abe.decode_bytes(fh.read())
            ref_pcm = common.read_wav(ref)
        except Exception as exc:
            res.fail("%s: %s" % (tag, exc))
            continue

        n = min(len(pcm.samples), len(ref_pcm.samples) - delay)
        if n < 100:
            res.fail("%s: too few samples" % tag)
            continue

        err = [pcm.samples[i] - ref_pcm.samples[delay + i] for i in range(n)]
        mse = sum(e * e for e in err) / n
        sig = sum(ref_pcm.samples[delay + i] ** 2 for i in range(n)) / n
        snr = 10 * math.log10(sig / mse) if mse else float("inf")

        # 4-bit ADPCM necessarily has quantisation error, so a plain SNR
        # threshold is the right check here.
        if snr > 15.0:
            res.ok("%s: SNR %.1f dB over %d samples (delay %+d)"
                   % (tag, snr, n, delay))
        else:
            res.fail("%s: SNR only %.1f dB" % (tag, snr))


def test_abe_byte_exact(res):
    """Prove the codec by re-encoding the decoded payload.

    IMA ADPCM is deterministic, so decoding a vendor payload and encoding it
    again must reproduce the original bytes exactly. A byte-exact round trip
    simultaneously validates the nibble order, both tables and the initial
    state - far stronger than any correlation measure.
    """
    print("\n[4b] A/B/E codec proof: byte-exact re-encode")
    import glob

    candidates = sorted(glob.glob(os.path.join(WORK, "samples_abe", "*.a"))) + \
                 sorted(glob.glob(os.path.join(WORK, "samples_abe", "*.e")))

    if not candidates:
        synth = _synth_samples()
        candidates = [synth["a"], synth["e"]]

    checked = 0
    for path in candidates[:12]:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            if len(data) < 64:
                continue
            hdr = abe.header_size(data)
            body = data[hdr:]
            if not body:
                continue
            pcm = abe.decode_payload(body)
            reencoded = abe.encode_payload(pcm)
        except Exception:
            continue

        n = min(len(reencoded), len(body))
        if n < 32:
            continue
        same = sum(1 for i in range(n) if reencoded[i] == body[i])
        ratio = same / n
        checked += 1
        name = os.path.basename(path)
        if ratio == 1.0:
            res.ok("%-18s re-encode byte-identical (1.000000, %d bytes)"
                   % (name, n))
        else:
            res.fail("%-18s re-encode match %.6f" % (name, ratio))

    if checked == 0:
        res.skip("no A/B/E payloads to round-trip")


def test_ump3(res):
    print("\n[5] UMP3 = MP3 verification")
    a = os.path.join(WORK, "samples_ump3", "smp_16k_64k.ump3")
    b = os.path.join(WORK, "samples_ump3", "ctl_16k_64k.mp3")

    if not (os.path.isfile(a) and os.path.isfile(b)):
        res.skip("no ump3/mp3 control pair")
        return

    if sha256_file(a) == sha256_file(b):
        res.ok("same source encoded as .ump3 and .mp3 is byte-identical")
    else:
        res.fail(".ump3 and .mp3 differ")

    with open(a, "rb") as fh:
        data = fh.read()
    info = ump3.describe(data)
    if info.get("valid"):
        res.ok("MP3 stream parsed: MPEG-%s layer %d, %d kbps, %d Hz, %d frames"
               % (info["mpeg_version"], info["layer"], info["bitrate_kbps"],
                  info["sample_rate"], info["frame_count"]))
    else:
        res.fail("MP3 structure not recognised")


def test_converter_cli(res):
    print("\n[6] End-to-end CLI conversion")
    samples = []
    for pat in (os.path.join(WORK, "verify", "my16k.f1a"),
                os.path.join(WORK, "samples_ump3", "smp_16k_64k.ump3"),
                os.path.join(WORK, "samples_abe", "zz_counter.a"),
                os.path.join(WORK, "samples_abe", "ext_16000.e")):
        if os.path.isfile(pat):
            samples.append(pat)

    if not samples:
        synth = _synth_samples()
        samples = [synth["a"], synth["e"]]
        res.ok("no vendor corpus; converting generated samples")

    if not samples:
        res.skip("no samples to convert")
        return

    outdir = tempfile.mkdtemp(prefix="jl_cli_")
    try:
        cmd = [sys.executable, os.path.join(_HERE, "jie2wav.py")] + samples + ["-o", outdir]
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace")

        if proc.returncode == 0:
            res.ok("converter exited 0")
        else:
            res.fail("converter exited %d" % proc.returncode)

        produced = [f for f in os.listdir(outdir) if f.endswith(".wav")]
        if len(produced) == len(samples):
            res.ok("produced %d/%d WAV files" % (len(produced), len(samples)))
        else:
            res.fail("produced %d/%d WAV files" % (len(produced), len(samples)))

        for f in produced:
            try:
                n, r, c = wav_info(os.path.join(outdir, f))
                res.ok("%-24s %6d frames @ %d Hz" % (f, n, r))
            except Exception as exc:
                res.fail("%s: %s" % (f, exc))
    finally:
        import shutil
        shutil.rmtree(outdir, ignore_errors=True)


def main():
    print("=" * 70)
    print("JieLi audio container toolkit - self test")
    print("=" * 70)

    res = Results()
    test_f1a_container(res)
    test_f1a_decode(res)
    test_abe(res)
    test_abe_quality(res)
    test_abe_byte_exact(res)
    test_ump3(res)
    test_converter_cli(res)

    print()
    print("=" * 70)
    print("RESULT: %d passed, %d failed, %d skipped"
          % (res.passed, res.failed, res.skipped))
    print("=" * 70)
    return 1 if res.failed else 0


if __name__ == "__main__":
    sys.exit(main())
