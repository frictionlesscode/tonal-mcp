# tonal-mcp — Build Spec

A remote MCP server exposing full CRUD on Tonal custom workouts to Claude, reachable from
mobile — the write counterpart to reads Claude already has via `garmin-mcp`/`macro-mcp`.

See a separate planning doc (not in this repo) for the full
plan this was built from (context, rejected alternatives, and why).

---

## Locked decisions

| Decision | Choice |
|---|---|
| Language | Python 3.11+ |
| Tonal client | reimplemented from scratch (`src/tonal_mcp/tonal_client.py`), via `httpx` — not a Node shell-out. Tonal's auth has no MFA/WAF to work around, unlike Garmin's. |
| MCP framework | FastMCP, Streamable HTTP transport |
| Auth | `SingleUserOAuthProvider`, copied verbatim from `garmin-mcp` (third use of the same file — `macro-mcp` copied it first) |
| Packaging | Docker, bound to `127.0.0.1:18082` only |
| Public exposure | existing Tailscale Funnel (`your-funnel-host.ts.net`), new `/tonal` path |
| Movement matching | reads `src/tonal_mcp/data/curated.json`, copied from `tonal-garmin-sync`'s `config/curated.json` as data (not a live call — see plan and M3 findings for why it's packaged, not a repo-relative path) |

## Charter

**tonal-mcp owns:** authenticating to Tonal, translating tool calls into `user-workouts`
request shapes, movement name→id lookup, reporting back what Tonal actually stored.

**tonal-mcp does not own:** exercise selection, programming philosophy, rep/set schemes,
periodization. Claude decides what workout to build; the server just writes it.

---

## M1 findings (2026-08-21) — the Tonal write path, proven live

Ran `scripts/prove_write_path.py` against the real account. All of create → read → update →
archive worked on the first structurally-correct request:

- `POST /user-workouts`, `PUT /user-workouts/{id}`, `DELETE /user-workouts/{id}` (soft —
  `publishState` goes to `'archived'`, confirmed by reading the workout back after delete),
  `GET /workouts/{id}` all behave exactly as reverse-engineered from
  `@dlwiest/ts-tonal-client`'s bundled source.
- **Tonal validates set shape server-side, movement by movement.** First attempt used
  `prescribedReps` for "Bodyweight Squat" and got a real, specific 400: *"Bodyweight Squat
  programmed as reps but must be duration."* Switching to `prescribedDuration` fixed it.
  Lesson for `create_workout`/`update_workout`: a bad request shape fails loudly with a
  usable message, not silently — the MCP tool should surface Tonal's own error message rather
  than swallowing it.
- **`weightPercentage` is stored verbatim, not resolved into an absolute weight at write
  time** — reading the created workout back showed `weightPercentage: 50` and `100`
  unchanged, byte for byte. What it means at *performance* time (presumably scaling some
  per-user, per-movement calibration on the machine) couldn't be determined from this test:
  "Bodyweight Squat" is `onMachine: False` and carries no cable load at all, so there was
  nothing for the percentage to scale. **Open**: re-run the probe against an `onMachine: True`
  weighted movement and check the Tonal app / machine behavior directly — needs a live app
  session, not just API calls, since the number likely resolves on the machine side, not
  server-side.

## M2 findings (2026-08-21)

