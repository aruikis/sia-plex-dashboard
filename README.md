# Sia + Plex Dashboard

One web page with two tabs — **Sia Node** and **Plex** — for watching
Anthony's two home servers:

- the **Sia storage host** (`hostd` v2.12.x) on the basement Mac mini M1
  (LAN `192.168.1.86`), and
- **Plex Media Server** ("RuikisFlix") on the Mac mini M4
  (LAN `192.168.1.114`, port 32400).

Built from the research in
`~/workspace/research_notes/sia-host-dashboard-research-20261117-1635/report.md`
(Option A: a small local poller rendering one page — no turnkey hostd-v2
dashboard exists, and hostd's own API already exposes everything needed).

## Hard rules baked into the design

- **Python 3, standard library only** (`http.server`, `sqlite3`, `urllib`,
  `json`). No pip installs; runs on a stock macOS Python 3.
- **Strictly read-only.** The poller only ever issues HTTP **GET** requests
  to hostd, Plex and Siascan. It cannot change state on either server, and
  it never touches the Plex library beyond reading section sizes.
- **Secrets stay on the machine.** The hostd API password and Plex token
  live only in `config.json` next to the app (git-ignored). They are used
  server-side by the poller and are never sent to the web page.

## Architecture

```
            +---------------- M1 (basement) ----------------+
            |  app.py  (this dashboard, port 8899)          |
            |    poller threads:                            |
            |      hostd    -> 127.0.0.1:9980   every 60 s  |
            |      plex     -> 192.168.1.114:32400 every 30s|
            |      siascan  -> api.siascan.com  every 15 min|
            |    store.py -> dashboard.db (SQLite)          |
            |    http.server -> index.html + /api/*         |
            +-------------------------------------------------+
   phone / M4 browser  --->  http://192.168.1.86:8899
```

- `parsers.py` — pure functions turning upstream JSON into normalised
  values; all derived math lives here (see below) and is unit-tested.
- `poller.py` — the three GET-only clients and poll loops. A failed poll
  never crashes anything: the source is marked **offline**, the error is
  recorded, and the last good data is kept and flagged **stale** (hostd is
  started by hand, so "node offline" is a normal state, not a bug).
- `store.py` — SQLite. Two tables:
  - `snapshots(ts, source, payload)` — one row per poll holding the
    *normalised* view model JSON (`source` = hostd / siascan / plex).
  - `history(ts, metric, value)` — flat series for the charts:
    `revenue_potential_sc`, `revenue_earned_sc`, `storage_used_bytes`,
    `wallet_balance_sc`, `plex_sessions`, `plex_bandwidth_mbps`.
  - Retention: snapshots pruned after 30 days, history after 90 days.
- `app.py` — config loading, the web server, `--mock` mode.
- `index.html` — the whole UI: dark theme (near-black, Anthony dislikes
  bright backgrounds), two tabs, inline-SVG charts, no framework, no
  external resources. It polls `/api/sia` and `/api/plex` every 15 s and
  `/api/history` every minute.
- `mockserver.py` + `fixtures/` — recorded-shape JSON fixtures and a tiny
  server that answers the same URL paths, so the full stack runs with no
  real services: `python3 app.py --mock`.

### JSON endpoints

| Endpoint | Returns |
|---|---|
| `GET /` | the page |
| `GET /api/sia` | latest Sia view model, with a `network` block (Siascan) merged in |
| `GET /api/plex` | latest Plex view model |
| `GET /api/history?metric=X&hours=N` | `{metric, points: [{ts, value}]}` for the chart metrics above |

### What the Sia tab shows

- **Health:** hostd version, uptime, peer count, accepting-contracts, last
  announcement, all active hostd alerts by severity, and the **sync
  traffic light** — consensus tip vs index tip vs Siascan network height
  (synced ≤ 2 blocks apart, catching up ≤ 10, stalled beyond that).
- **Money:** wallet spendable/confirmed/unconfirmed/immature; revenue
  **potential vs earned**, each split storage/ingress/egress/RPC; locked +
  risked collateral; contract counts (active/successful/renewed/rejected/
  failed); SC price in USD and the dollar value of the balance + potential.
- **Prices:** Anthony's configured prices **next to the Siascan network
  medians**.
- **Storage:** per-volume used/total, % full, read-only, status, successful
  vs failed reads/writes and errors. A volume with `available=false`
  renders as a loud red **UNAVAILABLE** card. Lost sectors are highlighted
  red whenever the count is above zero.

### What the Plex tab shows

Server name/version/platform, a static note about the verified remote-access
setup, library section item counts, **now playing** (user, title, client,
Direct Play / Direct Stream / Transcode, per-stream bandwidth), total
bandwidth right now, and a streams-over-time chart from stored snapshots.

## Derived math (from the research report)

- Currency: hostd serialises Siacoin amounts as **hastings**;
  1 SC = 10²⁴ hastings.
- Storage: **1 sector = 4 MiB**; bytes = sectors × 4 MiB.
- Prices: the API's storage price is **per TB per block**; per-month ≈
  per-block × **4320** blocks. Ingress/egress prices are per TB outright.
  Siascan's median prices use the same convention and get the same
  conversion, so the two columns are comparable.

## Network exposure note

The server binds all interfaces on port **8899** so Anthony can open it
from his phone or the M4. That is safe **only** because of his network
design: 8899 is **not** port-forwarded on the router, so the dashboard is
reachable from the home LAN only. Do not add a port forward for it — the
page has no login, by design, for home use.

## Endpoint-shape assumptions

The research could not cross-check hostd's rendered OpenAPI spec, so the
parsers are deliberately tolerant (case-insensitive keys, currency as
number-or-string, stats flat-or-nested). The specific assumptions are
listed in `BUILD-REPORT.md`. If a panel ever shows "—" after install, the
first step is comparing the real response shape against `fixtures/`.

## Tests

```
python3 -m unittest discover -s tests -t .
```

23 parser unit tests (derived math, sync logic, every fixture) + 6
end-to-end smoke tests (the real app in `--mock` mode, asserting fixture
values over HTTP). See `BUILD-REPORT.md` for the current counts.
