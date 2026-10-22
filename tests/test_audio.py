import builtins
import ctypes
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from sonoweave.audio import FRAME_SAMPLES, SAMPLE_RATE, StreamingSegmenter, capture_loopback, _loopback_microphone


def frame(value=0):
    return np.full((FRAME_SAMPLES, 2), value, dtype=np.float32)


def test_silence_never_produces_subtitles():
    segmenter = StreamingSegmenter()
    assert all(segmenter.feed(frame()) is None for _ in range(200))
    assert len(segmenter.leading) == 4


def test_first_partial_before_one_second_then_cumulative_revision_and_final():
    segmenter = StreamingSegmenter()
    assert all(segmenter.feed(frame(0.1)) is None for _ in range(17))
    partial = segmenter.feed(frame(0.1))
    assert partial is not None and not partial.final
    assert partial.audio.size / SAMPLE_RATE == 0.9
    assert all(segmenter.feed(frame(0.1)) is None for _ in range(10))
    revision = segmenter.feed(frame(0.1))
    assert revision.utterance_id == partial.utterance_id
    assert revision.sequence > partial.sequence
    assert revision.audio.size > partial.audio.size
    for _ in range(6):
        assert segmenter.feed(frame()) is None
    final = segmenter.feed(frame())
    assert final.final and final.utterance_id == partial.utterance_id
    assert final.sequence > revision.sequence
    assert not segmenter.frames


def test_short_phrase_still_finalizes_without_initial_partial():
    segmenter = StreamingSegmenter()
    for _ in range(8):
        assert segmenter.feed(frame(0.1)) is None
    result = None
    for _ in range(7):
        result = segmenter.feed(frame()) or result
    assert result is not None and result.final


def test_short_click_is_not_speech():
    segmenter = StreamingSegmenter()
    segmenter.feed(frame(0.5))
    assert all(segmenter.feed(frame()) is None for _ in range(10))


def test_leading_audio_preserved_and_utterance_ids_survive_device_reconnect():
    first = StreamingSegmenter()
    for _ in range(8):
        first.feed(frame())
    results = [first.feed(frame(0.1)) for _ in range(18)]
    first_window = next(value for value in results if value)
    assert np.all(first_window.audio[:4 * FRAME_SAMPLES] == 0)
    second = StreamingSegmenter()
    results = [second.feed(frame(0.1)) for _ in range(18)]
    assert next(value for value in results if value).utterance_id > first_window.utterance_id


def test_continuous_audio_memory_is_bounded():
    segmenter = StreamingSegmenter(max_seconds=2)
    results = [segmenter.feed(frame(0.1)) for _ in range(40)]
    assert results[-1].final
    assert results[-1].audio.size == SAMPLE_RATE * 2
    assert not segmenter.frames


def test_forced_boundary_preserves_audio_overlap_but_silence_expires_it():
    segmenter = StreamingSegmenter(max_seconds=2)
    for _ in range(40):
        segmenter.feed(frame(0.1))
    next_window = None
    for _ in range(8):
        next_window = segmenter.feed(frame(0.1)) or next_window
    assert next_window is not None and next_window.overlap_previous
    second = StreamingSegmenter(max_seconds=2)
    for _ in range(40):
        second.feed(frame(0.1))
    for _ in range(7):
        second.feed(frame())
    next_window = None
    for _ in range(18):
        next_window = second.feed(frame(0.1)) or next_window
    assert next_window is not None and not next_window.overlap_previous


def test_nonfinite_samples_are_silence():
    segmenter = StreamingSegmenter()
    assert segmenter.feed(frame(float("nan"))) is None
    assert segmenter.feed(frame(float("inf"))) is None
    results = [segmenter.feed(frame(0.1)) for _ in range(18)]
    assert all(np.isfinite(result.audio).all() for result in results if result)


def test_backend_import_before_manual_com_initialization(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    initialized, released = [], []
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(ole32=SimpleNamespace(
        CoInitializeEx=lambda *args: initialized.append(True) or 1,
        CoUninitialize=lambda: released.append(True),
    )), raising=False)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "soundcard":
            assert not initialized, "SoundCard rejects COM's S_FALSE on first import"
            return SimpleNamespace()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    stop = threading.Event()
    stop.set()
    capture_loopback(stop, lambda audio: None, lambda text, error: None)
    assert initialized == [True] and released == [True]


def linux_pulse(monkeypatch, sinks):
    class Pulse:
        def __init__(self, name, connect):
            assert connect is False

        def connect(self, timeout):
            assert timeout == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sink_list(self):
            return [SimpleNamespace(name=name, monitor_source_name=monitor) for name, monitor in sinks]

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "pulsectl", SimpleNamespace(Pulse=Pulse))


def test_linux_capture_uses_exact_monitor_and_reconnects_default_output(monkeypatch):
    linux_pulse(monkeypatch, [("headphones", "custom.monitor.one"), ("speakers", "custom.monitor.two")])
    stop = threading.Event()
    samples = 0
    monitors, windows, statuses = [], [], []

    class Recorder:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def record(self, numframes):
            nonlocal samples
            samples += numframes
            return frame(0.1)

    def microphone(id, include_loopback):
        assert include_loopback
        monitors.append(id)
        return SimpleNamespace(id=id, isloopback=True, recorder=lambda **kwargs: Recorder())

    sc = SimpleNamespace(
        default_speaker=lambda: SimpleNamespace(id="headphones" if samples < 2 * SAMPLE_RATE else "speakers"),
        get_microphone=microphone,
    )
    monkeypatch.setitem(sys.modules, "soundcard", sc)
    monkeypatch.setattr("sonoweave.audio.time", SimpleNamespace(monotonic=lambda: samples / SAMPLE_RATE))

    def receive(window):
        windows.append(window)
        if len(monitors) == 2:
            stop.set()

    capture_loopback(stop, receive, lambda text, error: statuses.append((text, error)))
    assert monitors == ["custom.monitor.one", "custom.monitor.two"]
    assert len({window.utterance_id for window in windows}) == 2
    assert all(not error for text, error in statuses)


@pytest.mark.parametrize("actual_id,is_loopback", [("physical-mic", False), ("monitor", False), ("other.monitor", True)])
def test_linux_never_records_fuzzy_or_physical_microphone(monkeypatch, actual_id, is_loopback):
    linux_pulse(monkeypatch, [("output", "monitor")])
    sc = SimpleNamespace(get_microphone=lambda **kwargs: SimpleNamespace(id=actual_id, isloopback=is_loopback))
    with pytest.raises(RuntimeError, match="monitor is unavailable"):
        _loopback_microphone(sc, SimpleNamespace(id="output"))


def test_linux_missing_monitor_reports_service_hint_without_recording(monkeypatch):
    linux_pulse(monkeypatch, [])
    monkeypatch.setitem(sys.modules, "soundcard", SimpleNamespace(default_speaker=lambda: SimpleNamespace(id="gone")))
    stop = threading.Event()
    statuses = []

    def receive_status(text, error):
        statuses.append((text, error))
        stop.set()

    capture_loopback(stop, lambda audio: pytest.fail("must not record a mic"), receive_status)
    assert statuses and statuses[0][1] and "pipewire-pulse" in statuses[0][0]


def test_linux_backend_load_failure_has_actionable_hint(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    original_import = builtins.__import__

    def failed_import(name, *args, **kwargs):
        if name == "soundcard":
            raise OSError("missing libpulse")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failed_import)
    statuses = []
    capture_loopback(threading.Event(), lambda audio: None, lambda text, error: statuses.append((text, error)))
    assert statuses[0][1] and "libpulse" in statuses[0][0]
