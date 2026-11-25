<img src="https://raw.githubusercontent.com/iotabyt/pgbackup-r2/main/.github/assets/mark.svg" width="64" height="64" alt="pgbackup-r2 logo">

# pgbackup-r2

Encrypted Postgres backups to Cloudflare R2 or any S3, with restore drills that prove they work.

This package is the CLI itself: the command is `pgbackup-r2`. `pgb` is a separate wrapper that runs the same CLI in Docker (see "Prefer Docker?" below). Whatever the docs show as `pgb doctor` is `pgbackup-r2 doctor` here.

## Install

```bash
pip install pgbackup-r2
# or
uv tool install pgbackup-r2
# or try it without installing:
uvx pgbackup-r2 --version
```

## What you need on your PATH

pgbackup-r2 drives the real Postgres tools and [age](https://age-encryption.org), so these need to be installed:

- **`pg_dump`, `pg_restore` and `psql`, from the same major version as your server.** On Debian or Ubuntu, that's `sudo apt install postgresql-client-17` (from the [PostgreSQL apt repository](https://www.postgresql.org/download/linux/debian/) if your distro doesn't carry your version). On macOS, it's `brew install postgresql@17`, with its `bin` folder on your `PATH`. [Here's why the version has to match.](https://pgbackup-r2.iotabyt.com/concepts/postgres-versions/)
- **`age`:** `sudo apt install age`, or `brew install age`.

Rather not install any of that? The [Docker image](https://pgbackup-r2.iotabyt.com/start/quick-start/) has it all built in (see below).

## Minimal config

Settings come from environment variables. These are the ones a backup needs:

```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname
S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com   # leave out for AWS S3
S3_BUCKET=my-backups
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AGE_RECIPIENTS=age1...       # the PUBLIC key from `pgbackup-r2 keygen`
```

**pgbackup-r2 doesn't read `.env` files.** Keep the settings in a `.env` that only you can read, and load it into your shell before running anything:

```bash
set -a; source .env; set +a
```

Quote values with spaces, such as `TIERS="daily=daily/ monthly=monthly/"`. If a `.env` in the current folder isn't loaded, `pgbackup-r2 doctor` tells you. Every setting, with its default, is in the [environment reference](https://pgbackup-r2.iotabyt.com/reference/environment/).

## Use it

```bash
pgbackup-r2 keygen     # a key pair: public key into AGE_RECIPIENTS, private key into your password manager
pgbackup-r2 doctor     # checks the settings, the database, the bucket and the keys
pgbackup-r2 backup     # pg_dump, encrypted with age, uploaded with its checksum and manifest
pgbackup-r2 list       # what's in the bucket, and when the last drill ran
pgbackup-r2 drill      # restore into a scratch database and check every table's row count
```

Restoring and drilling need the **private** key, in `AGE_IDENTITY` (or a file holding it, in `AGE_IDENTITY_FILE`). A drill also needs a scratch database it can wipe, in `DRILL_DATABASE_URL`: never the one in `DATABASE_URL`. `pgbackup-r2 <command> --help` lists what each command needs.

Tired of typing it? `alias pgbr='pgbackup-r2'`.

Exit codes are cron- and CI-friendly: 0 for success, and 1 when anything fails, including a drill that finds a problem. Details are in [Exit codes](https://pgbackup-r2.iotabyt.com/reference/exit-codes/). `pgbackup-r2 schedule` can also stay running and back up every day at `BACKUP_AT`, with no cron at all.

## What it does

- **No plain dump on disk:** `pg_dump` streams straight into age, and only the encrypted file is written.
- **The server only needs the public key,** so a stolen server can't open its own backups.
- **Every backup is checked:** a SHA-256 checksum, and an encrypted manifest of every table's row count, which drills restore against.
- **Retention with a floor:** backups older than `RETENTION_DAYS` are pruned, but never the newest `MIN_KEEP`. Or you can leave it to your bucket's lifecycle rules, but they know nothing about `MIN_KEEP`: if backups stop, a lifecycle rule eventually deletes every last one.
- **Heartbeats:** it pings `HEARTBEAT_URL` after every good backup or drill, and `HEARTBEAT_FAIL_URL` when one fails.

## Prefer Docker?

The Docker route needs nothing but Docker. `pgb` is a small wrapper script around the Docker image, which has this same CLI inside: it reads `.env` for you, runs drills against a throwaway Postgres for you, and schedules nightly backups. Install it with `curl -fsSL https://pgbackup-r2.iotabyt.com/install.sh | sh`, then follow the [quick start](https://pgbackup-r2.iotabyt.com/start/quick-start/).

## Links

- [Documentation](https://pgbackup-r2.iotabyt.com)
- [Changelog](https://pgbackup-r2.iotabyt.com/changelog/)
- [Issues](https://github.com/iotabyt/pgbackup-r2/issues)
- [Source on GitHub](https://github.com/iotabyt/pgbackup-r2)

MIT licensed.
