from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QSizeGrip, QVBoxLayout, QWidget

from .config import LANGUAGES, Settings


def caption_tail(text, font, width, max_lines=4):
    """Display a few most recent visual lines while keeping the text cache intact."""
    positions = []
    offset = 0
    for paragraph in text.split("\n"):
        layout = QTextLayout(paragraph, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(50, width))
            # Qt indexes UTF-16 code units; Python indexes Unicode code points.
            prefix = paragraph.encode("utf-16-le")[:line.textStart() * 2].decode("utf-16-le", errors="ignore")
            positions.append(offset + len(prefix))
        layout.endLayout()
        offset += len(paragraph) + 1
    if len(positions) <= max_lines:
        return text
    return "…" + text[positions[-max_lines]:].lstrip()


class CaptionWindow(QWidget):
    target_changed = Signal(str)
    retry_requested = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.has_caption = False
        self._caption_text = ""
        self._drag: QPoint | None = None
        self._base_status = ("正在准备语音模型…", False)
        self._translation_error = ""
        self.setWindowTitle("声屿 SonoWeave · 离线实时翻译字幕")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(440)
        self.setMaximumWidth(1500)
        self.setMinimumHeight(148)
        self.resize(860, 174)
        self.setMouseTracking(True)
        self.setStyleSheet("""
            QWidget { font-family: 'Segoe UI', 'Microsoft YaHei UI'; }
            QFrame#surface { background: rgba(19, 23, 30, 242); border: 1px solid rgba(255,255,255,28); border-radius: 20px; }
            QLabel { background: transparent; border: none; color: #F3F5FA; }
            QLabel#status { color: #9BA6B7; font-size: 11px; }
            QLabel#language { color: #C2CDDF; font-size: 11px; }
            QLabel#hint { color: #657084; font-size: 10px; }
            QSizeGrip { background: transparent; width: 12px; height: 12px; }
            QMenu { background: #1C222D; color: #E7EDF7; border: 1px solid #394252; border-radius: 10px; padding: 7px; }
            QMenu::item { padding: 7px 28px 7px 14px; border-radius: 5px; }
            QMenu::item:selected { background: #334457; }
            QMenu::separator { height: 1px; background: #343D4B; margin: 5px; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.surface = QFrame(objectName="surface")
        outer.addWidget(self.surface)
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(26, 16, 26, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #79DBC2; font-size: 10px;")
        self.status_label = QLabel(self._base_status[0], objectName="status")
        header.addWidget(self.dot)
        header.addSpacing(3)
        header.addWidget(self.status_label, 1)
        self.language_label = QLabel(LANGUAGES[settings.target], objectName="language")
        header.addWidget(self.language_label)
        layout.addLayout(header)
        self.caption_label = QLabel("让每一句，都听得懂。")
        self.caption_label.setFont(QFont("Microsoft YaHei UI", 23, QFont.Weight.Medium))
        self.caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption_label.setWordWrap(True)
        self.caption_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.caption_label, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        self.hint = QLabel("拖动移动  ·  右键切换语言", objectName="hint")
        footer.addWidget(self.hint)
        footer.addStretch()
        layout.addLayout(footer)
        self.grip = QSizeGrip(self)
        # Let mouse events on labels reach this widget, preserving drag/context menu.
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        screen = QApplication.primaryScreen()
        if screen:
            rect = screen.availableGeometry()
            self.resize(min(860, max(440, rect.width() - 48)), 174)
            self.move(rect.center().x() - self.width() // 2, rect.bottom() - self.height() - 60)

    def set_caption(self, text: str, final: bool = True) -> None:
        self.has_caption = True
        self._caption_text = text
        self._render_caption()
        self.hint.setText("离线 · 尾部已校准  ·  右键切换语言" if final else "离线 · 持续缓存与校准  ·  右键切换语言")
        self.adjust_height()

    def _render_caption(self):
        text = caption_tail(self._caption_text, self.caption_label.font(), max(100, self.surface.width() - 52))
        if text != self.caption_label.text():
            self.caption_label.setText(text)

    def adjust_height(self) -> None:
        available = max(100, self.surface.width() - 52)
        height = self.caption_label.heightForWidth(available)
        screen = self.screen().availableGeometry()
        required = min(max(174, height + 104), int(screen.height() * 0.65))
        bottom = self.geometry().bottom()
        self.resize(self.width(), required)
        self.move(self.x(), max(screen.top(), bottom - required + 1))

    def set_status(self, text: str, error: bool = False) -> None:
        self._base_status = (text, error)
        self._refresh_status()

    def set_translation_status(self, text: str, error: bool = False) -> None:
        self._translation_error = text if error else ""
        self._refresh_status()

    def _refresh_status(self) -> None:
        text, error = self._base_status
        if self._translation_error and not error:
            text, error = self._translation_error, True
        self.status_label.setText(text)
        self.dot.setStyleSheet(f"color: {'#F2B480' if error else '#79DBC2'}; font-size: 10px;")

    def choose_language(self, code: str) -> None:
        if code == self.settings.target:
            return
        try:
            self.settings.save_target(code)
        except OSError:
            self.set_status("无法保存语言设置，请检查目录权限", True)
            return
        self.language_label.setText(LANGUAGES[code])
        self._translation_error = ""
        if self.has_caption:
            self.has_caption = False
            self.caption_label.setText("正在切换翻译语言…")
        self.target_changed.emit(code)
        self._refresh_status()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        languages = menu.addMenu("翻译为")
        group = QActionGroup(menu)
        for code, name in LANGUAGES.items():
            action = languages.addAction(name)
            action.setCheckable(True)
            action.setChecked(code == self.settings.target)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, target=code: self.choose_language(target))
        if self._base_status[1]:
            menu.addSeparator()
            menu.addAction("重新连接", self.retry_requested.emit)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        menu.exec(event.globalPos())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if not handle or not handle.startSystemMove():
                self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.grip.move(self.width() - 26, self.height() - 26)
        if self.has_caption:
            self._render_caption()
            QTimer.singleShot(0, self._fit_after_resize)

    def _fit_after_resize(self) -> None:
        # Only increase height during manual width changes; avoid recursive resize events.
        required = self.caption_label.heightForWidth(max(100, self.surface.width() - 52)) + 104
        limit = int(self.screen().availableGeometry().height() * 0.65)
        if self.height() < min(required, limit):
            self.resize(self.width(), min(required, limit))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(0, 0, 0, 18), 8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(9, 10, -9, -7), 21, 21)

    def closeEvent(self, event) -> None:
        QApplication.instance().quit()
        event.accept()
