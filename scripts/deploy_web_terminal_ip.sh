#!/usr/bin/env bash
set -euo pipefail

# IP-only deployment without sudo:
# - ttyd runs as current user on UNIX socket
# - nginx runs in Docker and proxies /ttyd/
# - tmux keeps session persistent across reconnects

log() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1; }

if ! need_cmd docker; then
  die "docker is required."
fi
if ! need_cmd tmux; then
  die "tmux is required."
fi
if ! need_cmd apt || ! need_cmd dpkg-deb; then
  die "apt and dpkg-deb are required on Ubuntu."
fi

PROJECT_DIR="${PROJECT_DIR:-$HOME}"
TTYD_AUTH="${TTYD_AUTH:-}"
TTYD_USER="${TTYD_USER:-codex}"
TTYD_PASS="${TTYD_PASS:-}"
LISTEN_PORT="${LISTEN_PORT:-80}"
CODE_SERVER_VERSION="${CODE_SERVER_VERSION:-4.109.5}"

if [ -z "$TTYD_AUTH" ]; then
  if [ -z "$TTYD_PASS" ]; then
    if need_cmd openssl; then
      TTYD_PASS="$(openssl rand -hex 12)"
    else
      die "TTYD_PASS is empty and openssl is unavailable. Set TTYD_AUTH or TTYD_PASS."
    fi
  fi
  TTYD_AUTH="${TTYD_USER}:${TTYD_PASS}"
fi

if ! printf '%s' "$TTYD_AUTH" | rg -q '^[^:]+:.+$'; then
  die "TTYD_AUTH must be user:password."
fi

BASE_DIR="$HOME/.local/share/web-terminal"
RUN_DIR="$HOME/.local/run/ttyd"
TTYD_SOCKET="$RUN_DIR/ttyd.sock"
BIN_DIR="$HOME/.local/bin"
TTYD_LOCAL_DIR="$BASE_DIR/ttyd"
CODE_SERVER_DIR="$BASE_DIR/code-server"
CODE_SERVER_RUN_DIR="$HOME/.local/run/code-server"
CODE_SERVER_SOCKET="$CODE_SERVER_RUN_DIR/code-server.sock"
NGINX_CONF_DIR="$BASE_DIR/nginx"
NGINX_AUTH_FILE="$NGINX_CONF_DIR/htpasswd_ttyd"
ENV_FILE="$BASE_DIR/ttyd.env"
SERVICE_FILE="$HOME/.config/systemd/user/ttyd-codex.service"
CODE_SERVER_ENV_FILE="$BASE_DIR/code-server.env"
CODE_SERVER_SERVICE_FILE="$HOME/.config/systemd/user/code-server-codex.service"
ALIASES_FILE="$HOME/.bash_aliases"
TMUX_CONF_FILE="$HOME/.tmux.conf"
MOBILE_BIN="$BIN_DIR/codex-mobile"

TTYD_USER_PART="${TTYD_AUTH%%:*}"
TTYD_PASS_PART="${TTYD_AUTH#*:}"

mkdir -p "$BASE_DIR" "$RUN_DIR" "$BIN_DIR" "$TTYD_LOCAL_DIR" "$CODE_SERVER_DIR" "$CODE_SERVER_RUN_DIR" "$NGINX_CONF_DIR" "$HOME/.config/systemd/user"
chmod 755 "$RUN_DIR"
chmod 755 "$CODE_SERVER_RUN_DIR"

install_local_ttyd() {
  if [ -x "$BIN_DIR/ttyd" ]; then
    log "ttyd already exists at $BIN_DIR/ttyd"
    return
  fi

  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN
  (
    cd "$tmpdir"
    apt download ttyd >/dev/null
    apt download libev4 >/dev/null
    dpkg-deb -x ttyd_*.deb "$TTYD_LOCAL_DIR"
    dpkg-deb -x libev4_*.deb "$TTYD_LOCAL_DIR"
  )

  ln -sf "$TTYD_LOCAL_DIR/usr/bin/ttyd" "$BIN_DIR/ttyd"
  rm -rf "$tmpdir"
}

install_code_server() {
  if [ -x "$BIN_DIR/code-server" ]; then
    log "code-server already exists at $BIN_DIR/code-server"
    return
  fi

  local tmpdir url tarball
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN
  tarball="code-server-${CODE_SERVER_VERSION}-linux-amd64.tar.gz"
  url="https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/${tarball}"

  curl -fL "$url" -o "$tmpdir/$tarball"
  tar -xzf "$tmpdir/$tarball" -C "$tmpdir"

  rm -rf "$CODE_SERVER_DIR/current"
  mkdir -p "$CODE_SERVER_DIR/current"
  cp -a "$tmpdir/code-server-${CODE_SERVER_VERSION}-linux-amd64/"* "$CODE_SERVER_DIR/current/"

  ln -sf "$CODE_SERVER_DIR/current/bin/code-server" "$BIN_DIR/code-server"
}

