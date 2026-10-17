#!/usr/bin/env python3
"""Scan index/history without printing matched credential contents."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

PATTERNS = (
    ('private-key', re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----')),
    ('provider-key', re.compile(rb'(?:gsk_|sk-proj-|sk-ant-)[A-Za-z0-9_-]{20,}')),
    ('github-token', re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})')),
    ('aws-key', re.compile(rb'(?:AKIA|ASIA)[A-Z0-9]{16}')),
    ('credential-url', re.compile(rb'https?://[^\s/@:]+:[^\s/@]+@')),
)
VLESS = re.compile(rb'vless://([0-9a-fA-F-]{36})@')
FIXTURE_IDS = {b'00000000-0000-0000-0000-000000000000',
               b'12345678-1234-4234-8234-123456789abc'}
PRIVATE_NAMES = {'xray.json', 'wifi.json', 'config.json', 'fan.token', 'dhcp.leases'}
PRIVATE_PARTS = {'backups', 'private', 'state', '__pycache__', '.gitnexus'}


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + str(Path(root).resolve()), '-C', str(root), *args])


def findings(path, data, extra_secrets=()):
    """Return categories only. extra_secrets may be supplied in memory by root."""
    parts = PurePosixPath(path).parts
    name = parts[-1]
    categories = []
    example = name.endswith(('.example', '.example.json'))
    if (not example and (name in PRIVATE_NAMES or name == '.env' or name.endswith(('.env', '.token', '.key', '.pem', '.nmconnection'))
                        or name.startswith('.env.') or name.endswith(('.tar', '.tar.gz', '.zip', '.bundle', '.log', '.sqlite3', '.db', '.leases')))):
        categories.append('private-file')
    if set(parts) & PRIVATE_PARTS or any('last-good' in p or 'pending' in p or 'backup-path' in p for p in parts):
        categories.append('runtime-data')
    categories.extend(label for label, pattern in PATTERNS if pattern.search(data))
    for match in VLESS.finditer(data):
        if match[1].lower() not in FIXTURE_IDS or not (name.startswith('test_') or example):
            categories.append('vless-credential')
    if any(value and value in data for value in extra_secrets):
        categories.append('installed-secret')
    return sorted(set(categories))


def scan(root, history=False, extra_secrets=()):
    records = []
    if history:
        commits = git(root, 'rev-list', '--all').decode().splitlines()
        if not commits:
            raise ValueError('No commits to inspect')
        seen = set()
        for commit in commits:
            for row in git(root, 'ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
                if not row:
                    continue
                metadata, path = row.split(b'\t', 1)
                mode, kind, sha = metadata.decode().split()
                entry = (mode, sha, path)
                if entry in seen:
                    continue
                seen.add(entry)
                data = git(root, 'cat-file', '-p', sha)
                records.append((path.decode(), mode, data))
    else:
        for row in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
            if not row:
                continue
            metadata, path = row.split(b'\t', 1)
            mode, sha, stage = metadata.decode().split()
            if stage != '0':
                raise ValueError('Resolve index conflicts first')
            records.append((path.decode(), mode, git(root, 'cat-file', '-p', sha)))
    if not records:
        raise ValueError('No indexed files to inspect')
    output = []
    for path, mode, data in records:
        categories = findings(path, data, extra_secrets)
        if mode not in ('100644', '100755'):
            categories.append('unsupported-git-mode')
        if categories:
            output.append((path, sorted(set(categories))))
    return len(records), output


def installed_secrets():
    """Compare actual installed values in memory only; never return them to logs."""
    if os.geteuid() != 0:
        raise ValueError('Root is required for installed credential comparison')
    values = set()
    sensitive = {'id', 'password', 'publicKey', 'privateKey', 'shortId', 'psk', 'token', 'api_key', 'apiKey'}
    def collect(obj):
        if isinstance(obj, dict):
            if obj.get('protocol') == 'vless':
                for server in obj.get('settings', {}).get('vnext', []):
                    if server.get('address'):
                        values.add(server['address'].encode())
                sni = obj.get('streamSettings', {}).get('realitySettings', {}).get('serverName')
                if sni:
                    values.add(sni.encode())
            for key, value in obj.items():
                if key in sensitive and isinstance(value, str) and len(value) >= 8:
                    values.add(value.encode())
                else:
                    collect(value)
        elif isinstance(obj, list):
            for value in obj:
                collect(value)
    for folder in ['/etc/hardbox-vpn', '/var/lib/hardbox-vpn', '/opt/pi-dashboard/backups']:
        for path in Path(folder).rglob('*.json'):
            try:
                collect(json.loads(path.read_bytes()))
            except (ValueError, OSError):
                pass
    old = Path('/home/admin/python/img2txt/infra/xray/config.json')
    if old.is_file():
        collect(json.loads(old.read_bytes()))
    token = Path('/etc/pi-dashboard/fan.token')
    if token.is_file():
        values.add(token.read_bytes().strip())
    env = Path('/home/admin/python/img2txt/service/.env')
    if env.is_file():
        for line in env.read_text().splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            if any(part in key.upper() for part in ['KEY', 'TOKEN', 'PASSWORD']) and len(value.strip()) >= 8:
                values.add(value.strip().strip('"\'').encode())
    if not values:
        raise ValueError('No installed credentials found to compare')
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--staged', action='store_true')
    mode.add_argument('--history', action='store_true')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--installed', action='store_true', help='root-only exact comparison with installed credentials')
    args = parser.parse_args()
    try:
        secrets = installed_secrets() if args.installed else ()
        count, output = scan(args.root, args.history, secrets)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('Publication check could not complete:', type(error).__name__)
        return 2
    for path, categories in output:
        print(path + ': ' + ', '.join(categories))
    print(f'Checked {count} Git entries; findings: {len(output)}')
    return 1 if output else 0


if __name__ == '__main__':
    raise SystemExit(main())
