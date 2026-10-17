#!/usr/bin/env python3
"""Fixed root-only consumer of the primary VLESS settings. Never logs secrets."""
import copy
import fcntl
import hashlib
import json
import os
import subprocess
from pathlib import Path

from vpn_controller import atomic_write, stamp

SOURCE = Path('/etc/hardbox-vpn/xray.json')
TARGET = Path('/etc/hardbox-vpn/img2txt/xray.json')
STATE = Path('/var/lib/hardbox-vpn/img2txt-applied.json')
STATUS = Path('/run/pi-img2txt/vpn.json')
PENDING = Path('/var/lib/hardbox-vpn/dashboard-update-pending.json')
PROJECT = Path('/home/admin/python/img2txt')
COMPOSE = ['/usr/bin/docker', 'compose', '-f', str(PROJECT / 'docker-compose.yml'),
           '-f', str(PROJECT / 'docker-compose.xray.yml')]


def derive(source):
    matches = [item for item in source['outbounds'] if item.get('tag') == 'vless-only']
    if len(matches) != 1 or matches[0].get('protocol') != 'vless':
        raise ValueError('Missing primary VLESS outbound')
    outbound = copy.deepcopy(matches[0])
    # The host's interface binding is not a container routing setting.
    outbound.get('streamSettings', {}).get('sockopt', {}).pop('interface', None)
    return {'log': {'loglevel': 'warning', 'access': 'none'},
            'inbounds': [
                {'tag': 'socks', 'listen': '0.0.0.0', 'port': 10808, 'protocol': 'socks',
                 'settings': {'auth': 'noauth', 'udp': False}},
                {'tag': 'http', 'listen': '0.0.0.0', 'port': 10809, 'protocol': 'http', 'settings': {}}],
            'outbounds': [outbound]}


def run(args, timeout=90):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        # Docker and Xray errors can contain configuration material.
        atomic_write(Path('/var/lib/hardbox-vpn/img2txt-last-error.txt'), result.stderr[:16384])
        raise RuntimeError('img2txt integration check failed')
    return result.stdout


def publish(ok):
    atomic_write(STATUS, json.dumps({'synced': ok, 'updated_at': stamp()}).encode())
    import grp
    os.chown(STATUS, 0, grp.getgrnam('admin').gr_gid)
    STATUS.chmod(0o640)


def synchronize():
    source = json.loads(SOURCE.read_bytes())
    pending = PENDING
    if pending.exists():
        primary = next(item for item in source['outbounds'] if item.get('tag') == 'vless-only')
        digest = hashlib.sha256(json.dumps(primary, sort_keys=True).encode()).hexdigest()
        if json.loads(pending.read_bytes()).get('consumer_outbound_sha256') != digest:
            raise RuntimeError('Primary VPN transaction is not verified yet')
    desired = json.dumps(derive(source), sort_keys=True, indent=2).encode() + b'\n'
    fingerprint = hashlib.sha256(desired).hexdigest()
    try:
        previous = json.loads(STATE.read_bytes())['fingerprint']
    except (OSError, ValueError, KeyError):
        previous = None
    if TARGET.exists() and TARGET.read_bytes() == desired and previous == fingerprint:
        publish(True)
        return False
    TARGET.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    candidate = TARGET.with_name('candidate.json')
    try:
        atomic_write(candidate, desired)
        run(['/usr/local/lib/hardbox-vpn/xray', 'run', '-test', '-config', str(candidate)], 10)
    finally:
        candidate.unlink(missing_ok=True)
    atomic_write(TARGET, desired)
    # The official image deliberately runs as non-root. Grant its fixed group
    # read access while keeping the host directory root-only.
    os.chown(TARGET, 0, 65532)
    TARGET.chmod(0o640)
    # A file bind mount retains its old inode after atomic replacement.
    # Recreate, rather than restart, so Xray sees the new protected file.
    run(COMPOSE + ['up', '-d', '--no-build', '--no-deps', '--force-recreate', 'xray_proxy'], 40)
    run(['/usr/bin/curl', '-4fsS', '--retry', '3', '--retry-all-errors', '--retry-delay', '1',
         '--retry-max-time', '20', '--max-time', '8', '--proxy', 'http://127.0.0.1:10809',
         '--noproxy', '', 'https://www.cloudflare.com/cdn-cgi/trace'], 30)
    # Restart an existing application so cached LLM connections use the new VPN.
    run(COMPOSE + ['restart', '--timeout', '20', 'description_service'], 30)
    run(COMPOSE + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '60', 'description_service'], 75)
    atomic_write(STATE, json.dumps({'fingerprint': fingerprint, 'updated_at': stamp()}).encode())
    publish(True)
    return True


def main():
    os.umask(0o077)
    with open('/run/pi-img2txt/sync.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            changed = synchronize()
        except Exception:
            publish(False)
            raise SystemExit('img2txt VPN synchronization failed; check service state') from None
    print('img2txt VPN synchronized' if changed else 'img2txt VPN already synchronized')


if __name__ == '__main__':
    main()
