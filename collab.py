"""
Two-pencil collaboration: local HTTP+WebSocket server and optional public tunnel.
The guest opens a link in any chat app and draws in the browser.
"""

import base64
import hashlib
import json
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse

from PyQt5.QtCore import QObject, pyqtSignal

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PUBLIC_URL_RE = re.compile(
    r"https://(?:[a-zA-Z0-9]{6,}\.lhr\.life|[a-zA-Z0-9-]+\.trycloudflare\.com|[a-zA-Z0-9.-]+\.pinggy(?:-free)?\.link)"
)

SESSION_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "WhiteboardTool")
SESSION_FILE = os.path.join(SESSION_DIR, "collab_session.json")


def _session_dir():
    os.makedirs(SESSION_DIR, exist_ok=True)
    return SESSION_DIR


def load_session_config():
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        token = str(data.get("token", "")).strip()
        port = int(data.get("port", 0) or 0)
        if token:
            return {"token": token, "port": port}
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        pass
    return {}


def save_session_config(token, port):
    _session_dir()
    payload = {"token": token, "port": int(port)}
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, SESSION_FILE)


def clear_session_config():
    try:
        os.remove(SESSION_FILE)
    except OSError:
        pass


def _ws_accept(key):
    digest = hashlib.sha1((key + WS_GUID).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def _ws_send(sock, payload, opcode=0x1, lock=None):
    data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    header = bytearray()
    header.append(0x80 | (opcode & 0x0F))
    length = len(data)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    packet = bytes(header) + data
    if lock is None:
        sock.sendall(packet)
        return
    with lock:
        sock.sendall(packet)


class _SockReader:
    def __init__(self, sock, leftover=b""):
        self.sock = sock
        self.buf = bytearray(leftover)

    def pending(self):
        return bool(self.buf)

    def read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self.buf)))
            if not chunk:
                raise ConnectionError("closed")
            self.buf.extend(chunk)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out


def _ws_recv_message(reader, send_lock=None):
    """Read one text message, including frames a tunnel splits into pieces."""
    fragments = []
    message_opcode = None
    while True:
        header = reader.read(2)
        opcode = header[0] & 0x0F
        fin = (header[0] & 0x80) != 0
        masked = (header[1] & 0x80) != 0
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", reader.read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", reader.read(8))[0]
        if length > 1_000_000:
            raise ConnectionError("frame too large")
        mask = reader.read(4) if masked else b""
        raw = reader.read(length) if length else b""
        if masked and raw:
            raw = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            _ws_send(reader.sock, raw, opcode=0xA, lock=send_lock)
            continue
        if opcode == 0xA:
            continue
        if opcode in (0x1, 0x2):
            message_opcode = opcode
            fragments = [raw]
        elif opcode == 0x0 and message_opcode is not None:
            fragments.append(raw)
        else:
            continue
        if not fin:
            continue
        if message_opcode == 0x1:
            return b"".join(fragments).decode("utf-8")
        return ""


def _compact_state(state):
    """Keep pen strokes, drop huge pasted images that would drop the connection."""
    if not isinstance(state, dict):
        return {}

    def dumps(value):
        return json.dumps(value, separators=(",", ":"))

    try:
        if len(dumps(state)) <= 350000:
            return state
    except (TypeError, ValueError):
        return {}
    slim = {
        "viewW": state.get("viewW", 1),
        "viewH": state.get("viewH", 1),
        "offsetX": state.get("offsetX", 0),
        "offsetY": state.get("offsetY", 0),
        "canvasW": state.get("canvasW", 1),
        "canvasH": state.get("canvasH", 1),
        "strokes": state.get("strokes") or [],
        "images": [],
        "texts": state.get("texts") or [],
    }
    try:
        if len(dumps(slim)) <= 350000:
            return slim
    except (TypeError, ValueError):
        pass
    slim["strokes"] = []
    slim["texts"] = []
    return slim


def _guess_lan_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
    except OSError:
        return ""
    if not ip or ip.startswith("127."):
        return ""
    return ip


