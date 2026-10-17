# PI Hardbox status dashboard

Project: `/opt/pi-dashboard` on SSH host `raspberry` (Raspberry Pi 5).
Public URL: https://api.pi-hardbox.ru. Python standard library only.
Read this document before changing the project. No GitNexus index existed
in the project or the server's home directory when this feature was added.

## Services and permissions

- `pi-dashboard.service`: existing unprivileged HTTP server, admin:admin,
  loopback port 8080, published through the existing Tuna tunnel.
- `pi-fan-controller.service`: root process that writes to the `pwmfan`
  hwmon device and reads the CPU thermal sensor every 2 seconds.
- `/run/pi-fan/control.sock`: local Unix socket, root:admin, 0660,
  inside a root:admin 0750 runtime directory. No network listener.
- `pi-admin-controller.service`: root-only PAM verifier and fixed reboot broker.
  `/run/pi-admin/control.sock` is root:admin 0660 inside a 0750 directory.
- `/etc/pam.d/pi-dashboard` includes the system common-auth/common-account stacks.
  The former fan.token is retired and is never accepted by any HTTP endpoint.
- The old `fancontrol.service` must be disabled while the new controller runs.
  The new unit declares a conflict to prevent two userspace writers.
  Kernel thermal protection remains enabled.

## Owner workflow and routes

Three native peer links: **Дашборд** `/` (telemetry), **VPN** `/vpn` (Wi-Fi,
key replacement, routing), **Управление** `/manage` (fan, autostart, reboot).
Fan controls belong only to the manage view. The shared login disclosure is
available on VPN and management; the dashboard has no login button. Use the Raspberry Pi's **admin system password** —
the password normally meant by sudo password. This account has NOPASSWD sudo,
so `sudo -S -v` would not verify it; PAM explicitly verifies its real password
and account state. No system password is changed or copied into a config file.

`POST /api/auth/login` JSON `{password}` requires an allowed Origin and the
correct PAM password. It returns only authentication state and sets a 10-minute
`__Host-pi_admin` cookie: Secure, HttpOnly, SameSite=Strict, Path=/, no Domain.
The password clears after success; it is never put in URLs, logs or browser
storage. Tokens are stored only as SHA256 digests in the root broker's memory.
Session expiry is absolute; logout, broker restart or /etc/shadow change revokes
access. Five failed login attempts per minute are allowed globally. Credentials
do not authorize arbitrary users or commands: the identity is always admin.

`GET /api/auth` reports session state; `POST /api/auth/logout` revokes it and
expires the cookie. All fan/VPN/private-rule/reboot endpoints require this
session. Bearer owner codes are no longer supported. Every POST requires the
canonical website Origin (or the documented loopback development origin).
The website remains unprivileged and cannot read /etc/shadow or run sudo.

In **Управление → Вентилятор → Управлять охлаждением**, select automatic or
manual mode and apply. Manual power is 20–100%; PWM is not an RPM setpoint.
Public monitoring remains readable without login. Logout clears private drafts.

## Reboot and autostart

Public `GET /api/manage` reports cached enabled/running state of the known
dashboard, controllers, VPN/AP/DHCP/firewall, routing timer, Tuna website and
Docker services. These services are enabled at boot. The site additionally
wants/starts after pi-admin-controller via pi-dashboard.service.d/10-admin.conf.

The reboot button opens a styled native HTML dialog with a concrete disruption
warning, Cancel initially focused, native focus isolation, Escape and focus
restoration. Only the confirm button submits `POST /api/admin/reboot` JSON
`{confirm:true,request_id:UUID}`. The root broker independently validates the
session and confirmation, then schedules exactly one fixed systemd timer:
`systemd-run --unit=pi-dashboard-reboot --on-active=8s /usr/bin/systemctl reboot`.
The timer survives a web/broker restart. The fixed unit name prevents duplicate
scheduling; failures are reported, and uncertain results never auto-resubmit.
The button is disabled while pending, offline, unauthenticated or updating VPN.
Adding the button does not authorize a test reboot; verification mocks execution.

## Cooling policy

