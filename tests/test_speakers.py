from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt

from sonoweave.audio import AudioWindow
from sonoweave.engine import CaptionEngine
from sonoweave.speakers import SpeakerRegistry, SpeakerTurn, align_speakers
from sonoweave.streaming import CaptionRun, SourceChunk, TextJob, TimedWord, TranscriptCache, TranslationCache


def test_session_identity_survives_a_b_a_and_rejects_uncertainty():
    registry = SpeakerRegistry(max_speakers=2)
    assert registry.identify([1, 0, 0], 2) == 0
    assert registry.identify([0, 1, 0], 2) == 1
    assert registry.identify([0.99, 0.01, 0], 1) == 0
    assert registry.identify([0.5, 0.5, 0.707], 2) is None
    assert registry.identify([0, 0, 1], 2) is None  # no palette reuse
    assert registry.identify([1, 0, 0], 0.2) is None
    assert registry.identify([float("nan"), 0, 1], 2) is None


def test_word_alignment_preserves_text_and_marks_overlap_or_gaps_unknown():
    words = tuple(TimedWord(text, i, i + 1) for i, text in enumerate(("Hello", " there.", " Yes.", " Both.", " Gap.")))
    runs = align_speakers(words, (SpeakerTurn(0, 2, 0), SpeakerTurn(2, 4, 1), SpeakerTurn(3, 4, 0)))
    assert runs == (CaptionRun("Hello there.", 0), CaptionRun(" Yes.", 1), CaptionRun(" Both. Gap."))
    assert "".join(run.text for run in runs) == "".join(word.text for word in words)


def test_short_vad_gaps_and_zero_length_words_do_not_fragment_one_speaker():
    words = (TimedWord("Hello", 0.2, 1), TimedWord(" there", 1, 1.5), TimedWord("!", 1.5, 1.5))
    assert align_speakers(words, (SpeakerTurn(0.5, 0.9, 1), SpeakerTurn(1.2, 2, 1))) == (
        CaptionRun("Hello there!", 1),)


def test_label_only_revision_and_overlap_trimming_keep_source_alignment():
    cache = TranscriptCache()
    cache.update(SourceChunk(1, "Keep listening", "en", 1, True, 0, 2))
    chunk = SourceChunk(2, "listening now", "en", 1, False, 2, 4,
                        (CaptionRun("listening", 0), CaptionRun(" now", 1)))
    cache.update(chunk, overlap_previous=True)
    assert cache.snapshot()[-1].text == "now"
    assert cache.snapshot()[-1].runs == (CaptionRun("now", 1),)
    version = cache.version
    assert cache.relabel(2, (CaptionRun("now", 0),))
    assert cache.version == version + 1
    assert not cache.relabel(2, (CaptionRun("different", 0),))


def test_styled_cache_tail_slices_unicode_and_cleans_evicted_metadata():
    cache = TranslationCache(max_chunks=2)
    parts = tuple(SourceChunk(i, str(i), "en", 1, True, i, i + 1) for i in (1, 2, 3))
    cache.update(parts[:2], ("你好😀", "世界"), {1, 2},
                 ((CaptionRun("你好😀", 0),), (CaptionRun("世", 1), CaptionRun("界", 2))))
    assert cache.tail_runs(3) == (CaptionRun("…"), CaptionRun("世", 1), CaptionRun("界", 2))
    cache.update(parts[2:], ("新",), {2, 3}, ((CaptionRun("新", 1),),))
    assert set(cache.runs) == {2, 3}
    assert cache.tail() == "世界\n新"
    cache.clear()
    assert not cache.runs


