"""Cooling and HTTP boundary tests. Real hardware is never written."""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import dashboard
from fan_controller import FanController


class CoolingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.temperature = root / "temp"
        self.temperature.write_text("42000")
        (root / "pwm1").write_text("125")
        (root / "fan1_input").write_text("5500")
        self.fan = FanController(root, self.temperature)

    def test_automatic_curve_and_hysteresis(self):
        for temp, expected in [(42000, 49), (34000, 49), (33000, 0), (35000, 0), (36000, 49), (55000, 63), (65000, 80), (75000, 100)]:
            with self.subTest(temp=temp):
                self.temperature.write_text(str(temp))
                self.assertEqual(self.fan.tick()["pwm_percent"], expected)

    def test_manual_apply_and_expiry(self):
        with patch("fan_controller.time.monotonic", return_value=100):
            result = self.fan.request({"operation": "set", "mode": "manual", "percent": 25})
        self.assertEqual((result["mode"], result["pwm_percent"], result["manual_remaining_seconds"]), ("manual", 25, 1800))
        with patch("fan_controller.time.monotonic", return_value=1901):
            result = self.fan.tick()
        self.assertEqual((result["mode"], result["pwm_percent"]), ("auto", 49))

    def test_hot_manual_override_with_hysteresis(self):
        self.fan.request({"operation": "set", "mode": "manual", "percent": 20})
        for temp, expected, safety in [(75000, 100, "overheat"), (72000, 100, "overheat"), (70000, 20, None)]:
            self.temperature.write_text(str(temp))
            result = self.fan.tick()
            self.assertEqual((result["pwm_percent"], result["safety"]), (expected, safety))

    def test_sensor_failure_forces_full_speed(self):
        self.fan.request({"operation": "set", "mode": "manual", "percent": 20})
        for value in ("bad", "0", "200000"):
            self.temperature.write_text(value)
            result = self.fan.tick()
            self.assertEqual((result["pwm_percent"], result["safety"]), (100, "sensor"))
        self.temperature.unlink()
        self.assertEqual(self.fan.tick()["pwm_percent"], 100)

    def test_invalid_control_does_not_change_mode(self):
        requests = [None, [], {"operation": "set", "mode": "unknown"}]
        requests += [{"operation": "set", "mode": "manual", "percent": value} for value in (True, False, None, 19, 101, -1, "50", 50.0)]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.fan.request(request)
            self.assertEqual(self.fan.mode, "auto")

    def test_restart_starts_automatic(self):
        self.fan.request({"operation": "set", "mode": "manual", "percent": 100})
        restarted = FanController(self.fan.hwmon, self.temperature)
        self.assertEqual((restarted.tick()["mode"], restarted.data["pwm_percent"]), ("auto", 49))

    def test_pwm_failure_is_reported_and_falls_back_to_auto(self):
        with patch.object(self.fan, "_write_pwm", side_effect=OSError("read-only")):
            result = self.fan.request({"operation": "set", "mode": "manual", "percent": 50})
        self.assertFalse(result["available"])
        self.assertEqual(result["mode"], "auto")
        self.assertIsNone(result["pwm_percent"])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.token = Path(cls.tmp.name) / "fan.token"
        cls.token.write_text("test-owner-code")
        cls.token_patch = patch.object(dashboard, "_admin_request", side_effect=lambda request: {"ok": True, "authenticated": request.get("session") == "test-owner-code"})
        cls.token_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.token_patch.stop()
        cls.tmp.cleanup()

    def request(self, method="POST", path="/api/fan", payload=None, token="test-owner-code", origin="https://api.pi-hardbox.ru", content_type="application/json"):
        headers = {"Content-Type": content_type, "Origin": origin}
        if token is not None:
            headers["Cookie"] = dashboard.SESSION_COOKIE + "=" + token
        body = json.dumps({"mode": "manual", "percent": 50} if payload is None else payload) if method == "POST" else None
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        self.last_headers = dict(response.getheaders())
        raw = response.read()
        result = json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw
        connection.close()
        return response.status, result

    def test_missing_wrong_and_non_ascii_tokens_rejected(self):
        for token in (None, "wrong", "café"):
            with self.subTest(token=token), patch.object(dashboard, "_fan_request") as control:
                self.assertEqual(self.request(token=token)[0], 401)
                control.assert_not_called()

    def test_cross_origin_denied(self):
        with patch.object(dashboard, "_fan_request") as control:
            self.assertEqual(self.request(origin="https://evil.example")[0], 403)
            control.assert_not_called()

    def test_validation_rejects_invalid_commands(self):
        for value in ([], "auto", {"mode": "x"}, {"mode": "manual", "percent": True}, {"mode": "manual", "percent": 101}, {"mode": "manual", "percent": "50"}, {"mode": "manual", "percent": 50.1}, {"mode": "auto", "padding": "x" * 600}):
            with self.subTest(value=value), patch.object(dashboard, "_fan_request") as control:
                self.assertEqual(self.request(payload=value)[0], 400)
                control.assert_not_called()
        self.assertEqual(self.request(content_type="text/plain")[0], 415)

    def test_authenticated_manual_and_auto(self):
        for payload in ({"mode": "manual", "percent": 50}, {"mode": "auto"}):
            with patch.object(dashboard, "_fan_request", return_value={"available": True, "mode": payload["mode"]}) as control:
                status, result = self.request(payload=payload)
                self.assertEqual(status, 200)
                self.assertEqual(result["fan"]["mode"], payload["mode"])
                control.assert_called_once_with({"operation": "set", "mode": payload["mode"], "percent": payload.get("percent")})

    def test_controller_failure_is_explicit(self):
        with patch.object(dashboard, "_fan_request", side_effect=OSError("no socket")):
            self.assertEqual(self.request()[0], 503)
        with patch.object(dashboard, "_fan_request", return_value={"available": False}):
            self.assertEqual(self.request()[0], 503)

    def test_public_status_survives_missing_controller(self):
        with patch.object(dashboard.TELEMETRY, "snapshot", return_value={"status": "healthy"}), patch.object(dashboard, "_fan_request", side_effect=OSError("no socket")):
            status, result = self.request(method="GET", path="/api/status", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(result["fan"], {"available": False})

    def test_public_assets_do_not_contain_owner_token(self):
        for path in ("/", "/app.js", "/style.css"):
            status, body = self.request(method="GET", path=path, token=None)
            self.assertEqual(status, 200)
            self.assertNotIn(b"test-owner-code", body)


if __name__ == "__main__":
    unittest.main()
