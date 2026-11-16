# Crypto tracker

Self-hosted view of everything you hold, and of what ties your wallets together. You
list the wallets that are yours and mark as **public** the ones tied to your identity
(withdrawals from a KYC exchange, addresses you gave to people). The app then shows:

- **Portfolio**: every asset across all wallets and chains, split into private and public.
- **A verdict per wallet**: either the other wallets of yours it can be tied to, directly
  or through strangers, bridges, common inputs or a chain of your own wallets, with hop
  count and confidence; or "no link found", next to the list of what that check cannot
  rule out. It never says "safe".
- **Funds origin**: where each wallet's money came from, followed back to a labelled
  source (exchange, bridge, mixer...), one of your wallets, or the edge of what is known.
- **A 3D graph** of your wallets, their counterparties (labelled by type) and the links
  between them. Any link or origin opens as a highlighted path with each hop spelled
  out. A flat 2D graph is the default on phones and where WebGL is missing.

- **Fiat values** for the whole portfolio, each wallet and each asset, and a value history
  that grows with every sync.
- **Alerts** when a verdict gets worse: a new link between two wallets, or a stronger one.

A wallet is a single address or an HD wallet: paste an xpub, ypub, zpub or output
descriptor and all its used addresses are found and treated as one wallet.

Chains: Bitcoin and Ethereum out of the box; any EVM chain with an Etherscan-compatible
API through a config file. One web app, installable on a phone as a PWA.

## Run

```sh
uv sync
uv run python -m tracker          # http://127.0.0.1:8000
```

Under "Wallets & sync" add your wallets, tick "Also download open label sets" the first time (about 500,000
labels, takes a minute), and press Sync.

With Docker (the image is not exercised by the test suite):

```sh
docker build -t crypto-tracker .
docker run -p 8000:8000 -v tracker-data:/data -e CT_PASSWORD=change-me crypto-tracker
```

| Variable | Default | Meaning |
|---|---|---|
| `CT_HOST`, `CT_PORT` | `127.0.0.1`, `8000` | Listen address. Anything but loopback requires `CT_PASSWORD`; without a password the app only answers to `localhost`/`127.0.0.1`. |
| `CT_PASSWORD` | unset | HTTP Basic password (any user name). Put TLS in front of it. |
| `CT_DB` | `tracker.db` | SQLite file. |
| `CT_ESPLORA` | `https://blockstream.info/api` | Bitcoin indexer (Esplora API: Blockstream, mempool.space, your own). |
| `CT_CHAINS` | unset | JSON file of EVM chains and their API keys. Copy `chains.example.json` to `chains.json` (git-ignored), put your key in it, and set `CT_CHAINS=chains.json`. |
| `CT_DELAY` | `0.25` | Seconds between API calls. |
| `CT_SYNC_EVERY` | unset | Sync all wallets every this many minutes while the server runs. |
| `CT_CURRENCY` | `USD` | Currency of the values, e.g. `EUR` (ECB reference rate). |
| `CT_PRICES` | `on` | `off` never asks for prices. |
| `CT_BTC_PAGES`, `CT_EVM_ROWS` | `8`, `1000` | History loaded per address (pages of 25 / rows per list). |

## Privacy

The database says which wallets are yours. Keep it on your machine.

Every address you sync is sent to the indexer that answers for it, so that indexer sees
your wallets together, from one IP address. That is exactly the link this app helps you
avoid. For real privacy point `CT_ESPLORA` and `CT_CHAINS` at indexers you run
(electrs/mempool for Bitcoin, Blockscout for EVM), or at least reach the public ones
through Tor or a VPN.

HD wallets: the xpub or descriptor is stored in the database and never sent anywhere,
and private keys are refused. But each derived address is looked up at the indexer, which
therefore learns that they belong together. With a public indexer, an HD wallet is only
as private as that indexer is discreet.

Prices: only asset identifiers (coin names, token contracts) go to the price API, never
addresses or amounts.

## Data sources

- Bitcoin: any Esplora API. No key.
- EVM: any Etherscan-compatible API. The default is Routescan for Ethereum, without a key.
  Other chains need a free key (Etherscan v2 or Blockscout) or your own Blockscout; public
  Blockscout instances allow only a handful of calls without one.
- Prices: [DefiLlama](https://defillama.com/docs/api) coin prices, no key, cached for
  10 minutes; exchange rate from the ECB via frankfurter.app.
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
| exchange-flow | 0.09 to 0.8 | Money leaves one wallet into an exchange and the same asset, 0 to 2% less, comes out of an exchange into another wallet within 48 hours. Starts at 0.5 (same exchange), 0.3 (two exchanges) or 0.15 (the deposit went to an address that was never expanded); +0.15 within 0.5%, +0.1 within an hour; x0.6 for round amounts; divided by the number of equally plausible matches. |

Several transactions supporting the same guess combine as `1 - (1-c1)(1-c2)...`.

A link between two wallets multiplies the confidences along its path (a transfer counts
1.0) and then by 0.7 for every stranger it passes through. Links chain through your own
wallets: public A tied to private B tied to private C exposes C. Paths never pass through
a labelled service or a very active address: two people using Binance are not linked
on-chain. Such shared services are still listed in the verdict, because the service
itself can link you.

Funds origin is an estimate of the mix, not a coin trace: an address that forwards money
passes on its own sources in proportion to what it received of that asset, ignoring
timing. It stops at a label, at one of your wallets, at an address that was not expanded,
or at the hop limit, and says which.

## Known limits

- Swaps that change the asset (THORChain, BTC to ETH) are not matched: that needs price
  data or the swap service's own records.
- A bridge or exchange flow is only matched when both ends are wallets you declared.
- Mixers are not correlated by amount: their fixed denominations make it meaningless.
- HD wallets are Bitcoin only. A plain xpub is scanned as four address types (legacy,
  wrapped segwit, segwit, taproot), about 160 lookups when empty; a zpub, ypub or
  descriptor scans only its own.
- The value history starts with the first sync; it is not reconstructed from the past.
  Assets without a known price are listed but left out of the totals.
- Solana, Tron and other non-EVM chains are not supported.
- Very active addresses load only their newest history and are flagged in the UI.
- Token balances come from the transfer history unless the indexer has a token-balance
  call (`"tokenlist": true`), so rebasing tokens are off.
- Single addresses are checked by format, not checksum.
- An unlabelled exchange wallet looks like a stranger and can produce a false link;
  label it in the UI and the link disappears.

## Develop

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

three.js 0.187.0 and Cytoscape.js 3.35.0 (both MIT) are vendored unmodified in
`tracker/static/vendor/`, loaded as plain ES modules: no build step, nothing from the internet.
