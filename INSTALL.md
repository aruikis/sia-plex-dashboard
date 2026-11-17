# Installing the dashboard on the basement M1 (do this with Anthony present)

The dashboard runs **on the M1** — that's the only machine that can reach
hostd's localhost API, and it can reach Plex on the M4 across the LAN.

Nothing here installs anything system-wide: it's a folder, a config file,
and one Python command. Python 3 ships with macOS; there is nothing to
pip-install (standard library only).

## 1. Copy the folder to the M1

Copy the whole `sia-plex-dashboard/` folder to the M1, e.g. to
`/Users/anthonyruikis/sia-plex-dashboard`. (AirDrop, a USB stick, or
`scp` from the M4 all work.) Don't copy `dashboard.db` if one exists in
the source folder — the M1 creates its own.

## 2. Create the config (Anthony types the secrets himself)

In Terminal on the M1:

```sh
cd ~/sia-plex-dashboard
cp config.example.json config.json
open -e config.json
```

Anthony fills in the two placeholder values, in `config.json` only:

- `hostd_api_password` — the hostd API password (the one set when hostd
  was configured on the M1; the same password that unlocks hostd's web
  UI at http://localhost:9980).
- `plex_token` — the Plex token for the RuikisFlix server. One way to get
  it: in Plex Web on the M4, pick any library item → ⋯ menu → **Get Info**
  → **View XML**; the token is the `X-Plex-Token` value in that page's
  URL. (It's a server token — treat it like a password.)

Leave the URLs and intervals as they are. `config.json` is git-ignored;
never commit it, paste it into chat, or send it anywhere.

## 3. Start it

```sh
cd ~/sia-plex-dashboard
./start.sh
```

(Equivalent: `python3 app.py`.) You should see
`dashboard on http://0.0.0.0:8899`. Leave that Terminal window open —
closing it (or Ctrl+C) stops the dashboard. hostd itself must also be
running for the Sia tab to light up; if it isn't, the tab calmly shows
"node is not responding" until hostd starts — that's expected.

Optional sanity check before the real config exists — run it against the
built-in fake data instead:

```sh
./start.sh --mock
```

## 4. Open it

From Anthony's phone or the M4, on the home Wi-Fi:

**http://192.168.1.86:8899**

The Sia tab should show hostd v2.12.1, a green sync light, and the four
storage volumes (volume 6 / Seagate 20 will flag UNAVAILABLE until that
drive situation is sorted — that flag is the dashboard doing its job).
The Plex tab should show RuikisFlix and anything currently playing.

## 5. Stop it

Ctrl+C in the Terminal window running `start.sh`. Nothing else to undo;
deleting the folder removes the whole install (its history lives in
`dashboard.db` inside the folder).

## Notes

- **Do not port-forward 8899.** The page has no login; it's LAN-only by
  design (see README).
- Keep the M1 awake while the dashboard should be collecting history —
  it already stays up for hostd, so this is normally automatic.
- If a panel shows "—" everywhere while the node is clearly up, the
  hostd response shape may differ slightly from the fixtures; grab one
  real response (e.g. `curl -u :PASSWORD http://127.0.0.1:9980/api/state`)
  and compare against `fixtures/` — the parsers are tolerant, but a
  renamed field would need a one-line fix.
- Running it automatically at login (a launchd agent) is deliberately
  **not** part of v1 — add it in phase 2 once the manual run is proven.
