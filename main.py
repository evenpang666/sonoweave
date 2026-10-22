from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading


def main() -> int:
    parser = argparse.ArgumentParser(description="声屿 SonoWeave · 离线实时翻译字幕")
    parser.add_argument("--preview", action="store_true", help="仅预览字幕界面，不采集声音")
    parser.add_argument("--screenshot", type=Path, help="预览后保存界面截图并退出")
    args = parser.parse_args()
    from PySide6.QtCore import QLockFile, QObject, QTimer, Signal, Slot
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    from sonoweave.config import Settings, data_dir
    from sonoweave.window import CaptionWindow

    app = QApplication(sys.argv[:1])
    # Qt's offscreen plugin may not enumerate Windows system fonts.
    if sys.platform == "win32" and not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc"):
            QFontDatabase.addApplicationFont(str(fonts / name))
    app.setApplicationName("SonoWeave")
    app.setOrganizationName("SonoWeave")
    app.setQuitOnLastWindowClosed(True)
    root = data_dir()
    os.environ.setdefault("HF_HOME", str(root / "huggingface"))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    handler = RotatingFileHandler(root / "app.log", maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Do not log HTTP query strings: they can contain recognized text.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    lock = QLockFile(str(root / "app.lock"))
    if not args.preview and not args.screenshot and not lock.tryLock(100):
        return 0
    settings = Settings()
    window = CaptionWindow(settings)
    class Controller(QObject):
        engine = None
        preparation_status = Signal(int, str)
        preparation_finished = Signal(int, str, str)

        def __init__(self):
            super().__init__()
            self.generation = 0
            self.cancel_download = threading.Event()
            self.closing = False
            self.preparation_lock = threading.Lock()
            self.engine_status = ("正在加载离线识别与翻译模型…", False)
            self.preparation_status.connect(self.receive_preparation_status)
            self.preparation_finished.connect(self.receive_preparation_finished)

        @Slot()
        def start(self):
            old = self.engine
            if self.engine:
                self.engine.stop()
                self.engine.caption.disconnect(self.receive_caption)
                self.engine.status.disconnect(self.receive_status)
                self.engine.translation_status.disconnect(self.receive_translation_status)
                window.target_changed.disconnect(self.engine.set_target)
                self.engine = None
            self.prepare(old)

        @Slot(str)
        def switch_model(self, model):
            self.prepare()

        def prepare(self, old=None):
            self.generation += 1
            generation = self.generation
            self.cancel_download.set()
            cancel = self.cancel_download = threading.Event()
            selected = settings.model
            window.set_translation_status("")
            window.set_status(f"正在准备 {selected.upper()} 离线模型…")

            def run():
                from sonoweave.models import DownloadCancelled, ensure_models
                try:
                    with self.preparation_lock:
                        if old:
                            for thread in old.threads:
                                thread.join()
                        path = ensure_models(selected,
                            progress=lambda text: self.preparation_status.emit(generation, text),
                            cancelled=cancel.is_set)
                    if not cancel.is_set():
                        self.preparation_finished.emit(generation, str(path), "")
                except DownloadCancelled:
                    pass
                except Exception as exc:
                    logging.getLogger("sonoweave").warning("Model preparation failed: %s", type(exc).__name__)
                    message = str(exc) if isinstance(exc, RuntimeError) else "模型准备失败，请检查网络和磁盘空间后右键重试"
                    self.preparation_finished.emit(generation, "", message)
            threading.Thread(target=run, name="model-preparation", daemon=True).start()

        @Slot(int, str)
        def receive_preparation_status(self, generation, text):
            if not self.closing and generation == self.generation:
                window.set_status(text)

        @Slot(int, str, str)
        def receive_preparation_finished(self, generation, path, error):
            if self.closing or generation != self.generation:
                return
            if error:
                window.set_status(error, True)
                return
            self.cancel_download.set()
            if self.engine:
                window.set_status(*self.engine_status)
                self.engine.set_model(path)
                return
            from sonoweave.engine import CaptionEngine
            self.engine = CaptionEngine(settings.target, model_path=path)
            self.engine.caption.connect(self.receive_caption)
            self.engine.status.connect(self.receive_status)
            self.engine.translation_status.connect(self.receive_translation_status)
            window.target_changed.connect(self.engine.set_target)
            window.set_translation_status("")
            self.engine.start()

        @Slot(object)
        def receive_caption(self, update):
            if self.sender() is self.engine and update.revision == self.engine.revision:
                window.set_caption(update.text, update.final)

        @Slot(str, bool)
        def receive_status(self, text, error):
            if self.sender() is self.engine:
                self.engine_status = (text, error)
                logging.getLogger("sonoweave").info("Status: %s", text)
                # Active ASR must not overwrite download progress or its error.
                if self.cancel_download.is_set():
                    window.set_status(text, error)

        @Slot(str, bool, int)
        def receive_translation_status(self, text, error, revision):
            if self.sender() is self.engine and revision == self.engine.revision and self.cancel_download.is_set():
                window.set_translation_status(text, error)

        @Slot()
        def stop(self):
            self.closing = True
            self.cancel_download.set()
            if self.engine:
                self.engine.stop()

    controller = Controller()
    window.retry_requested.connect(controller.start)
    window.model_changed.connect(controller.switch_model)
    window.show()
    if args.preview or args.screenshot:
        window.set_status("正在聆听系统声音 · 离线")
        window.set_caption("世界的声音，就在眼前。\n让每一句话，都跨越语言的距离。")
        if args.screenshot:
            def screenshot():
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
                app.quit()
            QTimer.singleShot(300, screenshot)
    else:
        QTimer.singleShot(100, controller.start)
    app.aboutToQuit.connect(controller.stop)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
