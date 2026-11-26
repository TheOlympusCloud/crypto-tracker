#!/bin/sh
# The lite node's bitcoind (compose.yaml, profile node-lite): sized from NODE_MAX_DISK_GB,
# the most disk its volume may take, in GB as Proxmox and df count them (GiB). It then starts
# through the image's own entrypoint with the arguments compose.yaml gives.
#
# What is not blocks, in GiB, measured against Bitcoin Core 31.3 (README, "A lite node"):
#   14  chain state (the UTXO set): Core 31.3's own estimate for mainnet (m_assumed_chain_state_size)
#    1  its LevelDB compacting and flushing on top of that
#    1  block index, mempool.dat (-maxmempool=300), wallet, peers, fee estimates
#    2  blocks fetched ahead of the tip while it syncs (1024 of them), which cannot be pruned
# Bump RESERVE with the image when a new Core raises its chain state estimate.
set -eu

RESERVE=18
MIN=20 # RESERVE and 2 GiB of blocks, about a week: Core keeps the newest 288 (~0.5) anyway

cap=${NODE_MAX_DISK_GB:-}
case $cap in
'' | *[!0-9]*) cap=0 ;;
esac
if [ "$cap" -lt "$MIN" ]; then
    echo "bitcoind-lite: NODE_MAX_DISK_GB must be a whole number of GB, at least $MIN (got '${NODE_MAX_DISK_GB:-}'): the chain state alone takes about 15" >&2
    exit 1
fi

# -prune is the target for blocks and their undo data, in MiB.
exec /entrypoint.sh "$@" -prune=$(((cap - RESERVE) * 1024))
