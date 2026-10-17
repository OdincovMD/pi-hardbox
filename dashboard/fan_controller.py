#!/usr/bin/env python3
"""Privileged fan controller. Only a local Unix socket is exposed."""
from __future__ import annotations

import argparse
import grp
import json
import os
import signal
import socket
import socketserver
import threading
import time
from pathlib import Path

SOCKET = "/run/pi-fan/control.sock"
MANUAL_SECONDS = 1800


class FanController:
    def __init__(self, hwmon: Path, temperature: Path):
        self.hwmon = hwmon
        self.temperature = temperature
        self.lock = threading.Lock()
        self.mode = "auto"
        self.percent = 50
        self.deadline = 0.0
        self.hot = False
        self.warm = False
        self.data: dict = {"available": False}

    def _write_pwm(self, value: int):
        (self.hwmon / "pwm1").write_text(str(value), encoding="ascii")

    def _tick(self, now: float):
        if self.mode == "manual" and now >= self.deadline:
            self.mode = "auto"
        try:
            temp = int(self.temperature.read_text(encoding="ascii")) / 1000
            if not 0 < temp < 120:
                raise ValueError("invalid temperature")
        except (OSError, ValueError):
            temp = None
        if temp is not None:
            self.hot = temp >= 75 or (self.hot and temp > 70)
            self.warm = temp > 35 or (self.warm and temp > 33)
        safety = "sensor" if temp is None else "overheat" if self.hot else None
        if safety:
            target = 255
        elif self.mode == "manual":
            target = round(self.percent * 255 / 100)
        else:
            target = 255 if temp >= 75 else 205 if temp >= 65 else 160 if temp >= 55 else 125 if self.warm else 0
        error = None
        try:
            self._write_pwm(target)
            actual = int((self.hwmon / "pwm1").read_text(encoding="ascii"))
        except (OSError, ValueError):
            error = "Не удалось установить скорость вентилятора"
            actual = None
            self.mode = "auto"
            try:
                self._write_pwm(255)
            except OSError:
                pass
        try:
            rpm = int((self.hwmon / "fan1_input").read_text(encoding="ascii"))
        except (OSError, ValueError):
            rpm = None
        self.data = {
            "available": error is None,
            "mode": self.mode,
            "requested_percent": self.percent if self.mode == "manual" else None,
            "pwm_percent": round(actual * 100 / 255) if actual is not None else None,
            "rpm": rpm,
            "temperature_c": temp,
            "safety": safety,
            "error": error,
            "manual_remaining_seconds": max(0, int(self.deadline - now)) if self.mode == "manual" else 0,
        }
        return dict(self.data)

    def tick(self):
        with self.lock:
            return self._tick(time.monotonic())

    def request(self, request: dict):
        if not isinstance(request, dict):
            raise ValueError("Ожидается объект JSON")
        operation = request.get("operation")
        if operation == "status":
            with self.lock:
                return dict(self.data)
        if operation != "set":
            raise ValueError("Неизвестная операция")
        mode = request.get("mode")
        percent = request.get("percent")
        if mode not in ("auto", "manual"):
            raise ValueError("Выберите автоматический или ручной режим")
        if mode == "manual" and (type(percent) is not int or not 20 <= percent <= 100):
            raise ValueError("Скорость должна быть целым числом от 20 до 100%")
        with self.lock:
            self.mode = mode
            if mode == "manual":
                self.percent = percent
                self.deadline = time.monotonic() + MANUAL_SECONDS
            return self._tick(time.monotonic())


def discover():
    for path in Path("/sys/class/hwmon").glob("hwmon*"):
        if (path / "name").read_text(encoding="ascii").strip() == "pwmfan":
            return path
    raise RuntimeError("PWM fan not found")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--failsafe", action="store_true")
    args = parser.parse_args()
    fan = FanController(discover(), Path("/sys/class/thermal/thermal_zone0/temp"))
    if args.failsafe:
        fan._write_pwm(255)
        return
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(1)
            try:
                raw = self.rfile.readline(513)
                if len(raw) > 512 or not raw.endswith(b"\n"):
                    raise ValueError("Слишком большой запрос")
                result = fan.request(json.loads(raw))
                response = {"ok": True, "fan": result}
            except (ValueError, TypeError, OSError):
                response = {"ok": False, "error": "Недопустимый запрос управления"}
            try:
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            except OSError:
                pass

    fan.tick()
    Path(SOCKET).unlink(missing_ok=True)
    with socketserver.UnixStreamServer(SOCKET, Handler) as server:
        os.chown(SOCKET, 0, grp.getgrnam("admin").gr_gid)
        os.chmod(SOCKET, 0o660)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            while not stop.wait(2):
                fan.tick()
        finally:
            fan._write_pwm(255)
            server.shutdown()
            Path(SOCKET).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
