#!/usr/bin/env python3
"""Offline credential input; output must be outside the source checkout."""
import argparse
import configparser
import getpass
import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard'))
from vpn_controller import parse_uri


def private_file(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ssid', default='Hardbox-VPN')
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('Credential output must be outside this Git checkout')
    if not re.fullmatch(r'[A-Za-z0-9 _-]{1,32}', args.ssid):
        parser.error('SSID must use 1–32 safe ASCII letters, digits, spaces, _ or -')
    if output.exists():
        parser.error('Choose a new output directory; existing files are never overwritten')
    try:
        outbound = parse_uri(getpass.getpass('VLESS URI (hidden): '))
        password = getpass.getpass('New Wi-Fi password (hidden): ')
        if len(password) < 8 or len(password) > 63 or not password.isascii() or any(ord(c) < 32 or ord(c) > 126 for c in password):
            raise ValueError('Wi-Fi password must contain 8–63 printable ASCII characters')
        if password != getpass.getpass('Repeat Wi-Fi password: '):
            raise ValueError('Wi-Fi passwords differ')
    except ValueError:
        raise SystemExit('Invalid credentials; no files written') from None
    config = json.loads((ROOT / 'gateway/templates/xray.example.json').read_text())
    config['outbounds'][0] = outbound
    profile = configparser.ConfigParser(interpolation=None)
    profile.optionxform = str
    profile['connection'] = {'id': 'hardbox-vpn', 'uuid': str(uuid.uuid4()), 'type': 'wifi', 'interface-name': 'wlan0', 'autoconnect': 'false'}
    profile['wifi'] = {'mode': 'ap', 'ssid': args.ssid, 'band': 'bg', 'channel': '6', 'ap-isolation': '1'}
    profile['wifi-security'] = {'key-mgmt': 'wpa-psk', 'proto': 'rsn;', 'pairwise': 'ccmp;', 'group': 'ccmp;', 'psk': password.replace('\\', '\\\\').replace(' ', '\\s')}
    profile['ipv4'] = {'method': 'manual', 'address1': '10.77.0.1/24', 'never-default': 'true'}
    profile['ipv6'] = {'method': 'disabled'}
    import io
    rendered = io.StringIO(); profile.write(rendered, space_around_delimiters=False)
    os.umask(0o077)
    output.mkdir(mode=0o700, parents=False)
    private_file(output / 'xray.json', json.dumps(config, indent=2) + '\n')
    private_file(output / 'wifi.json', json.dumps({'ssid': args.ssid, 'password': password}) + '\n')
    private_file(output / 'hardbox-vpn.nmconnection', rendered.getvalue())
    print('Private configuration created outside the checkout; no network or services changed.')


if __name__ == '__main__':
    main()
