# Crypto tracker

One self-hosted app built on [am-i-exposed](https://github.com/Copexit/am-i-exposed), the
Bitcoin privacy scanner, with a wallet tracker inside it. am-i-exposed brings the depth on
one Bitcoin address, transaction, xpub or PSBT: a privacy score, Boltzmann entropy, CoinJoin
detection, an entity index of 30M addresses and a transaction graph explorer. The tracker,
**Wallets** in its menu (`/tracker/`), brings the breadth: your own wallets on many chains,
kept and synced by a server behind the app, and what ties them together. One
`docker compose up -d` starts all of it; see [Install](#install).

Self-hosted view of everything you hold, and of what ties your wallets together. You
list the wallets that are yours and mark as **public** the ones tied to your identity
(withdrawals from a KYC exchange, addresses you gave to people). The app then shows:

- **Portfolio**: every asset across all wallets and chains, split into private and public.
- **A verdict per wallet**: either the other wallets of yours it can be tied to, directly
  or through strangers, bridges, common inputs or a chain of your own wallets, with hop
  count and confidence; or "no link found", next to the list of what that check cannot
  rule out. It never says "safe".
- **What an observer sees**: which of your wallets look like one person, across chains,
  the public wallets among them and the links holding each group together, weakest first.
  See [Groups of wallets](#groups-of-wallets).
- **A privacy grade per wallet**, across all its chains: am-i-exposed's score and grade,
  with the findings behind it by chain. See
  [Privacy grade of your wallets](#privacy-grade-of-your-wallets).
- **Funds origin**: where each wallet's money came from, followed back to a labelled
  source (exchange, bridge, mixer...), one of your wallets, or the edge of what is known.
  Through a THORChain or Maya swap, or a bridge record, it carries on on the other chain:
  Solana money bridged from Ethereum is traced to the exchange that funded the Ethereum side.
- **A 3D graph** of your wallets, their counterparties (labelled by type) and the links
  between them. Any link or origin opens as a highlighted path with each hop spelled
  out. A flat 2D graph is the default on phones and where WebGL is missing. A busy address
  (an exchange, a DEX, a mixer) can be folded from its panel: the counterparties that deal
  with nothing else leave the picture, and its name counts them (+12). The checks and
  verdicts still see everything; the choice is kept in the browser.

- **Fiat values** for the whole portfolio, each wallet and each asset, and a value history
  that grows with every sync.
- **Alerts** when a verdict gets worse: a new link between two wallets, a stronger one, or
  a wallet's privacy grade that drops.

- **Lookup of any address**, yours or not: paste, type or scan it in the box at the top of
  every view. See [Look up an address](#look-up-an-address).
- **Privacy score of any address** on every chain the scan covers: am-i-exposed's for
  Bitcoin, Litecoin, Dogecoin and Bitcoin Cash, the tracker's for EVM, Solana and Tron. See
  [Privacy scan of EVM, Solana and Tron addresses](#privacy-scan-of-evm-solana-and-tron-addresses)
  and [Litecoin, Dogecoin and Bitcoin Cash](#litecoin-dogecoin-and-bitcoin-cash).

A wallet is a single address or an HD wallet: paste an xpub, ypub, zpub or output
descriptor and all its used addresses are found and treated as one wallet.

One web app, installable on a phone as a PWA, in the six languages of am-i-exposed.

## Screenshots

All taken from a demo instance: invented wallet names on public addresses, inside am-i-exposed's
own menu and look.

| | |
|---|---|
| ![A wallet's verdict: linked to a public wallet at 70%, with the evidence and what the check may have missed](docs/screenshots/verdict.webp) | ![3D graph with the path from a private wallet to a public one, hop by hop](docs/screenshots/graph-3d.webp) |
| **Verdict** for one wallet: what it can be tied to, how strongly, and what the check could not cover. | **3D graph**: the path between a private and a public wallet, numbered hop by hop. |
| ![The same path in the flat 2D graph](docs/screenshots/graph-2d.webp) | ![Lookup of an address that is not yours: what it holds and its links to your wallets](docs/screenshots/lookup.webp) |
| **2D graph**: the same path, flat. The default on phones. | **Lookup** of any address, read live: what it holds and how it connects to your wallets. |

<img src="docs/screenshots/mobile.webp" alt="Portfolio totals on a phone, split into private and public" width="280">

**On a phone**: the portfolio, split into private and public.

## Chains

| Chain | Works without a key | How deep it is read |
|---|---|---|
| Bitcoin, Litecoin | yes | Full: history, common-input and change heuristics, counterparties expanded. HD wallets on Bitcoin. |
| Bitcoin Cash | yes | Same heuristics; the 100 newest transactions per address. |
| Dogecoin | yes | Same heuristics; the 50 newest transactions. The free indexer allows about 100 lookups an hour: when that is used up the sync moves on, and the remaining addresses are read on a later sync. |
| Ethereum, Avalanche C | yes | Native coin and ERC-20 transfers, every ERC-20 token held, counterparties expanded. Your own wallets' NFT transfers too (ERC-721, ERC-1155): see [NFTs](#what-is-a-fact-and-what-is-a-guess). |
| Base, Arbitrum, Optimism, Polygon | **needs `CT_BLOCKSCOUT_KEY`** | Same as Ethereum once the key is set. One free [Blockscout key](https://dev.blockscout.com) covers all four. Arbitrum and Polygon can also use a free `CT_ETHERSCAN_KEY`. |
| BNB Chain | **needs `CT_NODEREAL_KEY`** | Read through [NodeReal](https://nodereal.io)'s MegaNode with a free key: BNB, internal and BEP-20 transfers, the balance and the tokens held; no NFTs. Addresses that are not yours: about the last day only. Etherscan's free plan no longer covers BNB Chain; a paid Etherscan key can be set instead in a `CT_CHAINS` file (`"bsc": {"url": "https://api.etherscan.io/v2/api", "params": {"chainid": "56"}, "apikey": "…", "symbol": "BNB", "llama": "bsc"}`). |
| Tron | yes | TRX and TRC-20 transfers, the 200 newest; energy and bandwidth delegations linked; counterparties not expanded automatically, except [shared ones](#shared-counterparties). |
| Solana | yes | SOL and SPL tokens (Token-2022 too, e.g. xStocks), the 25 newest transactions, read as net balance changes; fee payers and co-signers linked; the rent of a closed token account counts as sent by its owner; counterparties not expanded automatically, except [shared ones](#shared-counterparties). |

The depths above are for other addresses (counterparties, lookups). Your own wallets are
read in full on every chain: each sync reads what is new, then continues further back
from where the last one stopped, about 40 pages per source (Solana: 200 transactions; BNB
Chain: 20 windows of 100,000 blocks, about a day each). On BNB Chain the whole history of one
EVM address of yours, used there or not, takes several hours and about 700,000 of NodeReal's
10 million free monthly units, once; after that each sync of it costs about 1,000 units
(with `CT_SYNC_EVERY=60`, about 720,000 a month per address).
Between syncs the server keeps reading further back on its own every 5 minutes, until
every wallet is complete. Pressing Sync then waits only for the address being read (for
an EVM address read on several chains, that can take a few minutes).
Until a wallet's history is complete, its panel says "History filling in, back to …" and
its verdict stays "Partly checked".

The same EVM address is read on every EVM chain. Chains that are read less deeply say so
in each wallet's verdict, and chains waiting for a key are listed as such instead of
failing.

OFAC's sanctions lists flag addresses on every chain above; Tether's and Circle's stablecoin
freezes are read on Ethereum and Tron (see [Who is on the other side](#who-is-on-the-other-side)).

Bridges: when one of your wallets pays into Circle's CCTP, Across, Wormhole's Token Bridge,
the OP Stack bridges into Base and Optimism or Arbitrum's gateway router, the bridge's own
event in that transaction's receipt names who gets the money on the other chain: any chain
above that the bridge reaches, Solana included (CCTP and Wormhole). That recipient is linked
to the wallet as a `bridge-record` ([below](#what-is-a-fact-and-what-is-a-guess)), whether
you declared it or not. A wallet of yours that received through such a record has its funds
origin followed on the chain it was bridged from.

Mixers: when one of your wallets deposits into a Tornado Cash pool (on Ethereum, BNB Chain,
Polygon, Arbitrum, Optimism or Avalanche) and another withdraws from the same pool, the
deposits made into that pool around the two are counted. Few of them hide little: the two
wallets get a `mixer` link of at most 30% ([below](#what-is-a-fact-and-what-is-a-guess)).
BNB Chain read through NodeReal is left out: NodeReal does not serve the pool's logs.
Tornado's pools and routers end every path, as a labelled service does, even where no label
set names them.

The privacy scan reads an address as deeply as a lookup does (the depths above), so a scan
of a busy Solana or Tron address says "Partly checked". Its first funder comes from that read
when it holds the whole history, else from one more call per chain that lists the address's
oldest transactions; it is not found on BNB Chain through NodeReal, nor for a Solana address
with more transactions than a lookup reads (25), since no single call reaches back that far
there. Your own wallets are answered from their stored history once it is complete. The
[privacy grade of your wallets](#privacy-grade-of-your-wallets) covers every chain above,
from the full history synced.

Names: an EVM address is looked up in ENS (on Ethereum) and Basenames (on Base), a Solana
address in SNS (.sol). Base needs no key for this: names are asked of Base's own contracts,
not of its indexer. Bitcoin Cash addresses that a name lists are not read yet.

Approvals: on every EVM chain above except BNB Chain through NodeReal, which has no log
search, each sync reads the token approvals your EVM wallets gave. The first time it starts
at the wallet's first transaction on that chain, in block windows the indexer can answer
(Routescan: about a million blocks on Ethereum, so an old wallet takes a few minutes); after
that only the blocks since, page after page when a window holds more than 1,000. A sync
makes at most 40 such calls, 20 for one address; the rest waits for the next. Each chain is
read on its own: one whose indexer does not answer holds up no other.

### Shared counterparties

Each sync also reads up to 10 addresses that two or more of your wallets dealt with and
that were never read (with no label, or named only by OKX's list), busiest first: in full, as a lookup reads them (Tron: three TronGrid
calls), or on Solana with one call that only tells whether the address is busy (1,000
transactions or more). Dogecoin's are left out: its free indexer allows too few lookups.
The others wait for the next sync. A busy address (an exchange's hot wallet, a payment
processor) then ends every link through it, as a labelled service does. A quiet one that
sends everything to one exchange is that exchange's deposit address, and two of your
wallets that paid it get a `deposit-address` link (see
[What is a fact and what is a guess](#what-is-a-fact-and-what-is-a-guess)).

## Install

### With Docker Compose

From a fresh clone, one command builds and starts everything: the web app, the tracker's
server behind it, and the database in a Compose volume.

```sh
git clone https://github.com/TheOlympusCloud/crypto-tracker && cd crypto-tracker
docker compose up -d                  # http://<host>:8000/, the first build takes minutes
docker compose logs | grep password   # made on the first start, kept in the volume
```

The build needs about 2.5 GB of memory and 2 cores: give a container or VM at least 3 GB.
It runs 2 build workers, whatever the number of CPUs the host shows; on a machine with memory
to spare, `NEXT_BUILD_CPUS=8` in `.env` makes it faster (each worker takes about 115 MB).

Any user name and that password open it; `docker compose exec crypto-tracker cat
/data/password` shows the password again. The generated password also stays in the
container's log (`docker compose logs`) for as long as the container exists; to keep it out
of any log, put your own `CT_PASSWORD` in `.env` before the first start. Settings of the table below go in an optional
`.env` next to `compose.yaml` (`CT_PASSWORD` there replaces the generated one), then
`docker compose up -d` again. To update: `git pull && docker compose up -d --build`. Your
wallets, labels and history stay in the volume `data`.

### On Proxmox

On the Proxmox VE host, as root:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/TheOlympusCloud/crypto-tracker/main/proxmox/install.sh)"
```

It creates an unprivileged Debian 13 container (next free id, 2 cores, 4 GB, 16 GB disk,
DHCP on `vmbr0`, started with the host), installs Docker from Debian's own packages, clones
this repository into `/opt/crypto-tracker` and runs `docker compose up -d` there, exactly as
above. The first build takes several minutes. At the end it prints the address, the
password and the commands for logs, settings, updates and removal. If a step fails after
the container exists, it prints the command that removes it before a retry.

Change any default by setting it before the command, for example a static address:

```sh
LXC_IP=192.168.1.50/24,gw=192.168.1.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/TheOlympusCloud/crypto-tracker/main/proxmox/install.sh)"
```

| Variable | Default |
|---|---|
| `LXC_ID` | the next free id |
| `LXC_HOSTNAME` | `crypto-tracker` |
| `LXC_STORAGE` | the first storage that holds containers (`local-lvm`, `local-zfs`...) |
| `LXC_TEMPLATE_STORAGE` | `local` |
| `LXC_BRIDGE`, `LXC_IP` | `vmbr0`, `dhcp` |
| `LXC_DISK`, `LXC_MEMORY`, `LXC_CORES` | `16` GB, `4096` MB (at least `3072`), `2` (at least `2`); with `MEMPOOL=1`: `8192` MB, `4` |
| `MEMPOOL`, `MEMPOOL_DISK` | `0` (`1`: a Bitcoin node of your own), `1200` GB on top of `LXC_DISK` (at least `1100`) |
| `REPO_URL`, `REPO_REF` | this repository, `main` (a branch or a tag) |

With a [Bitcoin node of its own](#a-bitcoin-node-of-your-own) too:

```sh
MEMPOOL=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/TheOlympusCloud/crypto-tracker/main/proxmox/install.sh)"
```

That adds `MEMPOOL_DISK` (1200 GB) to the container's disk and gives it 8 GB and 4 cores, and
puts `COMPOSE_PROFILES=mempool` in its `.env`. It refuses, before making anything, when the
storage has less free space than the whole disk, or when `MEMPOOL_DISK` is under 1100 GB: the
node and its index take about 950 GB today and grow about 90 GB a year (see [Sizing](#sizing)). Thin storage (`local-lvm`,
ZFS) is counted by what it really has free, since the node really fills its disk.

Back the container up with Proxmox's own backups (`vzdump`): the volume is inside it. With a
Bitcoin node, leave its volumes out or back up only the app's: the node can sync again.

### A Bitcoin node of your own

Every Bitcoin address the app looks up goes to an indexer: blockstream.info for the wallet
tracker, mempool.space for the privacy scanner. They see your addresses together, from your IP
address. Instead the app can run a Bitcoin node next to it and read Bitcoin only from there:

```sh
echo COMPOSE_PROFILES=mempool >> .env   # next to compose.yaml
docker compose up -d
```

This starts Compose's `mempool` profile next to the app, every image pinned by version and
digest:

| Service | Image | What it is |
|---|---|---|
| `bitcoind` | `bitcoin/bitcoin` 31.3 | Bitcoin Core with its transaction index, no wallet. Its RPC is not published; the others sign in with the cookie it writes at each start. |
| `fulcrum` | `cculianu/fulcrum` v2.1.4 | The address index: which transactions touch an address. It talks to no other Electrum server. |
| `mempool-api`, `mempool-web`, `mempool-db` | `mempool/backend` and `mempool/frontend` v3.3.2, `mariadb` 10.11.19 | mempool's API on both, and its own site: a block explorer on port 8080 (`MEMPOOL_PORT`). |

`COMPOSE_PROFILES` in `.env` keeps the node in every later `docker compose up -d`, updates
included, and it is how the app learns that the node is there: `docker compose --profile
mempool up -d` would start the node without the app using it.

From then on:

- the **wallet tracker** reads Bitcoin from your node (`CT_ESPLORA` defaults to it). Its Chains
  list, under Wallets & sync, says "Read from your own node", and Bitcoin links open your own
  explorer instead of mempool.space. Your Bitcoin addresses are no longer sent to the THORChain
  and Maya swap APIs either: a swap between a Bitcoin wallet and another wallet of yours is still
  found from that other wallet's side, and each Bitcoin wallet's verdict says so;
- the **privacy scanner** (am-i-exposed) asks this server's `/api/`, which passes its requests on
  to your node: its badge at the top right says **Local**, as on Umbrel. Its other services (the
  Chainalysis check, the observatory) are reached as before, and it does not claim they go
  through Tor.

**Why Fulcrum.** mempool reads addresses from an Electrum server: electrs or Fulcrum. Fulcrum
has an official image from its author, and answers addresses with long histories quickly. That
matters here: a sync expands counterparties, which are often exchanges and other busy addresses,
where electrs is slow or gives up. The price is disk: about 150 GB for Fulcrum against about
45 GB for electrs, on a total around a terabyte.

#### Sizing

As of late 2026:

| Part | Disk |
|---|---|
| Bitcoin Core: blocks and chain state | about 710 GB |
| its transaction index | about 66 GB |
| Fulcrum's index | 130 to 180 GB |
| mempool's database, images and logs | about 15 GB |
| **The node** | **about 950 GB**, growing about 90 GB a year |
| The app | 16 GB |

Use an SSD: on a hard disk the first sync takes many days (Fulcrum's author says so of his own).
Memory: 8 GB for all of it together, the app's build included; 4 cores. The first sync downloads
the whole chain, about 700 GB: bitcoind takes from a day to several, depending on disk, CPU and
bandwidth, then Fulcrum builds its index, from 4 to over 20 hours.

#### While it syncs

Until the node has the whole chain and Fulcrum its index, the app reads Bitcoin from nowhere.
It does not stand in a public server meanwhile: that server would see your addresses exactly
when you set up a node so that none does, and what it has seen cannot be taken back. The rest
keeps working: other chains sync, your Bitcoin wallets keep what was read before, and their
history fills in once the node is ready.

The app says where the node stands, read from the node itself every 30 seconds (a newest block
more than 6 hours old: still syncing; an address the index cannot answer: index still building):

- the tracker, in its status line on every view: "Bitcoin: your own node is still syncing; it has
  the chain up to 19 Mar 2021 (block 676,000). Bitcoin addresses are read once it is ready, never
  from a public server meanwhile." Then "its address index (Fulcrum) is still being built".
  Each Bitcoin wallet's sync error says the same;
- the scanner, in its "Mempool Unreachable" window, shown over every page until dismissed:
  wait for it to finish syncing, then reload. Its badge shows a warning. It does not fall back
  to mempool.space either.

To have answers before the end of the sync anyway, knowing who sees them: set `CT_ESPLORA` (the
tracker) or a custom API in the scanner's settings to a public server, and remove it once your
node is ready.

#### A node you already run

For a mempool already running on your network (Umbrel, Start9, RaspiBlitz, myNode...), leave the
profile off and give its address, as your browser opens it, in `.env`:

```sh
CT_MEMPOOL=http://umbrel.local:3006
```

The tracker and the scanner then use it as above, waiting the same way while it syncs, and
Bitcoin links open it. Anything serving the Esplora API under `/api/` will do (mempool, Esplora).
Bitcoin Core alone will not: it has no address index.

#### Check it in your browser

1. Open the app, then the browser's developer tools (F12), tab **Network**. Tick "Preserve log"
   (Firefox: "Persist Logs") and reload the page.
2. Scan a Bitcoin address in the scanner.
3. Type `mempool.space` in the filter of the Network tab: nothing. Type `/api/`: the requests go
   to the app's own address (`/api/local-info`, `/api/blocks/tip/height`, `/api/address/...`),
   and `/api/local-info` answers `"torProxy": false`.
4. The badge at the top right says **Local**.

The tracker's requests leave from the server, not from the browser: its Chains list says "Read
from your own node".

### HTTPS

The phone camera (see [Scanning a QR code](#scanning-a-qr-code)) needs HTTPS, and the
password crosses the network unencrypted without it. Put a reverse proxy with a
certificate in front. With [Caddy](https://caddyserver.com) and a DNS name that points at
the host, this `Caddyfile` is all of it, certificate included:

```
tracker.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Then publish the port on loopback only (`"127.0.0.1:8000:8000"` in `compose.yaml`) so that
only the proxy reaches the app.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `CT_HOST`, `CT_PORT` | `127.0.0.1`, `8000` | Listen address. Anything but loopback requires `CT_PASSWORD`; without a password the app only answers to `localhost`/`127.0.0.1`. |
| `CT_PASSWORD` | unset | HTTP Basic password (any user name). Put TLS in front of it. After one good password the browser or app gets a cookie and is not asked again for 400 days; a new password signs every device out. |
| `CT_DB` | `tracker.db` | SQLite file. |
| `CT_WEB` | unset (the image: its build) | Directory of the web app's static build, served on every path but `/api/tracker/`. Unset: only the API answers. |
| `CT_PASSWORD_FILE` | unset (the image: `/data/password`) | Without `CT_PASSWORD`, the password kept in this file, made and printed on the first start. |
| `CT_ESPLORA` | your own node when there is one, else `https://blockstream.info/api` | Bitcoin indexer of the tracker (Esplora API: Blockstream, mempool.space, your own). |
| `COMPOSE_PROFILES` | unset | `mempool`: run [a Bitcoin node of your own](#a-bitcoin-node-of-your-own) next to the app. |
| `CT_MEMPOOL` | unset; with the `mempool` profile, its own | Address of a mempool of your own, as your browser opens it, e.g. `http://umbrel.local:3006`: the tracker and the scanner read Bitcoin from it. |
| `MEMPOOL_PORT` | `8080` | Port of the `mempool` profile's own site, a block explorer. |
| `CT_BLOCKSCOUT_KEY` | unset | Free Blockscout API key (`proapi_…`, from [dev.blockscout.com](https://dev.blockscout.com)); turns on Base, Arbitrum, Optimism and Polygon, token balances included. Set it in the environment, never in a file you commit. |
| `CT_NODEREAL_KEY` | unset | Free NodeReal API key (from [nodereal.io](https://nodereal.io), MegaNode); turns on BNB Chain. Calls are paced to the free plan's limit, about one every two seconds. |
| `CT_ETHERSCAN_KEY` | unset | Free Etherscan API key; turns on Arbitrum and Polygon, the chains Etherscan's free plan still covers. Not needed with `CT_BLOCKSCOUT_KEY`. |
| `CT_CHAINS` | unset | JSON file that adds EVM chains or points built-in ones at your own indexer, see `chains.example.json`. Keep the real file as `chains.json` (git-ignored). `"rpc"`: your own node's JSON-RPC URL for that chain, used for name lookups (`ethereum`, `base`) and, on any chain, to read its bridge receipts. |
| `CT_LITECOIN`, `CT_BITCOINCASH`, `CT_DOGECOIN`, `CT_TRON`, `CT_SOLANA` | public APIs | Base URL of the indexer for that chain (Esplora, Haskoin, BlockCypher, TronGrid, Solana RPC). |
| `CT_DELAY` | `0.25` | Seconds between API calls. |
| `CT_SYNC_EVERY` | unset | Sync all wallets every this many minutes while the server runs. |
| `CT_SWAPS` | `on` | `off` never asks THORChain or Maya about your addresses. `CT_THORCHAIN` and `CT_MAYA` set their Midgard URLs. Bridge records come from your indexers and stay on. |
| `CT_CURRENCY` | `USD` | Currency of the values, e.g. `EUR` (ECB reference rate). |
| `CT_PRICES` | `on` | `off` never asks for prices. |
| `CT_LABELS` | `on` | `off` never downloads label sets (sanctions lists and stablecoin freezes included), asks an explorer for contract names or looks up names (ENS, Basenames, SNS). |
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
   Add `/tracker/` to open on your wallets rather than on the privacy scanner, e.g.
   `192.168.1.20:8000/tracker/`.
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
flags it (scam, hack, on OFAC's sanctions lists or frozen by Tether or Circle, from the open
label sets or set by you), a mixer, direct
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

Where the page gets no live camera (plain HTTP, a refused permission), the scanner offers
**Take a photo of the QR** instead: the phone's own camera app takes the picture and the
code is read from it. Pasting always works. This is am-i-exposed's own scanner: codes are
decoded in the browser by [zxing-wasm](https://github.com/Sec-ant/zxing-wasm), served by
the app itself.

## Privacy scan of EVM, Solana and Tron addresses

The scanner's box (the home page) takes an EVM, Solana or Tron address as well as a Bitcoin
one, and so does **Privacy score** in the tracker's panels. A Bitcoin address goes to
am-i-exposed's own scan, in the browser. Any of the others opens the tracker's scan
(`/tracker/scan/`), which the server makes: the same score, grade, findings list and score
breakdown as am-i-exposed's, so a score means the same on every chain. An EVM address first
asks which chains to read, as a lookup does. Litecoin, Dogecoin and Bitcoin Cash go to
am-i-exposed's scan with their own profile (below).

| Finding | Impact | What it rests on |
|---|---|---|
| This address is flagged | -20, critical | The address itself is labelled as a scam or a hack, is on OFAC's sanctions lists, or Tether or Circle froze its USDT or USDC. |
| Dealt directly with an exchange | -15, high | Transfers to or from an address an open label set or you label as an exchange. |
| Dealt with a flagged address | -15, high | Transfers with an address labelled as a scam, a hack, under sanctions or frozen by Tether or Circle. |
| Dealt with a mixer | -10, medium | Transfers with a labelled mixer. |
| The same address on several chains | -2, low | Transfers of the same EVM address on two or more of the chains read. |
| Sends at hours that point to a time zone | -2, low | An estimate: 20 or more transactions the address sent (on EVM, only those it signed), of which eight hours of the day hold at most a tenth. That quiet stretch is taken as the night, midnight to 8:00 local time, which gives a time zone give or take two hours. Two quiet stretches apart (busy at noon and at midnight) give none. Never a link. |
| First funded by your wallet | -10, medium | One of your wallets sent the address its first coins on a chain (on Tron: the TRX that activated it). |
| First funded by the same address as your other wallets | -10, medium | A stranger sent the address, one of your wallets, its first coins and also sent coins to another of your wallets; it was read and is not busy. |
| First funded by an exchange | 0, low | An address an open label set or you label as an exchange sent it its first coins. It costs nothing more: the withdrawal is one of the transfers "Dealt directly with an exchange" counts. |
| First funded by a busy address | 0, low | As two rows up, but the stranger turned out busy or a known service, so it is stored as a very active address and links nobody. |
| Has a public name | -15, high | The address's primary name: ENS, Basenames or SNS (.sol). Counted only when the name points back at the address, since anybody can point their own reverse record at any name. |
| Its name lists addresses on other chains | -5, medium | The addresses of Bitcoin, Litecoin, Dogecoin, Tron, Solana or other EVM chains that the name publishes (ENSIP-9). |
| Shares an exchange deposit address | -10, high | One of your own wallets only: it and another wallet of yours paid the same exchange deposit address (the `deposit-address` guess, 85%). |
| The mixer hides little | -5, medium | One of your own wallets only: it deposited into a Tornado Cash pool and another wallet of yours withdrew from it, or the reverse, with fewer than 20 other deposits per deposit of the wallet in between (the `mixer` guess, 30% at most). |

The score is am-i-exposed's address score: 93, plus the impacts, kept within 0 to 100;
A+ from 90, B from 75, C from 50, D from 25, F below (`tracker/exposure/`, tested against
`score.ts` on one shared set of cases). The impacts are a first proposal, to be tuned on
real wallets. Each finding lists the transactions it rests on, with links to the chain's
explorer (a name rests on no transaction, so it lists none). Only labels from a source that
states them count: a guessed label (behaviour, a contract name) makes no finding, and a
token a stranger airdropped makes no counterparty.
The two guesses among the findings, a shared deposit address and a mixer that hides little,
say they are guesses and how sure; the one estimate, activity hours, says how many
transactions it rests on.
No finding is no clean bill: only labelled addresses can be recognised.

The first funder is the sender of the earliest native coin the address received on each
chain: gas on EVM, the transfer that activated a Tron account (read as its first TRX, so an
account activated by a TRC-10 token or an explicit account creation may name no activator, or
a later sender). On EVM, when the scan's read was cut on any chain (a very active address) or
a chain did not answer, the funder comes from one call per chain that lists the address's
oldest transactions, without internal transfers (contract payouts, bridge fills). If the
address sent one of them before any coin reached it, its gas came by such a transfer and no
funder is named. If a normal transfer reached it first, its sender is named, even when an
internal transfer brought the very first coins: the named funder can then be a later one.

A scan says **Partly checked**, and why, when the address has more history than is read,
an indexer did not answer, or a check ran out of calls (each scan may make 60 calls beyond
the address's own read).

Like a lookup, a scan stores nothing about the address. Its read is kept in memory for 10
minutes (200 reads at most), so a scan right after a lookup of the same address costs
nothing; a lookup always reads anew. The one exception is the scan of a wallet of yours whose
first funder is a stranger who also funded another of your wallets: that stranger is read
first (one call on Solana and Tron, a sync elsewhere), and stored as a very active address
when it is one; a quiet one is noted as such, so it is read once.

### Privacy grade of your wallets

Each wallet of yours gets one score and grade, on every chain, on its card in the
portfolio, in its panel and in its node's panel in the graph. The server makes it from what
syncs stored, so opening the page makes no call, every device shows the same grade, and an
alert is raised when the grade drops, naming the grade the page shows. It is the scan's
score (93 plus the impacts, the same grades) over:

| Finding | Impact | What it rests on |
|---|---|---|
| Tied to your public wallet | -70 x the link's confidence (-49 at 70%); critical from 50% | The strongest link of the wallet's verdict to a wallet you marked public (`tracker/exposure/wallets.py`). A public wallet does not get it: it is tied to you anyway. |
| Reused addresses | -10 per address, at most -40; medium | A Bitcoin, Litecoin, Dogecoin or Bitcoin Cash address of the wallet paid in two or more transactions. |
| Unlimited token approvals still open | 0, low | An EVM address of the wallet let a contract spend a token without limit (2^96 - 1 units or more, as 96-bit tokens store it) and never took it back, as the last sync read: each token, spender and chain. A spender is named by its label, else by its verified contract name followed by its address, since the deployer chooses that name. Security, not privacy: never scored. Only approvals the address signed, or of a token it moved itself, count, since a token can announce an approval naming anyone. |
| The scan's findings | as above | An address of the wallet that is itself on a sanctions list or frozen; exchanges, flagged addresses and mixers, once per chain, from the stored transfers of all the wallet's addresses (the trusted ones, as the verdict reads them); the same address on several EVM chains; the first funder of an EVM, Solana or Tron address, from its stored history; its names, as the last sync looked them up; an exchange deposit address it shares with another of your wallets; a Tornado Cash pool another of your wallets deposited into or withdrew from, with few deposits in between; activity hours, over the transactions all the wallet's addresses sent. An HD wallet's addresses that find the same thing on a chain show it once, with each one's transactions. |

Each finding counts once, at its worst over the wallet's addresses and chains: one
exchange is enough to be exposed. Reuse is the exception, counted per address, so one slip
in an HD wallet costs 10 points instead of the F am-i-exposed gives that one address. The
panel lists the findings by chain, the wallet's own first, then open approvals and activity
hours apart, under **Security and habits**. Approvals cannot change the grade, but a chain
whose approvals were not read yet (history still filling in, an indexer that did not
answer) is named in the panel, and the grade says "Partly checked" until it is read: an empty
list must not read as "none". A wallet never read has no
grade, and one whose verdict says "Partly checked" (history still filling in, a source
that failed) is graded "Partly checked" too. So is a wallet whose names were never looked
up (each sync looks up those of 20 addresses, each once a day), and one first funded by a
stranger who also funded another of your wallets, until that stranger has been read: the
page makes no call, so the panel says what it waits for. **Privacy score** reads that
stranger, and the answer is kept.

am-i-exposed's deep scan of a Bitcoin address (its 30-odd heuristics, run in the browser) is
not part of the grade: the server cannot see it. A Bitcoin wallet's grade therefore says
"Wallet checks; addresses not deep-scanned", and each address of an HD wallet has its
**Deep scan** one click away.

The grades always follow the default 4 hops, whatever `max_hops` a request to `/analysis`
asks for, so the grade shown is the one alerted on.

### Litecoin, Dogecoin and Bitcoin Cash

A Litecoin, Dogecoin or Bitcoin Cash address (in the scanner's box, a `litecoin:` or
`dogecoin:` link, or **Privacy score** in the tracker) runs am-i-exposed's own scan with that
chain's profile: the same address and transaction checks, score and result page as for
Bitcoin, in the chain's unit and with links to its explorer. A transaction of these chains is
opened from a scan of one of its addresses, or as `/#tx=<txid>&chain=litecoin` (`dogecoin`,
`bitcoincash`).

The profile (`web/src/lib/bitcoin/chain-profile.ts`) leaves out what the chain does not have:

| Left out | On | Why |
|---|---|---|
| Wallet fingerprints, Whirlpool, tx0, Wasabi, WabiSabi, Stonewall, JoinMarket, BIP47, ricochet, post-mix | all three | Bitcoin wallets and protocols. Equal outputs are still found, as a CoinJoin of unknown kind. |
| The entity index and its OFAC list | all three | Bitcoin addresses only. |
| Witness and address-type checks | Dogecoin, Bitcoin Cash | No segwit. |
| RBF | Bitcoin Cash | It has no RBF: an RBF finding there would be false. |
| Exact fee rate | Dogecoin | Its wallets all pay a fixed rate per kB. |

Thresholds follow the chain: dust is 0.01 DOGE on Dogecoin (1,000 base units elsewhere), a
round amount there is a multiple of 1,000 DOGE, and block counts (a transaction held before
it was mined, the age of coins spent together) are scaled to 2.5-minute blocks on Litecoin and
1-minute blocks on Dogecoin.

The browser asks the tracker's server (`/api/tracker/utxo/<chain>/`), which answers in
Esplora's form from the indexers of [Chains](#chains): Litecoin's is passed through, Dogecoin's
and Bitcoin Cash's are converted. It answers what a scan asks (an address, its transactions and
unspent outputs, a transaction and where its outputs went), not the chain tip or prices, so
these scans show no fiat values. The result page's graph and cluster read through it too, and
its Wabisator and Chainalysis checks, which screen Bitcoin only, are not offered. So the depths
are the tracker's: the 50 newest transactions
of a Dogecoin address and the 100 newest of a Bitcoin Cash one, and the result says
**Some data could not be fetched** when an address has more. Those two indexers do not count
an address's outputs: they are counted in the transactions read, at least one when the totals
say so. A Dogecoin address scan costs two lookups. Once Dogecoin's hourly quota is used up,
what the scan still needs is skipped and the result says the same instead of failing; a scan
that has read nothing yet says the indexer is not taking more requests. The quota is shared
with syncs.

## Privacy

The database says which wallets are yours. Keep it on your machine.

**Privacy score** of a Bitcoin address hands it to am-i-exposed, which looks it up from your
browser: with [a Bitcoin node of your own](#a-bitcoin-node-of-your-own), at that node through
this server; otherwise at mempool.space (or at the mempool instance set in its own settings),
and tor-check.copexit.workers.dev to see whether you use Tor. `CT_ESPLORA` and `CT_CHAINS` do
not apply to it. A Litecoin, Dogecoin or Bitcoin Cash scan goes through this server instead, to
the indexer of its chain (`CT_LITECOIN`, `CT_DOGECOIN`, `CT_BITCOINCASH`), and stores nothing.
The privacy scan of an EVM, Solana or Tron address is made by the tracker's
server instead: like a lookup, it sends the address to the indexer of its chain, and to the
name services below (Names); it stores nothing. Finding its first funder can take one more
call to the same indexer. On a wallet of yours, a stranger who first funded it and also
funded another of your wallets is looked up there too, as a sync looks up a counterparty.
The privacy grade of your wallets is made from what is already stored and sends nothing
anywhere.

Every address you sync is sent to the indexer that answers for it, so that indexer sees
your wallets together, from one IP address. That is exactly the link this app helps you
avoid. For real privacy run [a Bitcoin node of your own](#a-bitcoin-node-of-your-own) and
point `CT_CHAINS` at indexers you run (Blockscout for EVM), or at least reach the public ones
through Tor or a VPN.

HD wallets: the xpub or descriptor is stored in the database and never sent anywhere,
and private keys are refused. But each derived address is looked up at the indexer, which
therefore learns that they belong together. With a public indexer, an HD wallet is only
as private as that indexer is discreet.

Swaps: each of your addresses is also looked up at the THORChain and Maya Midgard APIs.
`CT_SWAPS=off` turns that off.

NFTs: the NFT transfers of your own EVM wallets are read from the indexer that already
reads them, so it learns nothing new. Other addresses are never asked about theirs.

Names: each of your EVM addresses is looked up once a day in ENS, at the indexer that
already reads it (Routescan), and in Basenames at a public Base RPC
(base-rpc.publicnode.com), which is a new party: it sees each of your EVM addresses it is
asked about. Your Solana addresses are looked up in SNS at the Solana RPC that already reads
them. A scan does the same for the address scanned. With your own nodes, set `"rpc"` for
`ethereum` and `base` in `CT_CHAINS`: the calls go there instead. `CT_LABELS=off` makes no
name lookup at all. Names read through a gateway (CCIP-read, such as cb.id) are not followed,
since that would tell the gateway too.

Approvals: the `Approval` events that name one of your EVM addresses as owner are read from
the indexer that already reads that address (Routescan, Blockscout, Etherscan, or yours
through `CT_CHAINS`), plus one call that turns a date into a block number. It learns nothing
new. A scan asks for none: only your own wallets' approvals are read, by the sync. Activity
hours come from what is stored, or from the scan's own read, and send nothing.

Bridges: the receipt of each transaction in which your wallet paid a bridge is read once,
from the indexer that already reads that wallet (or from the `"rpc"` of that chain in
`CT_CHAINS`, your own node). When it names a Solana token account, the account's owner is
read from the Solana RPC that reads your Solana wallets (`CT_SOLANA`). Neither learns
anything new, so `CT_SWAPS` does not apply. A recipient that is not yours is read once,
like a counterparty, to tell a stranger from a service. Funds origin follows swap and bridge
records from what is already stored: it asks nobody anything.

Mixers: the receipt of each transaction in which your wallet paid a Tornado Cash pool (or
its router), or a pool paid your wallet, is read once from the indexer that already reads
that wallet. For the deposits and a withdrawal of two of your wallets, the pool's deposits
between them are asked of the same indexer, once per block range (its `logs` module, one
page of 20 rows per deposit of the wallet, 1,000 at most). That question names only the pool
and two block numbers, but they are the blocks of your own transactions, which that indexer
already served you.

Labels: the label sets and OFAC's sanctions lists are downloaded whole from GitHub, and OKX's
list of its own addresses from OKX's site, so none of them learns which addresses you look
up. Tether's
and Circle's freezes are read as the stablecoin contracts' events, from Ethereum's indexer
(Routescan, or yours through `CT_CHAINS`) and from TronGrid: the question names only the
contract, never an address of yours. `CT_LABELS=off` turns all of it off.

Shared counterparties: up to 10 addresses per sync that two of your wallets dealt with are
looked up at the indexer of their chain (the Solana RPC for Solana ones). That indexer
already served the histories they appear in; it now also sees them asked for from your IP.

Solana signers: a stranger that paid the fee of, or signed, a transaction of one of your
wallets is looked up once at the same Solana RPC (its newest 1000 signatures), to tell a
relayer or a marketplace from a person. That RPC already sees your wallet; it now also sees
the stranger's address asked about.

Tron delegations: a stranger that lent energy or bandwidth to one of your wallets, or that
one of them lent to, is looked up once at TronGrid (its newest 200 transactions), to tell an
energy rental service from a person. TronGrid already sees your wallet; it now also sees the
stranger's address asked about.

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
- Names: ENS's Universal Resolver on Ethereum, the ENSIP-19 reverse registrar and the
  Basenames registry on Base, and the SNS name-service accounts on Solana, all read on chain
  without a key.

## Who is on the other side

Every counterparty the app can name gets a service name and a type (exchange, DEX, DEX
aggregator, bridge, mixer, lending, NFT marketplace, payment processor, contract...),
shown wherever the address appears, together with where the name comes from:

| Source | What it is | Trust |
|---|---|---|
| you | A label set by hand in the app. | Always wins. |
| the protocol | THORChain and Maya vaults, from the protocols' own swap records. | Fact. |
| sanctions and freezes | [OFAC's sanctions lists](https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses) per currency, as extracted by 0xB10C (MIT), for every chain the tracker reads; the addresses Tether (USDT) and Circle (USDC) froze and did not unfreeze, from their contracts' events on Ethereum and Tron. | Fact; outranks the open label sets. |
| an open label set | [etherscan-labels](https://github.com/brianleect/etherscan-labels) for Ethereum, BNB Chain, Polygon, Arbitrum, Optimism, Avalanche and Fantom (a 2023 snapshot), [GraphSense TagPacks](https://github.com/graphsense/graphsense-tagpacks) for Bitcoin, Ethereum, Litecoin and Tron, the program names of the [Solana explorer](https://github.com/solana-labs/explorer) (all MIT), and OKX's [proof-of-reserves](https://www.okx.com/proof-of-reserves) list: every address OKX signs as its own ("I am an OKX address") on Bitcoin, Litecoin, Dogecoin, the EVM chains, Tron and Solana, about 570,000, mostly deposit addresses. | Fact, but no barrier: see below. |
| the explorer | The verified contract name of an EVM address ("UniswapV2Router02"). The name is a fact; the type read from it is a guess. | Guess, 80%. |
| behaviour | An address that was read in full, sends everything to one exchange and was paid by 3 senders at most (a deposit address); one that takes one asset from you and returns another in the same transaction (a pool or router); one with too much history to read (some service). | Guess, 60%. |

The label sets are downloaded whole (about 1.1 million labels; OKX's list alone is a
110 MB file, published anew each month, and each copy replaces the last) on the first sync
and again every 30 days, and looked up locally, so naming an address tells nobody which
addresses you care about. Contract names are asked from the explorer that already served your
wallets' histories, 15 addresses per sync, each only once. `CT_LABELS=off` turns both off.

Phishing, hack, scam and sanctions labels in those sets get the type "scam, hack or
sanctions" and the **flagged** category: the check before sending calls such an address
risky. A database labelled before that keeps them as plain labels until the sets are
downloaded again.

Sanctioned and frozen addresses are flagged too, as "Sanctioned by OFAC" or "Frozen by Tether
(USDT)", and that label wins over an open set's: Garantex is an exchange, and sanctioned.
Only a label you set wins over it. They refresh with the label sets, every 30 days, each read
whole and replacing its old copy, so a delisted or unfrozen address loses the label; a list
that cannot be read keeps its old copy and is named in the sync's notes. Reading the freezes
takes a few minutes (about 3,600 frozen addresses on Ethereum and 8,900 on Tron in November
2026; TronGrid without a key is read slowly so that it does not suspend the app).

A known service ends a trace: two people using Binance are not linked. A guessed one
does not end it, it weakens it by how sure the guess is, because a wrong guess must not
hide a link. A guess is always shown as "(guess)", and disappears again when the
behaviour it was read from is no longer in the data.

An address named only by OKX's list is OKX's, but most of them are deposit addresses, each
one customer's: two wallets that paid the same one are one OKX account. So its name is shown
and counts as an exchange in the privacy scan and the check before sending, but it does not
end a trace. It is read and judged like a stranger: cut once found busy (a hot wallet),
passed through at 0.7 otherwise, and two wallets that paid one that sends everything to OKX
get the `deposit-address` link (85%). Where an open set also names it (OKX's hot wallets
often are), that label wins and ends the trace.

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
| name | 0.8 | The primary name of one of your EVM addresses (ENS, Basenames), checked forward, lists the other address (a Bitcoin, Solana... address, ENSIP-9). The name's owner published it, but anybody can type any address into their records. A stranger it lists is read first: a busy one, or a labelled service, links nobody. Several names listing the same address stay at 0.8. |
| bridge-record | 0.95 | A bridge's own event, in the receipt of a transaction in which your wallet paid it, names the recipient on the other chain: Circle CCTP (`DepositForBurn`), Across (`FundsDeposited`), Wormhole's Token Bridge (a plain transfer), the OP Stack bridges into Base and Optimism (`ETHDepositInitiated`, `ERC20DepositInitiated`), Arbitrum's gateway router (`TransferRouted`). On Solana the token account it names is replaced by the account's owner; a token account closed since links nobody, and the verdict says so. A recipient that is not yours is read first: a busy address or a service links nobody, a quiet one is listed in the wallet's verdict. Declare it yours later and the next sync links it, without reading the receipt again. Several deposits to the same address stay at 0.95. |
| deposit-address | 0.85 | Two of your wallets paid the same address, which was read in full without an error, is not a very active address, has no label but the tracker's own guess or OKX's list's, sends everything to one labelled exchange, and was paid by 3 senders at most (two addresses of one wallet count once; the exchange topping up gas is one). A deposit address belongs to one exchange account. One row per deposit address: paying it again adds nothing, a second shared deposit address combines. EVM, Tron, Bitcoin, Litecoin and Bitcoin Cash. |
| fee-payer | 0.9 | Solana: one address paid the fee of a transaction the other signed. Both hold a key of it: one owner, unless the payer is a relayer. |
| co-signer | 0.9 | Solana: two addresses, neither of them the fee payer, signed one transaction: Solana's common input. |
| delegation | 0.8 | Tron: one address lent the energy or bandwidth of its staked TRX to the other (`DelegateResourceContract`). People lend resources to their own wallets so these need not hold TRX for fees; energy rental services lend to everyone. Taking it back adds nothing. |
| mixer | 0.3 at most | One of your wallets deposited into a Tornado Cash pool and another of yours withdrew from the same pool later: with n deposits made in between, the withdrawal is the first wallet's with odds of at most 1/(1+n). A wallet that deposited several times could be behind any of its deposits: from each of them to the withdrawal, k of the deposits are the wallet's, with odds of at most k/(deposits in that stretch), and the strongest stretch counts. Held at 0.3, so it never raises an alert on its own. It is an upper bound: older deposits not yet withdrawn hide the pair further, and are not counted, nor are other people's deposits in the block of either end. From 20 deposits per deposit of the wallet (under 5%) the pool hides the pair and no line is drawn. Two addresses of one HD wallet are not paired. Several withdrawals between the same two wallets stay at the strongest. |

Several transactions supporting the same guess combine as `1 - (1-c1)(1-c2)...`.

A Solana fee payer or co-signer that is not yours is read first (its newest 1000
signatures): 1000 of them, or a label from a source that states it, make it a very active
address, which links nobody, and no line to it is stored. Only a quiet one is linked.
A Tron address that lent resources to one of your wallets, or was lent them, is read the same
way (one TronGrid page): its newest 200 transactions filling the page, as an energy rental
service's do, or such a label, make it a very active address.
The rent of a closed token account is booked to the account's owner: a refund to another
address is a transfer from the owner to it.

An NFT (ERC-721 or ERC-1155) one of your wallets sent is a transfer too, read from your own
wallets' histories: one moved between two of your wallets links them, as a payment does.
Anybody can send an NFT to anybody, often from a made-up sender, so one that arrived from a
stranger counts only once a wallet of yours has sent that same NFT on, as with tokens; it
is trusted one NFT at a time, never a whole contract, since a shared contract (OpenSea's)
lets anybody mint into it. A mint or a burn (from or to the zero address, or to 0x…dEaD) is
no transfer. In the graph a collection's NFTs between two addresses are one arrow. An NFT is
no money: never a balance, a funds origin, a bridged amount or an exchange flow.

The findings of the [privacy scan](#privacy-scan-of-evm-solana-and-tron-addresses) are
facts: a transfer read from the chain, with a counterparty that an open label set or you
name, or a name record. A sanction or a freeze is published by OFAC or by the issuer's own
contract, so it is a fact too. They add no line to the graph; the addresses a name of your
own wallet lists do (name, above). A first funder is such a transfer, already a solid arrow; the
finding only names it. A stranger who first funded one of your wallets and also funded
another joins them through a stranger (0.7), as any shared counterparty does. The scan of
such a wallet reads that stranger first: a busy one is stored as a very active address, which
no path passes through, so an unlabelled exchange wallet is not taken for one person who
funded both.
So is an open approval: the token's own `Approval` events, the last one per spender.
So is reuse in a
[wallet's grade](#privacy-grade-of-your-wallets); "tied to your public wallet" is as sure as
the link it rests on, and costs in proportion.
The exceptions are a shared deposit address and a mixer that hides little, which are the
`deposit-address` and `mixer` guesses above, shown as findings. Activity hours are an
estimate: block times read as a time zone, give or take two hours, and never a link, since
millions of people share one.

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

A swap or bridge record is not where it stops: the money is followed on from the record's
sender, on the chain and in the asset paid in, as one hop, and the source says which
protocols it crossed ("via Wormhole"). That sender is passed through even when it is one of
your wallets, since the money changed chain or asset there. A swap's payout transaction is
known (THORChain and Maya name it), so its vault is no longer a source. A bridge names no
payout transaction, and on Solana it mints the tokens, which no transfer shows: what a
bridge record brought in is listed as an asset of its own, "bridged from" the source chain.
A sender that was never read ends the walk, as any address that was not expanded does.

### Groups of wallets

The verdicts list pairs; the portfolio's **What an observer sees** also groups your wallets
into the ones that look like one person. Two wallets are in one group when a link of 50% or
more (the alert threshold) ties them without passing through a third wallet of yours, and
groups chain: A tied to B and B to C puts all three in one group, whatever their chains. A
group shows its wallets and chains, says so first when one of them is public (whoever knows
it can name the others), and lists the links holding it together, weakest first. When one
link alone holds two parts of a group together, it says so, with the two parts: that is the
guess a whole group rests on. **Show in graph** highlights every link of the group. A group
with a wallet not read in full says "Partly checked": it may be larger. The groups are worked
out on the server from the verdicts (`tracker/clusters.py`); nothing is fetched.

## Known limits

- Swaps that change the asset are matched only through THORChain and Maya Protocol,
  whose records are public. Other swap services and instant exchangers are not.
- Funds origin lists money that came in through a bridge record apart, as "bridged from"
  its chain, because the bridge's payout transaction is not known. On an EVM chain that
  payout is also a transfer (a mint from the zero address, a relayer's), listed under the
  asset received, so the same money shows twice there, once with its real origin. A bridge
  record is followed only for the wallet that received it: further up a path (a stranger, or
  another wallet of yours whose money came back over a bridge) the walk ends at that address.
  A THORChain or Maya swap whose two ends are the same address (ETH to USDC, or from one EVM
  chain to another) is stored as no record, so its origin still ends at the protocol's vault.
- A bridge or exchange flow is only matched by amount and time when both ends are wallets
  you declared. Bridge records also find a recipient you did not declare, but only for a
  deposit your wallet makes straight into the bridge, or through a contract a label set
  calls a bridge; through another contract (a DEX aggregator) it is matched by amount and
  time only. Wormhole transfers with a payload, wrapped tokens burned on their way back
  through Wormhole, Across to Solana and transfers out of Solana are not read as records.
- Mixers are not correlated by amount: their fixed denominations make it meaningless.
  Tornado Cash is read through its pools' own events instead (the `mixer` link), only
  between two wallets you declared, and not on BNB Chain read through NodeReal. Other mixers
  are only recognised by their labels.
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
  until it answers again (or until `CT_SWAPS=off`): a swap may have gone unseen. The same
  holds while a bridge receipt cannot be read: until its indexer answers again, or for good
  for a transaction of more than 1,000 logs on a Blockscout chain. The other receipts are
  read meanwhile.
- Alerts are raised for links of 50% confidence or more; weaker guesses are listed in
  the verdict only.
- On a mempool with an Electrum server (the `mempool` profile's, Umbrel's), the scanner reads
  the 10 newest transactions of an address: am-i-exposed pages by 25, which such a mempool does
  not. The tracker reads them all.
- The notices about your own node are in English, like the tracker's other server messages.
- Token balances come from the transfer history unless the indexer has a token-balance
  call (`"tokenlist": true`), so rebasing tokens are off.
- An indexer of your own in `CT_CHAINS` must also answer the NFT lists (`tokennfttx`,
  `token1155tx`), as Etherscan, Routescan and Blockscout do. One that does not leaves your
  wallets' history on that chain filling in, with the error in the wallet's panel.
- Address checksums are verified, except the mixed-case form of EVM addresses and
  Bitcoin Cash's cashaddr.
- An unlabelled exchange wallet looks like a stranger until it is read; each sync reads
  10 of those that two of your wallets dealt with (none on Dogecoin), and a busy one ends
  the link. One that looks quiet in what is read can still produce a false link; label it
  in the UI and the link disappears. A read that failed on one chain is tried again on
  later syncs, after the addresses never read. An address named only by OKX's list is read
  and judged the same way. On Solana the only exchange labels are OKX's own list, on Tron
  OKX's and GraphSense's: other exchanges' wallets there are strangers until you label them.
- OKX's list names its deposit addresses as OKX, so an address that only ever paid one of
  them counts as one of OKX's deposit addresses too. Two wallets that paid such an address
  (perhaps a person's wallet that only tops up an OKX account) get the `deposit-address`
  link at 85%, where a stranger in between would give 70%.
- Stablecoin freezes are read on Ethereum and Tron only, where most USDT and USDC lives;
  Tether's and Circle's freezes on other chains are not flagged. Ethereum's are read from an
  Etherscan-style indexer: with one that has no `logs` module, that set is not downloaded.
- A Solana address found quiet is not asked again, even if it gets busy later.
- Every sender counts towards a deposit address's 3, also dust: two address-poisoning
  payments of a token you hold (common with USDT on Tron) are enough to lose its link.
- A Solana fee payer or co-signer that cannot be read when its transaction is first read
  gets no link; the sync's error names it. The 25 newest transactions are read again on
  each sync, older ones are not. Once the RPC answers "too many requests", the other
  strangers of that sync wait for a later one.
- A database from before the Solana signer links reads each Solana wallet's history once
  more, from the newest, so older transactions get their links too; until it is done the
  wallet reads "Partly checked". A rent refund stored before keeps its old row, from the
  token account, next to the new one from the owner: a few thousandths of a SOL counted twice.
- A lookup reads no stranger, so it lists a Solana address's fee payers and co-signers as
  guesses even when one of them is a relayer, and a Tron address's delegations even when
  they come from a rental service.
- A database from before the Tron delegation links reads each Tron wallet's TRX history once
  more, from the newest, so older delegations get their links too; until it is done the
  wallet reads "Partly checked".
- Tron delegations made before Stake 2.0 (April 2023), by freezing TRX for another address,
  are not read.
- A delegation to or from a Tron stranger that cannot be read gets no link; the sync's error
  names the stranger. Tron's newest 200 transactions are read again on each sync, older ones
  are not.
- Open approvals are only unlimited ERC-20 ones. A large but finite approval, an NFT
  approval (`setApprovalForAll`) and the allowances Permit2 grants by signature are not
  listed; an unlimited approval to Permit2 itself is. A permit that a dApp's relayer
  submitted is listed only if the wallet moved that token in a transaction of its own. BNB
  Chain through NodeReal is not read, and a wallet's approvals on a chain wait until its
  history there is complete. A permit signed since the last read shows on the next day's
  read. More than 10,000 approvals naming one address in a single block (spam) stop its read
  on that chain, which the panel then names as not read.
- Activity hours take the night as midnight to 8:00 local time: a night worker, a bot or a
  wallet shared across time zones shows another zone, or none. Daylight saving time moves
  it by an hour.
- A Litecoin, Dogecoin or Bitcoin Cash scan uses am-i-exposed's own words: some findings and
  the top recommendation speak of sats, sat/vB and Bitcoin wallets, and the clearnet notice
  still names mempool.space although the scan goes through this server. A recent scan of one
  of their transactions reopens on Bitcoin (their addresses find their chain again); open it
  from the address instead. The other way round, a transaction id typed while one of their
  results is shown is looked up on that chain.

## Built on am-i-exposed

[am-i-exposed](https://github.com/Copexit/am-i-exposed) (MIT, © Copexit; its license is in
`web/LICENSE`) is the app itself: `web/` is a snapshot of it, v0.43.3, brought in without
its history, and the wallet tracker is built into it like any of its own pages: its menu,
its look, its two themes and its six languages.

- `web/UPSTREAM` records the upstream commit the snapshot came from.
- Left out of the snapshot: its CI workflows, the tutorial videos (the build turns them
  off), the source data of its entity index, archived PDFs and a notes file.
- The entity index (about 100 MB of generated data its repository keeps) is fetched at
  build time from that same commit by `scripts/entity-data.sh`, and checked against
  `web/public/data/entity-data.sha256`.
- The tracker's own code is apart from upstream's, so that its changes merge cleanly:
  `web/src/app/tracker/`, `web/src/components/tracker/`, `web/src/lib/tracker/`,
  `web/src/lib/i18n/tracker.ts` and the `tracker*.json` files in `web/public/locales/`. In
  upstream's own files the changes are a few lines: the menu item, the i18n set-up, the
  `three` and `cytoscape` packages, `torProxy` in `/api/local-info`: a host with its own
  mempool but no Tor sidecar (this server) is used like Umbrel for mempool, while the services
  Umbrel sends through its sidecar keep the public site's routes (`useLocalApi`,
  `NetworkContext`, and the hooks and panels of those services), and EVM, Solana and Tron
  addresses let through the input boxes (`AddressInput.tsx`, `InlineSearchBar.tsx`) and sent
  on to the tracker's scan where upstream would call them invalid (`useAnalysis.ts`).
  Litecoin, Dogecoin and Bitcoin Cash in the scanner thread a chain profile through the
  engine: `chain-profile.ts` is new, and the gates and thresholds are one or two lines each
  in `detect-input.ts`, `heuristic-steps.ts`, `tx-pipeline.ts`, `address-orchestrator.ts`,
  `orchestrator.ts`, `heuristics/` (`coinjoin`, `fee-analysis`, `tx-utils`, `round-amount`,
  `dust-output`, `utxo-analysis`, `anonymity-set`, `timing`, `utxo-age-spread`),
  `chain/backward.ts`, `chain/entity-proximity.ts`, `entity-filter/` (`entity-match`,
  `entity-index`), `bitcoin/address-type.ts`, `format.ts`, `useAnalysis.ts`,
  `useHashRouting.ts`, `useScanner.ts`, `useClusterAnalysis.ts`, `Results.tsx`,
  `ContextSection.tsx`, `ScanScreen.tsx`, `GraphExplorerPanel.tsx`, `FindingCardTables.tsx`
  and `GraphSidebar.tsx`.
- The tracker's server (`tracker/`, Python) stays behind the app: it keeps the database,
  syncs, and answers under `/api/tracker/`. `/api/` itself is left to am-i-exposed, which
  looks there for a self-hosted mempool.

To merge later upstream changes, from a clean working tree:

```sh
scripts/sync-am-i-exposed.sh v0.44.0      # a tag or a branch; default main
```

It fetches only the recorded commit and the new one, never upstream's history, and merges
upstream's changes between them into `web/` (three-way, like a git merge), minus what is
left out above. Conflicts stay in the files for you to resolve. It moves `web/UPSTREAM`
and the entity index checksums to the new commit; then run the checks below and commit it
as one merge. This is a real merge each time, not a version bump: the price of one app.

`pnpm build` rewrites `web/public/sitemap.xml` with today's dates: never commit that change
(`git checkout web/public/sitemap.xml`).

Both parts share one origin (address and port): am-i-exposed's code runs with the same
session as the tracker and could read and change your wallets through its API. Read
upstream's changes before merging them.

## Develop

The server, from the repository's top:

```sh
uv sync
uv run python -m tracker              # the API on http://127.0.0.1:8000/api/tracker/
uv run pytest
uv run ruff check . && uv run ruff format --check .
shellcheck proxmox/install.sh scripts/*.sh
```

A local node for another chain follows the Bitcoin one: a Compose profile in `compose.yaml`,
the chain's default URL as `os.environ.get("CT_<CHAIN>") or node.local("<profile>", "<its
service>") or "<public URL>"` (`tracker/node.py`), and its disk in `proxmox/install.sh`.

The web app, in `web/` (Node 22 or newer, pnpm through corepack):

```sh
corepack pnpm install
../scripts/entity-data.sh             # once, and after each upstream merge
corepack pnpm lint && corepack pnpm type-check && corepack pnpm test
corepack pnpm build                   # the static export in out/
cd .. && CT_WEB=web/out uv run python -m tracker   # the whole app on http://127.0.0.1:8000/
```
