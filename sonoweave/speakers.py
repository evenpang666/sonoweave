"""Local speaker turns and session identities, independent of ASR/translation."""
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
import threading

import numpy as np

from .audio import SAMPLE_RATE
from .config import speaker_model_location
from .models import SPEAKER_FILES, SEPARATION_FILE
from .streaming import CaptionRun


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: int | None
    overlap: bool = False


class SpeakerRegistry:
    """Keep bounded session prototypes; uncertain voices get no identity/color."""

    def __init__(self, match_threshold=0.55, new_threshold=0.5, max_speakers=8):
        self.prototypes = []
        self.match_threshold = match_threshold
        self.new_threshold = new_threshold
        self.max_speakers = max_speakers
        self.durations = []
        self.references = []
        self.lock = threading.RLock()

    def identify(self, embedding, seconds, *, excluded=(), anchor=None, learn=True):
        with self.lock:
            return self._identify(embedding, seconds, excluded=excluded, anchor=anchor, learn=learn)

    def _identify(self, embedding, seconds, *, excluded=(), anchor=None, learn=True):
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if seconds < 0.45 or not np.isfinite(vector).all() or norm < 1e-8:
            return None
        vector = vector / norm
        excluded = set(excluded)
        # An anchor refers to the very same audio samples analyzed earlier,
        # rather than assuming that adjacent turns belong to the same person.
        if anchor is not None and anchor not in excluded and 0 <= anchor < len(self.prototypes):
            if learn:
                self._remember(anchor, vector, seconds, anchored=True)
            return anchor
        if self.prototypes:
            scores = np.asarray([max(np.dot(vector, item) for item in references)
                                 if i not in excluded else -1.0
                                 for i, references in enumerate(self.references)])
            best = int(scores.argmax())
            if scores[best] >= self.match_threshold:
                if learn:
                    self._remember(best, vector, seconds)
                return best
            if scores[best] > self.new_threshold:
                return None
        if not learn or seconds < 0.65 or len(self.prototypes) >= self.max_speakers:
            return None
        self.prototypes.append(vector)
        self.durations.append(seconds)
        self.references.append([vector])
        return len(self.prototypes) - 1

    def _remember(self, identity, vector, seconds, anchored=False):
        score = max(np.dot(vector, item) for item in self.references[identity])
        if seconds > self.durations[identity] and (anchored or score >= 0.75):
            self.prototypes[identity] = vector
            self.durations[identity] = seconds
        if seconds >= 0.65 and score < 0.95 and (anchored or score >= self.match_threshold):
            self.references[identity].append(vector)
            # Retain the original sample and a few recent acoustic conditions.
            self.references[identity] = [self.references[identity][0], *self.references[identity][-3:]]


def align_speakers(words, turns):
    """Assign words by covered time; gaps and overlapping voices stay uncolored."""
    # VAD gaps inside one speaker's turn are not identity changes. Joining short
    # gaps avoids splitting a sentence into tiny independent translation jobs.
    regions = []
    for turn in sorted(turns, key=lambda item: (item.start, item.end)):
        if (regions and not turn.overlap and not regions[-1].overlap and turn.speaker is not None and regions[-1].speaker == turn.speaker
                and 0 <= turn.start - regions[-1].end <= 0.75):
            regions[-1] = SpeakerTurn(regions[-1].start, turn.end, turn.speaker)
        else:
            regions.append(turn)
    runs = []
    for word in words:
        duration = max(0.001, word.end - word.start)
        coverage = {}
        overlap = False
        active = []
        for turn in regions:
            start, end = max(word.start, turn.start), min(word.end, turn.end)
            if end <= start:
                continue
            overlap |= turn.overlap
            for other_start, other_end, other_speaker in active:
                if min(end, other_end) > max(start, other_start) and other_speaker != turn.speaker:
                    overlap = True
            active.append((start, end, turn.speaker))
            coverage[turn.speaker] = coverage.get(turn.speaker, 0) + end - start
        speaker = max(coverage, key=coverage.get) if coverage else None
        if overlap or coverage.get(speaker, 0) / duration < 0.6:
            speaker = None
        # Word times are approximate and punctuation can have zero duration.
        # A small edge tolerance is safe only when a single known voice is near.
        if speaker is None and not overlap:
            midpoint = (word.start + word.end) / 2
            nearby = {turn.speaker for turn in regions
                      if turn.start - 0.2 <= midpoint <= turn.end + 0.2}
            if len(nearby) == 1 and None not in nearby:
                speaker = nearby.pop()
        if runs and runs[-1].speaker == speaker:
            runs[-1] = CaptionRun(runs[-1].text + word.text, speaker)
        else:
            runs.append(CaptionRun(word.text, speaker))
    return tuple(runs)


