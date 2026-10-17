#!/usr/bin/env python3
"""Daily list refresh through the same serialized, transactional controller."""
import json
import socket
import time
import uuid


def request(payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect("/run/pi-vpn/control.sock")
        client.sendall(json.dumps(payload).encode() + b"\n")
        with client.makefile("rb") as stream:
            result = json.loads(stream.readline(32769))
    if not result.get("ok"):
        raise RuntimeError("VPN list refresh was not accepted")
    return result


if __name__ == "__main__":
    identifier = str(uuid.uuid4())
    request({"operation": "refresh-rules", "request_id": identifier})
    for _ in range(120):
        job = request({"operation": "latest", "request_id": identifier}).get("job")
        if job and job["state"] == "success":
            print("Domain lists checked successfully")
            break
        if job and job["state"] == "error":
            raise SystemExit("Domain lists were not updated; previous rules retained")
        time.sleep(2)
    else:
        raise SystemExit("Domain list update is still pending; inspect the controller")
