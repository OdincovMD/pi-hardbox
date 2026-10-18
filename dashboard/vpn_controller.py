#!/usr/bin/env python3
"""Private VPN monitor and transactional owner-key replacement over a Unix socket."""
from __future__ import annotations

import base64
import copy
import grp
import hashlib
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import socketserver
import statistics
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import vpn_routing as routing
import vpn_subscription as subscription

CONFIG = Path("/etc/hardbox-vpn/xray.json")
IMG2TXT_CONFIG = Path("/etc/hardbox-vpn/img2txt/xray.json")
STATE = Path("/var/lib/hardbox-vpn")
SUBSCRIPTION = Path("/etc/hardbox-vpn/subscription.json")
SOCKET = "/run/pi-vpn/control.sock"
XRAY = "/usr/local/lib/hardbox-vpn/xray"
BUSY = {"checking", "applying", "restoring"}


class RoutingConflict(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def command(args, timeout=5):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def atomic_write(path: Path, data: bytes):
    temporary = path.with_name(path.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def parse_uri(uri: str) -> dict:
    if not isinstance(uri, str) or not 10 < len(uri) <= 8192 or any(ord(c) < 32 for c in uri):
        raise ValueError("Вставьте полную ссылку vless:// без переносов строк")
    try:
        parsed = urlsplit(uri.strip())
        identifier = str(uuid.UUID(parsed.username or ""))
        host, port = parsed.hostname, parsed.port or 443
        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=24)
    except (ValueError, TypeError):
        raise ValueError("Не удалось прочитать VLESS-ссылку") from None
    if parsed.scheme != "vless" or not host or parsed.password or parsed.path not in ("", "/") or not 1 <= port <= 65535:
        raise ValueError("Проверьте адрес и порт в VLESS-ссылке")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("Адрес VPN-сервера должен быть публичным")
    except ValueError as error:
        if "публичным" in str(error):
            raise
        if len(host) > 253 or not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host):
            raise ValueError("Недопустимый адрес VPN-сервера") from None
    allowed = {"security", "alpn", "encryption", "type", "flow", "sni", "fp", "sid", "pbk", "spx"}
    if set(params) - allowed or any(len(values) != 1 for values in params.values()):
        raise ValueError("В ссылке есть неподдерживаемые или повторяющиеся параметры")
    query = {key: values[0] for key, values in params.items()}
    if query.get("security") != "reality" or query.get("type", "tcp") not in ("tcp", "raw") or query.get("encryption", "none") != "none":
        raise ValueError("Поддерживаются ключи VLESS + REALITY с транспортом TCP")
    if query.get("flow", "") not in ("", "xtls-rprx-vision"):
        raise ValueError("Неподдерживаемый параметр flow")
    fingerprint = query.get("fp", "chrome")
    if fingerprint not in {"chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq", "random", "randomized"}:
        raise ValueError("Неподдерживаемый отпечаток TLS")
    sni = query.get("sni", "")
    if not sni or len(sni) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", sni):
        raise ValueError("В ключе отсутствует корректный SNI")
    public_key = query.get("pbk", "")
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", public_key) or len(base64.urlsafe_b64decode(public_key + "=")) != 32:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("В ключе отсутствует корректный параметр pbk") from None
    short_id = query.get("sid", "")
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}){0,8}", short_id):
        raise ValueError("Проверьте параметр sid в ключе")
    spider = query.get("spx", "")
    if len(spider) > 256 or (spider and not spider.startswith("/")):
        raise ValueError("Неподдерживаемый параметр spx")
    reality = {"serverName": sni, "fingerprint": fingerprint, "password": public_key, "shortId": short_id}
    if spider:
        reality["spiderX"] = spider
    user = {"id": identifier, "encryption": "none", "flow": query.get("flow", "")}
    return {
        "tag": "vless-only", "protocol": "vless",
        "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": reality, "sockopt": {"interface": "eth0"}},
    }


def probe(port=11808):
    result = command([
        "/usr/bin/curl", "-4fsS", "--max-time", "8", "--connect-timeout", "4",
        "--proxy", f"socks5h://127.0.0.1:{port}", "--noproxy", "",
        "--write-out", "\nlatency=%{time_total}", "https://www.cloudflare.com/cdn-cgi/trace",
    ], timeout=10)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or "ip" not in values or not ipaddress.ip_address(values["ip"]).is_global:
        raise ValueError("VPN не подтвердил выход в интернет")
    return {"checked_at": stamp(), "online": True, "latency_ms": round(float(values["latency"]) * 1000)}


