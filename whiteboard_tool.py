"""
Lightweight whiteboard overlay for Windows.
Press * and - together to open the whiteboard; * and / to draw on windows.
"""

import base64
import ctypes
import math
import os
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

import keyboard
from PyQt5.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QEvent,
    QTimer,
    pyqtSignal,
    QBuffer,
    QIODevice,
    QByteArray,
)
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRegion,
    QTextOption,
)
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtNetwork import QLocalServer, QLocalSocket

from app_paths import data_dir, is_frozen, project_dir, resource_dir
from license_gate import ensure_licensed
from collab import CollabBridge, CollabSession, load_session_config
from shortcuts import (
    CheatSheetDialog,
    combo_pressed,
    format_action,
    hold_down,
    hold_qt_key,
    is_hold_key,
    matches,
    matches_any,
    shape_from_holds,
    sync_hold_state,
)
from annotate_overlay import (
    AnnotateOverlay,
    OverlayBridge,
    COLOR_BLACK,
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_RED,
    ERASER_WIDTH_STEP,
    MIN_CIRCLE_RADIUS,
    PEN_WIDTH_STEP,
    _arrow_head,
    _circle_from_drag,
    _drag_rect,
    _item_hit,
    _triangle_from_rect,
    clamp_eraser_width,
    clamp_pen_width,
    make_eraser_cursor,
    make_pen_dot_cursor,
)

PEN_WIDTH = 4.0
ERASER_WIDTH = 24.0
ERASER_OBJECT = "object"
ERASER_PIXEL = "pixel"
MIN_STROKE_POINT_DIST2 = 0.64
HANDLE_SIZE = 12
MIN_OBJECT_SCALE = 0.15
MAX_OBJECT_SCALE = 4.0
HOTKEY_COOLDOWN = 0.6
STICKER_MAX_SIZE = 900
FIGURE_MAX_SIZE = 450


def _bundled_stickers_dir():
    return os.path.join(resource_dir(), "assets", "stickers")


def _copy_sticker_tree(src, dest):
    if not os.path.isdir(src):
        return
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(src):
        from_path = os.path.join(src, name)
        to_path = os.path.join(dest, name)
        if os.path.isdir(from_path):
            _copy_sticker_tree(from_path, to_path)
        elif os.path.isfile(from_path) and not os.path.isfile(to_path):
            shutil.copy2(from_path, to_path)


def stickers_dir():
    bundled = _bundled_stickers_dir()
    if not is_frozen():
        return bundled
    dest = os.path.join(data_dir(), "stickers")
    _copy_sticker_tree(bundled, dest)
    return dest if os.path.isdir(dest) else bundled


def funkcijos_dir():
    return os.path.join(stickers_dir(), "Funkcijos")


def _as_pointf(point):
    return QPointF(float(point.x()), float(point.y()))


def _as_point(point):
    if isinstance(point, QPoint) and not isinstance(point, QPointF):
        return point
    return QPoint(int(round(float(point.x()))), int(round(float(point.y()))))


def _stroke_painter_path(points):
    path = QPainterPath()
    if not points:
        return path
    pts = [_as_pointf(point) for point in points]
    if len(pts) == 1:
        path.moveTo(pts[0])
        path.lineTo(pts[0] + QPointF(0.15, 0.0))
        return path
    path.moveTo(pts[0])
    if len(pts) == 2:
        path.lineTo(pts[1])
        return path
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
        c1 = QPointF(
            p1.x() + (p2.x() - p0.x()) / 6.0,
            p1.y() + (p2.y() - p0.y()) / 6.0,
        )
        c2 = QPointF(
            p2.x() - (p3.x() - p1.x()) / 6.0,
            p2.y() - (p3.y() - p1.y()) / 6.0,
        )
        path.cubicTo(c1, c2, p2)
    return path


def _append_stroke_point(points, pos, force=False):
    pos = _as_pointf(pos)
    if points and not force:
        last = _as_pointf(points[-1])
        dx = pos.x() - last.x()
        dy = pos.y() - last.y()
        if dx * dx + dy * dy < MIN_STROKE_POINT_DIST2:
            return False
    points.append(pos)
    return True


TOOL_PEN = "pen"
TOOL_ERASER = "eraser"
TOOL_HAND = "hand"
TOOL_TEXT = "text"
TOOL_SELECT = "select"
MIN_SELECT_SIZE = 8

APP_INSTANCE_KEY = "EkranoRasiklisSingleton"
APP_MUTEX_NAME = "Local\\EkranoRasiklisLock"
APP_SHORTCUT_NAME = "Ekrano rašiklis.lnk"
CREATE_NO_WINDOW = 0x08000000
CSIDL_DESKTOPDIRECTORY = 0x0010
ERROR_ALREADY_EXISTS = 183
SYNCHRONIZE = 0x00100000

TEXT_FONT_FAMILY = "Arial"
TEXT_FONT_SIZE = 16

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SHELL_WINDOW_CLASSES = {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
    "Progman",
    "WorkerW",
    "ForegroundStaging",
    "TaskListThumbnailWnd",
    "TaskListOverlayWnd",
    "LauncherTipWnd",
    "TopLevelWindowForOverflowXamlIsland",
    "XamlExplorerHostIslandWindow",
    "DummyDWMListenerWindow",
}
SHELL_EXES = {
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "textinputhost.exe",
    "lockapp.exe",
    "dwm.exe",
}
EXPLORER_APP_CLASSES = {"CabinetWClass", "ExploreWClass"}
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
DWMWA_EXCLUDED_FROM_CAPTURE = 24
DISCORD_EXES = {
    "discord.exe",
    "discordcanary.exe",
    "discordptb.exe",
    "discorddevelopment.exe",
}

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_dwmapi = ctypes.windll.dwmapi
_user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
_user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_user32.IsWindowVisible.restype = ctypes.c_bool
_user32.IsIconic.argtypes = [ctypes.c_void_p]
_user32.IsIconic.restype = ctypes.c_bool
_user32.SetWindowPos.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
_user32.SetWindowPos.restype = ctypes.c_bool
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = ctypes.c_void_p
_user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_bool
_kernel32.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_wchar_p]
_kernel32.OpenMutexW.restype = ctypes.c_void_p
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.SetLastError.argtypes = [ctypes.c_ulong]
_kernel32.GetLastError.restype = ctypes.c_ulong
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.c_wchar_p,
    ctypes.POINTER(ctypes.c_ulong),
]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
_user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_user32.SetWindowDisplayAffinity.restype = ctypes.c_bool
_dwmapi.DwmSetWindowAttribute.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_uint,
]
_dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
if ctypes.sizeof(ctypes.c_void_p) == 8:
    _user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
else:
    _user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_background():
    executable = getattr(sys, "executable", "").lower()
    return executable.endswith("pythonw.exe")


def _set_window_topmost(hwnd, activate=True):
    flags = SWP_NOMOVE | SWP_NOSIZE
    if activate:
        flags |= SWP_SHOWWINDOW
    else:
        flags |= SWP_NOACTIVATE
    _user32.SetWindowPos(
        int(hwnd),
        HWND_TOPMOST,
        0,
        0,
        0,
        0,
        flags,
    )


def _set_excluded_from_capture(hwnd, excluded):
    hwnd = int(hwnd)
    affinity = WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE
    affinity_ok = False
    try:
        affinity_ok = bool(_user32.SetWindowDisplayAffinity(hwnd, affinity))
    except Exception:
        affinity_ok = False
    dwm_ok = False
    try:
        value = ctypes.c_int(1 if excluded else 0)
        status = _dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_EXCLUDED_FROM_CAPTURE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        dwm_ok = int(status) == 0
    except Exception:
        dwm_ok = False
    return affinity_ok or dwm_ok


def _window_class_name(hwnd):
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    try:
        _user32.GetClassNameW(int(hwnd), buf, 256)
    except Exception:
        return ""
    return buf.value


def _is_foreign_app_window(hwnd):
    if not hwnd:
        return False
    hwnd = int(hwnd)
    try:
        if not _user32.IsWindowVisible(hwnd):
            return False
    except Exception:
        return False
    pid = ctypes.c_ulong(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if int(pid.value) == os.getpid():
        return False
    class_name = _window_class_name(hwnd)
    if class_name in SHELL_WINDOW_CLASSES:
        return False
    exe = _window_exe_name(hwnd)
    if exe in SHELL_EXES:
        return False
    if exe == "explorer.exe" and class_name not in EXPLORER_APP_CLASSES:
        return False
    return True


def _is_discord_popout(hwnd):
    if not hwnd:
        return False
    hwnd = int(hwnd)
    if _window_exe_name(hwnd) not in DISCORD_EXES:
        return False
    try:
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            exstyle = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        else:
            exstyle = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        return bool(int(exstyle) & WS_EX_TOPMOST)
    except Exception:
        return False


def _window_exe_name(hwnd):
    pid = ctypes.c_ulong(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    process = _kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not process:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(buf))
        ok = _kernel32.QueryFullProcessImageNameW(
            process, 0, buf, ctypes.byref(size)
        )
        if not ok:
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        _kernel32.CloseHandle(process)


def _discord_popout_hwnds(exclude_hwnd=None):
    found = []
    exclude = int(exclude_hwnd) if exclude_hwnd else 0

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        try:
            if exclude and int(hwnd) == exclude:
                return 1
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return 1
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                exstyle = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            else:
                exstyle = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if not (int(exstyle) & WS_EX_TOPMOST):
                return 1
            if _window_exe_name(hwnd) in DISCORD_EXES:
                found.append(int(hwnd))
        except Exception:
            pass
        return 1

    _user32.EnumWindows(callback, 0)
    return found


def _raise_discord_popouts(exclude_hwnd=None):
    if sys.platform != "win32":
        return
    for hwnd in _discord_popout_hwnds(exclude_hwnd=exclude_hwnd):
        _set_window_topmost(hwnd, activate=False)


ICON_INK = QColor(70, 70, 72)
ICON_STROKE = 1.35
MAX_UNDO = 10
CANVAS_SCALE = 1.5
MIN_ERASE_STEP = 3.5
VECTOR_ERASE_TYPES = ("stroke", "text", "line", "arrow", "rect", "square", "circle", "triangle")


def _capsule_bounds(p1, p2, radius):
    r = float(radius)
    x1, y1 = float(p1.x()), float(p1.y())
    x2, y2 = float(p2.x()), float(p2.y())
    return QRectF(
        min(x1, x2) - r,
        min(y1, y2) - r,
        abs(x2 - x1) + 2.0 * r,
        abs(y2 - y1) + 2.0 * r,
    )


def _make_icon(draw_fn, size=28):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    draw_fn(painter, size)
    painter.end()
    return pixmap


def _icon_pen_outline(width=ICON_STROKE, color=ICON_INK):
    return QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def _draw_pencil_glyph(painter, body_color, eraser_color=QColor(236, 154, 162)):
    painter.setPen(_icon_pen_outline(1.15, QColor(110, 85, 55)))
    painter.setBrush(eraser_color)
    painter.drawRoundedRect(QRectF(-2.3, -11.4, 4.6, 2.55), 0.75, 0.75)
    painter.setBrush(QColor(198, 204, 214))
    painter.drawRect(QRectF(-2.3, -8.9, 4.6, 1.95))
    painter.setPen(QPen(QColor(150, 156, 168), 0.7))
    painter.drawLine(QPointF(-2.15, -8.2), QPointF(2.15, -8.2))
    painter.setPen(_icon_pen_outline(1.15, QColor(110, 85, 55)))
    painter.setBrush(body_color)
    body = QPainterPath()
    body.moveTo(-2.3, -6.95)
    body.lineTo(-2.3, 4.85)
    body.lineTo(2.3, 4.85)
    body.lineTo(2.3, -6.95)
    painter.drawPath(body)
    painter.setPen(QPen(QColor(110, 85, 55, 140), 0.8))
    painter.drawLine(QPointF(0.0, -6.7), QPointF(0.0, 4.7))
    painter.setPen(_icon_pen_outline(1.15, QColor(110, 85, 55)))
    painter.setBrush(QColor(239, 221, 170))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(-2.3, 4.85),
                QPointF(2.3, 4.85),
                QPointF(0.0, 11.15),
            ]
        )
    )
    painter.setBrush(QColor(70, 70, 72))
    painter.setPen(Qt.NoPen)
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(-0.85, 8.35),
                QPointF(0.85, 8.35),
                QPointF(0.0, 11.15),
            ]
        )
    )


def _icon_eraser(painter, size):
    painter.save()
    painter.translate(size / 2.0, size / 2.0)
    painter.rotate(-16)
    painter.scale(float(size) / 28.0, float(size) / 28.0)
    painter.translate(-14.0, -14.0)
    outline = _icon_pen_outline(1.2, QColor(150, 92, 108))
    body = QRectF(8.0, 7.2, 12.0, 16.2)
    body_path = QPainterPath()
    body_path.addRoundedRect(body, 2.6, 2.6)
    painter.setPen(Qt.NoPen)
    painter.setClipPath(body_path)
    painter.setBrush(QColor(255, 228, 118))
    painter.drawRect(body)
    painter.setBrush(QColor(255, 154, 92))
    painter.drawRect(QRectF(11.7, 7.2, 4.6, 16.2))
    painter.setClipping(False)
    painter.setPen(outline)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(body_path)
    painter.setBrush(QColor(236, 128, 168))
    painter.drawRoundedRect(QRectF(8.0, 3.9, 12.0, 6.8), 3.0, 3.0)
    painter.restore()


def _icon_hand(painter, size):
    painter.save()
    painter.scale(float(size) / 28.0, float(size) / 28.0)
    painter.setPen(_icon_pen_outline(1.15, QColor(186, 132, 96)))
    painter.setBrush(QColor(242, 201, 166))
    painter.drawRoundedRect(QRectF(8.6, 4.5, 2.8, 11.2), 1.4, 1.4)
    painter.drawRoundedRect(QRectF(12.0, 3.1, 2.9, 12.4), 1.45, 1.45)
    painter.drawRoundedRect(QRectF(15.5, 3.8, 2.8, 11.6), 1.4, 1.4)
    painter.drawRoundedRect(QRectF(18.8, 5.4, 2.55, 10.0), 1.3, 1.3)
    painter.drawRoundedRect(QRectF(8.5, 13.0, 12.4, 10.4), 3.6, 3.6)
    painter.save()
    painter.translate(8.4, 15.6)
    painter.rotate(-42)
    painter.drawRoundedRect(QRectF(-6.0, -1.45, 7.4, 2.9), 1.45, 1.45)
    painter.restore()
    painter.restore()


def _icon_functions(painter, size):
    painter.setPen(_icon_pen_outline(1.35, QColor(80, 80, 82)))
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(QPointF(5, size - 5), QPointF(size - 5, size - 5))
    painter.drawLine(QPointF(5, size - 5), QPointF(5, 5))
    painter.setPen(_icon_pen_outline(1.7, QColor(66, 133, 244)))
    path = QPainterPath()
    path.moveTo(5, size - 8)
    path.cubicTo(size / 3.0, size - 8, size / 3.0, size / 3.0, size - 5, size / 3.0)
    painter.drawPath(path)


def _icon_shapes(painter, size):
    painter.setPen(_icon_pen_outline(1.2, QColor(80, 80, 82)))
    painter.setBrush(QColor(144, 202, 249))
    painter.drawEllipse(QRectF(4.4, 9.0, 10.2, 10.2))
    painter.setBrush(QColor(255, 171, 145))
    painter.drawRoundedRect(QRectF(13.5, 5.3, 9.8, 9.8), 1.3, 1.3)
    painter.setBrush(QColor(165, 214, 167))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(10.0, 23.2),
                QPointF(19.3, 23.2),
                QPointF(14.65, 15.5),
            ]
        )
    )


def _icon_pen(painter, size):
    painter.save()
    painter.translate(size / 2.0, size / 2.0)
    painter.rotate(-45)
    painter.scale(float(size) / 28.0, float(size) / 28.0)
    _draw_pencil_glyph(painter, QColor(255, 213, 79))
    painter.restore()


def _icon_launcher(painter, size):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(31, 111, 235))
    radius = max(4.0, size * 0.22)
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), radius, radius)
    painter.save()
    painter.translate(size / 2.0, size / 2.0)
    painter.rotate(-45)
    painter.scale((float(size) / 28.0) * 0.72, (float(size) / 28.0) * 0.72)
    _draw_pencil_glyph(painter, QColor(255, 213, 79))
    painter.restore()


def _icon_text(painter, size):
    painter.setPen(QPen(QColor(55, 71, 79), ICON_STROKE))
    font = QFont(TEXT_FONT_FAMILY, max(11, int(size * 0.52)), QFont.DemiBold)
    painter.setFont(font)
    painter.drawText(QRect(0, 1, size, size - 1), Qt.AlignCenter, "T")


