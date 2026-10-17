"""Read-only, bounded project health monitor. Public output is an allowlist."""
import copy
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECTS = (("skin-cancer-ai", "skin", "http://127.0.0.1:100/"),
            ("img2txt", "img2txt-description", "http://127.0.0.1:18081/health"))


def summarize(containers, http_code, docker_enabled=True, name="skin-cancer-ai", boot_enabled=True):
    total = len(containers)
    running = sum(item.get("running") is True and not item.get("paused") and not item.get("restarting") for item in containers)
    checks = [item.get("health") for item in containers if item.get("health") not in (None, "none")]
    healthy = checks.count("healthy")
    http_online = http_code is not None and 200 <= http_code < 400
    startup = bool(total and all(item.get("restart") in ("always", "unless-stopped") for item in containers))
    startup = docker_enabled if startup else False
    if startup is not False:
        startup = False if boot_enabled is False else None if boot_enabled is None or startup is None else True
    state = "up" if total and running == total and healthy == len(checks) and http_online else "down" if not running else "attention"
    if name == "img2txt" and {item.get("service") for item in containers} != {"description_service", "xray_proxy"}:
        state = "attention" if running else "down"
        startup = False
    return {"id": name, "name": name, "state": state,
            "running": running, "total": total, "healthy": healthy, "health_checks": len(checks),
            "autostart": startup, "http_online": http_online, "http_code": http_code}


def unknown(name):
    return dict(id=name, name=name, state="unknown", running=None, total=None, healthy=None,
                health_checks=None, autostart=None, http_online=None, http_code=None)


def boot_state(unit):
    enabled = subprocess.run(["/usr/bin/systemctl", "is-enabled", unit], capture_output=True, text=True, timeout=3).stdout.strip()
    return True if enabled == "enabled" else False if enabled in ("disabled", "masked", "enabled-runtime") else None


class ProjectMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"available": False, "updated_at": None, "projects": []}
        self.last_vpn_check = 0
        self.vpn_online = None

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def sample_project(self, name, compose, url):
        # Fixed owner-approved projects; no values from HTTP requests.
        listing = subprocess.run(["/usr/bin/docker", "ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=" + compose],
                                 capture_output=True, text=True, timeout=4, check=True)
        identifiers = listing.stdout.split()
        if len(identifiers) > 64 or any(not re.fullmatch(r"[0-9a-f]{12,64}", value) for value in identifiers):
            raise ValueError("Unexpected project container list")
        containers = []
        if identifiers:
            template = '{"running":{{.State.Running}},"paused":{{.State.Paused}},"restarting":{{.State.Restarting}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}},"restart":{{json .HostConfig.RestartPolicy.Name}},"service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
            inspected = subprocess.run(["/usr/bin/docker", "inspect", "--format", template, *identifiers], capture_output=True, text=True, timeout=4, check=True)
            if len(inspected.stdout) > 65536:
                raise ValueError("Project response too large")
            containers = [json.loads(line) for line in inspected.stdout.splitlines() if line.strip()]
            if len(containers) != len(identifiers):
                raise ValueError("Incomplete project response")
        http_code = None
        http_metadata = {}
        docker_enabled = None
        try:
            docker_enabled = boot_state("docker.service")
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            # Fixed local target; redirects are not followed to external hosts.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *_args, **_kwargs):
                    return None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(url, timeout=3) as response:
                http_code = response.status
                if name == "img2txt":
                    http_metadata = json.loads(response.read(8193))
        except urllib.error.HTTPError as error:
            http_code = error.code
        except OSError:
            pass
        startup = boot_state("img2txt.service") if name == "img2txt" else True
        result = summarize(containers, http_code, docker_enabled, name, startup)
        if name == "img2txt":
            llm = http_metadata.get("llm", {})
            state = llm.get("state")
            if state != "checking":
                try:
                    if abs((datetime.now(timezone.utc) - datetime.fromisoformat(llm["checked_at"])).total_seconds()) > 120:
                        state = "unknown"
                except (KeyError, TypeError, ValueError):
                    state = "unknown"
            result["llm_state"] = state if state in ("ready", "checking", "invalid_key", "missing_key", "denied", "rate_limited", "model_unavailable", "unavailable") else "unknown"
            if result["state"] == "up" and result["llm_state"] != "ready":
                result["state"] = "attention"
            try:
                result["vpn_synced"] = json.loads(Path("/run/pi-img2txt/vpn.json").read_bytes())["synced"] is True
            except (OSError, ValueError, KeyError):
                result["vpn_synced"] = None
            if time.monotonic() - self.last_vpn_check >= 30:
                try:
                    probe = subprocess.run(["/usr/bin/curl", "-4fsS", "--max-time", "5", "--proxy", "http://127.0.0.1:10809", "--noproxy", "", "--output", "/dev/null", "https://www.cloudflare.com/cdn-cgi/trace"], capture_output=True, timeout=6)
                    self.vpn_online = probe.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    self.vpn_online = None
                self.last_vpn_check = time.monotonic()
            result["vpn_online"] = self.vpn_online
            if result["state"] == "up" and (result["vpn_synced"] is not True or result["vpn_online"] is not True):
                result["state"] = "attention"
        return result

    def sample(self):
        projects = []
        for name, compose, url in PROJECTS:
            try:
                projects.append(self.sample_project(name, compose, url))
            except Exception:
                projects.append(unknown(name))
        return {"available": any(row["state"] != "unknown" for row in projects),
                "updated_at": datetime.now(timezone.utc).isoformat(), "projects": projects}

    def _loop(self):
        while True:
            try:
                data = self.sample()
            except Exception:
                data = {"available": False, "updated_at": datetime.now(timezone.utc).isoformat(), "projects": [unknown(name) for name, _, _ in PROJECTS]}
            with self.lock:
                self.data = data
            threading.Event().wait(15)

    def start(self):
        threading.Thread(target=self._loop, name="project-health", daemon=True).start()