Automatic control retains the old 35 °C / PWM 125 baseline, with a 33 °C
hysteresis boundary. At 55 °C PWM rises to 160, at 65 °C to 205, at 75 °C
to 255. Cold automatic operation may stop the fan.

Manual mode expires after 1800 monotonic seconds and returns to automatic.
Restarting the controller also returns to automatic. Requested settings are
intentionally not persisted. At 75 °C, safety forces PWM 255 until CPU
temperature falls to 70 °C. If the temperature sensor cannot be read or has
an invalid value, PWM is forced to 255. On stop/crash systemd's ExecStopPost
also sets PWM 255; automatic restarts recover control. An unavailable controller
disables website controls and is visibly reported.

## HTTP and socket contracts

`GET /api/status` remains public and adds `fan`:
available, mode, requested_percent, pwm_percent, rpm, temperature_c,
safety (`null`, `overheat`, `sensor`), error, manual_remaining_seconds.
An unavailable controller does not break the rest of telemetry.

`POST /api/fan`: JSON `{"mode":"auto"}` or
`{"mode":"manual","percent":50}`; the authenticated HttpOnly session cookie.
The endpoint validates origin, session, content type, request length (512 bytes),
mode and integer percentage; no CORS access is granted. Errors are JSON and
use 400/401/403/415/503. No shell commands or client-supplied paths are executed.

Socket requests are newline-terminated JSON, at most 512 bytes:
`{"operation":"status"}` or `{"operation":"set","mode":"manual","percent":50}`.
The controller validates the request independently of the web server.

## UI ownership and verification

### Per-core CPU infographic

`cpu.per_core` in `GET /api/status` contains a numerically ordered list of
`{"id":0,"percent":12.3}` objects. IDs are actual Linux logical CPU numbers.
All CPU counters are read together from `/proc/stat` once per sampling interval.
Usage is calculated from the change in total and idle+iowait over the interval;
guest/guest_nice are not counted twice. New or reset counters produce `null`
until a valid second sample exists, rather than fabricated 0% or 100% readings.

The public panel shows a percentage, native progress bar and SVG history per
core. All graphs share a fixed 0–100% vertical scale and 120-second time axis.
History is page-local and bounded to 41 timestamped samples per CPU. Duplicate
API samples do not add points; interruptions longer than 9 seconds break the
line instead of bridging a data gap. Connection loss preserves the last readings
with an explicit stale-data message. Russian labels and accessible chart names
include the current percentage. The native progress/SVG patterns and CSS tokens
in `dashboard.py` remain the canonical visualization owners.

The runtime CSS tokens and `DESIGN.md` own the existing visual design.
`dashboard.py` owns the single-page form, native radio/range/password controls,
global scrollbars and inline live feedback. Russian is the maintained locale.
Telemetry never overwrites an edited speed selection. Apply is disabled while
busy/offline/without a session; authentication failures open/focus the shared sudo login;
network failures instruct the owner to check current state before retrying.
Native keyboard controls and visible focus are required. No UI library,
additional frontend build or persistent browser password storage are used.

Run `python3 -m unittest -v test_fan_control.py test_cpu_telemetry.py`. These tests use temporary
sysfs fixtures and a local HTTP server; they never touch the real fan.
After deployment check both services, public telemetry, unauthenticated denial,
an authenticated speed change and its measured RPM, then restore automatic.

## Recovery

The deployment makes a timestamped backup under `/opt/pi-dashboard/backups/`
of dashboard.py, DESIGN.md and the original fancontrol script/unit.
For an immediate return to the former controller:

```sh
sudo systemctl disable --now pi-fan-controller.service
sudo systemctl enable --now fancontrol.service
```

Restore dashboard.py and DESIGN.md from the matching backup and restart
`pi-dashboard.service` to remove the new website controls. No reboot is required.


## Separate VPN Wi-Fi service

The owner-enabled Hardbox-VPN hotspot is maintained separately in
`/opt/hardbox-vpn/docs/README.md`. It uses wlan0-only transparent routing; the
dashboard, Docker and the host retain the ordinary eth0 default route. Read
that document before changing network/firewall settings. No Wi-Fi or VPN
credentials are exposed by this dashboard.

## VPN and Wi-Fi page