class SpeakerTracker:
    """Decode local tracks before matching voices; never pool an entire cluster.

    Ten-second overlapping segmentation windows provide local tracks. Acoustic
    observations are limited to nearby speech turns and exclude overlap. Same-
    audio anchors and per-window cannot-link constraints stabilize identities.
    ASR utterance boundaries do not reset the bounded audio context.
    """

    def __init__(self, registry=None):
        import sherpa_onnx as sherpa
        import onnxruntime as ort

        segmentation, embedding = (speaker_model_location(spec.filename) for spec in SPEAKER_FILES)
        if not segmentation.is_file() or not embedding.is_file():
            raise RuntimeError("缺少人声模型，请重启以下载")
        embedding_config = sherpa.SpeakerEmbeddingExtractorConfig(
            model=str(embedding), num_threads=1, provider="cpu")
        if not embedding_config.validate():
            raise RuntimeError("人声模型配置无效")
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.segmentation = ort.InferenceSession(str(segmentation), sess_options=options,
                                                 providers=["CPUExecutionProvider"])
        meta = self.segmentation.get_modelmeta().custom_metadata_map
        if meta.get("model_type") != "pyannote-segmentation-3.0" or meta.get("num_classes") != "7":
            raise RuntimeError("不支持的人声分段模型")
        self.window_size = int(meta["window_size"])
        self.frame_shift = int(meta["receptive_field_shift"]) / SAMPLE_RATE
        self.frame_offset = int(meta["receptive_field_size"]) / (2 * SAMPLE_RATE)
        self.extractor = sherpa.SpeakerEmbeddingExtractor(embedding_config)
        self.registry = registry if registry is not None else SpeakerRegistry()
        self.history = OrderedDict()
        self.previous = {}

    def analyze(self, audio, previous=()):
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        if len(audio) < round(0.45 * SAMPLE_RATE):
            return ()
        last = max(0, len(audio) - self.window_size)
        offsets = list(range(0, last + 1, self.window_size // 2))
        if offsets[-1] != last:
            offsets.append(last)
        result, anchors = [], list(previous)
        for i, offset in enumerate(offsets):
            seconds = offset / SAMPLE_RATE
            turns = self._analyze_chunk(audio[offset:offset + self.window_size], seconds, anchors)
            left = 0 if i == 0 else (offsets[i - 1] + offset + self.window_size) / (2 * SAMPLE_RATE)
            right = len(audio) / SAMPLE_RATE if i + 1 == len(offsets) else (
                offset + offsets[i + 1] + self.window_size) / (2 * SAMPLE_RATE)
            result.extend(SpeakerTurn(max(left, t.start), min(right, t.end), t.speaker, t.overlap)
                          for t in turns if min(right, t.end) > max(left, t.start))
            anchors.extend(turns)
        return tuple(sorted(result, key=lambda turn: (turn.start, turn.end)))

    @staticmethod
    def _intervals(mask):
        edges = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(np.int8)))
        return list(zip(edges[::2], edges[1::2]))

    def _analyze_chunk(self, audio, offset, previous):
        duration = len(audio) / SAMPLE_RATE
        padded = np.pad(audio, (0, self.window_size - len(audio)))
        scores = self.segmentation.run(None, {"x": padded.reshape(1, 1, -1)})[0][0]
        mapping = np.asarray(((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1),
                              (1, 1, 0), (1, 0, 1), (0, 1, 1)), dtype=bool)
        activity = mapping[np.argmax(scores, axis=-1)]
        times = self.frame_offset + np.arange(len(activity)) * self.frame_shift
        activity = activity[times < duration]
        times = times[times < duration]
        if not len(activity):
            return ()
        overlap = activity.sum(axis=1) > 1
        observations = []
        for local in range(3):
            groups = []
            for first, end in self._intervals(activity[:, local]):
                if (end - first) * self.frame_shift < 0.18:
                    continue
                if groups and (first - groups[-1][-1][1]) * self.frame_shift <= 0.65:
                    groups[-1].append((first, end))
                else:
                    groups.append([(first, end)])
            for group in groups:
                intervals = []
                for first, end in group:
                    clean = activity[first:end, local] & ~overlap[first:end]
                    intervals.extend((a + first, b + first) for a, b in self._intervals(clean))
                pieces = [audio[round(times[a] * SAMPLE_RATE):round(min(duration, self.frame_offset + b * self.frame_shift) * SAMPLE_RATE)]
                          for a, b in intervals]
                samples = np.concatenate(pieces)[:6 * SAMPLE_RATE] if pieces else np.empty(0, dtype=np.float32)
                vector = None
                if samples.size >= round(0.45 * SAMPLE_RATE):
                    stream = self.extractor.create_stream()
                    stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=np.ascontiguousarray(samples))
                    stream.input_finished()
                    if self.extractor.is_ready(stream):
                        vector = self.extractor.compute(stream)
                bounds = [(offset + times[a], offset + min(duration, self.frame_offset + b * self.frame_shift))
                          for a, b in intervals]
                observations.append((times[group[0][0]], local, group, bounds, vector, samples.size / SAMPLE_RATE))
        identities, result = {}, []
        for _, local, group, bounds, vector, seconds in sorted(observations, key=lambda item: item[0]):
            excluded = {identity for other, labels in identities.items() if other != local for identity in labels}
            anchor = self._anchor(bounds, previous, excluded)
            identity = self.registry.identify(vector, seconds, excluded=excluded, anchor=anchor,
                learn=not bool(overlap.any())) if vector is not None else None
            if identity is not None:
                identities.setdefault(local, set()).add(identity)
            for first, end in group:
                # Overlap is published separately with no single-speaker claim.
                for a, b in self._intervals(activity[first:end, local] & ~overlap[first:end]):
                    result.append(SpeakerTurn(offset + times[first + a],
                        offset + min(duration, self.frame_offset + (first + b) * self.frame_shift), identity))
        for first, end in self._intervals(overlap):
            result.append(SpeakerTurn(offset + times[first],
                offset + min(duration, self.frame_offset + end * self.frame_shift), None, True))
        return tuple(result)

    @staticmethod
    def _anchor(intervals, previous, excluded):
        coverage = {}
        for turn in previous:
            if turn.speaker is None or turn.speaker in excluded or turn.overlap:
                continue
            for start, end in intervals:
                left, right = max(start, turn.start), min(end, turn.end)
                if right > left:
                    coverage.setdefault(turn.speaker, []).append((left, right))
        totals = {}
        for identity, ranges in coverage.items():
            merged = []
            for start, end in sorted(ranges):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            totals[identity] = sum(end - start for start, end in merged)
        if not totals:
            return None
        identity = max(totals, key=totals.get)
        return identity if totals[identity] >= 0.4 and totals[identity] / sum(totals.values()) >= 0.8 else None

    def analyze_window(self, window):
        """Retain at most 24 seconds across silence-delimited ASR windows."""
        current = self.history.get(window.utterance_id)
        if current and current.sequence >= window.sequence:
            return {}
        if self.history and window.started - next(reversed(self.history.values())).ended > 20:
            self.history.clear()
            self.previous.clear()
        self.history[window.utterance_id] = window
        while len(self.history) > 1 and (
                sum(item.audio.size for item in self.history.values()) > 24 * SAMPLE_RATE or
                window.started + window.audio.size / SAMPLE_RATE - next(iter(self.history.values())).started > 24 or
                len(self.history) > 32):
            key, _ = self.history.popitem(last=False)
            self.previous.pop(key, None)
        pieces, origins, prior, size, last_end = [], {}, [], 0, None
        for key, item in self.history.items():
            skip = 0
            if last_end is not None:
                gap = item.started - last_end
                if gap > 0:
                    padding = np.zeros(round(min(gap, 0.5) * SAMPLE_RATE), dtype=np.float32)
                    pieces.append(padding)
                    size += padding.size
                elif gap < 0:
                    skip = min(item.audio.size, round(-gap * SAMPLE_RATE))
            origin = (size - skip) / SAMPLE_RATE
            origins[key] = origin
            pieces.append(item.audio[skip:])
            size += item.audio.size - skip
            last_end = item.started + item.audio.size / SAMPLE_RATE
            prior.extend(SpeakerTurn(t.start + origin, t.end + origin, t.speaker, t.overlap)
                         for t in self.previous.get(key, ()))
        turns = self.analyze(np.concatenate(pieces), prior)
        results = {}
        for key, item in self.history.items():
            origin, duration = origins[key], item.audio.size / SAMPLE_RATE
            relative = tuple(SpeakerTurn(max(0, t.start - origin), min(duration, t.end - origin), t.speaker, t.overlap)
                             for t in turns if min(duration, t.end - origin) > max(0, t.start - origin))
            self.previous[key] = relative
            results[key] = (item.sequence, relative)
        return results

    def identify_audio(self, audio, *, excluded=()):
        samples = np.ascontiguousarray(audio[:6 * SAMPLE_RATE], dtype=np.float32)
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=samples)
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            return None
        return self.registry.identify(self.extractor.compute(stream), samples.size / SAMPLE_RATE, excluded=excluded)


