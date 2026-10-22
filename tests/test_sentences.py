import pytest

from sonoweave.sentences import sentence_caption, sentence_speakers
from sonoweave.streaming import CaptionRun


@pytest.mark.parametrize("runs, speaker", [
    ((CaptionRun("主要说话人的一句", 1), CaptionRun("话。", 2)), 1),
    ((CaptionRun("主要说话人的一句", 1), CaptionRun("话。")), 1),
    ((CaptionRun("一半", 0), CaptionRun("一半。", 1)), None),
    ((CaptionRun("未知的内容占多数"), CaptionRun("短。", 1)), None),
])
def test_one_sentence_never_contains_multiple_voice_colors(runs, speaker):
    text = "".join(run.text for run in runs)
    assert sentence_speakers(text, runs) == (CaptionRun(text, speaker),)


def test_alternating_voices_change_color_only_at_sentence_end():
    runs = (CaptionRun("你准备好了吗", 0), CaptionRun("？", 1),
            CaptionRun("当然", 1), CaptionRun("！", 0), CaptionRun("那就出发。", 0))
    text = "".join(run.text for run in runs)
    assert sentence_caption(text, runs) == ("你准备好了吗？\n当然！\n那就出发。", (
        CaptionRun("你准备好了吗？", 0), CaptionRun("\n"),
        CaptionRun("当然！", 1), CaptionRun("\n"), CaptionRun("那就出发。", 0)))


def test_sentence_crossing_audio_windows_has_one_color_and_no_extra_line():
    runs = (CaptionRun("这是前一个音频窗口中的部分", 2), CaptionRun("\n"), CaptionRun("继续说。", 1))
    text, styled = sentence_caption("".join(run.text for run in runs), runs)
    assert text == "这是前一个音频窗口中的部分继续说。"
    assert styled == (CaptionRun(text, 2),)
