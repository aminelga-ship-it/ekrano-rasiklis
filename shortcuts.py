"""Persisted, user-editable keyboard shortcuts for the whiteboard and overlay."""

import json
import os
import sys

from app_paths import data_dir

import keyboard
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

try:
    import ctypes
except Exception:
    ctypes = None


MOD_MASK = Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier

KEY_NAMES = {
    Qt.Key_Escape: "Esc",
    Qt.Key_Tab: "Tab",
    Qt.Key_Backspace: "Backspace",
    Qt.Key_Return: "Enter",
    Qt.Key_Enter: "Enter",
    Qt.Key_Insert: "Insert",
    Qt.Key_Delete: "Delete",
    Qt.Key_Home: "Home",
    Qt.Key_End: "End",
    Qt.Key_Left: "←",
    Qt.Key_Right: "→",
    Qt.Key_Up: "↑",
    Qt.Key_Down: "↓",
    Qt.Key_Space: "Space",
    Qt.Key_CapsLock: "Caps",
    Qt.Key_Shift: "Shift",
    Qt.Key_Control: "Ctrl",
    Qt.Key_Alt: "Alt",
    Qt.Key_Meta: "Win",
    Qt.Key_Plus: "+",
    Qt.Key_Minus: "−",
    Qt.Key_Equal: "=",
    Qt.Key_Underscore: "_",
    Qt.Key_Asterisk: "*",
    Qt.Key_Slash: "/",
    Qt.Key_Backslash: "\\",
    Qt.Key_Period: ".",
    Qt.Key_Comma: ",",
    Qt.Key_Semicolon: ";",
    Qt.Key_QuoteLeft: "`",
    Qt.Key_BraceLeft: "[",
    Qt.Key_BraceRight: "]",
    Qt.Key_F1: "F1",
    Qt.Key_F2: "F2",
    Qt.Key_F3: "F3",
    Qt.Key_F4: "F4",
    Qt.Key_F5: "F5",
    Qt.Key_F6: "F6",
    Qt.Key_F7: "F7",
    Qt.Key_F8: "F8",
    Qt.Key_F9: "F9",
    Qt.Key_F10: "F10",
    Qt.Key_F11: "F11",
    Qt.Key_F12: "F12",
}

QT_TO_VK = {
    Qt.Key_Backspace: 0x08,
    Qt.Key_Tab: 0x09,
    Qt.Key_Return: 0x0D,
    Qt.Key_Enter: 0x0D,
    Qt.Key_Shift: 0x10,
    Qt.Key_Control: 0x11,
    Qt.Key_Alt: 0x12,
    Qt.Key_CapsLock: 0x14,
    Qt.Key_Escape: 0x1B,
    Qt.Key_Space: 0x20,
    Qt.Key_Delete: 0x2E,
    Qt.Key_Insert: 0x2D,
    Qt.Key_Home: 0x24,
    Qt.Key_End: 0x23,
    Qt.Key_Left: 0x25,
    Qt.Key_Up: 0x26,
    Qt.Key_Right: 0x27,
    Qt.Key_Down: 0x28,
    Qt.Key_Plus: 0xBB,
    Qt.Key_Equal: 0xBB,
    Qt.Key_Minus: 0xBD,
    Qt.Key_Underscore: 0xBD,
    Qt.Key_Slash: 0xBF,
    Qt.Key_Asterisk: 0x6A,
}

