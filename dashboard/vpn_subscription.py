"""Bounded subscription decoding and HTTPS fetch; URLs and profiles stay private."""
import base64
import binascii
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 1024 * 1024
MAX_NODES = 64
PUBLIC_FIELDS = ('state', 'last_check_at', 'last_success_at', 'next_check_at',
                 'total', 'compatible', 'unsupported', 'duplicates', 'tested',
                 'working', 'selected_index', 'latency_ms', 'message')


def is_public(address):
    value = ipaddress.ip_address(address)
    return value.is_global and not value.is_multicast and not value.is_reserved


def validate_url(value):
    if not isinstance(value, str) or not 8 < len(value) <= 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError('Укажите полную HTTPS-ссылку подписки без пробелов')
    try:
        parsed = urlsplit(value)
        port = parsed.port or 443
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or not 1 <= port <= 65535:
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
                raise ValueError()
        else:
            if not is_public(address):
                raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError('Нужна HTTPS-ссылка на публичный сервер подписки') from None
    return value


def public_addresses(host, port):
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not is_public(value) for value in addresses):
        raise ValueError('Сервер подписки должен иметь только публичные адреса')
    return sorted(addresses, key=lambda value: ipaddress.ip_address(value).version)


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, addresses, deadline):
        super().__init__(host, port=port, timeout=5, context=ssl.create_default_context())
        self.addresses, self.deadline = addresses, deadline

    def connect(self):
        for address in self.addresses:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            raw = None
            try:
                raw = socket.create_connection((address, self.port), timeout=min(5, remaining))
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError:
                if raw:
                    raw.close()
        raise OSError('Public subscription endpoint unavailable')


def fetch(url):
    # Pin each verified DNS result into the TLS connection; redirects revalidate.
    # No environment proxy, Referer, redirect to HTTP, cookie jar or raw error log.
    try:
        deadline = time.monotonic() + 30
        for _ in range(4):
            if time.monotonic() >= deadline:
                raise TimeoutError()
            validate_url(url)
            parsed = urlsplit(url)
            host, port = parsed.hostname.encode('idna').decode('ascii'), parsed.port or 443
            addresses = public_addresses(host, port)
            connection = PinnedHTTPS(host, port, addresses, deadline)
            try:
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
                connection.request('GET', path, headers={'User-Agent': 'Hardbox-VPN/1.0', 'Accept': 'text/plain', 'Accept-Encoding': 'identity'})
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location:
                        raise ValueError()
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise ValueError()
                body = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    if connection.sock:
                        connection.sock.settimeout(min(5, remaining))
                    chunk = response.read1(min(16384, MAX_BYTES + 1 - len(body)))
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError()
                    if not chunk:
                        return bytes(body)
            finally:
                connection.close()
    except Exception:
        raise ValueError('Не удалось получить подписку по HTTPS. Действующий VPN сохранён') from None
    raise ValueError('Слишком много перенаправлений подписки. Действующий VPN сохранён')


def decode(body, parse_uri):
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise ValueError('Пустая или слишком большая подписка')
    try:
        compact = b''.join(body.split())
        text = body.decode('utf-8-sig') if body.lstrip().startswith((b'vless://', b'#')) else base64.b64decode(compact + b'=' * (-len(compact) % 4), altchars=b'-_', validate=True).decode('utf-8-sig')
    except (UnicodeError, ValueError, binascii.Error):
        raise ValueError('Подписка не содержит корректный base64-список VLESS') from None
    rows = [row.strip() for row in text.splitlines() if row.strip().startswith('vless://')]
    if len(rows) > MAX_NODES:
        raise ValueError('В подписке больше 64 VLESS-профилей; обновление не применено')
    nodes, seen = [], set()
    counts = {'total': len(rows), 'compatible': 0, 'unsupported': 0, 'duplicates': 0}
    for index, row in enumerate(rows, 1):
        try:
            outbound = parse_uri(row)
        except ValueError:
            counts['unsupported'] += 1
            continue
        signature = hashlib.sha256(json.dumps(outbound, sort_keys=True).encode()).digest()
        if signature in seen:
            counts['duplicates'] += 1
            continue
        seen.add(signature)
        nodes.append({'index': index, 'outbound': outbound})
    counts['compatible'] = len(nodes)
    if not nodes:
        raise ValueError('В подписке нет совместимых VLESS + REALITY + TCP профилей')
    return nodes, counts


def settings(current, request):
    enabled, interval = request.get('enabled'), request.get('interval_hours')
    if type(enabled) is not bool or type(interval) is not int or not 1 <= interval <= 168:
        raise ValueError('Укажите автоматическое обновление и целый интервал от 1 до 168 часов')
    url = request.get('url', '')
    if not isinstance(url, str):
        raise ValueError('Проверьте ссылку подписки')
    url = validate_url(url) if url else current.get('url', '')
    if enabled and not url:
        raise ValueError('Добавьте ссылку подписки перед включением обновления')
    return {'enabled': enabled, 'interval_hours': interval, 'url': url}


def view(settings, status, private=False):
    result = {key: status[key] for key in PUBLIC_FIELDS if key in status}
    result.update(enabled=settings['enabled'], configured=bool(settings.get('url')), interval_hours=settings['interval_hours'])
    if private:
        result['revision'] = settings['revision']
    return result
