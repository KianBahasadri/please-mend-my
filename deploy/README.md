Deploy the embedded Go server directly on a non-burstable `c8g.xlarge` (4 vCPU, 8 GiB), encrypted 64 GiB gp3 root disk (6,000 IOPS), and a stable Elastic IP. The Go binary builds locally; catalog ingestion runs on EC2. The stack owns its VPC/subnet; HTTP 80 and HTTPS 443 are public and SSH 22 admits only your current IPv4 `/32`.

Live deployment: [https://pleasemendmy.com](https://pleasemendmy.com) and [https://www.pleasemendmy.com](https://www.pleasemendmy.com), proxied through Cloudflare to `32.193.25.68`; stack `please-mend-my`, `us-east-1`, instance `i-00b3468454b650217` (`aarch64`, 4 vCPU, 64 GiB root). The former 20 GiB instance was replaced; the Elastic IP and DNS were retained. Direct IP HTTP remains available for health probes. Cloudflare uses Full (strict) and redirects HTTP to HTTPS.

Measured in this environment:

- Loopback `GET /`, 5 warmups then 200 gzip/keep-alive transactions: p50 **0.076 ms**, p95 **0.092 ms**.
- Workstation network TTFB, 40 warm requests: p50 **31.81 ms**, p95 **35.18 ms**.
- Independent cold Firefox navigation: DOMContentLoaded **288 ms**, load **365 ms**, **32,011 bytes** transferred.

Systemd reported **11,894,784 bytes (~11.3 MiB)** of service memory. These samples describe these probes, not maximum capacity or latency from other locations. Verified compute pricing at deployment: **$0.15952/hour (~$116.45 per 730 hours)**, plus disk, public IPv4, and egress.

Prerequisites: authenticated AWS CLI, Go matching `go.mod`, Python 3, Git, OpenSSH, and curl. Commit and push the ingestion scripts before deployment: EC2 fetches them directly from the public GitHub commit. From the repository:

```bash
aws login
AWS_REGION=us-east-1 bash deploy/ec2.sh
```

The script builds a static Linux/ARM64 binary locally, chooses a zone offering `c8g.xlarge`, imports a project SSH public key, creates/updates CloudFormation stack `please-mend-my`, waits for cloud-init, prepares the cloud-built catalog, installs the binary atomically, restarts systemd, and checks `/healthz`. Re-run it for updates. `AWS_PROFILE`, `AWS_REGION`, `PMM_STACK`, `PMM_SSH_KEY`, and `PMM_KEY_NAME` can override defaults. The imported key must match the local key and carry `Project=please-mend-my`; unrelated stacks/keys are rejected.

`catalog.sh` runs the same [Python build pipeline](../scripts/build_catalog.py) used locally. EC2 fetches the two Python scripts from the pushed GitHub commit, verifies their hashes, installs pinned uv/Python and script dependencies, and downloads the pinned Kiwix archive directly. It builds its own SQLite inventory, sanitized guides, images, and browser index. No local archive, SQLite database, or generated runtime files are uploaded; deployment does not depend on a local catalog.

The unprivileged `please-mend-my-ingest.service` runs separately from the web server, limited to two CPUs and 5 GiB RAM with low CPU/I/O priority. A disconnected deployment leaves ingestion running; rerunning deployment waits for that build. Logs: `sudo journalctl -u please-mend-my-ingest -f`. Download/scratch state stays in `/opt/please-mend-my/ingest/work`; completed releases are atomically published under `/opt/please-mend-my/catalogs/<pipeline SHA-256>`. The fingerprint covers both Python scripts and their pinned versions. Matching completed releases skip download/build; changed ingestion code creates a separate release. Completion/provenance metadata is checked without a catalog audit. The binary and catalog symlink switch together before systemd restarts with `CATALOG_DIR`; failed startup restores the prior binary/catalog configuration. Existing live releases remain available during later builds.

Private keys stay under `~/.ssh/`; local binary build files live in an automatically removed temporary directory. Project host keys use `~/.ssh/please-mend-my_known_hosts`, with trust on first use for a new IP and strict verification afterward. For an instance replacement at the same Elastic IP, deployment authenticates the new Ed25519 host key using AWS console output before updating the saved key. Other unexpected key changes stop deployment. No credentials or deployment state belong in version control.

The [public SSM AMI parameter](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/finding-an-ami-parameter-store.html) selects Amazon Linux 2023 ARM64 at stack deployment. Systemd runs the binary as a dynamic unprivileged user with only the capability to bind port 80. CloudFormation outputs `PublicIp`, `Url`, and `InstanceId`.

Inspect the deployment:

```bash
aws cloudformation describe-stacks --stack-name please-mend-my --query 'Stacks[0].Outputs'
ssh -i ~/.ssh/please-mend-my_ec2_ed25519 -o UserKnownHostsFile=~/.ssh/please-mend-my_known_hosts ec2-user@PUBLIC_IP 'sudo systemctl status please-mend-my; sudo journalctl -u please-mend-my -n 30 --no-pager'
```

Removing the CloudFormation stack terminates this instance and deletes its disk/network/Elastic IP; the imported SSH key remains available for reuse.

Cloudflare setup for `pleasemendmy.com` is explicit and separate from EC2 deployment. Fill `CLOUDFLARE_API_TOKEN` in the repository-root `.env` with a zone-scoped token granting Zone Read, DNS Edit, SSL and Certificates Edit, and Zone Settings Edit. `.env` is ignored by Git; keep it private (`chmod 600 .env`). The helper finds it relative to its own file, regardless of the current directory. A populated environment variable takes precedence; the existing private `~/.config/please-mend-my/cloudflare-token` remains a fallback. Simple unquoted or single/double-quoted values work; the file is never executed. Python 3 and OpenSSL are needed locally; never commit credentials.

```bash
python3 deploy/cloudflare.py                 # Read only: zone, apex/www DNS, SSL settings.
python3 deploy/cloudflare.py origin-cert     # Issue/reuse local P-256 Origin CA certificate.
AWS_REGION=us-east-1 bash deploy/ec2.sh       # Install binary and local TLS credentials.
# Verify origin TLS and hostname using Cloudflare's Origin CA trust before switching DNS.
python3 deploy/cloudflare.py sync            # Explicitly apply DNS and SSL changes.
```

Certificates stay at `~/.config/please-mend-my/tls/origin.crt` and `origin.key` (`PMM_TLS_DIR` overrides). If both exist, `ec2.sh` uploads them through a private temporary directory, root-installs them under `/etc/please-mend-my/tls`, and adds this systemd drop-in before restart:

```ini
[Service]
LoadCredential=tls.crt:/etc/please-mend-my/tls/origin.crt
LoadCredential=tls.key:/etc/please-mend-my/tls/origin.key
Environment=TLS_CERT=%d/tls.crt
Environment=TLS_KEY=%d/tls.key
```

The Go process serves TLS directly on 443 and keeps HTTP 80 active. With neither local TLS file, deployment stays HTTP-only and preserves any existing remote TLS drop-in; exactly one file is rejected. Keep both local files for redeployments or instance replacement. `cloudflare.py sync` creates/updates only a proxied apex A record to `32.193.25.68` and proxied `www` CNAME to the apex, sets Full (strict) and Always Use HTTPS, and refuses conflicting/duplicate DNS records without deleting anything. It never changes MX/TXT or unrelated names. The [Origin CA documentation](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/) explains certificate issuance and trust; these origin certificates are for Cloudflare-to-server TLS, while Cloudflare presents its publicly trusted edge certificate to browsers.

The final native TLS configuration uses X25519/P-256 ECDHE: the default hybrid negotiation previously took ~3.1 seconds on the tested origin path; verified TLS 1.3/HTTP2 now completes without that delay. Public authoritative DNS and Cloudflare DNS-over-HTTPS return the proxied addresses. This workstation's upstream resolver temporarily retained a negative apex result; `www` resolved normally, and apex HTTPS was independently verified from EC2 and with the public edge address. No system DNS or hosts-file overrides were made.