DEFAULTS = {
    "color_black": {"key": int(Qt.Key_K), "mods": 0},
    "color_blue": {"key": int(Qt.Key_B), "mods": 0},
    "color_red": {"key": int(Qt.Key_R), "mods": 0},
    "color_green": {"key": int(Qt.Key_G), "mods": 0},
    "tool_eraser": {"key": int(Qt.Key_E), "mods": 0},
    "tool_text": {"key": int(Qt.Key_T), "mods": 0},
    "undo": {"key": int(Qt.Key_Z), "mods": int(Qt.ControlModifier)},
    "paste": {"key": int(Qt.Key_V), "mods": int(Qt.ControlModifier)},
    "delete": {"key": int(Qt.Key_Delete), "mods": 0, "aliases": [int(Qt.Key_Backspace)]},
    "brush_thicker": {
        "key": int(Qt.Key_Plus),
        "mods": int(Qt.ControlModifier),
        "aliases": [int(Qt.Key_Equal)],
    },
    "brush_thinner": {
        "key": int(Qt.Key_Minus),
        "mods": int(Qt.ControlModifier),
        "aliases": [int(Qt.Key_Underscore)],
    },
    "scale_up": {"key": int(Qt.Key_Plus), "mods": 0, "aliases": [int(Qt.Key_Equal)]},
    "scale_down": {"key": int(Qt.Key_Minus), "mods": 0, "aliases": [int(Qt.Key_Underscore)]},
    "overlay_zoom": {"key": int(Qt.Key_Plus), "mods": 0, "aliases": [int(Qt.Key_Equal)]},
    "overlay_mouse": {"key": int(Qt.Key_Space), "mods": 0},
    "close_whiteboard": {"key": int(Qt.Key_Escape), "mods": 0},
    "overlay_escape": {"key": int(Qt.Key_Escape), "mods": 0},
    "hold_line": {"key": int(Qt.Key_Shift), "mods": 0},
    "hold_rect": {"key": int(Qt.Key_Control), "mods": 0},
    "hold_circle": {"key": int(Qt.Key_Q), "mods": 0},
    "hold_triangle": {"key": int(Qt.Key_CapsLock), "mods": 0},
    "open_whiteboard": {"combo": ["*", "-"]},
    "open_overlay": {"combo": ["*", "/"]},
}

GROUPS = (
    (
        "Programa",
        (
            ("open_whiteboard", "Atidaryti lentą"),
            ("open_overlay", "Piešti ant langų"),
            ("close_whiteboard", "Uždaryti lentą"),
        ),
    ),
    (
        "Lenta ir piešimas",
        (
            ("color_black", "Juoda spalva"),
            ("color_blue", "Mėlyna spalva"),
            ("color_red", "Raudona spalva"),
            ("color_green", "Žalia spalva"),
            ("tool_eraser", "Trintukas"),
            ("tool_text", "Tekstas"),
            ("undo", "Atšaukti"),
            ("paste", "Įklijuoti paveikslėlį"),
            ("delete", "Ištrinti pažymėtą"),
            ("brush_thicker", "Storesnis pieštukas / trintukas"),
            ("brush_thinner", "Plonesnis pieštukas / trintukas"),
            ("scale_up", "Padidinti pažymėtą objektą"),
            ("scale_down", "Sumažinti pažymėtą objektą"),
        ),
    ),
    (
        "Figūros (laikant klavišą ir tempiant)",
        (
            ("hold_line", "Linija"),
            ("hold_rect", "Stačiakampis"),
            ("hold_arrow", "Rodyklė"),
            ("hold_circle", "Apskritimas"),
            ("hold_triangle", "Trikampis"),
        ),
    ),
    (
        "Piešimas ant langų",
        (
            ("overlay_zoom", "Priartinti"),
            ("overlay_mouse", "Pelės režimas / grįžti piešti"),
            ("overlay_escape", "Atgal / uždaryti piešimą ant langų"),
        ),
    ),
)

HOLD_IDS = ("hold_line", "hold_rect", "hold_circle", "hold_triangle")
COMBO_IDS = ("open_whiteboard", "open_overlay")
OVERLAY_ONLY = ("overlay_zoom", "overlay_mouse", "overlay_escape")
WHITEBOARD_ONLY = ("paste", "delete", "scale_up", "scale_down", "close_whiteboard")
SHARED_IDS = (
    "color_black",
    "color_blue",
    "color_red",
    "color_green",
    "tool_eraser",
    "tool_text",
    "undo",
    "brush_thicker",
    "brush_thinner",
)

_values = {}


def settings_path():
    return os.path.join(data_dir(), "shortcuts.json")


def _copy_spec(spec):
    copied = dict(spec)
    if "aliases" in copied:
        copied["aliases"] = list(copied["aliases"])
    if "combo" in copied:
        copied["combo"] = list(copied["combo"])
    return copied


def load():
    global _values
    _values = {key: _copy_spec(spec) for key, spec in DEFAULTS.items()}
    path = settings_path()
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    for key, spec in data.items():
        if key not in DEFAULTS or not isinstance(spec, dict):
            continue
        merged = _copy_spec(DEFAULTS[key])
        if "combo" in merged:
            combo = spec.get("combo")
            if isinstance(combo, (list, tuple)) and 1 <= len(combo) <= 2:
                merged["combo"] = [str(item) for item in combo]
        else:
            if "key" in spec:
                merged["key"] = int(spec["key"])
            if "mods" in spec:
                merged["mods"] = int(spec["mods"])
        _values[key] = merged