def _icon_select(painter, size):
    painter.setPen(QPen(QColor(66, 133, 244), 1.45, Qt.DashLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(QColor(66, 133, 244, 28))
    painter.drawRoundedRect(QRectF(5.2, 5.2, size - 10.4, size - 10.4), 2.0, 2.0)


def _icon_two_pens(painter, size):
    scale = (float(size) / 28.0) * 0.94
    painter.save()
    painter.translate(size * 0.36, size * 0.56)
    painter.rotate(-36)
    painter.scale(scale, scale)
    _draw_pencil_glyph(painter, QColor(255, 213, 79), QColor(236, 154, 162))
    painter.restore()
    painter.save()
    painter.translate(size * 0.66, size * 0.47)
    painter.rotate(-56)
    painter.scale(scale, scale)
    _draw_pencil_glyph(painter, QColor(100, 181, 246), QColor(239, 154, 154))
    painter.restore()


def _icon_close(painter, size):
    painter.setPen(QPen(QColor(90, 90, 92), 2.0, Qt.SolidLine, Qt.RoundCap))
    m = size * 0.28
    painter.drawLine(QPointF(m, m), QPointF(size - m, size - m))
    painter.drawLine(QPointF(size - m, m), QPointF(m, size - m))


def _icon_minimize(painter, size):
    painter.setPen(QPen(QColor(90, 90, 92), 2.0, Qt.SolidLine, Qt.RoundCap))
    y = size * 0.62
    painter.drawLine(QPointF(7, y), QPointF(size - 7, y))


def _icon_pause(painter, size):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(90, 90, 92))
    bar_w = max(3.5, size / 6.5)
    gap = max(3.0, size / 7.0)
    x1 = size / 2.0 - gap / 2.0 - bar_w
    x2 = size / 2.0 + gap / 2.0
    y = size / 5.0
    h = size - y * 2.0
    painter.drawRoundedRect(QRectF(x1, y, bar_w, h), 0.8, 0.8)
    painter.drawRoundedRect(QRectF(x2, y, bar_w, h), 0.8, 0.8)


def _icon_play(painter, size):
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(76, 175, 80))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(size * 0.30, size * 0.22),
                QPointF(size * 0.30, size * 0.78),
                QPointF(size * 0.78, size * 0.50),
            ]
        )
    )