def test_translation_keeps_speaker_turns_in_same_paragraph_and_publishes_color_only(app):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    part = SourceChunk(1, "Hello. Yes.", "en", 1, True, 0, 3,
                       (CaptionRun("Hello.", 0), CaptionRun(" Yes.", 1)))
    engine.source_cache.update(part)
    job = TextJob(part.text, "en", "zh-CN", 0, 1, 1, True, (), 0, 3, (part,), 1)

    class Translator:
        def translate_segments(self, texts, target, source, **kwargs):
            assert texts == ("Hello.", " Yes.")
            return ("你好。", "是的。")

    translated, runs = engine._translate_runs(Translator(), job, lambda: False)
    assert translated == ("你好。 是的。",)
    assert runs == ((CaptionRun("你好。", 0), CaptionRun(" "), CaptionRun("是的。", 1)),)
    received = []
    engine.caption.connect(received.append)
    engine._publish(job, (part,), translated, True, runs)
    engine._publish(job, (part,), translated, True, runs)
    assert len(received) == 1
    recolored = replace(part, runs=(CaptionRun("Hello.", 1), CaptionRun(" Yes.", 0)))
    engine.source_cache.relabel(1, recolored.runs)
    new_runs = ((CaptionRun("你好。", 1), CaptionRun(" "), CaptionRun("是的。", 0)),)
    engine._publish(job, (recolored,), translated, True, new_runs)
    assert len(received) == 2 and received[-1].runs == new_runs[0]
    engine._publish(job, (part,), translated, True, runs)  # late old color must not return
    assert len(received) == 2


def test_colored_progress_cannot_shorten_an_existing_translation(app):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    part = SourceChunk(1, "Source", "en", 1, False, 0, 2, (CaptionRun("Source", 1),))
    engine.source_cache.update(part)
    job = TextJob(part.text, "en", "zh-CN", 0, 1, 1, False, (), 0, 2, (part,))
    engine.translation_cache.update((part,), ("第一句。第二句。",), {1})
    engine._publish(job, (part,), ("第一句。",), False, ((CaptionRun("第一句。", 1),),))
    assert engine.translation_cache.tail() == "第一句。第二句。"


def test_background_diarization_revises_existing_text_without_rerunning_asr(app, monkeypatch):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    words = (TimedWord("Hello", 0, 1.5), TimedWord(" there", 1.5, 3))
    part = SourceChunk(1, "Hello there", "en", 1, True, 0, 3, (), words)
    engine.source_cache.update(part)
    monkeypatch.setattr("sonoweave.engine.SpeakerTracker", lambda **kwargs: SimpleNamespace(
        analyze_window=lambda window: {window.utterance_id: (window.sequence,
            (SpeakerTurn(0, 1.5, 0), SpeakerTurn(1.5, 3, 1)))}))
    now = time.monotonic()
    engine.speaker_queue.put(1, AudioWindow(1, 1, np.zeros(48000), True, now - 3, now))
    worker = threading.Thread(target=engine._diarize)
    worker.start()
    try:
        update = engine.text_queue.get(timeout=2)
        assert update.segments[0].runs == (CaptionRun("Hello", 0), CaptionRun(" there", 1))
        assert update.text == "Hello there"
    finally:
        engine.stop()
        worker.join(3)


def test_translation_worker_delivers_late_color_for_same_asr_sequence(app, monkeypatch):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    first, colored = threading.Event(), threading.Event()
    received = []
    part = SourceChunk(1, "Hello", "en", 1, True, 0, 2, (CaptionRun("Hello"),))
    engine.source_cache.update(part)
    with engine.lock:
        engine._queue_transcript()

    class Translator:
        device = "CPU"

        def warmup(self):
            pass

        def close(self):
            pass

        def translate_segments(self, texts, *args, **kwargs):
            return tuple("你好" for _ in texts)

    monkeypatch.setattr("sonoweave.engine.Translator", Translator)

    def caption(update):
        received.append(update)
        if update.runs[-1].speaker == 1:
            colored.set()
        else:
            first.set()

    engine.caption.connect(caption, Qt.ConnectionType.DirectConnection)
    worker = threading.Thread(target=engine._translate)
    worker.start()
    try:
        assert first.wait(3)
        with engine.lock:
            assert engine.source_cache.relabel(1, (CaptionRun("Hello", 1),))
            engine._queue_transcript()
        assert colored.wait(3)
        assert [update.text for update in received] == ["你好", "你好"]
        assert [update.sequence for update in received] == [1, 1]
    finally:
        engine.stop()
        worker.join(3)