def save():
    payload = {}
    for key, spec in _values.items():
        if "combo" in spec:
            payload[key] = {"combo": list(spec["combo"])}
        else:
            payload[key] = {"key": int(spec["key"]), "mods": int(spec.get("mods", 0))}
    path = settings_path()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def reset_defaults():
    global _values
    _values = {key: _copy_spec(spec) for key, spec in DEFAULTS.items()}
    save()


def get(action_id):
    return _values.get(action_id) or _copy_spec(DEFAULTS[action_id])


def set_key(action_id, key, mods=0):
    spec = get(action_id)
    spec["key"] = int(key)
    spec["mods"] = int(mods) & int(MOD_MASK)
    spec.pop("combo", None)
    _values[action_id] = spec
    save()


def set_combo(action_id, combo):
    spec = get(action_id)
    spec["combo"] = [str(item) for item in combo]
    _values[action_id] = spec
    save()


def key_name(key):
    key = int(key)
    if key in KEY_NAMES:
        return KEY_NAMES[key]
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key)
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    text = QKeySequence(key).toString(QKeySequence.NativeText)
    return text or "Klavišas"


def format_spec(spec):
    if spec.get("combo"):
        return " + ".join(token.upper() if len(token) == 1 else token for token in spec["combo"])
    parts = []
    mods = int(spec.get("mods", 0))
    if mods & Qt.ControlModifier:
        parts.append("Ctrl")
    if mods & Qt.ShiftModifier:
        parts.append("Shift")
    if mods & Qt.AltModifier:
        parts.append("Alt")
    parts.append(key_name(spec.get("key", 0)))
    return " + ".join(parts)


def format_action(action_id):
    return format_spec(get(action_id))


def _event_mods(event):
    return int(event.modifiers() & MOD_MASK)


def matches(event, action_id):
    spec = get(action_id)
    if spec.get("combo"):
        return False
    key = int(event.key())
    if key in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_CapsLock):
        return False
    keys = [int(spec.get("key", 0))] + [int(alias) for alias in spec.get("aliases", [])]
    if key not in keys:
        return False
    return _event_mods(event) == int(spec.get("mods", 0))


def matches_any(event, action_ids):
    return any(matches(event, action_id) for action_id in action_ids)


def qt_to_vk(key):
    key = int(key)
    if key in QT_TO_VK:
        return QT_TO_VK[key]
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        return key
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return 0x70 + (key - Qt.Key_F1)
    return None


def _win_key_down(vk):
    if sys.platform != "win32" or ctypes is None or vk is None:
        return False
    vk = int(vk)
    try:
        user32 = ctypes.windll.user32
        async_state = int(user32.GetAsyncKeyState(vk)) & 0xFFFF
        if async_state & 0x8000:
            return True
        state = (ctypes.c_ubyte * 256)()
        if user32.GetKeyboardState(state) and state[vk] & 0x80:
            return True
    except Exception:
        pass
    return False


def _hook_key_down(qt_key):
    qt_key = int(qt_key)
    names = []
    if Qt.Key_A <= qt_key <= Qt.Key_Z:
        names = [chr(qt_key).lower()]
    elif qt_key == Qt.Key_CapsLock:
        names = ["caps lock", "capslock"]
    elif qt_key in KEY_NAMES:
        names = [KEY_NAMES[qt_key].lower()]
    for name in names:
        try:
            if keyboard.is_pressed(name):
                return True
        except Exception:
            continue
    return False


def hold_qt_key(action_id):
    return int(get(action_id).get("key", 0))


def hold_keys():
    return {hold_qt_key(action_id) for action_id in ("hold_circle", "hold_triangle")}


def is_hold_key(key):
    return int(key) in hold_keys()


def _modifier_flag(key):
    if key == Qt.Key_Shift:
        return Qt.ShiftModifier
    if key == Qt.Key_Control:
        return Qt.ControlModifier
    if key == Qt.Key_Alt:
        return Qt.AltModifier
    if key == Qt.Key_Meta:
        return Qt.MetaModifier
    return None


def hold_down(action_id, tracked=False):
    spec = get(action_id)
    key = int(spec.get("key", 0))
    flag = _modifier_flag(key)
    if flag is not None:
        mods = QApplication.queryKeyboardModifiers()
        if mods & flag:
            return True
        return _win_key_down(qt_to_vk(key)) or _hook_key_down(key)
    vk = qt_to_vk(key)
    if _win_key_down(vk) or _hook_key_down(key):
        return True
    return bool(tracked)


