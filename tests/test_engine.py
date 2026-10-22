from dataclasses import replace
import threading
import time
from unittest.mock import Mock
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import Qt

from sonoweave.engine import CaptionEngine
from sonoweave.streaming import ContextMemory, LatestQueue, TextJob, SourceChunk
from sonoweave.translation import TranslationCancelled
from sonoweave.audio import AudioWindow


def job(**changes):
    base = TextJob("Hello", "en", "zh-CN", 0, 1, 1, False, (), time.monotonic(), time.monotonic())
    return replace(base, **changes)


def test_backpressure_coalesces_same_utterance_and_discards_oldest():
    queue = LatestQueue(maxsize=2)
    queue.put(1, "draft")
    queue.put(1, "corrected final")
    queue.put(2, "next")
    assert queue.get() == "corrected final"
    queue.put(3, "three")
    queue.put(4, "four")
    assert [queue.get(), queue.get()] == ["three", "four"]


def test_exit_clears_session_text_audio_voices_and_rejects_late_results(app):
    engine = CaptionEngine("zh-CN", speaker_colors=True)
    part = SourceChunk(1, "Private source", "en", 1, True, 0, 1)
    engine.source_cache.update(part)
    engine.translation_cache.update((part,), ("Private translation",), {1})
    engine.speaker_registry.identify([1, 0], 2)
    window = AudioWindow(1, 1, np.ones(16000), True, 0, 1)
    engine._enqueue_audio(window)
    engine.overlap_queue.put(1, (window, 0))
    with engine.lock:
        engine._queue_transcript()
    engine.last_published = ("Private translation",)
    engine.speaker_windows[1] = (1, ())
    engine.clear_session()
    assert engine.stop_event.is_set()
    assert not engine.source_cache.snapshot()
    assert not engine.translation_cache.snapshot()
    assert not engine.speaker_registry.prototypes
    assert not engine.speaker_windows
    assert engine.last_job is engine.last_published is None
    engine._enqueue_audio(window)
    engine._publish(job(), (part,), ("Late translation",), True)
    for queue in (engine.audio_queue, engine.speaker_queue, engine.overlap_queue, engine.text_queue):
        assert not queue.items
    assert not engine.translation_cache.snapshot()


def test_language_switch_retranslates_current_phrase_with_context(app):
    engine = CaptionEngine("zh-CN")
    engine.last_job = job(context=("Previous sentence",))
    engine.set_target("ja")
    assert engine.revision == 1
    latest = engine.text_queue.get()
    assert latest.target == "ja" and latest.revision == 1
    assert latest.context == ("Previous sentence",)


