#!/usr/bin/env bash
# JMESPath expressions deliberately contain literal backticks.
# shellcheck disable=SC2016
set -euo pipefail
umask 077

deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$deploy_dir/.." && pwd)
deploy_region=${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}
deploy_stack=${PMM_STACK:-please-mend-my}
deploy_key=${PMM_SSH_KEY:-$HOME/.ssh/please-mend-my_ec2_ed25519}
deploy_tls_dir=${PMM_TLS_DIR:-$HOME/.config/please-mend-my/tls}
deploy_tls=false
deploy_hosts=$HOME/.ssh/please-mend-my_known_hosts
deploy_tmp=$(mktemp -d)
trap 'rm -rf -- "$deploy_tmp"' EXIT
aws_cmd=(aws --region "$deploy_region" --no-cli-pager)

for tool in aws go curl ssh scp ssh-keygen python3; do
  command -v "$tool" >/dev/null || { printf 'Missing command: %s\n' "$tool" >&2; exit 1; }
done
if [[ -f "$deploy_tls_dir/origin.crt" && -f "$deploy_tls_dir/origin.key" ]]; then
  deploy_tls=true
elif [[ -e "$deploy_tls_dir/origin.crt" || -e "$deploy_tls_dir/origin.key" ]]; then
  printf 'TLS requires both origin.crt and origin.key in %s\n' "$deploy_tls_dir" >&2
  exit 1
fi
[[ "$deploy_stack" =~ ^[A-Za-z][A-Za-z0-9-]*$ ]] || { printf 'Invalid PMM_STACK\n' >&2; exit 1; }
account_id=$("${aws_cmd[@]}" sts get-caller-identity --query Account --output text)
deploy_key_name=${PMM_KEY_NAME:-$deploy_stack-$account_id}
previous_instance=''

