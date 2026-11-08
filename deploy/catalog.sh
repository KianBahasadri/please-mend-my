#!/usr/bin/env bash
# Build an immutable catalog on EC2 from public sources, never local corpus files.
set -euo pipefail
umask 077

catalog_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
catalog_repo_dir=$(cd -- "$catalog_script_dir/.." && pwd)
catalog_ip=${PMM_ORIGIN_IP:?Set PMM_ORIGIN_IP to the project EC2 address.}
catalog_key=${PMM_SSH_KEY:-$HOME/.ssh/please-mend-my_ec2_ed25519}
catalog_hosts=$HOME/.ssh/please-mend-my_known_hosts
[[ "$catalog_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { printf 'Invalid catalog host IPv4.\n' >&2; exit 1; }
for catalog_tool in python3 git ssh; do
  command -v "$catalog_tool" >/dev/null || { printf 'Missing command: %s\n' "$catalog_tool" >&2; exit 1; }
done
catalog_commit=${PMM_DEPLOY_COMMIT:-$(git -C "$catalog_repo_dir" rev-parse HEAD)}
catalog_digest=$(PYTHONPATH="$catalog_repo_dir/scripts" python3 -c 'from build_catalog import input_key, pipeline_files; print(input_key(pipeline_files()))')
catalog_sources=$(python3 - "$catalog_repo_dir" <<'PY'
import base64, hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'scripts'))
from build_catalog import pipeline_files
sources = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in pipeline_files()}
print(base64.b64encode(json.dumps(sources, sort_keys=True).encode()).decode())
PY
)
catalog_ssh=(-i "$catalog_key" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5 \
  -o "UserKnownHostsFile=$catalog_hosts" -o StrictHostKeyChecking=yes -o LogLevel=ERROR)
ssh "${catalog_ssh[@]}" "ec2-user@$catalog_ip" sudo bash -s -- \
  "$catalog_commit" "$catalog_digest" "$catalog_sources" <<'REMOTE'
set -euo pipefail
umask 022
commit=$1
digest=$2
source_manifest=$3
[[ "$commit" =~ ^[0-9a-f]{40}$ && "$digest" =~ ^[0-9a-f]{64}$ && "$source_manifest" =~ ^[A-Za-z0-9+/=]+$ ]]
exec 9>/run/please-mend-my-catalog.lock
flock -n 9 || { printf 'Another deployment is preparing the cloud catalog.\n' >&2; exit 1; }
base=/opt/please-mend-my/ingest
source_dir=$base/source/$commit
release_root=/opt/please-mend-my/catalogs
worker=please-mend-my-ingest
if ! id "$worker" >/dev/null 2>&1; then
  useradd --system --home-dir "$base" --shell /sbin/nologin "$worker"
fi
install -d -o root -g root -m 0755 "$base" "$source_dir/scripts"
for directory in work cache python tools config; do
  install -d -o "$worker" -g "$worker" -m 0755 "$base/$directory"
done
install -d -o "$worker" -g "$worker" -m 0755 "$release_root"
python3 - "$source_dir" "$commit" "$source_manifest" <<'PY'
import base64, hashlib, json, re, sys, time, urllib.request
from pathlib import Path, PurePosixPath
root, commit = Path(sys.argv[1]), sys.argv[2]
sources = json.loads(base64.b64decode(sys.argv[3], validate=True))
if not isinstance(sources, dict) or not 1 <= len(sources) <= 100:
    raise SystemExit('Invalid pipeline source manifest')
