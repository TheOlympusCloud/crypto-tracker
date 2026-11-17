#!/usr/bin/env bash
# Install or update Crypto tracker on a Debian host as a systemd service, without Docker.
# Run it as root from a copy of this repository:   sudo ./install.sh
# Running it again updates the code and keeps the wallets, labels and settings.
set -euo pipefail
umask 022

APP=/opt/crypto-tracker          # code, virtualenv and uv: replaced on every run
ENV_FILE=/etc/crypto-tracker/env # settings and the password: written once, then yours
DATA=/var/lib/crypto-tracker     # the database: never touched by this script
UNIT=/etc/systemd/system/crypto-tracker.service
RUN_AS=crypto-tracker
UV_VERSION=0.13.6

die() {
    echo "install.sh: $*" >&2
    exit 1
}

[ "$(id -u)" = 0 ] || die "run it as root: sudo $0"
command -v apt-get >/dev/null || die "needs an apt-based system (Debian 12 or 13)"
[ -d /run/systemd/system ] || die "needs systemd running as the init system"
src=$(cd "$(dirname "$0")" && pwd)
[ -d "$src/tracker" ] && [ -f "$src/uv.lock" ] || die "run it from a copy of the repository"

echo "==> System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends ca-certificates curl python3

echo "==> uv $UV_VERSION"
if [ "$("$APP/bin/uv" --version 2>/dev/null)" != "uv $UV_VERSION" ]; then
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" |
        env UV_UNMANAGED_INSTALL="$APP/bin" sh
fi

echo "==> Code and dependencies"
id -u "$RUN_AS" >/dev/null 2>&1 ||
    useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$RUN_AS"
mkdir -p "$APP/app"
rm -rf "$APP/app/tracker" # no stale modules after an update
cp -r "$src/tracker" "$src/pyproject.toml" "$src/uv.lock" "$APP/app/"
chmod -R u=rwX,go=rX "$APP/app/tracker"
# If the system Python is older than the app needs, uv downloads one: keep it readable.
(cd "$APP/app" && UV_PYTHON_INSTALL_DIR="$APP/python" \
    "$APP/bin/uv" sync --frozen --no-dev --no-cache --compile-bytecode)

password=
if [ ! -e "$ENV_FILE" ]; then
    password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
    mkdir -p "$(dirname "$ENV_FILE")"
    (
        umask 077
        cat >"$ENV_FILE" <<EOF
# Settings of crypto-tracker.service, see the table in the README.
# After a change: systemctl restart crypto-tracker
CT_HOST=0.0.0.0
CT_PORT=8000
CT_DB=$DATA/tracker.db
CT_PASSWORD=$password
EOF
    )
fi

echo "==> systemd service"
cat >"$UNIT" <<EOF
[Unit]
Description=Crypto tracker
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_AS
EnvironmentFile=$ENV_FILE
WorkingDirectory=$APP/app
ExecStart=$APP/app/.venv/bin/python -m tracker
StateDirectory=$(basename "$DATA")
StateDirectoryMode=0700
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --quiet crypto-tracker
systemctl restart crypto-tracker

# 401 is the app asking for the password, 200 a loopback-only setup without one.
port=$(sed -n 's/^CT_PORT=//p' "$ENV_FILE")
host=$(sed -n 's/^CT_HOST=//p' "$ENV_FILE")
case "$host" in "" | 0.0.0.0 | ::) host=127.0.0.1 ;; esac
code=
for _ in $(seq 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://$host:${port:-8000}/" || true)
    case "$code" in 200 | 401) break ;; esac
    sleep 1
done
case "$code" in
200 | 401) ;;
*)
    journalctl -u crypto-tracker -n 30 --no-pager >&2 || true
    die "the service did not come up, see the log above"
    ;;
esac

cat <<EOF

Crypto tracker is running and starts on boot.

  Open       http://$(hostname -I | awk '{print $1}'):${port:-8000}
EOF
if [ -n "$password" ]; then
    cat <<EOF
  User name  anything
  Password   $password
             Shown only this once; it is kept in $ENV_FILE (root only).
EOF
else
    echo "  Password   unchanged, in $ENV_FILE"
fi
cat <<EOF

  Settings   $ENV_FILE, then: systemctl restart crypto-tracker
  Logs       journalctl -u crypto-tracker -f
  Stop       systemctl stop crypto-tracker      (start again with: start)
  Update     get the new code, then run this script again
  Uninstall  systemctl disable --now crypto-tracker
             rm -r $UNIT $APP $(dirname "$ENV_FILE")
             userdel $RUN_AS
             rm -r $DATA      # your wallets, labels and history

The phone camera (QR scan) needs HTTPS: see "Install on a server" in the README.
EOF