def shape_from_holds(circle_tracked=False, triangle_tracked=False):
    line = hold_down("hold_line")
    rect = hold_down("hold_rect")
    if line and rect:
        return "arrow"
    if rect:
        return "rect"
    if line:
        return "line"
    if hold_down("hold_circle", tracked=circle_tracked):
        return "circle"
    if hold_down("hold_triangle", tracked=triangle_tracked):
        return "triangle"
    return "stroke"


def sync_hold_state():
    return hold_down("hold_circle"), hold_down("hold_triangle")


def _token_pressed(token):
    token = str(token).strip().lower()
    names = [token]
    if token in ("*", "asterisk", "star"):
        names = ("*", "multiply")
        if keyboard.is_pressed("8") and (
            keyboard.is_pressed("shift")
            or keyboard.is_pressed("left shift")
            or keyboard.is_pressed("right shift")
        ):
            return True
    elif token in ("-", "minus"):
        names = ("-", "minus", "subtract")
    elif token in ("/", "slash"):
        names = ("/", "?", "slash", "divide", "oem_2")
    for name in names:
        try:
            if keyboard.is_pressed(name):
                return True
        except Exception:
            continue
    return False


def combo_pressed(action_id):
    spec = get(action_id)
    combo = spec.get("combo") or []
    if not combo:
        return False
    return all(_token_pressed(token) for token in combo)


def qt_event_to_combo_token(event):
    key = int(event.key())
    text = (event.text() or "").strip()
    if key in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_CapsLock):
        return None
    if text in ("*", "/", "-", "+", "=", "?"):
        return "*" if text == "?" else text
    if key in (Qt.Key_Asterisk, Qt.Key_multiply):
        return "*"
    if key in (Qt.Key_Slash,):
        return "/"
    if key in (Qt.Key_Minus, Qt.Key_Underscore):
        return "-"
    if Qt.Key_F1 <= key <= Qt.Key_F12:
        return "f%d" % (key - Qt.Key_F1 + 1)
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key).lower()
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    name = key_name(key).lower()
    return name.replace(" ", "")


def _conflict_ids(action_id):
    if action_id in COMBO_IDS:
        return COMBO_IDS
    if action_id in HOLD_IDS:
        return HOLD_IDS
    if action_id in OVERLAY_ONLY:
        return OVERLAY_ONLY + SHARED_IDS
    if action_id in WHITEBOARD_ONLY:
        return WHITEBOARD_ONLY + SHARED_IDS
    return SHARED_IDS + WHITEBOARD_ONLY + OVERLAY_ONLY


def find_conflict(action_id, spec):
    for other_id in _conflict_ids(action_id):
        if other_id == action_id:
            continue
        other = get(other_id)
        if spec.get("combo"):
            if other.get("combo") and [token.lower() for token in other["combo"]] == [
                token.lower() for token in spec["combo"]
            ]:
                return other_id
            continue
        if other.get("combo"):
            continue
        if int(other.get("key", -1)) == int(spec.get("key", -2)) and int(other.get("mods", 0)) == int(
            spec.get("mods", 0)
        ):
            return other_id
    return None


