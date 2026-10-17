"""Cached LLM readiness; safe metadata only, never a generation request."""
import threading
import time
from datetime import datetime, timezone


def check_provider():
    import httpx
    from service.app.core.config import settings
    if not settings.llm_api_key:
        return 'missing_key'
    try:
        with httpx.Client(proxy=settings.llm_proxy_url or None, timeout=6, trust_env=False,
                          follow_redirects=False) as client:
            response = client.get(settings.llm_base_url.rstrip('/') + '/models',
                                  headers={'Authorization': 'Bearer ' + settings.llm_api_key})
        if response.status_code == 401:
            return 'invalid_key'
        if response.status_code == 403:
            return 'denied'
        if response.status_code == 429:
            return 'rate_limited'
        if response.status_code != 200:
            return 'unavailable'
        models = response.json().get('data', [])
        return 'ready' if any(item.get('id') == settings.description_model for item in models) else 'model_unavailable'
    except Exception:
        return 'unavailable'


class LLMHealth:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = None
        self.running = False
        self.data = {'state': 'checking', 'checked_at': None}

    def _sample(self):
        try:
            state = check_provider()
        except Exception:
            state = 'unavailable'
        with self.lock:
            self.data = {'state': state, 'checked_at': datetime.now(timezone.utc).isoformat()}
            self.running = False

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            if not self.running and (self.started is None or now - self.started >= 60):
                self.started, self.running = now, True
                threading.Thread(target=self._sample, name='llm-readiness', daemon=True).start()
            return dict(self.data)


health = LLMHealth()
