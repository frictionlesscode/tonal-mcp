# tonal-mcp

A self-hosted [MCP](https://modelcontextprotocol.io) server that gives Claude full
read/write access to your **Tonal custom workouts** — list, inspect, create, edit, and
archive them, straight from a chat. It's the write counterpart to the read-only data Claude
already gets from [garmin-mcp](https://github.com/frictionlesscode/garmin-mcp)/
[macro-mcp](https://github.com/frictionlesscode/macro-mcp): those hand back training and
nutrition data, this one lets Claude actually build the workout.

It's the data plane only. Exercise selection, programming philosophy, and periodization are
Claude's job (or yours) — this server's job is to turn a set list into a correctly-shaped
Tonal API call and hand back exactly what Tonal stored, honestly, with `null` where data
genuinely isn't available rather than a guess.

## Benefits

- **Build or edit a Tonal workout by describing it to Claude** — "make me a 30-minute upper
  body session with a superset of bench press and rows" — without opening the Tonal app's
  workout builder.
- **Full CRUD, not just reads.** `create_workout`, `update_workout`, and `delete_workout`
  (soft — Tonal archives, it doesn't destroy) are real writes against your account, alongside
  `list_workouts`/`get_workout`/`find_movement`/`estimate_workout_duration` for reading and
  planning.
- **No fabricated data.** Where Tonal's own API genuinely doesn't return something at a given
  call (e.g. set counts at list time, or a `sets` array for an archived workout), this server
  says so explicitly instead of guessing or silently substituting a wrong number — see
  [SPEC.md](SPEC.md)'s "Bug report findings" for two real cases of this being fixed.
- **Self-hosted, single-user.** Runs on your own machine in Docker; only you hold the Tonal
  login and the server's own token. See [`docs/tonal-access.md`](docs/tonal-access.md) for what
  "Tonal login" actually means here — there's no official API, so read that before setting up.
- **A destructive action gives you something back.** `delete_workout` captures the workout's
  full content (title, sets) *before* archiving it and returns that snapshot in its own
  response — Tonal itself won't return an archived workout's sets again after that call, so
  this is the only place they survive.

## Tools

`list_workouts`, `get_workout`, `find_movement`, `estimate_workout_duration`,
`create_workout`, `update_workout`, `delete_workout`.

Every tool's docstring documents exactly what its fields mean, which ones are honestly `null`
by design vs. genuinely unavailable, and any live-API quirk that shaped how it behaves — see
[SPEC.md](SPEC.md) for the full tool contracts and the findings behind each one.

## Setup

### Prerequisites

- Python 3.11+, Docker, a Tonal account (your own login).
- Somewhere to run this that can stay online (a home server, NAS, always-on PC) — not a
  cloud-hosted service.

### 1. Tonal access

Read [`docs/tonal-access.md`](docs/tonal-access.md) first — there is no official Tonal API, so
this signs in the same way the mobile app does, with your real email/password. Understand what
that means before putting your password in a config file.

### 2. Configure and run

```bash
cp .env.example .env
```

| Var | What it's for |
|---|---|
| `TONAL_EMAIL` / `TONAL_PASSWORD` | Your normal Tonal login. |
| `MCP_BEARER_TOKEN` | One-time login password for the server's OAuth flow (see "Auth" in [SPEC.md](SPEC.md)). Pick a long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `MCP_PUBLIC_URL` | The externally-reachable URL you'll expose this at — see [`docs/self-hosted-setup.md`](docs/self-hosted-setup.md). Required for correct OAuth redirect URLs; `127.0.0.1` won't work here. |
| `TZ` | Your local timezone, e.g. `America/New_York`. |

```bash
docker compose up --build -d
curl http://localhost:18082/health   # {"status": "ok", "version": "..."}
```

`/health` is unauthenticated; every other endpoint needs a real OAuth access token, not the
raw `MCP_BEARER_TOKEN` — see [SPEC.md](SPEC.md)'s auth section for why a static bearer header
alone isn't enough for Claude's connector UI.

### 3. Expose it and add the connector

[`docs/self-hosted-setup.md`](docs/self-hosted-setup.md) covers exposing this over Tailscale
Funnel (or any HTTPS tunnel) and adding it as a Claude custom connector, including two
constraints that cost real debugging time to find (port 443 only; OAuth discovery path
collisions when running alongside other MCP servers on the same host).

### 4. Development

```bash
python -m venv .venv
.venv\Scripts\activate      # or: source .venv/bin/activate
pip install -e ".[dev]"
pytest                      # fast, offline, mocked -- no Tonal credentials needed
pytest -m integration       # hits your real account; needs TONAL_EMAIL/TONAL_PASSWORD in .env
```

See [SPEC.md](SPEC.md)'s "Testing strategy" for why there are two tiers and what each one is
actually for.

## Related

- [garmin-mcp](https://github.com/frictionlesscode/garmin-mcp) — Garmin Connect data/writes.
- [macro-mcp](https://github.com/frictionlesscode/macro-mcp) — nutrition logging and macro
  targets.
- [tonal-garmin-sync](https://github.com/frictionlesscode/tonal-garmin-sync) — the separate,
  unrelated service that pushes *completed* Tonal workouts into Garmin Connect as activities.
  Different problem (recording what happened vs. building what's next); this project shares
  its curated movement catalog as data, not as a runtime dependency — see SPEC.md's "Why not a
  full refactor" for why the two stay decoupled.
