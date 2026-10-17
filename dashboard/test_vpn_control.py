"""VPN parser, HTTP authorization and transactional recovery without real changes."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dashboard
import vpn_controller as vpn
from test_fan_control import HTTPTests

KEY = base64.urlsafe_b64encode(bytes(32)).decode().rstrip("=")
URI = f"vless://12345678-1234-4234-8234-123456789abc@8.8.8.8:443?security=reality&type=tcp&encryption=none&flow=xtls-rprx-vision&sni=example.com&fp=qq&sid=00000000&pbk={KEY}#Test"
OTHER = URI.replace("@8.8.8.8", "@1.1.1.1")


class ParserTests(unittest.TestCase):
    def test_owner_style_uri_builds_only_a_bound_vless_outbound(self):
        result = vpn.parse_uri(URI)
        self.assertEqual(result["protocol"], "vless")
        self.assertEqual(result["streamSettings"]["sockopt"], {"interface": "eth0"})
        self.assertEqual(result["streamSettings"]["realitySettings"]["fingerprint"], "qq")
        self.assertNotIn("Test", json.dumps(result))

    def test_invalid_links_are_rejected_without_echoing_secrets(self):
        invalid = [None, "", URI + "\nsecret", URI.replace("vless://", "http://"), URI.replace("@8.8.8.8", "@127.0.0.1"), URI.replace("@8.8.8.8", "@192.168.1.1"), URI.replace("type=tcp", "type=ws"), URI.replace("security=reality", "security=tls"), URI.replace("sid=00000000", "sid=abc"), URI.replace("fp=qq", "fp=unknown"), URI.replace("pbk=" + KEY, "pbk=bad"), URI.replace("sni=example.com", "sni="), URI.replace("#Test", "&security=reality"), URI.replace("#Test", "&evil=1")]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as error:
                    vpn.parse_uri(value)
                self.assertNotIn(KEY, str(error.exception))


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name)
        self.path = state / "live.json"
        self.before = {"inbounds": [{"tag": "protected-wifi", "port": 12345}], "outbounds": [vpn.parse_uri(URI)], "log": {"loglevel": "warning", "access": "none"}}
        self.original = json.dumps(self.before).encode()
        self.path.write_bytes(self.original)
        for name, value in (("CONFIG", self.path), ("STATE", state)):
            p = patch.object(vpn, name, value)
            p.start(); self.addCleanup(p.stop)
        self.controller = vpn.Controller()
        consumer = patch.object(vpn, "sync_img2txt")
        consumer.start(); self.addCleanup(consumer.stop)
        self.controller.job = {"state": "checking"}
        self.commands = []

    def command(self, args, timeout=5):
        self.commands.append(args)
        return SimpleNamespace(returncode=0, stdout="1.1.1.1 STREAM public\n" if "ahosts" in args else "")

    def test_success_preserves_inbounds_and_stores_private_rollback(self):
        with patch.object(vpn, "command", side_effect=self.command), patch.object(self.controller, "candidate_probe"), patch.object(vpn, "probe", return_value={"online": True}), patch.object(vpn.time, "sleep"):
            self.controller.replace(vpn.parse_uri(OTHER))
        installed = json.loads(self.path.read_bytes())
        self.assertEqual(installed["inbounds"], self.before["inbounds"])
        self.assertEqual(installed["outbounds"], [vpn.parse_uri(OTHER)])
        self.assertEqual(self.controller.good.read_bytes(), self.original)
        self.assertFalse(self.controller.pending.exists())
        self.assertEqual(self.controller.job["state"], "success")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_candidate_failure_does_not_restart_or_touch_live_config(self):
        with patch.object(vpn, "command", side_effect=self.command), patch.object(self.controller, "candidate_probe", side_effect=ValueError("Ключ не работает")):
            self.controller.replace(vpn.parse_uri(OTHER))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(any("restart" in args for args in self.commands))
        self.assertEqual(self.controller.job["state"], "error")
        self.assertNotIn(KEY, json.dumps(self.controller.operation()))

    def test_failed_live_probe_restores_exact_previous_config(self):
        with patch.object(vpn, "command", side_effect=self.command), patch.object(self.controller, "candidate_probe"), patch.object(vpn, "probe", side_effect=ValueError("Проверка не прошла")), patch.object(vpn.time, "sleep"):
            self.controller.replace(vpn.parse_uri(OTHER))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(sum("restart" in args for args in self.commands), 2)
        self.assertFalse(self.controller.pending.exists())
        self.assertIn("восстановлен", self.controller.job["message"])

    def test_same_key_is_probed_without_live_restart(self):
        with patch.object(vpn, "command", side_effect=self.command), patch.object(self.controller, "candidate_probe") as candidate:
            self.controller.replace(vpn.parse_uri(URI))
        candidate.assert_called_once()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(any("restart" in args for args in self.commands))
        self.assertEqual(self.controller.job["state"], "success")

    def test_startup_recovers_a_crash_between_install_and_verification(self):
        self.controller.good.write_bytes(self.original)
        self.controller.pending.write_text("{}")
        self.path.write_text("unverified candidate")
        with patch.object(vpn, "command", side_effect=self.command):
            self.controller.recover()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.controller.pending.exists())

    def test_duplicate_request_is_idempotent_and_concurrent_change_is_blocked(self):
        with patch.object(vpn.threading, "Thread") as thread:
            self.controller.job = None
            first = self.controller.submit(URI, "first-operation-12345")
            duplicate = self.controller.submit(URI, "first-operation-12345")
            self.assertEqual(first, duplicate)
            thread.assert_called_once()
            with self.assertRaises(BlockingIOError):
                self.controller.submit(OTHER, "second-operation-12345")


class VPNHTTPTests(HTTPTests):
    def test_vpn_status_is_public_and_key_changes_need_owner_authorization(self):
        with patch.object(dashboard, "_vpn_request", return_value={"vpn": {"available": True, "clients": 1}}):
            self.assertEqual(self.request(method="GET", path="/api/vpn", token=None), (200, {"available": True, "clients": 1}))
        for token in (None, "incorrect"):
            with patch.object(dashboard, "_vpn_request") as controller:
                self.assertEqual(self.request(path="/api/vpn/config", payload={"uri": URI, "request_id": "operation-12345678"}, token=token)[0], 401)
                self.assertEqual(self.request(method="GET", path="/api/vpn/operation", token=token)[0], 401)
                controller.assert_not_called()

    def test_vpn_change_accepted_as_async_job_without_echoing_uri(self):
        job = {"id": "operation-12345678", "state": "checking"}
        with patch.object(dashboard, "_vpn_request", return_value={"ok": True, "job": job}) as controller:
            status, result = self.request(path="/api/vpn/config", payload={"uri": URI, "request_id": job["id"]})
        self.assertEqual((status, result), (202, {"job": job}))
        self.assertNotIn(URI, json.dumps(result))
        controller.assert_called_once_with({"operation": "replace", "uri": URI, "request_id": job["id"]})

    def test_controller_validation_and_busy_errors_have_correct_http_codes(self):
        for code, expected in (("invalid", 400), ("busy", 409), ("unavailable", 503)):
            with patch.object(dashboard, "_vpn_request", return_value={"ok": False, "code": code, "error": "Ошибка"}):
                self.assertEqual(self.request(path="/api/vpn/config", payload={"uri": URI, "request_id": "operation-12345678"})[0], expected)

    def test_vpn_route_has_its_own_title_and_no_embedded_credentials(self):
        status, html = self.request(method="GET", path="/vpn", token=None)
        self.assertEqual(status, 200)
        self.assertIn('data-page="vpn"'.encode(), html)
        self.assertIn("VPN и Wi-Fi — PI Hardbox".encode(), html)
        self.assertNotIn(KEY.encode(), html)
        self.assertNotIn(b"test-owner-code", html)


if __name__ == "__main__":
    unittest.main()
