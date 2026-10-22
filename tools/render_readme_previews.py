"""Render the real subtitle widget and model menu for documentation; no audio capture."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QContextMenuEvent, QFontDatabase, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu
from sonoweave.config import Settings
from sonoweave.window import CaptionWindow

app = QApplication([])
if not QFontDatabase.families():
    for name in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name))
output = ROOT / "docs" / "assets"
output.mkdir(parents=True, exist_ok=True)
window = CaptionWindow(Settings(ROOT / ".cache" / "readme-preview" / "settings.json"))
window.settings.model = "4b"
window.show()
window.set_status("界面预览 · 示例字幕")
window.set_caption("世界的声音，就在眼前。\n让每一句话，都跨越语言的距离。")
app.processEvents()
window.grab().save(str(output / "subtitle-preview.png"))
error = []


def capture_menu():
    try:
        menu = next(child for child in window.findChildren(QMenu) if child.isVisible())
        action = next(action for action in menu.actions() if action.text() == "翻译模型")
        menu.setActiveAction(action)
        submenu = action.menu()
        submenu.ensurePolished()
        submenu.resize(submenu.sizeHint())
        # Draw the actual widgets side by side, independent of desktop geometry.
        submenu.popup(QPoint(20, 20))
        app.processEvents()
        padding, gap = 24, 12
        width = padding * 2 + menu.width() + gap + submenu.width()
        height = padding * 2 + max(menu.height(), submenu.height())
        canvas = QPixmap(width * 2, height * 2)
        canvas.setDevicePixelRatio(2)
        canvas.fill(QColor("#101820"))
        painter = QPainter(canvas)
        menu.render(painter, QPoint(padding, padding))
        submenu.render(painter, QPoint(padding + menu.width() + gap, padding))
        painter.end()
        assert canvas.save(str(output / "model-menu.png"))
        submenu.close()
        menu.close()
    except Exception as exc:
        error.append(exc)
        for menu in window.findChildren(QMenu):
            menu.close()


QTimer.singleShot(100, capture_menu)
event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(100, 40), QPoint(100, 40))
window.contextMenuEvent(event)
if error:
    raise error[0]
print("Rendered real subtitle widget and 4B/2B menu without starting audio or models.")
