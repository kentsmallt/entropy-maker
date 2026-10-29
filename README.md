# entropy-maker

Post-only market maker for [Entropy](https://app.entropy.trade) markets on Hyperliquid (`io:ANTH` and the other reviewed `io:` perps). It quotes one bid and one ask with `tif: "Alo"` (add-liquidity-only). It does not cross the spread, and it does not send IOC or market orders.

**Paper mode is the default.** Nothing is signed and no order is sent unless live mode is armed with an exact set of flags. If those flags disagree, the process exits and sends nothing.

This is not a Hyperliquid or Entropy product, and it is not financial advice. Quoting can lose money. You can lose the collateral you post.

## What it will not do

- No wash trading and no self-trade. One account, one order per side, and the bid is kept strictly below the ask. An exchange status of `selfTradeCanceled` halts the process.
- No spoofing. Quote size is the configured size (or the remaining size after a partial). Orders are not inflated, and a price change inside `ENTROPY_MIN_REST_SECONDS` does not cancel them. Risk pulls (stale book, shutdown, a foreign order) may cancel immediately.
- No volume farming. There is no second account, no self-match helper, and no cancel/replace loop whose purpose is to print volume.
- No taker fallback. If an ALO price would trade immediately, that side is moved one tick away or dropped. It is never rewritten as IOC or GTC.
- No master-wallet signing. Live mode signs with a separate API wallet. If that address is the account address, startup fails.

## Paper and live

| | Paper (default) | Live |
|---|---|---|
| Flags | `ENTROPY_DRY_RUN=1` (or unset) and `ENTROPY_LIVE=0` | `ENTROPY_DRY_RUN=0`, `ENTROPY_LIVE=1`, and `ENTROPY_LIVE_CONFIRM` equal to the coin, together |
| Keys | not read | API wallet only, from a mode `0600` file |
| Orders | simulated in the state file | signed ALO orders |
| Fills | only when the opposite best price trades through the resting price | exchange fills |
| Failure | exits or pulls quotes | same, and a timeout is not retried |

Any other combination (`DRY_RUN=0` alone, `LIVE=1` while dry-run is still on, confirm coin mismatch) is a hard error.

Paper fills are a drill, not a forecast. Joining the touch is not treated as a fill. A locked or crossed book cancels quotes instead of inventing a self-fill.

## Risk gates

The process pulls its own quotes, and does not place new ones, when any of these are true:

- The book is missing, one-sided, locked, crossed, or older than `ENTROPY_MAX_BOOK_AGE_SECONDS` (local receive time and the exchange timestamp).
- Mid and mark disagree by more than `ENTROPY_MARK_DEVIATION_BPS`.
- Another open order on the selected coin is not tagged with this process's cloid. Those orders are not canceled.
- A tagged order is on the book but missing from the state file (`orphan_tagged_order`). Run `cancel-ours` after checking it.
- Available equity is below `ENTROPY_MIN_NOTIONAL_USD`.
- The position plus one more order would pass `ENTROPY_MAX_POSITION`. Only the reducing side stays up.
- Shutdown (`SIGINT` / `SIGTERM`).

Order size is in coin units, not dollars. Prices use Hyperliquid's perp rule (5 significant figures, and at most `6 - szDecimals` decimal places). Bids round down, asks round up, sizes round down.

After a timeout the same place or cancel is not sent again. The next step is a read: cloid status, open orders, then order history. If those reads still cannot prove the cloid, it is stored as uncertain and every further mutation stops. A cancel that is still open after `ENTROPY_RECONCILE_POLLS` successful reads also stops, without a second cancel.

Flattening uses the exchange position (paper: the simulated position), size `abs(position)` rounded down, `reduce_only=true`, and a new cloid. The price joins the touch only when that join would not take. It will not flip the position.

The reviewed coin list is in `entropy_maker.market.allowlist` (snapshot 2026-10-29, delisted names excluded). A coin that is later delisted, or that is not isolated, is refused even if the name is still listed. Adding a market is a code change.

`userDexAbstraction` / `userAbstraction` are logged in live preflight and are not treated as permission to trade.

## Run

Python 3.10+. Paper mode has no third-party packages.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# edit sizes. Do not put a private key in .env.
python -m entropy_maker preflight --env-file .env
python -m entropy_maker once --env-file .env
python -m entropy_maker status --env-file .env
python -m entropy_maker run --env-file .env
```

`preflight` only reads the public `metaAndAssetCtxs` and `l2Book` endpoints. `once` and `run` default to paper. `status` prints the state file. `cancel-ours` in paper mode only clears that file.

Useful variables (see `.env.example`):

- `ENTROPY_COIN` — default `io:ANTH`
- `ENTROPY_ORDER_SIZE`, `ENTROPY_MAX_POSITION` — coin units; both required before quoting
- `ENTROPY_HALF_SPREAD_BPS`, `ENTROPY_SKEW_BPS`, `ENTROPY_REPRICE_BPS`
- `ENTROPY_MIN_REST_SECONDS`, `ENTROPY_MAX_BOOK_AGE_SECONDS`
- `ENTROPY_STATE_FILE` — default `.entropy-maker/state.json`
- `ENTROPY_BOOK_SOURCE` — `rest` (default) or `ws` (`pip install -e ".[ws]"`)

State is written by temp file, fsync, and rename. The file records mode and coin; a later process that does not match both refuses to start.

### Live

Install the signer, create an API wallet in the Hyperliquid UI, and approve it on the master account. Put the API private key in a separate file:

```bash
pip install -e ".[live]"
install -m 600 /dev/null /etc/entropy-maker/agent.key
# paste the API wallet key into that file, then:
chmod 600 /etc/entropy-maker/agent.key
```

Arm live mode only when you intend to send orders. All three must match:

```bash
ENTROPY_DRY_RUN=0
ENTROPY_LIVE=1
ENTROPY_LIVE_CONFIRM=io:ANTH
ENTROPY_ACCOUNT_ADDRESS=0xYourMasterOrSubaccount
ENTROPY_AGENT_SECRET_FILE=/etc/entropy-maker/agent.key
```

Then:

```bash
python -m entropy_maker preflight --env-file /etc/entropy-maker.env
python -m entropy_maker once --env-file /etc/entropy-maker.env
python -m entropy_maker cancel-ours --env-file /etc/entropy-maker.env
```

Live `preflight` checks the agent address, name (if `ENTROPY_AGENT_NAME` is set), and that `validUntil` is at least `ENTROPY_AGENT_MIN_TTL_SECONDS` away. It still sends no order. `cancel-ours` cancels tagged cloids once and does not touch anyone else's orders.

`deploy/entropy-maker.service` runs `entropy-maker run` and calls `cancel-ours` on stop. `deploy/entropy-maker-watchdog.service` is a oneshot with the same cancel. Point both at an env file outside the git checkout. Do not commit that file.

If the process halts, read `halt_reason` with `status`. `reconcile` re-reads the exchange in live mode and will not clear a halt it still cannot prove. Fix the cause, then reconcile again. Deleting a live state file does not cancel orders that are already on the book; use `cancel-ours`.

## Layout

```
src/entropy_maker/
  config.py            paper/live gate
  quoting/             one post-only bid and ask
  risk/                inventory, foreign orders, stale book
  runtime/             planner, paper fills, reconcile, run loop
  exchange/            public /info client and the live signer
  identity/            cloid tag, agent check, secret file
  ops/                 preflight and cancel-ours
deploy/                systemd examples
tests/
```

The cloid tag is the four bytes `EMM1` (`0x454d4d31`). Logs redact 32-byte hex values. Do not pass the key on the command line.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests do not need network or a key. `preflight` and `once` do call the public Hyperliquid info API when you run them yourself.

## License

MIT. See [LICENSE](LICENSE).
