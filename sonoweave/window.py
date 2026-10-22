from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QPainter, QPen, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QScrollArea, QSizeGrip, QSizePolicy, QVBoxLayout, QWidget

from .config import DEFAULT_CAPTION_LINES, DEFAULT_FONT_SIZE, LANGUAGES, TRANSLATION_MODELS, Settings
from .streaming import CaptionRun, slice_runs
from .sentences import sentence_caption


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

    def fit_content(self):
        metrics = self.fontMetrics()
        lines = self.text().split("\n")
        width = max((metrics.horizontalAdvance(line) for line in lines), default=0) + 2
        height = metrics.lineSpacing() * len(lines) + 2
        self.setMinimumSize(width, height)
        return height

    def paintEvent(self, event):
        if not self.runs or "".join(run.text for run in self.runs) != self.text():
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor(SPEAKER_COLORS[0]))
        layouts, height, offset = [], 0.0, 0
        for paragraph in self.text().split("\n"):
            layout = QTextLayout(paragraph, self.font())
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.NoWrap)
            option.setAlignment(Qt.AlignmentFlag.AlignLeft)
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
        origin = QPointF(self.contentsRect().x(), self.contentsRect().y())
        for layout in layouts:
            layout.draw(painter, origin)
        painter.end()


def caption_tail(text, font, width, max_lines=DEFAULT_CAPTION_LINES):
    text, _ = sentence_caption(text)
    start = caption_tail_start(text, font, width, max_lines)
    return text[start:]


def caption_tail_start(text, font, width, max_lines=DEFAULT_CAPTION_LINES):
    """Rows end only at full stops, independent of sentence length and width."""
    positions = [0, *(match.end() for match in re.finditer(r"\n+", text) if match.end() < len(text))]
    return positions[-max(1, max_lines)] if len(positions) > max_lines else 0


