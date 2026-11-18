# Restore drill with nothing but Docker

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

You need the R2 endpoint, bucket and prefix, an API token's two keys, and the private key (`AGE_IDENTITY=AGE-SECRET-KEY-1…`) you saved when you ran `keygen`. `.env` holds your private key, so never commit it. The repo's `.gitignore` already excludes it.

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

To drill an older backup, give its stamp, which you can find with `list`:

```
docker compose run --rm drill list
docker compose run --rm drill drill 2026-11-01T020000Z
```

## Other things this folder can do

Quick check, with no database: does the backup download, decrypt, and contain a real dump?

```
docker compose run --rm drill verify
```

Write a plain `pg_dump` file into `./out`, to restore somewhere else or hand to someone:

```
docker compose run --rm drill decrypt latest --out /out/backup.dump
```

The file appears as `out/backup.dump` next to this README. Restore it with `pg_restore --no-owner --no-acl -d <db> out/backup.dump`.

On Linux, the container runs as uid 10001, which can't write to a folder you own. Run it as yourself instead:

```bash
docker compose run --rm --user "$(id -u):$(id -g)" drill decrypt latest --out /out/backup.dump
```

## Notes

- Match the images to your server's Postgres major version. For PG 17, use `postgres:17` in `compose.yml` and set `PGBACKUP_IMAGE=ghcr.io/iotabyt/pgbackup-r2:pg17` in `.env`.
- The scratch database has no published ports, and `down -v` deletes it along with all the restored data.
