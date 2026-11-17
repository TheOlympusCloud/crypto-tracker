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
  out. A flat 2D graph is the default on phones and where WebGL is missing. A busy address
  (an exchange, a DEX, a mixer) can be folded from its panel: the counterparties that deal
  with nothing else leave the picture, and its name counts them (+12). The checks and
  verdicts still see everything; the choice is kept in the browser.

- **Fiat values** for the whole portfolio, each wallet and each asset, and a value history
  that grows with every sync.
- **Alerts** when a verdict gets worse: a new link between two wallets, or a stronger one.

- **Lookup of any address**, yours or not: paste, type or scan it in the box at the top of
  every view. See [Look up an address](#look-up-an-address).

A wallet is a single address or an HD wallet: paste an xpub, ypub, zpub or output
descriptor and all its used addresses are found and treated as one wallet.

One web app, installable on a phone as a PWA.

## Screenshots

All taken from a demo instance with invented wallets and addresses.

| | |
|---|---|
| ![A wallet's verdict: linked to a public wallet at 70%, with the evidence and what the check may have missed](docs/screenshots/verdict.webp) | ![3D graph with the path from a private wallet to a public one, hop by hop](docs/screenshots/graph-3d.webp) |
| **Verdict** for one wallet: what it can be tied to, how strongly, and what the check could not cover. | **3D graph**: the path between a private and a public wallet, numbered hop by hop. |
| ![The same path in the flat 2D graph](docs/screenshots/graph-2d.webp) | ![Lookup of an address that is not yours: what it holds and its links to your wallets](docs/screenshots/lookup.webp) |
| **2D graph**: the same path, flat. The default on phones. | **Lookup** of any address: what it holds and how it connects to your wallets. |

<img src="docs/screenshots/mobile.webp" alt="Portfolio totals on a phone, split into private and public" width="280">

**On a phone**: the portfolio, split into private and public.

## Chains

| Chain | Works without a key | How deep it is read |
|---|---|---|
| Bitcoin, Litecoin | yes | Full: history, common-input and change heuristics, counterparties expanded. HD wallets on Bitcoin. |
| Bitcoin Cash | yes | Same heuristics; the 100 newest transactions per address. |
| Dogecoin | yes | Same heuristics; the 50 newest transactions. The free indexer allows about 100 lookups an hour: when that is used up the sync moves on, and the remaining addresses are read on a later sync. |
| Ethereum, Avalanche C | yes | Native coin and ERC-20 transfers, every ERC-20 token held, counterparties expanded. |
| Base, Arbitrum, Optimism, Polygon | **needs `CT_BLOCKSCOUT_KEY`** | Same as Ethereum once the key is set. One free [Blockscout key](https://dev.blockscout.com) covers all four. Arbitrum and Polygon can also use a free `CT_ETHERSCAN_KEY`. |
| BNB Chain | **not read for free** | Etherscan's free plan no longer covers it (nor Base and Optimism). With a paid Etherscan key, add it in a `CT_CHAINS` file (`"bsc": {"url": "https://api.etherscan.io/v2/api", "params": {"chainid": "56"}, "apikey": "…", "symbol": "BNB", "llama": "bsc"}`). |
| Tron | yes | TRX and TRC-20 transfers, the 200 newest; counterparties not expanded automatically. |
| Solana | yes | SOL and SPL tokens (Token-2022 too, e.g. xStocks), the 25 newest transactions, read as net balance changes; counterparties not expanded automatically. |

The depths above are for other addresses (counterparties, lookups). Your own wallets are
read in full on every chain: each sync reads what is new, then continues further back
from where the last one stopped, about 40 pages per source (Solana: 200 transactions).
Between syncs the server keeps reading further back on its own every 5 minutes, until
every wallet is complete. Pressing Sync then waits only for the address being read (for
an EVM address read on several chains, that can take a few minutes).
Until a wallet's history is complete, its panel says "History filling in, back to …" and
its verdict stays "Partly checked".

The same EVM address is read on every EVM chain. Chains that are read less deeply say so
in each wallet's verdict, and chains waiting for a key are listed as such instead of
failing.

## Run

```sh
uv sync
uv run python -m tracker          # http://127.0.0.1:8000
```

Under "Wallets & sync" add your wallets, tick "Also download open label sets" the first time (about 500,000
labels, takes a minute), and press Sync.

### Install on a server

On a Debian 12 or 13 host, from a copy of this repository:

```sh
sudo ./install.sh
```

It installs what is missing (Python, uv, the dependencies), creates the unprivileged
user `crypto-tracker` and a systemd service that starts on boot and restarts on failure,
and listens on port 8000 behind a generated password. The password is printed once, at
the end, with the URL and the commands for logs, stopping and uninstalling.

| Path | What |
|---|---|
| `/opt/crypto-tracker` | Code and dependencies, replaced on every run. |
| `/var/lib/crypto-tracker` | The database: wallets, labels, history. |
| `/etc/crypto-tracker/env` | The password and the variables of the table below, readable by root only. Restart after a change: `systemctl restart crypto-tracker`. |

To update, get the new code (`git pull`) and run the same command again. The database,
the password and the settings are kept.

The phone camera (see [Scanning a QR code](#scanning-a-qr-code)) needs HTTPS, which the
script does not set up. Put a reverse proxy with a certificate in front. With
[Caddy](https://caddyserver.com) and a DNS name that points at the host, this
`/etc/caddy/Caddyfile` is all of it, certificate included:

```
tracker.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Then set `CT_HOST=127.0.0.1` in `/etc/crypto-tracker/env` so that only the proxy reaches
the app, and restart it. Keep the password.

### Docker

With Docker (the image is not exercised by the test suite):

```sh
docker build -t crypto-tracker .
docker run -p 8000:8000 -v tracker-data:/data -e CT_PASSWORD=change-me crypto-tracker
```

| Variable | Default | Meaning |
|---|---|---|
| `CT_HOST`, `CT_PORT` | `127.0.0.1`, `8000` | Listen address. Anything but loopback requires `CT_PASSWORD`; without a password the app only answers to `localhost`/`127.0.0.1`. |
| `CT_PASSWORD` | unset | HTTP Basic password (any user name). Put TLS in front of it. After one good password the browser or app gets a cookie and is not asked again for 400 days; a new password signs every device out. |
| `CT_DB` | `tracker.db` | SQLite file. |
| `CT_ESPLORA` | `https://blockstream.info/api` | Bitcoin indexer (Esplora API: Blockstream, mempool.space, your own). |
| `CT_BLOCKSCOUT_KEY` | unset | Free Blockscout API key (`proapi_…`, from [dev.blockscout.com](https://dev.blockscout.com)); turns on Base, Arbitrum, Optimism and Polygon, token balances included. Set it in the environment, never in a file you commit. |
| `CT_ETHERSCAN_KEY` | unset | Free Etherscan API key; turns on Arbitrum and Polygon, the chains Etherscan's free plan still covers. Not needed with `CT_BLOCKSCOUT_KEY`. |
| `CT_CHAINS` | unset | JSON file that adds EVM chains or points built-in ones at your own indexer, see `chains.example.json`. Keep the real file as `chains.json` (git-ignored). |
| `CT_LITECOIN`, `CT_BITCOINCASH`, `CT_DOGECOIN`, `CT_TRON`, `CT_SOLANA` | public APIs | Base URL of the indexer for that chain (Esplora, Haskoin, BlockCypher, TronGrid, Solana RPC). |
| `CT_DELAY` | `0.25` | Seconds between API calls. |
| `CT_SYNC_EVERY` | unset | Sync all wallets every this many minutes while the server runs. |
| `CT_SWAPS` | `on` | `off` never asks THORChain or Maya about your addresses. `CT_THORCHAIN` and `CT_MAYA` set their Midgard URLs. |
| `CT_CURRENCY` | `USD` | Currency of the values, e.g. `EUR` (ECB reference rate). |
| `CT_PRICES` | `on` | `off` never asks for prices. |
| `CT_LABELS` | `on` | `off` never downloads label sets or asks an explorer for contract names. |
| `CT_BTC_PAGES`, `CT_EVM_ROWS` | `8`, `1000` | History loaded per address (pages of 25 / rows per list). |

## Android

The web app installs from Chrome's menu ("Add to Home screen") when your server has HTTPS
with a certificate the phone trusts. For a server on the LAN over plain http, or with a
self-signed certificate, there is a small Android app instead. It is only a frame around
the same pages: it asks where your server is and shows it.

Install:

1. Copy `crypto-tracker.apk` to the phone (Android 10 or newer) and open it. Android asks
   once to allow installing from that source.
2. Type the server's address, for example `192.168.1.20:8000` or `https://tracker.example`.
   - **Plain http** only works after ticking its box. Nothing is encrypted then: anyone on
     the same network can read your wallets and the password.
   - **Self-signed certificate**: tick its box. On first contact the app shows the
     certificate's SHA-256 fingerprint; accept only if it equals the one on your server
     (`openssl x509 -in cert.pem -noout -fingerprint -sha256`). The app then accepts that
     certificate and no other.
3. Enter the server's password when asked. It is remembered on the phone.

To change the server later, or to forget the password and certificate, long-press the app
icon and choose "Server settings".

A photo taken for the scanner is kept in the app's private storage, never in the gallery,
and nothing the app stores is included in backups or copied to a new phone.

The QR scanner uses the camera live over https (also self-signed, once accepted). Over
plain http a page is not allowed a live camera, so the scanner's "Take a photo" is used.

Build it yourself with the Android SDK and a JDK, no Gradle:

```sh
keytool -genkeypair -keystore release.jks -alias crypto-tracker -keyalg RSA -keysize 3072 -validity 10950
ANDROID_HOME=~/Android/Sdk ANDROID_KEYSTORE=release.jks ANDROID_KEYSTORE_PASS=... android/build.sh
```

Both commands run from the top of the repository. Keystores (`*.jks`, `*.keystore`) are
git-ignored, but the better place for one is outside the checkout. An update only installs
over the old app when it is signed with the same key, so keep it safe. The app has not been run on a real phone yet.

## Look up an address

The box at the top takes an address or a payment URI (`bitcoin:`, `litecoin:`,
`dogecoin:`, `bitcoincash:`, `tron:`, `solana:`, `ethereum:0x...@chainId`); amounts and
other parameters are dropped. The chain follows from the format. Only an EVM address
needs a question, because it exists on every EVM chain: you pick the chains to read,
unless the URI names one.

Each address shows the mark of its chain: in the box as soon as what you type or scan
reads as an address, in the wallet views and lists, and on the nodes of the graph. An EVM
address shows the generic EVM mark until one chain is chosen.

The view opens on a summary for whoever is about to send money there: **risky to send
to**, **check before sending**, **not fully checked** or **no red flags found**, with the
reason, then what it rests on: the chain, how old and how active the address is, its
label, your wallets it dealt with, and the red flags looked for. Those are a label that
flags it (scam, hack or sanctions, from the open label sets or set by you), a mixer, direct
dealings with either, an address that was never used, a new one, and an address that
starts and ends like one of your wallets or an address you paid: the trap of address
poisoning. It never says safe, and says "not fully checked" until the open label sets are
downloaded (on the first sync), as flagged addresses cannot be recognised before.

Then what the address holds per chain, every token included, with values, and who it
deals with: your own wallets first, then its counterparties with their labels, drawn
around it in the graph. Each token carries a badge, its reason one tap away:

- **verified**: the native coin, or a token whose exact contract is on Uniswap's default
  token list (EVM, Solana) or Jupiter's verified list (Solana);
- **fake · worth 0** (a likely scam), unless it has a reliable price: a name that advertises a website or a
  reward; a token that calls itself like a listed one (USDC, also with lookalike letters)
  without being it; or a token that arrived unasked, from strangers (not your wallets, not
  a labelled exchange or service) in transactions the address took no part in, and was
  never moved. These are folded away under the rest;
- **unknown**: everything else. It is the default: a market price alone is no list match.

The same check runs on your own wallets at every sync. A token judged fake, because it is
dressed as a real one (named like a listed token, or a dollar, ETH or BTC token on no list)
or because it arrived unasked from a stranger, is marked **fake · worth 0** wherever your
wallets' tokens show: it never counts in a total, and a fake USDC never joins the real one.

Only tokens with a reliable price get a value, and only their transfers make
counterparties: anyone can airdrop a token to anybody.

A lookup is read-only. The address is fetched live and nothing about it is stored, so it
is not treated as yours and no verdict or alert changes. From the view you can:

- **Track as mine**: adds it to your wallets, private by default, and syncs it.
- **Watch**: keeps it in a list under "Wallets & sync". Still not yours, still in no verdict.

Like a sync, a lookup sends the address to the indexer of its chain.

### Scanning a QR code

The scan button opens the camera and reads the code of a wallet. Browsers only give a
page the camera in a secure context, so the live camera needs one of:

- the app opened as `http://localhost:8000` or `http://127.0.0.1:8000` on the same machine;
- HTTPS: a reverse proxy with a certificate the phone trusts in front of the app (Caddy,
  nginx, Traefik; a Tailscale or Let's Encrypt certificate works, a self-signed one only
  once it is trusted on the phone). Set `CT_PASSWORD`, as for any non-loopback use.

Over plain HTTP the scan button still works through **Take a photo**: the phone's own
camera app takes the picture and the code is read from it. Pasting always works.
Codes are decoded in the browser: by its built-in detector where there is one, otherwise
by the bundled [jsQR](https://github.com/cozmo/jsQR) (Apache-2.0), which is what iOS uses.

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

Swaps: each of your addresses is also looked up at the THORChain and Maya Midgard APIs.
`CT_SWAPS=off` turns that off.

Prices: only asset identifiers (coin names, token contracts) go to the price API, never
addresses or amounts.

Token checks: Uniswap's token list is downloaded whole, so it learns nothing. The Solana
mints a looked-up address holds go to Jupiter's token API, like token contracts to the
price API; `CT_PRICES=off` spares it too.

## Data sources

- Bitcoin and Litecoin: any Esplora API (blockstream.info, litecoinspace.org). No key.
- Bitcoin Cash: Haskoin. Dogecoin: BlockCypher. Tron: TronGrid. Solana: the public RPC.
  All without a key, all rate-limited.
- EVM: Routescan for Ethereum and Avalanche without a key; Etherscan v2 for the other
  built-in chains with `CT_ETHERSCAN_KEY`; or any Etherscan-compatible API through
  `CT_CHAINS`. Public Blockscout instances allow only a handful of calls without a key.
- Prices: [DefiLlama](https://defillama.com/docs/api) coin prices, no key, cached for
  10 minutes; exchange rate from the ECB via frankfurter.app.
- Tokens: Routescan lists every ERC-20 an address holds; [Uniswap's default token
  list](https://tokens.uniswap.org) and [Jupiter's token API](https://dev.jup.ag/docs/token-api)
  (names and verified flag of Solana tokens), no key, cached for a day.
- Labels: see [Who is on the other side](#who-is-on-the-other-side).

## Who is on the other side

Every counterparty the app can name gets a service name and a type (exchange, DEX, DEX
aggregator, bridge, mixer, lending, NFT marketplace, payment processor, contract...),
shown wherever the address appears, together with where the name comes from:

| Source | What it is | Trust |
|---|---|---|
| you | A label set by hand in the app. | Always wins. |
| the protocol | THORChain and Maya vaults, from the protocols' own swap records. | Fact. |
| an open label set | [etherscan-labels](https://github.com/brianleect/etherscan-labels) for Ethereum, BNB Chain, Polygon, Arbitrum, Optimism, Avalanche and Fantom (a 2023 snapshot), [GraphSense TagPacks](https://github.com/graphsense/graphsense-tagpacks) for Bitcoin, Ethereum, Litecoin and Tron, and the program names of the [Solana explorer](https://github.com/solana-labs/explorer). All MIT. | Fact. |
| the explorer | The verified contract name of an EVM address ("UniswapV2Router02"). The name is a fact; the type read from it is a guess. | Guess, 80%. |
| behaviour | An address that forwards everything to an exchange (a deposit address); one that takes one asset from you and returns another in the same transaction (a pool or router); one with too much history to read (some service). | Guess, 60%. |

The label sets are downloaded whole (about 540,000 labels) on the first sync and again
every 30 days, and looked up locally, so naming an address tells nobody which addresses
you care about. Contract names are asked from the explorer that already served your
wallets' histories, 15 addresses per sync, each only once. `CT_LABELS=off` turns both off.

Phishing, hack, scam and sanctions labels in those sets get the type "scam, hack or
sanctions" and the **flagged** category: the check before sending calls such an address
risky. A database labelled before that keeps them as plain labels until the sets are
downloaded again.

A known service ends a trace: two people using Binance are not linked. A guessed one
does not end it, it weakens it by how sure the guess is, because a wrong guess must not
hide a link. A guess is always shown as "(guess)", and disappears again when the
behaviour it was read from is no longer in the data.

## What is a fact and what is a guess

Solid arrows are transfers read from the chain. Dashed lines are guesses that two
addresses share an owner; each carries a confidence, shown on the line. The numbers live
in `tracker/heuristics.py` and `tracker/graph.py`.

| Guess | Confidence | Rule |
|---|---|---|
| common-input | 0.9 | Two addresses are inputs of one Bitcoin transaction. |
| common-input, CoinJoin | 0.2 | Same, but the transaction has 3+ inputs and 3+ equal outputs. |
| coinjoin | 0.2 | A transfer inside such a transaction. Its amount is still a fact, but a CoinJoin exists so that nobody can tell which input paid which output: it is never read as a certain link, and never raises an alert. |
| change | 0.5 to 0.75 | In a 2-output payment, the output that matches the input script type (0.6), or is the non-round one (0.5); 0.75 when both rules agree, nothing when they disagree. |
| bridge | 0.5 to 0.95 | Money leaves one of your wallets on chain X and the same asset reaches another of your wallets on chain Y within 6 hours, 0 to 3% smaller. +0.25 if a labelled bridge is involved, +0.15 if within 0.5%, +0.1 if within 10 minutes; divided by the number of equally plausible matches. |
| exchange-flow | 0.09 to 0.8 | Money leaves one wallet into an exchange and the same asset, 0 to 2% less, comes out of an exchange into another wallet within 48 hours. Starts at 0.5 (same exchange), 0.3 (two exchanges) or 0.15 (the deposit went to an address that was never expanded); +0.15 within 0.5%, +0.1 within an hour; x0.6 for round amounts; divided by the number of equally plausible matches. Repeated matches between the same two wallets are not independent proof: they lift the best single match at most halfway to 0.8, never past it. |
| swap | 0.95 | THORChain or Maya Protocol recorded a swap from one address to the other (for example ETH in, BTC out). The record is public and names the destination, so this is close to a fact. Several swaps between the same two addresses stay at 0.95. |

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

- Swaps that change the asset are matched only through THORChain and Maya Protocol,
  whose records are public. Other swap services and instant exchangers are not.
- A bridge or exchange flow is only matched when both ends are wallets you declared.
- Mixers are not correlated by amount: their fixed denominations make it meaningless.
- HD wallets are Bitcoin only (no Ltub, dgub or EVM account keys yet). A plain xpub is scanned as four address types (legacy,
  wrapped segwit, segwit, taproot), about 160 lookups when empty; a zpub, ypub or
  descriptor scans only its own.
- The value history starts with the first sync; it is not reconstructed from the past.
  Assets without a known price are listed but left out of the totals.
- XRP, TON, Cardano, Cosmos and Monero are not supported.
- Litecoin's old script addresses starting with 3 are read as Bitcoin; use the M form.
- On Solana a token's symbol comes from Jupiter or the price source; tokens neither knows
  show as the first characters of their mint address.
- The token badge knows only the lists above and its own rules: a scam token can still be
  "unknown", and a lookup with an incomplete history never calls a token unasked.
- A token that only borrows a known name (a fake USDC) is listed apart, unpriced.
- Blockscout has not traced every internal transaction (contract-to-wallet payments) of
  some chains yet, seen on Optimism. A sync shows those it has; the full history of your own
  wallets stays "filling in" (with a sync error naming it) until Blockscout has traced them.
- Very active addresses that are not yours load only their newest history and are flagged
  in the UI. A very large wallet of yours can take several syncs to read in full.
- A transaction with more than 50 counterparties (a CoinJoin, an exchange batch) keeps
  its 50 largest and every wallet of yours; the wallet's verdict says how many were cut,
  and amounts for those transactions are lower than on a block explorer.
- If THORChain or Maya does not answer during a sync, every wallet reads "Partly checked"
  until it answers again (or until `CT_SWAPS=off`): a swap may have gone unseen.
- Alerts are raised for links of 50% confidence or more; weaker guesses are listed in
  the verdict only.
- Token balances come from the transfer history unless the indexer has a token-balance
  call (`"tokenlist": true`), so rebasing tokens are off.
- Address checksums are verified, except the mixed-case form of EVM addresses and
  Bitcoin Cash's cashaddr.
- An unlabelled exchange wallet looks like a stranger and can produce a false link;
  label it in the UI and the link disappears.

## Develop

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

three.js 0.187.0 and Cytoscape.js 3.35.0 (both MIT) are vendored unmodified in
`tracker/static/vendor/`, loaded as plain ES modules: no build step, nothing from the internet.