def caption_line_starts(text, font, width):
    return [0, *(match.end() for match in re.finditer("\n", text))]


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
        self._user_resized = False
        self._resize_edges = Qt.Edge(0)
        self._resize_start = None
        self._resize_geometry = None
        self._base_status = ("正在准备语音模型…", False)
        self._translation_error = ""
        self.setWindowTitle("声屿 SonoWeave · 实时翻译字幕")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(440)
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
        self.caption_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.caption_label.setMargin(0)
        self.caption_label.setIndent(0)
        self.caption_label.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.MinimumExpanding)
        self.caption_label.setWordWrap(False)
        self.caption_label.setTextFormat(Qt.TextFormat.PlainText)
        self.caption_scroll = QScrollArea()
        self.caption_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.caption_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.caption_scroll.setWidgetResizable(True)
        self.caption_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.caption_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.caption_scroll.setWidget(self.caption_label)
        layout.addWidget(self.caption_scroll, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        self.hint = QLabel("拖动移动  ·  右键设置", objectName="hint")
        footer.addWidget(self.hint)
        footer.addStretch()
        layout.addLayout(footer)
        self.grip = QSizeGrip(self)
        self.grip.setToolTip("拖动边缘或四角调整大小")
        # Let mouse events on labels reach this widget, preserving drag/context menu.
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        for widget in (self, *self.findChildren(QWidget)):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
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
        self.hint.setText("尾部已校准  ·  右键设置" if final else "持续缓存与校准  ·  右键设置")
        self.adjust_height()

    def _render_caption(self):
        display_text, display_runs = sentence_caption(self._caption_text, self._caption_runs)
        start = caption_tail_start(display_text, self.caption_label.font(),
                                   max(100, self.caption_scroll.viewport().width()), self.settings.caption_lines)
        text = display_text[start:]
        if text != self.caption_label.text():
            self.caption_label.setText(text)
        self.caption_label.fit_content()
        runs = slice_runs(display_runs, start) if self.settings.speaker_colors else ()
        self.caption_label.set_runs(runs)

    def adjust_height(self) -> None:
        height = self.caption_label.fit_content()
        if self._user_resized:
            return  # Manual dimensions remain fixed; overflow uses the scroll area.
        if self.caption_label.minimumWidth() > self.caption_scroll.viewport().width():
            height += self.caption_scroll.horizontalScrollBar().sizeHint().height()
        screen = self.screen().availableGeometry()
        required = min(max(174, height + 104), screen.height() - 24)
        self.resize(self.width(), required)
        # Grow down from a fixed text origin; move only to stay on screen.
        if self.geometry().bottom() > screen.bottom():
            self.move(self.x(), max(screen.top(), screen.bottom() - required + 1))

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
        speakers = menu.addAction("人声配色（实验）")
        speakers.setCheckable(True)
        speakers.setChecked(self.settings.speaker_colors)
        speakers.triggered.connect(self.choose_speaker_colors)
        sizes = menu.addMenu("字体大小")
        size_group = QActionGroup(menu)
        for size in sorted({12, 16, 20, 23, 28, 32, 40, 48, self.settings.font_size}):
            action = sizes.addAction(f"{size} pt" + ("（默认）" if size == DEFAULT_FONT_SIZE else ""))
            action.setCheckable(True)
            action.setChecked(size == self.settings.font_size)
            size_group.addAction(action)
            action.triggered.connect(lambda checked=False, value=size: self.choose_font_size(value))
        sizes.addSeparator()
        sizes.addAction("自定义…", self.custom_font_size)
        lines = menu.addMenu("显示行数")
        line_group = QActionGroup(menu)
        for count in range(1, 11):
            action = lines.addAction(f"{count} 行" + ("（默认）" if count == DEFAULT_CAPTION_LINES else ""))
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

    def _edges_at(self, position):
        edges = Qt.Edge(0)
        if position.x() < 12:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self.width() - 12:
            edges |= Qt.Edge.RightEdge
        if position.y() < 12:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self.height() - 12:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _edge_cursor(edges):
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            return (Qt.CursorShape.SizeFDiagCursor if edges in (
                Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
                else Qt.CursorShape.SizeBDiagCursor)
        return Qt.CursorShape.SizeHorCursor if horizontal else Qt.CursorShape.SizeVerCursor

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            return super().eventFilter(watched, event)
        position = self.mapFromGlobal(event.globalPosition().toPoint())
        edges = self._edges_at(position)
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if watched is self.grip:
                self._user_resized = True
            if edges:
                self._user_resized = True
                self._drag = None
                handle = self.windowHandle()
                if not handle or not handle.startSystemResize(edges):
                    self._resize_edges = edges
                    self._resize_start = event.globalPosition().toPoint()
                    self._resize_geometry = self.geometry()
                    self.grabMouse()
                event.accept()
                return True
        elif kind == QEvent.Type.MouseMove:
            if self._resize_edges:
                self._resize_to(event.globalPosition().toPoint())
                return True
            if not event.buttons():
                if edges:
                    self.setCursor(self._edge_cursor(edges))
                else:
                    self.unsetCursor()
        elif kind == QEvent.Type.MouseButtonRelease and self._resize_edges:
            self._resize_to(event.globalPosition().toPoint())
            self._resize_edges = Qt.Edge(0)
            self._resize_start = self._resize_geometry = None
            self.releaseMouse()
            return True
        return super().eventFilter(watched, event)

    def _resize_to(self, position):
        delta = position - self._resize_start
        rect = self._resize_geometry.adjusted(0, 0, 0, 0)
        edges = self._resize_edges
        if edges & Qt.Edge.LeftEdge:
            rect.setLeft(min(rect.left() + delta.x(), rect.right() - self.minimumWidth() + 1))
        elif edges & Qt.Edge.RightEdge:
            rect.setRight(max(rect.right() + delta.x(), rect.left() + self.minimumWidth() - 1))
        if edges & Qt.Edge.TopEdge:
            rect.setTop(min(rect.top() + delta.y(), rect.bottom() - self.minimumHeight() + 1))
        elif edges & Qt.Edge.BottomEdge:
            rect.setBottom(max(rect.bottom() + delta.y(), rect.top() + self.minimumHeight() - 1))
        self.setGeometry(rect)

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

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(0, 0, 0, 18), 8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(9, 10, -9, -7), 21, 21)

    def closeEvent(self, event) -> None:
        self.clear_session()
        QApplication.instance().quit()
        event.accept()

    def clear_session(self):
        self.has_caption = False
        self._caption_text = ""
        self._caption_runs = ()
        self.caption_label.clear()
        self.caption_label.set_runs(())
