---
title: "Troubleshooting Postgres backups"
sidebar:
  label: "Troubleshooting"
description: "Fixes for common pgbackup-r2 problems: PATH and Docker, database connections, pg_dump version mismatches, drills, restores, and every doctor message."
---

Start with `pgb doctor`. It checks each piece in turn and prints a fix under whatever fails. If that doesn't explain it, look below.

## Installing pgb

**`pgb: unknown command "…"`.** A typo in the command. `pgb` suggests the closest one it knows, and `pgb help` lists them all. (This is `pgb` answering, so it's installed fine.)

**`pgb: command not found`.** `~/.local/bin` isn't on your `PATH`. Run the line for your shell from [Install pgb](/start/install/#put-localbin-on-your-path), then open a new terminal. Check with `echo $PATH`.

**`zsh: command not found: pgb` right after installing.** zsh only rereads `~/.zshrc` in a new terminal, and it caches where commands live. Open a new terminal, or run `source ~/.zshrc` (or `rehash`).

**`permission denied` when running `pgb`.** The file lost its execute bit, which happens if it was downloaded by hand. Run `chmod +x ~/.local/bin/pgb`.

**PowerShell says running scripts is disabled.** Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

**`Cannot connect to the Docker daemon`.** Start Docker Desktop, or on Linux the docker service (`sudo systemctl start docker`). On Linux, your user also needs to be in the `docker` group: run `sudo usermod -aG docker $USER`, then log in again.

## Connecting to the database

**`could not translate host name "db"`.** `db` is a Docker Compose service name, and only containers on the Compose network can see it. Add `PGB_NETWORK=<project>_default` to `.env`. `docker network ls` lists the names. If the database publishes a port on this machine instead, use `localhost` as the host.

**`connection refused` to a database on your own machine.** Inside a container, `localhost` is the container itself. `pgb` handles this when the URL says `localhost` or `127.0.0.1`. Without `pgb`, use `host.docker.internal` (Docker Desktop) or `--network host` (Linux). Also check that Postgres listens on the port you think it does.

**A managed database times out from GitHub Actions.** The database must accept connections from anywhere, and Supabase's direct host is IPv6-only. See [Managed databases](/guides/managed-databases/#the-database-must-accept-outside-connections).

**Errors about prepared statements, or a dump that hangs, through a pooler.** `pg_dump` needs a real session. Use a direct connection, or a session-mode pooler, never a transaction-mode one. On Neon, use the host without `-pooler`.

## Versions

**`unsupported version (…) in file header`** or **`unrecognized configuration parameter "transaction_timeout"`.** `PG_MAJOR` doesn't match your server's major version. See [Postgres versions](/concepts/postgres-versions/).

**`server version mismatch` from `pg_dump`.** The image is older than the server. Raise `PG_MAJOR`, then run `pgb update`.

## Drills and restores

**`extra table … isn't in the manifest; the scratch database should start empty`.** The drill restored into a database that already had tables. `pgb drill` always starts empty. With your own `DRILL_DATABASE_URL`, drop and recreate that database first.

**`the private key doesn't match this backup`.** The backup was encrypted for a different public key. Find the private key that belongs to the `AGE_RECIPIENTS` in use when the backup was taken. That's one reason to keep a [second recipient](/concepts/keys/#lose-the-private-key-lose-the-backups).

**`checksum mismatch`.** The file in the bucket isn't what was uploaded: it's corrupt or altered. Try another backup (`pgb list`), and if several fail, check the bucket.

**`can't write to /out as uid …`.** On Linux, the container needs to run as you to write into `./out`. `pgb` does this. Without it, add `--user "$(id -u):$(id -g)"`.

**`couldn't record this drill in … .drills/`.** The token can't write to the bucket. The drill's result still stands, but `list` and `doctor` won't know about it. See [a token for your laptop](/guides/r2/#a-token-for-your-laptop).

## Doctor's messages

Each line starts with ✓ (fine), ✗ (fails `doctor`, exit 1), ⚠ (worth a look) or `-` (for information).

### ✗ Failures

| Message | What it means, and the fix |
|---|---|
| `Docker isn't running, or you can't reach it` | Start Docker. On Linux, add yourself to the `docker` group. |
| `Couldn't run the image …` | Docker couldn't pull or start the image. Check `PG_MAJOR` in `.env` (16, 17 or 18), and that Docker can reach `ghcr.io`. |
| `Image 0.1.1 has no doctor checks of its own` | The image is too old for `doctor`. Run `pgb update`. |
| `No .env in <folder>` | Run `pgb init`, or `cd` to the folder that has your `.env`. |
| `Settings: X, Y are not set` | Fill them in. The fix line says what each one is. `… is empty` means the name is there with nothing after the `=`. |
| `Settings: S3_ENDPOINT has a path (/…)` | The endpoint is just `https://<account-id>.r2.cloudflarestorage.com`. Drop the path; the bucket goes in `S3_BUCKET`, and a folder in `S3_PREFIX`. |
| `Settings: DRILL_DATABASE_URL and DATABASE_URL point at the same database (…)` | A drill wipes its database, so it must never be the live one. Point `DRILL_DATABASE_URL` at a scratch database. With `pgb`, leave it out: `pgb drill` starts its own. |
| `Settings: TIERS: …` (or another setting) | A setting has a bad value. The fix shows the right form. |
| `Database: can't connect to …` | Read the error after the colon. A host name that can't be found gets an explanation, with the `PGB_NETWORK` and `localhost` fixes. A refused `localhost` connection outside `pgb` gets the `host.docker.internal` hint. Bad passwords, and databases that don't exist, show Postgres's own words. |
| `Database: … is PostgreSQL 17.2, but PG_MAJOR is 16` | Set `PG_MAJOR` to the server's major version. |
| `Database: … but this image's tools are 16` | You ran the image directly with the wrong tag. Use the tag for your server's version. |
| `Bucket: can't list …: … says NoSuchBucket` (or `NoSuchKey`) | There's no bucket by that name at that endpoint. Check `S3_BUCKET` is the bucket's exact name, and that `S3_ENDPOINT` is the account that has it, with no path. |
| `Bucket: can't write to …` | The fix depends on the error: check `S3_BUCKET` and the endpoint (`NoSuchBucket`), the keys (`InvalidAccessKeyId`, `SignatureDoesNotMatch`), or the token's permission (`AccessDenied`: it needs Object Read & Write). An unreachable endpoint means `S3_ENDPOINT` is wrong. |
| `Bucket: wrote to … but can't delete` | The token can write but not delete, so `prune` would fail. Give it Object Read & Write, and remove the test file it names by hand. |
| `Keys: AGE_RECIPIENTS …` | `AGE_RECIPIENTS` contains a private key, or something that isn't an `age1…` key. |
| `Keys: AGE_IDENTITY can't open backups encrypted to AGE_RECIPIENTS` | The private and public keys don't belong together. The fix line shows your private key's public key: add it to `AGE_RECIPIENTS`. |

### ⚠ Worth a look

| Message | What to do |
|---|---|
| `The image is 0.3.2, older than pgb 0.4.2` | Run `pgb update` to pull the image that matches. Until then, newer features are skipped: `pgb drill` drills without its preflight check, with a one-line note. |
| `pgb 0.4.2 is older than the image (…)` | Re-run the install line to update `pgb` itself. |
| `TIERS isn't set and there are no backups directly under …, but found backups in …` | Your backups live in tier folders, but without `TIERS`, `list`, `restore` and `drill` only look at the prefix itself. Set `TIERS` as the fix line shows, quotes included. |
| `.env: pgbackup-r2 doesn't read .env, and X, Y from it aren't in the environment.` | Without `pgb`, nothing reads `.env` for you. Load it into the shell first: `set -a; source .env; set +a`. |
| `This bucket has never been drilled.` | Run `pgb drill`. |
| `Your last successful drill was 41 days ago.` | Run `pgb drill`. More than 35 days triggers this. |
| `No drill has passed yet (last drill: FAIL, …)` | Fix what the drill reported, then drill again. |
| `The most recent drill failed (…)` | An older drill passed, but the latest didn't. Run `pgb drill` to see why. |
| `.env: S3_BUKET → S3_BUCKET? pgbackup-r2 has no setting called S3_BUKET.` | A misspelt setting: nothing reads it, so the default (or nothing) is used instead. Rename it. |
| `.env: X isn't a pgbackup-r2 setting, so nothing reads it.` | A name pgbackup-r2 doesn't know, with nothing close enough to guess. Check the spelling against [Environment variables](/reference/environment/), or remove it. Without `pgb`, `doctor` only flags names that look like a misspelt setting, because the environment holds other programs' variables too. |

### `-` For information

| Message | Meaning |
|---|---|
| `AGE_IDENTITY isn't set: fine for a backup-only server` | Expected on a server. `verify`, `drill`, `decrypt` and `restore` need it. |
| `N other files … not made by pgbackup-r2 (left alone)` | Other things in the folder. They're never touched. |
| `Found folders …` with `Suggested: TIERS=…` | Your bucket looks like it has tier folders. `pgb init --detect` writes the suggestion. |
| `<folder>/ has no lifecycle rule that deletes old files, and PRUNE is off` | Nothing ever deletes old backups there. Add a lifecycle rule, or accept the growth. |
| `Couldn't read the bucket's lifecycle rules` | The token isn't allowed to read them. Check them in the dashboard instead. |
| `✓ Bucket: … The test file … is locked` | A lock rule covers the prefix itself, so doctor's test file stays until the lock ends. Lock the tier folders instead. |
