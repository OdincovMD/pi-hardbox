import json
import unittest
from unittest.mock import patch

import dashboard
from project_monitor import summarize
from test_fan_control import HTTPTests


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.running = [{"running": True, "health": "healthy", "restart": "unless-stopped"},
                        {"running": True, "health": "none", "restart": "unless-stopped"}]

    def test_running_without_healthcheck_is_not_counted_as_checked_healthy(self):
        status = summarize(self.running, 200)
        self.assertEqual((status["state"], status["healthy"], status["health_checks"], status["autostart"]), ("up", 1, 1, True))
        self.assertNotIn("restart", json.dumps(status))

    def test_failed_health_or_http_or_paused_container_requires_attention(self):
        self.running[0]["health"] = "unhealthy"
        self.assertEqual(summarize(self.running, 200)["state"], "attention")
        self.running[0]["health"] = "healthy"
        for code in (500, None):
            self.assertEqual(summarize(self.running, code)["state"], "attention")
        self.running[0]["paused"] = True
        self.assertEqual(summarize(self.running, 200)["running"], 1)
        self.assertEqual(summarize([], None)["state"], "down")

    def test_startup_depends_on_policy_and_docker_boot_enablement(self):
        self.assertFalse(summarize(self.running, 200, False)["autostart"])
        self.assertIsNone(summarize(self.running, 200, None)["autostart"])
        self.running[0]["restart"] = "no"
        self.assertFalse(summarize(self.running, 200)["autostart"])


class ProjectHTTPTests(HTTPTests):
    def test_project_summary_public_and_monitor_never_runs_in_http_thread(self):
        safe = {"available": True, "projects": [summarize([], None)]}
        with patch.object(dashboard.PROJECTS, "snapshot", return_value=safe), patch.object(dashboard.PROJECTS, "sample") as inspect:
            self.assertEqual(self.request(method="GET", path="/api/projects", token=None), (200, safe))
            inspect.assert_not_called()

    def test_assets_have_fixed_types_and_paths_and_head_works(self):
        for path, mime in (("/favicon.svg", "image/svg+xml"), ("/favicon.ico", "image/x-icon"), ("/apple-touch-icon.png", "image/png"), ("/share-cover.png", "image/png")):
            status, _ = self.request(method="GET", path=path, token=None)
            self.assertEqual(status, 200)
            self.assertEqual(self.last_headers["Content-Type"], mime)
            status, body = self.request(method="HEAD", path=path, token=None)
            self.assertEqual((status, body), (200, b""))
        self.assertEqual(self.request(method="GET", path="/assets/../../etc/shadow", token=None)[0], 404)

    def test_link_metadata_has_image_and_canonical_route(self):
        for path in ("/", "/vpn", "/manage"):
            status, html = self.request(method="GET", path=path, token=None)
            self.assertEqual(status, 200)
            self.assertIn(('content="https://api.pi-hardbox.ru' + path + '"').encode(), html)
            self.assertIn(b'property="og:image"', html)
            self.assertIn(b'rel="icon"', html)
            self.assertNotIn(b"__PAGE_URL__", html)


if __name__ == "__main__":
    unittest.main()
