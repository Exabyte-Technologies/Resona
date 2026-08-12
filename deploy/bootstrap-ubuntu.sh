#!/usr/bin/env bash
set -Eeuo pipefail

deploy_user="${1:?deploy user is required}"
app_dir="${2:?application directory is required}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "bootstrap-ubuntu.sh must run with sudo" >&2
  exit 1
fi

if ! id "$deploy_user" >/dev/null 2>&1; then
  echo "Deployment user $deploy_user does not exist" >&2
  exit 1
fi

required_packages=(python3 python3-venv python3-pip nginx certbot rsync curl ca-certificates)
missing_packages=()
for package in "${required_packages[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
    missing_packages+=("$package")
  fi
done

if (( ${#missing_packages[@]} )); then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing_packages[@]}"
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ is required; found {sys.version.split()[0]}")
PY

install -d -m 0755 -o "$deploy_user" -g "$deploy_user" "$app_dir"
install -d -m 0750 -o "$deploy_user" -g www-data "$app_dir/instance" "$app_dir/instance/storage"
install -d -m 0755 -o root -g root /var/www/certbot

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow 'Nginx Full'
fi