write_env_file() {
  cat > "$ENV_FILE" <<EOF
PROJECT_DIR="$PROJECT_DIR"
TTYD_AUTH="$TTYD_AUTH"
LD_LIBRARY_PATH="$TTYD_LOCAL_DIR/usr/lib/x86_64-linux-gnu:$TTYD_LOCAL_DIR/lib/x86_64-linux-gnu"
EOF
  chmod 600 "$ENV_FILE"
}

write_user_service() {
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=ttyd for Codex (user service)
After=network.target

[Service]
Type=simple
EnvironmentFile=%h/.local/share/web-terminal/ttyd.env
ExecStartPre=/usr/bin/rm -f %h/.local/run/ttyd/ttyd.sock
ExecStart=%h/.local/bin/ttyd \\
  -i %h/.local/run/ttyd/ttyd.sock \\
  -O \\
  -b /ttyd \\
  -P 5 \\
  -t "fontSize=18" \\
  -t "lineHeight=1.2" \\
  -t "cursorBlink=true" \\
  -t "scrollback=5000" \\
  bash -lc "cd '\${PROJECT_DIR}' && exec tmux new -A -s codex"
ExecStartPost=/bin/bash -lc 'for i in {1..30}; do [ -S %h/.local/run/ttyd/ttyd.sock ] && chmod 666 %h/.local/run/ttyd/ttyd.sock && exit 0; sleep 0.1; done; exit 1'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable --now ttyd-codex.service
}

write_code_server_env_file() {
  cat > "$CODE_SERVER_ENV_FILE" <<EOF
PROJECT_DIR="$PROJECT_DIR"
EOF
  chmod 600 "$CODE_SERVER_ENV_FILE"
}

write_code_server_service() {
  cat > "$CODE_SERVER_SERVICE_FILE" <<EOF
[Unit]
Description=code-server for mobile web IDE
After=network.target

[Service]
Type=simple
EnvironmentFile=%h/.local/share/web-terminal/code-server.env
ExecStartPre=/usr/bin/mkdir -p %h/.local/run/code-server
ExecStartPre=/usr/bin/chmod 755 %h/.local/run/code-server
ExecStartPre=/usr/bin/rm -f %h/.local/run/code-server/code-server.sock
ExecStart=%h/.local/bin/code-server \\
  --socket %h/.local/run/code-server/code-server.sock \\
  --socket-mode 666 \\
  --auth none \\
  --disable-telemetry \\
  --disable-update-check \\
  --abs-proxy-base-path /code \\
  \${PROJECT_DIR}
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable --now code-server-codex.service
}

write_shell_helpers() {
  cat > "$ALIASES_FILE" <<EOF
# Codex shortcuts optimized for quick use from mobile web terminal.
alias c='codex'
alias cv='codex --version'
alias croot='cd $PROJECT_DIR'
alias cgo='cd $PROJECT_DIR && codex'
alias cm='codex-mobile'
alias cls='clear'

codex_doctor() {
  echo "[1/3] codex version"
  codex --version || return 1
  echo "[2/3] project directory"
  cd "$PROJECT_DIR" || return 1
  pwd
  echo "[3/3] OpenAI connectivity (expect HTTP/401 if reachable)"
  curl -I -m 8 -sS https://api.openai.com/v1/models | sed -n '1,3p' || true
}
EOF

  cat > "$MOBILE_BIN" <<EOF
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$PROJECT_DIR"
cd "\$PROJECT_DIR"

case "\${1:-start}" in
  start)
    shift || true
    exec codex "\$@"
    ;;
  version)
    exec codex --version
    ;;
  doctor)
    codex --version
    echo "cwd: \$(pwd)"
    echo "checking OpenAI reachability (expect HTTP/401 if reachable)..."
    curl -I -m 8 -sS https://api.openai.com/v1/models | sed -n '1,3p' || true
    ;;
  shell)
    exec bash -il
    ;;
  *)
    echo "Usage: codex-mobile [start|version|doctor|shell]"
    exit 1
    ;;
esac
EOF
  chmod +x "$MOBILE_BIN"
}

write_tmux_conf() {
  cat > "$TMUX_CONF_FILE" <<'EOF'
# Keep tmux practical on small screens.
set -g mouse on
set -sg escape-time 0
set -g history-limit 20000
set -g renumber-windows on
setw -g aggressive-resize on

# Compact status line for mobile.
set -g status-position top
set -g status-interval 60
set -g status-left-length 30
set -g status-right-length 30
set -g status-bg colour235
set -g status-fg colour250
set -g status-left '#[fg=colour46] #S '
set -g status-right '#[fg=colour39]%H:%M '

# Quick reload after edits.
bind r source-file ~/.tmux.conf \; display-message "tmux.conf reloaded"
EOF
}

