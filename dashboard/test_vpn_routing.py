"""Routing invariants, authorization and rollback; no live network mutations."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import dashboard
import vpn_controller as vpn
import vpn_routing as routing
from test_vpn_control import TransactionTests, URI, OTHER
from test_fan_control import HTTPTests

CATALOG = {"domains": ["domain:gosuslugi.ru", "domain:vk.com", "domain:yandex.ru"], "commit": "a" * 40, "updated_at": "2026-10-17T18:00:00Z"}


class RoutingTests(unittest.TestCase):
    def test_owner_rules_are_domains_only_with_idn_and_limits(self):
        self.assertEqual(routing.manual_policy(["Яндекс.РФ", "VK.com", "vk.com"], ["example.com"])["direct"], ["vk.com", "xn--d1acpjx3f.xn--p1ai"])
        for value in ("https://vk.com", "*.ru", "ru", "127.0.0.1", "192.168.3.1", "test.local", "test.lan", "a.com:443", "a.com/path", "a..com", "foo.com\nbar.com", "-bad.com", "a.com@evil.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                routing.manual_policy([value], [])
        with self.assertRaises(ValueError):
            routing.manual_policy(["vk.com"], ["VK.COM"])
        with self.assertRaises(ValueError):
            routing.manual_policy(["a.com"] * 51, [])

    def test_precedence_and_private_network_guard(self):
        config = {"inbounds": [{"tag": "wifi-transparent"}], "outbounds": [vpn.parse_uri(URI)]}
        result = routing.build(config, {"direct": ["example.ru"], "vpn": ["vk.com"]}, CATALOG)
        rules = result["routing"]["rules"]
        self.assertEqual(rules[1]["outboundTag"], "blocked")
        self.assertIn("192.168.0.0/16", rules[1]["ip"])
        self.assertEqual([item.get("ruleTag") for item in rules[3:5]], ["owner-vpn", "owner-direct"])
        self.assertEqual(rules[-1]["outboundTag"], "vless-only")
        self.assertEqual(rules[2]["outboundTag"], "vless-only")
        self.assertIn("wifi-dns", rules[2]["inboundTag"])
        self.assertNotIn("domain:ru", json.dumps(result))
        self.assertEqual(result["inbounds"][0]["sniffing"]["routeOnly"], True)
        self.assertNotIn("routing", config)
        public, private = routing.view(result), routing.view(result, private=True)
        self.assertNotIn("direct", public)
        self.assertNotIn("example.ru", json.dumps(public))
        self.assertEqual(private["direct"], ["example.ru"])


class SplitTransactionTests(TransactionTests):
    def split(self):
        self.before = routing.build(self.before, {"direct": ["example.ru"], "vpn": ["vk.com"]}, CATALOG)
        self.original = json.dumps(self.before).encode()
        self.path.write_bytes(self.original)

    def test_key_change_preserves_policy_stats_and_direct_outbound(self):
        self.split()
        with patch.object(vpn, "command", side_effect=self.command), patch.object(self.controller, "candidate_probe"), patch.object(vpn, "probe", return_value={"online": True}), patch.object(vpn.time, "sleep"):
            self.controller.replace(vpn.parse_uri(OTHER))
        installed = json.loads(self.path.read_bytes())
        self.assertEqual(installed["routing"], self.before["routing"])
        self.assertEqual(installed["outbounds"][1:], self.before["outbounds"][1:])
        self.assertEqual(installed["api"], self.before["api"])

    def test_stale_revision_rejected_before_starting_worker(self):
        self.split()
        self.controller.job = None
        with patch.object(vpn.threading, "Thread") as worker, self.assertRaises(vpn.RoutingConflict):
            self.controller.submit_rules({"direct": [], "vpn": [], "request_id": "stale-operation-12345", "revision": "old"})
        worker.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_rule_update_retains_key_and_rolls_back_exact_bytes_on_failed_probe(self):
        self.split()
        manual = {"direct": ["api.ipify.org"], "vpn": []}
        with patch.object(vpn, "command", side_effect=self.command), patch.object(vpn, "probe", side_effect=ValueError()), patch.object(vpn.time, "sleep"):
            self.controller.update_rules(manual, routing.revision(self.before))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.controller.pending.exists())
        self.assertEqual(self.controller.job["state"], "error")

    def test_successful_rule_update_and_same_version_refresh_do_not_replace_key(self):
        self.split()
        with patch.object(vpn, "command", side_effect=self.command), patch.object(vpn, "probe", return_value={"online": True}), patch.object(vpn.time, "sleep"):
            self.controller.update_rules({"direct": ["api.ipify.org"], "vpn": []}, routing.revision(self.before))
        installed = json.loads(self.path.read_bytes())
        self.assertEqual(installed["outbounds"][0], self.before["outbounds"][0])
        self.assertEqual(routing.view(installed, True)["direct"], ["api.ipify.org"])
        before = self.path.read_bytes()
        self.commands.clear()
        with patch.object(routing, "fetch_catalog", return_value=CATALOG), patch.object(vpn, "command", side_effect=self.command):
            self.controller.update_rules()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.commands)


class RoutingHTTPTests(HTTPTests):
    def test_rules_read_write_refresh_are_all_owner_only(self):
        for method, path in (("GET", "/api/vpn/rules"), ("POST", "/api/vpn/rules"), ("POST", "/api/vpn/rules/refresh")):
            with patch.object(dashboard, "_vpn_request") as controller:
                self.assertEqual(self.request(method=method, path=path, payload={}, token=None)[0], 401)
                controller.assert_not_called()

    def test_conflict_has_409_and_rule_payload_is_scoped(self):
        payload = {"request_id": "rules-operation-12345", "direct": ["a.ru"], "vpn": [], "revision": "old", "command": "ignored"}
        with patch.object(dashboard, "_vpn_request", return_value={"ok": False, "code": "conflict", "error": "Правила изменились"}) as controller:
            self.assertEqual(self.request(path="/api/vpn/rules", payload=payload)[0], 409)
            self.assertNotIn("command", controller.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
