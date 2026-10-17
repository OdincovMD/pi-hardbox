import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import img2txt_llm_health as health


class ReadinessTests(unittest.TestCase):
    def check(self, status, models=(), key='fixture-key'):
        settings = SimpleNamespace(llm_api_key=key, llm_proxy_url='http://proxy:10809',
                                   llm_base_url='https://provider.example/v1', description_model='model')
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.return_value = SimpleNamespace(status_code=status, json=lambda: {'data': [{'id': m} for m in models]})
        http = SimpleNamespace(Client=MagicMock(return_value=client))
        with patch.dict(sys.modules, {'httpx': http, 'service.app.core.config': SimpleNamespace(settings=settings)}):
            result = health.check_provider()
        if key:
            self.assertEqual(client.get.call_args.args[0], 'https://provider.example/v1/models')
            self.assertEqual(http.Client.call_args.kwargs['proxy'], 'http://proxy:10809')
            self.assertFalse(http.Client.call_args.kwargs['trust_env'])
        return result

    def test_authentication_and_model_readiness_are_distinct(self):
        self.assertEqual(self.check(401), 'invalid_key')
        self.assertEqual(self.check(403), 'denied')
        self.assertEqual(self.check(429), 'rate_limited')
        self.assertEqual(self.check(200, ['model']), 'ready')
        self.assertEqual(self.check(200, ['other']), 'model_unavailable')
        self.assertEqual(self.check(500), 'unavailable')
        self.assertEqual(self.check(200, key=''), 'missing_key')

    def test_health_snapshots_are_cached_and_never_block_on_network(self):
        probe = health.LLMHealth()
        with patch.object(health.threading, 'Thread') as thread:
            self.assertEqual(probe.snapshot(), {'state': 'checking', 'checked_at': None})
            probe.snapshot()
            thread.assert_called_once()
        with patch.object(health, 'check_provider', return_value='invalid_key'):
            probe._sample()
        with patch.object(health.threading, 'Thread') as thread:
            self.assertEqual(probe.snapshot()['state'], 'invalid_key')
            thread.assert_not_called()


if __name__ == '__main__':
    unittest.main()
