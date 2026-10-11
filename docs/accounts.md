# Accounts

Who can sign in to the dashboard, how the first account gets created, and how an API key relates
to all of it. If you're using someone else's service, read this alongside
[remote-users.md](remote-users.md); if you're running the service, read it alongside
[deploy/README.md](deploy/README.md).

---

## An account is for a human; an API key is for a machine

Two different credentials, kept deliberately separate:

- **An account** (email + password) signs a **person** in to the **dashboard**, in a browser. It
  gets a session cookie.
- **An API key** authenticates a **machine** — a script, an agent, the OpenAI-compatible API — as
  a `Bearer` token on `/v1/*`.

Neither substitutes for the other:

- An API key **cannot** sign in at `/auth/login` — there is no "paste your key here" on the
  sign-in form any more. Signing in needs an email and a password.
- An API key is **never admin**, whoever owns it. A leaked key can consume the service (generate
  speech, clone a voice, transcribe audio) but cannot manage credentials: issuing or revoking keys
  and creating or disabling accounts is refused for a key even when its owner is an admin.
- A user rotates **their own** key from the dashboard's **Account** panel. An admin can see and
  revoke anyone's key (the **Settings → API keys** card), but still can't make one of
  them a dashboard credential.

## The master token: still break-glass, no longer a dashboard credential

The shared `AURIS_AUTH_TOKEN` (`scripts/token.sh show|set|rotate`) still works exactly as before
as a `Bearer` token on `/v1/*` — it's the unconditional `admin` principal, so a broken or empty
`users` table can never lock an operator out of their own server.

What's changed: **it no longer opens a dashboard session.** Posting it to `/auth/login` is
refused, the same as any other credential that isn't an email + password. If you were in the habit
of signing in to the dashboard with the master token, that stops working — create yourself a
named admin account instead (next section).

## Creating the first account

Before this feature, nobody needed an account to use the dashboard — the master token worked
there too. Now the dashboard needs an account from the start, and there's no UI to create the
first one (the Users panel that creates accounts is itself behind sign-in). So the first account
is always created from the command line, on the server box:

```bash
auris users add you@example.com --role admin
```

This prints a generated password **once** — there is no second chance to see it, and no email
delivery:

```
Initial password for you@example.com (you will not see it again):
<the generated password>
They must change it on first sign-in.
```

Copy it now. `--role` is `user` or `admin` (default `user`) — pass `--role admin` for the first
account, since only an admin can create further accounts. `auris users` only works on the box
running the server: it writes the server's own `users` table directly, so it refuses to run in
[remote-client mode](remote-users.md) (the same refusal `auris keys` already gives) and points you
at the admin HTTP API (`POST /v1/users`) instead.

Other `auris users` commands — like `auris users add`, these run directly on the box and bypass
the dashboard's admin check entirely, since anyone who can run a shell on the server already has
full access to its database:

```bash
auris users list                 # email, role, active/disabled, pending-password-change
auris users reset <email>        # new generated password, forces a change again
auris users disable <email>      # ends their sign-in and any API key they hold
auris users enable <email>       # re-enable a disabled account
```

`auris users disable` refuses to disable the **last active admin** — there must always be a way
back in.

## Signing in, and the forced first password change

A freshly created account has `must_change_password` set. Signing in with the generated password
works, but every other route answers `403 password_change_required` until the password is
changed — the dashboard shows this as a "choose a new password" screen that replaces the normal
sign-in form, with no way around it. Once changed, the account behaves like any other: sign in
with the new password at any time.

Changing a password — on first sign-in or afterwards from the **Account** panel — invalidates
**every other live session for that account**. It's the same mechanism as `auris users reset`:
each account has a `password_version` that's part of its session cookie, and a mismatch (because
the password changed since) is treated the same as an expired session.

## Resetting a forgotten password

There's no email delivery, so an admin resets it and hands the new password over out of band (in
person, over chat, however you'd hand over any other secret):

```bash
auris users reset alice@example.com
```

Like `auris users add`, this prints the new password once and sets `must_change_password`, so
Alice is forced to pick her own password the next time she signs in.

## Rotating your own API key

