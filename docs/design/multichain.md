# Multi-chain and cross-chain privacy analysis

Design for taking am-i-exposed's privacy analysis beyond Bitcoin: to every chain the tracker
reads, and across chains. It covers what exists today, the checks worth adding (ranked, with
the ones left out and why), how they fit the tracker's links, verdicts and graph, the
architecture, and the split into implementation issues.

Status: proposal, for review before any implementation issue is created.

## Summary

- **Two engines, one result.** UTXO chains (Bitcoin, Litecoin, Dogecoin, Bitcoin Cash) stay
  in am-i-exposed's engine in the browser, which gets a chain profile instead of Bitcoin
  constants. Account chains (the EVM chains, Solana, Tron) are analysed by the tracker's
  Python server, which already reads them, holds the label sets and the API keys. Both
  produce am-i-exposed's `Finding` type, are scored by its `score.ts` formula (on the server
  a Python port of it, tested against the original) and shown by its result components, so a
  score means the same thing on every chain.
- **Cross-chain through the tracker's link graph.** New kinds of link (exchange deposit
  address, bridge record, name record, Solana fee payer and co-signer) join the existing
  ones, so the verdicts, alerts and graph pick them up unchanged. On top: funds origin that
  follows a swap or bridge to the other chain, a view of which wallets an observer can join
  into one person, and one privacy score per wallet across all its chains.
- **Few, reliable checks.** Facts first (a protocol record, a name record, a label, the
  first funder), then guesses with a stated confidence. Checks whose false positives would
  mislead are dropped (2.8); the rest are ranked (2.7).
- **Sixteen implementation issues.** One small foundation in stage 2, nine in stage 3 that
  can run in parallel, six in stage 4.

## 1. What exists today

### 1.1 am-i-exposed (`web/`)

am-i-exposed analyses one Bitcoin address, transaction, xpub/descriptor or PSBT in the
browser, against mempool.space or the mempool instance set in its settings. Everything works
on Esplora/mempool JSON (`lib/api/types.ts`: `vin[].prevout{scriptpubkey_type,
scriptpubkey_address, value}`, `vout`, `status.block_time`).

