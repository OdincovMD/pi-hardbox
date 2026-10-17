import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('publication_check', Path(__file__).resolve().parents[1] / 'check_publication.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PublicationTests(unittest.TestCase):
    def test_names_secrets_and_examples(self):
        self.assertIn('private-file', checker.findings('service/.env', b'KEY=value'))
        self.assertIn('runtime-data', checker.findings('backups/original.py', b'source'))
        self.assertIn('provider-key', checker.findings('config.py', ('gsk_' + 'A' * 40).encode()))
        self.assertIn('installed-secret', checker.findings('asset.png', b'prefix-secret-suffix', [b'secret']))
        self.assertFalse(checker.findings('gateway/templates/wifi.example.json', b'{"password":"CHANGE_ME"}'))

    def test_index_is_checked_even_if_working_copy_was_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'config.py').write_text('gsk_' + 'B' * 40)
            subprocess.run(['git', '-C', str(root), 'add', 'config.py'], check=True)
            (root / 'config.py').write_text('clean working copy')
            count, output = checker.scan(root)
            self.assertEqual(count, 1)
            self.assertIn('provider-key', output[0][1])

    def test_old_committed_secret_is_found_after_current_file_was_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            def commit(text):
                (root / 'config.py').write_text(text)
                subprocess.run(['git', '-C', str(root), 'add', 'config.py'], check=True)
                subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
            commit('gsk_' + 'C' * 40)
            commit('clean')
            self.assertFalse(checker.scan(root)[1])
            self.assertIn('provider-key', checker.scan(root, history=True)[1][0][1])


if __name__ == '__main__':
    unittest.main()
