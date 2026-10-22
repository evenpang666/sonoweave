"""Opt-in checks with local public fixtures; no downloads or sound playback."""
import os
from pathlib import Path
import socket
import wave

import numpy as np
import pytest

from sonoweave.audio import AudioWindow
from sonoweave.engine import CaptionEngine
from sonoweave.models import SPEAKER_FILES, SEPARATION_FILE
from sonoweave.config import speaker_model_location
from sonoweave.recognition import load_recognizer
from sonoweave.speakers import SpeakerTracker, SpeechSeparator

pytestmark = pytest.mark.skipif(os.environ.get("SONOWEAVE_TEST_SPEAKER_MODELS") != "1",
                               reason="requires opt-in and local public audio/model fixtures")


def offline(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Inference attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", fail)
    if any(not speaker_model_location(spec.filename).is_file() for spec in SPEAKER_FILES):
        pytest.skip("local speaker models are missing")


def test_four_real_voices_keep_identity_when_they_return(monkeypatch):
    offline(monkeypatch)
    path = Path(".cache/speaker-test/four-speakers.wav")
    if not path.is_file():
        pytest.skip("official four-speaker public fixture is missing")
    with wave.open(str(path)) as stream:
        assert stream.getframerate() == 16000 and stream.getnchannels() == 1
        audio = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(np.float32) / 32768
    tracker = SpeakerTracker()
    def label(turns, point):
        identities = {turn.speaker for turn in turns if turn.start <= point < turn.end and turn.speaker is not None}
        assert len(identities) == 1
        return identities.pop()
    first = tracker.analyze(audio[:240000])
    a, b, c = label(first, 1.1), label(first, 8.8), label(first, 14)
    assert len({a, b, c}) == 3
    second = tracker.analyze(audio[240000:480000])
    assert label(second, 1) == c
    assert label(second, 8) == a
    d = label(second, 13.5)
    assert d not in {a, b, c}
    third = tracker.analyze(audio[480000:720000])
    assert label(third, 5.5) == d
    last = tracker.analyze(audio[720000:])
    assert label(last, 4) == c
    assert label(last, 8.5) == a


def test_known_english_overlap_recovers_both_sentences_with_different_labels(app, monkeypatch):
    offline(monkeypatch)
    if not speaker_model_location(SEPARATION_FILE.filename).is_file():
        pytest.skip("experimental separation model is not prepared by the app")
    path = Path(".cache/speaker-test/clean-overlap.npz")
    if not path.is_file():
        pytest.skip("public LibriSpeech two-speaker mixture is missing")
    audio = np.load(path)["mix"]
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    tracker = SpeakerTracker(registry=engine.speaker_registry)
    turns = tracker.analyze(audio)
    assert sum(turn.end - turn.start for turn in turns if turn.overlap) >= 0.25
    model = load_recognizer(device="cpu", cpu_threads=2)
    window = AudioWindow(1, 1, audio, True, 0, len(audio) / 16000)
    chunk = engine._recognize_separated(window, SpeechSeparator().separate(audio), model, tracker, "en")
    assert chunk is not None and chunk.separated
    assert len({run.speaker for run in chunk.runs}) == 2
    sentences = [run.text.casefold() for run in chunk.runs]
    assert any("middle classes" in text and "apostle" in text for text in sentences)
    assert any("returned" in text and "tents" in text for text in sentences)