From the dashboard's **Account** panel, under "My API keys": issue a new key, copy it, then revoke
the old one once whatever used it has switched over. The CLI equivalent (`auris keys
add <name> --owner <email>`, run on the box) still works for a quick one-off — name yourself as
`--owner` and it shows up in your own "My API keys" list exactly like one issued from the
dashboard. `--owner` is required; there's no account to default to from the box. A key issued
*before* this requirement existed has no owner: it won't show up in anyone's "My API keys" list,
only an admin can see or revoke it, and — since voice ownership shipped — it can no longer create
or modify a voice at all (see "Who can see which voices" below). Re-issue it with `--owner` to fix
that.

## Who can see which voices

A cloned voice belongs to the account that created it, and is **private by default** — visible
and usable only by its owner, until they share it. Built-in preset voices aren't owned by anyone
and stay visible to all.

**A voice cloned with no account is the exception, and it lists as `shared`.** `auris clone` run
on the box (and a clone made with the shared master token) has no account to attach, so the voice
is **unowned** — and an unowned voice is readable by everyone regardless of its visibility column,
so it is stored and shown as `shared` rather than `private`. That is deliberate: labelling it
private would be a lie, since every account can already see it. Claim it with `auris voices adopt
<email>` (next section) and `unshare` then works normally. See the **unowned** row below.

`auris voices` (CLI) and the dashboard's **Voices** tab both only ever
show what the signed-in viewer is allowed to see — someone else's private voice doesn't appear in
the list at all, it isn't a greyed-out row.

| A voice that is… | Its owner sees | Another account sees | An admin sees | An ownerless (legacy) API key sees |
|---|---|---|---|---|
| **private** | use + modify | nothing (not listed) | use + modify | nothing (not listed) |
| **shared** | use + modify | use only | use + modify | use only |
| **unowned** (no owner at all) | — (nobody owns it) | use only | use + modify | use only |

"Use" means listed in `auris voices` / the Voices tab, and usable with `auris tts`/`auris
say`/`/v1/tts`/`/v1/audio/speech`. "Modify" means rename, delete, add/remove a clip, or flip the
share toggle — refused for anyone but the owner or an admin, and for an unowned voice, refused
for everyone but an admin until someone adopts it (next section). An unowned API key — one minted
before keys required an `--owner` — can never modify *any* voice, including one it could otherwise
see, because it has no account to own the result; see "Rotating your own API key" above. The
**local CLI on the box**, run with no signed-in account at all, is a special case: it sees and can
modify every voice unconditionally (there's no viewer to scope it by). `auris voices adopt` only
ever means anything there, for exactly that reason, so it refuses to run against a remote
service; `share` and `unshare` work either way — on the box directly, or against a remote service
over the same API a dashboard click uses.

**Upgrading an existing deployment:** `migrate()` runs automatically on the next server restart —
it adds the two columns and marks every *existing* voice **shared and unowned**, so nothing a
colleague is already using breaks. Those voices stay admin-only to modify until someone claims
them:

```bash
auris voices adopt you@example.com   # on the box — assigns every unowned voice to one account
```

This is idempotent and safe to run more than once. Do it **before** anyone runs `auris voices
unshare` or flips the dashboard's share toggle off: unsharing an unowned voice is refused (both on
the box and over HTTP), because an unowned voice is already visible to everyone regardless of its
visibility column, so there'd be nothing for "private" to change until it has an owner. `auris
voices share` on an unowned voice still succeeds, but notes that it changed nothing.

Full command reference (`share`/`unshare`/`adopt`, the `--owner` disambiguator): see
[usage.md](usage.md#auris-voices).

## A sign-in failure doesn't say why

Wrong password, no such account, and a disabled account all answer the same `401` with the same
message. That's deliberate: telling an attacker that an email address *is* registered (just with
the wrong password) is itself a disclosure. If sign-in fails, double-check the email and password;
if you're sure both are right, ask an admin whether the account is disabled.

## This doesn't change where the service is private

Accounts and API keys are both **authentication** — who you say you are. They don't add
**transport security** — the tailnet or LAN perimeter you already put the service behind
([deploy/README.md](deploy/README.md) describes it as plain `http` over Tailscale) is still what
keeps it off the public internet. Public TLS certificates for the service were considered and
explicitly declined (CT-log exposure would make the service's hostname, and that it exists,
publicly discoverable) — this feature doesn't change that; don't go looking for a way to add them.
