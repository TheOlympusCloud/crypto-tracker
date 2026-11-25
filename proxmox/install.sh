#!/usr/bin/env bash
# Create a Debian 13 container on this Proxmox VE host and start the app in it with
# `docker compose up -d`, as from any fresh clone. Run it as root on the host:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/TheOlympusCloud/crypto-tracker/main/proxmox/install.sh)"
#
# Each setting below can be given before the command, e.g. LXC_IP=192.168.1.50/24,gw=192.168.1.1
set -euo pipefail

LXC_ID=${LXC_ID:-}                          # default: the next free id
LXC_HOSTNAME=${LXC_HOSTNAME:-crypto-tracker}
LXC_STORAGE=${LXC_STORAGE:-}                # default: the first storage that holds containers
LXC_TEMPLATE_STORAGE=${LXC_TEMPLATE_STORAGE:-local}
LXC_BRIDGE=${LXC_BRIDGE:-vmbr0}
LXC_IP=${LXC_IP:-dhcp}                      # or static: 192.168.1.50/24,gw=192.168.1.1
LXC_DISK=${LXC_DISK:-16}                    # GB
LXC_MEMORY=${LXC_MEMORY:-4096}              # MB, at least 3072: the build peaks at about 2.5 GB
LXC_CORES=${LXC_CORES:-2}                   # at least 2
REPO_URL=${REPO_URL:-https://github.com/TheOlympusCloud/crypto-tracker}
REPO_REF=${REPO_REF:-main}                  # a branch or a tag
APP=/opt/crypto-tracker                     # inside the container

die() {
    echo "install.sh: $*" >&2
    exit 1
}
in_ct() { pct exec "$LXC_ID" -- "$@"; }

[ "$(id -u)" = 0 ] || die "run it as root on the Proxmox VE host"
command -v pct >/dev/null || die "pct not found: run it on a Proxmox VE host"
[ "$LXC_MEMORY" -ge 3072 ] || die "LXC_MEMORY must be at least 3072 (MB): the build needs about 2.5 GB"
[ "$LXC_CORES" -ge 2 ] || die "LXC_CORES must be at least 2"

LXC_ID=${LXC_ID:-$(pvesh get /cluster/nextid)}
if pct status "$LXC_ID" >/dev/null 2>&1; then
    die "container $LXC_ID exists already: set LXC_ID to a free id"
fi
if [ -z "$LXC_STORAGE" ]; then
    LXC_STORAGE=$(pvesm status --content rootdir | awk 'NR == 2 { print $1 }')
    [ -n "$LXC_STORAGE" ] || die "no storage holds containers: set LXC_STORAGE"
fi

echo "==> Debian 13 template"
pveam update >/dev/null
template=$(pveam available --section system |
    awk '$2 ~ /^debian-13-standard_/ { print $2 }' | sort -V | tail -n 1)
[ -n "$template" ] || die "pveam offers no debian-13-standard template"
if ! pveam list "$LXC_TEMPLATE_STORAGE" | grep -F "/$template" >/dev/null; then
    pveam download "$LXC_TEMPLATE_STORAGE" "$template"
fi

echo "==> Container $LXC_ID ($LXC_HOSTNAME) on $LXC_STORAGE"
# From here on a failure leaves a container behind: say how to remove it before a retry.
trap '[ $? = 0 ] || echo "install.sh: to start over, remove container $LXC_ID: pct destroy $LXC_ID --force" >&2' EXIT
# nesting and keyctl let Docker run in an unprivileged container.
pct create "$LXC_ID" "$LXC_TEMPLATE_STORAGE:vztmpl/$template" \
    --hostname "$LXC_HOSTNAME" --ostype debian --unprivileged 1 \
    --features nesting=1,keyctl=1 \
    --cores "$LXC_CORES" --memory "$LXC_MEMORY" --swap 512 \
    --rootfs "$LXC_STORAGE:$LXC_DISK" \
    --net0 "name=eth0,bridge=$LXC_BRIDGE,ip=$LXC_IP" \
    --onboot 1 --start 1
# shellcheck disable=SC2016 # expanded inside the container
in_ct sh -c 'for _ in $(seq 60); do getent hosts deb.debian.org >/dev/null && exit 0; sleep 2; done; exit 1' ||
    die "container $LXC_ID cannot reach deb.debian.org: check LXC_BRIDGE and LXC_IP"

echo "==> Docker and the code"
in_ct sh -c 'export DEBIAN_FRONTEND=noninteractive && apt-get update -q &&
    apt-get install -y -q --no-install-recommends ca-certificates curl git \
        docker.io docker-cli docker-compose docker-buildx'
in_ct git clone -q --depth 1 --branch "$REPO_REF" "$REPO_URL" "$APP"

echo "==> Building and starting (several minutes the first time)"
in_ct sh -c "cd $APP && docker compose up -d --build"
code=
for _ in $(seq 60); do
    # 401: the app is up and asks for the password.
    code=$(in_ct curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ || true)
    [ "$code" = 401 ] && break
    sleep 2
done
if [ "$code" != 401 ]; then
    in_ct sh -c "cd $APP && docker compose logs --tail 30" >&2 || true
    die "the app did not come up, see the log above"
fi
# The first start made a password and keeps it in the data volume.
password=$(in_ct sh -c "cd $APP && docker compose exec -T crypto-tracker cat /data/password")
ip=$(in_ct hostname -I | awk '{ print $1 }')

cat <<EOF

Running in container $LXC_ID ($LXC_HOSTNAME), started with the host.

  Open       http://${ip:-<container ip>}:8000/            the privacy scanner
             http://${ip:-<container ip>}:8000/tracker/    your wallets
  User name  anything
  Password   $password
             Kept in the container's data volume: pct exec $LXC_ID -- sh -c 'cd $APP && docker compose exec crypto-tracker cat /data/password'
             It crosses the network unencrypted until HTTPS is set up (README).

  Logs       pct exec $LXC_ID -- docker compose --project-directory $APP logs -f
  Settings   pct exec $LXC_ID -- nano $APP/.env      (the README's table), then run the Update line
  Update     pct exec $LXC_ID -- sh -c 'cd $APP && git pull -q --ff-only && docker compose up -d --build'
  Uninstall  pct stop $LXC_ID && pct destroy $LXC_ID      (deletes your wallets too)
EOF
