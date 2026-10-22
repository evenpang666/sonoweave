from dataclasses import replace

import numpy as np

from sonoweave.audio import AudioWindow
from sonoweave.recognition import AdaptiveDecoding, LanguageTracker


def test_audio_decoder_accepts_pyav_open_options(tmp_path):
    import wave
    from faster_whisper.audio import decode_audio
    path = tmp_path / "sample.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(np.zeros(1600, dtype="<i2").tobytes())
    assert decode_audio(str(path), sampling_rate=16000).shape == (1600,)


def window(key=1, seconds=3, started=0):
    return AudioWindow(key, 1, np.zeros(round(seconds * 16000), dtype=np.float32), False,
                       started, started + seconds)


def test_subsecond_wrong_guess_cannot_lock_the_next_fifteen_seconds():
    tracker = LanguageTracker()
    short = window(seconds=0.9)
    tracker.begin(short)
    tracker.observe("zh", 0.99, short)
    assert tracker.language is None
    longer = window(seconds=3)
    tracker.begin(longer)
    tracker.observe("en", 0.95, longer)
    assert tracker.language == "en"


def test_contiguous_windows_keep_the_language_until_enough_audio_is_available():
    tracker = LanguageTracker()
    first = window(seconds=15)
    tracker.begin(first)
    tracker.observe("en", 0.99, first)
    next_window = window(key=2, seconds=0.9, started=14.5)
    tracker.begin(next_window)
    assert tracker.language == "en" and not tracker.should_probe(next_window)
    tracker.observe("zh", 0.99, next_window)
    assert tracker.language == "en"
    assert tracker.should_probe(window(key=2, seconds=4, started=14.5))


def test_genuine_language_change_is_detected_and_weak_guesses_need_confirmation():
    tracker = LanguageTracker()
    first = window()
    tracker.begin(first)
    tracker.observe("en", 0.99, first)
    second = window(key=2, started=3)
    tracker.begin(second)
    tracker.observe("ja", 0.88, second)
    assert tracker.language == "en"
    tracker.observe("ja", 0.89, replace(second, audio=np.zeros(96000), ended=9))
    assert tracker.language == "ja"
    tracker.observe("de", 0.99, replace(second, audio=np.zeros(144000), ended=12))
    assert tracker.language == "de"


def test_long_silence_resets_language_without_forcing_the_old_one():
    tracker = LanguageTracker()
    first = window()
    tracker.begin(first)
    tracker.observe("en", 0.99, first)
    tracker.begin(window(key=2, seconds=0.9, started=40))
    assert tracker.language is None


def test_stuck_greedy_prefix_is_refined_before_the_end_of_the_audio_window():
    decoding = AdaptiveDecoding()
    first = window(seconds=0.9)
    assert decoding.beam(first) == 1
    decoding.observe("The first sentence.", first)
    second = window(seconds=1.5)
    assert decoding.beam(second) == 1
    decoding.observe("The first sentence...", second)
    assert decoding.beam(window(seconds=2.5)) == 3
    decoding.observe("The first sentence. New speech follows.", window(seconds=2.5))
    # Do not return to the greedy decoder and erase the recovered words.
    assert decoding.beam(window(seconds=3.1)) == 3
    assert decoding.beam(window(key=2, seconds=0.9, started=15)) == 1


def test_progressing_recognition_keeps_the_fast_decoder_until_final_correction():
    decoding = AdaptiveDecoding()
    for index in range(1, 8):
        current = window(seconds=index * 0.55)
        assert decoding.beam(current) == 1
        decoding.observe("word " * index, current)
    assert decoding.beam(replace(current, final=True)) == 3
