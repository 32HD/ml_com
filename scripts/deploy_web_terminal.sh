#!/usr/bin/env bash
set -euo pipefail

# Deploy ttyd behind Nginx with HTTPS.
# Required env vars for full HTTPS deployment:
#   DOMAIN, EMAIL, TTYD_AUTH
# Optional:
#   CODER_USER, PROJECT_DIR, ENABLE_WETTY, WETTY_PORT_LOCAL

log() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1; }

trap 'warn "Failed at line $LINENO. Rollback hint: sudo systemctl stop ttyd-codex; sudo rm -f /etc/nginx/conf.d/terminal.conf; sudo systemctl reload nginx"' ERR

if ! need_cmd sudo; then
  die "sudo is required."
fi
sudo -v || die "sudo validation failed."

DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
CODER_USER="${CODER_USER:-$USER}"
PROJECT_DIR="${PROJECT_DIR:-$HOME}"
TTYD_AUTH="${TTYD_AUTH:-}"

ENABLE_WETTY="${ENABLE_WETTY:-0}"           # 1 to enable WeTTY
WETTY_PORT_LOCAL="${WETTY_PORT_LOCAL:-3000}"

TTYD_SOCKET="${TTYD_SOCKET:-/run/ttyd/ttyd.sock}"
TTYD_BASE_PATH="${TTYD_BASE_PATH:-/ttyd}"
WETTY_BASE_PATH="${WETTY_BASE_PATH:-/wetty}"
NGINX_SITE="${NGINX_SITE:-/etc/nginx/conf.d/terminal.conf}"

log "DOMAIN=${DOMAIN:-<empty>} EMAIL=${EMAIL:-<empty>} CODER_USER=$CODER_USER PROJECT_DIR=$PROJECT_DIR ENABLE_WETTY=$ENABLE_WETTY"

if [ -z "$TTYD_AUTH" ]; then
  die "TTYD_AUTH is required (format: user:StrongPass)."
fi
if ! printf '%s' "$TTYD_AUTH" | rg -q '^[^:]+:.+$'; then
  die "TTYD_AUTH format must be user:password."
fi
if [ "$TTYD_AUTH" = "codex:ChangeMeStrongPass" ]; then
  die "Please change TTYD_AUTH from the default placeholder."
fi

if ! id "$CODER_USER" >/dev/null 2>&1; then
  die "CODER_USER '$CODER_USER' does not exist."
fi

if [ -n "$DOMAIN" ] && [ -z "$EMAIL" ]; then
  die "EMAIL must be set when DOMAIN is set."
fi
if [ -z "$DOMAIN" ] && [ -n "$EMAIL" ]; then
  warn "EMAIL is set but DOMAIN is empty; TLS will be skipped."
fi

pkg_install() {
  if need_cmd apt-get; then
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
  elif need_cmd dnf; then
    sudo dnf install -y "$@"
  elif need_cmd yum; then
    sudo yum install -y "$@"
  else
    die "No known package manager found (apt/dnf/yum)."
  fi
}

install_base_deps() {
  log "Install base deps: nginx, tmux, certbot (nginx plugin), curl"
  if need_cmd apt-get; then
    pkg_install nginx tmux certbot python3-certbot-nginx curl
  else
    pkg_install nginx tmux certbot python3-certbot-nginx curl || pkg_install nginx tmux certbot curl
  fi
  sudo systemctl enable --now nginx
}

install_ttyd() {
  if need_cmd ttyd; then
    log "ttyd already installed: $(ttyd -v || true)"
    return
  fi

  log "Installing ttyd"
  if need_cmd apt-cache && [ "$(apt-cache policy ttyd 2>/dev/null | awk '/Candidate:/ {print $2}')" != "(none)" ]; then
    pkg_install ttyd
  else
    if need_cmd apt-get; then
      pkg_install build-essential cmake git libjson-c-dev libwebsockets-dev libssl-dev
    else
      pkg_install gcc gcc-c++ make cmake git json-c-devel libwebsockets-devel openssl-devel
    fi
    local tmpdir
    tmpdir="$(mktemp -d)"
    git clone --depth 1 https://github.com/tsl0922/ttyd.git "$tmpdir/ttyd"
    mkdir -p "$tmpdir/ttyd/build"
    (
      cd "$tmpdir/ttyd/build"
      cmake ..
      make -j"$(nproc)"
      sudo make install
    )
    rm -rf "$tmpdir"
  fi

  need_cmd ttyd || die "ttyd installation failed."
  log "ttyd installed: $(ttyd -v || true)"
}

write_ttyd_env() {
  log "Write /etc/ttyd/ttyd.env"
  sudo mkdir -p /etc/ttyd
  sudo bash -c "cat > /etc/ttyd/ttyd.env" <<EOF
CODER_USER="$CODER_USER"
PROJECT_DIR="$PROJECT_DIR"
TTYD_AUTH="$TTYD_AUTH"
EOF
  sudo chmod 600 /etc/ttyd/ttyd.env
}

