"""Describe published subtitle revisions without confusing new speech with edits."""
from collections import Counter
import math
import statistics

from sonoweave.sentences import sentence_ranges


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0, "median": None, "p95": None, "max": None}
    return {"count": len(values), "median": round(statistics.median(values), 3),
            "p95": round(values[max(0, math.ceil(len(values) * .95) - 1)], 3),
            "max": round(values[-1], 3)}


def caption_metrics(updates, audio_windows=()):
    previous, first_seen, edits = {}, {}, []
    history_changes = 0
    revisions = Counter()
    final_windows = {row["utterance_id"]: row for row in audio_windows if row["final"]}
    starts = {}
    for row in audio_windows:
        if "started" in row:
            starts.setdefault(row["utterance_id"], row["started"])
    final_delivery = {}
    for row in updates:
        for key, text in row.get("translation_snapshot", ()):
            first_seen.setdefault(key, row["at"])
            old = previous.get(key)
            if old is not None and old != text:
                prefix = 0
                for left, right in zip(old, text):
                    if left != right:
                        break
                    prefix += 1
                removed = len(old) - prefix
                tail_start = sentence_ranges(old)[-1][0]
                tail_size = len(old) - tail_start
                kind = "append" if not removed else "revision"
                large = kind == "revision" and tail_size >= 8 and removed / tail_size >= .5
                history_changes += prefix < tail_start
                revisions[key] += kind == "revision"
                edits.append(dict(at=row["at"], utterance_id=key, kind=kind,
                                  large_rewrite=large, common_prefix_chars=prefix,
                                  current_sentence_chars=tail_size,
                                  before=old, after=text))
            previous[key] = text
        key = row["utterance_id"]
        if row["final"] and key in final_windows:
            final_delivery.setdefault(key, max(0, row["at"] - final_windows[key]["ended"]))
    display = [row["at"] for row in updates if row.get("display_change", row.get("visible_change"))]
    return {
        "definition": "A revision replaces existing text within the same audio utterance. A large rewrite replaces at least half of the current sentence (>=8 characters). Completed preceding sentences are checked separately. Punctuation counts. This is not a semantic accuracy score.",
        "translated_utterances": len(previous),
        "append_count": sum(row["kind"] == "append" for row in edits),
        "revision_count": sum(row["kind"] == "revision" for row in edits),
        "large_rewrite_count": sum(row["large_rewrite"] for row in edits),
        "frozen_history_change_count": history_changes,
        "max_revisions_per_utterance": max(revisions.values(), default=0),
        "display_change_count": len(display),
        "display_update_gap_seconds": distribution([b - a for a, b in zip(display, display[1:])]),
        "first_caption_from_speech_start_seconds": distribution([
            value - starts[key] for key, value in first_seen.items() if key in starts]),
        "final_caption_from_final_audio_window_seconds": distribution(list(final_delivery.values())),
        "edits": edits,
    }