def _icon_trash(painter, size):
    painter.save()
    painter.scale(float(size) / 28.0, float(size) / 28.0)
    painter.setPen(_icon_pen_outline(1.25, QColor(96, 96, 98)))
    painter.setBrush(QColor(232, 232, 234))
    painter.drawRoundedRect(QRectF(11.15, 4.55, 5.7, 2.85), 1.15, 1.15)
    painter.drawLine(QPointF(6.3, 8.15), QPointF(21.7, 8.15))
    body = QPainterPath()
    body.moveTo(8.05, 8.15)
    body.lineTo(9.25, 22.35)
    body.lineTo(18.75, 22.35)
    body.lineTo(19.95, 8.15)
    body.closeSubpath()
    painter.setBrush(QColor(224, 224, 226))
    painter.drawPath(body)
    painter.setPen(QPen(QColor(150, 150, 152), 1.15, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(QPointF(12.15, 11.05), QPointF(12.45, 19.55))
    painter.drawLine(QPointF(15.85, 11.05), QPointF(15.55, 19.55))
    painter.restore()


def _icon_notes(painter, size):
    painter.save()
    painter.scale(float(size) / 28.0, float(size) / 28.0)
    painter.setPen(_icon_pen_outline(1.2, QColor(201, 164, 74)))
    painter.setBrush(QColor(255, 241, 150))
    painter.drawRoundedRect(QRectF(5.4, 4.2, 17.2, 19.6), 2.2, 2.2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 249, 214))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(16.4, 4.2),
                QPointF(22.6, 4.2),
                QPointF(22.6, 10.4),
            ]
        )
    )
    painter.setPen(_icon_pen_outline(1.15, QColor(201, 164, 74)))
    painter.setBrush(QColor(255, 249, 214))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(16.4, 4.2),
                QPointF(22.6, 10.4),
                QPointF(16.4, 10.4),
            ]
        )
    )
    painter.setPen(QPen(QColor(196, 164, 90), 1.2, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(QPointF(8.4, 13.2), QPointF(19.6, 13.2))
    painter.drawLine(QPointF(8.4, 16.6), QPointF(19.6, 16.6))
    painter.drawLine(QPointF(8.4, 20.0), QPointF(16.8, 20.0))
    painter.restore()


class TooltipButton(QToolButton):
    def __init__(self, tip, parent=None):
        super().__init__(parent)
        self._tip = tip
        self.setToolTip(tip)

    def enterEvent(self, event):
        QToolTip.showText(self.mapToGlobal(QPoint(self.width() // 2, self.height())), self._tip, self)
        super().enterEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)


class HotkeyBridge(QObject):
    open_whiteboard = pyqtSignal()


class Toolbar(QFrame):
    tool_selected = pyqtSignal(str)
    eraser_mode_selected = pyqtSignal(str)
    sticker_requested = pyqtSignal()
    funkcijos_requested = pyqtSignal()
    collab_toggled = pyqtSignal(bool)
    capture_pause_toggled = pyqtSignal(bool)
    minimize_requested = pyqtSignal()
    close_requested = pyqtSignal()
    clear_all_requested = pyqtSignal()
    cheat_sheet_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.setFixedHeight(52)
        self.setStyleSheet(
            "QFrame { background: #f0f0f0; border-bottom: 1px solid #cccccc; }"
            "QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px; }"
            "QToolButton:hover { background: #e4e4e4; }"
            "QToolButton:checked { background: #d6e8ff; border: 1px solid #7eb6ff; }"
            "QToolButton#closeBoard:hover { background: #fee2e2; border: 1px solid #fca5a5; }"
        )
        self._eraser_mode = ERASER_PIXEL
        self._current_tool = TOOL_PEN

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        title = QLabel("Įrankiai:")
        layout.addWidget(title)

        self._buttons = {}
        for tool, label, icon_fn in (
            (TOOL_PEN, "Pieštukas", _icon_pen),
            (TOOL_ERASER, "Trintukas", _icon_eraser),
            (TOOL_HAND, "Rankytė", _icon_hand),
            (TOOL_TEXT, "Tekstas", _icon_text),
            (TOOL_SELECT, "Žymėti plotą", _icon_select),
        ):
            button = TooltipButton(label)
            button.setIcon(QIcon(_make_icon(icon_fn)))
            button.setIconSize(QSize(28, 28))
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedSize(40, 40)
            button.clicked.connect(lambda checked, t=tool: self._on_tool_clicked(t))
            self._buttons[tool] = button
            layout.addWidget(button)
        eraser_button = self._buttons[TOOL_ERASER]
        eraser_button.setContextMenuPolicy(Qt.CustomContextMenu)
        eraser_button.customContextMenuRequested.connect(lambda *_: self._choose_eraser())
        self._update_eraser_tip()

        sticker_button = TooltipButton("Figūros")
        sticker_button.setIcon(QIcon(_make_icon(_icon_shapes)))
        sticker_button.setIconSize(QSize(28, 28))
        sticker_button.setFocusPolicy(Qt.NoFocus)
        sticker_button.setFixedSize(40, 40)
        sticker_button.clicked.connect(self.sticker_requested.emit)
        layout.addWidget(sticker_button)

        funkcijos_button = TooltipButton("Funkcijos")
        funkcijos_button.setIcon(QIcon(_make_icon(_icon_functions)))
        funkcijos_button.setIconSize(QSize(28, 28))
        funkcijos_button.setFocusPolicy(Qt.NoFocus)
        funkcijos_button.setFixedSize(40, 40)
        funkcijos_button.clicked.connect(self.funkcijos_requested.emit)
        layout.addWidget(funkcijos_button)

        clear_button = TooltipButton("Išvalyti viską")
        clear_button.setIcon(QIcon(_make_icon(_icon_trash)))
        clear_button.setIconSize(QSize(28, 28))
        clear_button.setFocusPolicy(Qt.NoFocus)
        clear_button.setFixedSize(40, 40)
        clear_button.clicked.connect(self.clear_all_requested.emit)
        layout.addWidget(clear_button)

        notes_button = TooltipButton("Atmintinė")
        notes_button.setIcon(QIcon(_make_icon(_icon_notes)))
        notes_button.setIconSize(QSize(28, 28))
        notes_button.setFocusPolicy(Qt.NoFocus)
        notes_button.setFixedSize(40, 40)
        notes_button.clicked.connect(self.cheat_sheet_requested.emit)
        layout.addWidget(notes_button)

        layout.addStretch()

        self.collab_button = TooltipButton("Dvieji pieštukai")
        self.collab_button.setIcon(QIcon(_make_icon(_icon_two_pens)))
        self.collab_button.setIconSize(QSize(28, 28))
        self.collab_button.setCheckable(True)
        self.collab_button.setFocusPolicy(Qt.NoFocus)
        self.collab_button.setFixedSize(40, 40)
        self.collab_button.clicked.connect(lambda checked: self.collab_toggled.emit(checked))
        layout.addWidget(self.collab_button)

        self.pause_button = TooltipButton(
            "Pristabdyti vaizdą (Discord / Teams nematys naujų užrašų)"
        )
        self.pause_button.setIcon(QIcon(_make_icon(_icon_pause)))
        self.pause_button.setIconSize(QSize(28, 28))
        self.pause_button.setCheckable(True)
        self.pause_button.setFocusPolicy(Qt.NoFocus)
        self.pause_button.setFixedSize(40, 40)
        self.pause_button.setStyleSheet(
            "QToolButton:checked { background: #ffcdd2; border: 1px solid #e57373; }"
        )
        self.pause_button.clicked.connect(self._on_pause_clicked)
        layout.addWidget(self.pause_button)

        minimize_button = TooltipButton("Sumažinti")
        minimize_button.setIcon(QIcon(_make_icon(_icon_minimize)))
        minimize_button.setIconSize(QSize(28, 28))
        minimize_button.setFocusPolicy(Qt.NoFocus)
        minimize_button.setFixedSize(40, 40)
        minimize_button.clicked.connect(self.minimize_requested.emit)
        layout.addWidget(minimize_button)

        close_button = TooltipButton("Uždaryti lentą")
        close_button.setObjectName("closeBoard")
        close_button.setIcon(QIcon(_make_icon(_icon_close)))
        close_button.setIconSize(QSize(28, 28))
        close_button.setFocusPolicy(Qt.NoFocus)
        close_button.setFixedSize(40, 40)
        close_button.clicked.connect(self.close_requested.emit)
        layout.addWidget(close_button)

        self.set_tool(TOOL_PEN)
        self.set_capture_paused(False)

    def _on_tool_clicked(self, tool):
        if tool == TOOL_ERASER:
            self._choose_eraser()
            return
        self._current_tool = tool
        self.tool_selected.emit(tool)

    def _update_eraser_tip(self):
        button = self._buttons.get(TOOL_ERASER)
        if button is None:
            return
        if self._eraser_mode == ERASER_PIXEL:
            tip = "Trintukas: pixeliais"
        else:
            tip = "Trintukas: visas objektas"
        button._tip = tip
        button.setToolTip(tip)

    def _choose_eraser(self):
        menu = QMenu(self)
        pixel_action = menu.addAction("Trinti pixeliais")
        object_action = menu.addAction("Ištrinti visą objektą")
        pixel_action.setCheckable(True)
        object_action.setCheckable(True)
        pixel_action.setChecked(self._eraser_mode == ERASER_PIXEL)
        object_action.setChecked(self._eraser_mode == ERASER_OBJECT)
        button = self._buttons[TOOL_ERASER]
        chosen = menu.exec_(button.mapToGlobal(QPoint(0, button.height())))
        if chosen is object_action:
            self._eraser_mode = ERASER_OBJECT
        elif chosen is pixel_action:
            self._eraser_mode = ERASER_PIXEL
        self._current_tool = TOOL_ERASER
        self._update_eraser_tip()
        self.eraser_mode_selected.emit(self._eraser_mode)
        self.tool_selected.emit(TOOL_ERASER)

    def _on_pause_clicked(self, checked):
        self.set_capture_paused(checked)
        self.capture_pause_toggled.emit(checked)

    def set_capture_paused(self, paused):
        self.pause_button.blockSignals(True)
        self.pause_button.setChecked(paused)
        self.pause_button.blockSignals(False)
        if paused:
            self.pause_button.setIcon(QIcon(_make_icon(_icon_play)))
            tip = "Rodyti vaizdą stebėtojams"
        else:
            self.pause_button.setIcon(QIcon(_make_icon(_icon_pause)))
            tip = "Pristabdyti vaizdą (Discord / Teams nematys naujų užrašų)"
        self.pause_button._tip = tip
        self.pause_button.setToolTip(tip)

    def set_tool(self, tool):
        self._current_tool = tool
        for name, button in self._buttons.items():
            button.setChecked(name == tool)


class CollabBar(QFrame):
    allow_toggled = pyqtSignal(bool)
    open_local_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setStyleSheet(
            "QFrame { background: #e8f1ff; border-bottom: 1px solid #b7d2f5; }"
            "QLineEdit { background: white; border: 1px solid #9abbe8; border-radius: 4px; padding: 3px 6px; }"
            "QPushButton { background: white; border: 1px solid #9abbe8; border-radius: 4px; padding: 4px 10px; }"
            "QPushButton:hover { background: #d6e8ff; }"
            "QPushButton:checked { background: #2e7d32; color: white; border: 1px solid #1b5e20; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Nuoroda:"))
        self.link_edit = QLineEdit()
        self.link_edit.setReadOnly(True)
        self.link_edit.setPlaceholderText("Ruošiama...")
        layout.addWidget(self.link_edit, 1)

        self.copy_button = QPushButton("Kopijuoti")
        self.copy_button.setFocusPolicy(Qt.NoFocus)
        self.copy_button.clicked.connect(self._copy)
        layout.addWidget(self.copy_button)

        self.open_button = QPushButton("Atidaryti")
        self.open_button.setFocusPolicy(Qt.NoFocus)
        self.open_button.clicked.connect(self.open_local_requested.emit)
        layout.addWidget(self.open_button)

        self.allow_button = QPushButton("Leisti rašyti")
        self.allow_button.setCheckable(True)
        self.allow_button.setFocusPolicy(Qt.NoFocus)
        self.allow_button.clicked.connect(lambda checked: self.allow_toggled.emit(checked))
        layout.addWidget(self.allow_button)

        self.meta = QLabel("Svečių: 0")
        self.meta.setMaximumWidth(460)
        layout.addWidget(self.meta)
        self.link_edit.setMinimumWidth(220)
        self._lan_url = ""
        self._public_url = ""
        self._guest_count = 0
        self._status = ""

    def set_urls(self, lan_url, public_url):
        self._lan_url = lan_url or ""
        self._public_url = public_url or ""
        self.link_edit.setText(self.current_url())

    def current_url(self):
        return self._public_url or self._lan_url

    def set_guest_count(self, count):
        self._guest_count = count
        self._refresh_meta()

    def set_status(self, status):
        self._status = status or ""
        self._refresh_meta()

    def reset(self):
        self._lan_url = ""
        self._public_url = ""
        self._guest_count = 0
        self._status = ""
        self.link_edit.clear()
        self.allow_button.setChecked(False)
        self._refresh_meta()

    def _refresh_meta(self):
        text = "Svečių: %s" % self._guest_count
        if self._status:
            text += "  •  " + self._status
        self.meta.setText(text)
        self.meta.setToolTip(self._status)

    def _copy(self):
        url = self.current_url()
        if url:
            QApplication.clipboard().setText(url)


class CanvasWidget(QWidget):
    local_stroke_start = pyqtSignal(str, str, float, float)
    local_stroke_point = pyqtSignal(str, float, float)
    local_stroke_end = pyqtSignal(str)
    view_changed = pyqtSignal()
    content_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.tool = TOOL_PEN
        self.pen_color = QColor(COLOR_BLACK)
        self.pen_width = PEN_WIDTH
        self.eraser_width = ERASER_WIDTH
        self.eraser_mode = ERASER_PIXEL
        self.items = []
        self._undo_stack = []
        self._erase_snapshot = None
        self._erase_changed = False
        self._last_erase_pos = None
        self._current_stroke = None
        self._current_shape = None
        self._local_stroke_id = None
        self._remote_strokes = {}
        self._drawing = False
        self._panning = False
        self._pan_last = None
        self._resizing = False
        self._resize_item = None
        self._resize_origin = QPointF()
        self._resize_native = QPointF()
        self._resize_start_dist = 0.0
        self._resize_start_scale = 1.0
        self._dragging_object = False
        self._drag_item = None
        self._live_item = None
        self._drag_offset = QPoint()
        self.selected_item = None
        self._selecting = False
        self._select_start = None
        self._select_current = None
        self._region_rect = None
        self._text_editor = None
        self._text_canvas_pos = None
        self._eraser_pos = None
        self._q_key_down = False
        self._caps_held = False
        self.offset = QPoint(0, 0)
        self._canvas_w = 1
        self._canvas_h = 1
        self._margin_x = 0
        self._margin_y = 0
        self._static_cache = None
        self._cache_dpr = 1.0
        self._cache_dirty = True
        self._offset_initialized = False
        self._remote_repaint_timer = QTimer(self)
        self._remote_repaint_timer.setSingleShot(True)
        self._remote_repaint_timer.setInterval(16)
        self._remote_repaint_timer.timeout.connect(self.update)
        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(32)
        self._hold_timer.timeout.connect(self._poll_shape_hold_keys)
        self._hold_timer.start()

        self._init_clipboard_image()
        self._update_cursor()

    def _text_font(self):
        font = QFont(TEXT_FONT_FAMILY, TEXT_FONT_SIZE)
        font.setStyleStrategy(QFont.PreferAntialias)
        if hasattr(QFont, "PreferNoHinting"):
            font.setHintingPreference(QFont.PreferNoHinting)
        return font

    def _dpr(self):
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except Exception:
            return max(1.0, float(self.devicePixelRatio()))

    def _configure_painter(self, painter):
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if hasattr(QPainter, "HighQualityAntialiasing"):
            painter.setRenderHint(QPainter.HighQualityAntialiasing, True)

    def _init_clipboard_image(self):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                self.items.append(
                    {
                        "type": "image",
                        "id": str(uuid.uuid4()),
                        "pixmap": QPixmap.fromImage(image),
                        "pos": QPoint(0, 0),
                        "scale": 1.0,
                    }
                )

    def _paste_clipboard_image(self):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if not mime.hasImage():
            return False
        image = clipboard.image()
        if image.isNull():
            return False
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return False
        self._push_restore_undo()
        center = self._view_to_canvas(QPoint(self.width() // 2, self.height() // 2))
        pos = QPoint(
            center.x() - pixmap.width() // 2,
            center.y() - pixmap.height() // 2,
        )
        self.items.append(
            {
                "type": "image",
                "id": str(uuid.uuid4()),
                "undo_id": str(uuid.uuid4()),
                "pixmap": pixmap,
                "pos": pos,
                "scale": 1.0,
            }
        )
        self._cache_dirty = True
        self.update()
        self.content_changed.emit()
        return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_canvas_bounds()
        if not self._offset_initialized and self.width() > 0 and self.height() > 0:
            self.offset = QPoint(self._margin_x, self._margin_y)
            self._offset_initialized = True
            for item in self.items:
                if item["type"] == "image" and item["pos"].x() == 0 and item["pos"].y() == 0:
                    item["pos"] = QPoint(self._margin_x, self._margin_y)
        self._cache_dirty = True
        self.view_changed.emit()

    def _update_canvas_bounds(self):
        w = max(1, self.width())
        h = max(1, self.height())
        self._canvas_w = max(w, int(round(w * CANVAS_SCALE)))
        self._canvas_h = max(h, int(round(h * CANVAS_SCALE)))
        self._margin_x = (self._canvas_w - w) // 2
        self._margin_y = (self._canvas_h - h) // 2
        self.offset.setX(max(0, min(self._max_offset_x(), self.offset.x())))
        self.offset.setY(max(0, min(self._max_offset_y(), self.offset.y())))

    def _max_offset_x(self):
        return max(0, self._canvas_w - self.width())

    def _max_offset_y(self):
        return max(0, self._canvas_h - self.height())

    def _view_to_canvas(self, pos):
        return QPoint(int(pos.x()) + self.offset.x(), int(pos.y()) + self.offset.y())

    def _view_to_canvas_f(self, pos):
        return QPointF(float(pos.x()) + float(self.offset.x()), float(pos.y()) + float(self.offset.y()))

    def _canvas_to_view(self, pos):
        return QPoint(int(pos.x()) - self.offset.x(), int(pos.y()) - self.offset.y())

    def _to_norm(self, canvas_pos):
        return (
            float(canvas_pos.x()) / float(max(1, self._canvas_w)),
            float(canvas_pos.y()) / float(max(1, self._canvas_h)),
        )

    def _from_norm(self, x, y):
        return QPoint(int(round(float(x) * self._canvas_w)), int(round(float(y) * self._canvas_h)))

    def _from_norm_f(self, x, y):
        return QPointF(float(x) * float(self._canvas_w), float(y) * float(self._canvas_h))

    def _serialize_stroke(self, stroke):
        return {
            "color": stroke["color"].name(),
            "points": [self._to_norm(point) for point in stroke["points"]],
        }

    def _serialize_image(self, item):
        image = item["pixmap"].toImage()
        ba = QByteArray()
        buffer = QBuffer(ba)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        rect = self._image_rect(item)
        return {
            "id": item.get("id", ""),
            "data": base64.b64encode(bytes(ba)).decode("ascii"),
            "x": float(rect.x()) / float(max(1, self._canvas_w)),
            "y": float(rect.y()) / float(max(1, self._canvas_h)),
            "w": float(rect.width()) / float(max(1, self._canvas_w)),
            "h": float(rect.height()) / float(max(1, self._canvas_h)),
        }

    def _serialize_text(self, item):
        return {
            "text": item["text"],
            "x": float(item["pos"].x()) / float(max(1, self._canvas_w)),
            "y": float(item["pos"].y()) / float(max(1, self._canvas_h)),
            "color": item["color"].name(),
        }

    def _deserialize_stroke(self, data):
        return {
            "type": "stroke",
            "author": data.get("author", "host"),
            "color": QColor(data.get("color", "#000000")),
            "points": [self._from_norm_f(float(x), float(y)) for x, y in data.get("points", [])],
        }

    def _deserialize_image(self, data):
        raw = base64.b64decode(data.get("data", ""))
        image = QImage()
        if not image.loadFromData(raw, "PNG"):
            return None
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return None
        nw = float(data.get("w", 0.1)) * self._canvas_w
        nh = float(data.get("h", 0.1)) * self._canvas_h
        scale = 1.0
        if pixmap.width() > 0 and pixmap.height() > 0:
            scale = min(nw / pixmap.width(), nh / pixmap.height())
            if scale <= 0:
                scale = 1.0
        return {
            "type": "image",
            "id": data.get("id") or str(uuid.uuid4()),
            "pixmap": pixmap,
            "pos": self._from_norm(float(data.get("x", 0)), float(data.get("y", 0))),
            "scale": scale,
        }

    def _deserialize_text(self, data):
        return {
            "type": "text",
            "text": str(data.get("text", "")),
            "pos": self._from_norm(float(data.get("x", 0)), float(data.get("y", 0))),
            "color": QColor(data.get("color", "#000000")),
        }

    def import_state(self, state):
        if not state:
            return
        self._finish_text_editor(False)
        self._undo_stack = []
        self._erase_snapshot = None
        self._erase_changed = False
        self.items = []
        self._remote_strokes = {}
        self._current_stroke = None
        self._current_shape = None
        self._local_stroke_id = None
        self.selected_item = None
        self._region_rect = None
        self._selecting = False

        if "offsetX" in state:
            self.offset = QPoint(int(state.get("offsetX", 0)), int(state.get("offsetY", 0)))
            self._offset_initialized = True

        for stroke in state.get("strokes", []):
            item = self._deserialize_stroke(stroke)
            if item["points"]:
                self.items.append(item)
        for image in state.get("images", []):
            item = self._deserialize_image(image)
            if item is not None:
                self.items.append(item)
        for text in state.get("texts", []):
            item = self._deserialize_text(text)
            if item["text"]:
                self.items.append(item)

        self._cache_dirty = True
        self.update()
        self.content_changed.emit()

    def export_state(self):
        strokes = []
        for item in self.items:
            if item["type"] == "stroke":
                strokes.append(self._serialize_stroke(item))
            else:
                points = self._shape_as_stroke_points(item)
                if points:
                    strokes.append(
                        self._serialize_stroke(
                            {
                                "color": item.get("color", QColor(COLOR_BLACK)),
                                "points": points,
                            }
                        )
                    )
        if self._current_stroke is not None:
            strokes.append(self._serialize_stroke(self._current_stroke))
        if self._current_shape is not None:
            points = self._shape_as_stroke_points(self._current_shape)
            if points:
                strokes.append(
                    self._serialize_stroke(
                        {
                            "color": self._current_shape.get("color", QColor(COLOR_BLACK)),
                            "points": points,
                        }
                    )
                )
        for stroke in self._remote_strokes.values():
            strokes.append(self._serialize_stroke(stroke))
        images = [self._serialize_image(item) for item in self.items if item["type"] == "image"]
        texts = [self._serialize_text(item) for item in self.items if item["type"] == "text"]
        return {
            "viewW": max(1, self.width()),
            "viewH": max(1, self.height()),
            "offsetX": self.offset.x(),
            "offsetY": self.offset.y(),
            "canvasW": self._canvas_w,
            "canvasH": self._canvas_h,
            "strokes": strokes,
            "images": images,
            "texts": texts,
        }

    def begin_remote_stroke(self, stroke_id, color, x, y):
        if stroke_id in self._remote_strokes:
            return
        self._remote_strokes[stroke_id] = {
            "type": "stroke",
            "author": "guest",
            "color": QColor(color),
            "points": [self._from_norm_f(x, y)],
        }
        self._schedule_remote_repaint()

    def erase_remote(self, x, y):
        self._erase_at(self._from_norm_f(x, y), mode=ERASER_OBJECT)

    def undo_guest(self):
        if self._remote_strokes:
            last_id = next(reversed(self._remote_strokes))
            self._remote_strokes.pop(last_id, None)
            self._schedule_remote_repaint()
            self.content_changed.emit()
            return
        for index in range(len(self.items) - 1, -1, -1):
            item = self.items[index]
            if item.get("type") == "stroke" and item.get("author") == "guest":
                removed = self.items.pop(index)
                if removed is self.selected_item:
                    self.selected_item = None
                    self._region_rect = None
                self._cache_dirty = True
                self.update()
                self.content_changed.emit()
                return

    def add_remote_point(self, stroke_id, x, y):
        stroke = self._remote_strokes.get(stroke_id)
        if stroke is None:
            self.begin_remote_stroke(stroke_id, "#1565c0", x, y)
            return
        stroke["points"].append(self._from_norm_f(x, y))
        self._schedule_remote_repaint()

    def add_remote_points(self, stroke_id, points):
        if not points:
            return
        stroke = self._remote_strokes.get(stroke_id)
        if stroke is None:
            self.begin_remote_stroke(stroke_id, "#1565c0", points[0][0], points[0][1])
            points = points[1:]
        for x, y in points:
            stroke["points"].append(self._from_norm_f(float(x), float(y)))
        self._schedule_remote_repaint()

    def end_remote_stroke(self, stroke_id):
        stroke = self._remote_strokes.pop(stroke_id, None)
        if stroke and stroke["points"]:
            self.items.append(stroke)
            self._cache_dirty = True
            self._schedule_remote_repaint()

    def _schedule_remote_repaint(self):
        if not self._remote_repaint_timer.isActive():
            self._remote_repaint_timer.start()

    def clear_all(self):
        self._finish_text_editor(False)
        if self.items or self._remote_strokes:
            self._push_restore_undo()
        self._erase_snapshot = None
        self._erase_changed = False
        self.items = []
        self._remote_strokes = {}
        self._current_stroke = None
        self._current_shape = None
        self._local_stroke_id = None
        self._drawing = False
        self.selected_item = None
        self._region_rect = None
        self._cache_dirty = True
        self.update()
        self.content_changed.emit()

    def _initial_image_scale(self, pixmap, max_size=None):
        max_dim = max(pixmap.width(), pixmap.height(), 1)
        limit = STICKER_MAX_SIZE if max_size is None else float(max_size)
        return limit / max_dim

    def _object_size(self, item):
        return QSize(
            max(1, int(item["pixmap"].width() * item["scale"])),
            max(1, int(item["pixmap"].height() * item["scale"])),
        )

    def _image_rect(self, item):
        return QRect(item["pos"], self._object_size(item))

    def _text_metrics(self, text=""):
        metrics = QFontMetrics(self._text_font())
        lines = (text or "").split("\n") or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines)
        height = metrics.lineSpacing() * max(0, len(lines) - 1) + metrics.height()
        return metrics, lines, width, height

    def _text_rect(self, item):
        _metrics, _lines, width, height = self._text_metrics(item["text"])
        pos = item["pos"]
        return QRect(int(pos.x()), int(pos.y()), max(1, width), max(1, height))

    def _draw_text_block(self, painter, pos, text, color):
        font = self._text_font()
        painter.setFont(font)
        painter.setPen(color)
        metrics = QFontMetrics(font)
        x = float(pos.x())
        y = float(pos.y())
        for line in (text or "").split("\n"):
            painter.drawText(QPointF(x, y + metrics.ascent()), line)
            y += metrics.lineSpacing()

    def _item_rect(self, item):
        if item["type"] == "image":
            return self._image_rect(item)
        if item["type"] == "text":
            return self._text_rect(item)
        return QRect()

    def _hit_item(self, canvas_pos):
        for item in reversed(self.items):
            if item["type"] in ("image", "text") and self._item_rect(item).contains(canvas_pos):
                return item
        return None

    def _resize_handle_rect(self, item):
        rect = self._item_rect(item)
        return QRect(
            rect.right() - HANDLE_SIZE,
            rect.bottom() - HANDLE_SIZE,
            HANDLE_SIZE,
            HANDLE_SIZE,
        )

    def _marquee_rect(self):
        if self._selecting and self._select_start is not None and self._select_current is not None:
            return QRect(self._select_start, self._select_current).normalized()
        if self._region_rect is not None:
            return QRect(self._region_rect)
        return None

    def _clear_region(self):
        self._selecting = False
        self._select_start = None
        self._select_current = None
        self._region_rect = None

    def _segment_intersection(self, p1, p2, q1, q2):
        x1, y1 = float(p1.x()), float(p1.y())
        x2, y2 = float(p2.x()), float(p2.y())
        x3, y3 = float(q1.x()), float(q1.y())
        x4, y4 = float(q2.x()), float(q2.y())
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(den) < 1e-9:
            return None
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
        u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
        if t < -1e-6 or t > 1.0 + 1e-6 or u < -1e-6 or u > 1.0 + 1e-6:
            return None
        t = max(0.0, min(1.0, t))
        return QPointF(x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    def _segment_rect_hits(self, p1, p2, rect):
        l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
        edges = (
            (QPoint(l, t), QPoint(r, t)),
            (QPoint(r, t), QPoint(r, b)),
            (QPoint(r, b), QPoint(l, b)),
            (QPoint(l, b), QPoint(l, t)),
        )
        hits = []
        seen = set()
        for a, bpt in edges:
            hit = self._segment_intersection(p1, p2, a, bpt)
            if hit is None:
                continue
            key = (round(float(hit.x()), 3), round(float(hit.y()), 3))
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
        hits.sort(key=lambda p: (p.x() - p1.x()) ** 2 + (p.y() - p1.y()) ** 2)
        return hits

    def _clip_stroke_outside(self, points, rect):
        if not points:
            return []
        fragments = []
        current = []

        def push(point):
            point = _as_pointf(point)
            if not current or current[-1].x() != point.x() or current[-1].y() != point.y():
                current.append(point)

        def close_frag():
            if current:
                fragments.append(list(current))
                current.clear()

        def inside(point):
            return QRectF(rect).contains(_as_pointf(point))

        if not inside(points[0]):
            push(points[0])

        for i in range(1, len(points)):
            a, bpt = points[i - 1], points[i]
            in_a, in_b = inside(a), inside(bpt)
            hits = self._segment_rect_hits(a, bpt, rect)
            if in_a and in_b:
                continue
            if (not in_a) and (not in_b):
                if len(hits) >= 2:
                    push(hits[0])
                    close_frag()
                    push(hits[-1])
                    push(bpt)
                else:
                    push(bpt)
                continue
            if not hits:
                if not in_b:
                    push(bpt)
                continue
            hit = hits[0]
            if in_a and not in_b:
                push(hit)
                push(bpt)
            else:
                push(hit)
                close_frag()
        close_frag()
        return fragments

    def _punch_image(self, item, canvas_rect):
        img_rect = self._image_rect(item)
        inter = img_rect.intersected(canvas_rect)
        if inter.isEmpty():
            return
        image = item["pixmap"].toImage().convertToFormat(QImage.Format_ARGB32)
        scale = item["scale"] if item["scale"] else 1.0
        sx = int(round((inter.x() - item["pos"].x()) / scale))
        sy = int(round((inter.y() - item["pos"].y()) / scale))
        sw = max(1, int(round(inter.width() / scale)))
        sh = max(1, int(round(inter.height() / scale)))
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(sx, sy, sw, sh, QColor(255, 255, 255, 255))
        painter.end()
        item["pixmap"] = QPixmap.fromImage(image)

    def _erase_rect(self, rect):
        rect = QRect(rect).normalized()
        if rect.isEmpty():
            return
        remaining = []
        for item in self.items:
            kind = item["type"]
            if kind == "image":
                img_rect = self._image_rect(item)
                if rect.contains(img_rect):
                    if item is self.selected_item:
                        self.selected_item = None
                    continue
                if img_rect.intersects(rect):
                    self._punch_image(item, rect)
                remaining.append(item)
            elif kind == "text":
                if rect.intersects(self._text_rect(item)):
                    if item is self.selected_item:
                        self.selected_item = None
                    continue
                remaining.append(item)
            elif kind == "stroke":
                fragments = self._clip_stroke_outside(item["points"], rect)
                if not fragments:
                    if item is self.selected_item:
                        self.selected_item = None
                    continue
                item["points"] = fragments[0]
                remaining.append(item)
                for extra in fragments[1:]:
                    remaining.append(self._copy_stroke(item, extra))
            elif kind in ("line", "arrow", "rect", "square", "circle", "triangle"):
                bounds = self._vector_bounds(item)
                if bounds is not None and bounds.toAlignedRect().intersects(rect):
                    if item is self.selected_item:
                        self.selected_item = None
                    continue
                remaining.append(item)
            else:
                remaining.append(item)
        self.items = remaining

        new_remote = {}
        extras = []
        for stroke_id, stroke in self._remote_strokes.items():
            fragments = self._clip_stroke_outside(stroke["points"], rect)
            if not fragments:
                continue
            stroke["points"] = fragments[0]
            new_remote[stroke_id] = stroke
            for extra in fragments[1:]:
                extras.append(self._copy_stroke(stroke, extra))
        self._remote_strokes = new_remote
        self.items.extend(extras)
        self._cache_dirty = True

    def _snapshot_rect(self, rect):
        rect = QRect(rect).normalized().intersected(QRect(0, 0, self._canvas_w, self._canvas_h))
        if rect.isEmpty():
            return None
        if self._static_cache is None or self._cache_dirty:
            self._rebuild_cache()
        snippet = QPixmap(rect.size())
        snippet.fill(Qt.white)
        painter = QPainter(snippet)
        self._configure_painter(painter)
        dpr = self._cache_dpr if self._static_cache is not None else 1.0
        source = QRectF(
            float(rect.x()) * dpr,
            float(rect.y()) * dpr,
            float(rect.width()) * dpr,
            float(rect.height()) * dpr,
        )
        painter.drawPixmap(QRectF(snippet.rect()), self._static_cache, source)
        painter.translate(-rect.x(), -rect.y())
        painter.setClipRect(rect)
        if self._current_stroke is not None:
            self._draw_stroke(painter, self._current_stroke)
        if self._current_shape is not None:
            self._draw_item(painter, self._current_shape)
        for stroke in self._remote_strokes.values():
            self._draw_stroke(painter, stroke)
        painter.end()
        return snippet, rect

    def _lift_region(self, rect):
        snapped = self._snapshot_rect(rect)
        if snapped is None:
            return None
        snippet, rect = snapped
        self._push_restore_undo()
        self._erase_rect(rect)
        item = {
            "type": "image",
            "id": str(uuid.uuid4()),
            "pixmap": snippet,
            "pos": QPoint(rect.topLeft()),
            "scale": 1.0,
            "lifted": True,
        }
        self.items.append(item)
        self.selected_item = item
        self._region_rect = None
        self._cache_dirty = True
        self.update()
        self.content_changed.emit()
        return item

    def delete_selection(self):
        if self._text_editor is not None:
            return False
        if self._region_rect is not None:
            self._push_restore_undo()
            self._erase_rect(self._region_rect)
            self._clear_region()
            self.update()
            self.content_changed.emit()
            return True
        if self.selected_item is None:
            return False
        self._push_restore_undo()
        if self.selected_item in self.items:
            self.items.remove(self.selected_item)
        self.selected_item = None
        self._cache_dirty = True
        self.update()
        self.content_changed.emit()
        return True

    def _update_select_cursor(self, canvas_pos):
        if self._region_rect is not None and self._region_rect.contains(canvas_pos):
            self.setCursor(Qt.SizeAllCursor)
            return
        if (
            self.selected_item is not None
            and self.selected_item.get("lifted")
            and self._item_rect(self.selected_item).contains(canvas_pos)
        ):
            self.setCursor(Qt.SizeAllCursor)
            return
        self.setCursor(Qt.CrossCursor)

    def _finish_text_editor(self, commit):
        if self._text_editor is None:
            return
        text = self._text_editor.toPlainText().strip()
        editor = self._text_editor
        canvas_pos = self._text_canvas_pos
        self._text_editor = None
        self._text_canvas_pos = None
        editor.deleteLater()
        if commit and text and canvas_pos is not None:
            self._push_restore_undo()
            self.items.append(
                {
                    "type": "text",
                    "text": text,
                    "pos": QPoint(canvas_pos),
                    "color": QColor(self.pen_color),
                }
            )
            self._cache_dirty = True
            self.update()
            self.content_changed.emit()

    def set_tool(self, tool):
        self._finish_text_editor(False)
        if self._drawing and self.tool == TOOL_ERASER:
            self._commit_erase_undo()
        self.tool = tool
        self._drawing = False
        self._last_erase_pos = None
        self._panning = False
        self._resizing = False
        self._dragging_object = False
        self._drag_item = None
        self._current_stroke = None
        self._current_shape = None
        self._selecting = False
        self._select_start = None
        self._select_current = None
        self._update_cursor()

    def _update_cursor(self):
        if self.tool == TOOL_HAND:
            self.setCursor(Qt.OpenHandCursor)
        elif self.tool == TOOL_ERASER:
            self.setCursor(make_eraser_cursor(self.eraser_width))
        elif self.tool == TOOL_TEXT:
            self.setCursor(Qt.IBeamCursor)
        elif self.tool == TOOL_SELECT:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(make_pen_dot_cursor(self.pen_color, self.pen_width + 3))

    def add_sticker(self, path, max_size=None):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        scale = self._initial_image_scale(pixmap, max_size)
        display_w = max(1, int(pixmap.width() * scale))
        display_h = max(1, int(pixmap.height() * scale))
        center = self._view_to_canvas(QPoint(self.width() // 2, self.height() // 2))
        pos = QPoint(
            center.x() - display_w // 2,
            center.y() - display_h // 2,
        )
        self._push_restore_undo()
        item = {"type": "image", "id": str(uuid.uuid4()), "pixmap": pixmap, "pos": pos, "scale": scale}
        self.items.append(item)
        self.selected_item = item
        self._cache_dirty = True
        self.update()
        self.content_changed.emit()

    def _scale_selected(self, factor):
        if self.selected_item is None or self.selected_item["type"] != "image":
            return
        new_scale = self.selected_item["scale"] * factor
        self.selected_item["scale"] = max(MIN_OBJECT_SCALE, min(MAX_OBJECT_SCALE, new_scale))
        self._cache_dirty = True
        self.update()
        self.content_changed.emit()

    def _start_text_input(self, canvas_pos):
        self._finish_text_editor(False)
        view_pos = self._canvas_to_view(canvas_pos)
        self._text_canvas_pos = QPoint(canvas_pos)
        editor = QPlainTextEdit(self)
        editor.setFont(self._text_font())
        editor.setFrameStyle(QFrame.NoFrame)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.setWordWrapMode(QTextOption.NoWrap)
        editor.document().setDocumentMargin(0)
        editor.setStyleSheet(
            "QPlainTextEdit { background: transparent; color: %s; border: none; padding: 0px; }"
            % self.pen_color.name()
        )
        editor.move(view_pos)
        editor.textChanged.connect(self._resize_text_editor)
        editor.installEventFilter(self)
        self._text_editor = editor
        self._resize_text_editor()
        editor.show()
        origin = editor.viewport().mapTo(editor, QPoint(0, 0))
        editor.move(view_pos.x() - origin.x(), view_pos.y() - origin.y())
        editor.setFocus()

    def _resize_text_editor(self, _text=""):
        editor = self._text_editor
        if editor is None:
            return
        metrics = QFontMetrics(editor.font())
        lines = (editor.toPlainText() or "").split("\n") or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines)
        needed = width + metrics.horizontalAdvance("W") + 4
        min_w = metrics.horizontalAdvance("MMMM") + 4
        max_w = max(min_w, self.width() - editor.x() - 8)
        height = metrics.lineSpacing() * len(lines) + 4
        editor.setFixedWidth(max(min_w, min(needed, max_w)))
        editor.setFixedHeight(max(metrics.height() + 4, height))

    def eventFilter(self, obj, event):
        if obj is self._text_editor:
            if event.type() == QEvent.KeyPress:
                key = event.key()
                if key == Qt.Key_Escape:
                    self._finish_text_editor(False)
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
                    self._finish_text_editor(True)
                    return True
            if event.type() == QEvent.FocusOut:
                self._finish_text_editor(True)
                return False
        return super().eventFilter(obj, event)

    def _q_is_held(self):
        return hold_down("hold_circle", tracked=bool(self._q_key_down))

    def _sync_shape_hold_keys(self):
        circle_down, triangle_down = sync_hold_state()
        self._q_key_down = circle_down
        self._caps_held = triangle_down

    def _poll_shape_hold_keys(self):
        if self._text_editor is not None:
            return
        prev_q = self._q_is_held()
        prev_caps = self._caps_held
        self._sync_shape_hold_keys()
        if self._q_is_held() != prev_q or self._caps_held != prev_caps:
            self.update()

    def event(self, event):
        etype = event.type()
        if etype == QEvent.ShortcutOverride and is_hold_key(event.key()):
            event.accept()
            return True
        if etype in (QEvent.KeyPress, QEvent.KeyRelease) and is_hold_key(event.key()):
            if etype == QEvent.KeyPress:
                self.keyPressEvent(event)
            else:
                self.keyReleaseEvent(event)
            return True
        return super().event(event)

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        if event.key() == hold_qt_key("hold_circle"):
            self._q_key_down = hold_down("hold_circle")
            self.update()
            return
        if event.key() == hold_qt_key("hold_triangle"):
            self._caps_held = hold_down("hold_triangle")
            self.update()
            return
        super().keyReleaseEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if matches(event, "delete"):
            if self.delete_selection():
                return
        if matches(event, "brush_thicker"):
            self._nudge_brush(1)
            return
        if matches(event, "brush_thinner"):
            self._nudge_brush(-1)
            return
        if matches(event, "color_black"):
            self.pen_color = QColor(COLOR_BLACK)
            self.set_tool(TOOL_PEN)
            return
        if matches(event, "color_blue"):
            self.pen_color = QColor(COLOR_BLUE)
            self.set_tool(TOOL_PEN)
            return
        if matches(event, "color_red"):
            self.pen_color = QColor(COLOR_RED)
            self.set_tool(TOOL_PEN)
            return
        if matches(event, "color_green"):
            self.pen_color = QColor(COLOR_GREEN)
            self.set_tool(TOOL_PEN)
            return
        if matches(event, "scale_up") and self.selected_item is not None:
            self._scale_selected(1.1)
            return
        if matches(event, "scale_down") and self.selected_item is not None:
            self._scale_selected(0.9)
            return
        if matches(event, "undo"):
            self._undo()
            return
        if matches(event, "paste"):
            if self._paste_clipboard_image():
                return
        if key == hold_qt_key("hold_circle"):
            self._q_key_down = True
            self.update()
            return
        if key == hold_qt_key("hold_triangle"):
            self._caps_held = True
            self.update()
            return
        super().keyPressEvent(event)

    def _nudge_brush(self, direction):
        if self.tool == TOOL_ERASER:
            self.eraser_width = clamp_eraser_width(
                self.eraser_width + direction * ERASER_WIDTH_STEP
            )
            label = "Trintukas: %d" % int(round(self.eraser_width))
        else:
            self.pen_width = clamp_pen_width(self.pen_width + direction * PEN_WIDTH_STEP)
            label = "Pieštukas: %d" % int(round(self.pen_width))
        self._update_cursor()
        QToolTip.showText(QCursor.pos(), label, self)
        self.update()

    def wheelEvent(self, event):
        if self.selected_item is not None and self.selected_item["type"] == "image":
            delta = event.angleDelta().y()
            if delta > 0:
                self._scale_selected(1.08)
            elif delta < 0:
                self._scale_selected(0.92)
            event.accept()
            return
        super().wheelEvent(event)

    def _clone_item(self, item):
        cloned = dict(item)
        if "color" in cloned:
            cloned["color"] = QColor(cloned["color"])
        if "pos" in cloned:
            pos = cloned["pos"]
            cloned["pos"] = QPointF(pos) if isinstance(pos, QPointF) else QPoint(pos)
        if "points" in cloned:
            cloned["points"] = [QPointF(p) for p in cloned["points"]]
        if "p1" in cloned:
            cloned["p1"] = QPointF(cloned["p1"])
        if "p2" in cloned:
            cloned["p2"] = QPointF(cloned["p2"])
        if "rect" in cloned:
            cloned["rect"] = QRectF(cloned["rect"])
        if "center" in cloned:
            cloned["center"] = QPointF(cloned["center"])
        if "_origin" in cloned:
            cloned["_origin"] = QPointF(cloned["_origin"])
        cloned.pop("_erase_raster", None)
        return cloned

    def _clone_canvas_state(self):
        return {
            "items": [self._clone_item(item) for item in self.items],
            "remote": {
                stroke_id: self._clone_item(stroke)
                for stroke_id, stroke in self._remote_strokes.items()
            },
        }

    def _push_restore_undo(self, snapshot=None):
        if snapshot is None:
            snapshot = self._clone_canvas_state()
        self._undo_stack.append(snapshot)
        extra = len(self._undo_stack) - MAX_UNDO
        if extra > 0:
            del self._undo_stack[:extra]

    def _apply_canvas_state(self, snapshot):
        self.items = snapshot["items"]
        self._remote_strokes = dict(snapshot.get("remote") or {})
        self.selected_item = None
        self._region_rect = None
        self._current_stroke = None
        self._current_shape = None
        self._cache_dirty = True
        self.update()
        self.content_changed.emit()

    def _begin_erase_undo(self):
        self._erase_snapshot = self._clone_canvas_state()
        self._erase_changed = False
        self._last_erase_pos = None

    def _commit_erase_undo(self):
        self._flush_erase_rasters()
        if self._erase_snapshot is not None and self._erase_changed:
            self._push_restore_undo(self._erase_snapshot)
            self.content_changed.emit()
        self._erase_snapshot = None
        self._erase_changed = False
        self._last_erase_pos = None

    def _undo(self):
        self._finish_text_editor(False)
        if self._erase_snapshot is not None:
            self._apply_canvas_state(self._erase_snapshot)
            self._erase_snapshot = None
            self._erase_changed = False
            self._drawing = False
            self._last_erase_pos = None
            return
        if self._drawing and (self._current_stroke is not None or self._current_shape is not None):
            self._drawing = False
            self._current_stroke = None
            self._current_shape = None
            self._local_stroke_id = None
            self.update()
            return
        if not self._undo_stack:
            return
        self._apply_canvas_state(self._undo_stack.pop())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        self._sync_shape_hold_keys()
        canvas_pos = self._view_to_canvas(event.pos())
        canvas_pos_f = self._view_to_canvas_f(event.localPos())

        if self.tool == TOOL_TEXT:
            self._start_text_input(canvas_pos)
            return

        if self.tool == TOOL_HAND:
            self._region_rect = None
            item = self._hit_item(canvas_pos)
            if item is not None and item["type"] == "image" and self._resize_handle_rect(item).contains(canvas_pos):
                self._resizing = True
                self._resize_item = item
                self.selected_item = item
                self._live_item = item
                self._resize_origin = QPointF(item["pos"])
                pixmap = item["pixmap"]
                self._resize_native = QPointF(float(max(1, pixmap.width())), float(max(1, pixmap.height())))
                self._resize_start_scale = float(item["scale"] or 1.0)
                self._cache_dirty = True
                return
            if item is not None:
                self.selected_item = item
                self._dragging_object = True
                self._drag_item = item
                self._live_item = item
                self._drag_offset = canvas_pos - item["pos"]
                self.setCursor(Qt.ClosedHandCursor)
                self._cache_dirty = True
                self.update()
                return
            self.selected_item = None
            self._panning = True
            self._pan_last = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return

        if self.tool == TOOL_SELECT:
            if self._region_rect is not None and self._region_rect.contains(canvas_pos):
                item = self._lift_region(self._region_rect)
                if item is not None:
                    item["lifted"] = True
                    self._dragging_object = True
                    self._drag_item = item
                    self._live_item = item
                    self._drag_offset = canvas_pos - item["pos"]
                    self.setCursor(Qt.SizeAllCursor)
                    self._cache_dirty = True
                return
            if (
                self.selected_item is not None
                and self.selected_item.get("lifted")
                and self._item_rect(self.selected_item).contains(canvas_pos)
            ):
                self._dragging_object = True
                self._drag_item = self.selected_item
                self._live_item = self.selected_item
                self._drag_offset = canvas_pos - self.selected_item["pos"]
                self.setCursor(Qt.SizeAllCursor)
                self._cache_dirty = True
                self.update()
                return
            self.selected_item = None
            self._region_rect = None
            self._selecting = True
            self._select_start = QPoint(canvas_pos)
            self._select_current = QPoint(canvas_pos)
            self.update()
            return

        if self.tool == TOOL_ERASER:
            self._region_rect = None
            self._drawing = True
            self._eraser_pos = canvas_pos_f
            self._last_erase_pos = QPointF(canvas_pos_f)
            self._begin_erase_undo()
            self._erase_at(canvas_pos_f)
            return

        self._region_rect = None
        self._drawing = True
        shape = self._shape_from_modifiers()
        if shape != "stroke":
            self._current_shape = self._begin_shape(canvas_pos_f, shape)
            self.update()
            return
        self._local_stroke_id = str(uuid.uuid4())
        self._current_stroke = {
            "type": "stroke",
            "author": "host",
            "color": QColor(self.pen_color),
            "width": self.pen_width,
            "points": [QPointF(canvas_pos_f)],
        }
        nx, ny = self._to_norm(canvas_pos_f)
        self.local_stroke_start.emit(self._local_stroke_id, self.pen_color.name(), nx, ny)
        self.update()

    def mouseMoveEvent(self, event):
        canvas_pos = self._view_to_canvas(event.pos())
        canvas_pos_f = self._view_to_canvas_f(event.localPos())

        if self._dragging_object and self._drag_item is not None:
            self._drag_item["pos"] = canvas_pos - self._drag_offset
            self.update()
            return

        if self._resizing and self._resize_item is not None:
            origin = self._resize_origin
            nw = self._resize_native.x()
            nh = self._resize_native.y()
            dx = max(1.0, float(canvas_pos_f.x()) - origin.x())
            dy = max(1.0, float(canvas_pos_f.y()) - origin.y())
            denom = max(1.0, nw * nw + nh * nh)
            new_scale = (dx * nw + dy * nh) / denom
            self._resize_item["scale"] = max(MIN_OBJECT_SCALE, min(MAX_OBJECT_SCALE, new_scale))
            self.update()
            return

        if self.tool == TOOL_HAND and self._panning and self._pan_last is not None:
            delta = event.pos() - self._pan_last
            self._pan_last = event.pos()
            self.offset.setX(max(0, min(self._max_offset_x(), self.offset.x() - delta.x())))
            self.offset.setY(max(0, min(self._max_offset_y(), self.offset.y() - delta.y())))
            self.update()
            self.view_changed.emit()
            return

        if self._selecting:
            self._select_current = QPoint(canvas_pos)
            self.update()
            return

        if not self._drawing:
            self._eraser_pos = canvas_pos_f
            if self.tool == TOOL_SELECT:
                self._update_select_cursor(canvas_pos)
            elif self.tool == TOOL_ERASER:
                self.update()
            return

        if self.tool == TOOL_ERASER:
            self._eraser_pos = canvas_pos_f
            last = self._last_erase_pos
            if last is None:
                self._erase_at(canvas_pos_f)
            else:
                self._erase_along(last, canvas_pos_f)
            self._last_erase_pos = QPointF(canvas_pos_f)
            self.update()
            return

        if self._current_shape is not None:
            self._update_shape(canvas_pos_f)
            self.update()
            return

        if self._current_stroke is not None:
            if _append_stroke_point(self._current_stroke["points"], canvas_pos_f):
                if self._local_stroke_id:
                    nx, ny = self._to_norm(canvas_pos_f)
                    self.local_stroke_point.emit(self._local_stroke_id, nx, ny)
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        moved_object = self._dragging_object
        resized_object = self._resizing

        if self._resizing:
            self._resizing = False
            self._resize_item = None
            self._live_item = None
            self._cache_dirty = True
            if resized_object:
                self.content_changed.emit()
            self.update()
            return

        if self._dragging_object:
            self._dragging_object = False
            self._drag_item = None
            self._live_item = None
            self._cache_dirty = True
            self._update_cursor()
            if moved_object:
                self.content_changed.emit()
            self.update()
            return

        if self._selecting:
            self._selecting = False
            rect = QRect(self._select_start, self._select_current).normalized()
            if rect.width() >= MIN_SELECT_SIZE and rect.height() >= MIN_SELECT_SIZE:
                self._region_rect = rect
                self.selected_item = None
            else:
                self._region_rect = None
            self._select_start = None
            self._select_current = None
            self._update_cursor()
            self.update()
            return

        if self.tool == TOOL_HAND:
            self._panning = False
            self._pan_last = None
            self.setCursor(Qt.OpenHandCursor)
            return

        if self.tool == TOOL_ERASER:
            self._drawing = False
            self._commit_erase_undo()
            return

        if self._drawing:
            self._drawing = False
            end_pos = self._view_to_canvas_f(event.localPos())
            if self._current_shape is not None:
                self._update_shape(end_pos)
                shape = self._current_shape
                self._current_shape = None
                shape.pop("_origin", None)
                if self._shape_is_valid(shape):
                    self._push_restore_undo()
                    self.items.append(shape)
                    self._cache_dirty = True
                    self.content_changed.emit()
            elif self._current_stroke is not None:
                points = self._current_stroke["points"]
                added = _append_stroke_point(points, end_pos)
                if not added and points:
                    points[-1] = _as_pointf(end_pos)
                    added = True
                if added and self._local_stroke_id:
                    nx, ny = self._to_norm(end_pos)
                    self.local_stroke_point.emit(self._local_stroke_id, nx, ny)
                if self._local_stroke_id:
                    self.local_stroke_end.emit(self._local_stroke_id)
                if self._current_stroke and self._current_stroke["points"]:
                    self._push_restore_undo()
                    self.items.append(self._current_stroke)
                    self._cache_dirty = True
                self._current_stroke = None
                self._local_stroke_id = None
            self.update()

    def _item_width(self, item):
        return float(item.get("width", PEN_WIDTH))

    def _shape_from_modifiers(self):
        self._sync_shape_hold_keys()
        return shape_from_holds(
            circle_tracked=bool(self._q_key_down),
            triangle_tracked=bool(self._caps_held),
        )

    def _begin_shape(self, pos, shape):
        color = QColor(self.pen_color)
        width = self.pen_width
        if shape == "line":
            return {"type": "line", "color": color, "width": width, "p1": pos, "p2": pos}
        if shape == "arrow":
            return {"type": "arrow", "color": color, "width": width, "p1": pos, "p2": pos}
        if shape == "rect":
            item = {
                "type": "rect",
                "color": color,
                "width": width,
                "rect": _drag_rect(pos, pos),
            }
            item["_origin"] = pos
            return item
        if shape == "circle":
            center, radius = _circle_from_drag(pos, pos)
            return {
                "type": "circle",
                "color": color,
                "width": width,
                "center": center,
                "radius": radius,
                "_origin": pos,
            }
        if shape == "triangle":
            return {
                "type": "triangle",
                "color": color,
                "width": width,
                "points": _triangle_from_rect(QRectF(pos, pos)),
                "_origin": pos,
            }
        return None

    def _update_shape(self, pos):
        current = self._current_shape
        if current is None:
            return
        kind = current["type"]
        if kind in ("line", "arrow"):
            current["p2"] = pos
        elif kind in ("square", "rect"):
            current["rect"] = _drag_rect(current["_origin"], pos)
        elif kind == "circle":
            center, radius = _circle_from_drag(current["_origin"], pos)
            current["center"] = center
            current["radius"] = radius
        elif kind == "triangle":
            current["points"] = _triangle_from_rect(QRectF(current["_origin"], pos))

    def _shape_is_valid(self, item):
        kind = item.get("type")
        if kind == "circle":
            radius = float(item.get("radius", 0))
            if radius < 4:
                item["radius"] = MIN_CIRCLE_RADIUS
            return True
        if kind in ("square", "rect"):
            rect = item.get("rect")
            if rect is None or min(rect.width(), rect.height()) < 4:
                return False
        if kind in ("line", "arrow"):
            if math.hypot(item["p2"].x() - item["p1"].x(), item["p2"].y() - item["p1"].y()) < 6:
                return False
        if kind == "triangle":
            pts = item.get("points") or []
            if len(pts) < 3:
                return False
            if (pts[0].x() - pts[1].x()) ** 2 + (pts[0].y() - pts[1].y()) ** 2 < 36:
                return False
        return True

    def _shape_as_stroke_points(self, item):
        kind = item.get("type")
        if kind == "line":
            return [item["p1"], item["p2"]]
        if kind == "arrow":
            pts = [item["p1"], item["p2"]]
            head = list(_arrow_head(item["p1"], item["p2"]))
            if len(head) >= 3:
                pts.extend([head[0], head[1], head[0], head[2]])
            return pts
        if kind in ("rect", "square"):
            rect = QRectF(item["rect"])
            return [
                rect.topLeft(),
                rect.topRight(),
                rect.bottomRight(),
                rect.bottomLeft(),
                rect.topLeft(),
            ]
        if kind == "circle":
            center = item["center"]
            radius = float(item["radius"])
            count = max(32, int(radius * 0.8))
            return [
                QPointF(
                    center.x() + radius * math.cos(2.0 * math.pi * i / count),
                    center.y() + radius * math.sin(2.0 * math.pi * i / count),
                )
                for i in range(count + 1)
            ]
        if kind == "triangle":
            pts = list(item.get("points") or [])
            if pts:
                pts.append(pts[0])
            return pts
        return None

    def _vector_bounds(self, item):
        pad = self._item_width(item) + 4.0
        kind = item["type"]
        xs, ys = [], []

        def add_point(point):
            xs.append(float(point.x()))
            ys.append(float(point.y()))

        if kind in ("line", "arrow"):
            add_point(item["p1"])
            add_point(item["p2"])
            if kind == "arrow":
                for point in _arrow_head(item["p1"], item["p2"]):
                    add_point(point)
        elif kind in ("rect", "square"):
            rect = QRectF(item["rect"])
            add_point(rect.topLeft())
            add_point(rect.bottomRight())
        elif kind == "circle":
            center = item["center"]
            radius = float(item["radius"])
            add_point(QPointF(center.x() - radius, center.y() - radius))
            add_point(QPointF(center.x() + radius, center.y() + radius))
        elif kind == "triangle":
            for point in item.get("points") or []:
                add_point(point)
        elif kind == "text":
            rect = QRectF(self._text_rect(item))
            return rect.adjusted(-2, -2, 2, 2)
        elif kind == "stroke":
            for point in item.get("points") or []:
                add_point(point)
        else:
            return None
        if not xs:
            return None
        return QRectF(
            min(xs) - pad,
            min(ys) - pad,
            max(xs) - min(xs) + 2 * pad,
            max(ys) - min(ys) + 2 * pad,
        )

    def _bake_item_to_image(self, item):
        bounds = self._vector_bounds(item)
        if bounds is None or bounds.width() < 1 or bounds.height() < 1:
            return None
        rect = bounds.toAlignedRect()
        pixmap = QPixmap(max(1, rect.width()), max(1, rect.height()))
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self._configure_painter(painter)
        painter.translate(-rect.x(), -rect.y())
        self._draw_item(painter, item)
        painter.end()
        return {
            "type": "image",
            "id": str(uuid.uuid4()),
            "pixmap": pixmap,
            "pos": QPoint(rect.topLeft()),
            "scale": 1.0,
            "lifted": True,
        }

    def _clip_stroke_outside_circle(self, points, center, radius):
        if not points:
            return []
        fragments = []
        current = []

        def push(point):
            point = _as_pointf(point)
            if not current or current[-1].x() != point.x() or current[-1].y() != point.y():
                current.append(point)

        def close_frag():
            if current:
                fragments.append(list(current))
                current.clear()

        def inside(point):
            dx = float(point.x()) - float(center.x())
            dy = float(point.y()) - float(center.y())
            return dx * dx + dy * dy <= radius * radius

        def point_at(p1, p2, t):
            return QPointF(
                float(p1.x()) + t * (float(p2.x()) - float(p1.x())),
                float(p1.y()) + t * (float(p2.y()) - float(p1.y())),
            )

        def intersections(p1, p2):
            ax, ay = float(p1.x()), float(p1.y())
            dx = float(p2.x()) - ax
            dy = float(p2.y()) - ay
            fx = ax - float(center.x())
            fy = ay - float(center.y())
            a = dx * dx + dy * dy
            b = 2.0 * (fx * dx + fy * dy)
            c = fx * fx + fy * fy - radius * radius
            if a < 1e-12:
                return []
            disc = b * b - 4.0 * a * c
            if disc < 0:
                return []
            disc = math.sqrt(disc)
            hits = []
            for t in ((-b - disc) / (2.0 * a), (-b + disc) / (2.0 * a)):
                if 0.0 < t < 1.0:
                    hits.append(t)
            hits.sort()
            return hits

        if not inside(points[0]):
            push(points[0])

        for i in range(1, len(points)):
            a, bpt = points[i - 1], points[i]
            in_a, in_b = inside(a), inside(bpt)
            hits = intersections(a, bpt)
            if in_a and in_b:
                continue
            if (not in_a) and (not in_b):
                if len(hits) >= 2:
                    push(point_at(a, bpt, hits[0]))
                    close_frag()
                    push(point_at(a, bpt, hits[-1]))
                    push(bpt)
                else:
                    push(bpt)
                continue
            if not hits:
                if not in_b:
                    push(bpt)
                continue
            hit = point_at(a, bpt, hits[0])
            if in_a and not in_b:
                push(hit)
                push(bpt)
            else:
                push(hit)
                close_frag()
        close_frag()
        return fragments

    def _copy_stroke(self, item, points):
        return {
            "type": "stroke",
            "author": item.get("author", "host"),
            "color": QColor(item["color"]),
            "width": self._item_width(item),
            "points": points,
        }

    def _flush_erase_rasters(self):
        for item in self.items:
            raster = item.pop("_erase_raster", None)
            if raster is not None:
                item["pixmap"] = QPixmap.fromImage(raster)

    def _paint_erase_capsule(self, painter, p1, p2, radius, clear=False):
        p1 = _as_pointf(p1)
        p2 = _as_pointf(p2)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if clear:
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            color = QColor(0, 0, 0, 0)
        else:
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            color = QColor(255, 255, 255, 255)
        dist = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
        if dist < 0.5:
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(p1, radius, radius)
            return
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, radius * 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(p1, p2)

    def _punch_erase_on_image_item(self, item, p1, p2, radius):
        img_rect = QRectF(self._image_rect(item))
        cap = _capsule_bounds(p1, p2, radius)
        if not img_rect.intersects(cap):
            return False
        raster = item.get("_erase_raster")
        if raster is None:
            pixmap = item["pixmap"]
            if pixmap.isNull():
                return False
            raster = pixmap.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
            item["_erase_raster"] = raster
        scale = float(item["scale"] if item["scale"] else 1.0) or 1.0
        ox = float(item["pos"].x())
        oy = float(item["pos"].y())

        def to_img(point):
            return QPointF((float(point.x()) - ox) / scale, (float(point.y()) - oy) / scale)

        painter = QPainter(raster)
        if not painter.isActive():
            return False
        self._paint_erase_capsule(painter, to_img(p1), to_img(p2), float(radius) / scale, clear=True)
        painter.end()
        return True

    def _stamp_erase_cache_segment(self, p1, p2, radius):
        if self._static_cache is None or self._cache_dirty:
            self._rebuild_cache()
        if self._static_cache is None:
            return
        painter = QPainter(self._static_cache)
        painter.scale(self._cache_dpr, self._cache_dpr)
        self._paint_erase_capsule(painter, p1, p2, radius, clear=False)
        painter.end()

    def _erase_along(self, start, end):
        if self.eraser_mode == ERASER_PIXEL:
            self._erase_pixels_segment(start, end)
            return
        x1, y1 = float(start.x()), float(start.y())
        x2, y2 = float(end.x()), float(end.y())
        dist = math.hypot(x2 - x1, y2 - y1)
        step = max(MIN_ERASE_STEP, float(self.eraser_width) * 0.28)
        if dist < 0.8:
            return
        steps = max(1, int(math.ceil(dist / step)))
        for i in range(1, steps + 1):
            t = i / float(steps)
            self._erase_at(QPointF(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))

    def _erase_at(self, canvas_pos, mode=None):
        if mode is None:
            mode = self.eraser_mode
        if mode == ERASER_PIXEL:
            self._erase_pixels_segment(canvas_pos, canvas_pos)
        else:
            self._erase_object_at(canvas_pos)

    def _erase_object_at(self, canvas_pos):
        radius = float(self.eraser_width)
        changed = False
        remaining = []
        hit_pos = _as_point(canvas_pos)

        for item in self.items:
            kind = item["type"]
            if kind == "image":
                rect = self._image_rect(item).adjusted(-int(radius), -int(radius), int(radius), int(radius))
                if rect.contains(hit_pos):
                    changed = True
                    if item is self.selected_item:
                        self.selected_item = None
                    continue
            elif kind == "text":
                rect = self._text_rect(item).adjusted(-int(radius), -int(radius), int(radius), int(radius))
                if rect.contains(hit_pos):
                    changed = True
                    if item is self.selected_item:
                        self.selected_item = None
                    continue
            elif kind == "stroke":
                if _item_hit(item, canvas_pos, radius + self._item_width(item) * 0.5):
                    changed = True
                    continue
            elif _item_hit(item, canvas_pos, radius + self._item_width(item) * 0.5):
                changed = True
                continue
            remaining.append(item)

        live_remaining = {}
        for stroke_id, stroke in self._remote_strokes.items():
            if _item_hit(stroke, canvas_pos, radius + self._item_width(stroke) * 0.5):
                changed = True
                continue
            live_remaining[stroke_id] = stroke
        if live_remaining != self._remote_strokes:
            self._remote_strokes = live_remaining

        if changed:
            self._erase_changed = True
            self.items = remaining
            self._cache_dirty = True
            self.update()
            if self._erase_snapshot is None:
                self.content_changed.emit()

    def _erase_pixels_segment(self, start, end):
        radius = float(self.eraser_width)
        cap = _capsule_bounds(start, end, radius)
        changed = False
        remaining = []

        def bake_if_hit(item):
            nonlocal changed
            bounds = self._vector_bounds(item)
            if bounds is None:
                remaining.append(item)
                return
            pad = radius + self._item_width(item) * 0.5
            if not bounds.adjusted(-pad, -pad, pad, pad).intersects(cap):
                remaining.append(item)
                return
            baked = self._bake_item_to_image(item)
            if baked is None:
                changed = True
                return
            self._punch_erase_on_image_item(baked, start, end, radius)
            remaining.append(baked)
            changed = True
            if item is self.selected_item:
                self.selected_item = baked

        for item in self.items:
            kind = item["type"]
            if kind == "image":
                if self._punch_erase_on_image_item(item, start, end, radius):
                    changed = True
                remaining.append(item)
            elif kind in VECTOR_ERASE_TYPES:
                bake_if_hit(item)
            else:
                remaining.append(item)

        live_remaining = {}
        for stroke_id, stroke in self._remote_strokes.items():
            bounds = self._vector_bounds(stroke)
            pad = radius + self._item_width(stroke) * 0.5
            if bounds is None or not bounds.adjusted(-pad, -pad, pad, pad).intersects(cap):
                live_remaining[stroke_id] = stroke
                continue
            baked = self._bake_item_to_image(stroke)
            if baked is not None:
                self._punch_erase_on_image_item(baked, start, end, radius)
                remaining.append(baked)
            changed = True
        if live_remaining != self._remote_strokes:
            self._remote_strokes = live_remaining

        if changed:
            self._erase_changed = True
            self.items = remaining
            self._stamp_erase_cache_segment(start, end, radius)
            self.update()

    def _stroke_pen(self, color, width=None):
        pen = QPen(color, float(PEN_WIDTH if width is None else width), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        pen.setCosmetic(False)
        return pen

    def _draw_stroke(self, painter, stroke):
        points = stroke["points"]
        if not points:
            return
        painter.setPen(self._stroke_pen(stroke["color"], self._item_width(stroke)))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(_stroke_painter_path(points))

    def _draw_item(self, painter, item):
        kind = item["type"]
        if kind == "image":
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            target = QRectF(self._image_rect(item))
            source = QRectF(item["pixmap"].rect())
            painter.drawPixmap(target, item["pixmap"], source)
            return
        if kind == "stroke":
            self._draw_stroke(painter, item)
            return
        if kind == "text":
            self._draw_text_block(painter, item["pos"], item["text"], item["color"])
            return
        color = item.get("color", QColor(COLOR_BLACK))
        painter.setPen(self._stroke_pen(color, self._item_width(item)))
        painter.setBrush(Qt.NoBrush)
        if kind == "line":
            painter.drawLine(item["p1"], item["p2"])
        elif kind == "arrow":
            painter.drawLine(item["p1"], item["p2"])
            head = _arrow_head(item["p1"], item["p2"])
            if not head.isEmpty():
                painter.setBrush(color)
                painter.setPen(Qt.NoPen)
                painter.drawPolygon(head)
        elif kind in ("square", "rect"):
            painter.drawRect(item["rect"])
        elif kind == "circle":
            center = item["center"]
            radius = item["radius"]
            painter.drawEllipse(center, radius, radius)
        elif kind == "triangle":
            painter.drawPolygon(QPolygonF(item["points"]))

    def _draw_eraser_preview(self, painter):
        if self.tool != TOOL_ERASER or self._eraser_pos is None:
            return
        radius = float(self.eraser_width)
        painter.setPen(QPen(QColor(80, 80, 80, 200), 1.2))
        painter.setBrush(QColor(200, 200, 200, 50))
        painter.drawEllipse(self._eraser_pos, radius, radius)

    def _rebuild_cache(self):
        self._flush_erase_rasters()
        dpr = self._dpr()
        width = max(1, int(round(self._canvas_w * dpr)))
        height = max(1, int(round(self._canvas_h * dpr)))
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(255, 255, 255))
        painter = QPainter(image)
        self._configure_painter(painter)
        painter.scale(dpr, dpr)
        for item in self.items:
            if item is self._live_item:
                continue
            self._draw_item(painter, item)
        painter.end()
        self._static_cache = QPixmap.fromImage(image)
        self._cache_dpr = dpr
        self._cache_dirty = False

    def _draw_selection(self, painter):
        if self.selected_item is None or self.selected_item["type"] not in ("image", "text"):
            return
        rect = self._item_rect(self.selected_item)
        pen = QPen(QColor(70, 130, 255), 2, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        if self.selected_item["type"] == "image" and self.tool == TOOL_HAND:
            handle = self._resize_handle_rect(self.selected_item)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(70, 130, 255))
            painter.drawRect(handle)

    def _draw_region(self, painter):
        rect = self._marquee_rect()
        if rect is None or rect.isEmpty():
            return
        painter.setPen(QPen(QColor(70, 130, 255), 2, Qt.DashLine))
        painter.setBrush(QColor(70, 130, 255, 45))
        painter.drawRect(rect)

    def paintEvent(self, _event):
        if self._static_cache is None or self._cache_dirty:
            self._rebuild_cache()

        painter = QPainter(self)
        self._configure_painter(painter)
        painter.fillRect(self.rect(), Qt.white)
        dpr = self._cache_dpr if self._static_cache is not None else 1.0
        source = QRectF(
            float(self.offset.x()) * dpr,
            float(self.offset.y()) * dpr,
            float(self.width()) * dpr,
            float(self.height()) * dpr,
        )
        painter.drawPixmap(QRectF(self.rect()), self._static_cache, source)

        painter.translate(-self.offset.x(), -self.offset.y())
        if self._live_item is not None:
            self._draw_item(painter, self._live_item)
        if self._current_stroke is not None:
            self._draw_stroke(painter, self._current_stroke)
        if self._current_shape is not None:
            self._draw_item(painter, self._current_shape)
        for stroke in self._remote_strokes.values():
            self._draw_stroke(painter, stroke)
        self._draw_selection(painter)
        self._draw_region(painter)
        self._draw_eraser_preview(painter)


class FreezeMirror(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lenta (užšaldyta)")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._pixmap = None

    def set_snapshot(self, pixmap, geometry, mask):
        self._pixmap = pixmap
        self.setGeometry(geometry)
        if mask is not None and not mask.isEmpty():
            self.setMask(mask)
        else:
            self.clearMask()
        self.update()

    def pixmap(self):
        return self._pixmap

    def clear_snapshot(self):
        self._pixmap = None

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), Qt.white)
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.drawPixmap(self.rect(), self._pixmap)


class PauseBanner(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("Vaizdas pristabdytas — Discord / Teams šio piešimo nemato")
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QLabel { background: rgba(183, 28, 28, 210); color: white; "
            "font-size: 14px; font-weight: 600; padding: 8px 16px; border-radius: 8px; }"
        )
        self.hide()


class RestoreChip(QFrame):
    restore_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lenta")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setStyleSheet(
            "QFrame { background: #1f6feb; border: 1px solid #1558c0; border-radius: 8px; }"
            "QPushButton { background: transparent; color: white; border: none; font-size: 14px; padding: 8px 16px; }"
            "QPushButton:hover { background: #1558c0; border-radius: 8px; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("Rodyti lentą")
        button.clicked.connect(self.restore_requested.emit)
        layout.addWidget(button)
        self.adjustSize()


def _status_badge_pixmap(kind, size=108):
    app = QApplication.instance()
    dpr = float(app.devicePixelRatio()) if app is not None else 1.0
    pixel = max(1, int(round(size * dpr)))
    pixmap = QPixmap(pixel, pixel)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.scale(dpr, dpr)
    inset = QRectF(4, 4, size - 8, size - 8)
    if kind == "on":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(220, 252, 231))
        painter.drawEllipse(QRectF(0, 0, size, size))
        painter.setBrush(QColor(22, 163, 74))
        painter.drawEllipse(inset)
        pen = QPen(QColor(255, 255, 255), max(6.0, size * 0.09), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        check = QPainterPath()
        check.moveTo(size * 0.30, size * 0.53)
        check.lineTo(size * 0.44, size * 0.68)
        check.lineTo(size * 0.72, size * 0.34)
        painter.drawPath(check)
    else:
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(241, 245, 249))
        painter.drawEllipse(QRectF(0, 0, size, size))
        painter.setBrush(QColor(71, 85, 105))
        painter.drawEllipse(inset)
        pen = QPen(QColor(255, 255, 255), max(6.0, size * 0.085), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        cx = size / 2.0
        painter.drawLine(QPointF(cx, size * 0.26), QPointF(cx, size * 0.48))
        arc = QRectF(size * 0.30, size * 0.32, size * 0.40, size * 0.40)
        painter.drawArc(arc, 50 * 16, 260 * 16)
    painter.end()
    return pixmap


class _ClickableRow(QFrame):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("shortcutRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _keycap_label(text):
    cap = QLabel(text)
    cap.setObjectName("keycap")
    cap.setAlignment(Qt.AlignCenter)
    cap.setMinimumWidth(42)
    return cap


def _shortcut_row(tokens, description, on_click=None):
    clickable = on_click is not None
    row = _ClickableRow() if clickable else QFrame()
    row.setObjectName("shortcutRow")
    if clickable:
        row.clicked.connect(on_click)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(16, 12, 18, 12)
    layout.setSpacing(8)
    for index, token in enumerate(tokens):
        if index:
            plus = QLabel("+")
            plus.setObjectName("keyPlus")
            plus.setAlignment(Qt.AlignCenter)
            plus.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(plus)
        cap = _keycap_label(token)
        cap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(cap)
    layout.addSpacing(10)
    text = QLabel(description)
    text.setObjectName("shortcutDesc")
    text.setWordWrap(True)
    text.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    layout.addWidget(text, 1)
    if clickable:
        hint = QLabel("Atidaryti")
        hint.setObjectName("shortcutHint")
        hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(hint)
    return row


def _combo_tokens(action_id, fallback):
    text = format_action(action_id) or fallback
    parts = [part.strip() for part in text.split("+") if part.strip()]
    return parts or [fallback]


class StatusToast(QWidget):
    closed = pyqtSignal()
    open_overlay = pyqtSignal()
    open_whiteboard = pyqtSignal()

    def __init__(self, kind):
        super().__init__()
        active = kind == "on"
        self.setWindowTitle("Ekrano rašiklis")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        font = QFont("Segoe UI")
        if not font.exactMatch():
            font = QFont("Arial")
        self.setFont(font)
        self.setStyleSheet(
            "QFrame#card { background: #ffffff; border: 1px solid #e6ebf2; border-radius: 24px; }"
            "QLabel#eyebrow { font-size: 13px; font-weight: 600; color: #64748b; letter-spacing: 0.4px; }"
            "QLabel#title { font-size: 28px; font-weight: 800; color: #0f172a; }"
            "QLabel#body { font-size: 16px; color: #475569; }"
            "QFrame#shortcutRow { background: #f8fafc; border: 1px solid #e8eef5; border-radius: 14px; }"
            "QFrame#shortcutRow:hover { background: #eef6ff; border: 1px solid #c7d7ea; }"
            "QLabel#keycap { background: #ffffff; border: 1px solid #d8e0ea; border-radius: 10px; "
            "padding: 7px 12px; font-size: 16px; font-weight: 700; color: #0f172a; min-height: 28px; }"
            "QLabel#keyPlus { font-size: 15px; font-weight: 700; color: #94a3b8; padding: 0 2px; }"
            "QLabel#shortcutDesc { font-size: 15px; color: #334155; }"
            "QLabel#shortcutHint { font-size: 13px; font-weight: 700; color: #16a34a; padding-left: 8px; }"
            "QPushButton#ok { background: %s; color: white; border: none; border-radius: 12px; "
            "padding: 12px 22px; font-size: 15px; font-weight: 700; min-width: 108px; }"
            "QPushButton#ok:hover { background: %s; }"
            % (("#16a34a", "#15803d") if active else ("#334155", "#1e293b"))
        )

        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(28, 22, 28, 30)
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(560)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(15, 23, 42, 48))
        card.setGraphicsEffect(shadow)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(36, 32, 36, 28)
        inner.setSpacing(0)

        badge = QLabel()
        badge.setAlignment(Qt.AlignCenter)
        badge.setPixmap(_status_badge_pixmap("on" if active else "off"))
        inner.addWidget(badge)
        inner.addSpacing(18)

        eyebrow = QLabel("EKRANO RAŠIKLIS")
        eyebrow.setObjectName("eyebrow")
        eyebrow.setAlignment(Qt.AlignCenter)
        inner.addWidget(eyebrow)
        inner.addSpacing(6)

        heading = QLabel("Aktyvuota" if active else "Išjungta")
        heading.setObjectName("title")
        heading.setAlignment(Qt.AlignCenter)
        inner.addWidget(heading)
        inner.addSpacing(8)

        body = QLabel(
            "Programa veikia fone. Dabar galite įjungti režimą; vėliau naudokite sparčiuosius klavišus."
            if active
            else "Išjungdami taupote kompiuterio resursus."
        )
        body.setObjectName("body")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        inner.addWidget(body)

        if active:
            inner.addSpacing(22)
            inner.addWidget(
                _shortcut_row(
                    _combo_tokens("open_overlay", "* + /"),
                    "Rašyti ant ekrano",
                    self._launch_overlay,
                )
            )
            inner.addSpacing(10)
            inner.addWidget(
                _shortcut_row(
                    _combo_tokens("open_whiteboard", "* + -"),
                    "Baltos lentos režimas",
                    self._launch_whiteboard,
                )
            )
        inner.addSpacing(26)

        ok = QPushButton("Gerai")
        ok.setObjectName("ok")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setFocusPolicy(Qt.NoFocus)
        ok.clicked.connect(self.close)
        inner.addWidget(ok, 0, Qt.AlignHCenter)

        wrap.addWidget(card)
        self.adjustSize()
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 800, 600)
        self.move(
            area.center().x() - self.width() // 2,
            area.center().y() - self.height() // 2,
        )
        if not active:
            QTimer.singleShot(10000, self.close)

    def _launch_overlay(self):
        self.hide()
        self.open_overlay.emit()
        self.close()

    def _launch_whiteboard(self):
        self.hide()
        self.open_whiteboard.emit()
        self.close()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


def _app_dir():
    return project_dir()


def _launcher_vbs_path():
    return os.path.join(_app_dir(), "ekrano_rasiklis.vbs")


def _app_icon_path():
    return os.path.join(data_dir(), "ekrano_rasiklis.ico")


def _stable_icon_path():
    root = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "WhiteboardTool",
    )
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "ekrano_rasiklis.ico")


def _pythonw_executable():
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.isfile(pyw):
            return pyw
    if exe.lower().endswith("pythonw.exe") and os.path.isfile(exe):
        return exe
    local = os.environ.get("LOCALAPPDATA", "")
    for version in ("Python313", "Python312", "Python311", "Python310"):
        candidate = os.path.join(local, "Programs", "Python", version, "pythonw.exe")
        if os.path.isfile(candidate):
            return candidate
    return "pythonw.exe"


def _desktop_dir():
    buf = ctypes.create_unicode_buffer(260)
    try:
        if ctypes.windll.shell32.SHGetFolderPathW(
            None, CSIDL_DESKTOPDIRECTORY, None, 0, buf
        ) == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _pixmap_to_dib(pixmap):
    image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    width = image.width()
    height = image.height()
    xor = bytearray()
    for y in range(height - 1, -1, -1):
        for x in range(width):
            color = image.pixelColor(x, y)
            xor += bytes(
                (
                    color.blue() & 0xFF,
                    color.green() & 0xFF,
                    color.red() & 0xFF,
                    color.alpha() & 0xFF,
                )
            )
    row_bytes = ((width + 31) // 32) * 4
    mask = bytearray()
    for y in range(height - 1, -1, -1):
        row = bytearray(row_bytes)
        for x in range(width):
            if image.pixelColor(x, y).alpha() < 16:
                row[x // 8] |= 0x80 >> (x % 8)
        mask += row
    header = struct.pack(
        "<IIIHHIIIIII",
        40,
        width,
        height * 2,
        1,
        32,
        0,
        len(xor),
        0,
        0,
        0,
        0,
    )
    return header + xor + mask


def _pixmap_to_png(pixmap):
    blob = QByteArray()
    buffer = QBuffer(blob)
    buffer.open(QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(blob)


def _write_ico(path, pixmaps):
    entries = []
    for pixmap in pixmaps:
        if pixmap.width() >= 256:
            payload = _pixmap_to_png(pixmap)
        else:
            payload = _pixmap_to_dib(pixmap)
        entries.append((pixmap.width(), pixmap.height(), payload))
    offset = 6 + 16 * len(entries)
    header = bytearray()
    header += (0).to_bytes(2, "little")
    header += (1).to_bytes(2, "little")
    header += len(entries).to_bytes(2, "little")
    data = bytearray()
    for width, height, payload in entries:
        header += bytes(
            [
                0 if width >= 256 else width & 0xFF,
                0 if height >= 256 else height & 0xFF,
                0,
                0,
            ]
        )
        header += (1).to_bytes(2, "little")
        header += (32).to_bytes(2, "little")
        header += len(payload).to_bytes(4, "little")
        header += offset.to_bytes(4, "little")
        data += payload
        offset += len(payload)
    with open(path, "wb") as handle:
        handle.write(header + data)


def _refresh_shell_icons():
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)
    except Exception:
        pass


def _ensure_app_icon():
    pixmaps = [_make_icon(_icon_launcher, size) for size in (16, 32, 48, 256)]
    project_path = _app_icon_path()
    stable_path = _stable_icon_path()
    written = ""
    try:
        _write_ico(project_path, pixmaps)
        written = project_path
        if os.path.abspath(stable_path) != os.path.abspath(project_path):
            _write_ico(stable_path, pixmaps)
            written = stable_path
    except OSError:
        try:
            _write_ico(stable_path, pixmaps)
            written = stable_path
        except OSError:
            written = ""
    icon = QIcon()
    for pixmap in pixmaps:
        icon.addPixmap(pixmap)
    return icon, written


def _ensure_launcher_vbs():
    path = _launcher_vbs_path()
    pyw = _pythonw_executable().replace('"', '""')
    contents = (
        'Set sh = CreateObject("WScript.Shell")\n'
        'Set fso = CreateObject("Scripting.FileSystemObject")\n'
        'dir = fso.GetParentFolderName(WScript.ScriptFullName)\n'
        'script = dir & "\\whiteboard_tool.py"\n'
        'pyw = "%s"\n'
        'If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"\n'
        'sh.Run """" & pyw & """ """ & script & """", 0, False\n'
    ) % pyw
    with open(path, "w", encoding="ascii", newline="\r\n") as handle:
        handle.write(contents)
    return path


def _ensure_desktop_shortcut(icon_path, force=False):
    if is_frozen():
        return
    desktop = _desktop_dir()
    if not os.path.isdir(desktop):
        return
    lnk = os.path.join(desktop, APP_SHORTCUT_NAME)
    if not force and os.path.isfile(lnk) and os.path.getsize(lnk) > 200:
        return
    vbs = _ensure_launcher_vbs()
    wscript = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
    tmp_lnk = lnk + ".tmp.lnk"
    ps1 = os.path.join(data_dir(), "_make_shortcut.ps1")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(%s)\n"
        "$s.TargetPath = %s\n"
        "$s.Arguments = %s\n"
        "$s.WorkingDirectory = %s\n"
        "$s.WindowStyle = 7\n"
        "$s.Description = %s\n"
        "$s.IconLocation = %s\n"
        "$s.Save()\n"
        "if (-not (Test-Path -LiteralPath %s) -or (Get-Item -LiteralPath %s).Length -le 0) {\n"
        "  throw 'shortcut save produced an empty file'\n"
        "}\n"
        "Move-Item -LiteralPath %s -Destination %s -Force\n"
    ) % (
        _ps_quote(tmp_lnk),
        _ps_quote(wscript),
        _ps_quote('"%s"' % vbs),
        _ps_quote(_app_dir()),
        _ps_quote("Ekrano rašiklis — įjungti / išjungti"),
        _ps_quote(icon_path or ""),
        _ps_quote(tmp_lnk),
        _ps_quote(tmp_lnk),
        _ps_quote(tmp_lnk),
        _ps_quote(lnk),
    )
    try:
        with open(ps1, "w", encoding="utf-8-sig") as handle:
            handle.write(script)
        subprocess.check_call(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File",
                ps1,
            ],
            creationflags=CREATE_NO_WINDOW,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _refresh_shell_icons()
    except Exception:
        try:
            if os.path.isfile(tmp_lnk):
                os.remove(tmp_lnk)
        except OSError:
            pass
    finally:
        try:
            os.remove(ps1)
        except OSError:
            pass


def _other_whiteboard_pids():
    # Frozen onefile starts a parent+child with the same .exe name; scanning
    # those PIDs would treat the bootloader as a second instance and kill both.
    if is_frozen():
        return []
    me = os.getpid()
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -like '*whiteboard_tool.py*' -and $_.ProcessId -ne %d } | "
        "Select-Object -ExpandProperty ProcessId" % me
    )
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            creationflags=CREATE_NO_WINDOW,
            timeout=6,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []
    pids = []
    for line in output.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.isdigit():
            pid = int(line)
            if pid != me:
                pids.append(pid)
    return pids


def _mutex_held():
    handle = _kernel32.OpenMutexW(SYNCHRONIZE, False, APP_MUTEX_NAME)
    if handle:
        _kernel32.CloseHandle(handle)
        return True
    return False


def _take_instance_mutex():
    _kernel32.SetLastError(0)
    handle = _kernel32.CreateMutexW(None, True, APP_MUTEX_NAME)
    if not handle:
        return None
    if _kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


def _kill_pids(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue


def _notify_running_instance_to_quit():
    socket = QLocalSocket()
    socket.connectToServer(APP_INSTANCE_KEY)
    if not socket.waitForConnected(400):
        return False
    socket.write(b"quit")
    socket.waitForBytesWritten(400)
    socket.waitForReadyRead(800)
    socket.disconnectFromServer()
    return True


def _toggle_off_existing():
    running = _mutex_held() or bool(_other_whiteboard_pids())
    if not running:
        QLocalServer.removeServer(APP_INSTANCE_KEY)
        return False
    _notify_running_instance_to_quit()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _mutex_held() and not _other_whiteboard_pids():
            QLocalServer.removeServer(APP_INSTANCE_KEY)
            return True
        time.sleep(0.08)
    pids = _other_whiteboard_pids()
    if pids:
        _kill_pids(pids)
    QLocalServer.removeServer(APP_INSTANCE_KEY)
    return True


def _show_status_toast(kind, quit_on_close=False):
    toast = StatusToast(kind)
    app = QApplication.instance()
    if app is not None:
        app._status_toast = toast
        if quit_on_close:
            toast.closed.connect(app.quit)
    toast.show()
    toast.raise_()
    toast.activateWindow()
    return toast


class WhiteboardWindow(QWidget):
    closed = pyqtSignal()
    collab_stopped = pyqtSignal()

    def __init__(self, app_collab):
        super().__init__()
        self.setWindowTitle("Whiteboard")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self._capture_paused = False
        self._is_minimized = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.toolbar = Toolbar(self)
        self.collab_bar = CollabBar(self)
        self.collab_bar.hide()
        self.canvas = CanvasWidget(self)
        root.addWidget(self.toolbar)
        root.addWidget(self.collab_bar)
        root.addWidget(self.canvas, 1)

        self.toolbar.tool_selected.connect(self._on_tool_selected)
        self.toolbar.eraser_mode_selected.connect(self._on_eraser_mode)
        self.toolbar.sticker_requested.connect(
            lambda: self._open_sticker_dialog(
                stickers_dir(), "Pasirinkite figūrą", max_size=FIGURE_MAX_SIZE
            )
        )
        self.toolbar.funkcijos_requested.connect(
            lambda: self._open_sticker_dialog(funkcijos_dir(), "Pasirinkite funkciją")
        )
        self.toolbar.collab_toggled.connect(self._on_collab_toggled)
        self.toolbar.capture_pause_toggled.connect(self._on_capture_pause_toggled)
        self.toolbar.minimize_requested.connect(self.minimize_whiteboard)
        self.toolbar.close_requested.connect(self.close)
        self.toolbar.clear_all_requested.connect(self._on_clear_all)
        self.toolbar.cheat_sheet_requested.connect(self._open_cheat_sheet)
        self.collab_bar.allow_toggled.connect(self._on_allow_guest)
        self.collab_bar.open_local_requested.connect(self._open_local_link)

        self.collab_bridge = app_collab["bridge"]
        self._app_collab = app_collab
        self.collab_bridge.guest_count_changed.connect(self.collab_bar.set_guest_count)
        self.collab_bridge.remote_stroke_start.connect(self.canvas.begin_remote_stroke)
        self.collab_bridge.remote_stroke_point.connect(self.canvas.add_remote_point)
        self.collab_bridge.remote_stroke_points.connect(self.canvas.add_remote_points)
        self.collab_bridge.remote_stroke_end.connect(self.canvas.end_remote_stroke)
        self.collab_bridge.remote_erase.connect(self.canvas.erase_remote)
        self.collab_bridge.remote_undo.connect(self.canvas.undo_guest)
        self.collab_bridge.urls_changed.connect(self.collab_bar.set_urls)
        self.collab_bridge.status_changed.connect(self.collab_bar.set_status)
        self.canvas.local_stroke_start.connect(self._bcast_stroke_start)
        self.canvas.local_stroke_point.connect(self._bcast_stroke_point)
        self.canvas.local_stroke_end.connect(self._bcast_stroke_end)
        self.canvas.view_changed.connect(self._bcast_view)
        self.canvas.content_changed.connect(self._bcast_state)

        self._collab = app_collab.get("session")
        self._last_view_bcast = 0.0
        self._cached_state = {}
        self._pending_bcast = {}
        self._bcast_timer = QTimer(self)
        self._bcast_timer.setSingleShot(True)
        self._bcast_timer.setInterval(16)
        self._bcast_timer.timeout.connect(self._flush_bcast)
        self._freeze_window = FreezeMirror()
        self._freeze_window.hide()
        self._pause_banner = PauseBanner(self)
        self._restore_chip = RestoreChip()
        self._restore_chip.restore_requested.connect(self.restore_whiteboard)
        self._restore_chip.hide()
        self._discord_timer = QTimer(self)
        self._discord_timer.setInterval(400)
        self._discord_timer.timeout.connect(self._keep_discord_on_top)
        self._yield_timer = QTimer(self)
        self._yield_timer.setInterval(100)
        self._yield_timer.timeout.connect(self._yield_to_foreground_app)
        self._yield_after = 0.0
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state_changed)

        self._cover_all_screens()
        if self._collab is not None:
            self.toolbar.collab_button.setChecked(True)
            self.collab_bar.show()
            session = self._collab
            self.collab_bar.set_urls(session.lan_url, session.public_url)
            if session.guest_count():
                self.collab_bar.set_guest_count(session.guest_count())
            self._collab.set_state_provider(app_collab["state_provider"])
        self.show()
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)
        self._force_topmost()
        QApplication.instance().installEventFilter(self)
        QTimer.singleShot(0, self._finish_open)

    def _finish_open(self):
        if self._collab is not None:
            if self._app_collab.get("allowed"):
                self.collab_bar.allow_button.setChecked(True)
                self._collab.set_allowed(True)
            self._bcast_state()

    def _cover_all_screens(self):
        screens = QApplication.screens()
        if not screens:
            return
        bounds = QRect()
        region = QRegion()
        for screen in screens:
            geo = screen.availableGeometry()
            bounds = geo if bounds.isNull() else bounds.united(geo)
            region = region.united(QRegion(geo))
        self.setGeometry(bounds)
        region.translate(-bounds.x(), -bounds.y())
        self.setMask(region)
        if self._capture_paused:
            self._sync_freeze_geometry()
            self._place_pause_banner()

    def _force_topmost(self):
        if sys.platform == "win32":
            _set_window_topmost(int(self.winId()))
        self.raise_()
        self.activateWindow()
        self._keep_discord_on_top()

    def _keep_discord_on_top(self):
        if self._is_minimized or not self.isVisible():
            return
        if self._capture_paused and self._freeze_window.isVisible() and sys.platform == "win32":
            _set_window_topmost(int(self._freeze_window.winId()), activate=False)
            _set_window_topmost(int(self.winId()), activate=False)
        _raise_discord_popouts(exclude_hwnd=int(self.winId()))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._is_minimized:
            self._force_topmost()
            self._discord_timer.start()
            self._yield_after = time.monotonic() + 0.45
            self._yield_timer.start()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if not self._is_minimized:
            QTimer.singleShot(0, self._keep_discord_on_top)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._capture_paused:
            self._place_pause_banner()

    def _on_app_state_changed(self, state):
        if state == Qt.ApplicationInactive:
            QTimer.singleShot(80, self._yield_to_foreground_app)

    def _yield_to_foreground_app(self):
        if self._is_minimized or not self.isVisible():
            return
        if sys.platform != "win32":
            return
        if time.monotonic() < self._yield_after:
            return
        if self.isActiveWindow():
            return
        app = QApplication.instance()
        if app is not None and app.activeModalWidget() is not None:
            return
        try:
            hwnd = _user32.GetForegroundWindow()
        except Exception:
            return
        if not _is_foreign_app_window(hwnd):
            return
        if _is_discord_popout(hwnd):
            return
        self.minimize_whiteboard(keep_other_app_focused=True)

    def minimize_whiteboard(self, keep_other_app_focused=False):
        self._is_minimized = True
        self._discord_timer.stop()
        self._yield_timer.stop()
        self._freeze_window.hide()
        self.hide()
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else self.geometry()
        chip = self._restore_chip
        chip.adjustSize()
        chip.move(area.right() - chip.width() - 16, area.top() + 16)
        chip.show()
        if sys.platform == "win32":
            _set_window_topmost(int(chip.winId()), activate=not keep_other_app_focused)
            _raise_discord_popouts(exclude_hwnd=int(chip.winId()))

    def restore_whiteboard(self):
        self._is_minimized = False
        self._restore_chip.hide()
        self._cover_all_screens()
        self.show()
        if self._capture_paused:
            self._show_freeze_mirror()
            self._apply_capture_exclusion(True)
            self._place_pause_banner()
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)
        self._force_topmost()

    def keyPressEvent(self, event):
        if isinstance(self.focusWidget(), (QLineEdit, QPlainTextEdit)):
            super().keyPressEvent(event)
            return
        if matches(event, "close_whiteboard"):
            self.close()
            return
        if matches(event, "tool_eraser"):
            self._on_tool_selected(TOOL_ERASER)
            return
        if matches(event, "tool_text"):
            self._on_tool_selected(TOOL_TEXT)
            return
        if matches(event, "delete"):
            if self.canvas.delete_selection():
                return
        self.canvas.keyPressEvent(event)
        if matches_any(event, ("color_black", "color_blue", "color_red", "color_green")):
            self.toolbar.set_tool(TOOL_PEN)

    def eventFilter(self, obj, event):
        etype = event.type()
        if not self._is_minimized and self.isVisible():
            focused = self.focusWidget()
            if not isinstance(focused, (QLineEdit, QPlainTextEdit)):
                if etype in (QEvent.KeyPress, QEvent.KeyRelease) and is_hold_key(event.key()):
                    if etype == QEvent.KeyRelease and event.isAutoRepeat():
                        return super().eventFilter(obj, event)
                    widget = obj if isinstance(obj, QWidget) else None
                    if widget is None or widget is self or self.isAncestorOf(widget):
                        if event.key() == hold_qt_key("hold_circle"):
                            if etype == QEvent.KeyPress:
                                self.canvas._q_key_down = True
                            else:
                                self.canvas._q_key_down = hold_down("hold_circle")
                        elif event.key() == hold_qt_key("hold_triangle"):
                            if etype == QEvent.KeyPress:
                                self.canvas._caps_held = True
                            else:
                                self.canvas._caps_held = hold_down("hold_triangle")
                        else:
                            self.canvas._sync_shape_hold_keys()
                        self.canvas.update()
                elif etype == QEvent.KeyPress and matches(event, "delete"):
                    if not isinstance(obj, (QLineEdit, QPlainTextEdit)):
                        widget = obj if isinstance(obj, QWidget) else None
                        if widget is None or widget is self or self.isAncestorOf(widget):
                            if self.canvas.delete_selection():
                                return True
        return super().eventFilter(obj, event)

    def _open_cheat_sheet(self):
        dialog = CheatSheetDialog(self)
        icon = QIcon()
        for size in (16, 20, 24, 32, 48):
            icon.addPixmap(_make_icon(_icon_notes, size))
        dialog.setWindowIcon(icon)
        dialog.exec_()
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)

    def _on_tool_selected(self, tool):
        self.toolbar.set_tool(tool)
        self.canvas.set_tool(tool)
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)

    def _on_eraser_mode(self, mode):
        self.canvas.eraser_mode = mode
        self.canvas._update_cursor()

    def _open_sticker_dialog(self, start_dir, title="Pasirinkite figūrą", max_size=None):
        if not os.path.isdir(start_dir):
            start_dir = os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            start_dir,
            "Paveikslėliai (*.png *.jpg *.jpeg *.gif *.bmp *.webp)",
        )
        if path:
            self.canvas.add_sticker(path, max_size=max_size)
            self.toolbar.set_tool(TOOL_HAND)
            self.canvas.set_tool(TOOL_HAND)
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)

    def closeEvent(self, event):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
            try:
                app.applicationStateChanged.disconnect(self._on_app_state_changed)
            except TypeError:
                pass
        self._is_minimized = False
        self._discord_timer.stop()
        self._yield_timer.stop()
        self._resume_capture(force=True)
        self._freeze_window.hide()
        self._freeze_window.deleteLater()
        self._restore_chip.hide()
        self._restore_chip.deleteLater()
        self._flush_bcast()
        if self._collab is not None:
            self._collab.broadcast({"type": "clear"})
            self._collab.set_state_provider(self._app_collab["state_provider"])
        else:
            self._stop_collab()
        self.closed.emit()
        super().closeEvent(event)

    def _on_capture_pause_toggled(self, paused):
        if paused:
            if not self._pause_capture():
                self.toolbar.set_capture_paused(False)
                self._pause_banner.setText(
                    "Nepavyko pristabdyti vaizdo. Dalinkitės visu ekranu Windows 10/11."
                )
                self._place_pause_banner()
                self._pause_banner.show()
                QTimer.singleShot(3500, self._hide_pause_banner_if_idle)
                return
        else:
            self._resume_capture()
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)

    def _hide_pause_banner_if_idle(self):
        if not self._capture_paused:
            self._pause_banner.hide()

    def _snapshot_for_capture(self):
        self.toolbar.set_capture_paused(False)
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.white)
        painter = QPainter(pixmap)
        y = 0
        painter.drawPixmap(0, y, self.toolbar.grab())
        y += self.toolbar.height()
        if self.collab_bar.isVisible():
            painter.drawPixmap(0, y, self.collab_bar.grab())
            y += self.collab_bar.height()
        painter.drawPixmap(0, y, self.canvas.grab())
        painter.end()
        self.toolbar.set_capture_paused(True)
        return pixmap

    def _sync_freeze_geometry(self):
        self._freeze_window.set_snapshot(
            self._freeze_window.pixmap(),
            self.geometry(),
            self.mask(),
        )

    def _show_freeze_mirror(self):
        self._sync_freeze_geometry()
        self._freeze_window.show()
        self._freeze_window.raise_()
        if sys.platform == "win32":
            _set_window_topmost(int(self._freeze_window.winId()), activate=False)
            _set_window_topmost(int(self.winId()), activate=False)

    def _apply_capture_exclusion(self, excluded):
        if sys.platform != "win32":
            return False
        return _set_excluded_from_capture(int(self.winId()), excluded)

    def _place_pause_banner(self):
        self._pause_banner.setMaximumWidth(max(120, self.width() - 16))
        self._pause_banner.adjustSize()
        top = self.toolbar.height()
        if self.collab_bar.isVisible():
            top += self.collab_bar.height()
        x = max(8, (self.width() - self._pause_banner.width()) // 2)
        self._pause_banner.move(x, top + 10)
        self._pause_banner.raise_()

    def _pause_capture(self):
        snapshot = self._snapshot_for_capture()
        self._freeze_window.set_snapshot(snapshot, self.geometry(), self.mask())
        self._show_freeze_mirror()
        self._freeze_window.repaint()
        QApplication.processEvents()
        if not self._apply_capture_exclusion(True):
            self._freeze_window.hide()
            return False
        self._capture_paused = True
        self._pause_banner.setText("Vaizdas pristabdytas — Discord / Teams šio piešimo nemato")
        self._place_pause_banner()
        self._pause_banner.show()
        self._keep_discord_on_top()
        return True

    def _resume_capture(self, force=False):
        was_paused = self._capture_paused
        self._capture_paused = False
        self._pause_banner.hide()
        self.toolbar.set_capture_paused(False)
        if was_paused or force:
            self._apply_capture_exclusion(False)
        self._freeze_window.hide()
        self._freeze_window.clear_snapshot()
        if self.isVisible() and not self._is_minimized:
            self._force_topmost()

    def _on_collab_toggled(self, enabled):
        if enabled:
            self._start_collab()
        else:
            self._stop_collab()
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)

    def _start_collab(self):
        if self._collab is not None:
            self.collab_bar.show()
            if self._capture_paused:
                self._place_pause_banner()
            return
        html_path = os.path.join(resource_dir(), "guest.html")
        if not os.path.isfile(html_path):
            self.toolbar.collab_button.setChecked(False)
            return
        self.collab_bar.reset()
        self.collab_bar.show()
        if self._capture_paused:
            self._place_pause_banner()
        self.collab_bar.set_status("Ruošiama nuoroda...")
        self._cached_state = self.canvas.export_state()
        saved = load_session_config()
        token = saved.get("token") or secrets.token_urlsafe(8)
        port = saved.get("port") or None
        session = CollabSession(html_path, token, self.collab_bridge)
        session.set_state_provider(self._app_collab["state_provider"])
        try:
            session.start(port=port)
        except Exception:
            self.toolbar.collab_button.setChecked(False)
            self.collab_bar.hide()
            return
        self._collab = session
        if self._app_collab is not None:
            self._app_collab["session"] = session
            self._app_collab["allowed"] = False
        QTimer.singleShot(0, session.start_tunnel)

    def _stop_collab(self):
        session = self._collab
        self._collab = None
        if self._app_collab is not None:
            self._app_collab["session"] = None
            self._app_collab["allowed"] = False
        if session is not None:
            session.stop()
        self.collab_bar.hide()
        self.collab_bar.reset()
        self.toolbar.collab_button.setChecked(False)
        if self._capture_paused:
            self._place_pause_banner()
        self.collab_stopped.emit()

    def _on_allow_guest(self, allowed):
        if self._collab is not None:
            self._collab.set_allowed(allowed)
        if self._app_collab is not None:
            self._app_collab["allowed"] = bool(allowed)
        self.collab_bar.set_status(
            "Svečias gali rašyti." if allowed else "Svečias mato lentą, bet rašyti dar negali."
        )
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)

    def _on_clear_all(self):
        self._flush_bcast()
        self.canvas.clear_all()
        if self._collab is not None:
            self._cached_state = self.canvas.export_state()
            self._collab.broadcast({"type": "clear"})
        self.canvas.setFocus(Qt.ActiveWindowFocusReason)

    def _open_local_link(self):
        url = self.collab_bar.current_url()
        if self._collab is not None:
            url = self._collab.lan_url
        if not url:
            return
        if sys.platform == "win32":
            ctypes.windll.user32.SetWindowPos(
                int(self.winId()),
                HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
            )
        webbrowser.open(url)

    def _bcast_stroke_start(self, stroke_id, color, x, y):
        if self._collab is not None:
            self._collab.broadcast(
                {"type": "stroke_start", "id": stroke_id, "color": color, "x": x, "y": y}
            )

    def _bcast_stroke_point(self, stroke_id, x, y):
        if self._collab is None:
            return
        bucket = self._pending_bcast.setdefault(stroke_id, [])
        bucket.append([x, y])
        if not self._bcast_timer.isActive():
            self._bcast_timer.start()

    def _flush_bcast(self):
        if self._collab is None:
            self._pending_bcast.clear()
            return
        for stroke_id, pts in self._pending_bcast.items():
            if pts:
                self._collab.broadcast({"type": "stroke_points", "id": stroke_id, "pts": pts})
        self._pending_bcast.clear()

    def _bcast_stroke_end(self, stroke_id):
        if self._collab is not None:
            self._flush_bcast()
            self._cached_state = self.canvas.export_state()
            self._collab.broadcast({"type": "stroke_end", "id": stroke_id})

    def _bcast_view(self):
        if self._collab is None:
            return
        now = time.monotonic()
        if now - self._last_view_bcast < 0.12:
            return
        self._last_view_bcast = now
        self._bcast_state()

    def _bcast_state(self):
        if self._collab is not None:
            self._cached_state = self.canvas.export_state()
            self._collab.broadcast({"type": "state", "state": self._cached_state})


