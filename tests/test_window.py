import json
from sonoweave.config import Settings
from sonoweave.window import CaptionWindow, caption_tail
from PySide6.QtGui import QFont
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
    text = "First\nSecond\nThird\nFourth\nFifth"
    assert caption_tail(text, QFont("Segoe UI", 14), 600, max_lines=2) == "…Fourth\nFifth"
    assert text == "First\nSecond\nThird\nFourth\nFifth"


def test_reconnect_is_available_even_without_a_component_error(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    actions = [action.text() for action in window.context_menu().actions()]
    assert "重新连接" in actions


def test_inline_colors_preserve_plain_text_and_recent_four_lines(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.choose_speaker_colors(True)
    window.show()
    runs = tuple(CaptionRun(text, i % 2) for i, text in enumerate((
        "First\n", "Second\n", "Third😀\n", "<b>Fourth</b>\n", "Fifth")))
    text = "".join(run.text for run in runs)
    window.set_caption(text, runs=runs)
    app.processEvents()
    assert window.caption_label.text() == "…Second\nThird😀\n<b>Fourth</b>\nFifth"
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
    assert (settings.speaker_colors, settings.font_size, settings.caption_lines) == (False, 23, 4)
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
    assert (loaded.speaker_colors, loaded.font_size, loaded.caption_lines) == (False, 23, 4)


def test_menu_changes_font_lines_and_color_without_losing_caption(app, tmp_path):
    window = CaptionWindow(Settings(tmp_path / "settings.json"))
    window.show()
    runs = tuple(CaptionRun(f"Line {i}\n", i % 2) for i in range(1, 9))
    text = "".join(run.text for run in runs).rstrip()
    runs = (*runs[:-1], CaptionRun(runs[-1].text.rstrip(), runs[-1].speaker))
    window.set_caption(text, runs=runs)
    assert not window.caption_label.runs
    assert window.caption_label.text().splitlines() == ["…Line 5", "Line 6", "Line 7", "Line 8"]
    menu = window.context_menu()
    actions = {action.text(): action for action in menu.actions()}
    line_menu = actions["显示行数"].menu()
    line_menu.actions()[5].trigger()
    assert window.caption_label.text().splitlines() == ["…Line 3", "Line 4", "Line 5", "Line 6", "Line 7", "Line 8"]
    size_menu = actions["字体大小"].menu()
    size_menu.actions()[1].trigger()
    assert window.caption_label.font().pointSize() == 16
    colors = []
    window.speaker_colors_changed.connect(colors.append)
    actions["人声配色"].trigger()
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


def test_line_limit_includes_ellipsis_after_wrapping(app):
    font = QFont("DejaVu Sans", 23)
    text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ " * 10
    for lines in (1, 2, 4, 10):
        tail = caption_tail(text, font, 180, max_lines=lines)
        assert tail.startswith("…")
        assert len(caption_line_starts(tail, font, 180)) <= lines


def test_failed_preference_save_does_not_change_window(app, tmp_path, monkeypatch):
    settings = Settings(tmp_path / "settings.json")
    window = CaptionWindow(settings)
    def fail(**changes):
        raise OSError("read only")
    monkeypatch.setattr(settings, "_save", fail)
    window.choose_font_size(32)
    window.choose_caption_lines(6)
    window.choose_speaker_colors(True)
    assert (settings.speaker_colors, settings.font_size, settings.caption_lines) == (False, 23, 4)
    assert window.caption_label.font().pointSize() == 23
