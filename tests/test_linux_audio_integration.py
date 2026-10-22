"""Opt-in real PulseAudio monitor test; CI supplies an isolated audio server.

Creates a virtual output and briefly changes the server default; do not enable
against a desktop session while listening to audio. No models or network needed.
"""
import os
import sys
import threading

import numpy as np
import pytest

from sonoweave.audio import SAMPLE_RATE, capture_loopback

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("SONOWEAVE_TEST_PULSE") != "1",
    reason="requires Linux and an explicitly enabled isolated PulseAudio server",
)


def test_real_default_output_monitor_receives_playback():
    import pulsectl
    import soundcard as sc

    stop, ready, heard = threading.Event(), threading.Event(), threading.Event()
    errors, windows = [], []

    def status(text, error):
        if error:
            errors.append(text)
        else:
            ready.set()

    def receive(window):
        windows.append(window)
        heard.set()

    with pulsectl.Pulse("SonoWeave-test") as pulse:
        previous = pulse.server_info().default_sink_name
        module = pulse.module_load("module-null-sink", "sink_name=sonoweave_ci rate=16000 channels=2")
        worker = threading.Thread(target=capture_loopback, args=(stop, receive, status), daemon=True)
        try:
            pulse.sink_default_set("sonoweave_ci")
            worker.start()
            assert ready.wait(8), errors
            speaker = sc.default_speaker()
            assert speaker.id == "sonoweave_ci"
            signal = (0.15 * np.sin(2 * np.pi * 440 * np.arange(3 * SAMPLE_RATE) / SAMPLE_RATE)).astype(np.float32)
            speaker.play(np.column_stack((signal, signal)), samplerate=SAMPLE_RATE, blocksize=2048)
            assert heard.wait(5), errors
        finally:
            stop.set()
            if worker.ident is not None:
                worker.join(5)
            if previous and previous != "sonoweave_ci":
                pulse.sink_default_set(previous)
            pulse.module_unload(module)
        assert not worker.is_alive()
        assert not errors
        assert windows and max(float(np.sqrt(np.mean(w.audio ** 2))) for w in windows) > 0.02
