Deploy the embedded Go server directly on a non-burstable `c8g.xlarge` (4 vCPU, 8 GiB), encrypted 64 GiB gp3 root disk (6,000 IOPS), and a stable Elastic IP. The Go binary builds locally; catalog ingestion runs on EC2. The stack owns its VPC/subnet; HTTP 80 and HTTPS 443 are public and SSH 22 admits only your current IPv4 `/32`.

Live deployment: [https://pleasemendmy.com](https://pleasemendmy.com) and [https://www.pleasemendmy.com](https://www.pleasemendmy.com), proxied through Cloudflare to `32.193.25.68`; stack `please-mend-my`, `us-east-1`, instance `i-00b3468454b650217` (`aarch64`, 4 vCPU, 64 GiB root). The former 20 GiB instance was replaced; the Elastic IP and DNS were retained. Direct IP HTTP remains available for health probes. Cloudflare uses Full (strict) and redirects HTTP to HTTPS.

On 2026-11-08, application `4084210` deployed from `main` over the existing
verified SSH connection, retaining the cloud-built 207,691-entry catalog while
the additional sources build separately. The 24 shell responses match their
published SHA-256 hashes on apex, and apex/www serve the committed frontend.
HTML includes `Cache-Control: no-cache, no-transform`: Cloudflare's automatic
analytics injection otherwise changes HTML bytes and breaks offline shell
validation. Catalog conditional HEAD returns 304. Five separate page probes
gave median TTFB 151 ms before and 84 ms after; these small delivery samples do
not measure search latency or establish a causal improvement.

The full-source refresh reuses the existing cloud iFixit, WikiMed, and First Aid
runtimes without rewriting their hardlinked files. It runs the pinned Appropedia
and wikiHow ingesters on EC2, merges all five original source runtimes with
`merge_catalogs.py`, and passes that completed snapshot to
`build_catalog.py --source-runtime`. `please-mend-my-source-refresh.service`
limits preparation to two CPUs and 5 GiB; it writes
`/opt/please-mend-my/ingest/work/prepared-release.path` and leaves the live
catalog unchanged until separate validation and atomic publication. This avoids
reingesting older source runtimes merely because shared helper code changed.
Disposable `work.sqlite` files were unlinked while ingestion was inactive;
archives, source metadata, published catalogs and rollback binary were retained.

Preparation completed successfully as `a49d8bc3…502c4234`: 435,213 items,
713,438 media files, 17,814 filled nouns, 12,311 models and 61 diagrams.
It contains no structured-guide overlay. The refresh service is stopped, and
the live catalog remains the previous 207,691-item release while work focuses
on the local demo. Qwen batches are stopped at the user's request; cached
responses remain available, with no new calls authorized.

Initial measurements in this environment, before catalog integration:

- Loopback `GET /`, 5 warmups then 200 gzip/keep-alive transactions: p50 **0.076 ms**, p95 **0.092 ms**.
- Workstation network TTFB, 40 warm requests: p50 **31.81 ms**, p95 **35.18 ms**.
- Independent cold Firefox navigation: DOMContentLoaded **288 ms**, load **365 ms**, **32,011 bytes** transferred.

Systemd reported **11,894,784 bytes (~11.3 MiB)** of service memory. These samples describe these probes, not maximum capacity or latency from other locations. Verified compute pricing at deployment: **$0.15952/hour (~$116.45 per 730 hours)**, plus disk, public IPv4, and egress.

Historical iFixit-only releases follow. After the 2026-11-07 VM replacement, release `e592145` served the catalog built directly on EC2: **103,473 items and 575,002 media files**. The cloud-built index matches the original local SHA-256, `3e232983deb2ba8142ae84e13b4e99ba72ed0a86c10312beb77c0d4b922c98b4`. Subsequent deployments reused it without downloading/building or uploading local catalog data. Public HTTPS, deployed HTML/worker bytes and connection policies, and a sample guide/image were verified on apex/www. Page TTFB was **174 ms before replacement** (one sample), then **141 ms median afterward** (five separate requests). The complete compressed catalog response took **415 ms**, with **153 ms TTFB**, from this workstation. These small network samples are delivery checks, not search latency or capacity measurements. Go tests, six Python release/key tests, and atomic publication under the corrected systemd mount policy passed. A single writable parent mount allows the work-to-release rename; separately bind-mounting both directories caused `EXDEV` despite sharing the same disk.

Alias release `13d729d` was rebuilt and enriched on EC2, then verified on apex/www. Its public index matches the local enriched SHA-256 `501c452eb5399561a5ed564ff1a9a74f5baa38f116b3b579c5c84f349adb0a8c`: 99,208 enriched items and 21 pinned Wikidata classes, with all 103,473 source items retained. Public worker/index execution returned 155 matches for `macbook notebook battery` and 9,689 for `cellphone`, retained the 53 exact `Samsung Galaxy S23` matches, and made no search-time requests after its one index load. Go, 10 Python tests and four worker tests passed. Five-request page TTFB medians were 151 ms before and 112 ms after; full index delivery was 442 ms (176 ms TTFB). These network probes are not a capacity benchmark. See [worker/index measurements](../scripts/README.md) for the measured search computation, preparation, size and memory tradeoffs.

Search release `9d56b6f` was built on EC2 and verified publicly with all 103,473 items and 575,002 media files. Its prepared index SHA-256 is `68ed406d7ae313722a0f2652a0f119a5cbb04a63953f7759b01fe93e1bfd25c2`, identical to the local build; immutable release `62a5fef44d1ed224c9ef43ea5717b24f193bc668386b1bcec6489207dce87a34` contains 44,326 terms and 5,258 stem mappings. The public worker returns 2,364 matches for `laptop batteries`, 2,356 for `laptpp batery`, 5,124 for `refrxxerator`, and retains S23/S24 counts of 53/50, with one startup index fetch and no query-time requests. A sample guide opens successfully; apex/www serve the committed HTML, worker and background script. The background holds for three loads while fonts rotate on every load. Go, 19 Python tests and 12 worker tests pass. Five-request page TTFB medians were 160 ms before and 180 ms after (individual after samples 108–520 ms); compressed catalog delivery was 418 ms with 107 ms TTFB. These small network samples describe delivery variability, not worker search speed. Common-query worker p95 improves from 9.55 to 1.93 ms; the initial compressed index grows from 2,724,228 to 3,577,878 bytes. See the [full benchmark and limits](../scripts/README.md).

Light-theme follow-up `0f5aad8` serves black beam/wash/pool/seam shading in all seven variants, with the same prepared catalog. Local, apex and www CSS bytes match the committed file; the web service is active at the expected deployed commit. The final five page probes return HTTP 200 with 163 ms median TTFB (106–175 ms range). Both the three-load background cadence and the per-load font rotation remain in place.

The combined build adds 104,200 English WikiMed articles and 18 First Aid entries to iFixit: **207,691 entries and 679,729 media files**. On 2026-11-07, EC2 completed the same pinned pipeline on ARM64, downloading and building all three sources itself. Immutable release `f55559d274f784f9e90e11cc3b1bb9182dbc2b37abd16c236c8727873d6411c3` matches the local build: 9,965,255 compressed index bytes, SHA-256 `806b19edc9f55c34750f63bcca949fcf3457b614a58286e8ad5aaccfa6f0048d`, 190,591 surface terms, and 55,904 stem mappings. No local corpus was transferred. Completed cloud release reuse was verified without invoking ingestion; the earlier same-content release took 0.59 seconds including SSH.

Application release `d6d28d6` is active with that catalog on apex/www. Public worker/index checks confirm the full 207,691-entry inventory, a 100-result limit, no entries beyond result 100, one startup index request, and no search-time requests. A representative guide and image from every source return HTTP 200 with their own attribution/license. Local and public HTML, worker/search scripts, theme CSS, and background script match the committed files; `connect-src 'self'`, the three-load background cadence, black light-theme spotlight, and empty-result cleanup remain in place. Go, 59 Python, and 13 worker tests pass; 37 queries preserve exactly the previous ranking's best 100 results.

The browser keeps the full index and exposes the best 100 matches per query, with virtual cards and visible-only prefetch. Workstation worker preparation improved from 659 to 493 ms; broad `a` warm p50/p95 improved from 17.04/36.72 to 9.20/21.81 ms, while the 27-query suite p95 remained around 2.4 ms. These are Node worker measurements, not browser or network latency; index size and resident index memory are unchanged. See [full measurements, source pins, and pipeline usage](../scripts/README.md).

Five page-delivery probes after two warmups gave median TTFB of 55.94 ms before and 51.45 ms after. The complete compressed index grew from 3,577,878 to 9,965,255 bytes and took 749 ms before versus 1,541 ms after to download, with TTFB of 67/78 ms. These small workstation network samples measure delivery, not search computation or capacity, and do not establish a causal page-speed improvement. The 100-result limit does not reduce the full catalog's initial transfer cost.

Prerequisites: authenticated AWS CLI, Go matching `go.mod`, Python 3, Git, OpenSSH, and curl. Commit and push the ingestion scripts before deployment: EC2 fetches them directly from the public GitHub commit. From the repository:

```bash
aws login
AWS_REGION=us-east-1 bash deploy/ec2.sh
```

The script builds a static Linux/ARM64 binary locally from a pinned committed Git snapshot, chooses a zone offering `c8g.xlarge`, imports a project SSH public key, creates/updates CloudFormation stack `please-mend-my`, waits for cloud-init, prepares the cloud-built catalog, installs the binary atomically, restarts systemd, and checks `/healthz`. Before changing the live binary or catalog link, it verifies index/manifest readability and guide/media directory traversal as `nobody`; a failed access check leaves the active release untouched. Uncommitted workspace edits are excluded. Re-run it for updates. `AWS_PROFILE`, `AWS_REGION`, `PMM_STACK`, `PMM_SSH_KEY`, and `PMM_KEY_NAME` can override defaults; `PMM_DEPLOY_COMMIT` pins a specific pushed commit (default: HEAD). The imported key must match the local key and carry `Project=please-mend-my`; unrelated stacks/keys are rejected.

`catalog.sh` runs the same [Python build pipeline](../scripts/build_catalog.py) used locally: `python3 scripts/build_catalog.py`, with `--data-dir` and `--release-root` selecting server storage. EC2 fetches the complete script/configuration/reviewed-art list returned by `pipeline_files()` from the pushed GitHub commit and verifies each SHA-256 and the combined code fingerprint. It computes the release key using its own enrichment caches, and installs uv 0.13.5 / Python 3.12.12 plus pinned libzim 4.1.0, lxml 6.1.3, and PyMuPDF 1.28.2. It downloads both pinned Kiwix archives, the pinned Archive.org PDF, and OEWN directly; the same Stage 0–4 noun/link/archetype/search stages run there as locally; no workstation archive, SQLite database, source-response cache, or generated runtime is uploaded.

The iFixit source remains in `work/runtime`, with a `source-build.json` receipt keyed by its ingestion, alias-enrichment, and vocabulary inputs. WikiMed uses `work/wikimed/runtime`; First Aid uses `work/first-aid/runtime`. Each source prepares aliases before merging: pinned Wikidata names plus source ancestry/curated repair terms for iFixit, explicit redirect titles for WikiMed, and source bookmark headings/chapter terms for First Aid. The PDF retains the 2002 edition with Change 1 from 2004, every page's text and rendered image, the exact original, outline, and provenance; its rough chapter entries are intended for later LLM restructuring and custom entry pages. Raw sources and extraction metadata remain in the source caches.

`merge_catalogs.py` creates a fresh stage and hardlinks source articles/media while sorting items and remapping shared aliases. Source licenses and proofs stay distinct: ZIMs require pinned archive hashes plus the internal ZIM checksum, while the PDF requires its pinned hashes and successful document/page verification. The combined manifest and `ingest.json` retain every constituent source and pin. `prepare_search.py` then builds inverted postings and Porter stem mappings for the final combined order. Only a completed release is published; download, enrichment, merge, or indexing failure preserves the current live catalog. WikiMed's disposable SQLite databases disable durable per-batch writes; an interrupted scratch database is recreated directly rather than reading gigabytes through `DROP TABLE`. Raw archives and completed releases remain intact.

The unprivileged `please-mend-my-ingest.service` runs separately from the web server, limited to two CPUs and 5 GiB RAM with low CPU/I/O priority. A disconnected deployment leaves ingestion running; rerunning deployment waits for that build. Logs: `sudo journalctl -u please-mend-my-ingest -f`. Download/scratch state stays in `/opt/please-mend-my/ingest/work`; completed releases are atomically published under `/opt/please-mend-my/catalogs/<pipeline SHA-256>`. The fingerprint covers all pipeline scripts, noun/source/diagram configurations, reviewed art and license notices, local enrichment cache state, and pinned uv/Python versions. Matching completed releases skip download/build without a corpus scan; source input receipts also permit reuse when only merge/index stages change. Hardlinks and atomic publication require source caches and release storage on the same filesystem. Published runtime directories use mode 0755 so the web service's `DynamicUser` can traverse them; source payload permissions remain unchanged. The binary and catalog symlink switch together before systemd restarts with `CATALOG_DIR`; failed startup restores the prior binary/catalog configuration. Existing live releases remain available during later builds.

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
