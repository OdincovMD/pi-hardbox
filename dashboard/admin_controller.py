#!/usr/bin/env python3
"""PAM authentication and a fixed reboot action; local Unix socket only."""
from __future__ import annotations
import ctypes as ct
import grp
import hashlib
import json
import os
import re
import secrets
import signal
import socketserver
import subprocess
import threading
import time

SOCKET = "/run/pi-admin/control.sock"
TTL = 600
SERVICES = {
    "pi-dashboard": "Сайт статуса", "pi-admin-controller": "Вход и управление",
    "tuna-http-8080": "Публикация сайта", "docker": "Контейнерный сервис",
    "pi-fan-controller": "Вентилятор", "pi-vpn-controller": "Управление VPN",
    "hardbox-vpn-firewall": "Маршрутизация Wi-Fi", "hardbox-vpn": "VPN",
    "hardbox-vpn-hotspot": "Точка доступа Wi-Fi", "hardbox-vpn-dhcp": "Адреса Wi-Fi",
    "hardbox-vpn-routing-refresh.timer": "Обновление исключений",
    "img2txt": "img2txt · описания изображений",
}


def pam_password(password, service="pi-dashboard"):
    if not isinstance(password, str) or not password or len(password.encode()) > 512 or "\0" in password:
        return False
    class Message(ct.Structure):
        _fields_ = [("style", ct.c_int), ("text", ct.c_char_p)]
    class Response(ct.Structure):
        _fields_ = [("text", ct.c_void_p), ("code", ct.c_int)]
    callback_type = ct.CFUNCTYPE(ct.c_int, ct.c_int, ct.POINTER(ct.POINTER(Message)), ct.POINTER(ct.POINTER(Response)), ct.c_void_p)
    class Conversation(ct.Structure):
        _fields_ = [("callback", callback_type), ("data", ct.c_void_p)]
    libc = ct.CDLL("libc.so.6")
    libc.calloc.argtypes = [ct.c_size_t, ct.c_size_t]; libc.calloc.restype = ct.c_void_p
    libc.strdup.argtypes = [ct.c_char_p]; libc.strdup.restype = ct.c_void_p
    libc.free.argtypes = [ct.c_void_p]

    @callback_type
    def conversation(count, messages, responses, _data):
        if not 0 < count <= 16:
            return 19
        memory = libc.calloc(count, ct.sizeof(Response))
        if not memory:
            return 5
        values = ct.cast(memory, ct.POINTER(Response))
        for index in range(count):
            style = messages[index].contents.style
            if style in (1, 2):
                value = password.encode() if style == 1 else b"admin"
                values[index].text = libc.strdup(value)
                if values[index].text:
                    continue
            elif style in (3, 4):
                continue
            for allocated in range(index + 1):
                libc.free(values[allocated].text)
            libc.free(memory)
            return 19
        responses[0] = values
        return 0

    pam = ct.CDLL("libpam.so.0")
    pam.pam_start.argtypes = [ct.c_char_p, ct.c_char_p, ct.POINTER(Conversation), ct.POINTER(ct.c_void_p)]
    for name in ("pam_authenticate", "pam_acct_mgmt", "pam_end"):
        getattr(pam, name).argtypes = [ct.c_void_p, ct.c_int]
    handle = ct.c_void_p()
    conv = Conversation(conversation, None)
    result = pam.pam_start(service.encode(), b"admin", ct.byref(conv), ct.byref(handle))
    if result:
        return False
    try:
        result = pam.pam_authenticate(handle, 1)  # PAM_DISALLOW_NULL_AUTHTOK
        if result == 0:
            result = pam.pam_acct_mgmt(handle, 0)
        return result == 0
    finally:
        pam.pam_end(handle, result)


def credential_version():
    return os.stat("/etc/shadow").st_mtime_ns


