"""Manage only Hardbox VPN resources; never flush shared firewall/routing tables."""
import subprocess
import sys
from pathlib import Path

NFT = "/usr/sbin/nft"
IP = "/usr/sbin/ip"
UNITS = ["hardbox-vpn-dhcp", "hardbox-vpn-hotspot", "hardbox-vpn", "hardbox-vpn-firewall"]

def run(*args, check=True, **kwargs):
    return subprocess.run(list(args), check=check, **kwargs)

def start_firewall():
    run("/usr/sbin/modprobe", "nft_tproxy")
    run("/usr/sbin/modprobe", "nf_tproxy_ipv4")
    rules = Path("/etc/hardbox-vpn/firewall.nft").read_text()
    existing = run(NFT, "list", "table", "inet", "hardbox_vpn", check=False, capture_output=True)
    if existing.returncode == 0:
        rules = "delete table inet hardbox_vpn\n" + rules
    run(NFT, "-c", "-f", "-", input=rules, text=True)
    run(IP, "route", "replace", "local", "0.0.0.0/0", "dev", "lo", "table", "177")
    current = run(IP, "-4", "rule", "show", capture_output=True, text=True).stdout
    owned_rule = next((line for line in current.splitlines() if line.startswith("1100:")), "")
    if not owned_rule:
        run(IP, "-4", "rule", "add", "priority", "1100", "fwmark", "0x01000000/0x01000000", "table", "177")
    elif "fwmark 0x1000000/0x1000000 lookup 177" not in owned_rule:
        raise RuntimeError("Policy-rule priority 1100 is already in use")
    run(NFT, "-f", "-", input=rules, text=True)

def stop_firewall():
    active = run("/usr/bin/nmcli", "-g", "GENERAL.CONNECTION", "device", "show", "wlan0", capture_output=True, text=True).stdout.strip()
    if active == "hardbox-vpn":
        # Never remove the leak guard while the AP is still broadcasting.
        run("/usr/bin/nmcli", "--wait", "15", "connection", "down", "hardbox-vpn")
    run(NFT, "delete", "table", "inet", "hardbox_vpn", check=False)
    run(IP, "-4", "rule", "del", "priority", "1100", "fwmark", "0x01000000/0x01000000", "table", "177", check=False)
    run(IP, "route", "del", "local", "0.0.0.0/0", "dev", "lo", "table", "177", check=False)

def rollback():
    run("systemctl", "disable", "--now", *[name + ".service" for name in UNITS], check=False)
    run("/usr/bin/nmcli", "--wait", "15", "connection", "down", "hardbox-vpn", check=False)
    stop_firewall()
    print("Hardbox VPN stopped; Ethernet, Docker and Tailscale were not modified", flush=True)

if __name__ == "__main__":
    actions = {"firewall-start": start_firewall, "firewall-stop": stop_firewall, "rollback": rollback}
    actions[sys.argv[1]]()
