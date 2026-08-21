# Getting Tonal access

**Short version:** there is nothing to sign up for. You put your normal Tonal email and
password in `.env` and that's it. The rest of this page explains what that actually means,
because you should know before you do it.

## There is no Tonal API

Tonal publishes no public API, no developer program, no OAuth, no personal access tokens, and
no app passwords. There is no supported way for another program to read or write your workouts.

So this service does what every other Tonal integration does: it authenticates against the
same private Auth0 tenant the Tonal mobile app uses (a plain resource-owner password grant —
confirmed live, see [SPEC.md](../SPEC.md)), then calls the same private REST API the app calls.

Consequences worth understanding, in increasing order of how much this project changes them:

1. **Your Tonal password sits in a file in plaintext.** Anyone who can read `.env` has full
   access to your Tonal account. There is no way around this — Tonal offers no token you could
   use instead. See [SECURITY.md](../SECURITY.md) if this repo has one, or treat `.env` with
   the same care you'd give the password itself.
2. **Tonal has not blessed this and could break it at any time.** A login change or an
   endpoint change would stop the server working. Nothing is guaranteed.
3. **Running this at all is a deliberate choice, not a default.** It reaches your account over an unofficial, unsupported path and holds a real credential to do it. Decide for yourself that that tradeoff is acceptable for your own use before you set it up — don't run it just because it exists.
4. **This server writes, not just reads.** `tonal-garmin-sync` (a separate, related project)
   only ever reads completed workouts. This one can create, edit, and archive custom workouts
   in your real account — a bug here has a materially different blast radius than a read-only
   integration: a malformed `update_workout` call replaces a workout's *entire* set list (it's
   not a partial patch, see the tool's own docstring), and `delete_workout`, while a soft
   delete, does remove a workout from your active library. Both are exercised by a real test
   suite against the live account (see SPEC.md's "Testing strategy") specifically because of
   this — but the access this server holds is real write access, not a sandboxed copy.

If any of that is unacceptable, this is the point to stop — everything else depends on it.

**A reasonable precaution:** make your Tonal password unique to Tonal. If `.env` ever leaks,
the damage stops there.

## Setting it up

Put your normal Tonal login into `.env`:

```bash
TONAL_EMAIL=you@example.com
TONAL_PASSWORD=your-tonal-password
```

Then restrict the file so other users on the machine can't read it:

```bash
chmod 600 .env
```

### If you sign in to Tonal with Google or Apple

The password grant needs an email and password, so you'll need to set one on your account
first. In the Tonal app: **Profile → Settings → Account**, and set a password (or use the
"forgot password" flow on the email address tied to your Google/Apple sign-in to create one).
Your existing sign-in method keeps working — you're just adding a second way in.

### If your household shares a Tonal

Tonal accounts are per-person. Use the account of the person whose workouts this server should
manage. One server instance operates on one person's account; running it against two people's
accounts means two instances, two `.env` files, two `DATA_DIR`s, two ports.

## When it fails

| What you see | What it means | What to do |
|---|---|---|
| `TONAL_EMAIL/TONAL_PASSWORD not configured` | The server can't see your `.env` | Check you copied `.env.example` to `.env`, in the repo root |
| `TonalClientError: Authentication failed` | Wrong credentials, or a Google/Apple-only account | Sign in to the Tonal app with that exact email and password. If you can't, set a password (above) |
| A specific 400 with a real message (e.g. `"<movement> programmed as reps but must be duration"`) | Tonal itself rejected the request shape for that particular movement — this is Tonal's own validation, not a bug | Check the movement's requirements via `find_movement`/`get_workout` on a similar existing workout; adjust and retry |
| Everything hangs or times out | Tonal's API is unreachable | Check the machine has internet; try again later |

## Next

Set up exposing the server publicly: [self-hosted-setup.md](self-hosted-setup.md).
