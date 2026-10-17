"""Domain-only split routing. The complete policy lives in the atomic Xray config."""
from __future__ import annotations

import copy
import hashlib
import io
import ipaddress
import json
import re
import tarfile
import urllib.request
from datetime import datetime, timezone

GROUPS = {
    "category-gov-ru": "Госуслуги и государственные сервисы",
    "vk": "ВКонтакте",
    "yandex": "Яндекс",
    "mailru-group": "Mail.ru, ОК и сервисы VK",
    "category-bank-ru": "Российские банки",
    "ozon": "Ozon",
    "wildberries": "Wildberries",
    "avito": "Авито",
}
PRIVATE = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
           "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
           "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
           "224.0.0.0/4", "240.0.0.0/4", "::/0"]


def domain(value):
    if not isinstance(value, str) or len(value) > 253:
        raise ValueError("Укажите домен сайта без ссылки, порта и пути")
    try:
        result = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Некорректное имя домена") from None
    labels = result.split(".")
    if len(result) > 253 or len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("Укажите домен сайта без ссылки, порта и пути")
    if labels[-1].isdigit() or labels[-1] in {"local", "localhost", "internal", "lan", "home", "test", "invalid"} or len(labels[-1]) < 2:
        raise ValueError("Исключения допускаются только для публичных доменных имён")
    try:
        ipaddress.ip_address(result)
    except ValueError:
        return result
    raise ValueError("Укажите домен сайта, а не IP-адрес")


def manual_policy(direct, vpn):
    if not isinstance(direct, list) or not isinstance(vpn, list) or max(len(direct), len(vpn)) > 50:
        raise ValueError("В каждом списке допускается до 50 доменов")
    direct, vpn = sorted({domain(item) for item in direct}), sorted({domain(item) for item in vpn})
    if sum(map(len, direct + vpn)) > 6000:
        raise ValueError("Списки слишком длинные: сократите количество доменов")
    if set(direct) & set(vpn):
        raise ValueError("Один домен не может одновременно находиться в обоих списках")
    return {"direct": direct, "vpn": vpn}


def revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def view(config, private=False):
    rules = config.get("routing", {}).get("rules", [])
    base = next((rule for rule in rules if rule.get("ruleTag", "").startswith("ru-services|")), None)
    result = {"enabled": bool(base), "default": "vpn", "base_domains": len(base.get("domain", [])) if base else 0,
              "groups": list(GROUPS.values()), "updated_at": base["ruleTag"].split("|")[2] if base else None,
              "source_revision": base["ruleTag"].split("|")[1] if base else None}
    for kind in ("direct", "vpn"):
        rule = next((rule for rule in rules if rule.get("ruleTag") == "owner-" + kind), {})
        values = [item.removeprefix("domain:") for item in rule.get("domain", [])]
        result[kind + "_count"] = len(values)
        if private:
            result[kind] = values
    if private:
        result["revision"] = revision(config)
    return result


def fetch_json_or_bytes(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": "Hardbox-VPN-routing/1.0", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Источник списков вернул слишком много данных")
    return data


def fetch_catalog():
    commit = json.loads(fetch_json_or_bytes("https://api.github.com/repos/v2fly/domain-list-community/commits/master", 200000))["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Не удалось проверить версию списка")
    packed = fetch_json_or_bytes("https://codeload.github.com/v2fly/domain-list-community/tar.gz/" + commit, 4000000)
    with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as archive:
        members = {member.name.rsplit("/", 1)[-1]: member for member in archive.getmembers()
                   if "/data/" in member.name and member.isfile()}
        cache, visiting = {}, set()

        def load(name):
            if name in cache:
                return cache[name]
            if name in visiting or len(visiting) > 30 or name.startswith("tld-") or name not in members:
                raise ValueError("Неподдерживаемый состав списка доменов")
            visiting.add(name)
            member = members[name]
            if member.size > 200000:
                raise ValueError("Раздел списка слишком большой")
            values = set()
            for raw in archive.extractfile(member).read().decode("utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                token = line.split()[0]
                if token.startswith("include:"):
                    child = token[8:]
                    if not re.fullmatch(r"[a-z0-9-]+", child):
                        raise ValueError("Неподдерживаемое включение списка")
                    values.update(load(child))
                else:
                    prefix = "full:" if token.startswith("full:") else "domain:"
                    value = token.split(":", 1)[1] if ":" in token else token
                    if ":" in token and not token.startswith(("full:", "domain:")):
                        raise ValueError("Неподдерживаемый тип доменного правила")
                    # Yandex operates its own brand TLD; generic TLD exceptions
                    # (especially .ru) remain prohibited.
                    values.add(prefix + ("yandex" if value == "yandex" else domain(value)))
            visiting.remove(name)
            cache[name] = values
            return values

        values = sorted(set().union(*(load(name) for name in GROUPS)))
    if not 100 <= len(values) <= 5000 or "domain:gosuslugi.ru" not in values or "domain:vk.com" not in values or "domain:yandex.ru" not in values:
        raise ValueError("Проверка полноты списка не пройдена")
    return {"domains": values, "commit": commit, "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def build(config, manual=None, catalog=None):
    result = copy.deepcopy(config)
    current = view(config, private=True)
    manual = manual_policy(manual["direct"], manual["vpn"]) if manual is not None else {key: current[key] for key in ("direct", "vpn")}
    if catalog is None:
        base = next((rule for rule in config.get("routing", {}).get("rules", []) if rule.get("ruleTag", "").startswith("ru-services|")), None)
        if not base:
            raise ValueError("Базовый список ещё не установлен")
        base = copy.deepcopy(base)
    else:
        base = {"type": "field", "ruleTag": "ru-services|" + catalog["commit"] + "|" + catalog["updated_at"],
                "domain": catalog["domains"], "outboundTag": "direct"}
    proxy = next(item for item in result["outbounds"] if item["tag"] == "vless-only")
    result["outbounds"] = [proxy,
        {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}, "streamSettings": {"sockopt": {"interface": "eth0"}}},
        {"tag": "blocked", "protocol": "blackhole", "settings": {}}]
    for inbound in result["inbounds"]:
        if inbound["tag"] == "wifi-transparent":
            inbound["sniffing"] = {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}
    # Fixed diagnostics cannot accidentally follow an owner's direct exception.
    rules = [
        {"type": "field", "inboundTag": ["stats-local"], "outboundTag": "stats-api"},
        {"type": "field", "ip": PRIVATE, "outboundTag": "blocked"},
        {"type": "field", "inboundTag": ["diagnostics", "wifi-dns", "probe"], "outboundTag": "vless-only"},
    ]
    for kind in ("vpn", "direct"):
        if manual[kind]:
            rules.append({"type": "field", "ruleTag": "owner-" + kind, "domain": ["domain:" + value for value in manual[kind]],
                          "outboundTag": "vless-only" if kind == "vpn" else "direct"})
    rules += [base, {"type": "field", "network": "tcp,udp", "outboundTag": "vless-only"}]
    result["routing"] = {"domainStrategy": "AsIs", "rules": rules}
    result["api"] = {"tag": "stats-api", "services": ["StatsService"]}
    result["stats"] = {}
    result.setdefault("policy", {}).setdefault("system", {}).update(statsOutboundUplink=True, statsOutboundDownlink=True)
    result["inbounds"] = [item for item in result["inbounds"] if item["tag"] != "stats-local"] + [
        {"tag": "stats-local", "listen": "127.0.0.1", "port": 11810, "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}}]
    return result