- **`estimate_workout_duration`'s live request shape disagrees with
  `ts-tonal-client`'s own source.** The library wraps the body as `{"sets":
  [...]}`, matching create/update -- but the live API rejects that here with
  `"json: cannot unmarshal object into Go value of type content.SetList"`.
  Confirmed live: this one endpoint wants the bare sets array as the entire
  POST body, unlike create/update which do want the nested-object shape.
  Fixed in `tonal_client.py`; regression test in `test_tonal_client.py`.
  Lesson: verify each endpoint against the live API even when a reference
  implementation exists — an unofficial API can drift out from under its own
  reverse-engineered client.
- FastMCP's `Client.call_tool(...).data` deserializes a `TypedDict`-typed
  tool result into an auto-generated pydantic model (attribute access, e.g.
  `result.id`), not a plain dict -- `scripts/mcp_smoke_local.py` uses
  attribute access accordingly. Tool return values themselves are still
  plain dicts server-side (`models.py`'s `TypedDict`s); this is purely a
  client-side deserialization detail.
- **`weightPercentage` must be a JSON integer, not a float.** `SetIn`
  originally declared it `float`; FastMCP's schema validation then coerced a
  plain `100` argument to `100.0` before my code ever saw it, and Tonal's
  Go backend rejects that: `"json: cannot unmarshal number 100.0 into Go
  struct field Set.SetInfo.weightPercentage of type int"`. Only surfaced
  through `scripts/mcp_smoke_local.py`'s real tool-call path — calling
  `service.py` directly with a plain Python dict (as the pytest suite and
  `prove_write_path.py` do) never coerces the type, so this bug was
  invisible to both. Fixed by typing `weight_percentage` as `int`
  everywhere (`models.py`, `tonal_client.py`'s `WorkoutSet`, `service.py`'s
  conversions). **Lesson this reinforces**: unit tests against a fake/mocked
  client don't exercise FastMCP's own schema coercion — the M2 gate's "real
  round trip" check is not optional decoration, it catches a real class of
  bug the mocked tests structurally cannot.

## M3/M4 findings (2026-08-21)

- **`pip install .` (non-editable, as Docker does) breaks a `Path(__file__).resolve().parent
  .parent.parent`-relative config path.** A regular install copies the package tree into
  site-packages; the repo-root-relative walk that worked under `pip install -e .` resolved to
  `/usr/local/lib/python3.12/config/curated.json` in the container — confirmed live
  (`FileNotFoundError` on container start). Fixed by moving `curated.json` into the package
  itself (`src/tonal_mcp/data/curated.json`, declared via `[tool.setuptools.package-data]`) so
  `Path(__file__).resolve().parent / "data"` resolves the same way under both install modes,
  rather than depending on the package's position relative to the repo root.
- Tailscale Funnel path-mapping commands must be single-line, not backslash-continued
  multi-line — a multi-line `--set-path=...` invocation was blocked by this environment's
  command classifier where the equivalent single-line form was not.
- Git Bash (MSYS) rewrites a bare leading-slash argument like `/tonal` into a Windows path
  before it reaches `tailscale` — confirmed live (`tailscale funnel --set-path=/tonal ...`
  produced a mapping literally titled `/C:/Program Files/Git/tonal`). Fixed by prefixing with
  `MSYS_NO_PATHCONV=1`, same fix already known from this session's earlier `docker exec` calls.
- Full public discovery chain verified live over `https://your-funnel-host.ts.net/tonal`:
  unauthenticated `POST /tonal/mcp` → 401 with the correct `WWW-Authenticate` pointing at
  `/.well-known/oauth-protected-resource/tonal/mcp`; that URL → 200; the RFC 8414
  root-relative form at `/.well-known/oauth-authorization-server/tonal` → 200. Other paths already served on the same Funnel host were unaffected — no regression from adding the third path.

## Milestones

### M1 — Prove the Tonal write path standalone ✅ done (above)

### M2 — MCP server, all six tools (full CRUD) ✅ done (above)

`list_workouts`, `get_workout`, `find_movement`, `estimate_workout_duration`,
`create_workout`, `update_workout`, `delete_workout`.

### M3 — Auth + Docker ✅ done (above)

