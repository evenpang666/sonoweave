from tools.caption_metrics import caption_metrics, distribution


def update(at, key, text, final=False):
    return dict(at=at, utterance_id=key, translation_snapshot=((key, text),),
                final=final, display_change=True)


def test_new_speech_and_prefix_growth_are_not_rewrites():
    rows = [update(1, 1, "这是第一句话的开头"), update(2, 1, "这是第一句话的开头和结尾。"),
            update(3, 2, "第二个人说了新内容。")]
    result = caption_metrics(rows)
    assert result["append_count"] == 1
    assert result["revision_count"] == result["large_rewrite_count"] == 0


def test_rewrite_size_and_actual_first_final_latencies():
    rows = [update(2, 1, "原来显示的是一个旧句子。"), update(3, 1, "后来整句变成了另外一句。", True)]
    result = caption_metrics(rows, [dict(utterance_id=1, final=True, started=.5, ended=2.5)])
    assert result["revision_count"] == result["large_rewrite_count"] == 1
    assert result["first_caption_from_speech_start_seconds"]["median"] == 1.5
    assert result["final_caption_from_final_audio_window_seconds"]["median"] == .5


def test_empty_metrics_do_not_claim_zero_latency():
    result = caption_metrics([])
    assert result["first_caption_from_speech_start_seconds"] == distribution([])
    assert result["display_update_gap_seconds"]["median"] is None


def test_current_sentence_rewrite_is_not_hidden_by_long_stable_history():
    prefix = "这是很长的历史内容" * 20 + "。"
    result = caption_metrics([update(1, 1, prefix + "这是正在显示的旧句子"),
                              update(2, 1, prefix + "整个末句已经变成其他内容")])
    assert result["large_rewrite_count"] == 1
    assert result["frozen_history_change_count"] == 0
