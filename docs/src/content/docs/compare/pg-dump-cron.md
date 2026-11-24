---
title: "pgbackup-r2 vs a pg_dump cron script"
description: "A pg_dump cron script gets a file into S3. Here's what it usually misses: encryption with no plaintext on disk, checksums, a retention floor, drills."
draft: true
sidebar:
  label: "vs a pg_dump cron script"
---

Most Postgres backups start as a few lines in cron, something like this:

```bash
pg_dump "$DATABASE_URL" | gzip > /tmp/db.sql.gz
aws s3 cp /tmp/db.sql.gz "s3://backups/db-$(date +%F).sql.gz"
find /backups -mtime +30 -delete
```

That's a fine start. It gets a file into a bucket. The trouble is everything it doesn't tell you. Here's what the script misses, and what pgbackup-r2 does instead.

## Encryption, without a plain copy on disk

The script above writes the whole database, in plain text, to `/tmp` before uploading it. Anyone who can read that disk, or the bucket, can read your data.

pgbackup-r2 pipes `pg_dump` straight into [age](https://age-encryption.org). The only file that ever touches disk is the encrypted one. The server only holds the **public** key, so even a stolen server can't open its own backups. See [Keys and security](/concepts/keys/).

## Checksums

If an upload is cut short, or a file changes in the bucket, the script never notices. pgbackup-r2 stores a SHA-256 of every backup in the object's metadata, and checks it before every restore, drill or verify.

## A retention floor

`find -mtime +30 -delete` deletes by age, whatever happened. If backups quietly stopped six weeks ago, it eventually deletes the last good one.

pgbackup-r2 prunes by `RETENTION_DAYS` too, but never the newest `MIN_KEEP` backups, however old they are. Or you can leave retention to your bucket's lifecycle rules. See [Retention](/concepts/retention/).

## Knowing when it stops

A cron job that fails sends mail to a mailbox nobody reads, and a cron job that never runs sends nothing at all. pgbackup-r2 pings a heartbeat URL (healthchecks.io, Better Stack, Uptime Kuma) after every good backup, and a different one on failure, so silence itself raises the alarm.

## Proof that it restores

This is the big one. A script tells you a file arrived. It doesn't tell you the file restores, that every table made it in, or that you can still decrypt it.

pgbackup-r2 counts every table's rows just before the dump and stores the counts in an encrypted manifest. A [drill](/guides/monthly-drills/) restores the backup into a throwaway Postgres and checks every count. `pgb doctor` nags you when the last good drill is more than 35 days old.

## The small things

- **Matching tools.** A `pg_dump` older or newer than your server either refuses to run or writes something your server won't restore. pgbackup-r2's images are tagged by Postgres major version. See [Postgres versions](/concepts/postgres-versions/).
- **Passwords out of `ps`.** Connection details reach `pg_dump` through its environment, never its command line.

## When the script is enough

If you restore from your backups regularly anyway, encrypt them some other way, and watch the job, a script can be all you need. pgbackup-r2 is for when you'd rather not build and maintain those pieces yourself.
