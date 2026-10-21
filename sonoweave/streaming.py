from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from queue import Empty
import re
import threading


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


@dataclass(frozen=True)
class SourceChunk:
    utterance_id: int
    text: str
    source: str
    sequence: int
    final: bool
    started: float
    ended: float


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

    def update(self, chunk, overlap_previous=False):
        previous = self.chunks.get(chunk.utterance_id)
        if previous and chunk.sequence <= previous.sequence:
            return False
        if overlap_previous:
            preceding = [part for part in self.chunks.values() if part.utterance_id < chunk.utterance_id]
            if preceding:
                from dataclasses import replace
                chunk = replace(chunk, text=remove_overlap(preceding[-1].text, chunk.text))
        if not chunk.text:
            return False
        if previous and chunk.text == previous.text and chunk.final == previous.final:
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
    """Keep aligned translations, revising a recent tail without clearing history."""

    def __init__(self, max_chars=8000, max_chunks=96):
        self.chunks = OrderedDict()
        self.max_chars = max_chars
        self.max_chunks = max_chunks

    @property
    def char_count(self):
        return sum(len(text) for text in self.chunks.values())

    def clear(self):
        self.chunks.clear()

    def update(self, segments, translations, retained_ids):
        if len(segments) != len(translations) or any(text is not None and (not isinstance(text, str) or not text.strip()) for text in translations):
            raise ValueError("Translation/source alignment mismatch")
        for segment, text in zip(segments, translations):
            if text is not None and segment.utterance_id in retained_ids:
                self.chunks[segment.utterance_id] = text.strip()
        self.chunks = OrderedDict(sorted((key, text) for key, text in self.chunks.items() if key in retained_ids))
        while len(self.chunks) > self.max_chunks or (self.char_count > self.max_chars and len(self.chunks) > 1):
            self.chunks.popitem(last=False)

    def snapshot(self):
        return tuple(self.chunks.items())

    def tail(self, max_chars=180):
        text = "\n".join(self.chunks.values())
        if len(text) <= max_chars:
            return text
        text = text[-max_chars:]
        # A subtitle shows the recent tail; the full retained text stays in memory.
        return "…" + text.lstrip()