class ShortcutButton(QPushButton):
    changed = pyqtSignal()

    def __init__(self, action_id, parent=None):
        super().__init__(parent)
        self.action_id = action_id
        self.capturing = False
        self._combo_tokens = []
        self._combo_timer = QTimer(self)
        self._combo_timer.setSingleShot(True)
        self._combo_timer.setInterval(900)
        self._combo_timer.timeout.connect(self._finish_combo)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMinimumWidth(132)
        self.clicked.connect(self.start_capture)
        self.refresh()

    def refresh(self):
        self.capturing = False
        self._combo_tokens = []
        self.setText(format_action(self.action_id))

    def start_capture(self):
        self.capturing = True
        self._combo_tokens = []
        self._combo_timer.stop()
        if self.action_id in COMBO_IDS:
            self.setText("Paspauskite 1–2 klavišus…")
        else:
            self.setText("Paspauskite klavišą…")
        self.grabKeyboard()
        self.setFocus(Qt.OtherFocusReason)

    def _finish_combo(self):
        self._combo_timer.stop()
        self.releaseKeyboard()
        tokens = [token for token in self._combo_tokens if token]
        if not tokens:
            self.refresh()
            return
        spec = {"combo": tokens[:2]}
        conflict = find_conflict(self.action_id, spec)
        if conflict:
            QMessageBox.information(
                self,
                "Užimta",
                "Šį derinį jau naudoja: %s." % _label_for(conflict),
            )
            self.refresh()
            return
        set_combo(self.action_id, spec["combo"])
        self.refresh()
        self.changed.emit()

    def keyPressEvent(self, event):
        if not self.capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key_Escape,):
            self.releaseKeyboard()
            self.refresh()
            event.accept()
            return
        if self.action_id in COMBO_IDS:
            token = qt_event_to_combo_token(event)
            if token is None:
                event.accept()
                return
            if token not in self._combo_tokens:
                self._combo_tokens.append(token)
            self.setText(" + ".join(item.upper() for item in self._combo_tokens))
            if len(self._combo_tokens) >= 2:
                self._combo_timer.stop()
                self.releaseKeyboard()
                self._finish_combo()
            else:
                self._combo_timer.start()
            event.accept()
            return
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta) and self.action_id not in HOLD_IDS:
            event.accept()
            return
        spec = {"key": int(key), "mods": 0 if self.action_id in HOLD_IDS else _event_mods(event)}
        if self.action_id in HOLD_IDS:
            spec["mods"] = 0
        conflict = find_conflict(self.action_id, spec)
        if conflict:
            QMessageBox.information(
                self,
                "Užimta",
                "Šį klavišą jau naudoja: %s." % _label_for(conflict),
            )
            self.releaseKeyboard()
            self.refresh()
            event.accept()
            return
        self.releaseKeyboard()
        set_key(self.action_id, spec["key"], spec["mods"])
        self.refresh()
        self.changed.emit()
        event.accept()

    def focusOutEvent(self, event):
        if self.capturing:
            if self.action_id in COMBO_IDS and self._combo_tokens:
                self._finish_combo()
            else:
                self.refresh()
            self.releaseKeyboard()
        super().focusOutEvent(event)


def _label_for(action_id):
    for _title, rows in GROUPS:
        for row_id, label in rows:
            if row_id == action_id:
                return label
    return action_id


class CheatSheetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Atmintinė")
        self.setModal(True)
        self.setMinimumSize(560, 620)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTitleHint
            | Qt.WindowCloseButtonHint
        )
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setStyleSheet(
            "QDialog { background: #f7f7f7; }"
            "QLabel#group { font-weight: 600; color: #333; padding-top: 8px; }"
            "QLabel#desc { color: #333; }"
            "QPushButton#key { background: white; border: 1px solid #c8c8c8; border-radius: 6px; padding: 6px 10px; }"
            "QPushButton#key:hover { background: #eef5ff; border-color: #7eb6ff; }"
            "QLabel#derived { background: white; border: 1px solid #c8c8c8; border-radius: 6px; padding: 6px 10px; min-width: 132px; }"
        )

        root = QVBoxLayout(self)
        intro = QLabel(
            "Visi sparčiųjų klavišų veiksmai. Spustelėkite klavišą dešinėje ir paspauskite naują derinį."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(4, 4, 12, 4)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        row = 0
        self._buttons = []
        self._arrow_label = None
        for title, actions in GROUPS:
            if title == "Piešimas ant langų":
                continue
            heading = QLabel(title)
            heading.setObjectName("group")
            grid.addWidget(heading, row, 0, 1, 2)
            row += 1
            for action_id, label in actions:
                desc = QLabel(label)
                desc.setObjectName("desc")
                grid.addWidget(desc, row, 0)
                if action_id == "hold_arrow":
                    derived = QLabel()
                    derived.setObjectName("derived")
                    derived.setAlignment(Qt.AlignCenter)
                    grid.addWidget(derived, row, 1)
                    self._arrow_label = derived
                else:
                    button = ShortcutButton(action_id)
                    button.setObjectName("key")
                    button.changed.connect(self._refresh_derived)
                    grid.addWidget(button, row, 1)
                    self._buttons.append(button)
                row += 1
        grid.setColumnStretch(0, 1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        reset = QPushButton("Atstatyti numatytuosius")
        reset.setFocusPolicy(Qt.NoFocus)
        reset.clicked.connect(self._reset)
        root.addWidget(reset, 0, Qt.AlignLeft)
        self._refresh_derived()

    def _refresh_derived(self):
        if self._arrow_label is not None:
            self._arrow_label.setText(
                "%s + %s" % (format_action("hold_line"), format_action("hold_rect"))
            )

    def _reset(self):
        reset_defaults()
        for button in self._buttons:
            button.refresh()
        self._refresh_derived()

    def keyPressEvent(self, event):
        focused = self.focusWidget()
        if isinstance(focused, ShortcutButton) and focused.capturing:
            focused.keyPressEvent(event)
            return
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


load()