def sync_img2txt():
    current = json.loads(CONFIG.read_bytes())
    outbound = next(item for item in current["outbounds"] if item.get("tag") == "vless-only")
    pending = STATE / "dashboard-update-pending.json"
    if pending.exists():
        marker = json.loads(pending.read_bytes())
        marker["consumer_outbound_sha256"] = hashlib.sha256(json.dumps(outbound, sort_keys=True).encode()).hexdigest()
        atomic_write(pending, json.dumps(marker).encode())
    expected = copy.deepcopy(outbound)
    expected.get("streamSettings", {}).get("sockopt", {}).pop("interface", None)
    # A boot-triggered invocation could already be finishing with the old key.
    # Reconcile again in that case, rather than joining it and claiming success.
    for _ in range(2):
        result = command(["/usr/bin/systemctl", "start", "hardbox-img2txt-sync.service"], timeout=190)
        if result.returncode:
            break
        try:
            actual = json.loads(IMG2TXT_CONFIG.read_bytes())
            if actual["outbounds"] == [expected]:
                return
        except (OSError, ValueError, KeyError):
            pass
    raise ValueError("Не удалось обновить VPN для img2txt")


class Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.job = None
        self.jobs = {}
        self.data = {"available": True, "online": None, "checked_at": None, "updated_at": None}
        self.last_probe = 0.0
        self.previous_bytes = None
        self.pending = STATE / "dashboard-update-pending.json"
        self.good = STATE / "dashboard-last-good.json"
        self.subscription = {"enabled": False, "url": "", "interval_hours": 6, "revision": str(uuid.uuid4())}
        self.subscription_status = {"state": "idle"}
        self.subscription_active = False
        try:
            saved = json.loads(SUBSCRIPTION.read_bytes())
            self.subscription.update(subscription.settings({}, saved), revision=saved["revision"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        try:
            saved = json.loads((STATE / "subscription-status.json").read_bytes())
            self.subscription_status.update({key: saved[key] for key in subscription.PUBLIC_FIELDS if key in saved})
            if self.subscription_status.get("state") == "checking":
                self.subscription_status.update(state="idle", next_check_at=None)
        except (OSError, ValueError, TypeError):
            pass

    def recover(self):
        if self.pending.exists():
            atomic_write(CONFIG, self.good.read_bytes())
            result = command(["/usr/bin/systemctl", "restart", "hardbox-vpn.service"], timeout=15)
            if result.returncode:
                raise RuntimeError("Cannot restore the previous VPN configuration")
            sync_img2txt()
            self.pending.unlink()

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.data)
            result["updating"] = self.subscription_active or bool(self.job and self.job["state"] in BUSY)
            result["subscription"] = subscription.view(self.subscription, self.subscription_status)
            return result

    def operation(self, request_id=None):
        with self.lock:
            return copy.deepcopy(self.jobs.get(request_id) if request_id else self.job)

    def rules(self):
        return routing.view(json.loads(CONFIG.read_bytes()), private=True)

    def subscription_settings(self):
        with self.lock:
            return subscription.view(self.subscription, self.subscription_status, private=True)

    def subscription_record(self, **values):
        with self.lock:
            self.subscription_status.update(values)
            atomic_write(STATE / "subscription-status.json", json.dumps(self.subscription_status).encode())

    def start_subscription(self, request_id, settings=None, expected=None):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,64}", request_id):
            raise ValueError("Недопустимый идентификатор операции")
        with self.lock:
            if request_id in self.jobs:
                return copy.deepcopy(self.jobs[request_id])
            if self.subscription_active or (self.job and self.job["state"] in BUSY):
                raise BlockingIOError()
            if settings is not None:
                if expected != self.subscription["revision"]:
                    raise RoutingConflict("Настройки подписки изменились. Загрузите актуальные значения")
                candidate = subscription.settings(self.subscription, settings)
                candidate["revision"] = str(uuid.uuid4())
                atomic_write(SUBSCRIPTION, json.dumps(candidate).encode())
                self.subscription = candidate
            if not self.subscription.get("url") and settings is None:
                raise ValueError("Сначала сохраните ссылку подписки")
            disabled = settings is not None and not self.subscription["enabled"]
            self.job = {"id": request_id, "kind": "subscription", "state": "success" if disabled else "checking",
                        "message": "Автообновление отключено. Текущее подключение сохранено" if disabled else "Получаем подписку и проверяем подключения отдельно от работающего VPN",
                        "started_at": stamp(), "finished_at": stamp() if disabled else None}
            self.jobs[request_id] = self.job
            if len(self.jobs) > 8:
                self.jobs.pop(next(iter(self.jobs)))
            if disabled:
                self.subscription_record(state="disabled", next_check_at=None, message=self.job["message"])
            result = copy.deepcopy(self.job)
            if not disabled:
                self.subscription_active = True
                threading.Thread(target=self.refresh_subscription, name="vpn-subscription", daemon=True).start()
            return result

    def subscription_due(self):
        with self.lock:
            if not self.subscription["enabled"] or self.subscription_active or (self.job and self.job["state"] in BUSY):
                return
            try:
                due = datetime.fromisoformat(self.subscription_status["next_check_at"]).timestamp()
            except (KeyError, TypeError, ValueError):
                due = 0
            if time.time() < due:
                return
        try:
            self.start_subscription(str(uuid.uuid4()))
        except BlockingIOError:
            pass

    def refresh_subscription(self):
        try:
            self.subscription_record(state="checking", last_check_at=stamp(), tested=0, working=0,
                                     total=0, compatible=0, unsupported=0, duplicates=0, message="Проверяем подписку")
            with self.lock:
                url = self.subscription["url"]
            nodes, counts = subscription.decode(subscription.fetch(url), parse_uri)
            self.subscription_record(**counts)
            current = json.loads(CONFIG.read_bytes())
            current_outbound = next(item for item in current["outbounds"] if item.get("tag") == "vless-only")
            measured = []
            started = time.monotonic()
            for index, node in enumerate(nodes, 1):
                if time.monotonic() - started > 600:
                    raise ValueError("Проверка подписки превысила 10 минут. Действующий VPN сохранён")
                self.set_job(state="checking", message=f"Проверяем подключение {index} из {len(nodes)}; рабочий VPN не прерывается")
                candidate = copy.deepcopy(current)
                candidate["outbounds"] = [node["outbound"] if item.get("tag") == "vless-only" else item for item in current["outbounds"]]
                try:
                    checked = self.candidate_probe(candidate, samples=3, check_llm=True)
                    measured.append((checked["latency_ms"], node["outbound"] != current_outbound, node["index"], node["outbound"]))
                except Exception:
                    pass
                self.subscription_record(tested=index, working=len(measured))
            if not measured:
                raise ValueError("Ни один профиль не прошёл проверки HTTPS и LLM API. Действующий VPN сохранён")
            latency, _, selected, outbound = min(measured, key=lambda item: item[:3])
            # Reuse the same serialized transaction, rollback and img2txt sync.
            self.replace(outbound)
            if self.operation()["state"] != "success":
                raise ValueError("Не удалось применить выбранное подключение; проверьте статус VPN")
            message = f"Подписка проверена: работают {len(measured)} из {len(nodes)}. Выбрано подключение №{selected}, HTTPS {latency} мс"
            self.subscription_record(state="success", last_success_at=stamp(), selected_index=selected, latency_ms=latency, message=message)
            self.set_job(state="success", message=message, finished_at=stamp())
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) and len(str(error)) < 160 and "\n" not in str(error) else "Не удалось обновить подписку. Проверьте действующее подключение VPN"
            self.subscription_record(state="error", message=message)
            self.set_job(state="error", message=message, finished_at=stamp())
        finally:
            try:
                with self.lock:
                    next_check = datetime.fromtimestamp(time.time() + self.subscription["interval_hours"] * 3600, timezone.utc).isoformat() if self.subscription["enabled"] else None
                    self.subscription_record(next_check_at=next_check)
            finally:
                self.subscription_active = False
            self.last_probe = 0.0

    def submit_rules(self, request):
        manual = routing.manual_policy(request.get("direct"), request.get("vpn"))
        return self.start_routing_job(request.get("request_id"), manual, request.get("revision"))

    def start_routing_job(self, request_id, manual=None, expected=None):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,64}", request_id):
            raise ValueError("Недопустимый идентификатор операции")
        with self.lock:
            if request_id in self.jobs:
                return copy.deepcopy(self.jobs[request_id])
            if self.subscription_active or (self.job and self.job["state"] in BUSY):
                raise BlockingIOError()
            if manual is not None and expected != routing.revision(json.loads(CONFIG.read_bytes())):
                raise RoutingConflict("Правила изменились. Загрузите актуальные списки перед сохранением")
            self.job = {"id": request_id, "kind": "rules", "state": "checking", "message": "Проверяем правила маршрутизации", "started_at": stamp(), "finished_at": None}
            self.jobs[request_id] = self.job
            if len(self.jobs) > 8:
                self.jobs.pop(next(iter(self.jobs)))
            result = copy.deepcopy(self.job)
        threading.Thread(target=self.update_rules, args=(manual, expected), name="vpn-routing-update", daemon=True).start()
        return result

    def update_rules(self, manual=None, expected=None):
        touched = False
        refresh = manual is None
        try:
            old_bytes = CONFIG.read_bytes()
            current = json.loads(old_bytes)
            if not refresh and expected != routing.revision(current):
                raise ValueError("Правила изменились. Загрузите актуальные списки")
            catalog = routing.fetch_catalog() if refresh else None
            if refresh and routing.view(current)["source_revision"] == catalog["commit"]:
                atomic_write(STATE / "routing-refresh.json", json.dumps({"checked_at": stamp(), "ok": True}).encode())
                self.set_job(state="success", message="Списки проверены: установлена актуальная версия", finished_at=stamp())
                return
            candidate = routing.build(current, manual, catalog)
            if candidate == current:
                self.set_job(state="success", message="Эти исключения уже действуют", finished_at=stamp())
                return
            path = STATE / "routing-candidate.json"
            try:
                atomic_write(path, json.dumps(candidate).encode())
                valid = command([XRAY, "run", "-test", "-config", str(path)], timeout=8)
                if valid.returncode:
                    raise ValueError("Xray отклонил новые правила")
            finally:
                path.unlink(missing_ok=True)
            self.set_job(state="applying", message="Применяем исключения; соединения могут кратко прерваться")
            atomic_write(self.good, old_bytes)
            atomic_write(self.pending, json.dumps({"started_at": stamp(), "kind": "rules"}).encode())
            touched = True
            atomic_write(CONFIG, json.dumps(candidate, indent=2).encode() + b"\n")
            restarted = command(["/usr/bin/systemctl", "restart", "hardbox-vpn.service"], timeout=15)
            if restarted.returncode:
                raise ValueError("Не удалось включить новые правила")
            time.sleep(.5)
            checked = probe()
            self.pending.unlink()
            touched = False
            with self.lock:
                self.data.update(checked)
            if refresh:
                atomic_write(STATE / "routing-refresh.json", json.dumps({"checked_at": stamp(), "ok": True}).encode())
            self.set_job(state="success", message="Правила применены. VPN по умолчанию сохранён.", finished_at=stamp())
        except Exception:
            message = "Не удалось обновить правила. Прежние исключения сохранены."
            if touched:
                self.set_job(state="restoring", message="Возвращаем прежние правила")
                try:
                    atomic_write(CONFIG, self.good.read_bytes())
                    if command(["/usr/bin/systemctl", "restart", "hardbox-vpn.service"], timeout=15).returncode:
                        raise RuntimeError()
                    self.pending.unlink(missing_ok=True)
                except Exception:
                    message = "Автовосстановление не завершено. Требуется проверка сервера владельцем."
            if refresh:
                atomic_write(STATE / "routing-refresh.json", json.dumps({"checked_at": stamp(), "ok": False}).encode())
            self.set_job(state="error", message=message, finished_at=stamp())
        finally:
            self.last_probe = 0.0

    def set_job(self, **fields):
        with self.lock:
            self.job.update(fields)

    def submit(self, uri, request_id):
        outbound = parse_uri(uri)
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,64}", request_id):
            raise ValueError("Недопустимый идентификатор операции")
        with self.lock:
            if request_id in self.jobs:
                return copy.deepcopy(self.jobs[request_id])
            if self.subscription_active or (self.job and self.job["state"] in BUSY):
                raise BlockingIOError("Другой ключ уже проверяется")
            self.job = {"id": request_id, "state": "checking", "message": "Проверяем новый ключ отдельно от работающего VPN", "started_at": stamp(), "finished_at": None}
            self.jobs[request_id] = self.job
            if len(self.jobs) > 8:
                self.jobs.pop(next(iter(self.jobs)))
            result = copy.deepcopy(self.job)
        threading.Thread(target=self.replace, args=(outbound,), name="vpn-key-update", daemon=True).start()
        return result

    def candidate_probe(self, config, samples=1, check_llm=False):
        path = STATE / "dashboard-candidate.json"
        candidate = copy.deepcopy(config)
        candidate["inbounds"] = [{"tag": "probe", "listen": "127.0.0.1", "port": 11809, "protocol": "socks", "settings": {"auth": "noauth", "udp": False}}]
        # A key must pass the real VPN test, independent of all split-routing rules.
        candidate["outbounds"] = [item for item in config["outbounds"] if item["tag"] == "vless-only"]
        host = candidate["outbounds"][0]["settings"]["vnext"][0]["address"]
        resolved = command(["/usr/bin/getent", "ahosts", host], timeout=4)
        addresses = {line.split()[0] for line in resolved.stdout.splitlines() if line.split()}
        if not addresses or any(not subscription.is_public(address) for address in addresses):
            raise ValueError("VPN-сервер должен иметь только публичные адреса")
        for name in ("api", "routing", "stats", "policy", "dns"):
            candidate.pop(name, None)
        atomic_write(path, json.dumps(candidate).encode())
        process = None
        try:
            valid = command([XRAY, "run", "-test", "-config", str(path)], timeout=6)
            if valid.returncode:
                raise ValueError("Xray отклонил параметры нового ключа")
            process = subprocess.Popen([XRAY, "run", "-config", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(20):
                if process.poll() is not None:
                    raise ValueError("Не удалось запустить проверку ключа")
                try:
                    with socket.create_connection(("127.0.0.1", 11809), timeout=.1):
                        break
                except OSError:
                    time.sleep(.1)
            measured = [probe(11809) for _ in range(samples)]
            if check_llm:
                checked = command(["/usr/bin/curl", "-4sS", "--max-time", "8", "--connect-timeout", "4", "--proxy", "socks5h://127.0.0.1:11809", "--noproxy", "", "--output", "/dev/null", "--write-out", "%{http_code}", "https://api.groq.com/openai/v1/models"], timeout=10)
                if checked.returncode or checked.stdout.strip() not in ("200", "401"):
                    raise ValueError("Через это подключение недоступен LLM API")
            return {**measured[-1], "latency_ms": round(statistics.median(item["latency_ms"] for item in measured))}
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            path.unlink(missing_ok=True)

    def replace(self, outbound):
        touched = False
        try:
            old_bytes = CONFIG.read_bytes()
            current = json.loads(old_bytes)
            candidate = copy.deepcopy(current)
            candidate["outbounds"] = [outbound if item.get("tag") == "vless-only" else item for item in current["outbounds"]]
            candidate["log"] = {"loglevel": "warning", "access": "none"}
            # Only public addresses can be used as a new proxy endpoint.
            host = outbound["settings"]["vnext"][0]["address"]
            resolved = command(["/usr/bin/getent", "ahosts", host], timeout=4)
            addresses = {line.split()[0] for line in resolved.stdout.splitlines() if line.split()}
            if not addresses or any(not subscription.is_public(address) for address in addresses):
                raise ValueError("VPN-сервер должен иметь доступный публичный адрес")
            self.candidate_probe(candidate)
            if candidate == current:
                sync_img2txt()
                self.set_job(state="success", message="Этот ключ уже установлен. Соединение проверено.", finished_at=stamp())
                return
            self.set_job(state="applying", message="Ключ проверен. Переключаем VPN; соединение может кратко прерваться")
            atomic_write(self.good, old_bytes)
            atomic_write(self.pending, json.dumps({"started_at": stamp()}).encode())
            touched = True
            atomic_write(CONFIG, json.dumps(candidate, indent=2).encode() + b"\n")
            restarted = command(["/usr/bin/systemctl", "restart", "hardbox-vpn.service"], timeout=15)
            if restarted.returncode:
                raise ValueError("Не удалось включить новый ключ")
            time.sleep(.5)
            checked = probe()
            self.set_job(state="applying", message="VPN проверен. Обновляем подключение и перезапускаем img2txt")
            sync_img2txt()
            self.pending.unlink()
            with self.lock:
                self.data.update(checked)
            atomic_write(STATE / "key-updated.json", json.dumps({"updated_at": stamp()}).encode())
            self.set_job(state="success", message="Новый ключ установлен. VPN и img2txt проверены.", finished_at=stamp())
        except Exception as error:
            # Never expose exceptions from Xray, curl, OS paths or the supplied URI.
            message = str(error) if isinstance(error, ValueError) and "\n" not in str(error) and len(str(error)) < 160 else "Не удалось проверить или применить ключ"
            if touched:
                self.set_job(state="restoring", message="Возвращаем прежний рабочий ключ")
                try:
                    atomic_write(CONFIG, self.good.read_bytes())
                    restored = command(["/usr/bin/systemctl", "restart", "hardbox-vpn.service"], timeout=15)
                    if restored.returncode:
                        raise RuntimeError()
                    sync_img2txt()
                    self.pending.unlink(missing_ok=True)
                    message += ". Прежний ключ восстановлен."
                except Exception:
                    message = "Автовосстановление не завершено. Требуется проверка сервера владельцем."
            else:
                message += ". Работающий ключ не изменён."
            self.set_job(state="error", message=message, finished_at=stamp())
        finally:
            self.last_probe = 0.0

    def sample(self):
        names = ["hardbox-vpn", "hardbox-vpn-hotspot", "hardbox-vpn-dhcp", "hardbox-vpn-firewall"]
        services = command(["/usr/bin/systemctl", "is-active", *names]).stdout.splitlines()
        services += ["unknown"] * (4 - len(services))
        info = command(["/usr/sbin/iw", "dev", "wlan0", "info"]).stdout
        stations = command(["/usr/sbin/iw", "dev", "wlan0", "station", "dump"]).stdout
        channel = re.search(r"channel (\d+) \((\d+) MHz\)", info)
        ssid = re.search(r"^\s*ssid (.+)$", info, re.MULTILINE)
        rules = command(["/usr/sbin/nft", "list", "table", "inet", "hardbox_vpn"]).stdout
        protection = services[3] == "active" and all(part in rules for part in ["chain wifi_clients", 'iifname "wlan0" jump wifi_clients', "meta nfproto ipv6", "tproxy ip to 127.0.0.1:12345", 'oifname "wlan0" counter'])
        config = json.loads(CONFIG.read_bytes())
        try:
            key_updated = json.loads((STATE / "key-updated.json").read_bytes())["updated_at"]
        except (OSError, ValueError, KeyError):
            key_updated = datetime.fromtimestamp(CONFIG.stat().st_mtime, timezone.utc).isoformat()
        sample = {"available": True, "updated_at": stamp(), "service_running": services[0] == "active", "wifi_active": services[1] == services[2] == "active" and "type AP" in info,
                  "ssid": ssid[1][:64] if ssid else "Hardbox-VPN", "security": "WPA2 · AES", "channel": int(channel[1]) if channel else None,
                  "band": "5 ГГц" if channel and int(channel[2]) > 4000 else "2,4 ГГц" if channel else None,
                  "clients": len(re.findall(r"^Station ", stations, re.MULTILINE)), "protection_active": protection,
                  "key_updated_at": key_updated, "download_bps": None, "upload_bps": None,
                  "routing": routing.view(config), "route_traffic": None}
        try:
            sample["routing"]["last_check"] = json.loads((STATE / "routing-refresh.json").read_bytes())
        except (OSError, ValueError):
            pass
        if sample["routing"]["enabled"] and sample["service_running"]:
            try:
                stats = json.loads(command([XRAY, "api", "statsquery", "--server=127.0.0.1:11810", "-pattern", "outbound>>>"], timeout=3).stdout)
                totals = {"direct": {"up": 0, "down": 0}, "vpn": {"up": 0, "down": 0}}
                for item in stats.get("stat", []):
                    pieces = item.get("name", "").split(">>>")
                    if len(pieces) == 4 and pieces[1] in ("direct", "vless-only") and pieces[3] in ("uplink", "downlink"):
                        totals["direct" if pieces[1] == "direct" else "vpn"]["up" if pieces[3] == "uplink" else "down"] = int(item.get("value", 0))
                sample["route_traffic"] = totals
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        try:
            now = time.monotonic()
            rx = int(Path("/sys/class/net/wlan0/statistics/rx_bytes").read_text())
            tx = int(Path("/sys/class/net/wlan0/statistics/tx_bytes").read_text())
            if self.previous_bytes:
                previous, old_rx, old_tx = self.previous_bytes
                sample.update(download_bps=max(0, round((tx - old_tx) / max(.1, now - previous))), upload_bps=max(0, round((rx - old_rx) / max(.1, now - previous))))
            self.previous_bytes = now, rx, tx
        except (OSError, ValueError):
            pass
        with self.lock:
            self.data.update(sample)
            busy = self.subscription_active or bool(self.job and self.job["state"] in BUSY)
            if not sample["service_running"]:
                self.data["online"] = False
        if not busy and time.monotonic() - self.last_probe >= 30:
            try:
                health = probe() if sample["service_running"] else {"online": False, "checked_at": stamp(), "latency_ms": None}
            except Exception:
                health = {"online": False, "checked_at": stamp(), "latency_ms": None}
            with self.lock:
                self.data.update(health)
            self.last_probe = time.monotonic()


def main():
    os.umask(0o077)
    controller = Controller()
    controller.recover()
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(3)
            try:
                raw = self.rfile.readline(12289)
                if len(raw) > 12288 or not raw.endswith(b"\n"):
                    raise ValueError("Недопустимый размер запроса")
                request = json.loads(raw)
                if not isinstance(request, dict):
                    raise ValueError("Недопустимый запрос")
                operation = request.get("operation")
                if operation == "status":
                    result = {"ok": True, "vpn": controller.snapshot()}
                elif operation == "latest":
                    result = {"ok": True, "job": controller.operation(request.get("request_id"))}
                elif operation == "replace":
                    result = {"ok": True, "job": controller.submit(request.get("uri"), request.get("request_id"))}
                elif operation == "rules":
                    result = {"ok": True, "rules": controller.rules()}
                elif operation == "set-rules":
                    result = {"ok": True, "job": controller.submit_rules(request)}
                elif operation == "refresh-rules":
                    result = {"ok": True, "job": controller.start_routing_job(request.get("request_id"))}
                elif operation == "subscription":
                    result = {"ok": True, "subscription": controller.subscription_settings()}
                elif operation in ("set-subscription", "refresh-subscription"):
                    result = {"ok": True, "job": controller.start_subscription(request.get("request_id"), request if operation == "set-subscription" else None, request.get("revision"))}
                else:
                    raise ValueError("Неизвестная операция")
            except BlockingIOError:
                result = {"ok": False, "code": "busy", "error": "Другое изменение VPN уже выполняется"}
            except RoutingConflict as error:
                result = {"ok": False, "code": "conflict", "error": str(error)}
            except ValueError as error:
                message = str(error) if not isinstance(error, json.JSONDecodeError) else "Недопустимый JSON"
                result = {"ok": False, "code": "invalid", "error": message}
            except Exception:
                result = {"ok": False, "code": "unavailable", "error": "Контроллер VPN временно недоступен"}
            try:
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")
            except OSError:
                pass

    Path(SOCKET).unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(SOCKET, Handler) as server:
        server.daemon_threads = True
        os.chown(SOCKET, 0, grp.getgrnam("admin").gr_gid)
        os.chmod(SOCKET, 0o660)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            while not stop.is_set():
                try:
                    controller.sample()
                    controller.subscription_due()
                except Exception:
                    with controller.lock:
                        controller.data.update(available=False, updated_at=stamp())
                stop.wait(5)
        finally:
            server.shutdown()
            Path(SOCKET).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
