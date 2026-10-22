from collections import OrderedDict
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import threading
import time

import numpy as np

from sonoweave.audio import AudioWindow
from sonoweave.engine import CaptionEngine
from sonoweave.speakers import SpeakerRegistry, SpeakerTracker, SpeakerTurn, SpeechSeparator, align_speakers
from sonoweave.streaming import CaptionRun, SourceChunk, TextJob, TimedWord


def test_distinct_local_tracks_cannot_collapse_to_one_identity():
    tracker = SpeakerTracker.__new__(SpeakerTracker)
    tracker.window_size, tracker.frame_shift, tracker.frame_offset = 32000, 0.05, 0
    scores = np.full((1, 40, 7), -100.0, dtype=np.float32)
    scores[0, :20, 1] = 0
    scores[0, 20:, 2] = 0
    tracker.segmentation = SimpleNamespace(run=lambda *args: [scores])
    tracker.extractor = SimpleNamespace(create_stream=lambda: SimpleNamespace(
        accept_waveform=lambda **kwargs: None, input_finished=lambda: None),
        is_ready=lambda stream: True, compute=lambda stream: [1.0, 0.0])
    tracker.registry = SpeakerRegistry()
    turns = tracker.analyze(np.ones(32000, dtype=np.float32))
    assert [turn.speaker for turn in turns] == [0, 1]


def test_detected_overlap_is_not_learned_as_a_new_mixed_voice():
    registry = SpeakerRegistry()
    assert registry.identify([1, 0], 2, learn=False) is None
    assert not registry.prototypes
    registry.identify([1, 0], 2)
    assert registry.identify([0, 1], 2, anchor=0, learn=False) == 0
    assert len(registry.references[0]) == 1
    words = (TimedWord("both speak", 0, 2),)
    assert align_speakers(words, (SpeakerTurn(0, 2, None, True),)) == (CaptionRun("both speak"),)


def test_history_revises_earlier_utterances_and_deduplicates_forced_audio_overlap():
    tracker = SpeakerTracker.__new__(SpeakerTracker)
    tracker.history, tracker.previous = OrderedDict(), {}
    observed = []
    def analyze(audio, previous=()):
        observed.append(audio.copy())
        return (SpeakerTurn(0, len(audio) / 16000, 0),)
    tracker.analyze = analyze
    first = AudioWindow(1, 1, np.ones(32000), True, 0, 2)
    second = AudioWindow(2, 1, np.full(32000, 2), True, 1.5, 3.5, True)
    tracker.analyze_window(first)
    result = tracker.analyze_window(second)
    assert set(result) == {1, 2}
    assert len(observed[-1]) == 56000
    assert result[2][1] == (SpeakerTurn(0, 2, 0),)
    assert tracker.analyze_window(second) == {}
    for key in range(3, 20):
        tracker.analyze_window(AudioWindow(key, 1, np.ones(32000), True, key * 2, key * 2 + 2))
    assert sum(item.audio.size for item in tracker.history.values()) <= 24 * 16000
    assert set(tracker.previous) == set(tracker.history)


def test_separator_restores_waveform_gain_before_vad():
    separator = SpeechSeparator.__new__(SpeechSeparator)
    separator.session = SimpleNamespace(run=lambda *args: [np.ones((1, 2, 32000), dtype=np.float32) * 30000])
    audio = np.ones(32000, dtype=np.float32) * 0.1
    result = separator.separate(audio)
    assert result.shape == (2, 32000)
    assert np.isfinite(result).all()
    assert float(np.max(np.abs(result))) < 1
    assert np.isclose(np.mean(result ** 2, axis=1).sum(), np.mean(audio ** 2))


def test_two_stream_transcripts_remain_separate_and_keep_distinct_colors(app):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    window = AudioWindow(1, 2, np.ones(48000, dtype=np.float32) * 0.1, True, 0, 3)
    model = Mock()
    model.transcribe.side_effect = [
        (iter([SimpleNamespace(text="Alice speaks.", words=[SimpleNamespace(word="Alice speaks.", start=0, end=2)])]),
         SimpleNamespace(language="en")),
        (iter([SimpleNamespace(text="Bob speaks.", words=[SimpleNamespace(word="Bob speaks.", start=0, end=2)])]),
         SimpleNamespace(language="en"))]
    tracker = SimpleNamespace(identify_audio=lambda audio, excluded=(): len(excluded))
    chunk = engine._recognize_separated(window, np.ones((2, 48000), dtype=np.float32) * 0.1, model, tracker, "en")
    assert chunk.text == "Alice speaks. Bob speaks."
    assert chunk.runs == (CaptionRun("Alice speaks.", 0), CaptionRun(" Bob speaks.", 1))
    assert chunk.separated
    engine.source_cache.update(SourceChunk(1, "Mixed ASR draft", "en", 8, False, 0, 4))
    assert engine._apply_separation(chunk, window, 0)
    assert engine.source_cache.snapshot()[0].sequence == 8
    job = engine.text_queue.get()
    translator = SimpleNamespace(translate_segments=lambda *args, **kwargs: ("甲说话。", "乙说话。"))
    texts, runs = engine._translate_runs(translator, job, lambda: False)
    assert [run.speaker for run in runs[0] if run.text.strip()] == [0, 1]
    assert not engine._apply_separation(chunk, window, 0)
    engine.set_speaker_colors(False)
    assert not engine._apply_separation(replace(chunk, sequence=9), replace(window, sequence=9), 0)


