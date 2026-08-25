#!/usr/bin/env bash
set -Eeuo pipefail

deploy_user="${1:?deploy user is required}"
app_dir="${2:?application directory is required}"
uploaded_env="${3:?uploaded environment file is required}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "finalize-ubuntu.sh must run with sudo" >&2
  exit 1
fi

required_env=(SECRET_KEY DATABASE_PATH RESONA_STORAGE_ROOT RESONA_USER_QUOTA_BYTES ADMIN_USERNAME ADMIN_PASSWORD CLOSEAI_BASE_URL CLOSEAI_API_KEY CLOSEAI_MODEL CLOSEAI_PREFER_ENV RESEND_API_KEY RESEND_FROM_EMAIL RESEND_FROM_NAME CAPTCHA_CHALLENGE_COUNT CAPTCHA_CHALLENGE_DIFFICULTY LETSENCRYPT_EMAIL PUBLIC_BASE_URL EXABYTE_OIDC_ISSUER EXABYTE_OIDC_CALLBACK_URL EXABYTE_OIDC_SCOPES REDIS_URL SESSION_COOKIE_SECURE)
for name in "${required_env[@]}"; do
  if ! grep -q "^${name}=" "$uploaded_env"; then
    echo "The uploaded environment is missing $name" >&2
    exit 1
  fi
done

letsencrypt_email="$(grep '^LETSENCRYPT_EMAIL=' "$uploaded_env" | cut -d= -f2-)"
letsencrypt_email="${letsencrypt_email#\"}"
letsencrypt_email="${letsencrypt_email%\"}"
if [[ ! "$letsencrypt_email" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "The uploaded environment contains an invalid LETSENCRYPT_EMAIL" >&2
  exit 1
fi

install -m 0600 -o "$deploy_user" -g "$deploy_user" "$uploaded_env" "$app_dir/.env"
rm -f "$uploaded_env"
install -d -m 0750 -o "$deploy_user" -g www-data "$app_dir/instance" "$app_dir/instance/storage" "$app_dir/instance/exabyte_avatars"

if systemctl is-active --quiet resona; then
  systemctl stop resona
fi
database="$app_dir/instance/resona.sqlite3"
if [[ -f "$database" ]]; then
  backup_dir="$app_dir/instance/backups"
  install -d -m 0750 -o "$deploy_user" -g "$deploy_user" "$backup_dir"
  backup="$backup_dir/resona-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
  cp --preserve=mode,timestamps "$database" "$backup"
  chown "$deploy_user:$deploy_user" "$backup"
  mapfile -t old_backups < <(find "$backup_dir" -maxdepth 1 -type f -name 'resona-*.sqlite3' -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2- | tail -n +6)
  if (( ${#old_backups[@]} )); then
    rm -f -- "${old_backups[@]}"
  fi
fi

if [[ ! -x "$app_dir/.venv/bin/python" ]]; then
  sudo -u "$deploy_user" python3 -m venv "$app_dir/.venv"
fi
sudo -u "$deploy_user" "$app_dir/.venv/bin/python" -m pip install --upgrade pip
sudo -u "$deploy_user" "$app_dir/.venv/bin/python" -m pip install --requirement "$app_dir/requirements.txt"
sudo -u "$deploy_user" "$app_dir/.venv/bin/python" -m pip check

install -m 0644 "$app_dir/deploy/resona.service" /etc/systemd/system/resona.service
install -m 0644 "$app_dir/deploy/resona-maintenance.service" /etc/systemd/system/resona-maintenance.service
install -m 0644 "$app_dir/deploy/resona-maintenance.timer" /etc/systemd/system/resona-maintenance.timer
install -m 0644 "$app_dir/deploy/resona-http.nginx" /etc/nginx/sites-available/resona
ln -sfn /etc/nginx/sites-available/resona /etc/nginx/sites-enabled/resona
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl enable nginx
systemctl restart nginx

certbot certonly \
  --webroot \
  --webroot-path /var/www/certbot \
  --domain resona.neuorise.com \
  --email "$letsencrypt_email" \
  --agree-tos \
  --non-interactive \
  --keep-until-expiring

install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
install -m 0755 "$app_dir/deploy/certbot-reload-nginx.sh" /etc/letsencrypt/renewal-hooks/deploy/resona-reload-nginx
install -m 0644 "$app_dir/deploy/resona.nginx" /etc/nginx/sites-available/resona
nginx -t
systemctl daemon-reload
systemctl enable nginx redis-server resona resona-maintenance.timer certbot.timer
systemctl restart redis-server
systemctl start certbot.timer
systemctl restart resona
systemctl restart resona-maintenance.timer
systemctl restart nginx

for attempt in {1..30}; do
  if curl --fail --silent --show-error --max-time 5 --resolve resona.neuorise.com:443:127.0.0.1 https://resona.neuorise.com/ >/dev/null; then
    systemctl --no-pager --full status resona | sed -n '1,12p'
    curl --silent --output /dev/null --write-out 'HTTPS %{http_code} · TLS %{ssl_verify_result}\n' --resolve resona.neuorise.com:443:127.0.0.1 https://resona.neuorise.com/
    echo "Resona HTTPS deployment is healthy"
    exit 0
  fi
  sleep 2
done

journalctl -u resona --no-pager -n 100 >&2
exit 1
