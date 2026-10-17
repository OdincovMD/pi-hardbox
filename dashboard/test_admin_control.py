"""Session, reboot and HTTP security tests; reboot execution is always mocked."""
import copy
import json
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import admin_controller as admin
import dashboard
from test_fan_control import HTTPTests


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.controller = admin.Controller()

    def login(self):
        with patch.object(admin, "pam_password", return_value=True):
            return self.controller.login("test-system-password")["session"]

    def test_password_and_session_material_never_stored_in_plaintext(self):
        token = self.login()
        self.assertTrue(self.controller.request({"operation": "authorize", "session": token})["authenticated"])
        self.assertNotIn(token, repr(self.controller.sessions))
        self.assertNotIn("test-system-password", repr(vars(self.controller)))
        self.controller.request({"operation": "logout", "session": token})
        self.assertFalse(self.controller.valid(token))

    def test_expired_and_changed_system_credentials_invalidate_session(self):
        token = self.login()
        with patch.object(admin.time, "monotonic", return_value=time.monotonic() + admin.TTL + 1):
            self.assertFalse(self.controller.valid(token))
        with patch.object(admin, "credential_version", return_value=-1):
            self.assertFalse(self.controller.valid(token))

    def test_wrong_password_is_rate_limited_and_never_creates_session(self):
        with patch.object(admin, "pam_password", return_value=False) as verify:
            for _ in range(5):
                self.assertEqual(self.controller.login("wrong")["code"], "unauthorized")
            self.assertEqual(self.controller.login("wrong")["code"], "limited")
            self.assertEqual(verify.call_count, 5)
        self.assertFalse(self.controller.sessions)

    def test_reboot_needs_auth_confirmation_and_is_idempotent(self):
        request = {"operation": "reboot", "request_id": str(uuid.uuid4()), "confirm": True}
        with patch.object(admin.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as execute:
            self.assertFalse(self.controller.request(request)["ok"])
            token = self.login(); request["session"] = token
            invalid = {**request, "confirm": False}
            self.assertEqual(self.controller.request(invalid)["code"], "invalid")
            execute.assert_not_called()
            result = self.controller.request(request)
            self.assertTrue(result["ok"])
            self.assertEqual(self.controller.request(request), result)
            self.assertEqual(self.controller.request({**request, "request_id": str(uuid.uuid4())}), result)
            execute.assert_called_once_with(["/usr/bin/systemd-run", "--quiet", "--unit=pi-dashboard-reboot", "--on-active=8s", "/usr/bin/systemctl", "reboot"], capture_output=True, timeout=5)

    def test_failed_reboot_does_not_claim_success(self):
        token = self.login()
        with patch.object(admin.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            result = self.controller.request({"operation": "reboot", "session": token, "confirm": True, "request_id": str(uuid.uuid4())})
        self.assertFalse(result["ok"])
        self.assertIsNone(self.controller.reboot_job)


class AdminHTTPTests(HTTPTests):
    def test_login_uses_pam_broker_and_issues_only_secure_http_only_cookie(self):
        with patch.object(dashboard, "_admin_request", return_value={"ok": True, "session": "S" * 43, "expires_in": 600}) as control:
            status, value = self.request(path="/api/auth/login", payload={"password": " secret with spaces "}, token=None)
        self.assertEqual((status, value), (200, {"authenticated": True, "expires_in": 600}))
        control.assert_called_once_with({"operation": "login", "password": " secret with spaces "})
        cookie = self.last_headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=600"):
            self.assertIn(flag, cookie)
        self.assertNotIn("S" * 43, json.dumps(value))

    def test_login_rejects_untrusted_or_missing_origin_and_throttles(self):
        with patch.object(dashboard, "_admin_request") as control:
            self.assertEqual(self.request(path="/api/auth/login", payload={"password": "test"}, token=None, origin="https://evil.example")[0], 403)
            self.assertEqual(self.request(path="/api/auth/login", payload={"password": "test"}, token=None, origin="")[0], 403)
            control.assert_not_called()
        with patch.object(dashboard, "_admin_request", return_value={"ok": False, "code": "limited", "error": "Подождите"}):
            self.assertEqual(self.request(path="/api/auth/login", payload={"password": "test"}, token=None)[0], 429)
            self.assertEqual(self.last_headers["Retry-After"], "60")

    def test_logout_expires_cookie_and_reboot_passes_only_fixed_fields(self):
        with patch.object(dashboard, "_admin_request", return_value={"ok": True}):
            self.assertEqual(self.request(path="/api/auth/logout", payload={})[0], 200)
            self.assertIn("Max-Age=0", self.last_headers["Set-Cookie"])
        def broker(request):
            if request["operation"] == "authorize":
                return {"authenticated": True}
            self.assertEqual(set(request), {"operation", "session", "confirm", "request_id"})
            return {"ok": True, "reboot": {"state": "scheduled"}}
        with patch.object(dashboard, "_admin_request", side_effect=broker):
            self.assertEqual(self.request(path="/api/admin/reboot", payload={"confirm": True, "request_id": str(uuid.uuid4()), "command": "bad"})[0], 202)

    def test_three_routes_have_correct_active_page_and_fan_belongs_to_manage(self):
        for path, page in (("/", "server"), ("/vpn", "vpn"), ("/manage", "manage")):
            status, html = self.request(method="GET", path=path, token=None)
            self.assertEqual(status, 200)
            self.assertIn(('data-page="' + page + '"').encode(), html)
            self.assertEqual(html.count(b'data-page-link='), 3)
        self.assertLess(dashboard.HTML.index('id="manage-view"'), dashboard.HTML.index('id="fan-title"'))
        self.assertNotIn('id="fan-key"', dashboard.HTML)
        self.assertNotIn('id="vpn-owner-key"', dashboard.HTML)


if __name__ == "__main__":
    unittest.main()
