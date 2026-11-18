# pgbackup-r2

Encrypted Postgres backups to Cloudflare R2 (or any S3), with restore drills that prove they work.

Most backup setups stop at "upload succeeded". That tells you a file arrived. It doesn't tell you whether that file restores, whether every table made it in, or whether you can still decrypt it. `pgbackup-r2 drill` restores a backup into a scratch database and checks every table's row count against a manifest written at backup time:

```
PASS  labpadi/2026-11-18T020000Z.dump.age  (48.2 MB, restored in 21.4s)
  37 tables and 1,284,903 rows match the manifest
```

It's built for small teams running a few apps on a VPS: one container per database, no agents, no dashboards. Everything works with **only Docker installed**, on Windows, macOS or Linux.

## What it does

- Takes logical backups with `pg_dump -Fc`, piped straight into [`age`](https://age-encryption.org) so the unencrypted dump never touches disk.
- Refuses to run without a public key. An unencrypted backup isn't possible.
- Uploads to R2 or any S3-compatible store as `<prefix>/<stamp>.dump.age`, with a SHA-256 checksum in the object metadata.
- Records the exact row count of every table in an encrypted manifest next to each dump.
- Runs on a schedule inside the container (`BACKUP_AT=02:00`), with no cron daemon.
- Prunes backups past `RETENTION_DAYS`, but never the newest `MIN_KEEP`.
- Pings a heartbeat URL (healthchecks.io, Better Stack, Uptime Kuma) on success, and a different one on failure.
- Restore drills (`drill`) restore a backup and compare row counts. `verify` is a quick check that needs no database.
- `decrypt` gives you a plain `pg_dump` file to restore somewhere the tool can't reach.
- Interactive output (spinners, tables, a big PASS/FAIL) when you run it in a terminal. Plain log lines in containers, cron and CI.

## How keys work

`age` uses a key pair. The **public key** (`age1…`) can only encrypt. The **private key** (`AGE-SECRET-KEY-1…`) decrypts.

The server only ever holds the public key. If someone steals the server or the bucket leaks, all they get is encrypted blobs. The private key lives in your password manager and on whatever machine runs restores and drills: your laptop, or a GitHub Actions secret. It never goes on the server.

## Quick start (Docker only)

You only need Docker. The commands below use `pgb`, a short wrapper around `docker run`. Where PowerShell and bash differ, both are shown.

### 0. Install the `pgb` shortcut

bash (Linux, macOS, WSL):

```bash
mkdir -p ~/.local/bin && curl -fsSL https://raw.githubusercontent.com/iotabyt/pgbackup-r2/main/scripts/pgb -o ~/.local/bin/pgb && chmod +x ~/.local/bin/pgb
```

(`~/.local/bin` must be on your `PATH`. On macOS it often isn't. Add `export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc`.)

PowerShell (Windows):

```powershell
New-Item -ItemType Directory -Force (Split-Path $PROFILE) | Out-Null; irm https://raw.githubusercontent.com/iotabyt/pgbackup-r2/main/scripts/pgb.ps1 | Add-Content $PROFILE; . $PROFILE
```

(If PowerShell refuses to load your profile, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.)

`pgb <command>` runs the image with the `.env` from the folder you're in. For example, on Linux, `pgb verify` is:

```bash
docker run --rm -it --env-file .env -v "$PWD/out:/out" --user "$(id -u):$(id -g)" \
  ghcr.io/iotabyt/pgbackup-r2:pg16 verify
```

What `pgb` adds to `docker run`:

- **`.env`:** used if the current folder has one.
- **Image tag:** taken from `PG_MAJOR` in `.env` (default 16).
- **Output folder:** `./out` is mounted at `/out` (`decrypt` creates it).
- **Terminal:** `-it` is added when you're at one.
- **User:** on Linux, the container runs as you, so files in `./out` are yours.

If you'd rather not install anything, type the `docker run` line yourself. To pass extra Docker options, set `PGB_DOCKER_ARGS`. For example, use `PGB_DOCKER_ARGS="--network host"` to reach a database on `localhost` (Linux).

### 1. Make a key pair

```
pgb keygen
```

This prints a public key for the server and a private key for your laptop. Nothing is written to disk. **Save the private key in your password manager now.** If you lose it, nobody can open your backups, including you.

For copy-paste friendly output, add `--plain`. The keys then come out exactly as they go in a `.env` file:

```
pgb keygen --plain
```

For backups and restores, the image tag must match your Postgres major version (`pg16`, `pg17` or `pg18`). `pgb` takes it from `PG_MAJOR` in `.env`. See [Good to know](#good-to-know).

### 2. Create an R2 bucket and an API token

In the [Cloudflare dashboard](https://dash.cloudflare.com):

1. Click **R2 Object Storage** in the sidebar. If this is your first time, accept the R2 terms.
2. Click **Create bucket**. Name it (e.g. `acme-db-backups`), leave **Location** on *Automatic*, and click **Create bucket**.
3. Back on the R2 overview page, click **Manage API tokens** (under *Account details* → *API Tokens*).
4. Click **Create Account API token**. (A *User API token* works too.)
5. Give it a name like `pgbackup-acme`.
6. Under **Permissions**, choose **Object Read & Write**. `prune` needs to delete objects, and this permission includes that.
7. Under **Specify bucket(s)**, choose **Apply to specific buckets only** and pick your bucket.
8. Leave **TTL** on *Forever* (or set an expiry and a reminder to rotate), then click **Create API Token**.
9. Copy the **Access Key ID** and **Secret Access Key**. The secret is shown only once. Also copy the endpoint shown for S3 clients, `https://<account-id>.r2.cloudflarestorage.com`.

For laptop drills you can make a second token with **Object Read only**.

### 3. Add the backup service on the server

**Docker Compose.** Put the service next to your database in the same `compose.yml`:

```yaml
services:
  db:
    image: postgres:16
    # ... your existing database

  backup:
    image: ghcr.io/iotabyt/pgbackup-r2:pg16
    restart: unless-stopped
    env_file: backup.env
    depends_on: [db]
```

Then add `backup.env`, and keep it out of git:

```bash
DATABASE_URL=postgresql://app:password@db:5432/app
S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
S3_BUCKET=acme-db-backups
S3_PREFIX=labpadi
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AGE_RECIPIENTS=age1...            # the PUBLIC key only
BACKUP_AT=02:00                   # UTC; "02:00,14:00" for twice a day
HEARTBEAT_URL=https://hc-ping.com/<uuid>
HEARTBEAT_FAIL_URL=https://hc-ping.com/<uuid>/fail
```

Start it, take a first backup straight away, and list what's in the bucket:

```
docker compose up -d backup
docker compose run --rm backup backup
docker compose run --rm backup list
```

The service runs `schedule` by default. It sleeps until `BACKUP_AT`, backs up, prunes and pings. Its output in `docker compose logs backup` is plain timestamped lines:

```
2026-11-18T02:00:01Z INFO backing up postgresql://***@db:5432/app to s3://acme-db-backups/labpadi/2026-11-18T020001Z.dump.age
2026-11-18T02:00:23Z INFO backed up labpadi/2026-11-18T020001Z.dump.age (48.2 MB, 37 tables) in 22.1s
```

**Kamal 2 accessory.** In `config/deploy.yml`:

```yaml
accessories:
  backup:
    image: ghcr.io/iotabyt/pgbackup-r2:pg16
    host: 203.0.113.10
    env:
      clear:
        S3_ENDPOINT: https://<account-id>.r2.cloudflarestorage.com
        S3_BUCKET: acme-db-backups
        S3_PREFIX: labpadi
        AGE_RECIPIENTS: age1...
        BACKUP_AT: "02:00"
      secret:
        - DATABASE_URL          # e.g. postgresql://app:pw@labpadi-db:5432/app
        - AWS_ACCESS_KEY_ID
        - AWS_SECRET_ACCESS_KEY
        - HEARTBEAT_URL
        - HEARTBEAT_FAIL_URL
```

Put the secrets in `.kamal/secrets`, then:

```
kamal accessory boot backup
kamal accessory exec backup --reuse "pgbackup-r2 backup"
kamal accessory logs backup
```

### 4. Prove it works, from your laptop

Copy the [`examples/drill`](examples/drill) folder to your laptop, fill in `.env` from `.env.example`, and run the commands below. `.env` needs `PG_MAJOR` (your server's major version), the endpoint, bucket, prefix and token, and your **private** key.

```
pgb verify
docker compose run --rm drill
docker compose down -v
```

`pgb verify` downloads the latest backup, checks the checksum, decrypts it, and lists the tables inside, all in a few seconds and with no database. The drill needs a throwaway Postgres next to it, so it runs through the folder's `compose.yml`: it restores into the scratch database and compares every table's row count. `down -v` deletes the scratch database.

To fill in `.env` in PowerShell:

```powershell
cd examples\drill
Copy-Item .env.example .env
notepad .env
```

In bash:

```bash
cd examples/drill
cp .env.example .env
nano .env
```

[`examples/drill/README.md`](examples/drill/README.md) has the details, including `pgb decrypt latest --out /out/backup.dump` into a normal folder. Add `DATABASE_URL` and `AGE_RECIPIENTS` to the same `.env`, and the folder can also take backups from your laptop with `pgb backup`.

### 5. Optional: a monthly drill in GitHub Actions

[`examples/monthly-drill.yml`](examples/monthly-drill.yml) runs a drill on the 1st of every month for each app prefix, against a Postgres service container. Copy it into `.github/workflows/` in a **private** repo and add the secrets listed at the top of the file. A failed drill fails the workflow, so GitHub emails you. If you set `DRILL_FAIL_URL`, the failure summary is also POSTed there.

## Restoring for real

Restore into a new database, check it, then switch your app over. Put the target in your `.env` next to the other settings:

```bash
RESTORE_DATABASE_URL=postgresql://user:pw@host:5432/app_restored
```

Then run:

```
pgb restore latest
```

The target has to be reachable from inside the container. A database on your own machine is not `localhost` there. On Linux, use `PGB_DOCKER_ARGS="--network host"`. With Docker Desktop, use `host.docker.internal` as the host.

`--target URL` still works and takes precedence over `RESTORE_DATABASE_URL`, but prefer the env var. Anything on a command line, the password included, is visible to every user on the machine who runs `ps`, and it ends up in your shell history too. From `.env`, the URL only ever lives in environment variables, and `pg_restore` gets the password through `PGPASSWORD` either way.

If the target is the same database as `DATABASE_URL`, the restore is refused. To overwrite production deliberately, pass `--to-production`. You'll then have to type the database name at an interactive terminal (`-it`) to confirm.

## Install without Docker

If you'd rather not use Docker:

```bash
pip install pgbackup-r2      # or: uv tool install pgbackup-r2
```

This needs `pg_dump`, `pg_restore` and `psql` from the same Postgres major version as your server, plus `age` (`apt install age`, `brew install age`), all on `PATH`. Set the same environment variables, then run from plain cron:

```cron
# /etc/cron.d/pgbackup-r2
0 2 * * *  backup  . /etc/pgbackup-r2.env && pgbackup-r2 backup
```

Use `export VAR=value` lines in the env file when you source it like this. Outside a terminal, output is plain log lines, so cron mail and log files stay readable.

## Reference

### Commands

| Command | What it does |
|---|---|
| `backup [--no-prune]` | Count rows, dump, encrypt, upload dump and manifest, prune, ping `HEARTBEAT_URL`. |
| `list` | Backups under the prefix, oldest first: stamp, size, key (and age, in a terminal). |
| `prune [--dry-run]` | Delete dumps and manifests older than `RETENTION_DAYS`, always keeping the newest `MIN_KEEP`. |
| `restore [STAMP\|latest] [--target URL] [--to-production]` | Checksum, decrypt, `pg_restore --clean --if-exists` into `RESTORE_DATABASE_URL` (or `--target`). Refuses the live database. |
| `drill [STAMP\|latest]` | Restore into `DRILL_DATABASE_URL` and compare every table's row count with the manifest. Exits 1 on failure. |
| `verify [STAMP\|latest] [--file PATH]` | Checksum, decrypt, and list the dump's tables with manifest counts. No database needed. |
| `decrypt [STAMP\|latest] --out PATH [--force] [--file PATH]` | Write a plain `pg_dump` custom-format file. `--file` decrypts a local `.dump.age` with no S3 settings. |
| `keygen` | Print a new age key pair, ready for `.env` files. Writes nothing. |
| `schedule [--at HH:MM[,HH:MM]] [--run-now]` | Stay running and back up at the given UTC times. The container's default command. |

Global flags: `--plain` (no colour, spinners or tables), `-q` (warnings only), `--version`, `--help`.

Output is interactive when stdout is a terminal and `NO_COLOR` isn't set. Otherwise it's plain. `schedule` is always plain. Docker only gives a container a terminal with `-it`. `pgb` and `docker compose run` add it for you.

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | | `postgres://` URL of the database to back up. Required for `backup`. Also used to refuse restores and drills into it. |
| `S3_BUCKET` | | Always required (except `decrypt`/`verify --file`). |
| `S3_PREFIX` | none | One per app, e.g. `labpadi`. |
| `S3_ENDPOINT` | | e.g. `https://<account-id>.r2.cloudflarestorage.com`. Leave unset for AWS S3. |
| `S3_REGION` | `auto` | `auto` is right for R2. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | | The R2/S3 token. Read by boto3. |
| `AGE_RECIPIENTS` | | Comma-separated `age1…` public keys. Required for `backup`. |
| `AGE_IDENTITY` | | The private key itself (`AGE-SECRET-KEY-1…`). Only for `restore`, `drill`, `verify` and `decrypt`. Never on the server. |
| `AGE_IDENTITY_FILE` | | A path to a private key file, instead of `AGE_IDENTITY`. Don't set both. |
| `DRILL_DATABASE_URL` | | Scratch database for `drill`. Everything in it can be overwritten. |
| `RESTORE_DATABASE_URL` | | Where `restore` restores to. `--target` overrides it. Prefer this to `--target`, which shows the password in `ps`. |
| `RETENTION_DAYS` | `30` | |
| `MIN_KEEP` | `7` | The newest N backups are never pruned. |
| `BACKUP_AT` | `02:00` | UTC times for `schedule`, comma-separated. |
| `HEARTBEAT_URL` | | GET after a successful backup or drill. |
| `HEARTBEAT_FAIL_URL` | | POST with the error text when a backup or drill fails. |
| `DRILL_TOLERANCE` | `0.01` | Allowed row-count drift per table: `max(1, expected × tolerance)`, exact for empty tables. |

Connection details reach `pg_dump`, `pg_restore` and `psql` as `PGHOST`/`PGPASSWORD`/… in their environment, never as arguments, so passwords don't show up in `ps`. Passwords in logged URLs are shown as `***`.

## Good to know

- **Match the image to your Postgres major version.** Use `pg16` for a PG 16 server, and so on. `pg_dump` refuses to dump a server newer than itself, and `pg_dump`/`pg_restore` 17+ emit `SET transaction_timeout`, which a PG 16 server rejects on restore. That's why the images are tagged `pg16`, `pg17` and `pg18`. A dump from a newer major version won't restore into an older server. When you upgrade Postgres, switch the tag (and `PG_MAJOR` in `examples/drill/.env`).
- **Logical backups only.** You get a snapshot of each nightly run, not point-in-time recovery. For PITR, or databases too big to dump and restore in a reasonable time, use [pgBackRest](https://pgbackrest.org) or [WAL-G](https://github.com/wal-g/wal-g).
- **Roles and ownership aren't restored.** Dumps use `--no-owner --no-acl`, so restored objects belong to the user you restore as. Recreate roles and grants yourself.
- **Lose the private key and the backups are gone.** There's no recovery. Consider a second recipient: generate another key pair, store that private key offline (printed, or on a USB stick in a safe), and list both public keys in `AGE_RECIPIENTS=age1first...,age1second...`.
- Row counts are taken just before the dump, in a separate transaction. A busy table can drift slightly, which is what `DRILL_TOLERANCE` allows for.
- A drill restores with `--clean`, so leftover tables in the scratch database show up as "extra table" failures. Start from an empty database. The `examples/drill` setup does this for you.

## Development

```bash
uv sync
uv run ruff check && uv run ruff format --check
uv run pytest
```

The end-to-end tests need a real Postgres, plus `age` and Postgres client tools of the same major version, on `PATH`. In WSL or Ubuntu:

```bash
sudo apt install age postgresql-client-16
docker run -d --name pgbackup-test -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16
PATH=/usr/lib/postgresql/16/bin:$PATH \
PGBACKUP_TEST_URL=postgresql://postgres:pw@localhost:5432/postgres uv run pytest -v
```

S3 is mocked with moto. For a manual run against a fake S3, start `uv run moto_server -p 5055` and set `S3_ENDPOINT=http://localhost:5055`.

To build an image: `docker build --build-arg PG_MAJOR=16 -t pgbackup-r2:pg16 .`

## License

MIT. Copyright (c) 2026 Frank Owusu Boakye.
