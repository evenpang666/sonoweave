import json
from sonoweave.config import Settings
from sonoweave.window import CaptionWindow, caption_tail, sentence_caption
from PySide6.QtGui import QFont
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from sonoweave.streaming import CaptionRun
from sonoweave.window import speaker_color
from sonoweave.window import caption_line_starts
import pytest


def test_corrupt_settings_recover_and_language_persists(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("broken", encoding="utf-8")
    settings = Settings(path)
    assert settings.target == "zh-CN"
    settings.save_target("ja")
    assert Settings(path).target == "ja"
    path.write_text(json.dumps(["wrong structure"]), encoding="utf-8")
    assert Settings(path).target == "zh-CN"


def test_window_renders_plain_text_and_switches_language(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.show()
    window.set_caption("<b>字幕不会作为 HTML 执行</b>")
    app.processEvents()
    assert window.caption_label.text() == "<b>字幕不会作为 HTML 执行</b>"
    targets = []
    window.target_changed.connect(targets.append)
    window.choose_language("ja")
    assert targets == ["ja"]
    assert window.language_label.text() == "日本語"
    assert Settings(window.settings.path).target == "ja"
    window.hide()


def test_translation_failure_does_not_hide_capture_error(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.set_status("设备已断开", True)
    window.set_translation_status("翻译失败", True)
    assert window.status_label.text() == "设备已断开"
    window.set_status("正在聆听系统声音")
    assert window.status_label.text() == "翻译失败"
    window.set_translation_status("")
    assert window.status_label.text() == "正在聆听系统声音"


def test_model_choice_is_persisted_and_signalled_without_changing_target(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    models = []
    window.model_changed.connect(models.append)
    window.choose_model("2b")
    window.choose_model("2b")
    assert models == ["2b"]
    assert Settings(window.settings.path).model == "2b"
    assert window.settings.target == "zh-CN"


def test_visible_tail_keeps_recent_lines_without_changing_the_source_text(app):
    text = "First.\nSecond.\nThird.\nFourth.\nFifth."
    assert caption_tail(text, QFont("Segoe UI", 14), 600, max_lines=2) == "Fourth.\nFifth."
    assert text == "First.\nSecond.\nThird.\nFourth.\nFifth."


def test_reconnect_is_available_even_without_a_component_error(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    actions = [action.text() for action in window.context_menu().actions()]
    assert "重新连接" in actions


def test_inline_colors_preserve_plain_text_and_recent_four_lines(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.choose_caption_lines(4)
    window.choose_speaker_colors(True)
    window.show()
    runs = tuple(CaptionRun(text, i % 2) for i, text in enumerate((
        "First.\n", "Second.\n", "Third😀.\n", "<b>Fourth</b>.\n", "Fifth.")))
    text = "".join(run.text for run in runs)
    window.set_caption(text, runs=runs)
    app.processEvents()
    assert window.caption_label.text() == "Second.\nThird😀.\n<b>Fourth</b>.\nFifth."
    assert "".join(run.text for run in window.caption_label.runs) == window.caption_label.text()
    assert {run.speaker for run in window.caption_label.runs} == {None, 0, 1}
    before = window.caption_label.text()
    window.grab()  # exercise UTF-16 ranges and custom painting
    recolored = tuple(CaptionRun(run.text, 2) for run in runs)
    window.set_caption(text, runs=recolored)
    assert window.caption_label.text() == before
    assert window.caption_label.runs[-1].speaker == 2
    assert speaker_color(0) != speaker_color(1) != speaker_color(2)
    window.hide()


def test_settings_migrate_old_files_and_preserve_all_preferences(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"target": "ja", "model": "2b"}))
    settings = Settings(path)
    assert (settings.speaker_colors, settings.font_size, settings.caption_lines) == (False, 16, 2)
    settings.save_speaker_colors(True)
    settings.save_font_size(31)
    settings.save_caption_lines(6)
    settings.save_target("en")
    settings.save_model("4b")
    loaded = Settings(path)
    assert (loaded.target, loaded.model, loaded.speaker_colors, loaded.font_size, loaded.caption_lines) == (
        "en", "4b", True, 31, 6)
    for method, value in ((settings.save_font_size, 49), (settings.save_caption_lines, 0),
                          (settings.save_speaker_colors, "false"), (settings.save_font_size, True)):
        with pytest.raises(ValueError):
            method(value)
    path.write_text(json.dumps({"speaker_colors": "true", "font_size": -10, "caption_lines": True}))
    loaded = Settings(path)
    assert (loaded.speaker_colors, loaded.font_size, loaded.caption_lines) == (False, 16, 2)


def test_menu_changes_font_lines_and_color_without_losing_caption(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.show()
    runs = tuple(CaptionRun(f"Line {i}.\n", i % 2) for i in range(1, 9))
    text = "".join(run.text for run in runs).rstrip()
    runs = (*runs[:-1], CaptionRun(runs[-1].text.rstrip(), runs[-1].speaker))
    window.set_caption(text, runs=runs)
    assert not window.caption_label.runs
    assert window.caption_label.text().splitlines() == ["Line 7.", "Line 8."]
    menu = window.context_menu()
    actions = {action.text(): action for action in menu.actions()}
    line_menu = actions["显示行数"].menu()
    line_menu.actions()[5].trigger()
    assert window.caption_label.text().splitlines() == ["Line 3.", "Line 4.", "Line 5.", "Line 6.", "Line 7.", "Line 8."]
    size_menu = actions["字体大小"].menu()
    size_menu.actions()[1].trigger()
    assert window.caption_label.font().pointSize() == 16
    colors = []
    window.speaker_colors_changed.connect(colors.append)
    actions["人声配色（实验）"].trigger()
    assert colors == [True]
    assert window.caption_label.runs
    window.choose_speaker_colors(False)
    assert not window.caption_label.runs
    window.set_caption(text, runs=runs)  # an old colored update is masked while disabled
    assert not window.caption_label.runs
    reloaded = Settings(window.settings.path)
    assert (reloaded.speaker_colors, reloaded.font_size, reloaded.caption_lines) == (False, 16, 6)
    assert window._caption_text == text
    window.hide()


def test_long_sentence_is_never_cut_to_enforce_the_line_target(app):
    font = QFont("DejaVu Sans", 23)
    text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ " * 10
    for lines in (1, 2, 4, 10):
        tail = caption_tail(text, font, 180, max_lines=lines)
        assert tail == text
        assert not tail.startswith("…")
    assert len(caption_line_starts(text, font, 180)) == 1
    assert caption_tail("Earlier sentence. " + text, font, 180, max_lines=2) == "Earlier sentence.\n" + text


def test_failed_preference_save_does_not_change_window(app, tmp_path, monkeypatch):
    settings = Settings(tmp_path / "settings.json")
    window = CaptionWindow(settings)
    def fail(**changes):
        raise OSError("read only")
    monkeypatch.setattr(settings, "_save", fail)
    window.choose_font_size(32)
    window.choose_caption_lines(6)
    window.choose_speaker_colors(True)
    assert (settings.speaker_colors, settings.font_size, settings.caption_lines) == (False, 16, 2)
    assert window.caption_label.font().pointSize() == 16


@pytest.mark.parametrize("text, expected", [
    ("第一句话。第二句话！第三句话？", "第一句话。\n第二句话！\n第三句话？"),
    ('他说：“开始吧。”随后出发。', '他说：“开始吧。”\n随后出发。'),
    ("First sentence. Second sentence!", "First sentence.\nSecond sentence!"),
    ("Dr. Smith lives in the U.S. and pays 3.14 dollars. Next sentence.",
     "Dr. Smith lives in the U.S. and pays 3.14 dollars.\nNext sentence."),
    ("First... still thinking.\nNext sentence.", "First... still thinking.\nNext sentence."),
    ("还没说完……\n继续说下去", "还没说完……继续说下去"),
    ("Not finished...\nkeep going", "Not finished... keep going"),
    ("一段话\n换了音频窗口\n仍未结束", "一段话换了音频窗口仍未结束"),
    ("真的吗？\n当然！\n继续说。下一句", "真的吗？\n当然！\n继续说。\n下一句"),
    ('Really?!Yes!! “Ready?” Go!', 'Really?!\nYes!!\n“Ready?”\nGo!'),
    ("Wait... what? Still thinking… yes!Next.", "Wait... what?\nStill thinking… yes!\nNext."),
    ("还没说完，继续说下去", "还没说完，继续说下去"),
])
def test_sentences_start_new_lines_without_splitting_abbreviations_or_numbers(text, expected):
    assert sentence_caption(text)[0] == expected


def test_sentence_layout_preserves_speaker_colors_raw_text_and_two_line_limit(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.choose_speaker_colors(True)
    window.show()
    runs = (CaptionRun("第一句。😀第二", 0), CaptionRun("句。第三句。", 1))
    text = "".join(run.text for run in runs)
    formatted, styled = sentence_caption(text, runs)
    assert formatted == "第一句。\n😀第二句。\n第三句。"
    assert "".join(run.text for run in styled) == formatted
    assert [(run.text, run.speaker) for run in styled if run.text not in ("\n", "第一句。", "第三句。")] == [
        ("😀第二句。", 0)]
    window.set_caption(text, runs=runs)
    app.processEvents()
    assert window.caption_label.text() == "😀第二句。\n第三句。"
    assert "".join(run.text for run in window.caption_label.runs) == window.caption_label.text()
    assert window._caption_text == text
    assert window.caption_label.font().pointSize() == 16
    window.hide()


@pytest.mark.parametrize("colored", [False, True])
def test_text_grows_from_a_fixed_top_left_origin(app, tmp_path, colored):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.choose_speaker_colors(colored)
    window.show()
    window.set_caption("A new sentence", runs=(CaptionRun("A new sentence", 1),))
    app.processEvents()
    origin = window.caption_label.mapToGlobal(QPoint(0, 0))
    assert window.caption_label.alignment() == Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
    text = "A new sentence continues to grow to the right."
    window.set_caption(text, runs=(CaptionRun(text, 1),))
    app.processEvents()
    assert window.caption_label.mapToGlobal(QPoint(0, 0)) == origin
    window.grab()
    window.hide()


@pytest.mark.parametrize("colored", [False, True])
def test_long_sentence_only_extends_horizontally_without_reflow(app, tmp_path, colored):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.choose_speaker_colors(colored)
    window.show()
    initial_height = window.height()
    sentence = "这是一句很长但必须保留开头和结尾的完整字幕，" * 160 + "到这里结束。"
    text = "前一句已经结束。" + sentence
    window.set_caption(text, runs=(CaptionRun(text, 1),))
    for _ in range(3):
        app.processEvents()
    assert window.caption_label.text() == "前一句已经结束。\n" + sentence
    assert window.height() == initial_height
    assert not window.caption_label.wordWrap()
    assert window.caption_scroll.horizontalScrollBar().maximum() > 0
    assert window.caption_scroll.horizontalScrollBar().value() == 0
    assert window.caption_scroll.verticalScrollBar().maximum() == 0
    window.caption_scroll.horizontalScrollBar().setValue(window.caption_scroll.horizontalScrollBar().maximum())
    app.processEvents()
    assert window.caption_label.text().endswith("到这里结束。")
    window.grab()
    if colored:
        assert "".join(run.text for run in window.caption_label.runs) == "前一句已经结束。\n" + sentence
    window.hide()


@pytest.mark.parametrize("horizontal, vertical", [(-1, 0), (1, 0), (0, -1), (0, 1),
                                                        (-1, -1), (1, -1), (-1, 1), (1, 1)])
def test_all_edges_and_corners_resize_and_keep_manual_dimensions(app, tmp_path, monkeypatch, horizontal, vertical):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.setGeometry(100, 100, 650, 260)
    window.show()
    app.processEvents()
    monkeypatch.setattr(window.windowHandle(), "startSystemResize", lambda edges: False)
    surface = window.surface  # Edges must work even when a child receives the mouse.
    x = 1 if horizontal < 0 else surface.width() - 2 if horizontal > 0 else surface.width() // 2
    y = 1 if vertical < 0 else surface.height() - 2 if vertical > 0 else surface.height() // 2
    position = QPoint(x, y)
    end = surface.mapToGlobal(position) + QPoint(horizontal * 60, vertical * 40)
    original = window.geometry()
    QTest.mousePress(surface, Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseMove(window, window.mapFromGlobal(end))
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=window.mapFromGlobal(end))
    app.processEvents()
    assert window.width() == original.width() + (60 if horizontal else 0)
    assert window.height() == original.height() + (40 if vertical else 0)
    if horizontal < 0:
        assert window.geometry().right() == original.right()
    if vertical < 0:
        assert window.geometry().bottom() == original.bottom()
    resized = window.geometry()
    window.set_caption("新字幕仍然只向右延伸，" * 20 + "不会重设窗口。")
    window.choose_caption_lines(10)
    window.choose_font_size(48)
    window.set_caption("第一句。第二句！第三句？第四句。")
    app.processEvents()
    assert window.geometry() == resized
    assert not window.caption_label.wordWrap()
    assert window.caption_scroll.verticalScrollBar().maximum() > 0
    window.hide()


def test_resize_grip_marks_dimensions_as_manual(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.show()
    app.processEvents()
    QTest.mousePress(window.grip, Qt.MouseButton.LeftButton, pos=window.grip.rect().center())
    QTest.mouseRelease(window.grip, Qt.MouseButton.LeftButton, pos=window.grip.rect().center())
    assert window._user_resized
    window.hide()
