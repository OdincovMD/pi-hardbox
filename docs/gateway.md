# Hardbox VPN Wi-Fi gateway

Read this document before changing this project. Raspberry Pi 5, Raspberry Pi
OS / Debian 13 ARM64, SSH alias `raspberry`. The status dashboard documentation
is `/opt/pi-dashboard/docs/README.md`. Both project locations were checked for
`.gitnexus`; no index existed when this service was created.

## Traffic contract

Ethernet `eth0` remains the ordinary default route. Host applications, dashboard,
Docker containers and Tailscale retain their existing networking. Only packets
arriving through `wlan0` enter the dedicated `inet hardbox_vpn` nftables table.
No shared firewall table is flushed and no OUTPUT interception is installed.

SSID `Hardbox-VPN`, WPA2 AES, 2.4GHz channel 6, AP/client isolation. Subnet
`10.77.0.0/24`, gateway `10.77.0.1`, DHCP `.100`–`.200`. NetworkManager manages
the AP with a manual address and never-default; its generic shared/NAT mode is
not used. IPv6 is disabled on this AP and explicitly blocked on ingress.

Xray VLESS/REALITY binds its outbound to `eth0`. Transparent TCP/UDP listeners
are loopback-only at 12345 and 12346. DNS on port 53 is directed through VLESS
to the requested resolver; DHCP advertises 1.1.1.1 and 9.9.9.9. A diagnostics
SOCKS endpoint is loopback-only at 11808.
Russian service domain exceptions use a separate eth0-bound direct outbound;
all other connections keep VLESS. No automatic direct fallback is configured. Mark
`0x01000000/0x01000000`, IPv4 policy-rule priority 1100, table 177 deliver only
the intercepted traffic locally. The mark is separate from Tailscale marks.

The Wi-Fi chain drops unsupported protocols and blocks multicast/broadcast
except DHCP. Forwarding from/to wlan0 is blocked as a second leak guard.
Clients cannot reach host administrative services or bypass VLESS into the LAN.
ICMP echo through VLESS is unsupported; test internet using HTTPS/DNS, not ping.
If Xray stops, all client internet is blocked; the server stays online. If only
the upstream VPN fails, VPN destinations and their DNS fail closed. Existing
direct connections may continue, but new lookups still require the VPN DNS.
HTTP/3/QUIC support depends on the upstream Vision service; browsers may use TCP.

## Files and secrets

- `/usr/local/lib/hardbox-vpn/xray`: official ARM64 binary, release and verified
  SHA256 recorded in `/opt/hardbox-vpn/release.json`.
- `/etc/hardbox-vpn/xray.json`: owner-supplied connection, root-only 0600.
- `/etc/hardbox-vpn/wifi.json`: SSID and generated password, root-only 0600.
- `/etc/NetworkManager/system-connections/hardbox-vpn.nmconnection`: 0600.
- `/etc/hardbox-vpn/firewall.nft`: owned firewall rules only.
- `/etc/hardbox-vpn/dnsmasq.conf`: DHCP only; no system DNS changes.
- `/var/lib/hardbox-vpn/backups/`: pre-install routes/rules/firewall snapshots.
- `/opt/hardbox-vpn/manage.py`: scoped firewall and rollback operations.

Never publish the VPN credentials or Wi-Fi password in public dashboard output,
URLs, logs or documentation. Inspect credentials only over authenticated SSH.

## Lifecycle

Four systemd units: `hardbox-vpn-firewall`, `hardbox-vpn`,
`hardbox-vpn-hotspot`, `hardbox-vpn-dhcp`. The AP requires the firewall and starts
only after its protection is installed. The NetworkManager profile itself has
autoconnect disabled; systemd owns safe boot ordering. DHCP depends on the AP.

An initial 15-minute systemd rollback timer stops the new services if setup is
not verified. Cancel it only after verifying direct host egress, public website,
Docker, transparent TCP+UDP/DNS, and a fail-closed test.

To stop the hotspot and its services safely:

```sh
sudo python3 /opt/hardbox-vpn/manage.py rollback
```

Rollback stops/disables only these four units, brings this AP down, removes only
the dedicated nftables table and policy route/rule, and leaves eth0, Docker and
Tailscale intact. It keeps credential/config files for inspection.