class WhiteboardApp:
    def __init__(self, app):
        self.app = app
        self.app.setQuitOnLastWindowClosed(False)
        self.bridge = HotkeyBridge()
        self.bridge.open_whiteboard.connect(self._open_whiteboard, Qt.QueuedConnection)
        self.overlay_bridge = OverlayBridge()
        self.overlay_bridge.open_overlay.connect(self._open_overlay, Qt.QueuedConnection)
        self.overlay_bridge.overlay_escape.connect(self._overlay_escape, Qt.QueuedConnection)
        self.overlay_bridge.overlay_space.connect(self._overlay_space, Qt.QueuedConnection)
        self._whiteboard = None
        self._overlay = None
        self._overlay_hotkeys = []
        self._overlay_pass_ok = False
        self._last_hotkey = 0.0
        self._listener_ready = threading.Event()
        self._listener_error = None
        self._quiet = is_background()
        self._collab = {
            "bridge": CollabBridge(),
            "session": None,
            "allowed": False,
            "state_provider": self._collab_state_provider,
        }
        self._singleton = QLocalServer()
        QLocalServer.removeServer(APP_INSTANCE_KEY)
        self._singleton.listen(APP_INSTANCE_KEY)
        self._singleton.newConnection.connect(self._on_singleton_connection)
        self._mutex_handle = _take_instance_mutex()

    def _collab_state_provider(self):
        if self._whiteboard is not None:
            return self._whiteboard.canvas.export_state()
        return {}

    def _log(self, message):
        if not self._quiet:
            print(message)

    def _on_singleton_connection(self):
        socket = self._singleton.nextPendingConnection()
        if socket is not None:
            socket.write(b"ok")
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
        self._shutdown()

    def _shutdown(self):
        if self._whiteboard is not None:
            try:
                self._whiteboard.close()
            except Exception:
                pass
        if self._overlay is not None:
            try:
                self._overlay.close()
            except Exception:
                pass
        self._remove_overlay_pass_hotkeys()
        QTimer.singleShot(0, self.app.quit)

    def _open_whiteboard(self):
        if self._overlay is not None:
            return
        if self._whiteboard is not None:
            self._whiteboard.restore_whiteboard()
            return

        self._log("Opening whiteboard...")
        self._whiteboard = WhiteboardWindow(app_collab=self._collab)
        self._whiteboard.closed.connect(self._on_whiteboard_closed)
        self._whiteboard.collab_stopped.connect(self._on_collab_stopped)

    def _open_overlay(self):
        if self._overlay is not None or self._whiteboard is not None:
            return
        self._log("Opening draw-on-windows overlay...")
        self._overlay = AnnotateOverlay()
        self._overlay.closed.connect(self._on_overlay_closed)
        self._overlay.mouse_mode_changed.connect(self._on_overlay_mouse_mode)

    def _on_overlay_closed(self):
        self._remove_overlay_pass_hotkeys()
        self._overlay = None
        self._last_hotkey = 0.0
        self._log("Overlay closed. Press * and / together to draw on windows again.")

    def _on_overlay_mouse_mode(self, enabled):
        if enabled:
            self._install_overlay_pass_hotkeys()
        else:
            self._remove_overlay_pass_hotkeys()

    def _install_overlay_pass_hotkeys(self):
        self._remove_overlay_pass_hotkeys()
        self._overlay_pass_ok = False
        try:
            self._overlay_hotkeys.append(
                keyboard.add_hotkey(
                    "space",
                    lambda: self.overlay_bridge.overlay_space.emit(),
                    suppress=True,
                )
            )
            self._overlay_hotkeys.append(
                keyboard.add_hotkey(
                    "esc",
                    lambda: self.overlay_bridge.overlay_escape.emit(),
                    suppress=True,
                )
            )
            self._overlay_pass_ok = True
        except Exception as exc:
            self._log(f"Overlay mouse hotkeys failed: {exc}")
            self._remove_overlay_pass_hotkeys()

    def _remove_overlay_pass_hotkeys(self):
        for handle in getattr(self, "_overlay_hotkeys", []):
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                pass
        self._overlay_hotkeys = []
        self._overlay_pass_ok = False

    def _overlay_escape(self):
        if self._overlay is not None:
            self._overlay.handle_esc()

    def _overlay_space(self):
        if self._overlay is not None:
            self._overlay.handle_space()

    def _on_whiteboard_closed(self):
        self._whiteboard = None
        self._last_hotkey = 0.0
        if self._collab.get("session"):
            self._log("Whiteboard closed. Bendras režimas vis dar aktyvus — ta pati nuoroda galioja.")
        else:
            self._log("Whiteboard closed. Press * and - together to open again.")

    def _on_collab_stopped(self):
        self._collab["session"] = None
        self._collab["allowed"] = False

    def _maybe_trigger(self):
        now = time.monotonic()
        if now - self._last_hotkey < HOTKEY_COOLDOWN:
            return
        if combo_pressed("open_overlay"):
            if self._overlay is not None or self._whiteboard is not None:
                return
            self._last_hotkey = now
            self.overlay_bridge.open_overlay.emit()
            return
        if combo_pressed("open_whiteboard"):
            if self._overlay is not None:
                return
            self._last_hotkey = now
            self.bridge.open_whiteboard.emit()

    def _on_key_event(self, event):
        if event.event_type != keyboard.KEY_DOWN:
            return
        overlay = self._overlay
        if overlay is not None:
            name = (event.name or "").lower()
            if overlay.is_mouse_mode() and not self._overlay_pass_ok:
                if name in ("esc", "escape"):
                    self.overlay_bridge.overlay_escape.emit()
                elif name == "space":
                    self.overlay_bridge.overlay_space.emit()
            return
        self._maybe_trigger()

    def _hotkey_listener(self):
        try:
            keyboard.hook(self._on_key_event, suppress=False)
            self._listener_ready.set()
            keyboard.wait()
        except Exception as exc:
            self._listener_error = exc
            self._listener_ready.set()

    def start_listener(self):
        if not self._quiet:
            print("=" * 50)
            print("Whiteboard tool is running.")
            print("Hotkey: hold * and - together for the whiteboard")
            print("Hotkey: hold * and / together to draw on windows")
            print("ESC goes back / closes.")
            print("=" * 50)

            if is_admin():
                print()
                print("WARNING: Running as Administrator can break global hotkeys.")
                print()

        thread = threading.Thread(target=self._hotkey_listener, daemon=True)
        thread.start()
        self._listener_ready.wait(timeout=5)

        if self._listener_error:
            if not self._quiet:
                print(f"Hotkey listener failed: {self._listener_error}")
            return 1

        if not self._quiet:
            print("Listening for hotkey...")
        return 0

    def run(self):
        code = self.start_listener()
        if code:
            return code
        return self.app.exec_()


def main():
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Ekrano rašiklis")
    app.setQuitOnLastWindowClosed(False)
    icon, icon_path = _ensure_app_icon()
    app.setWindowIcon(icon)
    _ensure_desktop_shortcut(icon_path, force="--install-shortcut" in sys.argv)
    if "--install-shortcut" in sys.argv:
        return 0
    if _toggle_off_existing():
        _show_status_toast("off", quit_on_close=True)
        return app.exec_()
    if not ensure_licensed():
        return 0
    tool = WhiteboardApp(app)
    code = tool.start_listener()
    if code:
        return code
    toast = _show_status_toast("on")
    toast.open_overlay.connect(tool._open_overlay)
    toast.open_whiteboard.connect(tool._open_whiteboard)
    return app.exec_()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        if is_frozen():
            import traceback
            log_path = os.path.join(data_dir(), "startup_error.log")
            try:
                with open(log_path, "w", encoding="utf-8") as handle:
                    traceback.print_exc(file=handle)
            except OSError:
                pass
        raise
