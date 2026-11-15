# Crypto tracker

Self-hosted wallet graph. You list the wallets that are yours, mark the ones other people
know about as **public**, and the app shows:

- every wallet as a node, with balances, and every transfer as an arrow, across chains;
- the external addresses your wallets dealt with, labelled as exchange, DEX, bridge,
  mixer and so on, and their own counterparties when you expand them;
- **traces**: the strongest on-chain path between each pair of your wallets, with a
  confidence, and a warning when a private wallet can be tied to a public one.

Chains: Bitcoin and Ethereum out of the box; any EVM chain with an Etherscan-compatible
API through a config file. One web app, installable on a phone as a PWA.

## Run

```sh
uv sync
uv run python -m tracker          # http://127.0.0.1:8000
```

Add your wallets, tick "Also download open label sets" the first time (about 500,000
labels, takes a minute), and press Sync.

With Docker (the image is not exercised by the test suite):

```sh
docker build -t crypto-tracker .
docker run -p 8000:8000 -v tracker-data:/data -e CT_PASSWORD=change-me crypto-tracker
```

| Variable | Default | Meaning |
|---|---|---|
| `CT_HOST`, `CT_PORT` | `127.0.0.1`, `8000` | Listen address. Anything but loopback requires `CT_PASSWORD`. |
| `CT_PASSWORD` | unset | HTTP Basic password (any user name). Put TLS in front of it. |
| `CT_DB` | `tracker.db` | SQLite file. |
| `CT_ESPLORA` | `https://blockstream.info/api` | Bitcoin indexer (Esplora API: Blockstream, mempool.space, your own). |
| `CT_CHAINS` | unset | JSON file of EVM chains, see `chains.example.json`. |
| `CT_DELAY` | `0.25` | Seconds between API calls. |
| `CT_BTC_PAGES`, `CT_EVM_ROWS` | `8`, `1000` | History loaded per address (pages of 25 / rows per list). |

## Privacy

The database says which wallets are yours. Keep it on your machine.

Every address you sync is sent to the indexer that answers for it, so that indexer sees
your wallets together, from one IP address. That is exactly the link this app helps you
avoid. For real privacy point `CT_ESPLORA` and `CT_CHAINS` at indexers you run
(electrs/mempool for Bitcoin, Blockscout for EVM), or at least reach the public ones
through Tor or a VPN.

## Data sources

- Bitcoin: any Esplora API. No key.
- EVM: any Etherscan-compatible API. The default is Routescan for Ethereum, without a key.
  Other chains need a free key (Etherscan v2 or Blockscout) or your own Blockscout; public
  Blockscout instances allow only a handful of calls without one.
- Labels: [etherscan-labels](https://github.com/brianleect/etherscan-labels) (EVM, MIT, a
  2023 snapshot) and [GraphSense TagPacks](https://github.com/graphsense/graphsense-tagpacks)
  (Bitcoin and Ethereum, MIT). They are downloaded on request, not shipped. Labels you set
  by hand in the UI always win.

## What is a fact and what is a guess

Solid arrows are transfers read from the chain. Dashed lines are guesses that two
addresses share an owner; each carries a confidence, shown on the line. The numbers live
in `tracker/heuristics.py` and `tracker/graph.py`.

| Guess | Confidence | Rule |
|---|---|---|
| common-input | 0.9 | Two addresses are inputs of one Bitcoin transaction. |
| common-input, CoinJoin | 0.2 | Same, but the transaction has 3+ inputs and 3+ equal outputs. |
| change | 0.5 to 0.75 | In a 2-output payment, the output that matches the input script type (0.6), or is the non-round one (0.5); 0.75 when both rules agree, nothing when they disagree. |
| bridge | 0.5 to 0.95 | Money leaves one of your wallets on chain X and the same asset reaches another of your wallets on chain Y within 6 hours, 0 to 3% smaller. +0.25 if a labelled bridge is involved, +0.15 if within 0.5%, +0.1 if within 10 minutes; divided by the number of equally plausible matches. |

Several transactions supporting the same guess combine as `1 - (1-c1)(1-c2)...`.

A trace multiplies the confidences along its path (a transfer counts 1.0) and then by 0.7
for every stranger it passes through. Traces never pass through a labelled service or a
very active address: two people using Binance are not linked on-chain. Such shared
services are still listed on the trace, because the service itself can link you.

## Known limits

- Bridges that change the asset (THORChain, BTC to ETH swaps) are not matched: that needs
  price data.
- A bridge is only matched when both ends are wallets you declared.
- No fiat values.
- Solana, Tron and other non-EVM chains are not supported.
- Very active addresses load only their newest history and are flagged in the UI.
- Token balances come from the transfer history unless the indexer has a token-balance
  call (`"tokenlist": true`), so rebasing tokens are off.
- Addresses are checked by format, not checksum.
- An unlabelled exchange wallet looks like a stranger and can produce a false trace;
  label it in the UI and the trace disappears.

## Develop

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The graph library, Cytoscape.js 3.35.0 (MIT), is vendored in `tracker/static/` so the app
loads nothing from the internet.