Copied `oauth.py`/`auth.py` from `garmin-mcp` (third deployment of the same class — see
`oauth.py`'s docstring). Dockerfile + compose bound to `127.0.0.1:18082`. `/health`
unauthenticated; confirmed unauthenticated `/mcp` correctly 401s.

### M4 — Wire into the Tailscale Funnel ✅ done (above); claude.ai connector — pending user

`/tonal` added alongside the Funnel's existing paths, full discovery chain verified live (above).
**Remaining, user-only step** (adding an OAuth connector and entering a credential isn't
something this assistant does on someone's behalf): in claude.ai, Settings → Connectors →
Add custom connector → `https://your-funnel-host.ts.net/tonal/mcp`, sign in with the
`MCP_BEARER_TOKEN` from `.env`. Then confirm from an actual chat: ask it to create a small
test workout, check it in the Tonal app, then update and archive it.
**Gate:** `create_workout`/`update_workout` both work from an actual Claude chat.
Confirmed live from an actual chat 2026-08-21 -- which is also how the two bugs below were
found (see "Bug report findings").

## Bug report findings (2026-08-21) — filed after real chat testing

Both traced to genuine live-API behavior, not conversion bugs in this server -- confirmed by
reproducing each directly against `TonalClient`, bypassing `service.py` entirely.

- **`list_workouts`'s `set_count` was always 0.** `GET /user-workouts` (the list endpoint)
  never includes a `sets` array at all — confirmed live: a real item's keys have
  `movementIds` (distinct movements used) but no `sets`. `set_count=len(raw.get("sets") or
  [])` was therefore computing `len([])` for every item, indistinguishable from "confirmed
  empty workout." Fixed: `set_count` is `int | None`, `None` when the source data isn't
  present (honest, not fabricated); added `movement_count` (from `movementIds`) as a
  real, differently-sourced size signal available at list time. `models.py`/`service.py`.
- **Same call also ignored `limit`.** `limit=5` returned 10 (the account's full count) —
  confirmed live at limit values 2/5/10/25/100, all returning the same 10, and confirmed the
  API ignores both the `x-paginate-*` headers ts-tonal-client sends *and* an `?offset=&limit=`
  query-string form. Fixed by truncating client-side in `service.list_workouts` so this
  tool's own `limit` contract holds regardless of what the upstream endpoint honors.
- **Archived workouts lose their `sets` array on `get_workout`.** Confirmed live: create →
  get (7 sets present) → delete → get again → `sets` key is entirely absent from the raw
  response, not just empty (title/description/duration_min/publish_state all still present).
  This is Tonal's own behavior, not something this server strips. Not "fixable" in the sense
  of recovering the data — fixed by documenting it loudly in `get_workout`'s and
  `delete_workout`'s docstrings (capture sets via `get_workout` *before* archiving if you'll
  need them) rather than leaving the "still listed/fetchable" claim implying full content.

**Why the test suite didn't catch these first**: the mocked unit tests (`test_tonal_client.py`,
`test_service.py`) encode assumptions about the API's shape as hand-written fixtures — a wrong
assumption about the shape produces a self-consistent but wrong test, not a failure. And
`scripts/mcp_smoke_local.py`, the one check that hits the real account, *called* both
`list_workouts` and `get_workout`-after-`delete_workout` but never asserted on the specific
fields that were actually broken (`set_count`'s value; `sets` after archiving) — it exercised
the code path without checking the thing that mattered. Both fake fixtures now mirror the real
shape (no `sets` key in list results; no `sets` key on an archived `get_workout_by_id`) instead
of the more convenient wrong shape, and `mcp_smoke_local.py` needs the same treatment — see
the "Testing strategy" section below, added in direct response to this.

## Testing strategy (2026-08-21, post-bug-report; upgraded same day)

The gap above is structural, not a one-off oversight: mocked tests can only be as correct as
the fixtures they're handed, and a wrong assumption about a third-party API's shape produces a
test that passes for the wrong reason. The fix isn't "write more mocked tests" -- it's making
the one thing that *does* touch the real API assert on every claim this server's docstrings
make, not just exercise the call.

**Upgraded from a standalone script to real integration tests** (`tests/test_integration.py`,
marked `@pytest.mark.integration`, excluded from the default `pytest` run via
`addopts = "-m 'not integration'"`, run explicitly with `pytest -m integration`) after the
first version of this section shipped as `scripts/mcp_smoke_local.py` and someone reasonably
asked "shouldn't we have our own integration tests?" The concrete problem with the script form:
no cleanup guarantee -- a failed assertion partway through just crashed the script, leaving the
created test workout stranded rather than reaching `delete_workout`. `tests/test_integration.py`
uses a fixture (`throwaway_workout`) with `try/finally` teardown instead, so a failing test
still archives what it created. It also gets per-test failure isolation (one broken assertion
doesn't prevent the other six tests from running) and standard `pytest` tooling, neither of
which the sequential script had. `scripts/mcp_smoke_local.py` is deleted, not kept alongside --
two versions of the same live check would drift.

The standing rule carries over unchanged: **every behavioral claim in a tool's docstring gets a
live assertion, not just a call.**

- Every field in every response gets its type and (where knowable) value asserted -- not just
  the fields the current change happens to touch. `set_count`'s `None`-ness would have been
  caught immediately by an assertion on it, not just a call to `list_workouts`.
- Any docstring claim of the shape "X behaves like Y" (e.g. "still listed/fetchable") gets a
  literal round-trip proving it -- `test_delete_workout_archives_and_strips_sets` does exactly
  this for `publish_state` *and* `sets`.
- When a live-API finding changes a docstring or a return shape, the integration test changes
  in the same commit, not as a follow-up -- the fix and its proof land together.
- Real API state matters: the mocked `FakeClient` in `test_service.py` was rewritten to be
  *stateful* (an actual `dict` of workouts that `delete_workout` mutates) rather than branching
  on a magic id like `"archived-1"`, specifically so it could model "sets present before
  archiving, gone after" instead of two disconnected canned responses that happened to differ.

This doesn't replace the mocked pytest suite (still the fast, offline check for request-shape
translation and error handling, and the only one that runs without live credentials) -- it's
the complement that catches wrong assumptions the mocked suite structurally cannot.

## Retest findings (2026-08-21) — second bug report, after the first fix pass

Filed after retesting the fixes above from a real chat. `limit` confirmed fixed as-is. The
other two prompted real follow-up work, not just re-confirmation:

- **`set_count` staying `None` forever, with `movement_count` alongside it, was flagged as
  only a partial fix** -- fair question: is `movement_count` the permanent answer, or is
  `set_count` still meant to ship? Decided and documented explicitly in `list_workouts`'
  docstring: `movement_count` is the permanent list-time signal; `set_count` stays `None`
  by design, because populating it for real would mean an N-call fan-out (one `get_workout`
  per listed item) on every `list_workouts` call, for data most callers won't need for most
  items. Callers who need an exact count for specific workouts call `get_workout` on those
  ids -- the tool doesn't do that eagerly on their behalf.
- **"Archived workouts lose their sets" was correctly marked Not Fixed** -- the first pass
  only documented the behavior, it didn't address the actual pain point (an archived workout
  can't be used to recreate itself). Real fix this round: `delete_workout` now fetches the
  workout's full detail *before* archiving it and returns that snapshot (`title`, `sets`) in
  its own response -- `DeleteResult` gained `title`/`sets` fields. A `get_workout` call made
  *after* archiving still shows `sets: []` (that part is genuinely Tonal's own behavior and
  can't change), but a caller no longer has to remember to fetch first: `delete_workout`'s
  return value is now the permanent record of what was deleted. Regression-tested at both
  layers (`test_service.py`'s stateful fake, and live in `test_integration.py`).

## Tool contracts (M2)

```
list_workouts(limit: int = 25) -> [{id, title, publish_state, duration_min, set_count, movement_count}]
get_workout(workout_id: str) -> {id, title, description, publish_state, duration_min, sets: [...]}
find_movement(name: str) -> [{id, name, on_machine}]  # ranked matches, reusing tonal-garmin-sync's approach
estimate_workout_duration(sets: [...]) -> {duration_sec}
create_workout(title: str, sets: [...], description: str = "") -> {id, title, duration_min}
update_workout(workout_id: str, title: str, sets: [...], description: str = "") -> {id, title, duration_min}
delete_workout(workout_id: str) -> {id, publish_state, title, sets: [...]}  # archives (does not
  # destroy); sets is a snapshot captured immediately before archiving -- Tonal itself strips
  # sets from a later get_workout on an archived id, so this response is the only place they
  # survive (see "Retest findings")
```

Each `sets` entry at the tool boundary: `{movement_id, block_number, block_start, set_group,
round, repetition, repetition_total, weight_percentage=100, prescribed_reps?,
prescribed_duration?, description=""}` — mirrors `WorkoutSet` in `tonal_client.py`. Exactly
one of `prescribed_reps`/`prescribed_duration` must be set, and which one is *required* by
Tonal depends on the specific movement (see M1 finding above) — the tool should let Tonal's
own 400 surface rather than guessing.