def test_language_change_cancels_old_translation(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    translator = Mock()

    def translate(text, target, source, context, should_cancel):
        if target == "zh-CN":
            entered.set()
            assert release.wait(3)
            assert should_cancel()
            raise TranslationCancelled
        finished.set()
        return "こんにちは"

    translator.translate.side_effect = translate
    monkeypatch.setattr("sonoweave.engine.Translator", lambda: translator)
    received = []
    engine.caption.connect(received.append)
    engine.last_job = job()
    engine.text_queue.put(1, engine.last_job)
    worker = threading.Thread(target=engine._translate)
    worker.start()
    assert entered.wait(3)
    engine.set_target("ja")
    release.set()
    assert finished.wait(3)
    engine.stop()
    worker.join(3)
    app.processEvents()
    assert [(value.text, value.revision) for value in received] == [("こんにちは", 1)]
    translator.close.assert_called_once()


def test_model_switch_cancels_old_work_and_frees_before_reloading(app, monkeypatch):
    engine = CaptionEngine("zh-CN", model_path="4b")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    old, new = Mock(), Mock()
    order = []
    old.close.side_effect = lambda: order.append("closed 4b")

    def factory(model_path):
        if model_path == "4b":
            return old
        assert order == ["closed 4b"]
        return new

    def translate_old(*args, should_cancel, **kwargs):
        entered.set()
        assert release.wait(3)
        assert should_cancel()
        raise TranslationCancelled

    def translate_new(*args, **kwargs):
        finished.set()
        return "新的译文"

    old.translate.side_effect = translate_old
    new.translate.side_effect = translate_new
    monkeypatch.setattr("sonoweave.engine.Translator", factory)
    part = SourceChunk(1, "Hello", "en", 1, False, 0, 1)
    engine.source_cache.update(part)
    engine.last_job = job()
    engine.text_queue.put(1, engine.last_job)
    received = []
    engine.caption.connect(received.append)
    worker = threading.Thread(target=engine._translate)
    worker.start()
    assert entered.wait(3)
    engine.set_model("2b")
    assert engine.source_cache.snapshot() == (part,)
    release.set()
    assert finished.wait(3)
    engine.stop()
    worker.join(3)
    app.processEvents()
    assert [(update.text, update.revision) for update in received] == [("新的译文", 1)]
    new.close.assert_called_once()


def test_model_switch_while_idle_does_not_run_new_job_on_old_model(app, monkeypatch):
    engine = CaptionEngine("zh-CN", model_path="4b")
    waiting, finished = threading.Event(), threading.Event()
    old, new = Mock(), Mock()
    original_get = engine.text_queue.get

    def get():
        waiting.set()
        return original_get(timeout=1)

    engine.text_queue.get = get
    new.translate.side_effect = lambda *a, **k: (finished.set() or "新模型译文")
    monkeypatch.setattr("sonoweave.engine.Translator", lambda model_path: old if model_path == "4b" else new)
    worker = threading.Thread(target=engine._translate)
    worker.start()
    assert waiting.wait(3)
    engine.last_job = job()
    engine.set_model("2b")
    assert finished.wait(3)
    engine.stop()
    worker.join(3)
    old.translate.assert_not_called()
    new.translate.assert_called_once()


def test_rapid_return_to_original_model_still_completes_reload(app, monkeypatch):
    engine = CaptionEngine("zh-CN", model_path="4b")
    warming, release, reloaded = threading.Event(), threading.Event(), threading.Event()
    old, new = Mock(), Mock()
    loaded = []

    def warmup():
        warming.set()
        assert release.wait(3)

    old.warmup.side_effect = warmup
    new.warmup.side_effect = reloaded.set

    def factory(model_path):
        loaded.append(model_path)
        if len(loaded) == 1:
            return old
        old.close.assert_called_once()
        return new

    monkeypatch.setattr("sonoweave.engine.Translator", factory)
    worker = threading.Thread(target=engine._translate)
    worker.start()
    assert warming.wait(3)
    engine.set_model("2b")
    engine.set_model("4b")
    release.set()
    assert reloaded.wait(3)
    engine.stop()
    worker.join(3)
    assert loaded == ["4b", "4b"]


def test_context_memory_is_bounded_and_clears_after_silence_or_language_change():
    memory = ContextMemory(max_sentences=2)
    memory.commit(1, "en", "One", 0)
    memory.commit(2, "en", "Two", 1)
    memory.commit(3, "en", "Three", 2)
    assert memory.preceding(4, "en", 3) == ("Two", "Three")
    assert memory.preceding(3, "en", 3) == ("Two",)
    assert memory.preceding(4, "ja", 3) == ()
    memory.commit(4, "en", "New", 3)
    assert memory.preceding(5, "en", 30) == ()


def test_context_does_not_accumulate_duplicate_final_or_exceed_character_limit():
    memory = ContextMemory(max_chars=8)
    memory.commit(1, "en", "old", 0)
    memory.commit(1, "en", "corrected", 1)
    assert memory.preceding(2, "en", 2) == ("orrected",)


def test_component_error_is_not_hidden_by_other_models_becoming_ready(app):
    engine = CaptionEngine("zh-CN")
    statuses = []
    engine.status.connect(lambda text, error: statuses.append((text, error)))
    engine._component_status("translation", "模型缺失", True)
    engine._component_status("recognition")
    engine._component_status("audio")
    assert statuses[-1] == ("模型缺失", True)
    engine._component_status("translation")
    assert statuses[-1] == ("正在聆听系统声音", False)


def test_translation_waits_for_whisper_allocation_and_can_cancel_startup(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    engine.coordinate_startup = True
    loaded = threading.Event()

    def translate_once(*args):
        loaded.set()
        engine.stop()

    monkeypatch.setattr(engine, "_translate_once", translate_once)
    worker = threading.Thread(target=engine._translate)
    worker.start()
    assert not loaded.wait(0.15)
    engine.recognition_loaded.set()
    assert loaded.wait(2)
    worker.join(2)
    assert not worker.is_alive()

    engine = CaptionEngine("zh-CN")
    engine.coordinate_startup = True
    monkeypatch.setattr(engine, "_translate_once", lambda *args: pytest.fail("Cancelled startup loaded Qwen"))
    worker = threading.Thread(target=engine._translate)
    worker.start()
    engine.stop()
    worker.join(2)
    assert not worker.is_alive()


def test_progress_cannot_roll_an_existing_paragraph_back_to_its_first_sentence(app):
    engine = CaptionEngine("zh-CN")
    part = SourceChunk(1, "First sentence. Longer second sentence.", "en", 1, False, 0, 1)
    engine.source_cache.update(part)
    current = job(segments=(part,))
    engine.translation_cache.update((part,), ("第一句。更长的第二句。",), {1})
    received = []
    engine.caption.connect(received.append)
    engine._publish(current, (part,), ("第一句。",), complete=False)
    assert not received
    assert engine.translation_cache.tail() == "第一句。更长的第二句。"
    engine._publish(current, (part,), ("第一句。更长的第二句。新增第三句。",), complete=False)
    assert len(received) == 1 and not received[0].final


def test_publishing_revisions_keeps_history_and_updates_only_current_sentence(app):
    engine = CaptionEngine("zh-CN")
    engine.caption_rewrite_interval = 0
    part = SourceChunk(1, "A sentence. More speech", "en", 1, False, 0, 1)
    engine.source_cache.update(part)
    current = job(segments=(part,))
    received = []
    engine.caption.connect(received.append)
    engine._publish(current, (part,), ("历史句子。当前句子草稿",), complete=True)
    engine._publish(current, (part,), ("历史被重新翻译。当前句子已更新",), complete=True)
    assert received[-1].text == "历史句子。当前句子已更新"
    assert "".join(run.text for run in received[-1].runs) == received[-1].text


def test_draft_rewrites_wait_but_appends_and_final_correction_are_immediate(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    now = [10.0]
    monkeypatch.setattr("sonoweave.engine.time.monotonic", lambda: now[0])
    part = SourceChunk(1, "Live speech", "en", 1, False, 0, 1)
    engine.source_cache.update(part)
    current = job(segments=(part,))
    received = []
    engine.caption.connect(received.append)
    engine._publish(current, (part,), ("最初的句子草稿",), complete=True)
    now[0] = 10.5
    engine._publish(current, (part,), ("整句换成另一种说法",), complete=True)
    assert len(received) == 1
    assert engine.translation_cache.chunks[1] == "整句换成另一种说法"
    now[0] = 11.3
    engine._publish(current, (part,), ("整句换成另一种说法",), complete=True)
    assert len(received) == 2
    now[0] = 11.4
    engine._publish(current, (part,), ("整句换成另一种说法并追加文字",), complete=True)
    assert len(received) == 3
    now[0] = 11.5
    engine._publish(replace(current, final=True), (part,), ("最终校准立即显示。",), complete=True)
    assert len(received) == 4 and received[-1].final


def test_new_utterance_and_language_revision_do_not_wait_for_rewrite_interval(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    monkeypatch.setattr("sonoweave.engine.time.monotonic", lambda: 10)
    first = SourceChunk(1, "First", "en", 1, False, 0, 1)
    second = SourceChunk(2, "Second", "en", 1, False, 1, 2)
    engine.source_cache.update(first)
    received = []
    engine.caption.connect(received.append)
    engine._publish(job(), (first,), ("原来的文字",), True)
    engine.source_cache.update(second)
    engine._publish(job(utterance_id=2), (first, second), ("上一句最终更正", "新的一句"), True)
    assert len(received) == 2
    engine.set_target("ja")
    engine._publish(job(utterance_id=2, revision=1), (second,), ("新しい文",), True)
    assert len(received) == 3 and received[-1].revision == 1


def test_last_held_rewrite_flushes_without_more_asr_and_reports_display_wait(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    now = [10.0]
    monkeypatch.setattr("sonoweave.engine.time.monotonic", lambda: now[0])
    part = SourceChunk(1, "Live speech", "en", 1, False, 9, 10)
    engine.source_cache.update(part)
    current = job(segments=(part,), started=9, ended=10)
    received = []
    engine.caption.connect(received.append)
    engine._publish(current, (part,), ("原来的草稿",), complete=True)
    now[0] = 10.5
    engine._publish(current, (part,), ("修订后的草稿",), complete=True)
    engine.check_health(11)
    assert len(received) == 1
    engine.check_health(12)
    assert received[-1].text == "修订后的草稿"
    assert received[-1].lag_seconds == 2
    assert engine.pending_caption is None
    engine.check_health(13)
    assert len(received) == 2


def test_held_rewrite_cannot_reappear_after_target_change_or_stop(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    monkeypatch.setattr("sonoweave.engine.time.monotonic", lambda: 10)
    part = SourceChunk(1, "Live speech", "en", 1, False, 0, 1)
    engine.source_cache.update(part)
    received = []
    engine.caption.connect(received.append)
    engine._publish(job(), (part,), ("旧的草稿",), True)
    engine._publish(job(), (part,), ("新修订的草稿",), True)
    assert engine.pending_caption is not None
    engine.set_target("ja")
    engine.check_health(15)
    assert len(received) == 1 and engine.pending_caption is None
    engine.clear_session()
    assert engine.pending_caption is None


def test_identical_progress_is_not_a_fake_subtitle_refresh_but_final_state_is_delivered(app):
    engine = CaptionEngine("zh-CN")
    part = SourceChunk(1, "A sentence.", "en", 1, False, 0, 1)
    engine.source_cache.update(part)
    current = job(segments=(part,))
    received = []
    engine.caption.connect(received.append)
    engine._publish(current, (part,), ("一句话。",), complete=False)
    engine._publish(current, (part,), ("一句话。",), complete=True)
    assert len(received) == 1
    engine._publish(replace(current, final=True), (part,), ("一句话。",), complete=True)
    assert len(received) == 2 and received[-1].final


def test_speech_retained_by_whisper_confidence_is_not_discarded_again(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    model = Mock()
    model.transcribe.return_value = (
        iter([SimpleNamespace(text="A clearly decoded sentence.", no_speech_prob=0.9, avg_logprob=-0.1)]),
        SimpleNamespace(language="en", language_probability=0.99),
    )
    monkeypatch.setattr("sonoweave.engine.load_recognizer", lambda: model)
    now = time.monotonic()
    engine.audio_queue.put(1, AudioWindow(1, 1, np.zeros(48000), False, now - 3, now))
    worker = threading.Thread(target=engine._recognize)
    worker.start()
    try:
        received = engine.text_queue.get(timeout=2)
        assert received.text == "A clearly decoded sentence."
    finally:
        engine.stop()
        worker.join(3)


def test_unexpected_job_exception_does_not_kill_translation_worker(app, monkeypatch):
    engine = CaptionEngine("zh-CN")
    translator = Mock(device="GPU")
    received, done = [], threading.Event()
    engine.last_job = job()
    engine.text_queue.put(1, engine.last_job)

    def translate(*args, **kwargs):
        if translator.translate.call_count == 1:
            engine.last_job = job(utterance_id=2)
            engine.text_queue.put(2, engine.last_job)
            raise RuntimeError("Unexpected backend failure")
        return "后续字幕。"

    translator.translate.side_effect = translate
    monkeypatch.setattr("sonoweave.engine.Translator", lambda: translator)

    def caption(update):
        received.append(update.text)
        done.set()

    engine.caption.connect(caption, Qt.ConnectionType.DirectConnection)
    worker = threading.Thread(target=engine._translate)
    worker.start()
    try:
        assert done.wait(3)
        assert received == ["后续字幕。"]
        assert worker.is_alive()
    finally:
        engine.stop()
        worker.join(3)


def test_stalled_generation_warns_once_but_idle_audio_does_not(app):
    engine = CaptionEngine("zh-CN")
    warnings = []
    engine.translation_status.connect(lambda text, error, revision: warnings.append((text, error, revision)))
    engine.check_health(now=100)
    assert not warnings
    engine.translation_active_since = 1
    engine.last_caption_at = 2
    engine.check_health(now=11)
    engine.check_health(now=12)
    assert len(warnings) == 1 and warnings[0][1] is True
    engine.translation_active_since = None
    engine.check_health(now=200)
    assert len(warnings) == 1


def test_reconnect_wait_is_bounded_and_cancellable(app):
    engine = CaptionEngine("zh-CN")
    stuck = Mock()
    stuck.is_alive.return_value = True
    stuck.join.side_effect = time.sleep
    engine.threads = [stuck]
    assert not engine.wait_stopped(timeout=0.01)
    assert not engine.wait_stopped(cancelled=lambda: True)


def test_dead_translation_thread_is_reported_even_without_an_active_job(app):
    engine = CaptionEngine("zh-CN")
    engine.threads = [SimpleNamespace(name="translation", ident=1, is_alive=lambda: False)]
    warnings = []
    engine.translation_status.connect(lambda text, error, revision: warnings.append(text))
    engine.check_health()
    assert len(warnings) == 1 and "线程已停止" in warnings[0]


def test_stuck_recognition_is_reported_even_if_translation_is_idle(app):
    engine = CaptionEngine("zh-CN")
    engine.recognition_active_since = 1
    warnings = []
    engine.translation_status.connect(lambda text, error, revision: warnings.append(text))
    engine.check_health(now=10)
    assert len(warnings) == 1 and "识别暂时无进展" in warnings[0]