# Fail before changing resources if this account already has an unrelated stack.
if stack_project=$("${aws_cmd[@]}" cloudformation describe-stacks --stack-name "$deploy_stack" \
  --query 'Stacks[0].Tags[?Key==`Project`].Value | [0]' --output text 2>"$deploy_tmp/stack.err"); then
  [[ "$stack_project" == please-mend-my ]] || { printf 'Refusing to modify an unrelated stack.\n' >&2; exit 1; }
  previous_instance=$("${aws_cmd[@]}" cloudformation describe-stacks --stack-name "$deploy_stack" \
    --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue | [0]' --output text)
elif ! grep -q 'does not exist' "$deploy_tmp/stack.err"; then
  cat "$deploy_tmp/stack.err" >&2
  exit 1
fi

# Build the web server locally; the VM builds its catalog directly from public sources.
cd -- "$repo_dir"
deploy_commit=$(git rev-parse "${PMM_DEPLOY_COMMIT:-HEAD}")
mkdir "$deploy_tmp/source"
git archive "$deploy_commit" | tar -x -C "$deploy_tmp/source"
(
  cd -- "$deploy_tmp/source"
  CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags='-s -w' -o "$deploy_tmp/server" .
)
availability_zone=$("${aws_cmd[@]}" ec2 describe-instance-type-offerings --location-type availability-zone \
  --filters Name=instance-type,Values=c8g.xlarge --query 'sort(InstanceTypeOfferings[].Location)[0]' --output text)
[[ "$availability_zone" != None && -n "$availability_zone" ]] || { printf 'c8g.xlarge is not offered in this region.\n' >&2; exit 1; }

mkdir -p -- "$(dirname -- "$deploy_key")" "$HOME/.ssh"
if [[ ! -f "$deploy_key" ]]; then
  [[ ! -e "$deploy_key.pub" ]] || { printf 'Public key exists without its private key.\n' >&2; exit 1; }
  ssh-keygen -q -t ed25519 -N '' -C please-mend-my-deploy -f "$deploy_key"
fi
if [[ ! -f "$deploy_key.pub" ]]; then
  ssh-keygen -y -f "$deploy_key" > "$deploy_key.pub"
fi

remote_public=$("${aws_cmd[@]}" ec2 describe-key-pairs --filters "Name=key-name,Values=$deploy_key_name" \
  --include-public-key --query 'KeyPairs[0].PublicKey' --output text)
if [[ "$remote_public" == None ]]; then
  "${aws_cmd[@]}" ec2 import-key-pair --key-name "$deploy_key_name" \
    --public-key-material "fileb://$deploy_key.pub" \
    --tag-specifications 'ResourceType=key-pair,Tags=[{Key=Project,Value=please-mend-my}]' >/dev/null
else
  key_project=$("${aws_cmd[@]}" ec2 describe-key-pairs --filters "Name=key-name,Values=$deploy_key_name" \
    --query 'KeyPairs[0].Tags[?Key==`Project`].Value | [0]' --output text)
  read -r local_algorithm local_material _ < "$deploy_key.pub"
  read -r remote_algorithm remote_material _ <<< "$remote_public"
  if [[ "$key_project" != please-mend-my || "$local_algorithm $local_material" != "$remote_algorithm $remote_material" ]]; then
    printf 'Existing EC2 key is unrelated or differs from the local key; refusing to overwrite it.\n' >&2
    exit 1
  fi
fi

admin_ip=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 10 https://checkip.amazonaws.com)
[[ "$admin_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { printf 'Cannot determine administrator IPv4 address.\n' >&2; exit 1; }
"${aws_cmd[@]}" cloudformation deploy --stack-name "$deploy_stack" --template-file "$deploy_dir/ec2.yaml" \
  --parameter-overrides "KeyName=$deploy_key_name" "AdminCidr=$admin_ip/32" "AvailabilityZone=$availability_zone" \
  --tags Project=please-mend-my --no-fail-on-empty-changeset
server_ip=$("${aws_cmd[@]}" cloudformation describe-stacks --stack-name "$deploy_stack" \
  --query 'Stacks[0].Outputs[?OutputKey==`PublicIp`].OutputValue | [0]' --output text)
[[ "$server_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { printf 'Stack did not return a public IP.\n' >&2; exit 1; }
server_instance=$("${aws_cmd[@]}" cloudformation describe-stacks --stack-name "$deploy_stack" \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue | [0]' --output text)

touch "$deploy_hosts"
# Authenticate replacement keys through AWS before updating the same Elastic IP.
verify_stack_host_key() {
  local key_attempt
  for ((key_attempt = 0; key_attempt < 40; key_attempt++)); do
    "${aws_cmd[@]}" ec2 get-console-output --instance-id "$server_instance" --latest \
      --query Output --output text > "$deploy_tmp/console-output"
    if python3 "$deploy_dir/verify_host_key.py" "$deploy_tmp/console-output" "$server_ip" > "$deploy_tmp/host-key"; then
      cp -p "$deploy_hosts" "$deploy_hosts.previous"
      ssh-keygen -R "$server_ip" -f "$deploy_hosts" >/dev/null 2>&1
      cat "$deploy_tmp/host-key" >> "$deploy_hosts"
      return 0
    fi
    sleep 3
  done
  printf 'Replacement host key is not yet available from AWS console output.\n' >&2
  return 1
}
if [[ -n "$previous_instance" && "$previous_instance" != "$server_instance" ]]; then
  verify_stack_host_key
fi
ssh_policy=accept-new
if ssh-keygen -F "$server_ip" -f "$deploy_hosts" >/dev/null; then ssh_policy=yes; fi
ssh_options=(-i "$deploy_key" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5 \
  -o ConnectionAttempts=1 -o "UserKnownHostsFile=$deploy_hosts" -o "StrictHostKeyChecking=$ssh_policy")
ssh_ready=false
for ((attempt = 0; attempt < 30; attempt++)); do
  if ssh "${ssh_options[@]}" "ec2-user@$server_ip" true 2>"$deploy_tmp/ssh.err"; then ssh_ready=true; break; fi
  if grep -q 'REMOTE HOST IDENTIFICATION HAS CHANGED' "$deploy_tmp/ssh.err"; then
    # Also recover if a previous deployment stopped after instance replacement.
    verify_stack_host_key || break
  fi
  sleep 2
done
if [[ "$ssh_ready" != true ]]; then cat "$deploy_tmp/ssh.err" >&2; exit 1; fi
ssh "${ssh_options[@]}" "ec2-user@$server_ip" 'sudo timeout 180 cloud-init status --wait && test -f /etc/systemd/system/please-mend-my.service'
catalog_snapshot=''
catalog_snapshot=$(PMM_ORIGIN_IP="$server_ip" PMM_DEPLOY_COMMIT="$deploy_commit" bash "$deploy_tmp/source/deploy/catalog.sh")
remote_upload=$(ssh "${ssh_options[@]}" "ec2-user@$server_ip" 'mktemp /home/ec2-user/please-mend-my.XXXXXX')
scp "${ssh_options[@]}" "$deploy_tmp/server" "ec2-user@$server_ip:$remote_upload"
if [[ "$deploy_tls" == true ]]; then
  remote_tls=$(ssh "${ssh_options[@]}" "ec2-user@$server_ip" 'mktemp -d /home/ec2-user/please-mend-my-tls.XXXXXX')
  scp "${ssh_options[@]}" "$deploy_tls_dir/origin.crt" "$deploy_tls_dir/origin.key" "ec2-user@$server_ip:$remote_tls/"
  ssh "${ssh_options[@]}" "ec2-user@$server_ip" bash -s -- "$remote_tls" <<'TLS'
set -euo pipefail
chmod 600 "$1/origin.crt" "$1/origin.key"
sudo install -d -o root -g root -m 0700 /etc/please-mend-my/tls
sudo install -o root -g root -m 0600 "$1/origin.crt" /etc/please-mend-my/tls/origin.crt
sudo install -o root -g root -m 0600 "$1/origin.key" /etc/please-mend-my/tls/origin.key
sudo install -d -o root -g root -m 0755 /etc/systemd/system/please-mend-my.service.d
sudo tee /etc/systemd/system/please-mend-my.service.d/tls.conf >/dev/null <<'UNIT'
[Service]
LoadCredential=tls.crt:/etc/please-mend-my/tls/origin.crt
LoadCredential=tls.key:/etc/please-mend-my/tls/origin.key
Environment=TLS_CERT=%d/tls.crt
Environment=TLS_KEY=%d/tls.key
UNIT
rm -rf -- "$1"
TLS
fi
ssh "${ssh_options[@]}" "ec2-user@$server_ip" sudo bash -s -- "$remote_upload" "$catalog_snapshot" "$deploy_commit" <<'REMOTE'
set -euo pipefail
catalog_snapshot=$2
catalog_link=/opt/please-mend-my/catalog
catalog_config=/etc/systemd/system/please-mend-my.service.d/catalog.conf
previous_catalog=$(readlink "$catalog_link" || true)
had_server=false
had_config=false
if [[ -f /opt/please-mend-my/server ]]; then
  cp -p /opt/please-mend-my/server /opt/please-mend-my/server.previous
  had_server=true
fi
if [[ -f "$catalog_config" ]]; then
  cp -p "$catalog_config" "$catalog_config.previous"
  had_config=true
fi
rollback() {
  trap - ERR
  printf 'Release failed; restoring the previous service.\n' >&2
  if [[ "$had_server" == true ]]; then
    cp -p /opt/please-mend-my/server.previous /opt/please-mend-my/server.rollback
    mv -f /opt/please-mend-my/server.rollback /opt/please-mend-my/server
  fi
  if [[ -n "$previous_catalog" ]]; then
    ln -sfn "$previous_catalog" /opt/please-mend-my/catalog.next
    mv -Tf /opt/please-mend-my/catalog.next "$catalog_link"
  elif [[ -n "$catalog_snapshot" ]]; then
    rm -f "$catalog_link"
  fi
  if [[ "$had_config" == true ]]; then
    cp -p "$catalog_config.previous" "$catalog_config"
  elif [[ -n "$catalog_snapshot" ]]; then
    rm -f "$catalog_config"
  fi
  systemctl daemon-reload
  systemctl restart please-mend-my.service || true
  exit 1
}
trap rollback ERR
install -o root -g root -m 0755 "$1" /opt/please-mend-my/server.next
mv -f /opt/please-mend-my/server.next /opt/please-mend-my/server
rm -f -- "$1"
if [[ -n "$catalog_snapshot" ]]; then
  [[ "$catalog_snapshot" =~ ^/opt/please-mend-my/catalogs/[0-9a-f]{64}$ && -f "$catalog_snapshot/manifest.json" ]]
  ln -sfn "$catalog_snapshot" /opt/please-mend-my/catalog.next
  mv -Tf /opt/please-mend-my/catalog.next "$catalog_link"
  install -d -o root -g root -m 0755 /etc/systemd/system/please-mend-my.service.d
  cat > "$catalog_config" <<'UNIT'
[Service]
Environment=CATALOG_DIR=/opt/please-mend-my/catalog
UNIT
fi
systemctl daemon-reload
systemctl enable please-mend-my.service
systemctl restart please-mend-my.service
for attempt in {1..20}; do
  if curl --fail --silent --max-time 2 http://127.0.0.1/healthz >/dev/null; then break; fi
  sleep 0.25
done
systemctl is-active --quiet please-mend-my.service
curl --fail --silent --max-time 2 http://127.0.0.1/healthz >/dev/null
if [[ -n "$catalog_snapshot" ]]; then curl --fail --silent --head --max-time 2 http://127.0.0.1/api/catalog >/dev/null; fi
printf '%s\n' "$3" > /opt/please-mend-my/deployed-commit
trap - ERR
REMOTE
curl --fail --silent --show-error --retry 10 --retry-delay 1 --retry-connrefused \
  --connect-timeout 5 --max-time 10 "http://$server_ip/healthz"
printf '\nDeployed: http://%s\n' "$server_ip"
