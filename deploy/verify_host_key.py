#!/usr/bin/env python3
"""Extract an Ed25519 host key from authenticated EC2 console output."""
import base64
import ipaddress
from pathlib import Path
import re
import sys


def console_host_key(console, address):
    ipaddress.ip_address(address)
    if "ssh-ed25519 " not in console:
        try:
            console = base64.b64decode(console.strip(), validate=True).decode("utf-8", errors="replace")
        except (ValueError, UnicodeError):
            return None
    section = re.search(r"-----BEGIN SSH HOST KEY KEYS-----([\s\S]*?)-----END SSH HOST KEY KEYS-----", console)
    if not section:
        return None
    key = re.search(r"(?:^|\n)ssh-ed25519 ([A-Za-z0-9+/]+={0,2})(?:\s|$)", section[1])
    if not key:
        return None
    wire = base64.b64decode(key[1], validate=True)
    if len(wire) != 51 or wire[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
        raise ValueError("Console contained an invalid Ed25519 public key.")
    return f"{address} ssh-ed25519 {key[1]}"


if __name__ == "__main__":
    key = console_host_key(Path(sys.argv[1]).read_text(), sys.argv[2])
    if not key:
        sys.exit(1)
    print(key)
