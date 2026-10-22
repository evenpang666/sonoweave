from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from queue import Empty
import re
import threading

from .sentences import sentence_ranges


class LatestQueue:
    """Keep only the newest snapshot per utterance, with bounded backlog."""

    def __init__(self, maxsize=3):
        self.maxsize = maxsize
        self.items = OrderedDict()
        self.condition = threading.Condition()

    def put(self, key, value):
        with self.condition:
            self.items[key] = value
            while len(self.items) > self.maxsize:
                self.items.popitem(last=False)
            self.condition.notify()

    def get(self, timeout=0.2):
        with self.condition:
            if not self.condition.wait_for(lambda: bool(self.items), timeout):
                raise Empty
            return self.items.popitem(last=False)[1]

    def clear(self):
        with self.condition:
            self.items.clear()


def bounded_context(values, max_chars=320):
    selected = []
    remaining = max_chars
    for text in reversed(tuple(values)[-2:]):
        if not text or remaining <= 0:
            continue
        selected.append(text[-remaining:])
        remaining -= len(selected[-1])
    return tuple(reversed(selected))


class ContextMemory:
    """A short source-only context; never persisted and never includes the draft."""

    def __init__(self, max_sentences=2, max_age=20.0, max_chars=320):
        self.sentences = deque(maxlen=max_sentences)
        self.max_age = max_age
        self.max_chars = max_chars

    def preceding(self, utterance_id, source, now):
        if self.sentences:
            last = self.sentences[-1]
            if source != last[1] or now - last[3] > self.max_age:
                self.sentences.clear()
        values = [item[2] for item in self.sentences if item[0] != utterance_id]
        remaining = self.max_chars
        selected = []
        for text in reversed(values):
            selected.append(text[-remaining:])
            remaining -= len(selected[-1])
            if remaining <= 0:
                break
        return tuple(reversed(selected))

    def commit(self, utterance_id, source, text, now):
        if self.sentences and self.sentences[-1][0] == utterance_id:
            self.sentences.pop()
        self.sentences.append((utterance_id, source, text, now))


@dataclass(frozen=True)
class CaptionRun:
    text: str
    speaker: int | None = None


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float


def slice_runs(runs, start=0, end=None):
    """Slice styled text using Python character offsets, retaining identities."""
    result, offset = [], 0
    for run in runs:
        stop = offset + len(run.text)
        text = run.text[max(0, start - offset):None if end is None else max(0, end - offset)]
        if text:
            result.append(CaptionRun(text, run.speaker))
        offset = stop
        if end is not None and offset >= end:
            break
    return tuple(result)


@dataclass(frozen=True)
class TextJob:
    text: str
    source: str
    target: str
    revision: int
    utterance_id: int
    sequence: int
    final: bool
    context: tuple[str, ...]
    started: float
    ended: float
    segments: tuple[SourceChunk, ...] = ()
    cache_version: int = 0


@dataclass(frozen=True)
class CaptionUpdate:
    text: str
    revision: int
    utterance_id: int
    sequence: int
    final: bool
    lag_seconds: float
    since_started_seconds: float
    source_chars: int = 0
    translation_chars: int = 0
    translated_segments: int = 0
    runs: tuple[CaptionRun, ...] = ()


@dataclass(frozen=True)
class SourceChunk:
    utterance_id: int
    text: str
    source: str
    sequence: int
    final: bool
    started: float
    ended: float
    runs: tuple[CaptionRun, ...] = ()
    words: tuple[TimedWord, ...] = ()
    separated: bool = False


def remove_overlap(previous, current):
    """Deduplicate ONLY audio explicitly carried across a forced window boundary."""
    pattern = r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|[\u3400-\u9fff]"
    left = list(re.finditer(pattern, previous))[-16:]
    right = list(re.finditer(pattern, current))[:16]
    for size in range(min(len(left), len(right)), 0, -1):
        if [part.group().casefold() for part in left[-size:]] == [part.group().casefold() for part in right[:size]]:
            return current[right[size - 1].end():].lstrip(" ,;:.!?，；：。！？")
    return current