for name, expected in sorted(sources.items()):
    relative = PurePosixPath(name)
    if (relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in {'scripts', 'docs'}
            or not re.fullmatch(r'[a-zA-Z0-9_./-]+', name) or not re.fullmatch(r'[0-9a-f]{64}', expected)):
        raise SystemExit('Unsafe pipeline source path or hash')
    destination = root / name
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        continue
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = destination.with_name(destination.name + '.tmp')
    for attempt in range(3):
        try:
            address = f'https://raw.githubusercontent.com/KianBahasadri/please-mend-my/{commit}/{name}'
            with urllib.request.urlopen(address, timeout=120) as response:
                body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != expected:
                raise ValueError('Pinned pipeline source hash mismatch: ' + name)
            temporary.write_bytes(body)
            temporary.chmod(0o644)
            temporary.replace(destination)
            break
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
PY
[[ "$(PYTHONPATH="$source_dir/scripts" python3 -c 'from build_catalog import input_key, pipeline_files; print(input_key(pipeline_files()))')" == "$digest" ]]
# Enrichment caches belong to this host. Their fingerprint must never come from
# the workstation: no generated response cache or corpus is uploaded.
digest=$(python3 "$source_dir/scripts/build_catalog.py" --data-dir "$base/work" --print-key)
release=$release_root/$digest
if [[ ! -x "$base/tools/uv" ]] || [[ "$("$base/tools/uv" --version | cut -d' ' -f1-2)" != 'uv 0.13.5' ]]; then
  curl --fail --silent --show-error --location --retry 3 --connect-timeout 10 --max-time 120 \
    https://astral.sh/uv/0.13.5/install.sh -o "$base/uv-install.sh"
  sudo -u "$worker" env UV_INSTALL_DIR="$base/tools" UV_NO_MODIFY_PATH=1 XDG_CONFIG_HOME="$base/config" sh "$base/uv-install.sh" >&2
  rm -f "$base/uv-install.sh"
fi
state=$(systemctl show please-mend-my-ingest.service --property=ActiveState --value 2>/dev/null || true)
run_ingestion=false
if [[ "$state" == activating ]]; then
  [[ -f "$base/current-key" && "$(cat "$base/current-key")" == "$digest" ]] || {
    printf 'A different cloud ingestion is already running; leave it undisturbed.\n' >&2; exit 1;
  }
  run_ingestion=true
elif [[ ! -f "$release/ingest.json" ]]; then
  cat > /etc/systemd/system/please-mend-my-ingest.service <<UNIT
[Unit]
Description=Build repair and medical catalogs from public sources on EC2
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$worker
Group=$worker
WorkingDirectory=$base
Environment=HOME=$base
Environment=XDG_CONFIG_HOME=$base/config
Environment=UV_CACHE_DIR=$base/cache
Environment=UV_PYTHON_INSTALL_DIR=$base/python
ExecStart=/usr/bin/python3 $source_dir/scripts/build_catalog.py --uv $base/tools/uv --data-dir $base/work --release-root $release_root
TimeoutStartSec=infinity
CPUQuota=200%
CPUWeight=10
MemoryMax=5G
MemorySwapMax=0
OOMPolicy=stop
IOWeight=10
Nice=10
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
# One writable mount permits atomic renames from work/runtime to catalogs.
# Root-owned code/binaries remain protected by normal file permissions.
ReadWritePaths=/opt/please-mend-my
UMask=0022
UNIT
  printf '%s\n' "$digest" > "$base/current-key"
  systemctl daemon-reload
  run_ingestion=true
fi
if [[ "$run_ingestion" == true ]]; then
  printf 'EC2 is downloading and ingesting the pinned Kiwix archives.\n' >&2
  if ! systemctl start please-mend-my-ingest.service; then
    journalctl -u please-mend-my-ingest.service -n 35 --no-pager >&2
    exit 1
  fi
fi
if [[ ! -f "$release/ingest.json" ]]; then
  journalctl -u please-mend-my-ingest.service -n 35 --no-pager >&2
  exit 1
fi
# Inspect completion metadata only; never audit the finished corpus during deploy.
sudo -u "$worker" python3 "$source_dir/scripts/build_catalog.py" \
  --uv "$base/tools/uv" --data-dir "$base/work" --release-root "$release_root" >&2
printf '%s\n' "$release"
REMOTE