write_nginx_auth_file() {
  # Enforce Basic Auth at Nginx.
  printf '%s:%s\n' "$TTYD_USER_PART" "$(openssl passwd -apr1 "$TTYD_PASS_PART")" > "$NGINX_AUTH_FILE"
  # Container worker user must be able to read this mounted file.
  chmod 644 "$NGINX_AUTH_FILE"
}

write_nginx_conf() {
  cat > "$NGINX_CONF_DIR/default.conf" <<EOF
map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}

# ttyd with --check-origin compares Origin against Host.
# Derive Host from Origin when present so HTTPS frontends (e.g. Tailscale Serve)
# still pass strict origin checks.
map \$http_origin \$ttyd_host {
    default \$http_host;
    "~^https?://([^/]+)" \$1;
}

server {
    listen 80;
    server_name _;

    location = /healthz {
        add_header Content-Type text/plain;
        return 200 "ok\n";
    }

    location /ttyd/ {
        auth_basic "Restricted";
        auth_basic_user_file /etc/nginx/htpasswd_ttyd;
        proxy_pass http://unix:/run/ttyd/ttyd.sock:/ttyd/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_set_header Host \$ttyd_host;
        proxy_set_header Origin \$http_origin;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }

    location = /code {
        return 302 /code/;
    }

    location /code/ {
        auth_basic "Restricted";
        auth_basic_user_file /etc/nginx/htpasswd_ttyd;
        proxy_pass http://unix:/run/code-server/code-server.sock:/;
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

    location = / {
        return 302 /code/;
    }

    location = /term {
        return 302 /ttyd/;
    }

    location = /term/ {
        return 302 /ttyd/;
    }
}
EOF
}

run_nginx_container() {
  docker rm -f webterm-nginx >/dev/null 2>&1 || true
  docker run -d --name webterm-nginx --restart unless-stopped \
    -p "${LISTEN_PORT}:80" \
    -v "$NGINX_CONF_DIR/default.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "$NGINX_AUTH_FILE:/etc/nginx/htpasswd_ttyd:ro" \
    -v "$RUN_DIR:/run/ttyd" \
    -v "$CODE_SERVER_RUN_DIR:/run/code-server" \
    nginx:1.27-alpine >/dev/null
}

smoke_test() {
  local code auth_code code_ide_auth_code

  test -S "$TTYD_SOCKET"
  ls -l "$TTYD_SOCKET"
  test -S "$CODE_SERVER_SOCKET"
  ls -l "$CODE_SERVER_SOCKET"

  systemctl --user is-active ttyd-codex.service >/dev/null
  systemctl --user is-active code-server-codex.service >/dev/null
  docker ps --filter name=webterm-nginx --format '{{.Names}} {{.Status}} {{.Ports}}'

  curl -fsS "http://127.0.0.1:${LISTEN_PORT}/healthz" >/dev/null

  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${LISTEN_PORT}/ttyd/")"
  if [ "$code" != "401" ] && [ "$code" != "200" ]; then
    die "Unexpected /ttyd/ status without auth: $code"
  fi

  auth_code="$(curl -s -u "$TTYD_AUTH" -o /dev/null -w '%{http_code}' "http://127.0.0.1:${LISTEN_PORT}/ttyd/")"
  if [ "$auth_code" != "200" ] && [ "$auth_code" != "302" ]; then
    die "Unexpected /ttyd/ status with auth: $auth_code"
  fi

  code_ide_auth_code="$(curl -s -u "$TTYD_AUTH" -o /dev/null -w '%{http_code}' "http://127.0.0.1:${LISTEN_PORT}/code/")"
  if [ "$code_ide_auth_code" != "200" ] && [ "$code_ide_auth_code" != "302" ]; then
    die "Unexpected /code/ status with auth: $code_ide_auth_code"
  fi
}

install_local_ttyd
install_code_server
write_env_file
write_user_service
write_code_server_env_file
write_code_server_service
write_shell_helpers
write_tmux_conf
write_nginx_auth_file
write_nginx_conf
run_nginx_container
smoke_test

log "Deployment done (IP mode)."
log "URL (IDE default): http://<SERVER_IP>:${LISTEN_PORT}/"
log "Terminal: http://<SERVER_IP>:${LISTEN_PORT}/term/"
log "IDE: http://<SERVER_IP>:${LISTEN_PORT}/code/"
log "Basic Auth: $TTYD_AUTH"
log "tmux session: codex"
