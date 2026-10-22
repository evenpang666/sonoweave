from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QPainter, QPen, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QSizeGrip, QVBoxLayout, QWidget

from .config import LANGUAGES, TRANSLATION_MODELS, Settings
from .streaming import CaptionRun, slice_runs


SPEAKER_COLORS = ("#F3F5FA", "#8FD3FF", "#FFD58A", "#B9E89A", "#D9B5FF", "#FFADBC", "#8EE3D8", "#E8DF9C")


def speaker_color(speaker):
    return SPEAKER_COLORS[speaker % len(SPEAKER_COLORS)] if speaker is not None else SPEAKER_COLORS[0]


class CaptionLabel(QLabel):
    """Plain text with per-speaker formats; no HTML interpretation or extra UI."""

    def __init__(self, text):
        super().__init__(text)
        self.runs = ()

    def set_runs(self, runs):
        self.runs = tuple(runs)
        self.update()

    def paintEvent(self, event):
        if not self.runs or "".join(run.text for run in self.runs) != self.text():
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor(SPEAKER_COLORS[0]))
        layouts, height, offset = [], 0.0, 0
        for paragraph in self.text().split("\n"):
            layout = QTextLayout(paragraph, self.font())
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.setTextOption(option)
            formats, position = [], 0
            for run in slice_runs(self.runs, offset, offset + len(paragraph)):
                span = QTextLayout.FormatRange()
                span.start = position
                span.length = len(run.text.encode("utf-16-le")) // 2
                span.format = QTextCharFormat()
                span.format.setForeground(QColor(speaker_color(run.speaker)))
                formats.append(span)
                position += span.length
            layout.setFormats(formats)
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(self.contentsRect().width())
                line.setPosition(QPointF(0, height))
                height += line.height()
            layout.endLayout()
            if not paragraph:
                height += self.fontMetrics().height()
            layouts.append(layout)
            offset += len(paragraph) + 1
        origin = QPointF(self.contentsRect().x(), self.contentsRect().y() + max(0, (self.contentsRect().height() - height) / 2))
        for layout in layouts:
            layout.draw(painter, origin)
        painter.end()


def caption_tail(text, font, width, max_lines=4):
    start = caption_tail_start(text, font, width, max_lines)
    return "…" + text[start:] if start else text


def caption_tail_start(text, font, width, max_lines=4):
    """Display a few most recent visual lines while keeping the text cache intact."""
    positions = caption_line_starts(text, font, width)
    if len(positions) <= max_lines:
        return 0
    start = positions[-max_lines]
    start += len(text[start:]) - len(text[start:].lstrip())
    # The added ellipsis also takes space and must not create an extra line.
    while start < len(text) and len(caption_line_starts("…" + text[start:], font, width)) > max_lines:
        start += 1
        start += len(text[start:]) - len(text[start:].lstrip())
    return start


def caption_line_starts(text, font, width):
    positions = []
    offset = 0
    for paragraph in text.split("\n"):
        if not paragraph:
            positions.append(offset)
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
    return positions


