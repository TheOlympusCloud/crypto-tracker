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
#
# Started from a snapshot (NODE_SNAPSHOT, README "From a snapshot"), it keeps a second chain
# state until it has checked the snapshot from the genesis block on: SECOND GiB more, the
# UTXO set at the snapshot's block (12.4 at 935,000) with its compacting, and the blocks that
# chain state fetches ahead. The snapshot file itself fits in that room until it is loaded.
set -eu

RESERVE=18
MIN=20 # RESERVE and 2 GiB of blocks, about a week: Core keeps the newest 288 (~0.5) anyway
SECOND=17

cap=${NODE_MAX_DISK_GB:-}
case $cap in
'' | 0* | *[!0-9]*) cap=0 ;;
esac
# chainstate_snapshot: a snapshot loaded and not checked yet, even with NODE_SNAPSHOT removed.
if [ -n "${NODE_SNAPSHOT:-}" ] || [ -d "${BITCOIN_DATA:-/home/bitcoin/.bitcoin}/chainstate_snapshot" ]; then
    RESERVE=$((RESERVE + SECOND)) MIN=$((MIN + SECOND))
    if [ "$cap" -lt "$MIN" ]; then
        echo "bitcoind-lite: NODE_MAX_DISK_GB must be a whole number of GB, at least $MIN with a snapshot (got '${NODE_MAX_DISK_GB:-}'): until it has checked the snapshot the node keeps two chain states of about 15 GB each, and Bitcoin Core then keeps at least 1100 MiB of blocks" >&2
        exit 1
    fi
    echo "bitcoind-lite: a snapshot: $SECOND of the $cap GB go to a second chain state until the node has checked the snapshot, $((cap - RESERVE)) GB to blocks meanwhile. Once checked, remove NODE_SNAPSHOT from .env to give them back to blocks (README, \"From a snapshot\")." >&2
elif [ "$cap" -lt "$MIN" ]; then
    echo "bitcoind-lite: NODE_MAX_DISK_GB must be a whole number of GB, at least $MIN (got '${NODE_MAX_DISK_GB:-}'): the chain state alone takes about 15" >&2
    exit 1
fi

# -prune is the target for blocks and their undo data, in MiB.
exec /entrypoint.sh "$@" -prune=$(((cap - RESERVE) * 1024))
