"""7-day trial and one-time Stripe license for the frozen .exe only."""

import hashlib
import hmac
import json
import math
import os
import re
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import quote

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app_paths import data_dir, is_frozen, project_dir

TRIAL_DAYS = 7
PRICE_LABEL = "9,99 €"
PRODUCT_ID = "ekrano-rasiklis-v1"
LICENSE_SECRET = b"er-lic-v1-9f3c7a1e4b8d2f06c5a91e77b0d34c8e2a16f5b9"
STRIPE_PAYMENT_URL = "https://buy.stripe.com/aFa3cn9kg1fr2WMbum8k800"
REG_PATH = r"Software\EkranoRasiklis"


def _license_path():
    return os.path.join(data_dir(), "license.json")


def _url_candidates():
    return (
        os.path.join(project_dir(), "stripe_url.txt"),
        os.path.join(data_dir(), "stripe_url.txt"),
    )


def payment_url():
    for path in _url_candidates():
        try:
            with open(path, "r", encoding="utf-8") as handle:
                url = handle.read().strip()
            if url.startswith("https://"):
                return url
        except OSError:
            continue
    return (STRIPE_PAYMENT_URL or "").strip()


def _now():
    return datetime.now()


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _load_file_state():
    try:
        with open(_license_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {}


def _save_file_state(data):
    payload = dict(data)
    tmp = _license_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, _license_path())


def _reg_read():
    if os.name != "nt":
        return {}
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        out = {}
        for name in ("FirstSeen", "LastSeen", "License", "Email"):
            try:
                value, _ = winreg.QueryValueEx(key, name)
                if value:
                    out[name.lower()] = str(value)
            except OSError:
                pass
        winreg.CloseKey(key)
        return out
    except OSError:
        return {}


def _reg_write(first_seen, last_seen, email="", license_key=""):
    if os.name != "nt":
        return
    try:
        import winreg

        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        winreg.SetValueEx(key, "FirstSeen", 0, winreg.REG_SZ, first_seen)
        winreg.SetValueEx(key, "LastSeen", 0, winreg.REG_SZ, last_seen)
        if email:
            winreg.SetValueEx(key, "Email", 0, winreg.REG_SZ, email)
        if license_key:
            winreg.SetValueEx(key, "License", 0, winreg.REG_SZ, license_key)
        winreg.CloseKey(key)
    except OSError:
        pass


def _normalize_email(email):
    return re.sub(r"\s+", "", str(email or "").strip().lower())


def _normalize_key(key):
    return re.sub(r"[^A-Za-z0-9]", "", str(key or "")).upper()


