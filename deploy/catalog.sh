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
catalog_commit=$(git -C "$catalog_repo_dir" rev-parse HEAD)
catalog_digest=$(python3 "$catalog_repo_dir/scripts/build_catalog.py" --print-key)
read -r catalog_ingest_hash catalog_builder_hash < <(python3 - "$catalog_repo_dir" <<'PY'
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1]) / 'scripts'
print(*(hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ('ingest_ifixit.py', 'build_catalog.py')))
PY
)
catalog_ssh=(-i "$catalog_key" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5 \
  -o "UserKnownHostsFile=$catalog_hosts" -o StrictHostKeyChecking=yes -o LogLevel=ERROR)
ssh "${catalog_ssh[@]}" "ec2-user@$catalog_ip" sudo bash -s -- \
  "$catalog_commit" "$catalog_digest" "$catalog_ingest_hash" "$catalog_builder_hash" <<'REMOTE'
set -euo pipefail
umask 022
commit=$1
digest=$2
ingest_hash=$3
builder_hash=$4
[[ "$commit" =~ ^[0-9a-f]{40}$ && "$digest" =~ ^[0-9a-f]{64}$ &&
   "$ingest_hash" =~ ^[0-9a-f]{64}$ && "$builder_hash" =~ ^[0-9a-f]{64}$ ]]
exec 9>/run/please-mend-my-catalog.lock
flock -n 9 || { printf 'Another deployment is preparing the cloud catalog.\n' >&2; exit 1; }
base=/opt/please-mend-my/ingest
source_dir=$base/source/$commit
release_root=/opt/please-mend-my/catalogs
release=$release_root/$digest
worker=please-mend-my-ingest
if ! id "$worker" >/dev/null 2>&1; then
  useradd --system --home-dir "$base" --shell /sbin/nologin "$worker"
fi
install -d -o root -g root -m 0755 "$base" "$source_dir/scripts"
for directory in work cache python tools; do
  install -d -o "$worker" -g "$worker" -m 0755 "$base/$directory"
done
install -d -o "$worker" -g "$worker" -m 0755 "$release_root"
fetch_script() {
  local name=$1 expected=$2 destination=$source_dir/scripts/$1
  if [[ ! -f "$destination" ]] || [[ "$(sha256sum "$destination" | cut -d' ' -f1)" != "$expected" ]]; then
    curl --fail --silent --show-error --location --retry 3 --connect-timeout 10 --max-time 120 \
      "https://raw.githubusercontent.com/KianBahasadri/please-mend-my/$commit/scripts/$name" -o "$destination.tmp"
    printf '%s  %s\n' "$expected" "$destination.tmp" | sha256sum --check --status
    chmod 0644 "$destination.tmp"
    mv "$destination.tmp" "$destination"
  fi
}
fetch_script ingest_ifixit.py "$ingest_hash"
fetch_script build_catalog.py "$builder_hash"
[[ "$(python3 "$source_dir/scripts/build_catalog.py" --print-key)" == "$digest" ]]
if [[ ! -x "$base/tools/uv" ]] || [[ "$("$base/tools/uv" --version | cut -d' ' -f1-2)" != 'uv 0.13.5' ]]; then
  curl --fail --silent --show-error --location --retry 3 --connect-timeout 10 --max-time 120 \
    https://astral.sh/uv/0.13.5/install.sh -o "$base/uv-install.sh"
  sudo -u "$worker" env UV_INSTALL_DIR="$base/tools" UV_NO_MODIFY_PATH=1 sh "$base/uv-install.sh" >&2
  rm -f "$base/uv-install.sh"
fi
state=$(systemctl show please-mend-my-ingest.service --property=ActiveState --value 2>/dev/null || true)
if [[ "$state" == activating ]]; then
  [[ -f "$base/current-key" && "$(cat "$base/current-key")" == "$digest" ]] || {
    printf 'A different cloud ingestion is already running; leave it undisturbed.\n' >&2; exit 1;
  }
elif [[ ! -f "$release/ingest.json" ]]; then
  cat > /etc/systemd/system/please-mend-my-ingest.service <<UNIT
[Unit]
Description=Build the repair catalog from public sources on EC2
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$worker
Group=$worker
WorkingDirectory=$base
Environment=HOME=$base
Environment=UV_CACHE_DIR=$base/cache
Environment=UV_PYTHON_INSTALL_DIR=$base/python
ExecStart=/usr/bin/python3 $source_dir/scripts/build_catalog.py --uv $base/tools/uv --data-dir $base/work --release-root $release_root
TimeoutStartSec=infinity
CPUQuota=200%
CPUWeight=10
MemoryMax=5G
IOWeight=10
Nice=10
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=$base/work $base/cache $base/python $release_root
UMask=0022
UNIT
  printf '%s\n' "$digest" > "$base/current-key"
  systemctl daemon-reload
  systemctl reset-failed please-mend-my-ingest.service
  systemctl start --no-block please-mend-my-ingest.service
  printf 'EC2 is downloading and ingesting the pinned Kiwix archive.\n' >&2
fi
while [[ "$(systemctl show please-mend-my-ingest.service --property=ActiveState --value)" == activating ]]; do
  sleep 3
done
if [[ ! -f "$release/ingest.json" ]]; then
  journalctl -u please-mend-my-ingest.service -n 35 --no-pager >&2
  exit 1
fi
# Inspect completion metadata only; never audit the finished corpus during deploy.
sudo -u "$worker" python3 "$source_dir/scripts/build_catalog.py" \
  --uv "$base/tools/uv" --data-dir "$base/work" --release-root "$release_root" >&2
printf '%s\n' "$release"
REMOTE
