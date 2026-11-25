---
title: "Match pg_dump to your Postgres version"
sidebar:
  label: "Postgres versions"
description: "Why the pgbackup-r2 image must match your Postgres major version: pg_dump version mismatches, transaction_timeout errors on restore, and upgrades."
---

The images are tagged by Postgres major version: `pg16`, `pg17` and `pg18`. Each one carries `pg_dump`, `pg_restore` and `psql` from that version. **Use the tag that matches your server's major version.** `pgb` picks it from `PG_MAJOR` in `.env`:

```bash
PG_MAJOR=17      # run SHOW server_version; against your database
```

`pgb doctor` connects and checks this for you. A mismatch is a ✗:

```text
✗ Database: db/shop is PostgreSQL 17.2, but PG_MAJOR is 16
    Set PG_MAJOR=17 in .env, so the image (pg17) and the drill's scratch database match the server
```

## Why it matters

There are three ways a mismatch bites. Each one surfaces at the worst moment: in a restore.

**Older tools can't dump a newer server.** `pg_dump` 16 refuses to dump a Postgres 17 server ("server version mismatch"). You'd find out on the first backup, at least.

**Newer tools write things older servers reject.** This is the sneaky one. From version 17, `pg_dump` and `pg_restore` emit `SET transaction_timeout = 0` at the start of a restore. Postgres 16 doesn't know that setting, so restoring into a 16 server with 17 or newer tools fails:

```text
pg_restore: error: could not execute query: ERROR:  unrecognized configuration parameter "transaction_timeout"
```

The backup itself looked fine. Only a restore, or a drill, shows the problem.

**Older tools can't read newer dumps.** A dump written by a newer `pg_dump` can use a newer archive format than an older `pg_restore` understands. It stops with `unsupported version (…) in file header`.

pgbackup-r2 recognises both errors and adds a hint, but the fix is always the same: one major version everywhere.

## The drill matches too

`pgb drill` starts its throwaway database as `postgres:<PG_MAJOR>`, so the drill restores into the same major version as production. `examples/drill/compose.yml` does the same with its own `PG_MAJOR`.

## When you upgrade Postgres

1. Upgrade the server.
2. Set `PG_MAJOR` to the new version in every `.env`: the server's and your laptop's.
3. Run `pgb update` to pull the new image.
4. Restart the nightly backups: `pgb nightly stop && pgb nightly start`. For a Compose service or Kamal accessory, change the image tag and redeploy.
5. Take a backup and run a drill.

Backups made before the upgrade come from the old major version, and they restore fine with the new tools into a new server. A dump from a newer major version never restores into an older server.

This has been tested: a backup taken with Postgres 18's `pg_dump` (the `pg18` image) restores cleanly with `pg_restore` 19 into a Postgres 19 scratch server.
