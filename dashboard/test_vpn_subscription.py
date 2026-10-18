"""Subscription scheduling, privacy and failed-node isolation; no live changes."""
import base64
import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dashboard
import vpn_controller as vpn
import vpn_subscription as sub
from test_fan_control import HTTPTests
from test_vpn_control import URI, OTHER

URL = "https://subscription.example/feed"


class SubscriptionTests(unittest.TestCase):
    def test_base64_padding_duplicates_and_unsupported_nodes(self):
        payload = ("# header\n" + URI + "\n" + URI.replace("#Test", "#Russia") + "\n" + OTHER + "\n" + URI.replace("type=tcp", "type=ws")).encode()
        for encoded in (base64.b64encode(payload), base64.urlsafe_b64encode(payload).rstrip(b"="), payload):
            nodes, counts = sub.decode(encoded, vpn.parse_uri)
            self.assertEqual([n["index"] for n in nodes], [1, 3])
            self.assertEqual(counts, dict(total=4, compatible=2, unsupported=1, duplicates=1))

    def test_bad_empty_large_or_unbounded_lists_are_rejected(self):
        for value in (b"", b"not base64!", base64.b64encode(b"hello"), b"x" * (sub.MAX_BYTES+1), base64.b64encode(((URI+"\n")*65).encode())):
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                sub.decode(value, vpn.parse_uri)

    def test_url_must_be_public_https_and_errors_never_echo_it(self):
        for url in ("http://subscription.example/feed", "https://127.0.0.1/feed", "https://224.0.0.1/feed", "https://192.168.1.1/feed", "https://[::1]/feed", "https://" + "user:secret" + "@subscription.example/", URL+"#secret", URL+"\nsecret"):
            with self.assertRaises(ValueError) as error: sub.validate_url(url)
            self.assertNotIn(url, str(error.exception))
        self.assertEqual(sub.validate_url(URL), URL)
        with patch.object(sub.socket, "getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("127.0.0.1",443))]), self.assertRaises(ValueError):
            sub.public_addresses("subscription.example",443)

    def test_dns_is_pinned_and_private_redirect_is_refused(self):
        connection = MagicMock()
        connection.getresponse.return_value.status = 302
        connection.getresponse.return_value.getheader.return_value = "https://127.0.0.1/private"
        with patch.object(sub, "public_addresses", return_value=["8.8.8.8"]) as dns, patch.object(sub, "PinnedHTTPS", return_value=connection) as tls:
            with self.assertRaises(ValueError) as error: sub.fetch(URL)
        dns.assert_called_once_with("subscription.example",443)
        self.assertEqual(tls.call_args.args[:3], ("subscription.example",443,["8.8.8.8"]))
        connection.close.assert_called_once()
        self.assertNotIn(URL, str(error.exception))

    def test_fetch_body_cap_and_timeout_are_safe(self):
        for chunks in ([b"payload", b""], [b"x" * (sub.MAX_BYTES+1)]):
            connection = MagicMock()
            connection.getresponse.return_value.status = 200
            connection.getresponse.return_value.read1.side_effect = chunks
            with patch.object(sub,"public_addresses",return_value=["8.8.8.8"]), patch.object(sub,"PinnedHTTPS",return_value=connection):
                if len(chunks) == 2: self.assertEqual(sub.fetch(URL), b"payload")
                else:
                    with self.assertRaises(ValueError): sub.fetch(URL)
            connection.close.assert_called_once()
        with patch.object(sub,"public_addresses",side_effect=OSError("private provider URL")):
            with self.assertRaises(ValueError) as error: sub.fetch(URL)
        self.assertNotIn("private provider URL",str(error.exception))

    def test_settings_blank_keeps_url_and_never_reads_it_back(self):
        settings = sub.settings({"url":URL}, dict(enabled=True, interval_hours=6,url=""))
        self.assertEqual(settings["url"],URL)
        settings["revision"]="revision"
        for private in (False,True):
            value=sub.view(settings,{"state":"success","url":URL},private)
            self.assertNotIn(URL,json.dumps(value)); self.assertNotIn("url",value)
        for interval in (True,0,169,6.0,"6"):
            with self.assertRaises(ValueError): sub.settings({},dict(enabled=True,interval_hours=interval,url=URL))
        with self.assertRaises(ValueError): sub.settings({},dict(enabled=True,interval_hours=6))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)
        self.config=self.state/"live.json"
        self.original=json.dumps({"inbounds":[{"tag":"wifi","port":12345}],"outbounds":[vpn.parse_uri(URI)],"routing":{"rules":[{"outboundTag":"direct"}]}}).encode()
        self.config.write_bytes(self.original)
        for name,value in (("STATE",self.state),("CONFIG",self.config),("SUBSCRIPTION",self.state/"subscription.json")):
            item=patch.object(vpn,name,value);item.start();self.addCleanup(item.stop)
        self.c=vpn.Controller()
        self.c.subscription.update(enabled=True,url=URL)
        self.c.job={"id":"subscription-job-12345","state":"checking"}
        self.fetch=patch.object(sub,"fetch",return_value=base64.b64encode((URI+"\n"+OTHER).encode()));self.fetch.start();self.addCleanup(self.fetch.stop)

    def apply(self,outbound):
        self.applied=outbound
        self.c.set_job(state="success")

    def test_fastest_working_profile_wins_and_next_run_is_persisted(self):
        with patch.object(self.c,"candidate_probe",side_effect=[dict(latency_ms=90),dict(latency_ms=20)]) as probe, patch.object(self.c,"replace",side_effect=self.apply):
            self.c.refresh_subscription()
        self.assertEqual(self.applied,vpn.parse_uri(OTHER))
        self.assertTrue(all(call.kwargs==dict(samples=3,check_llm=True) for call in probe.call_args_list))
        self.assertEqual(self.c.subscription_status["working"],2)
        self.assertEqual(self.c.subscription_status["selected_index"],2)
        self.assertEqual(self.c.subscription_status["latency_ms"],20)
        self.assertIsNotNone(self.c.subscription_status["next_check_at"])
        self.assertEqual(vpn.Controller().subscription_status["next_check_at"],self.c.subscription_status["next_check_at"])
        self.assertNotIn(URL,json.dumps(self.c.snapshot()))
        self.assertEqual(self.config.read_bytes(),self.original)

    def test_all_failed_preserves_current_config_and_schedules_retry(self):
        with patch.object(self.c,"candidate_probe",side_effect=ValueError("failed")),patch.object(self.c,"replace") as apply:
            self.c.refresh_subscription()
        apply.assert_not_called()
        self.assertEqual(self.config.read_bytes(),self.original)
        self.assertEqual(self.c.job["state"],"error")
        self.assertIsNotNone(self.c.subscription_status["next_check_at"])
        self.assertFalse(self.c.subscription_active)

    def test_tie_prefers_current_and_failed_profile_is_skipped(self):
        for results,expected in (([dict(latency_ms=30),dict(latency_ms=30)],URI),([ValueError(),dict(latency_ms=40)],OTHER)):
            with patch.object(self.c,"candidate_probe",side_effect=results),patch.object(self.c,"replace",side_effect=self.apply): self.c.refresh_subscription()
            self.assertEqual(self.applied,vpn.parse_uri(expected))

    def test_revision_idempotency_serialization_and_settings_permissions(self):
        self.c.job=None
        with patch.object(vpn.threading,"Thread") as thread:
            request=dict(enabled=True,interval_hours=12,url=URL)
            job=self.c.start_subscription("subscription-first-12345",request,self.c.subscription["revision"])
            self.assertEqual(job,self.c.start_subscription("subscription-first-12345",request,"stale"))
            thread.assert_called_once()
            for action in (lambda:self.c.start_subscription("subscription-second-12345"),lambda:self.c.submit(URI,"manual-operation-12345"),lambda:self.c.start_routing_job("routing-operation-12345")):
                with self.assertRaises(BlockingIOError): action()
        self.assertEqual(vpn.SUBSCRIPTION.stat().st_mode&0o777,0o600)
        self.c.subscription_active=False;self.c.job=None
        with self.assertRaises(vpn.RoutingConflict):self.c.start_subscription("subscription-stale-12345",request,"stale")
        self.assertEqual(vpn.Controller().subscription["interval_hours"],12)

    def test_disable_keeps_working_config_and_does_not_probe(self):
        self.c.job=None
        with patch.object(vpn.threading,"Thread") as thread:
            result=self.c.start_subscription("disable-operation-12345",dict(enabled=False,interval_hours=6,url=""),self.c.subscription["revision"])
        thread.assert_not_called();self.assertEqual(result["state"],"success")
        self.assertIsNone(self.c.subscription_status["next_check_at"])
        self.assertEqual(self.config.read_bytes(),self.original)
        self.assertEqual(vpn.Controller().subscription["url"],URL)

    def test_due_does_not_overlap_or_run_early(self):
        self.c.job=None
        with patch.object(self.c,"start_subscription") as start:
            self.c.subscription_status["next_check_at"]="2999-01-01T00:00:00+00:00";self.c.subscription_due();start.assert_not_called()
            self.c.subscription_status["next_check_at"]=None;self.c.subscription_active=True;self.c.subscription_due();start.assert_not_called()
            self.c.subscription_active=False;self.c.subscription_due();start.assert_called_once()
        self.c.subscription_record(state="checking",next_check_at="2999-01-01T00:00:00+00:00")
        self.assertIsNone(vpn.Controller().subscription_status["next_check_at"])

    def test_candidate_median_and_llm_probe_without_live_restart(self):
        self.c.subscription_active=False
        commands=[]
        def run(args,timeout=5):
            commands.append(args)
            return SimpleNamespace(returncode=0,stdout="1.1.1.1 STREAM public" if "ahosts" in args else "401")
        process=MagicMock();process.poll.return_value=None
        with patch.object(vpn,"command",side_effect=run),patch.object(vpn.subprocess,"Popen",return_value=process),patch.object(vpn.socket,"create_connection"),patch.object(vpn,"probe",side_effect=[dict(latency_ms=30),dict(latency_ms=90),dict(latency_ms=10)]):
            result=self.c.candidate_probe(json.loads(self.original),samples=3,check_llm=True)
        self.assertEqual(result["latency_ms"],30)
        self.assertFalse(any("restart" in call for call in commands))
        self.assertFalse((self.state/"dashboard-candidate.json").exists())
        process.terminate.assert_called_once()