class SpeechSeparator:
    """Experimental offline utility; the caption pipeline does not use it."""

    def __init__(self):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(speaker_model_location(SEPARATION_FILE.filename)),
            sess_options=options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def separate(self, audio):
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        outputs = self.session.run(None, {getattr(self, "input_name", "mix"): audio[None]})
        if len(outputs) == 2 and all(np.asarray(item).ndim == 1 for item in outputs):
            result = np.stack(outputs)[None]
        else:
            result = np.asarray(outputs[0], dtype=np.float32)
        if result.ndim != 3 or result.shape[:2] != (1, 2) or result.shape[-1] < len(audio):
            raise RuntimeError("双人语音分离输出无效")
        result = result[0, :, :len(audio)]
        if not np.isfinite(result).all():
            raise RuntimeError("双人语音分离输出含无效采样")
        # SI-SDR training is scale invariant; exported estimates can have an
        # arbitrary gain. Preserve relative channel energy while restoring the
        # mixture's power before VAD/Whisper (otherwise VAD saturates).
        power = float(np.mean(result.astype(np.float64) ** 2, axis=1).sum())
        mixture_power = float(np.mean(audio.astype(np.float64) ** 2))
        if power > 1e-20:
            result = result * np.float32(np.sqrt(mixture_power / power))
        return np.ascontiguousarray(np.clip(result, -1, 1))
