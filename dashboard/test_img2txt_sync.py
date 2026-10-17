import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import img2txt_vpn_sync as sync
import vpn_controller as vpn
from test_vpn_control import URI, OTHER, TransactionTests
from project_monitor import ProjectMonitor, summarize


class SyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        permissions = patch.object(sync.os, 'chown')
        permissions.start(); self.addCleanup(permissions.stop)
        for name, filename in [('SOURCE', 'source.json'), ('TARGET', 'target.json'), ('STATE', 'state.json'), ('PENDING', 'pending.json')]:
            patcher = patch.object(sync, name, self.root / filename)
            patcher.start(); self.addCleanup(patcher.stop)
        sync.SOURCE.write_text(json.dumps({'outbounds': [vpn.parse_uri(URI)]}))

    def test_derivation_keeps_only_primary_outbound_and_does_not_modify_source(self):
        source = {'outbounds': [vpn.parse_uri(URI), {'tag': 'direct', 'protocol': 'freedom'}], 'routing': {'rules': ['private']}}
        original = copy.deepcopy(source)
        result = sync.derive(source)
        self.assertEqual(source, original)
        self.assertEqual(len(result['outbounds']), 1)
        self.assertNotIn('routing', result)
        self.assertNotIn('interface', result['outbounds'][0]['streamSettings']['sockopt'])
        self.assertEqual({i['protocol'] for i in result['inbounds']}, {'socks', 'http'})

    def test_changed_key_recreates_bind_mounted_proxy_and_restarts_app_same_key_is_noop(self):
        with patch.object(sync, 'run', return_value=b'') as run, patch.object(sync, 'publish'):
            self.assertTrue(sync.synchronize())
            self.assertTrue(any('--force-recreate' in call.args[0] and 'xray_proxy' in call.args[0] for call in run.call_args_list))
            self.assertTrue(any('restart' in call.args[0] and 'description_service' in call.args[0] for call in run.call_args_list))
            run.reset_mock()
            self.assertFalse(sync.synchronize())
            run.assert_not_called()
            sync.SOURCE.write_text(json.dumps({'outbounds': [vpn.parse_uri(OTHER)]}))
            self.assertTrue(sync.synchronize())
            self.assertEqual(json.loads(sync.TARGET.read_bytes())['outbounds'][0]['settings']['vnext'][0]['address'], '1.1.1.1')

    def test_failed_restart_does_not_mark_new_key_applied_and_retry_recreates(self):
        with patch.object(sync, 'publish'), patch.object(sync, 'run', return_value=b''):
            sync.synchronize()
        before = sync.STATE.read_bytes()
        sync.SOURCE.write_text(json.dumps({'outbounds': [vpn.parse_uri(OTHER)]}))
        def fail(args, *_):
            if 'restart' in args:
                raise RuntimeError('fixture')
            return b''
        with patch.object(sync, 'publish'), patch.object(sync, 'run', side_effect=fail):
            with self.assertRaises(RuntimeError):
                sync.synchronize()
        self.assertEqual(sync.STATE.read_bytes(), before)
        with patch.object(sync, 'publish'), patch.object(sync, 'run', return_value=b'') as run:
            self.assertTrue(sync.synchronize())
            self.assertTrue(any('--force-recreate' in call.args[0] for call in run.call_args_list))

    def test_boot_cannot_copy_an_unverified_primary_transaction(self):
        sync.PENDING.write_text('{}')
        with patch.object(sync, 'run') as run:
            with self.assertRaises(RuntimeError):
                sync.synchronize()
        run.assert_not_called()
        self.assertFalse(sync.TARGET.exists())


class HookTests(unittest.TestCase):
    def test_joined_old_boot_sync_is_reconciled_again_before_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root / 'source.json', root / 'consumer.json'
            outbound = vpn.parse_uri(OTHER)
            source.write_text(json.dumps({'outbounds': [outbound]}))
            target.write_text(json.dumps(sync.derive({'outbounds': [vpn.parse_uri(URI)]})))
            pending = root / 'dashboard-update-pending.json'
            pending.write_text('{}')
            calls = []
            def command(args, **_):
                calls.append(args)
                if len(calls) == 2:
                    target.write_text(json.dumps(sync.derive({'outbounds': [outbound]})))
                return SimpleNamespace(returncode=0)
            with patch.object(vpn, 'CONFIG', source), patch.object(vpn, 'STATE', root), patch.object(vpn, 'IMG2TXT_CONFIG', target), patch.object(vpn, 'command', side_effect=command):
                vpn.sync_img2txt()
            self.assertEqual(len(calls), 2)
            self.assertIn('consumer_outbound_sha256', json.loads(pending.read_bytes()))


class IntegratedTransactionTests(TransactionTests):
    def test_consumer_failure_restores_both_primary_and_consumer(self):
        observed = []
        def synchronize():
            endpoint = json.loads(self.path.read_bytes())['outbounds'][0]['settings']['vnext'][0]['address']
            observed.append(endpoint)
            if endpoint == '1.1.1.1':
                raise ValueError('Не удалось обновить VPN для img2txt')
        with patch.object(vpn, 'command', side_effect=self.command), patch.object(vpn, 'probe', return_value={'online': True}), patch.object(self.controller, 'candidate_probe'), patch.object(vpn, 'sync_img2txt', side_effect=synchronize), patch.object(vpn.time, 'sleep'):
            self.controller.replace(vpn.parse_uri(OTHER))
        self.assertEqual(observed, ['1.1.1.1', '8.8.8.8'])
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.controller.job['state'], 'error')
        self.assertFalse(self.controller.pending.exists())


class ProjectIntegrationTests(unittest.TestCase):
    def test_proxy_alone_does_not_count_as_working_application(self):
        proxy = {'service': 'xray_proxy', 'running': True, 'restart': 'unless-stopped'}
        self.assertEqual(summarize([proxy], 200, name='img2txt')['state'], 'attention')
        app = {'service': 'description_service', 'running': True, 'restart': 'unless-stopped', 'health': 'healthy'}
        self.assertEqual(summarize([proxy, app], 200, name='img2txt')['state'], 'up')
        self.assertFalse(summarize([proxy, app], 200, name='img2txt', boot_enabled=False)['autostart'])

    def test_one_failed_project_does_not_hide_the_other(self):
        monitor = ProjectMonitor()
        with patch.object(monitor, 'sample_project', side_effect=[summarize([], None), RuntimeError('fixture')]):
            data = monitor.sample()
        self.assertTrue(data['available'])
        self.assertEqual([p['id'] for p in data['projects']], ['skin-cancer-ai', 'img2txt'])
        self.assertEqual(data['projects'][1]['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
