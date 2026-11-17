# Build report — Sia + Plex dashboard v1

Date: 2026-11-17. Status: **built and tested; NOT installed anywhere.**
Nothing was installed on any Mac and no live service was touched — all
verification ran against fixtures or dead ports on this VM.

## What was built

Single-process Python 3 app (standard library only): three GET-only poller
loops (hostd 60 s, Plex 30 s, Siascan 15 min), SQLite snapshot + history
storage, and an `http.server` web layer on port 8899 serving one
dark-themed page with two tabs (Sia Node / Plex) plus `/api/sia`,
`/api/plex`, `/api/history`. Design follows the research report's
Option A. See README.md for the full architecture.

## Files

| File | Purpose |
|---|---|
| `app.py` | entrypoint: config, web server, `--mock` mode, mock history seeding |
| `poller.py` | GET-only clients + poll loops + view-model assembly; offline/stale handling |
| `parsers.py` | pure parsers + all derived math (hastings→SC, sectors→bytes, ×4320 prices, sync light) |
| `store.py` | SQLite: `snapshots` + `history` tables, 30 d / 90 d retention |
| `mockserver.py` | fixture HTTP server used by `--mock` and the smoke test |
| `index.html` | the page (tabs, panels, inline-SVG charts, no libraries) |
| `config.example.json` | placeholder config; real `config.json` is git-ignored |
| `.gitignore` | excludes `config.json`, `dashboard.db`, `__pycache__` |
| `start.sh` | `./start.sh [--mock]` — CP Live-style launcher |
| `fixtures/` (16 files) | recorded-shape mock JSON for hostd (9), Siascan (3), Plex (4) |
| `tests/test_parsers.py` | 23 unit tests |
| `tests/test_smoke.py` | 6 end-to-end tests (real app subprocess in `--mock` mode) |
| `README.md`, `INSTALL.md` | architecture + the M1 install walkthrough for later |

## Test results

```
python3 -m unittest discover -s tests -t .
Ran 29 tests in 0.563s — OK (23 parser unit + 6 smoke)
```

Coverage highlights: sector→byte math (262,144 sectors = 1 TiB exactly),
hastings→SC, per-block→per-month price (0.424 SC/block → 1,831.68 SC/TB/mo),
potential-vs-earned split (185 / 440 SC from the fixture), sync-light
statuses (synced / catching_up / stalled / unknown), unavailable-volume
flagging, Siascan used = total − remaining (2.76 PB), Plex session modes
(Direct Play vs Transcode) and bandwidth sum (32.5 Mbps).

Extra manual checks beyond the suite:
- App pointed at three dead ports: `/api/sia` and `/api/plex` return
  `online: false` with the error recorded, page still serves 200 — the
  "hostd not started yet" case degrades gracefully.
- Mock mode eyeballed: fiat block present (125 SC ≈ $0.0073 at the
  fixture rate of $0.000058/SC — tiny because SC's price is tiny, not a
  math error), sync = synced (602208 / 602207 / 602209), 1 unavailable
  volume, aggregate used 17.4 TB.

## Endpoint-shape assumptions (from the research report; verify at install)

The report's endpoint list comes from hostd's Go API client, but the
rendered OpenAPI spec could not be cross-checked, so parsers are tolerant
(case-insensitive keys, currency as string-or-number, nested-or-flat
stats). Specific assumptions:

1. **hostd API prefix is `/api`** (configurable via `hostd_api_prefix`).
   Evidence: the real-world `curl 'http://localhost:9980/api/metrics?
   response=prometheus'` in the research. If the M1's hostd answers at
   bare paths instead, set the prefix to `""` — one-line config change.
2. **Auth** is HTTP Basic with empty username + API password (the Go
   client's scheme).
3. **Currency** fields serialise as decimal strings of hastings; numbers
   are also accepted.
4. **`/metrics`** nests `revenue{potential,earned}{rpc,storage,ingress,
   egress}`, `contracts{...}`, `storage{...Sectors}`, `data{rhp,syncer}`,
   `wallet{balance, immatureBalance}`.
5. **`/volumes`** returns a list of volume objects with stats either flat
   or under a `stats` key; `available`/`status` drive the UNAVAILABLE
   flag. Fixture mirrors the live case: volume 6,
   `/Volumes/Seagate 20/Sia/sia`, unavailable.
6. **Prices** (hostd `/settings` and Siascan alike): storage is per TB
   per block → ×4320 for per-month; ingress/egress per TB. Anthony's
   prices are read from `/settings`, falling back to `/metrics` pricing.
7. **Siascan**: `GET /metrics/host` (HostMetrics: `activeHosts`,
   `totalStorage`, `remainingStorage`, prices nested under
   `v2Settings.prices`), `GET /metrics/block` (height under `index`),
   `GET /exchange-rate/usd` returning a bare JSON number. The price-block
   search also tries `settings`, `priceTable`, `prices`, `pricing` nests.
8. **Plex** answers JSON when sent `Accept: application/json`; identity
   from `GET /` (`friendlyName`, `version`); sessions from
   `/status/sessions` with `Session.bandwidth` in kbps, transcode
   detected via `TranscodeSession` or the media part `decision`; section
   counts via `/library/sections/:id/all?X-Plex-Container-Size=0` →
   `totalSize`.

## Deliberately left for phase 2

- **External (off-LAN) watchdog** — the research's #1 standout idea
  (sia.watch pattern / UptimeRobot). A LAN dashboard cannot detect the
  node being dead from the internet's side; v1 doesn't pretend to.
- **Alert push** (Telegram-style relay of hostd's `/alerts`) — v1 shows
  alerts on the page only; nothing is pushed anywhere.
- **launchd auto-start** on the M1 — v1 is started by hand (INSTALL.md)
  until the manual run is proven.
- **Per-host Siascan page data** (`Host(pubkey)`: last scan, accepting
  state) and HostScore context — v1 uses network aggregates only.
- **`/system/dir` disk check** (Mac mini system disk filling is invisible
  to Sia metrics) — noted in the research, not yet polled.
- **Contract list detail** (`/v2/contracts`) — counts come from
  `/metrics`; no per-contract table in v1.
- History is real-time only (plus synthetic seed data in `--mock` demo
  mode); charts fill in as the poller runs.
