import json
from sonoweave.config import Settings
from sonoweave.window import CaptionWindow, caption_tail
from PySide6.QtGui import QFont


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


def test_visible_tail_keeps_recent_lines_without_changing_the_source_text(app):
    text = "First\nSecond\nThird\nFourth\nFifth"
    assert caption_tail(text, QFont("Segoe UI", 14), 600, max_lines=2) == "…Fourth\nFifth"
    assert text == "First\nSecond\nThird\nFourth\nFifth"
