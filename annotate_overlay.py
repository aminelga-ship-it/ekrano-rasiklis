"""
Transparent draw-on-windows overlay.
Hotkey * and / opens it. Discord full-screen share can see the ink.
"""

import ctypes
import math
import sys

from PyQt5.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal, QTimer
from PyQt5.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QKeyEvent,
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
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from shortcuts import (
    format_action,
    hold_down,
    hold_qt_key,
    is_hold_key,
    matches,
    shape_from_holds,
    sync_hold_state,
)

PEN_WIDTH = 5.0
ERASER_WIDTH = 28.0
MIN_PEN_WIDTH = 2.0
MAX_PEN_WIDTH = 36.0
MIN_ERASER_WIDTH = 8.0
MAX_ERASER_WIDTH = 96.0
PEN_WIDTH_STEP = 1.0
ERASER_WIDTH_STEP = 4.0
ZOOM_SCALE = 2.0
MIN_STROKE_DIST2 = 0.64
ARROW_HEAD = 22.0
_MENU_ICON_CACHE = {}
MENU_ROW_HEIGHT = 30

TOOL_PEN = "pen"
TOOL_ERASER = "eraser"
TOOL_TEXT = "text"
TOOL_MOUSE = "mouse"

COLOR_BLACK = QColor(0, 0, 0)
COLOR_BLUE = QColor(25, 118, 210)
COLOR_RED = QColor(220, 0, 0)
COLOR_GREEN = QColor(0, 150, 50)
PEN_DOT_SIZE = 8

HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
VK_Q = 0x51
VK_CAPITAL = 0x14

_user32 = ctypes.windll.user32
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
if ctypes.sizeof(ctypes.c_void_p) == 8:
    _user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
else:
    _user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    _user32.SetWindowLongW.restype = ctypes.c_long

_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte * 256)]
_user32.GetKeyboardState.restype = ctypes.c_bool

MIN_CIRCLE_RADIUS = 16.0


def _win_key_down(vk):
    if sys.platform != "win32":
        return False
    try:
        vk = int(vk)
        state = (ctypes.c_ubyte * 256)()
        if _user32.GetKeyboardState(state) and state[vk] & 0x80:
            return True
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def sync_shape_hold_keys():
    return sync_hold_state()


def q_key_is_held(q_key_down):
    return hold_down("hold_circle", tracked=bool(q_key_down))


class OverlayBridge(QObject):
    open_overlay = pyqtSignal()
    overlay_escape = pyqtSignal()
    overlay_space = pyqtSignal()