def make_license_key(email):
    email = _normalize_email(email)
    digest = hmac.new(
        LICENSE_SECRET,
        ("%s|%s" % (PRODUCT_ID, email)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20].upper()
    chunks = [digest[i : i + 4] for i in range(0, 20, 4)]
    return "ER1-" + "-".join(chunks)


def license_key_valid(email, key):
    email = _normalize_email(email)
    if not email or "@" not in email:
        return False
    expected = _normalize_key(make_license_key(email))
    given = _normalize_key(key)
    return bool(given) and hmac.compare_digest(expected, given)


def activate_license(email, key):
    email = _normalize_email(email)
    key = str(key or "").strip()
    if not license_key_valid(email, key):
        return False
    now = _now().isoformat(timespec="seconds")
    state = _load_file_state()
    first = state.get("first_seen") or now
    state.update(
        {
            "first_seen": first,
            "last_seen": now,
            "email": email,
            "license": key,
        }
    )
    _save_file_state(state)
    _reg_write(first, now, email, key)
    return True


def _merge_state():
    file_state = _load_file_state()
    reg_state = _reg_read()
    first_candidates = [
        _parse_dt(file_state.get("first_seen")),
        _parse_dt(reg_state.get("firstseen")),
    ]
    last_candidates = [
        _parse_dt(file_state.get("last_seen")),
        _parse_dt(reg_state.get("lastseen")),
    ]
    first_seen = min([item for item in first_candidates if item], default=None)
    last_seen = max([item for item in last_candidates if item], default=None)
    email = file_state.get("email") or reg_state.get("email") or ""
    key = file_state.get("license") or reg_state.get("license") or ""
    return first_seen, last_seen, email, key


def _touch_trial(first_seen, last_seen):
    now = _now()
    if first_seen is None:
        first_seen = now
    if last_seen is None:
        last_seen = now
    elif now + timedelta(hours=12) >= last_seen:
        last_seen = max(last_seen, now)
    first_iso = first_seen.replace(microsecond=0).isoformat()
    last_iso = last_seen.replace(microsecond=0).isoformat()
    state = _load_file_state()
    if not state.get("license"):
        state["first_seen"] = first_iso
        state["last_seen"] = last_iso
        _save_file_state(state)
        _reg_write(first_iso, last_iso, state.get("email", ""), state.get("license", ""))
    return first_seen, last_seen


def license_status():
    """Return dict: licensed, trial_active, trial_expired, days_left, email."""
    if not is_frozen():
        return {
            "licensed": True,
            "trial_active": False,
            "trial_expired": False,
            "days_left": TRIAL_DAYS,
            "email": "",
            "needs_unlock": False,
        }
    first_seen, last_seen, email, key = _merge_state()
    if email and key and license_key_valid(email, key):
        return {
            "licensed": True,
            "trial_active": False,
            "trial_expired": False,
            "days_left": 0,
            "email": email,
            "needs_unlock": False,
        }
    first_seen, last_seen = _touch_trial(first_seen, last_seen)
    now = _now()
    ends = first_seen + timedelta(days=TRIAL_DAYS)
    remaining = ends - now
    expired = remaining.total_seconds() <= 0 or (last_seen and now + timedelta(hours=12) < last_seen)
    if expired:
        days_left = 0
    else:
        days_left = max(1, int(math.ceil(remaining.total_seconds() / 86400.0)))
        days_left = min(TRIAL_DAYS, days_left)
    return {
        "licensed": False,
        "trial_active": not expired,
        "trial_expired": expired,
        "days_left": days_left,
        "email": email,
        "needs_unlock": expired,
    }


class LicenseDialog(QDialog):
    def __init__(self, status, parent=None):
        super().__init__(parent)
        self._status = status
        self.setWindowTitle("Ekrano rašiklis")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        font = QFont("Segoe UI")
        if not font.exactMatch():
            font = QFont("Arial")
        self.setFont(font)
        expired = bool(status.get("trial_expired"))
        self.setStyleSheet(
            "QFrame#card { background: #ffffff; border: 1px solid #e6ebf2; border-radius: 24px; }"
            "QLabel#eyebrow { font-size: 13px; font-weight: 600; color: #64748b; letter-spacing: 0.4px; }"
            "QLabel#title { font-size: 26px; font-weight: 800; color: #0f172a; }"
            "QLabel#body { font-size: 15px; color: #475569; }"
            "QLabel#fieldLabel { font-size: 13px; font-weight: 600; color: #334155; }"
            "QLineEdit { border: 1px solid #d8e0ea; border-radius: 12px; padding: 10px 12px; "
            "font-size: 15px; color: #0f172a; background: #f8fafc; }"
            "QLineEdit:focus { border: 1px solid #93c5fd; background: #ffffff; }"
            "QPushButton#buy { background: #1d4ed8; color: white; border: none; border-radius: 12px; "
            "padding: 12px 18px; font-size: 15px; font-weight: 700; }"
            "QPushButton#buy:hover { background: #1e40af; }"
            "QPushButton#unlock { background: #16a34a; color: white; border: none; border-radius: 12px; "
            "padding: 12px 18px; font-size: 15px; font-weight: 700; }"
            "QPushButton#unlock:hover { background: #15803d; }"
            "QPushButton#later { background: transparent; color: #64748b; border: none; "
            "padding: 8px; font-size: 14px; }"
        )

        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(24, 18, 24, 24)
        card = QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(480)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(15, 23, 42, 48))
        card.setGraphicsEffect(shadow)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(32, 28, 32, 24)
        inner.setSpacing(0)

        eyebrow = QLabel("EKRANO RAŠIKLIS")
        eyebrow.setObjectName("eyebrow")
        eyebrow.setAlignment(Qt.AlignCenter)
        inner.addWidget(eyebrow)
        inner.addSpacing(8)

        title = QLabel("Bandomasis laikotarpis baigėsi" if expired else "7 dienų bandymas")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        inner.addWidget(title)
        inner.addSpacing(8)

        if expired:
            body_text = (
                "Norint toliau naudoti programą, reikalingas vienkartinis apmokėjimas (%s).\n"
                "Po mokėjimo įveskite tą patį el. paštą ir gautą licencijos raktą."
            ) % PRICE_LABEL
        else:
            body_text = (
                "Liko %s d. nemokamai. Vėliau — vienkartinis mokestis %s."
            ) % (status.get("days_left", TRIAL_DAYS), PRICE_LABEL)
        body = QLabel(body_text)
        body.setObjectName("body")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        inner.addWidget(body)
        inner.addSpacing(18)

        email_label = QLabel("El. paštas")
        email_label.setObjectName("fieldLabel")
        inner.addWidget(email_label)
        inner.addSpacing(4)
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("vardas@pastas.lt")
        self.email_edit.setText(status.get("email") or "")
        inner.addWidget(self.email_edit)
        inner.addSpacing(10)

        key_label = QLabel("Licencijos raktas")
        key_label.setObjectName("fieldLabel")
        inner.addWidget(key_label)
        inner.addSpacing(4)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("ER1-XXXX-XXXX-XXXX-XXXX-XXXX")
        inner.addWidget(self.key_edit)
        inner.addSpacing(18)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buy = QPushButton("Pirkti %s" % PRICE_LABEL)
        buy.setObjectName("buy")
        buy.setCursor(Qt.PointingHandCursor)
        buy.clicked.connect(self._buy)
        unlock = QPushButton("Atrakinti")
        unlock.setObjectName("unlock")
        unlock.setCursor(Qt.PointingHandCursor)
        unlock.clicked.connect(self._unlock)
        buttons.addWidget(buy)
        buttons.addWidget(unlock)
        inner.addLayout(buttons)

        if not expired:
            later = QPushButton("Tęsti bandymą")
            later.setObjectName("later")
            later.setCursor(Qt.PointingHandCursor)
            later.clicked.connect(self.accept)
            inner.addSpacing(6)
            inner.addWidget(later, 0, Qt.AlignHCenter)
        else:
            quit_btn = QPushButton("Išeiti")
            quit_btn.setObjectName("later")
            quit_btn.setCursor(Qt.PointingHandCursor)
            quit_btn.clicked.connect(self.reject)
            inner.addSpacing(6)
            inner.addWidget(quit_btn, 0, Qt.AlignHCenter)

        wrap.addWidget(card)

    def _buy(self):
        url = payment_url()
        if not url:
            QMessageBox.information(
                self,
                "Mokėjimas",
                "Stripe mokėjimo nuoroda dar nenustatyta.",
            )
            return
        email = _normalize_email(self.email_edit.text())
        if email and "@" in email:
            joiner = "&" if "?" in url else "?"
            url = url + joiner + "prefilled_email=" + quote(email)
        webbrowser.open(url)

    def _unlock(self):
        email = self.email_edit.text()
        key = self.key_edit.text()
        if activate_license(email, key):
            self.accept()
            return
        QMessageBox.warning(
            self,
            "Netinka",
            "El. paštas arba licencijos raktas neteisingas.",
        )


def ensure_licensed(parent=None):
    """Return True if the frozen app may continue. Source builds always pass."""
    status = license_status()
    if not status["needs_unlock"]:
        return True
    dialog = LicenseDialog(status, parent)
    dialog.exec_()
    return bool(license_status()["licensed"])
