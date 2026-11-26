#!/usr/bin/env bash
# Create a Debian 13 container on this Proxmox VE host and start the app in it with
# `docker compose up -d`, as from any fresh clone. Run it as root on the host:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/TheOlympusCloud/crypto-tracker/main/proxmox/install.sh)"
#
# Each setting below can be given before the command, e.g. LXC_IP=192.168.1.50/24,gw=192.168.1.1
set -euo pipefail

MEMPOOL=${MEMPOOL:-0}                       # 1: the older name of NODE=full
if [ "$MEMPOOL" = 1 ]; then default_node=full; else default_node=none; fi
NODE=${NODE:-$default_node}                 # a Bitcoin node of your own too: full or lite (README)
MEMPOOL_DISK=${MEMPOOL_DISK:-1200}          # GB added for a full node; node and index take ~950 today
MEMPOOL_MIN_DISK=1100                       # GB: today's ~950 and a year of growth
NODE_MAX_DISK_GB=${NODE_MAX_DISK_GB:-50}    # GB added for a lite node: the most it takes
NODE_MIN_DISK=20                            # GB: its floor, as in node/bitcoind-lite.sh
LXC_ID=${LXC_ID:-}                          # default: the next free id
LXC_HOSTNAME=${LXC_HOSTNAME:-crypto-tracker}
LXC_STORAGE=${LXC_STORAGE:-}                # default: the first storage that holds containers
LXC_TEMPLATE_STORAGE=${LXC_TEMPLATE_STORAGE:-local}
LXC_BRIDGE=${LXC_BRIDGE:-vmbr0}
LXC_IP=${LXC_IP:-dhcp}                      # or static: 192.168.1.50/24,gw=192.168.1.1
LXC_DISK=${LXC_DISK:-16}                    # GB
case $NODE in
full) node_memory=8192 node_cores=4 ;;
lite) node_memory=6144 node_cores=4 ;;
*) node_memory=4096 node_cores=2 ;;
esac
LXC_MEMORY=${LXC_MEMORY:-$node_memory}      # MB, at least 3072: the build peaks at about 2.5 GB
LXC_CORES=${LXC_CORES:-$node_cores}         # at least 2
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
case $MEMPOOL in 0 | 1) ;; *) die "MEMPOOL must be 0 or 1" ;; esac
case $NODE in none | full | lite) ;; *) die "NODE must be none, full or lite" ;; esac
disk=$LXC_DISK
if [ "$NODE" = full ]; then
    [ "$MEMPOOL_DISK" -ge "$MEMPOOL_MIN_DISK" ] ||
        die "MEMPOOL_DISK must be at least $MEMPOOL_MIN_DISK (GB): bitcoind with its transaction index and Fulcrum's address index take about 950 GB, and grow about 90 GB a year"
    disk=$((LXC_DISK + MEMPOOL_DISK)) added="MEMPOOL_DISK $MEMPOOL_DISK"
elif [ "$NODE" = lite ]; then
    case $NODE_MAX_DISK_GB in '' | 0* | *[!0-9]*) cap=0 ;; *) cap=$NODE_MAX_DISK_GB ;; esac
    [ "$cap" -ge "$NODE_MIN_DISK" ] ||
        die "NODE_MAX_DISK_GB must be a whole number of GB, at least $NODE_MIN_DISK: a pruned node's chain state alone takes about 15 (README, \"A lite node\")"
    disk=$((LXC_DISK + cap)) added="NODE_MAX_DISK_GB $cap"
fi

LXC_ID=${LXC_ID:-$(pvesh get /cluster/nextid)}
if pct status "$LXC_ID" >/dev/null 2>&1; then
    die "container $LXC_ID exists already: set LXC_ID to a free id"
fi
if [ -z "$LXC_STORAGE" ]; then
    LXC_STORAGE=$(pvesm status --content rootdir | awk 'NR == 2 { print $1 }')
    [ -n "$LXC_STORAGE" ] || die "no storage holds containers: set LXC_STORAGE"
fi
if [ "$NODE" != none ]; then
    # The node fills its disk for real: thin provisioning does not make that room.
    free=$(pvesm status --storage "$LXC_STORAGE" | awk 'NR == 2 { print int($6 / 1048576) }')
    [ "${free:-0}" -ge "$disk" ] ||
        die "storage $LXC_STORAGE has ${free:-0} GB free; a container with a Bitcoin node needs $disk GB (LXC_DISK $LXC_DISK + $added): choose another LXC_STORAGE"
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
    --rootfs "$LXC_STORAGE:$disk" \
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
# In .env, so that every later `docker compose up -d` (the Update line) keeps it on.
if [ "$NODE" = full ]; then
    in_ct sh -c "echo COMPOSE_PROFILES=mempool >>$APP/.env"
elif [ "$NODE" = lite ]; then
    in_ct sh -c "echo COMPOSE_PROFILES=node-lite >>$APP/.env && echo NODE_MAX_DISK_GB=$cap >>$APP/.env"
fi

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
if [ "$NODE" = full ]; then
    cat <<EOF

  Bitcoin node  syncing now: a day or more for the chain, then hours for its address index.
                Until it is done, Bitcoin addresses wait for it (never a public server).
  Its progress  pct exec $LXC_ID -- docker compose --project-directory $APP logs -f --tail 5 bitcoind fulcrum
  Explorer      http://${ip:-<container ip>}:8080/      mempool, on your own node
EOF
fi
if [ "$NODE" = lite ]; then
    cat <<EOF

  Bitcoin node  lite, at most $cap GB: syncing now, a day or more. Your Bitcoin wallets wait
                for it (never a public server); other Bitcoin addresses are read from a
                public server, as the Chains list says.
  Its progress  pct exec $LXC_ID -- docker compose --project-directory $APP logs -f --tail 5 bitcoind-lite
EOF
fi