def test_default_disabled_never_loads_speaker_models_or_enqueues_audio(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    load = Mock(side_effect=AssertionError("speaker model loaded while disabled"))
    monkeypatch.setattr("sonoweave.engine.SpeakerTracker", load)
    waiting = threading.Event()
    original_wait = engine.stop_event.wait
    def wait(timeout):
        waiting.set()
        return original_wait(timeout)
    monkeypatch.setattr(engine.stop_event, "wait", wait)
    now = time.monotonic()
    engine._enqueue_audio(AudioWindow(1, 1, np.zeros(48000), True, now - 3, now))
    assert not engine.speaker_queue.items
    worker = threading.Thread(target=engine._diarize)
    worker.start()
    try:
        assert waiting.wait(2)
        load.assert_not_called()
    finally:
        engine.stop()
        worker.join(3)


def test_disabling_discards_inflight_speaker_result_and_preserves_text(app, monkeypatch):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    part = SourceChunk(1, "Hello", "en", 1, True, 0, 3, (CaptionRun("Hello", 1),), (TimedWord("Hello", 0, 3),))
    engine.source_cache.update(part)
    engine.translation_cache.update((part,), ("你好",), {1}, ((CaptionRun("你好", 1),),))
    entered, release = threading.Event(), threading.Event()
    def analyze(window):
        entered.set()
        assert release.wait(3)
        return {window.utterance_id: (window.sequence, (SpeakerTurn(0, 3, 1),))}
    monkeypatch.setattr("sonoweave.engine.SpeakerTracker", lambda **kwargs: SimpleNamespace(analyze_window=analyze))
    now = time.monotonic()
    engine._enqueue_audio(AudioWindow(1, 1, np.zeros(48000), True, now - 3, now))
    worker = threading.Thread(target=engine._diarize)
    worker.start()
    try:
        assert entered.wait(3)
        engine.set_speaker_colors(False)
        assert engine.translation_cache.tail() == "你好"
        assert engine.translation_cache.tail_runs() == (CaptionRun("你好"),)
        assert not engine.source_cache.snapshot()[0].runs
        assert engine.text_queue.get().revision == engine.revision == 1
        release.set()
    finally:
        release.set()
        engine.stop()
        worker.join(3)
    assert not engine.speaker_windows
    assert not engine.source_cache.snapshot()[0].runs


def test_engine_leaves_longer_history_available_for_configurable_line_count(app):
    engine = CaptionEngine("zh-CN")
    part = SourceChunk(1, "Long source", "en", 1, True, 0, 3)
    engine.source_cache.update(part)
    job = TextJob(part.text, "en", "zh-CN", 0, 1, 1, True, (), 0, 3, (part,))
    text = "\n".join(f"{i}: " + "字幕" * 20 for i in range(8))
    received = []
    engine.caption.connect(received.append)
    engine._publish(job, (part,), (text,), True)
    assert len(text) > 180
    assert received[-1].text == text


@pytest.mark.parametrize("enabled", [False, True])
def test_word_timestamps_are_requested_only_with_speaker_colors(app, monkeypatch, enabled):
    engine = CaptionEngine("zh-CN", speaker_colors=enabled)
    model = Mock()
    model.transcribe.return_value = (iter([SimpleNamespace(text="Hello", words=[
        SimpleNamespace(word="Hello", start=0, end=3)])]), SimpleNamespace(language="en", language_probability=0.99))
    monkeypatch.setattr("sonoweave.engine.load_recognizer", lambda: model)
    now = time.monotonic()
    engine.audio_queue.put(1, AudioWindow(1, 1, np.zeros(48000), True, now - 3, now))
    worker = threading.Thread(target=engine._recognize)
    worker.start()
    try:
        job = engine.text_queue.get(timeout=2)
        assert model.transcribe.call_args.kwargs["word_timestamps"] is enabled
        assert model.transcribe.call_args.kwargs["without_timestamps"] is not enabled
        assert bool(job.segments[0].words) is enabled
    finally:
        engine.stop()
        worker.join(3)