## Verification

First test the VLESS key through a loopback SOCKS listener with no routing
changes. Then verify the full interception using a temporary Linux network
namespace/veth as a client of the same firewall chain. Test TCP, UDP DNS, and
failure with Xray deliberately stopped. Remove the temporary namespace/rules.
Finally test actual association/DHCP from a phone on the advertised SSID.

Verified 2026-10-17: an isolated namespace client successfully resolved DNS over
UDP and TCP, used alternate public DNS through the tunnel, and accessed HTTPS
through a different public IP from the host. Stopping Xray blocked both client
HTTPS and DNS while the host/dashboard stayed online; restarting recovered the
client. Existing Docker IPv4/IPv6 firewall rules were unchanged. The AP and DHCP
services are active. The owner confirmed phone association and internet access after setup.


## Website VPN management

The owner-enabled /vpn page is documented in /opt/pi-dashboard/docs/README.md
and UX-CONTRACT.md. pi-vpn-controller.service exposes cached safe status over a
local Unix socket and validates/probes new VLESS keys before applying them.
Updates use root-only dashboard-last-good.json and dashboard-update-pending.json
under /var/lib/hardbox-vpn for rollback/crash recovery. The webpage uses the same PAM-authenticated
admin session as fan and reboot controls and never reads back the current VLESS link. Wi-Fi
passwords, the AP profile, isolation rules and ordinary host/container routing
are not changed by a VPN-key update.

## Domain split routing

`/opt/pi-dashboard/vpn_routing.py` builds one complete policy in the root-only
Xray config. Priority: loopback statistics API → private/reserved destination
block → diagnostics/DNS forced VPN → owner's VPN domains → owner's direct
domains → selected Russian service groups direct → all remaining TCP/UDP VPN.
The private destination guard also prevents a direct exception from opening
the router, server services or other LAN resources to hotspot clients.

HTTP/TLS/QUIC sniffing is enabled only for transparent client connections with
`routeOnly: true`. No TLS decryption, content inspection or browsing log is
enabled. Hidden/unrecognized domains remain through VPN. IPv6 stays disabled.
Encrypted DNS used by clients is ordinary VPN traffic unless explicitly covered
by a direct-domain exception; it is not decoded by the gateway.

The base list comes from v2fly/domain-list-community's eight selected groups:
category-gov-ru, vk, yandex, mailru-group, category-bank-ru, ozon, wildberries,
avito, including their dependencies. The source is fetched over HTTPS at a
single immutable commit, parsed with size/format/completeness limits, and never
executed or extracted to the filesystem. No generic .ru/.рф or country IP rule
is used. The brand TLD .yandex is an explicit exception. Upstream list changes
may fail validation; the prior installed list then remains active. Source:
https://github.com/v2fly/domain-list-community (MIT license).

`hardbox-vpn-routing-refresh.timer` checks daily around 04:30 Europe/Moscow
with up to 20 minutes jitter and Persistent=true. It uses the same serialized
controller transaction as the website. Same-commit checks do not restart VPN.
New rules are syntax-checked, atomically installed and checked through the
VPN-only diagnostics endpoint; failure restores the previous complete config.
The key and existing owner's exceptions are preserved during list updates.
Manual rules are preserved during key changes. Pending-marker recovery covers
all changes; only one update is allowed at a time.

Xray StatsService binds only 127.0.0.1:11810. It exposes aggregate outbound
up/down byte counters to the local controller. Public data includes totals,
counts, list group names and timestamps, never owner's domain lists, request
destinations or client identifiers. Counters reset when Xray restarts and
include VPN DNS and controller probes. The actual key-change timestamp is kept
separately in /var/lib/hardbox-vpn/key-updated.json.

Deployment recovery: keep local backups outside this repository. Stop the
routing refresh timer before restoring a matching controller/config snapshot.
Recovery files may contain credentials and must remain root-only. Never restore
shared host firewall snapshots just to revert a domain-list change.

The shared PAM/session and three-route UI contract is documented in
/opt/pi-dashboard/docs/README.md. All gateway units and the daily timer remain
enabled on boot; changing website authentication does not restart the gateway.
