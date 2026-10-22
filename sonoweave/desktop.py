"""Configure Qt before importing its platform plugins."""
import os
import sys


def configure_qt_platform():
    # XWayland permits positioning and requests to stay above other windows.
    # Native Wayland leaves these to the compositor. Respect explicit choices,
    # including offscreen used by tests, and allow native Wayland if X is absent.
    if (sys.platform == "linux" and os.environ.get("WAYLAND_DISPLAY")
            and os.environ.get("DISPLAY") and not os.environ.get("QT_QPA_PLATFORM")):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