`/vpn` is a separate peer route. Public `GET /api/vpn` returns cached real HTTPS
probe state/timestamp/latency, SSID, band/channel/security, associated client count,
aggregate Wi-Fi rates and protection/service state. No endpoint/client addresses,
passwords or VLESS links are published. Wi-Fi metrics refresh every 5 seconds;
the tunnel probe runs independently every 30 seconds.

The root `pi-vpn-controller.service` owns `/run/pi-vpn/control.sock`
(root:admin 0660 in a 0750 directory). The unprivileged website reaches it through
AF_UNIX and gains no access to root config files. `vpn_controller.py` accepts only
fixed status/job/key/routing operations, with no client-supplied commands, paths,
service names or probe URLs.

`POST /api/vpn/config` requires the PAM-authenticated session, allowed origin,
and JSON `{"uri":"vless://…","request_id":"UUID"}` (12288-byte limit).
It returns 202 for an async job. Protected `GET /api/vpn/operation?request_id=UUID`
returns its sanitized result. Only one update runs at once; eight request IDs are
retained for in-memory idempotency. IDs do not replace owner authorization.

Only VLESS+REALITY+TCP/raw is supported. Parsing constructs an outbound only;
protected inbounds, Wi-Fi settings and firewall are preserved. A separate Xray
on loopback 11809 validates syntax and actual egress before live replacement.
The URI ALPN parameter is accepted as in the original link; REALITY uses the
selected uTLS fingerprint.

Root-only `dashboard-last-good.json` and `dashboard-update-pending.json` in
`/var/lib/hardbox-vpn` protect recovery. Live config is atomically written 0600
and fsynced, the VPN client alone restarts, and live egress is checked. Failure
restores previous bytes. A pending marker restores last-good config after an
interrupted controller restart. Current URI is never returned to the browser.
See `UX-CONTRACT.md` for form, feedback and route ownership.

## Russian service exceptions

The owner-approved routing policy sends selected Russian services directly
and all unknown/other Wi-Fi traffic through VPN. Server and Docker routing is
unchanged. Read `/opt/hardbox-vpn/docs/README.md` for the complete traffic contract,
source groups, DNS behavior, hidden-domain limitation and daily refresh timer.
There is no automatic direct fallback for VPN destinations.

The /vpn page shows aggregate direct/VPN byte counters, base rule count, manual
counts, source installation date and last refresh status. Counters reset on VPN
restart and include DNS/probes. Public responses never include manual domains.

Protected `GET /api/vpn/rules` returns `rules` with owner direct/vpn domain lists
and a revision hash of the complete current config. Protected `POST /api/vpn/rules`
takes `{direct: [domain], vpn: [domain], revision, request_id}`. Both lists allow
up to 50 public domains; combined ASCII names are limited to 6000 characters.
Domain rules include subdomains. Duplicate entries normalize; a domain in both
lists is rejected. Owner VPN entries override owner direct and base rules.
URI/path/port/wildcard/IP/private names are rejected. Unicode uses IDNA.

Protected `POST /api/vpn/rules/refresh` takes `{request_id}`. Both mutations return
202 async jobs using the existing protected operation endpoint and shared PAM-authenticated
session, origin restrictions, input limits, global serialization, idempotency and
rollback. A stale revision returns 409; it never silently overwrites another
change. The browser preserves the draft until explicit reload/save/logout and
never stores it in localStorage, sessionStorage or URLs.

`vpn_routing.py` owns source parsing and policy construction; the existing root
controller owns transactions and status. A separate candidate VPN-key probe
strips routing/stats/API configuration to guarantee a true VPN check and avoid
sharing the live API port. Key replacement updates only the VLESS outbound and
preserves all routing and statistics settings. The website retains no new root
privileges. Run unit tests including `test_vpn_routing.py` and a transparent
namespace routing test before considering a network deployment verified.

The full snapshot test command is in the repository README.
Native PAM conversation success/failure was checked using an isolated temporary
profile and fixture password; the production profile rejects a wrong admin
password. The owner must enter their actual password through HTTPS; no actual
password is requested or retained in this chat. Reboot execution is mocked.

## Status indicators, projects and link images

