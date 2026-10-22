"""One sentence-boundary rule for immutable history and subtitle layout."""
import re


def sentence_ends(text):
    for match in re.finditer(r'''([。．.!?！？]+)(["'”’」』）》】）)\]]*)''', text):
        punctuation = match.group(1)
        emphatic = any(mark in punctuation for mark in "!?！？")
        if not emphatic and len(punctuation) != 1:
            continue  # ..., 。。。 and similar pauses are not full stops.
        if not emphatic and punctuation != "。":
            if match.end() < len(text) and not text[match.end()].isspace() and not re.match(
                    r"[\u2e80-\u9fff]", text[match.end()]):
                continue  # Numbers, URLs and dotted initials.
            prefix = re.search(r"[A-Za-z.]+$", text[:match.start()])
            word = prefix.group().lower() if prefix else ""
            if word in {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc", "e.g", "i.e", "fig", "no", "st"} or re.fullmatch(r"(?:[a-z]\.)*[a-z]", word):
                continue
        yield match.end()


def sentence_ranges(text):
    ranges, start = [], 0
    for end in sentence_ends(text):
        if text[start:end].strip():
            ranges.append((start, end))
        start = end
    if text[start:].strip():
        ranges.append((start, len(text)))
    return tuple(ranges)


def sentence_speakers(text, runs):
    """Choose one identity per sentence; tied or mostly unknown speech stays neutral."""
    from .streaming import CaptionRun, slice_runs
    if not runs:
        return ()
    if "".join(run.text for run in runs) != text:
        return (CaptionRun(text),)
    styled = []
    for start, end in sentence_ranges(text):
        counts, total = {}, 0
        for run in slice_runs(runs, start, end):
            weight = sum(char.isalnum() for char in run.text)
            total += weight
            if run.speaker is not None:
                counts[run.speaker] = counts.get(run.speaker, 0) + weight
        speaker = max(counts, key=counts.get) if counts else None
        if speaker is not None and counts[speaker] * 2 <= total:
            speaker = None
        styled.append(CaptionRun(text[start:end], speaker))
    # Preserve trailing whitespace too, without treating it as a new sentence.
    end = sentence_ranges(text)[-1][1] if styled else 0
    if end < len(text):
        styled.append(CaptionRun(text[end:]))
    return tuple(styled)


def _replace(text, runs, edits):
    from .streaming import CaptionRun, slice_runs
    parts, styled, start = [], [], 0
    for left, right, replacement in edits:
        parts.extend((text[start:left], replacement))
        if runs:
            styled.extend(slice_runs(runs, start, left))
            if replacement:
                styled.append(CaptionRun(replacement))
        start = right
    parts.append(text[start:])
    if runs:
        styled.extend(slice_runs(runs, start))
    return "".join(parts), tuple(styled)


def sentence_caption(text, runs=()):
    # ASR windows, speaker turns and the model itself can contain newlines.
    # They never create rows until sentence-ending punctuation has been observed.
    edits = []
    for match in re.finditer(r"[ \t]*[\r\n]+[ \t]*", text):
        left, right = text[:match.start()], text[match.end():]
        separator = " " if left and right and (left[-1].isascii() or right[0].isascii()) else ""
        edits.append((match.start(), match.end(), separator))
    if edits:
        text, runs = _replace(text, runs, edits)
    # Normalize after joining audio windows: a sentence may span several chunks.
    runs = sentence_speakers(text, runs)
    edits = []
    for end in sentence_ends(text):
        following = end
        while following < len(text) and text[following] in " \t":
            following += 1
        if following < len(text):
            edits.append((end, following, "\n"))
    return _replace(text, runs, edits) if edits else (text, tuple(runs))
