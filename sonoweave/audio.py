from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import count
import logging
import sys
import threading
import time
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 800
_utterance_ids = count(1)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioWindow:
    utterance_id: int
    sequence: int
    audio: np.ndarray
    final: bool
    started: float
    ended: float
    overlap_previous: bool = False


class StreamingSegmenter:
    """Emit growing speech windows before silence, then a final correction window."""

    def __init__(self, threshold=0.002, first_seconds=0.9, update_seconds=0.55,
                 silence_seconds=0.35, max_seconds=15.0, clock=time.monotonic):
        self.threshold = threshold
        self.first_samples = round(first_seconds * SAMPLE_RATE)
        self.update_samples = round(update_seconds * SAMPLE_RATE)
        self.silence_samples = round(silence_seconds * SAMPLE_RATE)
        self.max_samples = round(max_seconds * SAMPLE_RATE)
        self.clock = clock
        self.reset()

    def reset(self):
        self.leading = deque(maxlen=4)
        self.frames = []
        self.samples = self.voiced_samples = self.silent_samples = self.last_emitted = 0
        self.sequence = 0
        self.utterance_id = None
        self.started = 0.0
        self.overlap_previous = False
        self.pending_silence = 0

    def feed(self, frame: np.ndarray) -> AudioWindow | None:
        frame = np.asarray(frame, dtype=np.float32)
        if frame.ndim == 2:
            frame = frame.mean(axis=1)
        frame = np.nan_to_num(frame, nan=0.0, posinf=0.0, neginf=0.0).reshape(-1)
        if not frame.size:
            return None
        voiced = float(np.sqrt(np.mean(frame * frame))) >= self.threshold
        if not self.frames:
            if not voiced:
                self.leading.append(frame.copy())
                if self.overlap_previous:
                    self.pending_silence += frame.size
                    if self.pending_silence >= self.silence_samples:
                        self.overlap_previous = False
                        self.leading = deque(list(self.leading)[-4:], maxlen=4)
                return None
            self.frames = list(self.leading)
            self.samples = sum(part.size for part in self.frames)
            self.leading.clear()
            self.utterance_id = next(_utterance_ids)
            self.started = self.clock() - (self.samples + frame.size) / SAMPLE_RATE
        self.frames.append(frame.copy())
        self.samples += frame.size
        self.voiced_samples += frame.size if voiced else 0
        self.silent_samples = 0 if voiced else self.silent_samples + frame.size
        final = self.silent_samples >= self.silence_samples or self.samples >= self.max_samples
        if self.voiced_samples < round(0.3 * SAMPLE_RATE):
            if final:
                self.reset()
            return None
        due = self.samples >= self.first_samples and (
            self.last_emitted == 0 or self.samples - self.last_emitted >= self.update_samples
        )
        if not final and not due:
            return None
        self.sequence += 1
        self.last_emitted = self.samples
        result = AudioWindow(self.utterance_id, self.sequence, np.concatenate(self.frames),
                             final, self.started, self.clock(), self.overlap_previous)
        if final:
            carried = self.frames[-10:] if self.samples >= self.max_samples and not self.silent_samples else []
            self.reset()
            if carried:
                self.leading = deque(carried, maxlen=10)
                self.overlap_previous = True
        return result


def _loopback_microphone(sc, speaker):
    if sys.platform == "linux":
        import pulsectl

        # Query the sink -> monitor relationship. Monitor names can be customized,
        # and SoundCard's fuzzy matching must never fall back to a physical mic.
        with pulsectl.Pulse("SonoWeave-monitor", connect=False) as pulse:
            pulse.connect(timeout=2)
            sink = next((item for item in pulse.sink_list() if item.name == speaker.id), None)
            if sink is None or not sink.monitor_source_name:
                raise RuntimeError("Default output has no monitor source")
            monitor_id = sink.monitor_source_name
        microphone = sc.get_microphone(id=monitor_id, include_loopback=True)
        if microphone.id != monitor_id or not microphone.isloopback:
            raise RuntimeError("Default output monitor is unavailable")
        return microphone
    return sc.get_microphone(id=str(speaker.id), include_loopback=True)


def capture_loopback(stop: threading.Event, on_audio: Callable, on_status: Callable) -> None:
    if sys.platform not in ("win32", "linux"):
        on_status("系统声音采集支持 Windows 和 Linux（PulseAudio / PipeWire）", True)
        return
    import ctypes
    initialized = False
    try:
        # SoundCard rejects COM's S_FALSE on first import. Import before taking
        # this worker's own reference; subsequent capture threads still need it.
        import soundcard as sc
        if sys.platform == "win32":
            initialized = ctypes.windll.ole32.CoInitializeEx(None, 0) in (0, 1)
        while not stop.is_set():
            segmenter = StreamingSegmenter()
            try:
                speaker = sc.default_speaker()
                if speaker is None:
                    raise RuntimeError("No default output device")
                microphone = _loopback_microphone(sc, speaker)
                with microphone.recorder(samplerate=SAMPLE_RATE, blocksize=2048) as recorder:
                    on_status("正在聆听系统声音", False)
                    next_check = time.monotonic() + 2
                    while not stop.is_set():
                        window = segmenter.feed(recorder.record(numframes=FRAME_SAMPLES))
                        if window is not None:
                            on_audio(window)
                        if time.monotonic() >= next_check:
                            current = sc.default_speaker()
                            if current is None or current.id != speaker.id:
                                break
                            next_check = time.monotonic() + 2
            except Exception:
                log.exception("System audio capture failed")
                message = ("无法采集系统声音，请检查 PulseAudio / pipewire-pulse，正在重连…"
                           if sys.platform == "linux" else "无法采集系统声音，正在重连音频设备…")
                on_status(message, True)
                stop.wait(2)
    except Exception:
        log.exception("Audio backend failed")
        message = ("音频组件无法启动，请检查 libpulse 和 PulseAudio / pipewire-pulse 后重连"
                   if sys.platform == "linux" else "音频组件无法启动，请右键重试")
        on_status(message, True)
    finally:
        if initialized:
            ctypes.windll.ole32.CoUninitialize()