class TranscriptCache:
    """Continuously accumulate source text, replacing only the current ASR draft."""

    def __init__(self, max_chars=8000, max_chunks=96):
        self.chunks = OrderedDict()
        self.max_chars = max_chars
        self.max_chunks = max_chunks
        self.version = 0

    @property
    def char_count(self):
        return sum(len(chunk.text) for chunk in self.chunks.values())

    def update(self, chunk, overlap_previous=False, *, allow_same_sequence=False):
        previous = self.chunks.get(chunk.utterance_id)
        if previous and (chunk.sequence < previous.sequence or
                         (chunk.sequence == previous.sequence and not allow_same_sequence)):
            return False
        if overlap_previous:
            preceding = [part for part in self.chunks.values() if part.utterance_id < chunk.utterance_id]
            if preceding:
                from dataclasses import replace
                text = remove_overlap(preceding[-1].text, chunk.text)
                chunk = replace(chunk, text=text, runs=slice_runs(chunk.runs, len(chunk.text) - len(text)))
        if not chunk.text:
            return False
        if previous and chunk.text == previous.text and chunk.final == previous.final and chunk.runs == previous.runs:
            # Track sequence to reject stale windows without scheduling identical work.
            self.chunks[chunk.utterance_id] = chunk
            return False
        self.chunks[chunk.utterance_id] = chunk
        self.version += 1
        while len(self.chunks) > self.max_chunks or (self.char_count > self.max_chars and len(self.chunks) > 1):
            self.chunks.popitem(last=False)
        return True

    def snapshot(self):
        return tuple(self.chunks.values())

    def relabel(self, utterance_id, runs):
        from dataclasses import replace
        chunk = self.chunks.get(utterance_id)
        if chunk is None or chunk.runs == runs or "".join(run.text for run in runs) != chunk.text:
            return False
        self.chunks[utterance_id] = replace(chunk, runs=runs)
        self.version += 1
        return True

    def recent(self, max_chars=640, max_chunks=3):
        selected = []
        count = 0
        for chunk in reversed(self.snapshot()):
            if selected and (chunk.source != selected[-1].source or selected[-1].started - chunk.ended > 20):
                break
            if selected and (count + len(chunk.text) > max_chars or len(selected) >= max_chunks):
                break
            selected.append(chunk)
            count += len(chunk.text)
        return tuple(reversed(selected))

    def context_before(self, first_id, max_chars=320):
        selected = []
        following = self.chunks.get(first_id)
        for chunk in reversed(self.snapshot()):
            if chunk.utterance_id >= first_id:
                continue
            if following and (chunk.source != following.source or following.started - chunk.ended > 20):
                break
            selected.append(chunk.text)
            following = chunk
            if len(selected) == 2:
                break
        return bounded_context(reversed(selected), max_chars)


class TranslationCache:
    """Freeze published history; only the last sentence can still be revised."""

    def __init__(self, max_chars=8000, max_chunks=96):
        self.chunks = OrderedDict()
        self.runs = {}
        self.max_chars = max_chars
        self.max_chunks = max_chunks
        self.latest_id = None

    @property
    def char_count(self):
        return sum(len(text) for text in self.chunks.values())

    def clear(self):
        self.chunks.clear()
        self.runs.clear()
        self.latest_id = None

    def update(self, segments, translations, retained_ids, runs=None):
        if len(segments) != len(translations) or any(text is not None and (not isinstance(text, str) or not text.strip()) for text in translations):
            raise ValueError("Translation/source alignment mismatch")
        if runs is not None and (len(runs) != len(segments) or any(
                text is not None and "".join(run.text for run in styled) != text.strip()
                for text, styled in zip(translations, runs))):
            raise ValueError("Styled translation/source alignment mismatch")
        published = [segment.utterance_id for segment, text in zip(segments, translations)
                     if text is not None and segment.utterance_id in retained_ids]
        next_id = max(published + ([self.latest_id] if self.latest_id is not None else [])) if published else self.latest_id
        for index, (segment, text) in enumerate(zip(segments, translations)):
            if text is not None and segment.utterance_id in retained_ids:
                key = segment.utterance_id
                text = text.strip()
                styled = runs[index] if runs is not None else (CaptionRun(text),)
                previous = self.chunks.get(key)
                if previous is not None:
                    if self.latest_id is not None and key < self.latest_id:
                        continue
                    old_parts, new_parts = sentence_ranges(previous), sentence_ranges(text)
                    frozen = max(0, len(old_parts) - 1)
                    if frozen:
                        if len(new_parts) <= frozen:
                            continue  # A shortened re-decode cannot erase history or the current draft.
                        old_end, new_start = old_parts[frozen][0], new_parts[frozen][0]
                        text = previous[:old_end] + text[new_start:]
                        styled = (*slice_runs(self.runs[key], 0, old_end), *slice_runs(styled, new_start))
                self.chunks[key] = text
                self.runs[key] = tuple(styled)
        self.latest_id = next_id
        self.chunks = OrderedDict(sorted((key, text) for key, text in self.chunks.items() if key in retained_ids))
        while len(self.chunks) > self.max_chunks or (self.char_count > self.max_chars and len(self.chunks) > 1):
            self.chunks.popitem(last=False)
        self.runs = {key: value for key, value in self.runs.items() if key in self.chunks}

    def snapshot(self):
        return tuple(self.chunks.items())

    def tail(self, max_chars=180):
        return "".join(run.text for run in self.tail_runs(max_chars))

    def tail_runs(self, max_chars=180):
        runs = []
        for key, text in self.chunks.items():
            if runs:
                runs.append(CaptionRun("\n"))
            runs.extend(self.runs.get(key, (CaptionRun(text),)))
        text = "".join(run.text for run in runs)
        if max_chars is None or len(text) <= max_chars:
            return tuple(runs)
        start = len(text) - max_chars
        start += len(text[start:]) - len(text[start:].lstrip())
        # A subtitle shows the recent tail; the full retained text stays in memory.
        return (CaptionRun("…"), *slice_runs(runs, start))
