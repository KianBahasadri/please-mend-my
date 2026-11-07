#!/usr/bin/env python3
"""Inspect by default; explicitly issue Origin CA certificates or sync DNS/SSL."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CONFIG = Path.home() / ".config" / "please-mend-my"


class Cloudflare:
    def __init__(self):
        self.token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not self.token:
            token_file = CONFIG / "cloudflare-token"
            if token_file.stat().st_mode & 0o077:
                raise ValueError(f"Token file must be private: chmod 600 {token_file}")
            self.token = token_file.read_text().strip()
        if not self.token or any(char.isspace() for char in self.token):
            raise ValueError("Provide one Cloudflare API token in the environment or private token file.")

    def call(self, path, method="GET", body=None, query=None):
        url = "https://api.cloudflare.com/client/v4" + path
        if query:
            url += "?" + urlencode(query)
        request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                          method=method)
        try:
            with urlopen(request, timeout=25) as response:
                payload = json.load(response)
        except HTTPError as error:
            try:
                payload = json.load(error)
            except (ValueError, OSError):
                raise ValueError(f"Cloudflare {method} {path}: HTTP {error.code}") from None
            detail = "; ".join(f"{item.get('code')}: {item.get('message', 'API error')}"
                               for item in payload.get("errors", []))
            raise ValueError(f"Cloudflare {method} {path}: {detail}".replace(self.token, "[redacted]")) from None
        if not payload.get("success"):
            detail = "; ".join(str(item.get("message", "API error")) for item in payload.get("errors", []))
            raise ValueError(f"Cloudflare {method} {path}: {detail}".replace(self.token, "[redacted]"))
        return payload

    def result(self, path, **kwargs):
        return self.call(path, **kwargs)["result"]


def zone_for(api, domain):
    zones = api.result("/zones", query={"name": domain, "per_page": 50})
    zones = [zone for zone in zones if zone["name"] == domain]
    if len(zones) != 1:
        raise ValueError(f"Expected exactly one accessible zone for {domain}; found {len(zones)}.")
    return zones[0]


def records_for(api, zone_id, name):
    payload = api.call(f"/zones/{zone_id}/dns_records", query={"name": name, "per_page": 100})
    if payload.get("result_info", {}).get("total_pages", 1) > 1:
        raise ValueError(f"More than 100 DNS records at {name}; inspect manually.")
    return [record for record in payload["result"] if record["name"].rstrip(".").lower() == name]


def inspect(api, zone, domain):
    records = []
    other_types = {}
    for name in (domain, "www." + domain):
        found = records_for(api, zone["id"], name)
        records.extend({key: record.get(key) for key in ("id", "type", "name", "content", "proxied", "ttl")}
                       for record in found if record["type"] in ("A", "AAAA", "CNAME"))
        other_types[name] = sorted({record["type"] for record in found if record["type"] not in ("A", "AAAA", "CNAME")})
    settings = {name: api.result(f"/zones/{zone['id']}/settings/{name}")["value"]
                for name in ("ssl", "always_use_https")}
    print(json.dumps({"zone": {key: zone.get(key) for key in ("id", "name", "status", "name_servers")},
                      "records": records, "other_record_types": other_types, "settings": settings}, indent=2))


def openssl(*args, check=True):
    result = subprocess.run(["openssl", *map(str, args)], capture_output=True, check=False)
    if check and result.returncode:
        raise ValueError("OpenSSL operation failed; inspect local certificate/key files.")
    return result


def origin_cert(api, domain):
    directory = Path(os.environ.get("PMM_TLS_DIR", str(CONFIG / "tls"))).expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    key, csr, cert = (directory / filename for filename in ("origin.key", "origin.csr", "origin.crt"))
    if cert.exists():
        if not key.exists():
            raise ValueError("Origin certificate exists without its private key; refusing to replace it.")
        valid = openssl("x509", "-in", cert, "-checkend", "86400", "-noout", check=False).returncode == 0
        host = openssl("x509", "-in", cert, "-checkhost", domain, "-noout", check=False).returncode == 0
        www = openssl("x509", "-in", cert, "-checkhost", "www." + domain, "-noout", check=False).returncode == 0
        same_key = openssl("x509", "-in", cert, "-pubkey", "-noout").stdout == openssl("pkey", "-in", key, "-pubout").stdout
        if not (valid and host and www and same_key):
            raise ValueError("Existing origin certificate/key is invalid, expiring, or for another domain; inspect before replacing.")
    else:
        if not key.exists():
            openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", key)
        key.chmod(0o600)
        openssl("req", "-new", "-sha256", "-key", key, "-out", csr, "-subj", f"/CN={domain}",
                "-addext", f"subjectAltName=DNS:{domain},DNS:*.{domain}")
        result = api.result("/certificates", method="POST", body={"csr": csr.read_text(),
                            "hostnames": [domain, "*." + domain], "request_type": "origin-ecc", "requested_validity": 5475})
        certificate = result.get("certificate", "")
        if not certificate.startswith("-----BEGIN CERTIFICATE-----"):
            raise ValueError("Cloudflare returned no PEM certificate; local key/CSR retained for inspection.")
        cert.write_text(certificate + ("" if certificate.endswith("\n") else "\n"))
        cert.chmod(0o600)
    key.chmod(0o600)
    print(cert)
    print(key)


def sync(api, zone, domain, address):
    planned = []
    for record_type, name, content in (("A", domain, address), ("CNAME", "www." + domain, domain)):
        records = records_for(api, zone["id"], name)
        addresses = [record for record in records if record["type"] in ("A", "AAAA", "CNAME")]
        if len(addresses) > 1 or any(record["type"] != record_type for record in addresses):
            raise ValueError(f"Conflicting/duplicate address records at {name}; inspect and reconcile before sync.")
        if record_type == "CNAME" and any(record["type"] != "CNAME" for record in records):
            raise ValueError(f"Other DNS records exist at {name}; refusing to replace them with a CNAME.")
        desired = {"type": record_type, "name": name, "content": content, "proxied": True, "ttl": 1}
        planned.append((addresses[0] if addresses else None, desired))

    # Preflight both names before writing. No DNS record is ever deleted.
    changes = []
    for setting, desired in (("ssl", "strict"), ("always_use_https", "on")):
        path = f"/zones/{zone['id']}/settings/{setting}"
        if api.result(path)["value"] != desired:
            api.result(path, method="PATCH", body={"value": desired})
            changes.append(f"{setting}: {desired}")
    for existing, desired in planned:
        path = f"/zones/{zone['id']}/dns_records"
        if existing is None:
            api.result(path, method="POST", body=desired)
            changes.append(f"Created {desired['type']} {desired['name']}")
        elif any(existing.get(key) != value for key, value in desired.items()):
            api.result(path + "/" + existing["id"], method="PATCH", body=desired)
            changes.append(f"Updated {desired['type']} {desired['name']}")
    print(json.dumps({"changes": changes}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="inspect", choices=("inspect", "origin-cert", "sync"))
    parser.add_argument("--domain", default="pleasemendmy.com")
    parser.add_argument("--ip", default="32.193.25.68", help="Origin IPv4 address used by sync")
    args = parser.parse_args()
    domain = args.domain.rstrip(".").lower()
    if "." not in domain or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain.split(".")):
        raise ValueError("Invalid DNS domain.")
    address = str(ipaddress.IPv4Address(args.ip))
    os.umask(0o077)
    api = Cloudflare()
    zone = zone_for(api, domain)
    if args.command == "origin-cert":
        origin_cert(api, domain)
    elif args.command == "sync":
        sync(api, zone, domain, address)
    else:
        inspect(api, zone, domain)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, URLError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
