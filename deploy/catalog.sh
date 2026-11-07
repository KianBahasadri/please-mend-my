#!/usr/bin/env bash
# Prepare an immutable catalog release without changing the running service.
set -euo pipefail
umask 077

catalog_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
catalog_repo_dir=$(cd -- "$catalog_script_dir/.." && pwd)
catalog_source=${PMM_CATALOG_DIR:-$catalog_repo_dir/catalog-data/runtime}
catalog_ip=${PMM_ORIGIN_IP:?Set PMM_ORIGIN_IP to the project EC2 address.}
catalog_key=${PMM_SSH_KEY:-$HOME/.ssh/please-mend-my_ec2_ed25519}
catalog_hosts=$HOME/.ssh/please-mend-my_known_hosts
[[ "$catalog_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { printf 'Invalid catalog host IPv4.\n' >&2; exit 1; }

for catalog_tool in python3 rsync ssh; do
  command -v "$catalog_tool" >/dev/null || { printf 'Missing command: %s\n' "$catalog_tool" >&2; exit 1; }
done

# Read only the published completion metadata and index, never the raw archive.
catalog_digest=$(python3 - "$catalog_source" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
manifest_bytes = (root / 'manifest.json').read_bytes()
manifest = json.loads(manifest_bytes)
body = (root / 'index.json').read_bytes()
index = json.loads(body)
if not manifest.get('runtimeComplete') or manifest.get('revision') != index.get('revision'):
    raise SystemExit('Catalog snapshot is not complete.')
if manifest.get('indexedItems') != len(index.get('items', [])) or manifest.get('indexBytes') != len(body):
    raise SystemExit('Catalog completion metadata does not match its index.')
if not (root / 'index.json.gz').is_file() or not (root / 'guides').is_dir() or not (root / 'media').is_dir():
    raise SystemExit('Completed catalog runtime files are missing.')
print(hashlib.sha256(body + b'\0' + manifest_bytes).hexdigest())
PY
)
catalog_ssh=(-i "$catalog_key" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5 \
  -o "UserKnownHostsFile=$catalog_hosts" -o StrictHostKeyChecking=yes -o LogLevel=ERROR)
catalog_target=ec2-user@$catalog_ip
catalog_snapshot=/opt/please-mend-my/catalogs/$catalog_digest

# The path contains only a fixed prefix and the locally computed SHA-256.
# shellcheck disable=SC2029
if ssh "${catalog_ssh[@]}" "$catalog_target" "test -f '$catalog_snapshot/manifest.json'"; then
  printf '%s\n' "$catalog_snapshot"
  exit 0
fi

ssh "${catalog_ssh[@]}" "$catalog_target" 'sudo install -d -o ec2-user -g ec2-user -m 0755 /opt/please-mend-my/catalog-staging'
printf -v catalog_transport '%q ' ssh "${catalog_ssh[@]}"
# The receiver stays unpublished. rsync verifies transferred bytes and renames
# temporary files; --delete affects only this project staging directory.
rsync -a -z --compress-choice=zstd --compress-level=3 --chmod=D755,F644 \
  --partial --partial-dir=.rsync-partial --delete-delay \
  --exclude='*.tmp' --exclude='*.part' --info=stats2 \
  -e "$catalog_transport" "$catalog_source/" "$catalog_target:/opt/please-mend-my/catalog-staging/" >&2

ssh "${catalog_ssh[@]}" "$catalog_target" bash -s -- "$catalog_digest" <<'REMOTE'
set -euo pipefail
catalog_digest=$1
[[ "$catalog_digest" =~ ^[0-9a-f]{64}$ ]]
python3 - "$catalog_digest" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
root = Path('/opt/please-mend-my/catalog-staging')
body = (root / 'index.json').read_bytes()
manifest_bytes = (root / 'manifest.json').read_bytes()
manifest = json.loads(manifest_bytes)
if hashlib.sha256(body + b'\0' + manifest_bytes).hexdigest() != sys.argv[1] or not manifest.get('runtimeComplete'):
    raise SystemExit('Uploaded catalog release is incomplete or has a different index.')
guide_files = sum(entry.is_file() for entry in os.scandir(root / 'guides'))
media_files = sum(entry.is_file() for entry in os.scandir(root / 'media'))
if guide_files != 2 * manifest['indexedItems'] or media_files != manifest['uniqueMediaFiles']:
    raise SystemExit('Uploaded runtime file counts differ from its completed manifest.')
print(f"Catalog release ready: {manifest['indexedItems']:,} items, {media_files:,} media files.", file=sys.stderr)
PY
sudo install -d -o root -g root -m 0755 /opt/please-mend-my/catalogs
catalog_snapshot=/opt/please-mend-my/catalogs/$catalog_digest
sudo mv /opt/please-mend-my/catalog-staging "$catalog_snapshot"
sudo chown -R root:root "$catalog_snapshot"
printf '%s\n' "$catalog_snapshot"
REMOTE