class CaptionWindow(QWidget):
    target_changed = Signal(str)
    retry_requested = Signal()
    model_changed = Signal(str)
    speaker_colors_changed = Signal(bool)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.has_caption = False
        self._caption_text = ""
        self._caption_runs = ()
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
            QWidget { font-family: 'Segoe UI', 'Microsoft YaHei UI', 'Noto Sans CJK SC', 'Noto Sans', sans-serif; }
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
        self.caption_label = CaptionLabel("让每一句，都听得懂。")
        font = QFont("Microsoft YaHei UI", settings.font_size, QFont.Weight.Medium)
        font.setFamilies(["Microsoft YaHei UI", "Noto Sans CJK SC", "Noto Sans", "DejaVu Sans"])
        self.caption_label.setFont(font)
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

    def set_caption(self, text: str, final: bool = True, runs=()) -> None:
        self.has_caption = True
        self._caption_text = text
        self._caption_runs = tuple(runs) if "".join(run.text for run in runs) == text else ()
        self._render_caption()
        self.hint.setText("离线 · 尾部已校准  ·  右键切换语言" if final else "离线 · 持续缓存与校准  ·  右键切换语言")
        self.adjust_height()

    def _render_caption(self):
        room = max(1, int((self.screen().availableGeometry().height() - 128) /
                          max(1, self.caption_label.fontMetrics().lineSpacing())))
        start = caption_tail_start(self._caption_text, self.caption_label.font(),
                                   max(100, self.surface.width() - 52), min(self.settings.caption_lines, room))
        text = ("…" if start else "") + self._caption_text[start:]
        if text != self.caption_label.text():
            self.caption_label.setText(text)
        runs = slice_runs(self._caption_runs, start) if self.settings.speaker_colors else ()
        self.caption_label.set_runs(((CaptionRun("…"),) if start and runs else ()) + runs)

    def adjust_height(self) -> None:
        available = max(100, self.surface.width() - 52)
        height = self.caption_label.heightForWidth(available)
        screen = self.screen().availableGeometry()
        required = min(max(174, height + 104), screen.height() - 24)
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

    def choose_model(self, model: str) -> None:
        if model == self.settings.model:
            return
        try:
            self.settings.save_model(model)
        except OSError:
            self.set_status("无法保存模型设置，请检查目录权限", True)
            return
        self.set_translation_status("")
        self.model_changed.emit(model)

    def choose_speaker_colors(self, enabled: bool) -> None:
        if enabled == self.settings.speaker_colors:
            return
        try:
            self.settings.save_speaker_colors(enabled)
        except OSError:
            self.set_status("无法保存人声配色设置，请检查目录权限", True)
            return
        if not enabled:
            self._caption_runs = ()
        if self.has_caption:
            self._render_caption()
        self.speaker_colors_changed.emit(enabled)

    def choose_font_size(self, size: int) -> None:
        try:
            self.settings.save_font_size(size)
        except OSError:
            self.set_status("无法保存字体设置，请检查目录权限", True)
            return
        font = self.caption_label.font()
        font.setPointSize(size)
        self.caption_label.setFont(font)
        self._refresh_layout()

    def choose_caption_lines(self, lines: int) -> None:
        try:
            self.settings.save_caption_lines(lines)
        except OSError:
            self.set_status("无法保存行数设置，请检查目录权限", True)
            return
        self._refresh_layout()

    def _refresh_layout(self):
        if self.has_caption:
            self._render_caption()
        self.adjust_height()

    def custom_font_size(self):
        size, accepted = QInputDialog.getInt(self, "字幕字体大小", "字号（pt）：", self.settings.font_size, 12, 48)
        if accepted:
            self.choose_font_size(size)

    def context_menu(self):
        menu = QMenu(self)
        languages = menu.addMenu("翻译为")
        group = QActionGroup(menu)
        for code, name in LANGUAGES.items():
            action = languages.addAction(name)
            action.setCheckable(True)
            action.setChecked(code == self.settings.target)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, target=code: self.choose_language(target))
        models = menu.addMenu("翻译模型")
        model_group = QActionGroup(menu)
        for key, spec in TRANSLATION_MODELS.items():
            action = models.addAction(spec["label"])
            action.setCheckable(True)
            action.setChecked(key == self.settings.model)
            model_group.addAction(action)
            action.triggered.connect(lambda checked=False, model=key: self.choose_model(model))
        menu.addSeparator()
        speakers = menu.addAction("人声配色")
        speakers.setCheckable(True)
        speakers.setChecked(self.settings.speaker_colors)
        speakers.triggered.connect(self.choose_speaker_colors)
        sizes = menu.addMenu("字体大小")
        size_group = QActionGroup(menu)
        for size in sorted({12, 16, 20, 23, 28, 32, 40, 48, self.settings.font_size}):
            action = sizes.addAction(f"{size} pt" + ("（默认）" if size == 23 else ""))
            action.setCheckable(True)
            action.setChecked(size == self.settings.font_size)
            size_group.addAction(action)
            action.triggered.connect(lambda checked=False, value=size: self.choose_font_size(value))
        sizes.addSeparator()
        sizes.addAction("自定义…", self.custom_font_size)
        lines = menu.addMenu("显示行数")
        line_group = QActionGroup(menu)
        for count in range(1, 11):
            action = lines.addAction(f"{count} 行" + ("（默认）" if count == 4 else ""))
            action.setCheckable(True)
            action.setChecked(count == self.settings.caption_lines)
            line_group.addAction(action)
            action.triggered.connect(lambda checked=False, value=count: self.choose_caption_lines(value))
        menu.addSeparator()
        menu.addAction("重新连接", self.retry_requested.emit)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        return menu

    def contextMenuEvent(self, event) -> None:
        self.context_menu().exec(event.globalPos())

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
        limit = self.screen().availableGeometry().height() - 24
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
