# Self-hosted setup notes

Generic guidance for exposing this server publicly so it can be added as a Claude custom
connector, plus a few operational notes worth knowing before you run this long-term.
Everything below uses placeholder values — `<your-machine>`, `example.ts.net`, etc. — swap in
your own. This mirrors [garmin-mcp's](https://github.com/frictionlesscode/garmin-mcp/blob/main/docs/self-hosted-setup.md)
and [macro-mcp's](https://github.com/frictionlesscode/macro-mcp/blob/main/docs/self-hosted-setup.md)
own setup docs closely — all three servers use the identical auth mechanism and are meant to
run side by side on the same machine.

## Exposing the server publicly (Tailscale Funnel)

Claude's connector UI needs a real HTTPS URL to reach your server's `/mcp` endpoint —
`127.0.0.1` or a bare LAN IP won't work.

**Two hard constraints drive this entire section. Both were learned the expensive way, on the
sibling servers, and apply here unchanged:**

1. **Claude's backend only egresses on port 443.** Any other port fails *silently* — the
   request never arrives and **nothing appears in your logs**, which looks identical to a
   server bug. Note this means Funnel *will* happily configure an arbitrary port for you —
   that's not evidence it works.
2. **OAuth protected-resource discovery is domain-root-relative**, so two MCP servers sharing
   one hostname collide. Details and the workaround below.

If `garmin-mcp` and/or `macro-mcp` already occupy port 443 on this tailnet node,
`tonal-mcp` goes on a **path under that same port**, not a different port.

### Path-based sharing on port 443

```bash
tailscale funnel --bg --set-path=/tonal 18082
```

That alone is **not sufficient**, and the failure is confusing: Claude reports an invalid or
expired token and never shows a login prompt, while your logs show a bare `POST /mcp` → 401
with no `/register` or `/authorize` behind it — i.e. it gave up before trying to authenticate.

The cause is a prefix mismatch:

- On a 401, the server advertises where to authenticate via a `WWW-Authenticate` header
  pointing at `/.well-known/oauth-protected-resource/tonal/mcp` — a **domain-root** path.
- If the domain root is proxied to a different server (e.g. `garmin-mcp`), the client follows
  that pointer to the *wrong* server and gets a 404.
- Worse, FastMCP registers that metadata route *including* the `/tonal` prefix, while
  `--set-path` **strips** the prefix before forwarding — so the route could never match even
  if routing were correct.

Fix it with two mappings that pass those specific paths through **unstripped**, by giving the
target as a full URL including its path — as single-line commands (a backslash-continued
multi-line form was blocked by this environment's command classifier when this was set up):

```bash
tailscale funnel --bg --set-path=/.well-known/oauth-protected-resource/tonal/mcp http://127.0.0.1:18082/.well-known/oauth-protected-resource/tonal/mcp

tailscale funnel --bg --set-path=/.well-known/oauth-authorization-server/tonal http://127.0.0.1:18082/.well-known/oauth-authorization-server
```

The second covers RFC 8414's root-relative discovery form
(`/.well-known/oauth-authorization-server/<path>`), which clients typically try **before** the
path-appended form — without it, discovery 404s even though the appended form works.

> **If you're on Git Bash / MSYS (Windows):** a bare leading-slash argument like `/tonal` gets
> silently rewritten into a Windows path before it reaches `tailscale` — confirmed live, it
> produced a mapping literally titled `/C:/Program Files/Git/tonal`. Prefix every `tailscale`
> command above with `MSYS_NO_PATHCONV=1` to stop this.

`tailscale funnel status` should then show something like:

```
|-- /                                               proxy http://127.0.0.1:18080
|-- /macro                                          proxy http://127.0.0.1:18081
|-- /tonal                                          proxy http://127.0.0.1:18082
|-- /.well-known/oauth-authorization-server/macro   proxy http://127.0.0.1:18081/.well-known/oauth-authorization-server
|-- /.well-known/oauth-authorization-server/tonal   proxy http://127.0.0.1:18082/.well-known/oauth-authorization-server
|-- /.well-known/oauth-protected-resource/macro/mcp proxy http://127.0.0.1:18081/.well-known/oauth-protected-resource/macro/mcp
|-- /.well-known/oauth-protected-resource/tonal/mcp proxy http://127.0.0.1:18082/.well-known/oauth-protected-resource/tonal/mcp
```

Set `MCP_PUBLIC_URL` to the path-inclusive URL (e.g.
`https://<your-machine>.example.ts.net/tonal`) and restart — OAuth's issuer and redirect URLs
derive from it. Connect Claude to `<MCP_PUBLIC_URL>/mcp`.

> **Scaling note.** These mappings compensate for a real architectural constraint rather than
> removing it. A fourth MCP server on this hostname would need its own set. At that point, give
> each server its own Tailscale hostname instead.

### Verifying (test the discovery chain, not just `/health`)

`/health` passing proves almost nothing here — it would return 200 the entire time the
connector was broken. Walk the chain a client actually walks:

```bash
# 1. unauthenticated call must 401 *and* advertise where to authenticate
curl -sk -i -X POST https://<host>/tonal/mcp -H 'Content-Type: application/json' -d '{}' \
  | grep -i www-authenticate

# 2. the URL from that header must return 200 JSON (not 404 from another server on this host)
curl -sk https://<host>/.well-known/oauth-protected-resource/tonal/mcp

# 3. RFC 8414 root-relative discovery must return 200
curl -sk -o /dev/null -w '%{http_code}\n' \
  https://<host>/.well-known/oauth-authorization-server/tonal

# 4. confirm any sibling servers on this host are still intact
curl -sk https://<host>/health
curl -sk https://<host>/macro/health
```

If a connector attempt fails, `docker logs tonal-mcp` distinguishes the cases precisely:
`/register` or `/authorize` appearing means discovery worked and it's a genuine auth problem;
a bare `POST /mcp` → 401 with nothing before it means discovery failed or the client is
reusing a cached credential.

### Stale cached credentials

Changing `MCP_PUBLIC_URL` changes the OAuth issuer, invalidating any token Claude already
holds — and Claude will keep retrying the dead token rather than re-authenticating. **Delete
the connector entirely and re-add it**; editing it in place may not clear the cache. Clearing
the server side too (`rm ./data/oauth_state.json`, then `docker compose up -d`) guarantees a
clean slate.

### Other options

Any reverse tunnel or reverse proxy that terminates HTTPS and forwards to the container's
published port works the same way — Cloudflare Tunnel and ngrok are common alternatives. The
only requirement is a stable public HTTPS URL to put in `MCP_PUBLIC_URL`.

## Picking a host port

`compose.yml` defaults to publishing `127.0.0.1:18082:8080` — chosen not to collide with
`garmin-mcp`'s `18080` or `macro-mcp`'s `18081`. If `18082` is already taken by something else,
change the host-side number in `compose.yml` (leave the container-side `8080` alone) and
update your tunnel mapping to match.

## Surviving a reboot

Four things have to come back independently:

| Piece | How it persists |
|---|---|
| Tailscale service | Installs as an auto-start service. |
| Funnel path mappings | Persisted by `--bg`; survives restarts without re-running. |
| The container | `restart: unless-stopped` in `compose.yml` brings it back once Docker is up. |
| Docker itself | **The weak link — see below.** |

On Windows, Docker Desktop's `AutoStart` setting fires **at user login, not at boot** (the
underlying `com.docker.service` is typically `Manual`/`Stopped` until then). A machine that
reboots and sits at the lock screen leaves the container down, and the connector simply fails
until someone logs in.

If this host is one you actually log into, that's fine. For genuinely unattended operation you
need either auto-login, or Docker Engine running as a real service rather than Docker Desktop.

Check yours:

```powershell
(Get-CimInstance Win32_Service -Filter "Name='com.docker.service'").StartMode
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' tonal-mcp
tailscale funnel status
```

## Confirming it actually works

Everything above gets you to "reachable and authenticated." The real test is narrower and more
concrete: **create a small test workout through Claude, confirm it in the Tonal app, then
update and archive it.** Once the connector's added (Claude → Settings → Connectors → Add
custom connector → `<MCP_PUBLIC_URL>/mcp`, sign in with your `MCP_BEARER_TOKEN`), try it and
see what comes back.
