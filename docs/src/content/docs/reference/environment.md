---
title: "Environment variables"
description: "Every setting pgbackup-r2 and pgb read, with defaults: DATABASE_URL, the R2 and S3 settings, age keys, BACKUP_AT, retention, TIERS and heartbeats."
---

Settings come from environment variables, usually a `.env` file that Docker reads. Write values without quotes, and keep comments on their own lines: `docker --env-file` keeps quotes and trailing comments as part of the value. The one exception is `TIERS`, which accepts quotes. An empty value counts as unset.

## Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | | `postgresql://user:password@host:5432/dbname` of the database to back up. Required for `backup`. Also used to refuse restores and drills into the live database. Use a direct, non-pooled connection. `?sslmode=require` is honoured. |
| `PG_MAJOR` | `16` | Your server's major version. `pgb` uses it for the image tag (`pg17`) and the drill's Postgres. `doctor` checks it against the server. |
| `RESTORE_DATABASE_URL` | | Where `restore` restores to. `--target` overrides it. Prefer this to `--target`, which shows the password in `ps`. |
| `DRILL_DATABASE_URL` | | A scratch database for `drill`. Everything in it can be overwritten. `pgb drill` sets this for you. |
| `DRILL_TOLERANCE` | `0.01` | The allowed row-count drift per table: `max(1, expected × tolerance)`. Empty tables must match exactly. |

## Storage

| Variable | Default | Notes |
|---|---|---|
| `S3_BUCKET` | | Required, except for `decrypt --file` and `verify --file`. |
| `S3_PREFIX` | none | One per app, e.g. `shop`. `pgb nightly` names its container after it. |
| `S3_ENDPOINT` | | `https://<account-id>.r2.cloudflarestorage.com`, or `https://<account-id>.eu.r2.cloudflarestorage.com` for an EU-jurisdiction bucket. Leave it empty for AWS S3. See [Other storage](/guides/other-storage/). |
| `S3_REGION` | `auto` | `auto` is right for R2. For AWS and B2, use the bucket's region. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | | The R2/S3 token. |

## Keys

| Variable | Default | Notes |
|---|---|---|
| `AGE_RECIPIENTS` | | Comma-separated `age1…` public keys. Required for `backup`. |
| `AGE_IDENTITY` | | The private key itself (`AGE-SECRET-KEY-1…`). Only for `restore`, `drill`, `verify` and `decrypt`. Never on the server. |
| `AGE_IDENTITY_FILE` | | A path to a private key file, instead of `AGE_IDENTITY`. Prefer it where secrets are mounted as files: shared machines, CI, Kamal. See [Keys and security](/concepts/keys/#key-in-a-file-or-in-the-environment). Don't set both. |

## Schedule, retention and heartbeats

| Variable | Default | Notes |
|---|---|---|
| `BACKUP_AT` | `02:00` | UTC times for `schedule` and `pgb nightly`, comma-separated: `02:00,14:00`. |
| `RETENTION_DAYS` | `30` | `prune` deletes backups older than this. |
| `MIN_KEEP` | `7` | The newest N backups are never pruned. |
| `PRUNE` | `true`, or `false` with `TIERS` | Prune after each backup. With `TIERS`, only the most frequent tier is pruned. |
| `TIERS` | | Use tier folders, e.g. `"daily=daily/ monthly=monthly/"`. See [Use your existing folders](/guides/existing-folders/). |
| `HEARTBEAT_URL` | | GET after a successful backup or drill. |
| `HEARTBEAT_FAIL_URL` | | POST with the error text when a backup or drill fails. A failed ping never fails a backup. |

## pgb only

| Variable | Where | Notes |
|---|---|---|
| `PGB_NETWORK` | `.env` or shell | A Docker network to join, e.g. a Compose project's `myapp_default`. |

pgb also passes two variables of its own to the container. Don't set them yourself:

- `PGB_VERSION`, which tells the image it runs under `pgb`, so help and messages say `pgb`
- `PGB_ENV_KEYS`, for `pgb doctor` only: the names in `.env`, never the values, so `doctor` can catch a misspelt setting

## Output

| Variable | Notes |
|---|---|
| `NO_COLOR` | Any non-empty value turns off colour and interactive output. |