class Controller:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.failures = []
        self.reboot_job = None
        self.services = []

    def login(self, password):
        with self.lock:
            now = time.monotonic()
            self.failures = [value for value in self.failures if now - value < 60]
            if len(self.failures) >= 5:
                return {"ok": False, "code": "limited", "error": "Слишком много попыток. Повторите вход через минуту."}
            self.failures.append(now)
            if not pam_password(password):
                return {"ok": False, "code": "unauthorized", "error": "Неверный пароль пользователя admin"}
            self.failures.clear()
            self.sessions = {key: value for key, value in self.sessions.items() if now - value[0] < TTL}
            if len(self.sessions) >= 32:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).digest()] = (time.monotonic(), credential_version())
            return {"ok": True, "session": token, "expires_in": TTL, "authenticated": True}

    def valid(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return False
        value = self.sessions.get(hashlib.sha256(token.encode()).digest())
        return bool(value and time.monotonic() - value[0] < TTL and value[1] == credential_version())

    def request(self, data):
        operation = data.get("operation")
        if operation == "login":
            return self.login(data.get("password"))
        with self.lock:
            if operation == "status":
                return {"ok": True, "available": True, "services": self.services}
            valid = self.valid(data.get("session"))
            if operation == "authorize":
                return {"ok": True, "authenticated": valid, "reboot": self.reboot_job if valid else None}
            if not valid:
                return {"ok": False, "code": "unauthorized", "error": "Войдите с паролем sudo пользователя admin"}
            if operation == "logout":
                self.sessions.pop(hashlib.sha256(data["session"].encode()).digest(), None)
                return {"ok": True}
            if operation != "reboot" or data.get("confirm") is not True or not isinstance(data.get("request_id"), str) or not re.fullmatch(r"[0-9a-f-]{36}", data["request_id"]):
                return {"ok": False, "code": "invalid", "error": "Подтвердите перезагрузку сервера"}
            # One fixed transient timer survives controller/web restarts and
            # systemd rejects a second scheduling under the same unit name.
            if self.reboot_job:
                return {"ok": True, "reboot": self.reboot_job}
            try:
                result = subprocess.run(["/usr/bin/systemd-run", "--quiet", "--unit=pi-dashboard-reboot", "--on-active=8s",
                                         "/usr/bin/systemctl", "reboot"], capture_output=True, timeout=5)
            except subprocess.TimeoutExpired:
                return {"ok": False, "code": "uncertain", "error": "Результат команды пока неизвестен. Не отправляйте перезагрузку повторно."}
            if result.returncode:
                return {"ok": False, "code": "unavailable", "error": "Не удалось запланировать перезагрузку. Проверьте состояние сервера."}
            self.reboot_job = {"id": data["request_id"], "state": "scheduled", "delay_seconds": 8}
            return {"ok": True, "reboot": self.reboot_job}

    def sample(self):
        rows = []
        for unit, label in SERVICES.items():
            # One query per unit, not per HTTP client.
            output = subprocess.run(["/usr/bin/systemctl", "show", unit, "-p", "UnitFileState", "-p", "ActiveState"], capture_output=True, text=True, timeout=3).stdout
            values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
            rows.append({"label": label, "enabled": values.get("UnitFileState") == "enabled", "running": values.get("ActiveState") == "active"})
        with self.lock:
            self.services = rows


def main():
    controller = Controller()
    stop = threading.Event()
    slots = threading.BoundedSemaphore(8)
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            if not slots.acquire(blocking=False):
                return
            try:
                self.connection.settimeout(4)
                raw = self.rfile.readline(4097)
                data = json.loads(raw) if len(raw) <= 4096 and raw.endswith(b"\n") else None
                if not isinstance(data, dict):
                    raise ValueError()
                result = controller.request(data)
            except Exception:
                result = {"ok": False, "code": "unavailable", "error": "Служба управления временно недоступна"}
            finally:
                slots.release()
            try:
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")
            except OSError:
                pass
    for value in (signal.SIGINT, signal.SIGTERM):
        signal.signal(value, lambda *_: stop.set())
    try:
        os.unlink(SOCKET)
    except FileNotFoundError:
        pass
    with socketserver.ThreadingUnixStreamServer(SOCKET, Handler) as server:
        server.daemon_threads = True
        os.chown(SOCKET, 0, grp.getgrnam("admin").gr_gid); os.chmod(SOCKET, 0o660)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            while not stop.is_set():
                try:
                    controller.sample()
                except Exception:
                    pass
                stop.wait(15)
        finally:
            server.shutdown()
            os.unlink(SOCKET)


if __name__ == "__main__":
    main()