def test_unchanged_asr_sequence_advance_does_not_discard_colored_translation(app):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    part = SourceChunk(1, "Stable text", "en", 1, False, 0, 2, (CaptionRun("Stable text", 1),))
    engine.source_cache.update(part)
    job = TextJob(part.text, "en", "zh-CN", 0, 1, 1, False, (), 0, 2, (part,))
    assert not engine.source_cache.update(replace(part, sequence=2))
    received = []
    engine.caption.connect(received.append)
    engine._publish(job, (part,), ("稳定文字",), True, ((CaptionRun("稳定文字", 1),),))
    assert received[-1].runs == (CaptionRun("稳定文字", 1),)


def test_overlap_jobs_coalesce_but_preserve_final_windows(app):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    first = AudioWindow(1, 1, np.zeros(48000), False, 0, 3)
    engine._schedule_overlap(first)
    engine._schedule_overlap(replace(first, sequence=2, audio=np.zeros(56000)))
    assert engine.overlap_queue.items[1][0].sequence == 1
    engine._schedule_overlap(replace(first, sequence=3, final=True, audio=np.zeros(56000)))
    assert engine.overlap_queue.items[1][0].final


def test_partial_separation_keeps_existing_caption_instead_of_dropping_a_voice(app):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    window = AudioWindow(1, 1, np.ones(48000, dtype=np.float32) * 0.1, True, 0, 3)
    model = Mock()
    model.transcribe.side_effect = [
        (iter([SimpleNamespace(text="One audible sentence", words=[])]), SimpleNamespace(language="en")),
        (iter([]), SimpleNamespace(language="en"))]
    tracker = SimpleNamespace(identify_audio=lambda *args, **kwargs: 0)
    assert engine._recognize_separated(window, np.ones((2, 48000), dtype=np.float32) * 0.1,
                                       model, tracker, "en") is None


def test_detected_overlap_keeps_default_color_without_separation(app, monkeypatch):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    part = SourceChunk(1, "Both voices.", "en", 1, True, 0, 3,
                       (CaptionRun("Both voices.", 0),), (TimedWord("Both voices.", 0, 3),))
    engine.source_cache.update(part)
    schedule = Mock(side_effect=AssertionError("overlap separation must stay disabled"))
    monkeypatch.setattr(engine, "_schedule_overlap", schedule)
    monkeypatch.setattr("sonoweave.engine.SpeakerTracker", lambda **kwargs: SimpleNamespace(
        analyze_window=lambda window: {window.utterance_id: (window.sequence, (SpeakerTurn(0, 3, None, True),))}))
    now = time.monotonic()
    window = AudioWindow(1, 1, np.ones(48000, dtype=np.float32) * 0.1, True, now - 3, now)
    engine._enqueue_audio(window)
    worker = threading.Thread(target=engine._diarize)
    worker.start()
    try:
        job = engine.text_queue.get(timeout=2)
        assert job.segments[0].runs == (CaptionRun("Both voices."),)
        assert not engine.overlap_queue.items
        assert not engine.speaker_audio
        schedule.assert_not_called()
    finally:
        engine.stop()
        worker.join(3)


def test_caption_pipeline_only_starts_sequential_voice_analysis(app, monkeypatch):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    threads = []
    def thread(**kwargs):
        threads.append(kwargs)
        return SimpleNamespace(start=lambda: None)
    monkeypatch.setattr("sonoweave.engine.threading.Thread", thread)
    engine.start()
    assert [item["name"] for item in threads] == ["system-audio", "recognition", "speakers", "translation"]
    assert all(item["target"] != engine._separate for item in threads)


def test_overlap_worker_is_lazy_and_emits_two_voice_repair(app, monkeypatch):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    waiting = threading.Event()
    original_get = engine.overlap_queue.get
    def get():
        waiting.set()
        return original_get()
    engine.overlap_queue.get = get
    sources = np.ones((2, 48000), dtype=np.float32) * 0.1
    separator = Mock(return_value=SimpleNamespace(separate=lambda audio: sources))
    model = Mock()
    model.transcribe.side_effect = [
        (iter([SimpleNamespace(text="First speaker", words=[])]), SimpleNamespace(language="en")),
        (iter([SimpleNamespace(text="Second speaker", words=[])]), SimpleNamespace(language="en"))]
    load = Mock(return_value=model)
    monkeypatch.setattr("sonoweave.engine.SpeechSeparator", separator)
    monkeypatch.setattr("sonoweave.engine.load_recognizer", load)
    monkeypatch.setattr("sonoweave.engine.SpeakerTracker", lambda **kwargs: SimpleNamespace(
        identify_audio=lambda audio, excluded=(): len(excluded)))
    worker = threading.Thread(target=engine._separate)
    worker.start()
    try:
        assert waiting.wait(2)
        load.assert_not_called()
        separator.assert_not_called()
        window = AudioWindow(1, 1, sources[0], True, 0, 3)
        engine.source_cache.update(SourceChunk(1, "Mixed text", "en", 1, True, 0, 3))
        engine.overlap_queue.put(1, (window, 0))
        job = engine.text_queue.get(timeout=2)
        load.assert_called_once_with(device="cpu", cpu_threads=2)
        assert job.segments[0].separated
        assert {run.speaker for run in job.segments[0].runs} == {0, 1}
    finally:
        engine.stop()
        worker.join(3)