class SubscriptionHTTPTests(HTTPTests):
    def test_settings_and_refresh_need_session_and_origin(self):
        with patch.object(dashboard,"_vpn_request") as control:
            for path in ("/api/vpn/subscription","/api/vpn/subscription/refresh"):
                self.assertEqual(self.request(path=path,payload={},token=None)[0],401)
                self.assertEqual(self.request(path=path,payload={},origin="https://evil.example")[0],403)
            self.assertEqual(self.request(method="GET",path="/api/vpn/subscription",token=None)[0],401)
            control.assert_not_called()

    def test_async_settings_refresh_and_conflict(self):
        payload=dict(enabled=True,interval_hours=6,url=URL,revision="revision",request_id="subscription-http-12345")
        with patch.object(dashboard,"_vpn_request",return_value={"ok":True,"job":{"state":"checking"}}) as control:
            code,result=self.request(path="/api/vpn/subscription",payload=payload)
            self.assertEqual(code,202);self.assertNotIn(URL,json.dumps(result))
            control.assert_called_once_with({"operation":"set-subscription",**payload})
        with patch.object(dashboard,"_vpn_request",return_value={"ok":False,"code":"conflict","error":"Reload"}):
            self.assertEqual(self.request(path="/api/vpn/subscription",payload=payload)[0],409)
        with patch.object(dashboard,"_vpn_request",return_value={"ok":True,"job":{"state":"checking"}}) as control:
            self.assertEqual(self.request(path="/api/vpn/subscription/refresh",payload=payload)[0],202)
            control.assert_called_once_with({"operation":"refresh-subscription","request_id":payload["request_id"]})


if __name__=="__main__":unittest.main()
