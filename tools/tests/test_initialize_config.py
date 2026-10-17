import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('initialize_config', ROOT / 'gateway/initialize_config.py')
initializer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(initializer)


class InitializationTests(unittest.TestCase):
    def test_fixture_config_is_private_and_separate_from_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'private-output'
            uri = ('vless://' + '00000000-0000-0000-0000-000000000000'
                   + '@vpn.example.net:443?security=reality&type=tcp&sni=example.net&pbk=' + 'A' * 43)
            previous = os.umask(0o022)
            self.addCleanup(os.umask, previous)
            with patch('sys.argv', ['initialize_config.py', '--output', str(target)]), patch.object(initializer.getpass, 'getpass', side_effect=[uri, 'fixture-password', 'fixture-password']), contextlib.redirect_stdout(io.StringIO()):
                initializer.main()
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual(len(list(target.iterdir())), 3)
            for path in target.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            config = json.loads((target / 'xray.json').read_text())
            self.assertEqual(config['outbounds'][0]['settings']['vnext'][0]['address'], 'vpn.example.net')
            self.assertEqual(json.loads((target / 'wifi.json').read_text())['password'], 'fixture-password')

    def test_output_inside_checkout_is_rejected_before_requesting_credentials(self):
        with patch('sys.argv', ['initialize_config.py', '--output', str(ROOT / 'generated')]), patch.object(initializer.getpass, 'getpass') as secret, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                initializer.main()
            secret.assert_not_called()


if __name__ == '__main__':
    unittest.main()
