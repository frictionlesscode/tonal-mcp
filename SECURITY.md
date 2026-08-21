# Security

This service holds credentials for your Tonal account, and — unlike a read-only integration —
can actually create, edit, and archive workouts in it. Here is exactly what it stores, why,
and what you should do about it.

## What's stored, and how sensitive it is

| What | Where | How bad if leaked |
|---|---|---|
| **Tonal email + password, in plaintext** | `.env` | **Severe.** Full read/write access to your Tonal account — not just data, an attacker could create or delete real workouts. |
| **OAuth state (registered clients, access/refresh tokens)** | `data/oauth_state.json` | **Severe.** A valid refresh token in this file means "logged in to this MCP server" with no further check — equivalent to holding `MCP_BEARER_TOKEN` itself. |
| **`MCP_BEARER_TOKEN`** | `.env` | **Severe** until first use, then reduced. It's the one-time login-form password for the OAuth flow (see `oauth.py`) — anyone who has it can complete a login and get a working access token. Once a real client has logged in, it's no longer sent per-request, only at login. |

### Why your Tonal password is in a plaintext file

Because Tonal offers no alternative. There is no public API, no OAuth, no personal access
tokens, and no app passwords — the only way to authenticate is the same email-and-password
login the mobile app uses, which means the service needs the real password to hand. See
[`docs/tonal-access.md`](docs/tonal-access.md) for the full picture, including why this
project's write access makes that a materially different tradeoff than a read-only
integration's.

That's a genuine downside and you should weigh it before installing this. If it bothers you,
consider changing your Tonal password to one you don't reuse anywhere else, so the blast
radius is limited to Tonal.

## What you should do

**Lock down the files.**

```bash
chmod 600 .env
chmod 700 data
```

**Never commit secrets.** `.gitignore` already excludes `.env`, `/data/`, and TLS
certs/keys (`*.crt`/`*.key`/`*.pem`, in case you ever drop a Tailscale cert in this directory
for local testing). Before your first push:

```bash
git status --ignored     # confirm .env and data/ are listed as ignored
```

**Generate a real random `MCP_BEARER_TOKEN`**, not something you invented:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Delete `data/oauth_state.json` if you ever suspect it leaked.** That immediately invalidates
every registered client and issued token; you'll need to re-add the connector in Claude
afterward (see [`docs/self-hosted-setup.md`](docs/self-hosted-setup.md)'s "Stale cached
credentials" section).

## How the service protects things

- OAuth access is gated by `SingleUserOAuthProvider` (`oauth.py`) — Dynamic Client
  Registration itself is deliberately open (any client can self-register, that's the point of
  DCR), so the actual security boundary is a login-form check against `MCP_BEARER_TOKEN`,
  compared with `hmac.compare_digest` (constant-time).
- `FailedAttemptLimiter` (`auth.py`): 10 failed login attempts within 60s locks that client out
  for 5 minutes, including subsequent attempts with the *correct* token — a leaked/guessed
  token can't just be retried past a temporary block.
- The container is only reachable via the loopback-bound Docker port publish
  (`127.0.0.1:18082`) — nothing on the public internet can connect to that port directly, only
  a locally-running trusted process (Tailscale Funnel, Cloudflare Tunnel, this repo's own
  tests) can.
- `/health` returns only `{status, version}` — no account data, no token expiry, unauthenticated
  by design so monitoring doesn't need a credential.
- No telemetry, no analytics, no outbound connections other than Tonal's own API.

## Reporting a vulnerability

Please open a GitHub issue for anything low-risk. For something genuinely sensitive, use
GitHub's private vulnerability reporting on this repository (Security → Report a
vulnerability) rather than a public issue.

This is a hobby project maintained in spare time — there is no SLA, and no warranty of any
kind (see [LICENSE](LICENSE)).