**Pipeline.** `#addr=`, `#tx=` or `#xpub=` (`hooks/useHashRouting.ts`) → `cleanInput` and
`detectInputType` (`lib/analysis/detect-input.ts`; Bitcoin prefixes only, so any other
chain's address is `invalid`) → result cache (`lib/api/analysis-cache.ts`) → engine, loaded
lazily (`lib/analysis/engine.ts`):

- **Transaction** (`run-txid-analysis.ts`): the transaction, its prevouts, outspends, parent
  and child, a backward/forward trace (depth 4), 28 per-transaction heuristics, Boltzmann
  entropy (WASM), then cross-heuristic rules (`cross-heuristic/`).
- **Address** (`run-address-analysis.ts`): OFAC pre-check, the address, its UTXOs and up to
  4 pages of transactions, 6 per-address heuristics, temporal and prospective analysis, a
  quick score per transaction, and a pre-send view (`address-orchestrator.ts`).
- **xpub/descriptor** (`lib/bitcoin/descriptor.ts`, `lib/wallet/scan.ts`,
  `wallet-audit.ts`) and **PSBT/raw transaction** (`lib/bitcoin/psbt.ts`,
  `run-local-analysis.ts`, `pre-broadcast-checklist.ts`).

**Heuristics** (`lib/analysis/heuristics/`, registry in `heuristic-registry.ts`):

| Group | Checks (finding ids) |
|---|---|
| Ownership | common-input `h3-cioh`; change detection with 8 signals `h2-*` (script type, round amount, value gap, unnecessary input, optimal and shadow change, round fiat, fresh output); self-send, sweep; consolidation `consolidation-*`; unnecessary input; peel chain |
| Reuse and exposure | address reuse `h8-address-reuse` (−70 to −93, forces F); UTXO set `h9-*`; address type `h10-*`; spending pattern, recurring payments, high activity |
| CoinJoin | Whirlpool, tx0, WabiSabi, Wasabi 1, JoinMarket, Stonewall `h4-*`; anonymity set `anon-set-*`; post-mix consolidation |
| Entropy | Boltzmann `h5-*`, link matrix `linkability-*` |
| Fingerprints | wallet fingerprint `h11-*` (nVersion, nLockTime, nSequence, BIP69, low-R); fee `h6-*` (round rate, RBF, CPFP); script-type mix; witness `witness-*`; coin selection; timing `timing-*`; UTXO age spread |
| Leaks in the transaction | round amounts `h1-*`; dust `dust-*`; OP_RETURN `h7-*`; BIP47 notification; exchange withdrawal pattern |
| Known parties | entity index (about 30M Bitcoin addresses, `entity-filter/`, fetched at build by `scripts/entity-data.sh`); OFAC (`data/ofac-addresses.json`, Bitcoin only); an optional Chainalysis screening proxy started by the user; Bisq, HodlHodl, Ashigaru ricochet, Lightning channels |
| Chain analysis | backward and forward trace, taint, entity proximity, CIOH clustering, spending patterns (`lib/analysis/chain/`) |

**Score** (`lib/scoring/score.ts`): base 93 for an address, 70 for a transaction, plus the
sum of `scoreImpact`, clamped to 0..100. Grades A+ ≥ 90, B ≥ 75, C ≥ 50, D ≥ 25, F. A
`Finding` has `id`, `severity` (critical, high, medium, low, good), `scoreImpact`, `params`
for its text, `confidence`, `adversaryTiers` and `temporality`; `ScoringResult` has `score`,
`grade`, `findings` and `partial`. The texts are looked up as `finding.<id>.<field>` in the
`common` namespace, with the English text of the finding as the default.

**Graph** (`lib/graph/`, `components/viz/graph/`): a transaction graph whose edges are spent
outputs. It is UTXO-shaped by construction.

**Networks**: `lib/bitcoin/networks.ts` knows mainnet, testnet4 and signet of Bitcoin, and
`context/NetworkContext.tsx` picks the backend (custom URL, Umbrel, Tor, default). There is no
notion of another chain; chain constants are spread over `detect-input.ts`, `lib/bitcoin/*`,
`lib/constants.ts` and the heuristics.

### 1.2 Which of it generalises to the tracker's other UTXO chains

| | Litecoin | Dogecoin, Bitcoin Cash |
|---|---|---|
| CIOH, consolidation, unnecessary input, peel chain, entropy and link matrix, anonymity set, exchange pattern, address reuse, UTXO count, spending, recurring, high activity, clustering, coinbase, generic multisig, BIP69 | unchanged | unchanged |
| Round amount, dust, round fee rate | unchanged (same units, similar fees) | thresholds per chain: on Dogecoin almost every amount is round and dust is far larger |
| Timing (stale locktime), UTXO age spread | block time 2.5 min | block time 1 min (DOGE), 10 min (BCH) |
| Change detection | unchanged | the script-type signal is almost inert (P2PKH only) |
| Address type, script-type mix, witness, segwit fee check | needs Litecoin prefixes | inert: no segwit, no taproot |
| RBF signal | unchanged | **wrong on Bitcoin Cash**, which has no RBF: must be off |
| Wallet fingerprints, Whirlpool, tx0, WabiSabi, Wasabi, Stonewall, BIP47, ricochet, Bisq, HodlHodl | Bitcoin wallets and protocols only: off | off |
| Entity index, OFAC, Chainalysis proxy | Bitcoin data only: off, replaced by the tracker's labels and per-chain OFAC lists | off, same |
| Input detection, xpub, descriptors | prefixes, version bytes and `ltc` HRP per chain | same, plus CashAddr |
| Esplora client, caches, trace, graph | litecoinspace.org is Esplora: works | no public Esplora: needs an Esplora-shaped proxy |

Chain-specific gaps: Litecoin's MWEB peg-ins and peg-outs hide amounts and destinations and
need their own reading; Bitcoin Cash's CashFusion uses unequal amounts, so no current CoinJoin
detector sees it (a CashFusion detector is left for later, see 2.6).

### 1.3 The tracker (`tracker/`, behind `/api/tracker/`)

The tracker already reads every chain this design needs, server side:

- **Chain adapters** (`tracker/chains.py`): Bitcoin and Litecoin through Esplora, Bitcoin
  Cash through Haskoin, Dogecoin through BlockCypher, the last two converted to Esplora's
  shape; seven EVM chains through Etherscan-compatible APIs (Routescan without a key for
  Ethereum and Avalanche, Blockscout or Etherscan with a free key, NodeReal for BNB Chain,
  any self-hosted indexer through `CT_CHAINS`); Tron through TronGrid; Solana through its
  JSON-RPC. Everything becomes transfer rows `(chain, txid, src, dst, asset, token, amount,
  ts)`. An EVM address is one node read on every EVM chain.
- **Full history of my own wallets** (`tracker/history.py`), bookmarked per source, filled
  in across syncs.
- **Labels** (`tracker/labels.py`, `tracker/detect.py`): about 540,000 from open sets
  (etherscan-labels, GraphSense TagPacks, Solana program names), downloaded whole; THORChain
  and Maya vaults; verified contract names; behaviour guesses (exchange deposit address, swap
  contract, high-volume address). Categories cex, dex, bridge, mixer, flagged, defi, service.
- **Links** (`tracker/heuristics.py`, `tracker/swaps.py`): common-input 0.9 (0.2 in a
  CoinJoin), change 0.5–0.75, bridge by amount and time 0.5–0.95, exchange flow 0.09–0.8,
  THORChain/Maya swap record 0.95.
- **Verdicts, origins, graph** (`tracker/graph.py`): the strongest path between each pair of
  my wallets (a transfer 1.0, a link its confidence, ×0.7 per stranger, never through a known
  service or a hub), chained through my own wallets; funds origin per wallet and asset,
  followed upstream to a label, my wallet or the edge; the node graph. Alerts when a link
  appears or strengthens (`tracker/alerts.py`).
- **Lookup** (`tracker/lookup.py`): a live, read-only view of any address with a "check
  before sending" verdict (flagged, mixer, address poisoning, new, unused).
- **Bridge to the scanner**: `PrivacyScoreLink` (`web/src/components/tracker/bits.tsx`)
  opens `/#addr=` for Bitcoin addresses only.

The design builds on the tracker for everything that is not a UTXO transaction: its data,
its labels and its link graph are where account-chain and cross-chain privacy lives.

## 2. Feature catalogue

### 2.1 How checks are ranked

A check is worth building when an observer (an exchange, an analytics firm, anyone with an
explorer) really uses it, when it rests on a fact or on a guess with an honest confidence,
and when the data comes from a free or self-hostable source. Each check below is either a
**fact** (on-chain or published by the protocol: shown as certain), a **guess** (shown with
its confidence and the rule), or an **estimate** (shown as such, never used as a link).

Rank 1 is the most useful and reliable. "Link" means the check also adds a link between two
addresses to the tracker's graph (section 3.5); "finding" means it adds a finding to the
privacy scan and the wallet score (section 4.1).

### 2.2 Every chain

| # | Check | Detects | Why you care | Data, free source | Reliability | Output |
|---|---|---|---|---|---|---|
| 1 | **Linked to a public wallet** | One of your private wallets can be tied to a public one (exists as the verdict) | Whatever is tied to your identity spreads to it | The tracker's verdicts | As the links it rests on | finding |
| 2 | **Exchange exposure (KYC)** | Direct deposits to or withdrawals from a labelled exchange | The exchange knows the address is yours, and can be made to tell | Transfers + label sets | Fact (where labelled) | finding |
| 3 | **Exchange deposit address shared** | Two of your wallets paid the same exchange deposit address (EVM, Tron, Bitcoin, Litecoin, Bitcoin Cash) | Deposit addresses belong to one exchange account: the two wallets are one customer | Transfers, and the candidate itself: a sync does not expand counterparties by default (depth 0), and never on Tron, so each unread address paid by two or more of my wallets is read once (`sync.sync_address`, at most 10 per sync, busiest first) | Guess, high (0.85), rules in 3.5 | link + finding |
| 4 | **Flagged and sanctioned counterparties** | Dealings with addresses on OFAC's list, frozen by Tether or Circle, or labelled scam, hack, mixer | Exchanges freeze or question such funds | [0xB10C's OFAC lists](https://github.com/0xB10C/ofac-sanctioned-digital-currency-addresses) (MIT; XBT, ETH, USDT, USDC, TRX, SOL, BSC, ARB, LTC...), USDT/USDC `AddedBlackList`/`Blacklisted` events through the indexer, label sets | Fact | finding; feeds the lookup's "check before sending" |
| 5 | **Activity hours** | The hours at which the wallet sends, and the time zone they suggest | A time zone narrows down who you are | Timestamps of transactions the wallet sent (already stored) | Estimate, only from 20 or more sent transactions | finding, never a link |

### 2.3 EVM

Ethereum, BNB Chain, Base, Arbitrum, Optimism, Polygon, Avalanche and any chain added through
`CT_CHAINS`.

| # | Check | Detects | Why you care | Data, free source | Reliability | Output |
|---|---|---|---|---|---|---|
| 1 | **First funder (gas)** | Who sent the first native coin, needed for gas, on each chain | A fresh wallet funded from your main wallet or from your exchange account is tied to it from its first transaction; the most used Sybil and clustering signal | The earliest incoming native transfer; own wallets: already stored; others: one `txlist`/`txlistinternal` call with `sort=asc` per chain (Routescan keyless, Blockscout, NodeReal, self-hosted) | Fact | finding; a funder that is mine is already a transfer link |
| 2 | **Name records** | A primary ENS name (reverse record, checked forward), Basenames; the addresses of other chains a name publishes (ENSIP-9: BTC, SOL...) | A name is a public tag on every transaction; addresses listed on it tie chains together for anyone | `eth_call` to the ENS Universal Resolver (on Base for Basenames), sent where 4.9 says: the indexer that already reads the wallet if it serves `eth_call` (Routescan does, keyless), else the chain's `rpc` in `CT_CHAINS`, else a public RPC | Fact (reverse record is set by the address itself); listed addresses: 0.8 | finding; link 0.8 between the name's address and each address it lists |
| 3 | **Same address on several EVM chains** | The address is active on N chains | Anything that ties it on one chain ties it on all | Already read | Fact | finding (low impact, explains) |
| 4 | **Bridge records** | Bridging through CCTP, Across, Wormhole, the OP Stack and Arbitrum bridges, with the recipient the bridge event names | The recipient is public: the two ends are one person to anyone looking | The bridge's own event in the receipt of your transaction (no new party involved) | Fact (0.95, like swaps) | link (section 3.1) |
| 5 | **Mixer use and anonymity set** | Deposits to and withdrawals from Tornado Cash pools (and other labelled mixers); when both legs are your wallets, how many deposits went into the pool between them | Tornado use is flagged by exchanges; a small anonymity set links deposit and withdrawal | Pool `Deposit`/`Withdrawal` logs for that block range through the indexer's `logs` module (Routescan keyless, checked) | Fact for use; link at most 1 / (1 + deposits in between), capped at 0.3, only when both legs are yours (3.5) | finding; link |
| 6 | **NFTs moved between your wallets** | ERC-721/1155 transfers between two of your wallets | As direct a link as a payment; today they are not read | `tokennfttx`, `token1155tx` lists of the same indexers | Fact; NFTs from strangers ignored (anyone can airdrop) | link (transfer) |
| 7 | **Open token approvals** | Unlimited or large ERC-20 approvals still open, and Permit2 allowances, with the spender's label | Security rather than privacy: an approval to a drained or unknown contract can empty the wallet; approvals also list the dApps you use | `Approval` logs with your address as owner, the current `allowance` by `eth_call` | Fact | finding, score impact 0 (shown under security) |

Smart-account owners and deployers (a Safe's `getOwners()`, the creator of a contract wallet)
are facts too, but matter to few users, and `.bnb` names need a resolver of their own: both
deferred (6.2).

### 2.4 Solana

| # | Check | Detects | Why you care | Data, free source | Reliability | Output |
|---|---|---|---|---|---|---|
| 1 | **Fee payer** | Your transactions whose fee was paid by another address | The payer holds a key that signed with you: one owner, unless it is a relayer | `getTransaction` (already fetched): `accountKeys[0]` | Fact. A payer that is not mine is read first (one `getSignaturesForAddress`, limit 1000): 1000 signatures or a label make it a hub and no link is stored (3.5) | link 0.9 |
| 2 | **Co-signers** | Two addresses signing one transaction | Solana's equivalent of common inputs | `accountKeys[].signer` of the same transactions | Fact. A co-signer that is not mine is read first, as a payer: marketplace and RFQ authorities come out as hubs | link 0.9 |
| 3 | **First funder and rent refunds** | Who first funded the account; where the rent of your closed token accounts went | Refunds to another wallet tie the two; today they are booked to the token account, not its owner | Same transactions: `closeAccount` destination, first incoming SOL | Fact | link (transfer); finding |
| 4 | **Exchange exposure** | As 2.2 #2 | As 2.2 #2 | Needs Solana exchange labels, which the open sets lack: exchanges' published proof-of-reserves addresses (OKX signs each one), plus your own labels | Fact once labelled | finding |
| 5 | **Name records** | `.sol` names (SNS) pointing at the address | As EVM names | Bonfida's free SNS proxy, or the name-service program through your RPC | Fact | finding |

### 2.5 Tron

| # | Check | Detects | Why you care | Data, free source | Reliability | Output |
|---|---|---|---|---|---|---|
| 1 | **Activator** | The address that activated the account (the first TRX or TRC-10 transfer, which pays the account's creation) | Exchanges activate their deposit addresses; a wallet activated by your other wallet is tied to it | TronGrid `transactions?only_to=true&order_by=block_timestamp,asc&limit=1` | Fact | finding; transfer link already |
| 2 | **Energy and bandwidth delegation** | `DelegateResourceContract` from one address to another | Delegating resources to your own wallets ties them; rental services are hubs | TronGrid transaction list (today only `TransferContract` is read) | Fact. A delegator that is not mine is read first (one TronGrid page): a full page of 200 or a label make it a hub, as rental services are, and no link is stored | link 0.8 |
| 3 | **Deposit address shared, exchange exposure, Tether freezes** | 2.2 #2, #3, #4 on USDT-TRC20 | Most exchange flows on Tron are USDT | GraphSense Tron labels, Tether's `AddedBlackList` events on TronGrid; deposit-address candidates read as in 2.2 #3 (three TronGrid calls each) | As 2.2 | link + finding |

Account permissions (owner or active keys set to another address) are facts but rare: deferred.

### 2.6 Litecoin, Dogecoin, Bitcoin Cash

All of am-i-exposed's generic UTXO heuristics (1.2), run by its own engine with a chain
profile, on address and transaction scans. Bitcoin-only checks are off. Account keys of these
chains (Ltub, dgub) stay out, as in the tracker (README, Known limits). The entity index is
replaced by the tracker's labels (GraphSense has Litecoin and Bitcoin Cash) and the per-chain
OFAC lists. Litecoin's MWEB peg-in and peg-out are shown as
information. A CashFusion detector for Bitcoin Cash is left for later: no public rule
recognises its unequal outputs reliably.

### 2.7 Overall ranking

1. **Linked to a public wallet**: the exposure that matters most; it exists, and becomes the
   first finding of every wallet's score.
2. **Exchange exposure**: facts, on every chain, and the party most likely to be asked.
3. **Exchange deposit address shared**: strong and cheap, on EVM, Tron, Bitcoin, Litecoin
   and Bitcoin Cash.
4. **Bridge records**: facts that tie chains together, read without any new party.
5. **First funder and activator**: facts, the first thing a Sybil hunter looks at.
6. **Name records**: facts, and a name undoes every other precaution.
7. **Sanctions and stablecoin freezes**: facts that protect the funds themselves.
8. **Solana fee payers and co-signers**: facts, Solana's common inputs.
9. **Litecoin, Dogecoin, Bitcoin Cash in the scanner**: proven heuristics on three more
   chains.
10. **Cross-chain origin and the observer's view**: the above, followed and grouped.
11. **Mixer anonymity set**: a bounded guess that matters to few users.
12. **Tron delegation, NFT transfers**: facts, but rarely the only link.
13. **Approvals (security) and activity hours (estimate)**: useful context, little weight in
    the score.

### 2.8 Left out, and why

| Check | Why it is not built |
|---|---|
| Wallets holding the same NFTs or airdrops | Anyone can send them to anybody: a stranger can create the "link". The tracker already ignores unasked tokens for this reason. |
| Gas price, nonce or wallet-software fingerprints on EVM | Since EIP-1559 most wallets use the same fee logic; the signal is weak and would tie strangers. |
| Same time zone as a link between wallets | Millions share a time zone. Kept only as an estimate about one wallet (2.2 #5). |
| Same dApps or Solana programs as a link | Everyone uses the same few DEXes. |
| Amounts matched through a mixer | Fixed denominations make amounts meaningless (README, Known limits). The anonymity-set bound (2.3 #5) is the honest version. |
| Dust or poisoning transfers received as a link | Sent by attackers to strangers; already filtered as zero-value or unasked. |
| Wallet fingerprints, Whirlpool and Bitcoin-only protocols on other UTXO chains | They do not exist there; on Bitcoin Cash the RBF finding would be false. |
| LayerZero/Stargate recipient decoding | The recipient sits in an encoded message, not in the event; the amount-and-time bridge match covers it until a reliable decoder exists. |
| Dune Spellbook exchange addresses | Business Source License, not an open data set. |

## 3. Cross-chain checks

### 3.1 Bridge records

Today a bridge is matched by amount and time between two of my wallets (`match_bridges`,
0.5–0.95). Most bridges name the recipient in the event they emit on the source chain, as
THORChain does in its memo. Reading it makes the link a fact and also finds bridges to an
address that is not declared yet:

| Bridge | Event on the source chain | Recipient field | Destination |
|---|---|---|---|
| Circle CCTP (v1, v2) | `DepositForBurn` | `mintRecipient` (bytes32: an EVM address, or on Solana a token account) | `destinationDomain` |
| Across v3 | `FundsDeposited` / `V3FundsDeposited` | `recipient` | `destinationChainId` |
| Wormhole Token Bridge | `LogMessagePublished` | `to` in the transfer payload (on Solana a token account) | `toChain` |
| OP Stack (Base, Optimism) | `ETHDepositInitiated`, `ERC20DepositInitiated` | `to` | the L2 |
| Arbitrum | gateway router deposit | `to` | Arbitrum One |

Only the transactions my wallets sent to a bridge contract are read again, as receipts
(`eth_getTransactionReceipt` through the indexer's `proxy` module, which Routescan serves
without a key, or the chain's RPC): no third party learns anything new. Each record is
stored like a THORChain swap (the `swap` table already has protocol, sender, recipient,
tx_in, tx_out) and becomes a link `bridge-record` of 0.95. The amount-and-time match stays
for bridges without a decoder.

On Solana, CCTP and Wormhole name the recipient's token account, not the wallet. Its owner
is read with one `getAccountInfo` (`jsonParsed`, `info.owner`) on the Solana RPC that
already reads my wallets (`CT_SOLANA`), so no new party. A token account closed since has
no owner to read: the record is kept without a link and says why.
Solana-side origins (Wormhole, CCTP from Solana) can be looked up on Wormholescan by
transaction (free, no key, checked); that sends the transaction id out, so `CT_SWAPS=off`
turns it off, like the THORChain and Maya lookups.

### 3.2 One person across chains: what an observer sees

The verdicts list pairs. What users ask is "which of my wallets look like one person?". A
new view groups my wallets into the connected parts of the links of 50% or more (the alert
threshold), across chains: for each group, its wallets and chains, whether a public wallet is
in it, and the links holding it together, weakest first. When one link alone holds two parts
together, it says so ("without this exchange flow, these two groups are apart"), which is the
most actionable advice the tracker can give. It is computed from `graph.verdicts`, on the
server; nothing new is fetched.

### 3.3 Cross-chain funds origin

`graph.origins` follows money per chain and asset, so a swap or a bridge is an origin of its
own ("THORChain", "bridge"). With protocol records (3.1 and the existing swaps), an incoming
transfer whose transaction is a record's `tx_out` continues upstream from the record's
sender, on the source chain and in the source asset, sharing out the same proportion. The
path shows the hop ("via Wormhole"). It counts as one hop; a record whose sender is not read
ends the walk there, as today.

### 3.4 One privacy score per wallet, across chains

The server computes it, once, for everything that shows it or alerts on it, so the grade is
the same on every device and equals the one alerted on. The formula is am-i-exposed's address
score (`score.ts`: base 93, impacts summed and clamped, the same grade bounds), ported in a few
lines of Python; one shared fixture of findings is scored by both versions in their tests.

It scores:

- the wallet's own findings: linked to a public wallet (impact scaled by the link's
  confidence), exchange exposure, deposit address shared. A wallet in a group with a public
  wallet (3.2) is always linked to it through the verdict's chained links, so that finding
  already scores it, at the chained confidence; a second finding for the group would count
  the same link twice;
- the server-side findings of its addresses, on every chain, read from stored data: the
  checks of the counterparties once per chain over all the wallet's addresses (an HD wallet
  paid by one exchange from three addresses has one exchange finding, for three transfers),
  a family's checks (first funder, the same address on several chains) once per address,
  and for UTXO addresses their reuse. An exchange that first funded an address costs
  nothing more: its withdrawal is one of the transfers exchange exposure counts;
- each finding id once, with its worst impact across the wallet's addresses: one exchange
  exposure is enough to be exposed. Reuse is the exception, counted per address: −10 for
  each reused address, at most −40, where am-i-exposed's single-address score gives −70 to
  −93 and would make a whole HD wallet F for one slip;
- `partial` exactly when the verdict is not "full" (history filling in, a source down), or
  a check needs a call the page does not make: names a sync has not looked up yet, or a
  stranger who first funded two of my wallets and was never read. The address's own scan
  reads that stranger, and its answer is kept (a busy one as a hub, a quiet one in
  `quiet`), so the next grade is complete; the next syncs look the names up.

am-i-exposed's deep scan of a Bitcoin or Litecoin address (its 30-odd heuristics, run in the
browser) is not part of the wallet grade: the server cannot see it. It stays one click away,
and its own grade shows next to the address once it has run, labelled "deep scan". A Bitcoin
HD wallet that was never deep-scanned therefore has a full wallet grade, built from the
wallet-level and reuse findings and labelled "wallet checks; addresses not deep-scanned".

The score breaks down into its findings (`ScoreBreakdown`), so the number is never alone. An
alert is raised when the grade drops, from the same number the page shows.

### 3.5 How it fits the existing links, verdicts and graph

New links go into the `link` table with a kind of their own, so `graph.trace`, the verdicts,
alerts and both graphs use them without changes, and the existing rules hold: a known service
or a hub ends a path, a guessed one weakens it, and several pieces of evidence combine.

| Kind | Confidence | Rule | Combines as |
|---|---|---|---|
| `deposit-address` | 0.85 | Two of my wallets paid the same address that forwards only to one labelled exchange, was read in full (the candidate read of 2.2 #3), is not a hub, and received from no more than 3 senders | combine: one row per deposit address, so repeated payments to one address count once |
| `bridge-record` | 0.95 | A bridge event names the recipient (3.1) | `max`, like `swap` |
| `name` | 0.8 | A name's primary address lists another address (ENSIP-9) | `max` |
| `fee-payer` | 0.9 | Solana: one address paid the fee of another's transaction; a stranger only after its read (2.4) | combine |
| `co-signer` | 0.9 | Solana: two addresses signed one transaction; a stranger only after its read (2.4) | combine |
| `delegation` | 0.8 | Tron: resources delegated from one address to another; a stranger only after its read (2.5) | combine |
| `mixer` | ≤ 1 / (1 + deposits in between), at most 0.3 | Tornado deposit by one of my wallets, withdrawal to another, same pool. An upper bound: the real anonymity set is every unspent earlier deposit, so the cap keeps it below the alert threshold on its own | `max` |

**Precondition for every new kind.** A link is stored only between two of my wallets, or
between one of mine and a stranger that has been read and is neither busy nor labelled. A
busy stranger, or one labelled as a service by a sure source (the rule `graph.trace` applies:
a guessed label weakens a path and does not cut it), is stored as a hub (`node.hub`), which
`graph.trace` already treats as the end of a path. A guessed label or a person's name is
read like any stranger. This matters because `hub` is only ever set by reading an address, and a sync reads
strangers only on the deep chains (`chains.DEEP`) and only when asked for depth: a Solana fee
payer or a Tron delegator that nobody read would otherwise join two strangers' wallets (two
links of 0.9 through one relayer give 0.9 × 0.9 × 0.7 = 0.57, above the alert threshold).
Each check that creates such links runs M1's busy check on the stranger first, inside the
sync's budget.

Transfers that become readable (NFTs between my wallets, rent refunds booked to the owner)
are transfers, worth 1.0, under the existing "trusted token" rule. First funders and
activators are already transfers; the new findings name them.

The graph gets a colour and dash per new kind (`web/src/lib/tracker/kinds.ts`, `LINKS` and
`DASHES`) and a legend entry; the README's "What is a fact and what is a guess" table gets
one row per kind.

## 4. Architecture

### 4.1 The finding contract

Every check, in the browser or on the server, ends as am-i-exposed's `Finding`. The server
sends ids and parameters, never sentences:

```json
{
  "id": "x-exchange-exposure",
  "severity": "high",
  "scoreImpact": -15,
  "confidence": "deterministic",
  "params": {"exchange": "Binance", "count": 3, "chain": "ethereum"},
  "evidence": [{"chain": "ethereum", "txid": "0x..."}],
  "adversaryTiers": ["kyc_exchange"],
  "temporality": "historical"
}
```

- `Finding` requires `title`, `description` and `recommendation`, which the adapter below
  fills. `evidence` is not in upstream's type: `TrackerFinding = Finding & { evidence }` in
  `web/src/lib/tracker/types.ts` carries it, and upstream's components ignore it.
- The server also sends the score (3.4) with the findings, in `ScoringResult`'s shape
  (`score`, `grade`, `findings`, `partial`), so the page never computes its own.
- Ids of tracker checks start with `x-`, so they never collide with upstream's.
- Their texts (`finding.<id>.title`, `.description`, `.recommendation`) live in a tracker
  namespace, `tracker-exposure`. A small adapter in `web/src/lib/tracker/` fills `title`,
  `description` and `recommendation` from it before the findings reach upstream's
  components, which then show them through their English-default fallback. No upstream file
  needs the new ids.
- Impacts and severities are constants in one Python module, the way `heuristics.py` holds
  the link confidences, so the README and the legend can quote them.

### 4.2 UTXO chains in am-i-exposed's engine

A `ChainProfile` replaces the Bitcoin constants the engine reads:

```ts
interface ChainProfile {
  id: "bitcoin" | "litecoin" | "dogecoin" | "bitcoincash";
  unit: string;                       // BTC, LTC, DOGE, BCH
  blockSeconds: number;               // 600, 150, 60, 600
  dust: number;                       // base units below which an output is dust
  roundMultiples: number[];           // round-amount thresholds
  features: Set<"segwit" | "taproot" | "rbf" | "bitcoin-wallets" | "entity-index">;
  address: { bech32?: string; base58: number[]; cashaddr?: boolean };
  api: string;                        // Esplora-shaped base URL
  explorer: { address: string; tx: string };
}
```

- Registry entries name the features they need; the engine skips the others.
- Thresholds come from the profile instead of `lib/constants.ts`.
- `detect-input.ts` asks the profiles which chain an address belongs to; `#addr=` gains an
  optional `&chain=`.
- Bitcoin keeps its own backend setting (mempool.space, Umbrel, Tor, a custom URL, the local
  mempool of OLYMPUS-292). Litecoin, Dogecoin and Bitcoin Cash go through the tracker's
  server: `/api/tracker/utxo/<chain>/...` answers the Esplora calls the engine makes
  (address, its transactions, UTXOs, a transaction, outspends, tip height), passing Litecoin
  through to `CT_LITECOIN` and converting Dogecoin and Bitcoin Cash with the converters
  `chains.py` already has. One place for indexer URLs, rate limits and a later local node.
- This is the one part of the design that changes upstream files beyond a hook. It is kept
  to the profile threading and the gating, and listed in the README's merge notes.

### 4.3 Account chains on the server

- `tracker/exposure/` package: `__init__.py` holds the check registry per family (`evm`,
  `solana`, `tron`, and chain-agnostic checks that read stored transfers), a `run()` that
  builds one context and calls the checks, and the impact table. Each check is one module,
  so parallel issues touch different files.
- The context reuses what exists: `chains.fetch` / `lookup.view` for a live read,
  `history`-filled transfers for my own wallets, the label table, `tokens` judgments, the
  verdicts. A check may make extra calls (a first funder, an `eth_call`) only through the
  context's budgeted client (4.6).
- `GET /api/tracker/exposure/<address>?chains=` scans any address. `GET /analysis` gains the
  findings and score of each of my wallets, computed from stored data plus what syncs cache
  (names, approvals, funders), so opening the wallet page costs no API call.
- New configuration follows the existing pattern: a public default, overridable per chain in
  `CT_CHAINS` (`"rpc"` for `eth_call` and logs, next to `"url"`), env vars for the rest. That
  is also the hook OLYMPUS-292 asked for: a local node for another chain is one URL.

### 4.4 Reuse of the Python backend

| Need | Reused from |
|---|---|
| History, balances, tokens of any address | `chains.fetch*`, `lookup.view` |
| Full history of my wallets | `history.py`, the `transfer`, `sent` tables |
| Exchange, mixer, flagged, bridge labels | `labels.py` (new sets: OFAC per chain, exchange proof-of-reserves lists, stablecoin freezes) |
| Deposit-address guess | `detect.behaviour`, tightened |
| Protocol records | `swaps.py` pattern and `swap` table, for bridges |
| Links, verdicts, origins, alerts | `graph.py`, `alerts.py`, extended by new link kinds |
| HTTP, retries, pacing, safe error text | `chains.get`, `chains._rpc`, `chains.describe` |

### 4.5 Caching

- **My wallets**: everything stays in SQLite, read once and kept, as today (names and
  approvals in new tables, refreshed with each sync; bridge records in `swap`).
- **Addresses that are not mine**: a lookup or scan stores nothing (the README's promise).
  Their raw reads are kept in memory only, for 10 minutes, at most 200 addresses, so a scan
  right after a lookup costs nothing.
- **Public reference data**: label sets and the OFAC and exchange lists every 30 days, as the
  label sets today; per-pool mixer event counts kept by block range, since they never change.
- **Browser**: am-i-exposed's IndexedDB caches as they are, keyed by chain as well as backend
  (`cache-policy.ts` prefixes `network@baseUrl` already).

### 4.6 Rate limits

- All calls go through `chains.get` / `chains._rpc`, with a minimum interval per host
  (Routescan without a key allows 2 calls a second and 10,000 a day; NodeReal's pacing and
  BlockCypher's hourly cooldown stay).
- A scan of an address that is not mine has a budget (about 60 calls). When it runs out, the
  result is `partial` and says which checks did not run, the way a verdict says "Partly
  checked".
- A per-day quota that runs out (Routescan's) is counted, and the "partly checked" note names
  it, so a user sees why a check waits.

### 4.7 The UI

- **Scan page.** The input box takes any address the tracker reads. Bitcoin works as today;
  Litecoin, Dogecoin and Bitcoin Cash run the engine with their profile; an EVM, Solana or
  Tron address opens the account scan, rendered with upstream's `GradeDial`, `FindingsList`
  and `ScoreBreakdown`, with the chain's mark. (Not `VerdictBand`: its top recommendation
  is Bitcoin advice, from `selectRecommendations`; M1 gives the scan a header of its own.) An EVM address asks for its
  chains, as the lookup does. Upstream's routing changes by a few lines (send what it calls
  invalid to the tracker's detector first); the account scan lives in
  `web/src/app/tracker/` and `web/src/components/tracker/`.
- **Wallet pages.** Each wallet shows its score and grade next to its verdict, and its
  findings by chain. "Privacy score" works for every chain and opens the scan.
- **Portfolio** shows each wallet's grade; **alerts** fire when a grade drops.
- **Graph.** New link kinds drawn like the existing guesses; the groups of 3.2 can be
  highlighted; a node's panel lists its findings.
- **Consistency rules.** One score scale and grade colours everywhere (upstream's); "partly
  checked" wherever data is missing; facts drawn solid, guesses dashed with their confidence;
  no page says "safe".

### 4.8 i18n

The six locales (en, es, pt, de, fr, pl) as today. Every issue ships its strings in all six,
in tracker namespaces (`tracker-exposure` for findings, the existing ones for the rest), so
upstream's `common.json` stays untouched. `web/src/lib/tracker/__tests__/locales.test.ts`
already enforces the same keys in every language, Polish plural forms, matching placeholders
and no missing key; the new namespace is added to `TRACKER_NS`. Keys are added in
alphabetical order, so parallel issues rarely touch the same lines. Chain names, amounts and
dates go through parameters and `Intl`, never concatenation. Server-side English sentences
are not added: the server sends ids and parameters.

### 4.9 Privacy of the analysis itself

Every new call is listed in the README's Privacy section with what it reveals. The design
avoids new parties where it can: bridge records come from receipts at the indexer that
already serves the wallet; Solana token-account owners from the same Solana RPC; label and
sanction lists are downloaded whole.

- **`eth_call`** (names, allowances) goes to the indexer that already reads the wallet when it
  serves `eth_call` (Routescan for Ethereum and Avalanche, Etherscan), else to the chain's
  `rpc` in `CT_CHAINS` (your node), else to a public RPC such as publicnode. That public RPC
  is a new party that sees each of my EVM addresses it is asked about; the README says so,
  and `CT_LABELS=off` turns name lookups off as it does contract names.
- **Wormholescan** (Solana-side bridge records only) sees transaction ids; `CT_SWAPS=off`
  turns it off, like the THORChain and Maya lookups.
- **SNS names** are read from the name service's accounts at the Solana RPC that already reads
  the wallet (`CT_SOLANA`), so no new party; Bonfida's proxy is not used (M5).
  `CT_LABELS=off` turns it off.

## 5. The split

Each issue is user-visible behaviour, mergeable and verifiable on its own, on a branch from
`feat/multichain`. Every one includes: Python tests with recorded API responses (no network),
web tests where it touches the UI, its strings in the six locales, the README (Chains,
Privacy, the fact-and-guess table) and a review before it is merged.

### Stage 2

**M1. Privacy scan for EVM, Solana and Tron addresses** (foundation)
- The scan page accepts an EVM, Solana or Tron address and shows a score, grade and findings
  with upstream's result components; an EVM address asks for its chains.
- "Privacy score" in the tracker works for those chains and opens that scan.
- Checks: exchange exposure (2.2 #2), flagged and mixer counterparties from the existing
  labels (2.2 #4, labels only), same address on several EVM chains (2.3 #3).
- Delivers the finding contract (4.1), `tracker/exposure/` with its registry, impact table
  and score (the Python port of `score.ts`, tested on one fixture shared with a vitest),
  `GET /api/tracker/exposure/<address>`, the adapter, the `tracker-exposure` namespace, the
  in-memory cache, the scan budget, and the busy check of a stranger (3.5) that M3, M4, M9
  and M14 use: one Solana `getSignaturesForAddress` (limit 1000), one TronGrid page, or
  `sync.sync_address` elsewhere; a busy stranger, or a service labelled by a sure source,
  is stored as a hub.
- Done when: a known exchange-funded test address shows the exchange finding with evidence
  links; a partial read says "partly checked"; nothing is stored for a scanned address
  (test); the busy check stores a Solana address with 1000 signatures as a hub and one with
  10 as nothing (tests); the locale test passes with the new namespace.
- Depends on: nothing. Parallel: alone in its stage.

### Stage 3 (all depend on M1 only, unless said)

**M2. One privacy score per wallet, across chains**
- Each wallet card and panel shows a grade and its findings by chain; the portfolio shows
  grades; a node's panel in the graph lists the findings of that wallet; an alert fires when
  a grade drops. Rules of 3.4, including `partial`, computed on the server.
- Done when: a wallet linked at 70% to a public one loses the stated impact; a wallet with a
  history still filling in reads "partly checked"; a Bitcoin-only HD wallet never
  deep-scanned shows a full wallet grade labelled "addresses not deep-scanned", and one
  reused address costs it −10, not an F; the alerted grade equals the displayed one (test);
  opening the page makes no API call.
- Files: `tracker/exposure/` (wallet findings), `app.py` `/analysis`, `alerts.py`,
  `components/tracker/portfolio/`, the graph's node panel. Parallel: yes.

**M3. Shared counterparties: exchange deposit addresses, busy strangers**
- Each sync reads the unread, unlabelled addresses that two or more of my wallets dealt with,
  at most 10, busiest first (`sync.sync_address` on EVM, Tron, Bitcoin, Litecoin and Bitcoin
  Cash; on Solana the one-call busy check of 2.4; none on Dogecoin, whose quota is too
  small). Busy ones become hubs, so they stop paths, which today an unread Solana or Tron
  exchange wallet does not.
- New link `deposit-address` (3.5) and finding; tightened deposit-address guess.
- Done when: two wallets paying one deposit address are linked at 0.85 and alerted, on EVM
  and on a Tron USDT deposit address never read before; a hot wallet, a hub, an address with
  4+ senders or one that also pays anyone else never makes the link (tests for each); the
  eleventh candidate waits for the next sync.
- Files: new `tracker/deposits.py`, `detect.py`, one line in `sync.py`, `kinds.ts`.
  Parallel: yes.

**M4. First funder and activator**
- EVM first funder per chain, Tron activator, Solana first funder: shown in scans and wallet
  panels, naming the funder (your wallet, an exchange, a stranger who also funded another
  wallet of yours). A stranger funder is read first with the busy check of 2.4/2.5, so an
  unlabelled exchange hot wallet is not called "a stranger who funded both".
- Done when: own wallets use stored history (no call); other addresses take at most one call
  per chain; a CEX funder yields the exchange-exposure wording; a busy unlabelled funder of
  two wallets of mine is marked a hub and named as such.
- Files: `tracker/exposure/funder.py` only (its ascending read uses `chains.get`). Parallel:
  yes.

**M5. Name records: ENS, Basenames, SNS**
- Primary name (verified forward) and the other-chain addresses a name lists; finding and
  `name` link (0.8).
- Done when: a known ENS name is found and shown; a reverse record whose forward does not
  match is ignored; addresses listed on the name become links; `eth_call` goes to Routescan
  for Ethereum and to a self-hosted RPC when one is set in `CT_CHAINS`; `CT_LABELS=off`
  makes no name lookup.
- Data: Universal Resolver by `eth_call`; keccak-256 for namehashes: a small pure-Python
  implementation tested against known vectors (decided in OLYMPUS-298: no dependency for
  about 30 lines). Files: `tracker/exposure/names.py`, a `name` table. Parallel: yes.

**M6. Bridge records**
- CCTP, Across, Wormhole (EVM side), OP Stack and Arbitrum bridges decoded from receipts
  (3.1); `bridge-record` links; records in `swap`.
- Done when: recorded receipts of each bridge give the right recipient and destination chain;
  a recorded CCTP and a recorded Wormhole transfer to Solana resolve the token account to its
  owner wallet (one `getAccountInfo` each), and a closed token account gives no link; a
  bridge to an undeclared address shows in the wallet's verdict as a link to a stranger; no
  new host is called with `CT_SWAPS=off`.
- Files: new `tracker/bridges.py`, `sync.py`, `kinds.ts`. Depends on nothing (stage 3 to
  share the review load). Parallel: yes.

**M7. Litecoin, Dogecoin and Bitcoin Cash in the privacy scanner**
- The ChainProfile (4.2), gated heuristics, per-chain input detection, the Esplora-shaped
  proxy; "Privacy score" works for those chains.
- Done when: a Litecoin address and transaction scan with their profile; Bitcoin results are
  unchanged (upstream's test suite passes); no RBF finding on Bitcoin Cash; Dogecoin's quota
  shows as "partly checked" rather than an error.
- Files: upstream `lib/analysis/`, `lib/bitcoin/`, `detect-input.ts`; `app.py` route.
  Parallel: yes, but it shares upstream's routing hook with M1, hence after it.

**M8. Sanctions lists and stablecoin freezes on every chain**
- OFAC lists per currency, USDT and USDC freeze events (Ethereum, Tron), as flagged labels;
  used by the lookup's "check before sending", the scans and the verdict.
- Done when: a sanctioned ETH, TRX and SOL address shows as flagged; the lists refresh with
  the label sets; `CT_LABELS=off` skips them.
- Files: `labels.py`, `tracker/exposure/flagged.py`. Parallel: yes.

**M9. Solana links: fee payers, co-signers, rent refunds**
- `fee-payer` and `co-signer` links; rent refunds of closed token accounts booked to the
  owner. Signers are not in the transfer rows: the Solana reader returns them as `links`,
  as the UTXO reader returns `btc_links`, for snapshots and full reads (`history.py`).
- Done when: recorded transactions give the right links; a relayer that was never read
  before is read once, comes out as a hub and links nobody (test), and two wallets of mine
  that used it are not linked; existing Solana transfers are unchanged.
- Files: `chains.py` (Solana part), `history.py`, `kinds.ts`. Parallel: yes; shares
  `chains.py` with M14 and M15 (different functions).

**M10. Exchange labels for Solana and Tron**
- Exchanges' published proof-of-reserves addresses (OKX signs each; others where they
  publish lists) as an open label set, refreshed like the others.
- Done when: an exchange's Solana hot wallet is named in a lookup; if no list can be fetched
  without an account, the issue stops and reports instead of scraping.
- Files: `labels.py`. Parallel: yes.

### Stage 4

**M11. Cross-chain funds origin** (depends on M6)
- 3.3: origins follow swap and bridge records to the source chain and asset.
- Done when: a SOL wallet funded through Wormhole from an ETH wallet funded by an exchange
  shows that exchange as the origin, with the bridge hop in the path.
- Files: `graph.py` `origins`, origin panel. Parallel: yes.

**M12. What an observer sees: my wallets as people** (best after M3, M5, M6, M9)
- 3.2: groups, chains, public wallets in them, the links holding them, single links that
  hold a group together; highlighted in the graph.
- Done when: three wallets joined by two links of 50% or more form one group whose weakest
  link is named; a link below 50% splits them; a group with a public wallet is marked.
- Files: new `tracker/clusters.py`, `/analysis`, a panel, graph highlight. Parallel: yes.

**M13. Mixer use and anonymity set**
- 2.3 #5, finding and `mixer` link.
- Done when: a recorded deposit and withdrawal with few deposits in between give the bound,
  never above 0.3; a large pool gives no link; the pool scan is cached by block range.
- Files: new `tracker/mixers.py`. Parallel: yes.

**M14. Tron resource delegation links**
- `delegation` links; rental services end paths.
- Done when: a recorded delegation between two wallets of mine links them at 0.8; a rental
  service never read before is read once, comes out as a hub and links nobody (test); Tron
  transfers are unchanged.
- Files: `chains.py` (Tron part). Parallel: yes.

**M15. NFT transfers between my wallets**
- Read `tokennfttx` and `token1155tx` for my wallets; NFTs from strangers ignored.
- Done when: an NFT moved between two wallets of mine links them as a transfer; an NFT
  airdropped by a stranger draws no edge; the history bookmarks of the new lists fill in
  like the others.
- Files: `chains.py` (EVM lists), `history.py`. Parallel: yes, small conflict risk with M14
  (both in `chains.py`, different parts).

**M16. Open token approvals and activity hours**
- 2.3 #7 (security group, impact 0) and 2.2 #5 (estimate). Two small findings sharing a
  wallet-panel section.
- Done when: a recorded unlimited approval still open is listed with its spender's label,
  and one revoked since is not; activity hours need 20 sent transactions and say how many
  they rest on; neither changes the privacy score beyond its stated impact.
- Files: `tracker/exposure/approvals.py`, `tracker/exposure/hours.py`. Parallel: yes.

### Order and parallelism

| Stage | Issues | Notes |
|---|---|---|
| 2 | M1 | Foundation, small: three findings on existing data, and the busy check stage 3 shares. |
| 3 | M2, M3, M4, M5, M6, M7, M8, M9, M10 | Each in its own module; shared files (`kinds.ts`, `sync.py`, the locale files, `app.py`) get one-line or alphabetical additions. M9 is the only one in `chains.py`. |
| 4 | M11, M12, M13, M14, M15, M16 | M11 needs M6; M12 is most useful once stage 3 links exist. M14 and M15 both edit `chains.py`, in different functions. |

Expected conflicts are trivial: registry lines, `LINKS`/`DASHES` entries, locale keys and
`SCHEMA` lines. Whoever merges second rebases on `feat/multichain`.

## 6. Open points

### 6.1 To confirm during implementation

- Routescan without a key serves `logs`, and the `proxy` receipt and `eth_call` calls (all
  three checked); the default public RPC per EVM chain where the indexer has no `eth_call`
  (publicnode answers for Ethereum).
- Circle's and Wormholescan's APIs answer without a key (both answered here); they are only
  needed for Solana-side records.
- That at least one exchange's proof-of-reserves list can be downloaded without an account
  (M10).
- Score impacts of the new findings: the values in M1's table are a first proposal, to be
  tuned on real wallets during M2's review.

### 6.2 Deferred

Smart-account owners and deployers (Safe), `.bnb` names, Tron account permissions,
CashFusion detection, LayerZero/Stargate recipient decoding, local nodes for chains other
than Bitcoin (only the URL hook is built), a cross-chain transaction graph in upstream's
graph explorer (it is UTXO-shaped; the tracker's graph covers cross-chain).
