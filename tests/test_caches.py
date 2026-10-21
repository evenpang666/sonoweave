from dataclasses import replace

import pytest

from sonoweave.streaming import SourceChunk, TranscriptCache, TranslationCache, remove_overlap


def source(key, text, sequence=1, final=True, language="en", started=None):
    start = key * 3.0 if started is None else started
    return SourceChunk(key, text, language, sequence, final, start, start + 2)


def test_source_accumulates_across_pauses_but_replaces_only_current_draft():
    cache = TranscriptCache()
    cache.update(source(1, "The first sentence."))
    cache.update(source(2, "The next", final=False))
    cache.update(source(2, "The next sentence is complete.", sequence=2))
    assert [part.text for part in cache.snapshot()] == ["The first sentence.", "The next sentence is complete."]
    assert cache.version == 3
    assert not cache.update(source(2, "stale", sequence=1))


def test_recent_complete_source_is_a_window_not_just_the_latest_sentence():
    cache = TranscriptCache()
    for key in range(1, 6):
        cache.update(source(key, f"Sentence {key}."))
    assert [part.utterance_id for part in cache.recent()] == [3, 4, 5]
    assert cache.context_before(3) == ("Sentence 1.", "Sentence 2.")
    assert len(cache.snapshot()) == 5


def test_silence_or_language_change_limits_translation_context_without_erasing_source_cache():
    cache = TranscriptCache()
    cache.update(source(1, "Earlier English."))
    cache.update(source(2, "New topic.", started=40))
    assert [part.utterance_id for part in cache.recent()] == [2]
    assert cache.context_before(2) == ()
    cache.update(source(3, "日本語", language="ja", started=43))
    assert [part.utterance_id for part in cache.recent()] == [3]
    assert len(cache.snapshot()) == 3


def test_only_explicit_acoustic_overlap_is_deduplicated():
    cache = TranscriptCache()
    cache.update(source(1, "We need to keep listening"))
    cache.update(source(2, "keep listening to the speaker"), overlap_previous=True)
    assert cache.snapshot()[-1].text == "to the speaker"
    other = TranscriptCache()
    other.update(source(1, "Yes yes"))
    other.update(source(2, "yes I agree"))
    assert other.snapshot()[-1].text == "yes I agree"
    assert remove_overlap("我们持续识别", "识别新的内容") == "新的内容"


def test_translation_cache_appends_and_revises_tail_without_losing_prefix():
    cache = TranslationCache()
    segments = tuple(source(key, f"Source {key}") for key in (1, 2, 3))
    cache.update(segments, ("第一句。", "第二句。", "草稿"), {1, 2, 3})
    next_segments = (segments[1], replace(segments[2], text="corrected"), source(4, "new"))
    cache.update(next_segments, ("第二句已校准。", "修订后的第三句。", "新增第四句。"), {1, 2, 3, 4})
    assert cache.snapshot() == ((1, "第一句。"), (2, "第二句已校准。"), (3, "修订后的第三句。"), (4, "新增第四句。"))
    assert cache.tail() == "第一句。\n第二句已校准。\n修订后的第三句。\n新增第四句。"


def test_invalid_window_is_rejected_atomically():
    cache = TranslationCache()
    segments = (source(1, "one"), source(2, "two"))
    with pytest.raises(ValueError):
        cache.update(segments, ("only one",), {1, 2})
    assert cache.snapshot() == ()


def test_missing_old_translation_keeps_last_good_value_but_appends_new_speech():
    cache = TranslationCache()
    first, second = source(1, "old"), source(2, "new")
    cache.update((first,), ("已有译文。",), {1})
    cache.update((first, second), (None, "新增译文。"), {1, 2})
    assert cache.snapshot() == ((1, "已有译文。"), (2, "新增译文。"))


def test_buffers_are_bounded_and_visible_tail_does_not_discard_cached_history():
    sources = TranscriptCache(max_chars=30, max_chunks=5)
    translations = TranslationCache(max_chars=30, max_chunks=5)
    for key in range(1, 20):
        chunk = source(key, "1234567890")
        sources.update(chunk)
        translations.update((chunk,), ("abcdefghij",), {part.utterance_id for part in sources.snapshot()})
    assert sources.char_count <= 30 and len(sources.snapshot()) == 3
    assert translations.char_count <= 30 and len(translations.snapshot()) == 3
    assert len(translations.tail(10)) <= 11
    assert len(translations.snapshot()) == 3
