# Restore drills with Docker Compose

> **Most people should use `pgb drill` instead.** It does the same thing with no compose file: see the [Quick start](https://pgbackup-r2.iotabyt.com/start/quick-start/). This folder is for people who'd rather use plain `docker compose`.

This folder restores your latest backup into a throwaway Postgres and checks that every table has the row count recorded at backup time. It works the same on Windows, macOS and Linux. You don't need to install `age`, `pg_dump` or Python.

## 1. Install Docker Desktop

Get it from <https://www.docker.com/products/docker-desktop/> and start it. On Linux, Docker Engine with the compose plugin is enough.

## 2. Copy `.env.example` to `.env` and fill it in

PowerShell:

```powershell
cd examples\drill
Copy-Item .env.example .env
notepad .env
```

bash:

```bash
cd examples/drill
cp .env.example .env
nano .env
```

You need:

- `PG_MAJOR`, which must match your server's major version (see below)
- the R2 endpoint, bucket and prefix
- an API token's two keys
- the private key (`AGE_IDENTITY=AGE-SECRET-KEY-1…`) you saved when you ran `keygen`

`.env` holds your private key, so never commit it. The repo's `.gitignore` already excludes it.

### Set `PG_MAJOR` to your server's version

`PG_MAJOR` picks both images: the scratch database (`postgres:16`) and the tool (`pgbackup-r2:pg16`). It must match the **major** version of the server your backups come from. To check, run this against your database:

```sql
SHOW server_version;
```

If the server says `17.4`, set `PG_MAJOR=17`. If you leave `PG_MAJOR` empty, it defaults to 16.

Why it matters: a dump made from a newer major version won't restore into an older one. A PG 17 dump restored into a PG 16 scratch database fails. Tools from 17 onwards also send `SET transaction_timeout`, which a 16 server rejects. A matching pair always works.

## 3. Run the drill

These commands are the same in PowerShell and bash:

```
docker compose run --rm drill
docker compose down -v
```

The first command starts a scratch Postgres, restores the latest backup into it, and compares row counts against the backup's manifest. The second throws the scratch database away.

A pass looks like this:

```
✓ Downloaded labpadi/2026-11-18T020000Z.dump.age (48.2 MB)  3.1s
✓ Checksum matches  0.2s
✓ Decrypted, and it's a pg_dump archive  0.4s
✓ Restored into postgresql://***@scratch:5432/drill  21.4s
✓ Read the manifest  0.1s
✓ Counted restored rows  1.2s

  table                expected   restored
 ───────────────────────────────────────────
  public."Readings"   1,204,511  1,204,511   ok
  public.schools            250        250   ok
  …

╭──────────────────────────────────────────────────────────╮
│                                                          │
│   PASS   labpadi/2026-11-18T020000Z.dump.age             │
│         (48.2 MB, restored in 21.4s)                     │
│         37 tables and 1,284,903 rows match the manifest  │
│                                                          │
╰──────────────────────────────────────────────────────────╯
```

In CI or with `--plain` you get the plain version:

```
PASS  labpadi/2026-11-18T020000Z.dump.age  (48.2 MB, restored in 21.4s)
  37 tables and 1,284,903 rows match the manifest
```

A failure prints `FAIL` and one line per problem, and exits with code 1.

To drill an older backup, give its stamp, which you can find with `pgb list`:

```
pgb list
docker compose run --rm drill drill 2026-11-01T020000Z
```

## Other things this folder can do

These use `pgb`, the short wrapper from [Install pgb](https://pgbackup-r2.iotabyt.com/start/install/). Run them from this folder so `pgb` picks up its `.env`. `pgb drill` works here too: it starts its own throwaway database and ignores `compose.yml`.

If you haven't installed `pgb`, `docker compose run --rm drill <command>` does the same. On Linux, add `--user "$(id -u):$(id -g)"` for `decrypt`, so you can write to `./out`.

### Take a backup from your laptop

Add two more lines to `.env`, and the same folder can back up too:

```bash
DATABASE_URL=postgresql://user:password@host:5432/app?sslmode=require
AGE_RECIPIENTS=age1...      # the PUBLIC key from keygen
```

Then:

```
pgb backup
pgb list
```

`DATABASE_URL` must be a **direct** connection, not one that goes through a pooler. `pg_dump` needs a real session, so a transaction-mode pooler (PgBouncer, Supavisor) breaks it. On Neon, use the connection string whose host doesn't contain `-pooler`.

Backing up and drilling from the same `.env` is safe. The drill only ever restores into the scratch database, and it refuses to run if `DRILL_DATABASE_URL` is the same database as `DATABASE_URL`.

For nightly backups, run `pgb nightly start` on a server that's always on, or one of the other setups in the [guides](https://pgbackup-r2.iotabyt.com/guides/vps/). A laptop that's asleep at 02:00 won't take them.

### Check a backup without a database

Quick check, with no database: does the backup download, decrypt, and contain a real dump?

```
pgb verify
```

### Get a plain dump file

Write a plain `pg_dump` file into `./out`, to restore somewhere else or hand to someone:

```
pgb decrypt latest --out /out/backup.dump
```

The file appears as `out/backup.dump` next to this README, owned by you. Restore it with `pg_restore --no-owner --no-acl -d <db> out/backup.dump`.

To check a `.dump.age` you already downloaded, put it in `./out` and point at it there: `pgb verify --file /out/2026-11-18T020000Z.dump.age`.

## Notes

- Change `PG_MAJOR` in `.env` when you upgrade Postgres. Once you upgrade, new backups come from the new major version, and they need the matching images to restore.
- The scratch database has no published ports, and `down -v` deletes it along with all the restored data.