def _read_http_request(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
        if len(data) > 65536:
            raise ConnectionError("request too large")
    header_blob, _, leftover = data.partition(b"\r\n\r\n")
    lines = header_blob.decode("iso-8859-1").split("\r\n")
    method, path, _ = lines[0].split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return method, path, headers, leftover


def _query_token(path):
    parsed = urlparse(path)
    values = parse_qs(parsed.query)
    token = values.get("k", [""])[0]
    return parsed.path, token


def _find_ssh():
    found = shutil.which("ssh") or shutil.which("ssh.exe")
    if found:
        return found
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidate = os.path.join(windir, "System32", "OpenSSH", "ssh.exe")
    if os.path.isfile(candidate):
        return candidate
    return None


def _find_cloudflared():
    found = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if found:
        return found
    root = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "WhiteboardTool")
    dest = os.path.join(root, "cloudflared.exe")
    tmp = dest + ".tmp"
    if os.path.isfile(dest) and os.path.getsize(dest) > 1_000_000:
        return dest
    if os.path.isfile(tmp) and os.path.getsize(tmp) > 1_000_000:
        try:
            os.replace(tmp, dest)
            return dest
        except OSError:
            return tmp
    return None


def _popen_hidden(args):
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )


def _ssh_base_args(ssh):
    known_hosts = "NUL" if os.name == "nt" else "/dev/null"
    return [
        ssh,
        "-n",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=" + known_hosts,
        "-o",
        "GlobalKnownHostsFile=" + known_hosts,
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
    ]