def make_pen_dot_cursor(color, diameter=PEN_DOT_SIZE):
    visual = max(6.0, min(18.0, float(diameter)))
    size = 22
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    center = QPointF(size / 2.0, size / 2.0)
    radius = max(2.0, visual / 2.0)
    fill = QColor(color)
    outline = QColor(255, 255, 255) if fill.lightness() < 150 else QColor(30, 30, 30)
    painter.setPen(QPen(outline, 2.0))
    painter.setBrush(fill)
    painter.drawEllipse(center, radius, radius)
    painter.end()
    return QCursor(pixmap, size // 2, size // 2)


def make_eraser_cursor(diameter):
    visual = max(10, min(48, int(round(float(diameter)))))
    size = visual + 6
    if size % 2 == 0:
        size += 1
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    center = QPointF(size / 2.0, size / 2.0)
    radius = max(4.0, visual / 2.0 - 1.0)
    painter.setPen(QPen(QColor(40, 40, 40, 220), 1.5))
    painter.setBrush(QColor(255, 255, 255, 70))
    painter.drawEllipse(center, radius, radius)
    painter.end()
    return QCursor(pixmap, size // 2, size // 2)


def clamp_pen_width(value):
    return max(MIN_PEN_WIDTH, min(MAX_PEN_WIDTH, float(value)))


def clamp_eraser_width(value):
    return max(MIN_ERASER_WIDTH, min(MAX_ERASER_WIDTH, float(value)))


def _widget_alive(widget):
    if widget is None:
        return False
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False


def _as_pointf(point):
    return QPointF(float(point.x()), float(point.y()))


def _stroke_path(points):
    path = QPainterPath()
    if not points:
        return path
    pts = [_as_pointf(p) for p in points]
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
        path.cubicTo(
            QPointF(p1.x() + (p2.x() - p0.x()) / 6.0, p1.y() + (p2.y() - p0.y()) / 6.0),
            QPointF(p2.x() - (p3.x() - p1.x()) / 6.0, p2.y() - (p3.y() - p1.y()) / 6.0),
            p2,
        )
    return path


def _append_point(points, pos):
    pos = _as_pointf(pos)
    if points:
        last = points[-1]
        dx = pos.x() - last.x()
        dy = pos.y() - last.y()
        if dx * dx + dy * dy < MIN_STROKE_DIST2:
            return False
    points.append(pos)
    return True


def _dist2(a, b):
    dx = float(a.x()) - float(b.x())
    dy = float(a.y()) - float(b.y())
    return dx * dx + dy * dy


def _dist2_to_segment(point, a, b):
    px, py = float(point.x()), float(point.y())
    x1, y1 = float(a.x()), float(a.y())
    x2, y2 = float(b.x()), float(b.y())
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    if length2 < 1e-9:
        return _dist2(point, a)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    return (px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2


def _drag_rect(p1, p2):
    return QRectF(_as_pointf(p1), _as_pointf(p2)).normalized()


def _circle_from_drag(p1, p2):
    radius = math.hypot(float(p2.x()) - float(p1.x()), float(p2.y()) - float(p1.y()))
    return _as_pointf(p1), max(1.0, radius)


def _triangle_from_rect(rect):
    rect = QRectF(rect).normalized()
    return [
        QPointF(rect.center().x(), rect.top()),
        QPointF(rect.left(), rect.bottom()),
        QPointF(rect.right(), rect.bottom()),
    ]


def _arrow_head(p1, p2, size=ARROW_HEAD):
    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    length = math.hypot(dx, dy)
    if length < 1:
        return QPolygonF()
    size = min(size, max(10.0, length * 0.28))
    ux, uy = dx / length, dy / length
    left = QPointF(
        p2.x() - ux * size + uy * size * 0.45,
        p2.y() - uy * size - ux * size * 0.45,
    )
    right = QPointF(
        p2.x() - ux * size - uy * size * 0.45,
        p2.y() - uy * size + ux * size * 0.45,
    )
    return QPolygonF([_as_pointf(p2), left, right])


def _item_hit(item, pos, radius):
    limit = radius * radius
    kind = item["type"]
    if kind == "stroke":
        points = item["points"]
        for i, point in enumerate(points):
            if _dist2(point, pos) <= limit:
                return True
            if i and _dist2_to_segment(pos, points[i - 1], point) <= limit:
                return True
        return False
    if kind == "line" or kind == "arrow":
        if _dist2_to_segment(pos, item["p1"], item["p2"]) <= limit:
            return True
        if kind == "arrow":
            for point in _arrow_head(item["p1"], item["p2"]):
                if _dist2(point, pos) <= limit:
                    return True
        return False
    if kind in ("square", "rect"):
        rect = item["rect"]
        corners = [
            rect.topLeft(),
            rect.topRight(),
            rect.bottomRight(),
            rect.bottomLeft(),
            rect.topLeft(),
        ]
        for i in range(4):
            if _dist2_to_segment(pos, corners[i], corners[i + 1]) <= limit:
                return True
        return False
    if kind == "circle":
        dist = math.sqrt(_dist2(pos, item["center"]))
        return abs(dist - item["radius"]) <= radius
    if kind == "triangle":
        pts = item["points"]
        for i in range(3):
            if _dist2_to_segment(pos, pts[i], pts[(i + 1) % 3]) <= limit:
                return True
        return False
    if kind == "text":
        return item["rect"].adjusted(-radius, -radius, radius, radius).contains(_as_pointf(pos))
    return False


def _item_bounds(item):
    kind = item.get("type")
    pad = float(item.get("width", PEN_WIDTH)) * 0.5 + 6.0
    if kind == "stroke":
        points = item.get("points") or []
        if not points:
            return QRect()
        xs = [float(p.x()) for p in points]
        ys = [float(p.y()) for p in points]
        rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    elif kind in ("line", "arrow"):
        rect = QRectF(_as_pointf(item["p1"]), _as_pointf(item["p2"])).normalized()
        if kind == "arrow":
            head = _arrow_head(item["p1"], item["p2"])
            if not head.isEmpty():
                rect = rect.united(head.boundingRect())
    elif kind in ("square", "rect"):
        rect = QRectF(item["rect"])
    elif kind == "circle":
        center = item["center"]
        radius = float(item["radius"])
        rect = QRectF(center.x() - radius, center.y() - radius, radius * 2.0, radius * 2.0)
    elif kind == "triangle":
        pts = item.get("points") or []
        if len(pts) < 3:
            return QRect()
        rect = QPolygonF(pts).boundingRect()
    elif kind == "text":
        rect = QRectF(item.get("rect") or QRectF())
        pad = max(pad, 6.0)
    else:
        return QRect()
    return rect.adjusted(-pad, -pad, pad, pad).toAlignedRect()


def _point_bounds(pos, radius):
    r = float(radius) + 4.0
    return QRectF(float(pos.x()) - r, float(pos.y()) - r, r * 2.0, r * 2.0).toAlignedRect()


def _pen_for(color, width=PEN_WIDTH):
    pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    pen.setCosmetic(False)
    return pen


def _configure_painter(painter):
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    if hasattr(QPainter, "HighQualityAntialiasing"):
        painter.setRenderHint(QPainter.HighQualityAntialiasing, True)


def _set_click_through(hwnd, enabled):
    if sys.platform != "win32":
        return
    hwnd = int(hwnd)
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        style = int(_user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        setter = _user32.SetWindowLongPtrW
    else:
        style = int(_user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        setter = _user32.SetWindowLongW
    style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW
    if enabled:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    setter(hwnd, GWL_EXSTYLE, style)


def _force_topmost(hwnd, activate=True):
    if sys.platform != "win32":
        return
    flags = SWP_NOMOVE | SWP_NOSIZE
    flags |= SWP_SHOWWINDOW if activate else SWP_NOACTIVATE
    _user32.SetWindowPos(int(hwnd), HWND_TOPMOST, 0, 0, 0, 0, flags)


def _menu_icon(kind, size=18):
    key = (kind, int(size))
    cached = _MENU_ICON_CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    ink = QColor(51, 65, 85)
    pen = QPen(ink, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    m = 3.2
    if kind == "hamburger":
        y = size * 0.32
        for _ in range(3):
            painter.drawLine(QPointF(m, y), QPointF(size - m, y))
            y += size * 0.18
    elif kind == "chevron":
        painter.drawLine(QPointF(m + 1, size * 0.38), QPointF(size / 2.0, size * 0.62))
        painter.drawLine(QPointF(size / 2.0, size * 0.62), QPointF(size - m - 1, size * 0.38))
    elif kind == "pen":
        painter.save()
        painter.translate(size / 2.0, size / 2.0)
        painter.rotate(-45)
        painter.scale(float(size) / 26.0, float(size) / 26.0)
        outline = QPen(ink, 1.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        light = QColor(241, 245, 249)
        mid = QColor(203, 213, 225)
        painter.setPen(outline)
        painter.setBrush(mid)
        painter.drawRoundedRect(QRectF(-2.3, -11.4, 4.6, 2.55), 0.75, 0.75)
        painter.setBrush(light)
        painter.drawRect(QRectF(-2.3, -8.9, 4.6, 1.95))
        painter.setPen(QPen(ink, 0.7))
        painter.drawLine(QPointF(-2.15, -8.2), QPointF(2.15, -8.2))
        painter.setPen(outline)
        painter.setBrush(light)
        body = QPainterPath()
        body.moveTo(-2.3, -6.95)
        body.lineTo(-2.3, 4.85)
        body.lineTo(2.3, 4.85)
        body.lineTo(2.3, -6.95)
        painter.drawPath(body)
        painter.setPen(QPen(ink, 0.8))
        painter.drawLine(QPointF(0.0, -6.7), QPointF(0.0, 4.7))
        painter.setPen(outline)
        painter.setBrush(mid)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(-2.3, 4.85),
                    QPointF(2.3, 4.85),
                    QPointF(0.0, 11.15),
                ]
            )
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(ink)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(-0.85, 8.35),
                    QPointF(0.85, 8.35),
                    QPointF(0.0, 11.15),
                ]
            )
        )
        painter.restore()
    elif kind == "eraser":
        painter.setBrush(QColor(226, 232, 240))
        painter.drawRoundedRect(QRectF(m, size * 0.30, size - 2 * m, size * 0.40), 2.0, 2.0)
    elif kind == "text":
        font = QFont("Segoe UI", max(9, int(size * 0.72)), QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, "T")
    elif kind == "zoom":
        painter.drawEllipse(QPointF(size * 0.42, size * 0.42), size * 0.24, size * 0.24)
        painter.drawLine(QPointF(size * 0.60, size * 0.60), QPointF(size - m, size - m))
    elif kind == "mouse":
        painter.drawRoundedRect(QRectF(size * 0.32, m, size * 0.36, size - 2 * m), 4.0, 4.0)
        painter.drawLine(QPointF(size * 0.50, m + 2), QPointF(size * 0.50, size * 0.42))
    elif kind == "undo":
        painter.setPen(QPen(ink, 1.85, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cy = size / 2.0
        painter.drawLine(QPointF(size - m, cy), QPointF(m + 1.2, cy))
        painter.drawLine(QPointF(m + 1.2, cy), QPointF(size * 0.46, m + 1.5))
        painter.drawLine(QPointF(m + 1.2, cy), QPointF(size * 0.46, size - m - 1.5))
    elif kind == "line":
        painter.drawLine(QPointF(m, size - m), QPointF(size - m, m))
    elif kind == "rect":
        painter.drawRoundedRect(QRectF(m, m + 1, size - 2 * m, size - 2 * m - 1), 2.0, 2.0)
    elif kind == "arrow":
        painter.drawLine(QPointF(m, size - m), QPointF(size - m - 1, m + 2))
        painter.drawLine(QPointF(size - m - 1, m + 2), QPointF(size * 0.58, m + 2))
        painter.drawLine(QPointF(size - m - 1, m + 2), QPointF(size - m - 1, size * 0.42))
    elif kind == "circle":
        painter.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
    elif kind == "triangle":
        path = QPainterPath()
        path.moveTo(size / 2.0, m)
        path.lineTo(size - m, size - m)
        path.lineTo(m, size - m)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "shapes":
        painter.drawRoundedRect(QRectF(m, m, size - 2 * m, size - 2 * m), 2.0, 2.0)
        tri = QPainterPath()
        tri.moveTo(size / 2.0, m + 3.4)
        tri.lineTo(size - m - 3.2, size - m - 3.0)
        tri.lineTo(m + 3.2, size - m - 3.0)
        tri.closeSubpath()
        painter.drawPath(tri)
    elif kind == "colors":
        painter.setPen(Qt.NoPen)
        radius = size * 0.16
        dots = (
            (QColor(15, 23, 42), size * 0.32, size * 0.32),
            (QColor(25, 118, 210), size * 0.68, size * 0.32),
            (QColor(220, 0, 0), size * 0.32, size * 0.68),
            (QColor(0, 150, 50), size * 0.68, size * 0.68),
        )
        for color, x, y in dots:
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, y), radius, radius)
    painter.end()
    _MENU_ICON_CACHE[key] = pixmap
    return pixmap


class OverlayMenu(QWidget):
    action = pyqtSignal(str)
    toggled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Meniu")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(
            "OverlayMenu { background: transparent; }"
            "QFrame#card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; }"
            "QPushButton#header { background: transparent; border: none; border-radius: 10px; "
            "padding: 6px 10px; font-size: 13px; font-weight: 700; color: #0f172a; text-align: left; }"
            "QPushButton#header:hover { background: #f8fafc; }"
            "QPushButton#row { background: transparent; border: none; border-radius: 8px; "
            "padding: 4px 8px; text-align: left; }"
            "QPushButton#row:hover { background: #f1f5f9; }"
            "QPushButton#row:checked { background: #e8f1ff; }"
            "QLabel#rowTitle { font-size: 13px; color: #0f172a; }"
            "QLabel#hotkey { font-size: 11px; font-weight: 700; color: #334155; }"
            "QPushButton#section { background: transparent; border: none; border-radius: 8px; "
            "padding: 4px 8px; text-align: left; }"
            "QPushButton#section:hover { background: #f1f5f9; }"
            "QPushButton#swatch { border-radius: 13px; min-width: 26px; min-height: 26px; "
            "max-width: 26px; max-height: 26px; }"
            "QLabel#swatchKey { font-size: 11px; font-weight: 600; color: #64748b; }"
        )
        self._expanded = False
        self._shapes_open = False
        self._colors_open = False
        self._rows = {}
        self._swatches = {}
        self._state = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        card = QFrame()
        card.setObjectName("card")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(6, 6, 6, 6)
        card_layout.setSpacing(3)

        self._header = QPushButton()
        self._header.setObjectName("header")
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setFocusPolicy(Qt.NoFocus)
        self._header.clicked.connect(self.toggle)
        card_layout.addWidget(self._header)

        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(2, 4, 2, 2)
        body.setSpacing(2)
        self._add_row(body, "pen", "pen", "Pieštukas")
        self._add_row(body, "eraser", "eraser", "Trintukas", "tool_eraser")
        self._add_row(body, "text", "text", "Tekstas", "tool_text")
        self._add_row(body, "zoom", "zoom", "Zoom", "overlay_zoom")
        self._add_row(body, "mouse", "mouse", "Pelė", "overlay_mouse")
        self._add_row(body, "undo", "undo", "Atgal", "undo")

        self._shapes_btn = self._add_section(body, "Figūros", "shapes")
        self._shapes_btn.clicked.connect(self._toggle_shapes)
        self._shapes_box = QWidget()
        shapes = QVBoxLayout(self._shapes_box)
        shapes.setContentsMargins(10, 0, 0, 4)
        shapes.setSpacing(2)
        self._add_row(shapes, "line", "line", "Linija", "hold_line")
        self._add_row(shapes, "rect", "rect", "Stačiakampis", "hold_rect")
        self._add_row(shapes, "arrow", "arrow", "Rodyklė")
        self._add_row(shapes, "circle", "circle", "Apskritimas", "hold_circle")
        self._add_row(shapes, "triangle", "triangle", "Trikampis", "hold_triangle")
        self._shapes_box.hide()
        body.addWidget(self._shapes_box)

        self._colors_btn = self._add_section(body, "Spalvos", "colors")
        self._colors_btn.clicked.connect(self._toggle_colors)
        self._colors_box = QWidget()
        colors = QHBoxLayout(self._colors_box)
        colors.setContentsMargins(12, 4, 8, 6)
        colors.setSpacing(10)
        for action_id, color in (
            ("color_black", COLOR_BLACK),
            ("color_blue", COLOR_BLUE),
            ("color_red", COLOR_RED),
            ("color_green", COLOR_GREEN),
        ):
            colors.addWidget(self._make_swatch(action_id, color), 0, Qt.AlignCenter)
        self._colors_box.hide()
        body.addWidget(self._colors_box)

        self._body.hide()
        card_layout.addWidget(self._body)
        root.addWidget(card)
        self._card = card
        self._refresh_header()
        self._refresh_shortcuts()
        self._fit()

    def _add_section(self, layout, title, icon_kind="shapes"):
        button = QPushButton()
        button.setObjectName("section")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        row = QHBoxLayout(button)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(_menu_icon(icon_kind))
        icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        label = QLabel(title)
        label.setObjectName("rowTitle")
        label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        chevron = QLabel()
        chevron.setPixmap(_menu_icon("chevron"))
        chevron.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        button._chevron = chevron
        row.addWidget(icon)
        row.addWidget(label, 1)
        row.addWidget(chevron)
        self._polish_row(button)
        layout.addWidget(button)
        return button

    def _add_row(self, layout, action_id, icon_kind, title, shortcut_id=None):
        button = QPushButton()
        button.setObjectName("row")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda _checked=False, key=action_id: self.action.emit(key))
        row = QHBoxLayout(button)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(_menu_icon(icon_kind))
        icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        name = QLabel(title)
        name.setObjectName("rowTitle")
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        name.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        hotkey = QLabel()
        hotkey.setObjectName("hotkey")
        hotkey.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hotkey.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        hotkey.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        row.addWidget(icon)
        row.addWidget(name)
        row.addWidget(hotkey)
        row.addStretch(1)
        button._hotkey = hotkey
        button._shortcut_id = shortcut_id
        self._rows[action_id] = button
        self._polish_row(button)
        layout.addWidget(button)
        return button

    def _make_swatch(self, action_id, color):
        cell = QWidget()
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        button = QPushButton()
        button.setObjectName("swatch")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolTip(action_id)
        button.clicked.connect(lambda _checked=False, key=action_id: self.action.emit(key))
        key = QLabel()
        key.setObjectName("swatchKey")
        key.setAlignment(Qt.AlignCenter)
        key.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(button, 0, Qt.AlignCenter)
        layout.addWidget(key)
        button._key = key
        button._color = QColor(color)
        self._swatches[action_id] = button
        return cell

    def _refresh_header(self):
        icon = QIcon(_menu_icon("hamburger", 18))
        self._header.setIcon(icon)
        self._header.setIconSize(QSize(18, 18))
        self._header.setText("Meniu")

    def _refresh_shortcuts(self):
        for action_id, button in self._rows.items():
            shortcut_id = getattr(button, "_shortcut_id", None)
            if shortcut_id:
                button._hotkey.setText(format_action(shortcut_id))
            elif action_id == "arrow":
                button._hotkey.setText(
                    "%s + %s" % (format_action("hold_line"), format_action("hold_rect"))
                )
            else:
                button._hotkey.setText("")
        for action_id, button in self._swatches.items():
            button._key.setText(format_action(action_id))
            self._paint_swatch(button, button._color, False)

    def _paint_swatch(self, button, color, selected):
        border = "#0f172a" if selected else "rgba(15, 23, 42, 40)"
        width = "2px" if selected else "1px"
        button.setStyleSheet(
            "QPushButton#swatch { background: %s; border: %s solid %s; border-radius: 13px; }"
            % (color.name(), width, border)
        )

    def _polish_row(self, button):
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setMinimumHeight(MENU_ROW_HEIGHT)

    def _expanded_width(self):
        title_font = QFont(self.font())
        title_font.setPixelSize(13)
        hot_font = QFont(self.font())
        hot_font.setPixelSize(11)
        hot_font.setWeight(QFont.DemiBold)
        title_fm = QFontMetrics(title_font)
        hot_fm = QFontMetrics(hot_font)
        chrome = 12 + 4 + 16 + 12 + 18 + 16
        widest = 0
        for button in self._rows.values():
            name = ""
            for child in button.findChildren(QLabel):
                if child.objectName() == "rowTitle":
                    name = child.text()
                    break
            hot = ""
            if getattr(button, "_hotkey", None) is not None:
                hot = button._hotkey.text()
            widest = max(
                widest,
                chrome + title_fm.horizontalAdvance(name) + hot_fm.horizontalAdvance(hot),
            )
        for title in ("Figūros", "Spalvos"):
            widest = max(widest, chrome + title_fm.horizontalAdvance(title) + 18)
        colors = 12 + 4 + 12 + 8
        count = 0
        for button in self._swatches.values():
            cell = button.parentWidget()
            cell_w = 32
            if cell is not None:
                cell_w = max(cell.sizeHint().width(), cell.minimumSizeHint().width(), 32)
            colors += cell_w
            count += 1
        if count > 1:
            colors += (count - 1) * 10
        return max(widest, colors)

    def _fit(self):
        limit = 16777215
        self.setMinimumSize(0, 0)
        self.setMaximumSize(limit, limit)
        self._body.setVisible(self._expanded)
        self._shapes_box.setVisible(self._expanded and self._shapes_open)
        self._colors_box.setVisible(self._expanded and self._colors_open)
        if not self._expanded:
            self._body.setMinimumWidth(0)
            self._body.setMaximumHeight(0)
            self._shapes_box.setMaximumHeight(0)
            self._colors_box.setMaximumHeight(0)
            self._card.setMinimumWidth(0)
            self._colors_box.setMinimumWidth(0)
            header = self._header.sizeHint()
            self.setFixedSize(max(108, header.width() + 20), max(36, header.height() + 14))
            return
        width = self._expanded_width()
        self._body.setMaximumHeight(limit)
        self._shapes_box.setMaximumHeight(limit if self._shapes_open else 0)
        self._colors_box.setMaximumHeight(limit if self._colors_open else 0)
        self._body.setMinimumWidth(width)
        self._card.setMinimumWidth(width)
        self._colors_box.setMinimumWidth(max(0, width - 16))
        height = max(32, self._header.sizeHint().height()) + 14
        height += 8 * MENU_ROW_HEIGHT
        if self._shapes_open:
            height += 5 * MENU_ROW_HEIGHT + 8
        if self._colors_open:
            height += 56
        self.setFixedSize(width, height)

    def toggle(self):
        self._expanded = not self._expanded
        if self._expanded:
            self._refresh_shortcuts()
            self.sync(self._state)
        self._fit()
        self.toggled.emit()

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._fit()
        self.toggled.emit()

    def _toggle_shapes(self):
        self._shapes_open = not self._shapes_open
        self._fit()
        self.toggled.emit()

    def _toggle_colors(self):
        self._colors_open = not self._colors_open
        self._fit()
        self.toggled.emit()

    def sync(self, state):
        self._state = dict(state or {})
        if not self._expanded:
            return
        tool = self._state.get("tool")
        sticky = self._state.get("shape")
        color = self._state.get("color")
        mouse = bool(self._state.get("mouse"))
        zoom = bool(self._state.get("zoom"))
        for action_id, button in self._rows.items():
            checked = False
            if action_id == "mouse":
                checked = mouse
            elif action_id == "zoom":
                checked = zoom
            elif action_id == "eraser":
                checked = tool == TOOL_ERASER and not mouse
            elif action_id == "text":
                checked = tool == TOOL_TEXT and not mouse
            elif action_id == "pen":
                checked = tool == TOOL_PEN and not mouse and not sticky and not zoom
            elif action_id in ("line", "rect", "arrow", "circle", "triangle"):
                checked = sticky == action_id and not mouse
            button.setChecked(checked)
        for action_id, button in self._swatches.items():
            selected = color == action_id
            self._paint_swatch(button, button._color, selected)

    def place_on(self, overlay):
        origin = overlay.geometry().topLeft()
        self.move(origin.x() + 16, origin.y() + 16)


