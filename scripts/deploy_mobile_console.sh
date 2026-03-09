#!/usr/bin/env bash
set -euo pipefail

log() { printf '[INFO] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1; }

if ! need_cmd docker; then die "docker is required"; fi
if ! need_cmd tmux; then die "tmux is required"; fi
if ! need_cmd python3; then die "python3 is required"; fi
if ! need_cmd openssl; then die "openssl is required"; fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

WORKSPACE_ROOT="${WORKSPACE_ROOT:-$REPO_ROOT}"
PROJECTS_ROOT="${PROJECTS_ROOT:-$HOME/codex}"
PROJECT_DIR="${PROJECT_DIR:-$PROJECTS_ROOT}"
MOBILE_USER="${MOBILE_USER:-chd}"
MOBILE_PASS="${MOBILE_PASS:-chd}"
LISTEN_PORT="${LISTEN_PORT:-80}"
ENABLE_FIREWALL="${ENABLE_FIREWALL:-0}"
VPN_CIDR="${VPN_CIDR:-}"
GITHUB_OWNER="${GITHUB_OWNER:-}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
GITHUB_DEFAULT_PRIVATE="${GITHUB_DEFAULT_PRIVATE:-1}"
WEBTERM_CONTAINER_NAME="${WEBTERM_CONTAINER_NAME:-webterm-nginx}"

WEBTERM_BASE="$HOME/.local/share/web-terminal"
WEBTERM_NGINX_DIR="$WEBTERM_BASE/nginx"
WEBTERM_AUTH_FILE="$WEBTERM_NGINX_DIR/htpasswd_ttyd"
WEBTERM_AUTH_ENV="$WEBTERM_BASE/mobile_auth.env"

TTYD_ENV_FILE="$WEBTERM_BASE/ttyd.env"
TTYD_SERVICE_FILE="$HOME/.config/systemd/user/ttyd-codex.service"
CODE_SERVICE_FILE="$HOME/.config/systemd/user/code-server-codex.service"
CODE_ENV_FILE="$WEBTERM_BASE/code-server.env"

BRIDGE_BASE="$HOME/.local/share/codex-mobile"
BRIDGE_ENV="$BRIDGE_BASE/codex-bridge.env"
BRIDGE_VENV="$BRIDGE_BASE/venv"
BRIDGE_SERVICE="$HOME/.config/systemd/user/codex-bridge.service"
BRIDGE_RUN_DIR="$HOME/.local/run/codex-bridge"

TTYD_RUN_DIR="$HOME/.local/run/ttyd"
CODE_RUN_DIR="$HOME/.local/run/code-server"
FRONTEND_DIR="$WORKSPACE_ROOT/mobile_console/frontend"

TTYD_BIN="${TTYD_BIN:-$HOME/.local/bin/ttyd}"
CODE_SERVER_BIN="${CODE_SERVER_BIN:-$HOME/.local/bin/code-server}"
TTYD_LD_LIBRARY_PATH="${TTYD_LD_LIBRARY_PATH:-}"

if [ -z "$TTYD_LD_LIBRARY_PATH" ] && [ -d "$HOME/miniconda3/lib" ]; then
  TTYD_LD_LIBRARY_PATH="$HOME/miniconda3/lib"
fi

mkdir -p "$WEBTERM_NGINX_DIR" "$BRIDGE_BASE" "$HOME/.config/systemd/user" "$BRIDGE_RUN_DIR"
chmod 755 "$BRIDGE_RUN_DIR"
mkdir -p "$PROJECTS_ROOT"

cat > "$WEBTERM_AUTH_ENV" <<AUTH
MOBILE_USER="$MOBILE_USER"
MOBILE_PASS="$MOBILE_PASS"
AUTH
chmod 600 "$WEBTERM_AUTH_ENV"
printf '%s:%s\n' "$MOBILE_USER" "$(openssl passwd -apr1 "$MOBILE_PASS")" > "$WEBTERM_AUTH_FILE"
chmod 644 "$WEBTERM_AUTH_FILE"

if [ ! -x "$TTYD_BIN" ] && need_cmd ttyd; then
  TTYD_BIN="$(command -v ttyd)"
fi
if [ ! -x "$CODE_SERVER_BIN" ] && need_cmd code-server; then
  CODE_SERVER_BIN="$(command -v code-server)"
fi
if [ ! -x "$TTYD_BIN" ]; then
  die "ttyd not found. Set TTYD_BIN or install ttyd"
fi
if [ ! -x "$CODE_SERVER_BIN" ]; then
  die "code-server not found. Set CODE_SERVER_BIN or install code-server"
fi

cat > "$TTYD_ENV_FILE" <<EOF2
PROJECT_DIR="$PROJECT_DIR"
TTYD_LD_LIBRARY_PATH="$TTYD_LD_LIBRARY_PATH"
EOF2
chmod 600 "$TTYD_ENV_FILE"

cat > "$TTYD_SERVICE_FILE" <<EOF2
[Unit]
Description=ttyd for Codex (user service)
After=network.target

[Service]
Type=simple
EnvironmentFile=%h/.local/share/web-terminal/ttyd.env
ExecStartPre=/usr/bin/mkdir -p %h/.local/run/ttyd
ExecStartPre=/usr/bin/chmod 755 %h/.local/run/ttyd
ExecStartPre=/usr/bin/rm -f %h/.local/run/ttyd/ttyd.sock
ExecStart=/usr/bin/env LD_LIBRARY_PATH=\${TTYD_LD_LIBRARY_PATH} ${TTYD_BIN} \\
  -i %h/.local/run/ttyd/ttyd.sock \\
  -O \\
  -b /ttyd \\
  -P 5 \\
  -t "fontSize=14" \\
  -t "lineHeight=1.08" \\
  -t "cursorBlink=true" \\
  -t "scrollback=5000" \\
  bash -lc "cd '\${PROJECT_DIR}' && exec tmux new -A -s codex"
ExecStartPost=/bin/bash -lc 'for i in {1..30}; do [ -S %h/.local/run/ttyd/ttyd.sock ] && chmod 666 %h/.local/run/ttyd/ttyd.sock && exit 0; sleep 0.1; done; exit 1'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF2

cat > "$CODE_ENV_FILE" <<EOF2
PROJECT_DIR="$PROJECT_DIR"
EOF2
chmod 600 "$CODE_ENV_FILE"

cat > "$CODE_SERVICE_FILE" <<EOF2
[Unit]
Description=code-server for mobile web IDE
After=network.target

[Service]
Type=simple
EnvironmentFile=%h/.local/share/web-terminal/code-server.env
ExecStartPre=/usr/bin/mkdir -p %h/.local/run/code-server
ExecStartPre=/usr/bin/chmod 755 %h/.local/run/code-server
ExecStartPre=/usr/bin/rm -f %h/.local/run/code-server/code-server.sock
ExecStart=${CODE_SERVER_BIN} \
  --socket %h/.local/run/code-server/code-server.sock \
  --socket-mode 666 \
  --auth none \
  --disable-telemetry \
  --disable-update-check \
  --abs-proxy-base-path /ide \
  \${PROJECT_DIR}
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF2

if [ ! -d "$BRIDGE_VENV" ]; then
  python3 -m venv "$BRIDGE_VENV"
fi
"$BRIDGE_VENV/bin/pip" install --upgrade pip >/dev/null
"$BRIDGE_VENV/bin/pip" install -r "$WORKSPACE_ROOT/mobile_console/backend/requirements.txt" >/dev/null

cat > "$BRIDGE_ENV" <<EOF2
WORKSPACE_ROOT="$WORKSPACE_ROOT"
CODEX_WORKSPACE_ROOT="$PROJECTS_ROOT"
CODEX_DEFAULT_REPO="$PROJECT_DIR"
CODEX_MOBILE_STATE_DIR="$BRIDGE_BASE"
CODEX_MOBILE_WRAPPER="$WORKSPACE_ROOT/mobile_console/scripts/codex-mobile"
GITHUB_OWNER="$GITHUB_OWNER"
GITHUB_TOKEN="$GITHUB_TOKEN"
GITHUB_DEFAULT_PRIVATE="$GITHUB_DEFAULT_PRIVATE"
EOF2
chmod 600 "$BRIDGE_ENV"

cat > "$BRIDGE_SERVICE" <<'EOF2'
[Unit]
Description=codex-bridge (FastAPI backend for Codex Mobile)
After=network.target

[Service]
Type=simple
EnvironmentFile=%h/.local/share/codex-mobile/codex-bridge.env
ExecStartPre=/usr/bin/mkdir -p %h/.local/run/codex-bridge
ExecStartPre=/usr/bin/chmod 755 %h/.local/run/codex-bridge
ExecStartPre=/usr/bin/rm -f %h/.local/run/codex-bridge/codex-bridge.sock
ExecStart=%h/.local/share/codex-mobile/venv/bin/uvicorn \
  app.main:app \
  --app-dir ${WORKSPACE_ROOT}/mobile_console/backend \
  --uds %h/.local/run/codex-bridge/codex-bridge.sock \
  --timeout-graceful-shutdown 8 \
  --timeout-keep-alive 20 \
  --proxy-headers
Restart=always
RestartSec=2
KillSignal=SIGINT
TimeoutStopSec=10

[Install]
WantedBy=default.target
EOF2

cp "$WORKSPACE_ROOT/mobile_console/nginx/mobile-console.conf" "$WEBTERM_NGINX_DIR/default.conf"

systemctl --user daemon-reload
systemctl --user enable ttyd-codex.service
systemctl --user enable code-server-codex.service
systemctl --user enable codex-bridge.service
systemctl --user restart ttyd-codex.service
systemctl --user restart code-server-codex.service
systemctl --user restart codex-bridge.service

if [ -x "$HOME/.local/bin/tsu" ]; then
  "$HOME/.local/bin/tsu" serve reset >/dev/null 2>&1 || true
fi

if [ "$ENABLE_FIREWALL" = "1" ]; then
  if [ -z "$VPN_CIDR" ]; then
    die "ENABLE_FIREWALL=1 requires VPN_CIDR, e.g. 10.26.43.0/24"
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    if command -v ufw >/dev/null 2>&1; then
      sudo ufw allow from "$VPN_CIDR" to any port "$LISTEN_PORT" proto tcp
      sudo ufw deny "$LISTEN_PORT"/tcp
      log "UFW policy updated for VPN_CIDR=$VPN_CIDR port=$LISTEN_PORT"
    else
      log "ufw not found; skip firewall config"
    fi
  else
    log "No passwordless sudo; skip firewall config"
  fi
fi

docker rm -f "$WEBTERM_CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d --name "$WEBTERM_CONTAINER_NAME" --restart unless-stopped \
  -p "${LISTEN_PORT}:80" \
  -v "$WEBTERM_NGINX_DIR/default.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$WEBTERM_AUTH_FILE:/etc/nginx/htpasswd_ttyd:ro" \
  -v "$TTYD_RUN_DIR:/run/ttyd" \
  -v "$CODE_RUN_DIR:/run/code-server" \
  -v "$BRIDGE_RUN_DIR:/run/codex-bridge" \
  -v "$FRONTEND_DIR:/usr/share/nginx/html/mobile:ro" \
  nginx:1.27-alpine >/dev/null

for i in {1..20}; do
  if curl -fsS "http://127.0.0.1:${LISTEN_PORT}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
curl -fsS -u "$MOBILE_USER:$MOBILE_PASS" "http://127.0.0.1:${LISTEN_PORT}/" -o /dev/null
curl -fsS -u "$MOBILE_USER:$MOBILE_PASS" "http://127.0.0.1:${LISTEN_PORT}/term/" -o /dev/null
curl -fsS -u "$MOBILE_USER:$MOBILE_PASS" "http://127.0.0.1:${LISTEN_PORT}/ide/" -o /dev/null
curl -fsS -u "$MOBILE_USER:$MOBILE_PASS" "http://127.0.0.1:${LISTEN_PORT}/api/healthz" -o /dev/null

log "Deployed Codex Mobile MVP"
log "Projects root: $PROJECTS_ROOT"
log "Entry:  http://<SERVER_VPN_IP>:${LISTEN_PORT}/"
log "Term:   http://<SERVER_VPN_IP>:${LISTEN_PORT}/term/"
log "IDE:    http://<SERVER_VPN_IP>:${LISTEN_PORT}/ide/"
log "User:   $MOBILE_USER"
log "Pass:   $MOBILE_PASS"
log "Container: $WEBTERM_CONTAINER_NAME"
