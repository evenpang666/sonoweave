from dataclasses import replace
import threading
import time
from unittest.mock import Mock

from sonoweave.engine import CaptionEngine
from sonoweave.streaming import ContextMemory, LatestQueue, TextJob
from sonoweave.translation import TranslationCancelled


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
    assert statuses[-1] == ("正在聆听系统声音 · 离线", False)