class AnnotateOverlay(QWidget):
    closed = pyqtSignal()
    mouse_mode_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rašymas ant langų")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.tool = TOOL_PEN
        self.pen_color = QColor(COLOR_BLACK)
        self.pen_width = PEN_WIDTH
        self.eraser_width = ERASER_WIDTH
        self._color_label = "juoda"
        self.items = []
        self._current = None
        self._drawing = False
        self._mouse_mode = False
        self._text_editor = None
        self._text_pos = None
        self._zoom_bg = None
        self._zoom_active = False
        self._zoom_locked = False
        self._zoom_x = 0.0
        self._zoom_y = 0.0
        self._closing = False
        self._q_key_down = False
        self._caps_held = False
        self._sticky_shape = None
        self._text_ignore_focus = False
        self._undo_stack = []
        self._erase_snapshot = None
        self._eraser_preview_pos = None
        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(32)
        self._hold_timer.timeout.connect(self._poll_hold_keys)
        self._hold_timer.start()

        self._cover_screens()
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabKeyboard()
        self.setFocus(Qt.ActiveWindowFocusReason)
        _set_click_through(int(self.winId()), False)
        _force_topmost(int(self.winId()), activate=True)
        self._update_cursor()
        self._menu = OverlayMenu()
        self._menu.action.connect(self._on_menu_action)
        self._menu.toggled.connect(self._on_menu_toggled)
        self._menu.place_on(self)
        self._menu.show()
        self._raise_menu()
        self._sync_menu()
        QTimer.singleShot(0, self._ensure_input)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.applicationStateChanged.connect(self._on_app_state)

    def _ensure_input(self):
        if self._closing or self._mouse_mode:
            return
        self.activateWindow()
        self.grabKeyboard()
        self.setFocus(Qt.ActiveWindowFocusReason)
        _force_topmost(int(self.winId()), activate=True)
        self._raise_menu()

    def _raise_menu(self):
        menu = getattr(self, "_menu", None)
        if menu is None:
            return
        try:
            menu.place_on(self)
            menu.raise_()
            if sys.platform == "win32" and menu.isVisible():
                _force_topmost(int(menu.winId()), activate=False)
        except RuntimeError:
            self._menu = None

    def _on_menu_toggled(self):
        QTimer.singleShot(0, self._restore_keyboard)
        self._raise_menu()

    def _on_app_state(self, state):
        if self._closing or not self.isVisible():
            return
        if state == Qt.ApplicationActive:
            self._raise_menu()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow() and not self._closing:
            self._raise_menu()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._raise_menu()

    def _canvas_rect_to_view(self, rect):
        r = QRectF(rect)
        if self._zoom_active:
            r = QRectF(
                (r.x() - self._zoom_x) * ZOOM_SCALE,
                (r.y() - self._zoom_y) * ZOOM_SCALE,
                r.width() * ZOOM_SCALE,
                r.height() * ZOOM_SCALE,
            )
        return r.toAlignedRect().adjusted(-2, -2, 2, 2)

    def _union_item_bounds(self, items):
        bounds = QRect()
        for item in items:
            bounds = bounds.united(_item_bounds(item))
        return bounds

    def _update_region(self, rect=None):
        if rect is None or (self._zoom_active and not self._zoom_locked):
            self.update()
            return
        view = self._canvas_rect_to_view(rect)
        view = view.intersected(self.rect())
        if view.isEmpty():
            return
        self.update(view)

    def _menu_state(self):
        color_id = "color_black"
        if self.pen_color == COLOR_BLUE:
            color_id = "color_blue"
        elif self.pen_color == COLOR_RED:
            color_id = "color_red"
        elif self.pen_color == COLOR_GREEN:
            color_id = "color_green"
        return {
            "tool": self.tool,
            "shape": self._sticky_shape,
            "color": color_id,
            "mouse": self._mouse_mode,
            "zoom": self._zoom_active,
        }

    def _sync_menu(self):
        menu = getattr(self, "_menu", None)
        if menu is None:
            return
        try:
            menu.sync(self._menu_state())
        except RuntimeError:
            self._menu = None

    def _on_menu_action(self, action_id):
        if action_id == "mouse":
            self.handle_space()
            self._sync_menu()
            return
        if self._mouse_mode:
            self._set_mouse_mode(False)
        if action_id == "pen":
            self._sticky_shape = None
            self.tool = TOOL_PEN
            if self._zoom_active:
                self._exit_zoom()
        elif action_id == "eraser":
            self._finish_text(False)
            self._sticky_shape = None
            self.tool = TOOL_ERASER
        elif action_id == "text":
            self._finish_text(False)
            self._sticky_shape = None
            self.tool = TOOL_TEXT
        elif action_id == "zoom":
            self._enter_zoom()
        elif action_id == "undo":
            self._undo()
        elif action_id in ("line", "rect", "arrow", "circle", "triangle"):
            self._finish_text(False)
            self.tool = TOOL_PEN
            self._sticky_shape = action_id
        elif action_id == "color_black":
            self.pen_color = QColor(COLOR_BLACK)
            self._color_label = "juoda"
        elif action_id == "color_blue":
            self.pen_color = QColor(COLOR_BLUE)
            self._color_label = "mėlyna"
        elif action_id == "color_red":
            self.pen_color = QColor(COLOR_RED)
            self._color_label = "raudona"
        elif action_id == "color_green":
            self.pen_color = QColor(COLOR_GREEN)
            self._color_label = "žalia"
        self._update_cursor()
        self.update()
        self._sync_menu()
        QTimer.singleShot(0, self._restore_keyboard)

    def is_mouse_mode(self):
        return self._mouse_mode

    def _cover_screens(self):
        screens = QApplication.screens()
        if not screens:
            return
        bounds = QRect()
        region = QRegion()
        for screen in screens:
            geo = screen.geometry()
            bounds = geo if bounds.isNull() else bounds.united(geo)
            region = region.united(QRegion(geo))
        self.setGeometry(bounds)
        region.translate(-bounds.x(), -bounds.y())
        self.setMask(region)

    def _apply_focus_flags(self, click_through):
        flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        if click_through:
            flags |= Qt.WindowDoesNotAcceptFocus
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.show()
        _set_click_through(int(self.winId()), click_through)
        _force_topmost(int(self.winId()), activate=not click_through)
        self._raise_menu()

    def _stop_live_drawing(self):
        if self._erase_snapshot is not None:
            if self._erase_snapshot != self.items:
                self._undo_stack.append(("restore", self._erase_snapshot))
            self._erase_snapshot = None
        self._drawing = False
        self._current = None

    def _set_mouse_mode(self, enabled):
        if self._zoom_active:
            return
        self._finish_text(False)
        self._stop_live_drawing()
        enabled = bool(enabled)
        if enabled == self._mouse_mode:
            if not enabled:
                self.tool = TOOL_PEN
                self._update_cursor()
            return
        self._mouse_mode = enabled
        if self._mouse_mode:
            self.tool = TOOL_MOUSE
            self.releaseKeyboard()
            self._apply_focus_flags(True)
        else:
            self.tool = TOOL_PEN
            self._apply_focus_flags(False)
            self.activateWindow()
            self.grabKeyboard()
            self.setFocus(Qt.ActiveWindowFocusReason)
        self.mouse_mode_changed.emit(self._mouse_mode)
        self._update_cursor()
        self._sync_menu()
        self.update()

    def handle_space(self):
        if self._zoom_active:
            return
        self._set_mouse_mode(not self._mouse_mode)

    def handle_esc(self):
        if self._text_editor is not None:
            self._finish_text(False)
            self.tool = TOOL_PEN
            self._sticky_shape = None
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        if self._zoom_active:
            self._exit_zoom()
            self.tool = TOOL_PEN
            self._sticky_shape = None
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        if self._mouse_mode:
            self._set_mouse_mode(False)
            return
        if self.tool != TOOL_PEN or self._sticky_shape:
            self.tool = TOOL_PEN
            self._sticky_shape = None
            self._stop_live_drawing()
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        self.close()

    def _grab_desktop(self):
        origin = self.geometry().topLeft()
        pixmap = QPixmap(self.size())
        pixmap.fill(QColor(0, 0, 0))
        painter = QPainter(pixmap)
        for screen in QApplication.screens():
            geo = screen.geometry()
            part = screen.grabWindow(0)
            dest = QPoint(geo.x() - origin.x(), geo.y() - origin.y())
            painter.drawPixmap(dest, part)
        painter.end()
        return pixmap

    def _zoom_src_size(self):
        return (
            max(1.0, self.width() / ZOOM_SCALE),
            max(1.0, self.height() / ZOOM_SCALE),
        )

    def _clamp_zoom(self, x, y):
        src_w, src_h = self._zoom_src_size()
        max_x = max(0.0, self.width() - src_w)
        max_y = max(0.0, self.height() - src_h)
        return max(0.0, min(max_x, x)), max(0.0, min(max_y, y))

    def _update_zoom_from_mouse(self, pos):
        src_w, src_h = self._zoom_src_size()
        self._zoom_x, self._zoom_y = self._clamp_zoom(
            float(pos.x()) - src_w / 2.0,
            float(pos.y()) - src_h / 2.0,
        )

    def _enter_zoom(self):
        if self._zoom_active:
            return
        self._finish_text(False)
        self._stop_live_drawing()
        self._mouse_mode = False
        self.hide()
        QApplication.processEvents()
        self._zoom_bg = self._grab_desktop()
        self._zoom_active = True
        self._zoom_locked = False
        self._update_zoom_from_mouse(self.mapFromGlobal(QCursor.pos()))
        self._apply_focus_flags(False)
        self.activateWindow()
        self.grabKeyboard()
        self.setFocus(Qt.ActiveWindowFocusReason)
        _force_topmost(int(self.winId()), activate=True)
        self._raise_menu()
        self._update_cursor()
        self._sync_menu()
        self.update()

    def _lock_zoom(self):
        self._zoom_locked = True
        self.tool = TOOL_PEN
        self._update_cursor()
        self._sync_menu()
        self.update()

    def _exit_zoom(self):
        self._zoom_active = False
        self._zoom_locked = False
        self._zoom_bg = None
        self._stop_live_drawing()
        self._update_cursor()
        self._sync_menu()

    def _view_to_canvas(self, pos):
        if not self._zoom_active:
            return _as_pointf(pos)
        return QPointF(
            self._zoom_x + float(pos.x()) / ZOOM_SCALE,
            self._zoom_y + float(pos.y()) / ZOOM_SCALE,
        )

    def _q_is_held(self):
        return hold_down("hold_circle", tracked=bool(self._q_key_down))

    def _sync_shape_hold_keys(self):
        circle_down, triangle_down = sync_hold_state()
        self._q_key_down = circle_down
        self._caps_held = triangle_down

    def _poll_hold_keys(self):
        if self._mouse_mode or _widget_alive(self._text_editor):
            return
        prev_q = self._q_is_held()
        prev_caps = self._caps_held
        self._sync_shape_hold_keys()
        if self._q_is_held() != prev_q or self._caps_held != prev_caps:
            if self._drawing and self._current is not None:
                self._update_region(_item_bounds(self._current))

    def _shape_from_modifiers(self, drawing=False):
        self._sync_shape_hold_keys()
        held = shape_from_holds(
            circle_tracked=bool(self._q_key_down),
            triangle_tracked=bool(self._caps_held),
        )
        if held != "stroke":
            return held
        if self._sticky_shape:
            return self._sticky_shape
        return "stroke"

    def _color_name(self):
        return self._color_label

    def _tool_label(self):
        if self._zoom_active and not self._zoom_locked:
            return "Zoom — judinkite pelę, spustelėkite kad užfiksuoti"
        prefix = "Zoom 200% · " if self._zoom_active else ""
        if self.tool == TOOL_ERASER:
            return prefix + "Trintukas · %d" % int(round(self.eraser_width))
        if self.tool == TOOL_TEXT:
            return prefix + "Tekstas"
        if self.tool == TOOL_MOUSE:
            return prefix + "Pelė — langai aktyvūs"
        labels = {
            "line": "Linija",
            "arrow": "Rodyklė",
            "rect": "Stačiakampis",
            "square": "Stačiakampis",
            "circle": "Apskritimas",
            "triangle": "Trikampis",
            "stroke": "Pieštukas",
        }
        return prefix + labels.get(self._shape_from_modifiers(), "Pieštukas") + " · %d" % int(round(self.pen_width))

    def _update_cursor(self):
        if self._mouse_mode:
            self.unsetCursor()
            return
        if self._zoom_active and not self._zoom_locked:
            self.setCursor(Qt.OpenHandCursor)
            return
        if self.tool == TOOL_ERASER:
            self.setCursor(make_eraser_cursor(self.eraser_width))
        elif self.tool == TOOL_TEXT:
            self.setCursor(Qt.IBeamCursor)
        else:
            self.setCursor(make_pen_dot_cursor(self.pen_color, self.pen_width + 3))

    def _text_font(self):
        font = QFont("Arial", 20)
        font.setStyleStrategy(QFont.PreferAntialias)
        if hasattr(QFont, "PreferNoHinting"):
            font.setHintingPreference(QFont.PreferNoHinting)
        return font

    def _text_rect(self, pos, text):
        metrics = QFontMetrics(self._text_font())
        lines = (text or "").split("\n") or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines)
        height = metrics.lineSpacing() * max(0, len(lines) - 1) + metrics.height()
        return QRectF(
            float(pos.x()),
            float(pos.y()),
            max(1.0, float(width)),
            max(1.0, float(height)),
        )

    def _finish_text(self, commit):
        editor = self._text_editor
        if not _widget_alive(editor):
            self._text_editor = None
            self._text_pos = None
            self._text_ignore_focus = False
            return
        self._text_editor = None
        pos = self._text_pos
        self._text_pos = None
        self._text_ignore_focus = False
        text = ""
        try:
            text = editor.toPlainText().strip()
        except RuntimeError:
            text = ""
        try:
            editor.hide()
            editor.deleteLater()
        except RuntimeError:
            pass
        if commit and text and pos is not None:
            self.items.append(
                {
                    "type": "text",
                    "text": text,
                    "pos": _as_pointf(pos),
                    "rect": self._text_rect(pos, text),
                    "color": QColor(self.pen_color),
                }
            )
            self._undo_stack.append(("add", self.items[-1]))
        QTimer.singleShot(0, self._restore_keyboard)
        self.update()

    def _start_text(self, canvas_pos):
        self._finish_text(False)
        view = canvas_pos
        if self._zoom_active:
            view = QPointF(
                (canvas_pos.x() - self._zoom_x) * ZOOM_SCALE,
                (canvas_pos.y() - self._zoom_y) * ZOOM_SCALE,
            )
        self._text_pos = _as_pointf(canvas_pos)
        self.releaseKeyboard()
        self._text_ignore_focus = True
        editor = QPlainTextEdit(self)
        editor.setFont(self._text_font())
        editor.setFrameStyle(QFrame.NoFrame)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        editor.setWordWrapMode(QTextOption.NoWrap)
        editor.document().setDocumentMargin(0)
        editor.setAttribute(Qt.WA_InputMethodEnabled, True)
        editor.setAutoFillBackground(False)
        color = self.pen_color.name()
        editor.setStyleSheet(
            "QPlainTextEdit { background: transparent; color: %s; border: none; padding: 0px; }"
            % color
        )
        editor.move(int(view.x()), int(view.y()))
        editor.textChanged.connect(self._resize_text_editor)
        editor.installEventFilter(self)
        self._text_editor = editor
        self._resize_text_editor()
        editor.show()
        editor.raise_()
        origin = editor.viewport().mapTo(editor, QPoint(0, 0))
        editor.move(int(view.x()) - origin.x(), int(view.y()) - origin.y())
        editor.setFocus(Qt.OtherFocusReason)
        QTimer.singleShot(0, self._arm_text_editor)

    def _arm_text_editor(self):
        editor = self._text_editor
        if not _widget_alive(editor):
            return
        try:
            editor.raise_()
            editor.setFocus(Qt.OtherFocusReason)
        except RuntimeError:
            self._text_editor = None
            return
        self._text_ignore_focus = False

    def _resize_text_editor(self):
        editor = self._text_editor
        if not _widget_alive(editor):
            return
        try:
            metrics = QFontMetrics(editor.font())
            lines = (editor.toPlainText() or "").split("\n") or [""]
            width = max(metrics.horizontalAdvance(line) for line in lines)
            needed = width + metrics.horizontalAdvance("W") + 4
            min_w = metrics.horizontalAdvance("MMMM") + 4
            editor.setFixedWidth(max(min_w, min(needed, max(120, self.width() - editor.x() - 8))))
            height = metrics.lineSpacing() * len(lines) + 4
            editor.setFixedHeight(max(metrics.height() + 4, height))
        except RuntimeError:
            self._text_editor = None

    def _restore_keyboard(self):
        if self._closing or self._mouse_mode or _widget_alive(self._text_editor):
            return
        self.activateWindow()
        self.grabKeyboard()
        self.setFocus(Qt.ActiveWindowFocusReason)
        self._raise_menu()

    def _commit_text_and_pen(self):
        self._finish_text(True)
        self.tool = TOOL_PEN
        self._update_cursor()
        self._sync_menu()

    def eventFilter(self, obj, event):
        if obj is self._text_editor and _widget_alive(obj):
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self.handle_esc()
                return True
            if event.type() == QEvent.KeyPress and event.key() in (
                Qt.Key_Return,
                Qt.Key_Enter,
            ) and event.modifiers() & Qt.ControlModifier:
                self._commit_text_and_pen()
                return True
            if event.type() == QEvent.KeyPress and event.key() in (
                Qt.Key_CapsLock,
                Qt.Key_NumLock,
                Qt.Key_ScrollLock,
            ):
                return False
            if event.type() == QEvent.FocusOut:
                if self._text_ignore_focus:
                    return False
                self._finish_text(True)
                self.tool = TOOL_PEN
                self._update_cursor()
        if (
            not self._closing
            and self.isVisible()
            and not self._mouse_mode
            and not _widget_alive(self._text_editor)
        ):
            etype = event.type()
            if etype in (QEvent.KeyPress, QEvent.KeyRelease) and is_hold_key(event.key()):
                if etype == QEvent.KeyRelease and event.isAutoRepeat():
                    return super().eventFilter(obj, event)
                widget = obj if isinstance(obj, QWidget) else None
                if widget is None or widget is self or self.isAncestorOf(widget):
                    if event.key() == hold_qt_key("hold_circle"):
                        if etype == QEvent.KeyPress:
                            self._q_key_down = True
                        else:
                            self._q_key_down = hold_down("hold_circle")
                    elif event.key() == hold_qt_key("hold_triangle"):
                        if etype == QEvent.KeyPress:
                            self._caps_held = True
                        else:
                            self._caps_held = hold_down("hold_triangle")
                    else:
                        self._sync_shape_hold_keys()
                    if self._drawing and self._current is not None:
                        self._update_region(_item_bounds(self._current))
        return super().eventFilter(obj, event)

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

    def focusNextPrevChild(self, _next):
        return False

    def _erase_at(self, pos):
        radius = float(self.eraser_width)
        dirty = _point_bounds(pos, radius + 3.0)
        old_preview = self._eraser_preview_pos
        self._eraser_preview_pos = pos
        if old_preview is not None:
            dirty = dirty.united(_point_bounds(old_preview, radius + 3.0))
        remaining = []
        changed = False
        for item in self.items:
            if _item_hit(item, pos, radius):
                dirty = dirty.united(_item_bounds(item))
                changed = True
                continue
            remaining.append(item)
        if changed:
            self.items = remaining
        self._update_region(dirty)

    def _undo(self):
        if self._drawing:
            bounds = QRect()
            if self._current is not None:
                bounds = bounds.united(_item_bounds(self._current))
            if self._erase_snapshot is not None:
                bounds = bounds.united(self._union_item_bounds(self._erase_snapshot))
                bounds = bounds.united(self._union_item_bounds(self.items))
            self._drawing = False
            self._current = None
            if self._erase_snapshot is not None:
                self.items = self._erase_snapshot
                self._erase_snapshot = None
            self._update_region(bounds or None)
            return
        if self._text_editor is not None:
            self._finish_text(False)
            self.tool = TOOL_PEN
            self._update_cursor()
            self.update()
            return
        if not self._undo_stack:
            return
        action, payload = self._undo_stack.pop()
        if action == "add":
            if payload in self.items:
                bounds = _item_bounds(payload)
                self.items.remove(payload)
                self._update_region(bounds)
                return
        elif action == "restore":
            bounds = self._union_item_bounds(self.items)
            self.items = list(payload)
            self._update_region(bounds.united(self._union_item_bounds(self.items)))
            return
        self.update()

    def _begin_item(self, pos):
        self._sync_shape_hold_keys()
        if self.tool == TOOL_ERASER:
            self._drawing = True
            self._erase_snapshot = list(self.items)
            self._erase_at(pos)
            return
        if self.tool == TOOL_TEXT:
            self._start_text(pos)
            return
        shape = self._shape_from_modifiers(drawing=True)
        self._drawing = True
        width = self.pen_width
        color = QColor(self.pen_color)
        if shape == "stroke":
            self._current = {
                "type": "stroke",
                "color": color,
                "width": width,
                "points": [pos],
            }
        elif shape == "line":
            self._current = {"type": "line", "color": color, "width": width, "p1": pos, "p2": pos}
        elif shape == "arrow":
            self._current = {"type": "arrow", "color": color, "width": width, "p1": pos, "p2": pos}
        elif shape == "rect":
            self._current = {
                "type": "rect",
                "color": color,
                "width": width,
                "rect": _drag_rect(pos, pos),
            }
            self._current["_origin"] = pos
        elif shape == "circle":
            center, radius = _circle_from_drag(pos, pos)
            self._current = {
                "type": "circle",
                "color": color,
                "width": width,
                "center": center,
                "radius": radius,
                "_origin": pos,
            }
        elif shape == "triangle":
            self._current = {
                "type": "triangle",
                "color": color,
                "width": width,
                "points": _triangle_from_rect(QRectF(pos, pos)),
                "_origin": pos,
            }
        if self._current is not None:
            self._update_region(_item_bounds(self._current))

    def _update_item(self, pos):
        if not self._drawing:
            return
        if self.tool == TOOL_ERASER:
            self._erase_at(pos)
            return
        current = self._current
        if current is None:
            return
        kind = current["type"]
        if kind == "stroke":
            points = current["points"]
            last_pts = points[-4:] if points else []
            if not _append_point(points, pos):
                return
            pts = last_pts + [points[-1]]
            pad = float(current.get("width", self.pen_width)) * 0.5 + 8.0
            xs = [float(p.x()) for p in pts]
            ys = [float(p.y()) for p in pts]
            self._update_region(
                QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2.0 * pad, max(ys) - min(ys) + 2.0 * pad)
            )
            return
        old_bounds = _item_bounds(current)
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
        self._update_region(old_bounds.united(_item_bounds(current)))

    def _commit_item(self, pos):
        if not self._drawing:
            return
        self._update_item(pos)
        self._drawing = False
        current = self._current
        self._current = None
        if current is None:
            if self._erase_snapshot is not None and self._erase_snapshot != self.items:
                self._undo_stack.append(("restore", self._erase_snapshot))
            self._erase_snapshot = None
            return
        current.pop("_origin", None)
        kind = current["type"]
        if kind == "stroke" and not current.get("points"):
            return
        if kind == "circle":
            radius = float(current.get("radius", 0))
            if radius < 4:
                current["radius"] = MIN_CIRCLE_RADIUS
        if kind in ("square", "rect"):
            rect = current.get("rect")
            if rect is None or min(rect.width(), rect.height()) < 4:
                self._update_region(_item_bounds(current))
                return
        if kind in ("line", "arrow"):
            if math.hypot(current["p2"].x() - current["p1"].x(), current["p2"].y() - current["p1"].y()) < 6:
                self._update_region(_item_bounds(current))
                return
        if kind == "triangle":
            pts = current.get("points") or []
            if len(pts) < 3 or _dist2(pts[0], pts[1]) < 36:
                self._update_region(_item_bounds(current))
                return
        self.items.append(current)
        self._undo_stack.append(("add", current))
        self._update_region(_item_bounds(current))

    def _menu_contains_global(self, pos):
        menu = getattr(self, "_menu", None)
        if menu is None or not menu.isVisible():
            return False
        try:
            return menu.frameGeometry().contains(pos)
        except RuntimeError:
            return False

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self._mouse_mode:
            return
        if self._menu_contains_global(event.globalPos()):
            self._raise_menu()
            return
        if self._zoom_active and not self._zoom_locked:
            self._update_zoom_from_mouse(event.pos())
            self._lock_zoom()
            return
        self._begin_item(self._view_to_canvas(event.localPos()))

    def mouseMoveEvent(self, event):
        if self._mouse_mode:
            return
        if self._menu_contains_global(event.globalPos()):
            return
        if self._zoom_active and not self._zoom_locked:
            self._update_zoom_from_mouse(event.pos())
            self.update()
            return
        canvas_pos = self._view_to_canvas(event.localPos())
        if self._drawing:
            self._update_item(canvas_pos)
            return
        if self.tool == TOOL_ERASER:
            old = self._eraser_preview_pos
            self._eraser_preview_pos = canvas_pos
            dirty = _point_bounds(canvas_pos, self.eraser_width)
            if old is not None:
                dirty = dirty.united(_point_bounds(old, self.eraser_width))
            self._update_region(dirty)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._mouse_mode:
            return
        if not self._drawing and self._menu_contains_global(event.globalPos()):
            return
        if self._zoom_active and not self._zoom_locked:
            return
        if self._drawing:
            self._commit_item(self._view_to_canvas(event.localPos()))

    def keyPressEvent(self, event):
        key = event.key()
        if matches(event, "overlay_escape") or matches(event, "close_whiteboard"):
            self.handle_esc()
            return
        if matches(event, "overlay_mouse"):
            self.handle_space()
            return
        if matches(event, "undo"):
            editor = self._text_editor
            if _widget_alive(editor):
                try:
                    if editor.document().isUndoAvailable():
                        editor.undo()
                        return
                except RuntimeError:
                    self._text_editor = None
            self._undo()
            return
        if _widget_alive(self._text_editor):
            if matches(event, "overlay_escape") or matches(event, "close_whiteboard"):
                self.handle_esc()
                return
            if key in (
                Qt.Key_CapsLock,
                Qt.Key_NumLock,
                Qt.Key_ScrollLock,
                Qt.Key_Tab,
                Qt.Key_Backtab,
                Qt.Key_Shift,
                Qt.Key_Control,
                Qt.Key_Alt,
                Qt.Key_Meta,
            ):
                return
            if event.text():
                try:
                    self._text_editor.setFocus(Qt.OtherFocusReason)
                    forwarded = QKeyEvent(
                        event.type(),
                        event.key(),
                        event.modifiers(),
                        event.text(),
                        event.isAutoRepeat(),
                        event.count(),
                    )
                    QApplication.sendEvent(self._text_editor, forwarded)
                except RuntimeError:
                    self._text_editor = None
            event.accept()
            return
        if event.isAutoRepeat() and is_hold_key(key):
            return
        if matches(event, "brush_thicker"):
            self._nudge_brush(1)
            return
        if matches(event, "brush_thinner"):
            self._nudge_brush(-1)
            return
        if matches(event, "overlay_zoom"):
            self._enter_zoom()
            self._sync_menu()
            return
        if matches(event, "tool_eraser"):
            self._finish_text(False)
            self._sticky_shape = None
            self.tool = TOOL_ERASER
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        if matches(event, "tool_text"):
            self._finish_text(False)
            self._sticky_shape = None
            self.tool = TOOL_TEXT
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        if key in (Qt.Key_Shift, Qt.Key_Control):
            return
        if key == hold_qt_key("hold_circle"):
            self._q_key_down = True
            self.update()
            return
        if key == hold_qt_key("hold_triangle"):
            if self.tool == TOOL_TEXT or _widget_alive(self._text_editor):
                return
            self._caps_held = True
            self.update()
            return
        if matches(event, "color_black"):
            self.pen_color = QColor(COLOR_BLACK)
            self._color_label = "juoda"
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        if matches(event, "color_blue"):
            self.pen_color = QColor(COLOR_BLUE)
            self._color_label = "mėlyna"
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        if matches(event, "color_red"):
            self.pen_color = QColor(COLOR_RED)
            self._color_label = "raudona"
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        if matches(event, "color_green"):
            self.pen_color = QColor(COLOR_GREEN)
            self._color_label = "žalia"
            self._update_cursor()
            self._sync_menu()
            self.update()
            return
        super().keyPressEvent(event)

    def _nudge_brush(self, direction):
        if self.tool == TOOL_ERASER:
            self.eraser_width = clamp_eraser_width(
                self.eraser_width + direction * ERASER_WIDTH_STEP
            )
        else:
            self.pen_width = clamp_pen_width(self.pen_width + direction * PEN_WIDTH_STEP)
        self._update_cursor()
        self.update()

    def keyReleaseEvent(self, event):
        key = event.key()
        if event.isAutoRepeat():
            return
        if key == hold_qt_key("hold_circle"):
            self._q_key_down = hold_down("hold_circle")
            self.update()
            return
        if key == hold_qt_key("hold_triangle"):
            self._caps_held = hold_down("hold_triangle")
            self.update()
            return
        if key in (Qt.Key_Shift, Qt.Key_Control):
            return
        super().keyReleaseEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        _force_topmost(int(self.winId()), activate=not self._mouse_mode)
        self._raise_menu()

    def closeEvent(self, event):
        if self._closing:
            super().closeEvent(event)
            return
        self._closing = True
        if hasattr(self, "_hold_timer"):
            self._hold_timer.stop()
        menu = getattr(self, "_menu", None)
        if menu is not None:
            try:
                menu.close()
                menu.deleteLater()
            except RuntimeError:
                pass
            self._menu = None
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
            try:
                app.applicationStateChanged.disconnect(self._on_app_state)
            except TypeError:
                pass
        self._finish_text(False)
        self.releaseKeyboard()
        self.items = []
        self.closed.emit()
        super().closeEvent(event)

    def _draw_item(self, painter, item):
        color = item["color"]
        kind = item["type"]
        painter.setPen(_pen_for(color, item.get("width", self.pen_width)))
        painter.setBrush(Qt.NoBrush)
        if kind == "stroke":
            painter.drawPath(_stroke_path(item["points"]))
        elif kind == "line":
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
            r = item["radius"]
            painter.drawEllipse(center, r, r)
        elif kind == "triangle":
            painter.drawPolygon(QPolygonF(item["points"]))
        elif kind == "text":
            font = self._text_font()
            metrics = QFontMetrics(font)
            path = QPainterPath()
            y = float(item["pos"].y())
            x = float(item["pos"].x())
            for line in (item["text"] or "").split("\n"):
                path.addText(QPointF(x, y + metrics.ascent()), font, line)
                y += metrics.lineSpacing()
            painter.strokePath(path, QPen(QColor(255, 255, 255), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.fillPath(path, color)

    def paintEvent(self, event):
        painter = QPainter(self)
        _configure_painter(painter)
        dirty = event.rect()
        painter.setClipRect(dirty)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        if self._mouse_mode:
            painter.fillRect(dirty, QColor(0, 0, 0, 0))
        elif self._zoom_active:
            painter.fillRect(dirty, QColor(0, 0, 0, 0))
        else:
            # Alpha 1 keeps the overlay clickable; fully transparent pixels pass clicks to Chrome.
            painter.fillRect(dirty, QColor(0, 0, 0, 1))
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        def draw_visible_items():
            for item in self.items:
                if self._canvas_rect_to_view(_item_bounds(item)).intersects(dirty):
                    self._draw_item(painter, item)
            if self._current is not None:
                if self._canvas_rect_to_view(_item_bounds(self._current)).intersects(dirty):
                    self._draw_item(painter, self._current)
            self._draw_eraser_preview(painter, dirty)

        if self._zoom_active and self._zoom_bg is not None:
            src_w, src_h = self._zoom_src_size()
            source = QRectF(self._zoom_x, self._zoom_y, src_w, src_h)
            painter.drawPixmap(QRectF(self.rect()), self._zoom_bg, source)
            painter.save()
            painter.scale(ZOOM_SCALE, ZOOM_SCALE)
            painter.translate(-self._zoom_x, -self._zoom_y)
            draw_visible_items()
            painter.restore()
        else:
            draw_visible_items()

    def _draw_eraser_preview(self, painter, dirty=None):
        if self.tool != TOOL_ERASER or self._mouse_mode:
            return
        pos = self._eraser_preview_pos
        if pos is None:
            pos = self._view_to_canvas(self.mapFromGlobal(QCursor.pos()))
        if dirty is not None:
            preview = self._canvas_rect_to_view(_point_bounds(pos, self.eraser_width))
            if not preview.intersects(dirty):
                return
        radius = float(self.eraser_width)
        painter.setPen(QPen(QColor(80, 80, 80, 200), 1.2))
        painter.setBrush(QColor(255, 255, 255, 40))
        painter.drawEllipse(pos, radius, radius)