def _configure_socket(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


class CollabBridge(QObject):
    guest_count_changed = pyqtSignal(int)
    remote_stroke_start = pyqtSignal(str, str, float, float)
    remote_stroke_point = pyqtSignal(str, float, float)
    remote_stroke_points = pyqtSignal(str, object)
    remote_stroke_end = pyqtSignal(str)
    remote_erase = pyqtSignal(float, float)
    remote_undo = pyqtSignal()
    urls_changed = pyqtSignal(str, str)
    status_changed = pyqtSignal(str)


class CollabSession:
    def __init__(self, html_path, token, bridge):
        self._html_path = html_path
        self._token = token
        self.bridge = bridge
        self.allowed = False
        self.port = 0
        self.local_url = ""
        self.lan_url = ""
        self.public_url = ""
        self._send_lock = threading.Lock()
        self._running = False
        self._listen = None
        self._clients = []
        self._clients_lock = threading.Lock()
        self._accept_thread = None
        self._tunnel_proc = None
        self._html = ""
        self._state_provider = lambda: {}
        self._tunnel_lock = threading.Lock()
        self._drain_thread = None

    def set_state_provider(self, fn):
        self._state_provider = fn

    def start(self, enable_tunnel=True, port=None):
        with open(self._html_path, "r", encoding="utf-8") as handle:
            self._html = handle.read()

        self._listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound = False
        if port:
            try:
                self._listen.bind(("0.0.0.0", int(port)))
                bound = True
            except OSError:
                pass
        if not bound:
            self._listen.bind(("0.0.0.0", 0))
        _configure_socket(self._listen)
        self._listen.listen(16)
        self._listen.settimeout(0.5)
        self.port = self._listen.getsockname()[1]
        self.local_url = "http://127.0.0.1:%s/?k=%s" % (self.port, self._token)
        lan_ip = _guess_lan_ip()
        self.lan_url = ("http://%s:%s/?k=%s" % (lan_ip, self.port, self._token)) if lan_ip else ""
        save_session_config(self._token, self.port)
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        self.bridge.urls_changed.emit(self.lan_url, self.public_url)
        if enable_tunnel and self.lan_url:
            status = "Tame pačiame Wi-Fi jau galima. Kuriama vieša nuoroda..."
        elif enable_tunnel:
            status = "Kuriama vieša nuoroda..."
        elif self.lan_url:
            status = "Nuoroda tame pačiame Wi-Fi paruošta."
        else:
            status = "Vietinė nuoroda paruošta."
        self.bridge.status_changed.emit(status)
        self._enable_tunnel = enable_tunnel

    def start_tunnel(self):
        if not getattr(self, "_enable_tunnel", True) or not self._running:
            return
        threading.Thread(target=self._start_tunnel, daemon=True).start()

    def stop(self):
        self._running = False
        clear_session_config()
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for sock in clients:
            try:
                sock.close()
            except OSError:
                pass
        if self._listen is not None:
            try:
                self._listen.close()
            except OSError:
                pass
            self._listen = None
        self._kill_tunnel()
        self.bridge.guest_count_changed.emit(0)

    def set_allowed(self, allowed):
        self.allowed = bool(allowed)
        self.broadcast({"type": "permission", "allowed": self.allowed})

    def broadcast(self, message, exclude=None):
        if isinstance(message, dict) and message.get("type") in ("state", "hello") and "state" in message:
            message = dict(message)
            message["state"] = _compact_state(message.get("state"))
        payload = json.dumps(message, separators=(",", ":"))
        dead = []
        with self._clients_lock:
            clients = list(self._clients)
        for sock in clients:
            if sock is exclude:
                continue
            try:
                _ws_send(sock, payload, lock=self._send_lock)
            except OSError:
                dead.append(sock)
        if dead:
            with self._clients_lock:
                for sock in dead:
                    if sock in self._clients:
                        self._clients.remove(sock)
                    try:
                        sock.close()
                    except OSError:
                        pass
            self.bridge.guest_count_changed.emit(self.guest_count())

    def guest_count(self):
        with self._clients_lock:
            return len(self._clients)

    def _accept_loop(self):
        while self._running:
            try:
                conn, _addr = self._listen.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
            thread.start()

    def _handle_client(self, conn):
        try:
            _configure_socket(conn)
            conn.settimeout(20)
            method, path, headers, leftover = _read_http_request(conn)
            _pathname, token = _query_token(path)
            upgrade = headers.get("upgrade", "").lower()
            if method == "GET" and upgrade == "websocket":
                if token != self._token:
                    self._http_response(conn, 403, "text/plain; charset=utf-8", b"Neteisingas raktas")
                    return
                self._websocket_client(conn, headers, leftover)
                return
            if method == "GET":
                body = self._html.encode("utf-8")
                self._http_response(conn, 200, "text/html; charset=utf-8", body)
                return
            self._http_response(conn, 405, "text/plain; charset=utf-8", b"Method not allowed")
        except Exception:
            try:
                conn.close()
            except OSError:
                pass

    def _http_response(self, conn, status, content_type, body):
        reason = {200: "OK", 403: "Forbidden", 405: "Method Not Allowed"}.get(status, "OK")
        header = (
            "HTTP/1.1 %s %s\r\n"
            "Content-Type: %s\r\n"
            "Content-Length: %s\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-store\r\n"
            "\r\n"
        ) % (status, reason, content_type, len(body))
        try:
            conn.sendall(header.encode("ascii") + body)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _websocket_client(self, conn, headers, leftover=b""):
        key = headers.get("sec-websocket-key", "")
        if not key:
            conn.close()
            return
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: %s\r\n"
            "\r\n"
        ) % _ws_accept(key)
        conn.sendall(response.encode("ascii"))
        conn.settimeout(None)
        with self._clients_lock:
            self._clients.append(conn)
        self.bridge.guest_count_changed.emit(self.guest_count())
        reader = _SockReader(conn, leftover)
        try:
            try:
                state = _compact_state(self._state_provider() or {})
            except Exception:
                state = {}
            _ws_send(
                conn,
                json.dumps(
                    {"type": "hello", "allowed": self.allowed, "state": state},
                    separators=(",", ":"),
                ),
                lock=self._send_lock,
            )
            while self._running:
                if not reader.pending():
                    ready, _, _ = select.select([conn], [], [], 30)
                    if not ready:
                        try:
                            _ws_send(conn, b"", opcode=0x9, lock=self._send_lock)
                        except OSError:
                            break
                        continue
                text = _ws_recv_message(reader, self._send_lock)
                if text is None:
                    break
                if not text:
                    continue
                try:
                    self._on_guest_message(conn, text)
                except Exception:
                    continue
        except (OSError, ConnectionError, UnicodeError, ValueError):
            pass
        finally:
            with self._clients_lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except OSError:
                pass
            self.bridge.guest_count_changed.emit(self.guest_count())

    def _on_guest_message(self, conn, text):
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            return
        kind = message.get("type")
        if kind == "request_state":
            try:
                state = _compact_state(self._state_provider() or {})
            except Exception:
                state = {}
            _ws_send(
                conn,
                json.dumps({"type": "hello", "allowed": self.allowed, "state": state}, separators=(",", ":")),
                lock=self._send_lock,
            )
            return
        if not self.allowed:
            _ws_send(
                conn,
                json.dumps({"type": "permission", "allowed": False}, separators=(",", ":")),
                lock=self._send_lock,
            )
            return
        if kind == "erase":
            self.bridge.remote_erase.emit(float(message.get("x", 0)), float(message.get("y", 0)))
            self.broadcast(message, exclude=conn)
            return
        if kind == "undo":
            self.bridge.remote_undo.emit()
            return
        stroke_id = str(message.get("id", ""))
        if not stroke_id:
            return
        color = str(message.get("color", "#1565c0"))
        if kind in ("stroke_start", "stroke_point", "stroke_points", "stroke_end"):
            message = dict(message)
            message["author"] = "guest"
        if kind == "stroke_start":
            self.bridge.remote_stroke_start.emit(
                stroke_id, color, float(message.get("x", 0)), float(message.get("y", 0))
            )
            self.broadcast(message, exclude=conn)
        elif kind == "stroke_point":
            self.bridge.remote_stroke_point.emit(
                stroke_id, float(message.get("x", 0)), float(message.get("y", 0))
            )
            self.broadcast(message, exclude=conn)
        elif kind == "stroke_points":
            pts = message.get("pts", [])
            if pts:
                self.bridge.remote_stroke_points.emit(stroke_id, pts)
            self.broadcast(message, exclude=conn)
        elif kind == "stroke_end":
            self.bridge.remote_stroke_end.emit(stroke_id)
            self.broadcast(message, exclude=conn)

    def _start_tunnel(self):
        if not self._tunnel_lock.acquire(blocking=False):
            return
        try:
            self._start_tunnel_locked()
        finally:
            self._tunnel_lock.release()

    def _start_tunnel_locked(self):
        ssh = _find_ssh()
        port = str(self.port)
        attempts = []
        if ssh:
            attempts.append(
                (
                    "localhost.run",
                    _ssh_base_args(ssh) + ["-R", "80:127.0.0.1:" + port, "nokey@localhost.run"],
                    20,
                )
            )

        cloudflared = _find_cloudflared()
        if cloudflared:
            attempts.append(
                (
                    "cloudflared",
                    [cloudflared, "tunnel", "--url", "http://127.0.0.1:" + port, "--no-autoupdate"],
                    25,
                )
            )

        if not attempts:
            self.bridge.status_changed.emit(
                "Nerasta OpenSSH. Viešai nuorodai reikia Windows OpenSSH kliento."
            )
            return

        for name, args, timeout in attempts:
            if not self._running:
                return
            self.bridge.status_changed.emit("Kuriama vieša nuoroda...")
            if self._try_tunnel_command(args, timeout):
                return

        if self._running:
            if self.lan_url:
                self.bridge.status_changed.emit(
                    "Vieša nuoroda nepavyko. Tame pačiame Wi-Fi naudokite rodomą nuorodą."
                )
            else:
                self.bridge.status_changed.emit(
                    "Vieša nuoroda nepavyko. Atidarykite vietinę nuorodą šiame kompiuteryje."
                )

    def _try_tunnel_command(self, args, timeout):
        try:
            proc = _popen_hidden(args)
        except Exception:
            return False

        self._kill_tunnel()
        self._tunnel_proc = proc
        chunks = []

        def reader():
            try:
                for line in iter(proc.stdout.readline, b""):
                    chunks.append(line)
            except Exception:
                pass

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout
        buffer = ""
        while time.monotonic() < deadline and self._running:
            if chunks:
                incoming = b"".join(chunks)
                del chunks[:]
                buffer += incoming.decode("utf-8", errors="replace")
                match = PUBLIC_URL_RE.search(buffer)
                if match:
                    self.public_url = match.group(0).rstrip("/") + "/?k=" + self._token
                    self.bridge.urls_changed.emit(self.lan_url, self.public_url)
                    self.bridge.status_changed.emit("Vieša nuoroda paruošta.")
                    threading.Thread(target=self._watch_tunnel, args=(proc,), daemon=True).start()
                    return True
            if proc.poll() is not None:
                break
            time.sleep(0.15)

        if proc.poll() is None:
            self._kill_proc(proc)
        if self._tunnel_proc is proc:
            self._tunnel_proc = None
        return False

    def _watch_tunnel(self, proc):
        while self._running and proc.poll() is None:
            time.sleep(2)
        if self._running and self._tunnel_proc is proc:
            self.public_url = ""
            self.bridge.urls_changed.emit(self.lan_url, "")
            self.bridge.status_changed.emit("Vieša nuoroda nutrūko, kuriama nauja...")
            self._start_tunnel()

    def _kill_proc(self, proc):
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
        except OSError:
            pass

    def _kill_tunnel(self):
        proc = self._tunnel_proc
        self._tunnel_proc = None
        self._kill_proc(proc)