Service rows on /manage show separate labeled static indicator lights for
current running state and boot enablement. Green means on, red means stopped,
amber means disabled/attention, gray means unknown; text always states the result.
A failed status fetch grays indicators instead of leaving an apparent green.

The owner-approved public application list contains skin-cancer-ai and img2txt.
Its card appears on dashboard and management. `project_monitor.py` runs as the
existing unprivileged website user, not in the root auth broker. Every 15 seconds
it reads only the fixed Docker Compose projects skin and img2txt-description container state/health/restart policy,
checks docker.service boot enablement and probes the fixed local frontend URL.
Only a selected Docker inspect template is requested; environment variables,
mounts, container names, errors, URLs and credentials are never published.
No container is started/stopped/reconfigured by monitoring.

Public GET /api/projects returns a cached allowlist: timestamp, availability,
project name/id, overall state, running/total count, healthy/checked count,
HTTP response code/online and startup boolean (or unknown). Healthcheck-less
containers count as running, not as successfully health-checked. Paused,
restarting, unhealthy containers and failed HTTP prevent a healthy overall
state. Autostart requires compatible policies AND enabled Docker boot startup;
unless-stopped intentionally respects a manual stop. Unknown/stale data is gray.
img2txt requires both description_service and xray_proxy; a lone proxy never counts as a healthy application. Its private health endpoint is published only on host loopback 18081. The monitor checks actual proxy HTTPS egress every 30 seconds and reads only a sanitized synchronization boolean from /run/pi-img2txt/vpn.json. img2txt autostart additionally requires enabled img2txt.service. A failed project inspection does not hide the other project.

Fixed public image endpoints: /favicon.svg, /favicon.ico,
/apple-touch-icon.png and /share-cover.png. The first three adapt the existing
PI mark; the last is an original generated cover (1733x907) for Open Graph and
Twitter cards. All routes include a canonical URL, image metadata and favicon
links. Image paths are strictly allowlisted; no general asset-directory access.
The generation prompt and local source/export paths are in assets/README.md.
Test with test_project_status.py and the existing admin/HTTP tests.


## img2txt VPN consumer

`img2txt_vpn_sync.py`, invoked by the fixed root oneshot
`hardbox-img2txt-sync.service`, derives only vless-only from the primary
/etc/hardbox-vpn/xray.json. The generated 0640 root:65532 config, inside a root-only 0700 directory, is
/etc/hardbox-vpn/img2txt/xray.json. It has just HTTP/SOCKS inbounds and the
VLESS outbound; there is no direct fallback and no Wi-Fi routing policy.
Host-only interface binding is removed inside the container. Credentials never
appear in public status, stdout or the application environment.

A successful primary live probe is followed by consumer synchronization before
the update is committed. The helper validates the derived config, recreates the
proxy (required to refresh an atomically replaced file bind mount), probes HTTPS
through it, restarts description_service, and waits for application health.
Only then is the applied fingerprint recorded. Unchanged configuration is a
no-op. A failed consumer update restores the previous primary config and
synchronizes img2txt back to it. Pending transaction recovery does the same.
Only key changes invoke consumer synchronization; Russian routing updates do
not restart img2txt.

The enabled img2txt.service calls the same helper before Compose startup,
waits for health, and retries failed startup. Both containers have
restart: unless-stopped. Only LLM_PROXY_URL uses the proxy; backend callbacks
and unrelated host/container traffic retain direct routes. No host reboot is
needed for deployment or verification. The helper unit is static and is started
by the application boot unit or the key transaction; it needs no separate timer.
LLM provider credentials remain in service/.env and are independent of VLESS.

Run test_img2txt_sync.py along with the documented suite. For rollback, use the
img2txt-* deployment backup manifest; restore Compose/unit/code and daemon-reload.
Never disclose backed-up VPN JSON or environment files.


img2txt /health retains status=ok for application liveness and additionally
returns a sanitized cached llm state. The application's daemon probes only
GET /models through its configured LLM proxy, at most once per minute; no
images or generation jobs are sent. Invalid/missing API key, access denial,
rate limit and missing configured model are distinguished. Public project
status exposes only this state. Unknown or older-than-120-second checks cannot
produce a green LLM light. A failed provider key does not block VPN key rotation;
its separate red LLM light makes the project require attention.