write_ttyd_service() {
  local ttyd_bin
  ttyd_bin="$(command -v ttyd)"
  log "Write systemd unit /etc/systemd/system/ttyd-codex.service (ttyd: $ttyd_bin)"
  sudo bash -c "cat > /etc/systemd/system/ttyd-codex.service" <<EOF
[Unit]
Description=ttyd for Codex Web Terminal
After=network.target

[Service]
Type=simple
EnvironmentFile=/etc/ttyd/ttyd.env
User=${CODER_USER}
WorkingDirectory=%h
RuntimeDirectory=ttyd
RuntimeDirectoryMode=0755
UMask=0002
ExecStartPre=/usr/bin/rm -f ${TTYD_SOCKET}
ExecStart=${ttyd_bin} \\
  -i ${TTYD_SOCKET} \\
  -c \${TTYD_AUTH} \\
  -O \\
  -b ${TTYD_BASE_PATH} \\
  bash -lc "cd '\${PROJECT_DIR}' && exec tmux new -A -s codex"
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable --now ttyd-codex
  sudo systemctl status ttyd-codex --no-pager -l
}

deploy_wetty() {
  [ "$ENABLE_WETTY" = "1" ] || return 0
  log "Deploy WeTTY via Docker on 127.0.0.1:${WETTY_PORT_LOCAL}"

  if ! need_cmd docker; then
    die "ENABLE_WETTY=1 but docker is not installed."
  fi

  sudo docker rm -f wetty >/dev/null 2>&1 || true
  sudo docker run -d --name wetty --restart unless-stopped \
    -p "127.0.0.1:${WETTY_PORT_LOCAL}:3000" \
    wettyoss/wetty \
    --ssh-host=127.0.0.1 --ssh-port=22 --base="${WETTY_BASE_PATH}"
}

write_nginx_site() {
  local server_name
  if [ -n "$DOMAIN" ]; then
    server_name="$DOMAIN"
  else
    warn "DOMAIN is empty. Configuring HTTP only with server_name _."
    server_name="_"
  fi

  log "Write Nginx config $NGINX_SITE"
  sudo bash -c "cat > $NGINX_SITE" <<EOF
map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name ${server_name};

    location = /healthz {
        add_header Content-Type text/plain;
        return 200 "ok\n";
    }

    location ${TTYD_BASE_PATH}/ {
        proxy_pass http://unix:${TTYD_SOCKET}:/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }
EOF

  if [ "$ENABLE_WETTY" = "1" ]; then
    sudo bash -c "cat >> $NGINX_SITE" <<EOF
    location ${WETTY_BASE_PATH}/ {
        proxy_pass http://127.0.0.1:${WETTY_PORT_LOCAL}${WETTY_BASE_PATH}/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }
EOF
  fi

  sudo bash -c "cat >> $NGINX_SITE" <<EOF
    location = / {
        return 302 ${TTYD_BASE_PATH}/;
    }
}
EOF

  sudo nginx -t
  sudo systemctl reload nginx
}

open_firewall_if_needed() {
  if need_cmd ufw && sudo ufw status 2>/dev/null | rg -q '^Status: active'; then
    log "UFW active: allowing 80 and 443"
    sudo ufw allow 80/tcp
    sudo ufw allow 443/tcp
  fi
}

issue_tls() {
  if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    warn "Skip TLS. Set DOMAIN and EMAIL for Let's Encrypt."
    return 0
  fi

  log "Issue TLS cert with certbot --nginx for ${DOMAIN}"
  sudo certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect
  sudo systemctl reload nginx
}

smoke_tests() {
  log "Smoke test: nginx healthz"
  curl -fsS "http://127.0.0.1/healthz" >/dev/null

  log "Smoke test: ttyd socket"
  sudo test -S "$TTYD_SOCKET"
  sudo ls -l "$TTYD_SOCKET"

  log "Smoke test: services"
  sudo systemctl is-active nginx ttyd-codex

  log "Smoke test: tmux session for ${CODER_USER}"
  sudo -u "$CODER_USER" tmux has-session -t codex

  if [ -n "$DOMAIN" ]; then
    log "Smoke test: HTTPS endpoint"
    curl -fsSI "https://${DOMAIN}/healthz" >/dev/null || warn "HTTPS check failed; verify DNS/ports and certbot output."
  fi
}

install_base_deps
install_ttyd
write_ttyd_env
write_ttyd_service
deploy_wetty
write_nginx_site
open_firewall_if_needed
issue_tls
smoke_tests

log "Done."
if [ -n "$DOMAIN" ]; then
  log "Open: https://${DOMAIN}${TTYD_BASE_PATH}/"
else
  log "Open: http://<SERVER_IP>${TTYD_BASE_PATH}/"
fi
