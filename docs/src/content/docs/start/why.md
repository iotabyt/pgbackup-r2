---
title: "Why pgbackup-r2"
description: "What pgbackup-r2 believes about Postgres backups, what it does on purpose (age encryption, logical dumps, drills), and what it will never do."
next:
  link: /start/quick-start/
  label: Quick start
---

pgbackup-r2 is opinionated. It does a few things, the same way every time, and says no to the rest. This page explains why.

## What we believe

**A backup you haven't restored isn't a backup.** An upload that succeeded only proves a file arrived. So every pgbackup-r2 setup includes drills, and `doctor` nags you when the last one is more than 35 days old.

**The server should never hold the key that opens its own backups.** If the machine that takes the backups can also read them, then whoever breaks into it can too. The server gets the public key only. The private key lives in your password manager and on your laptop.

**Backups should be boring.** The best backup tool is the one you set up once and stop thinking about. It runs every night, pings a heartbeat, and only speaks up when something's wrong.

## What it does on purpose

- **It encrypts everything.** A bucket leaks, a token gets committed to a repo, a provider has a bad day. Encrypted dumps make all of those someone else's noise.
- **It runs one database per container.** One `.env`, one bucket prefix and one schedule per database keeps each setup small enough to understand at a glance, and failures stay contained.
- **It takes logical dumps.** `pg_dump` output restores into any Postgres of the same major version, anywhere. You don't need the same disk, the same host or the same cloud.
- **It matches your Postgres version.** The image's tools come from your server's major version (`PG_MAJOR`), because mismatched tools fail at restore time, the worst possible moment. See [Postgres versions](/concepts/postgres-versions/).

## Non-goals

- **Point-in-time recovery.** Restoring to the minute before an accident needs the write-ahead log, which is a different tool. Use [pgBackRest](https://pgbackrest.org) or [WAL-G](https://github.com/wal-g/wal-g).
- **A web dashboard.** Your bucket, `pgb list`, a heartbeat service and an email from CI already tell you everything. A dashboard would be one more thing to secure and keep running.
- **An agent or daemon on your database server.** pgbackup-r2 connects like any other client. The optional scheduler (`pgb nightly`, or the container's default command) is the only thing that stays running, and it can run anywhere that reaches the database.
- **An unencrypted mode, ever.** No flag, no setting, no exception. If `AGE_RECIPIENTS` is missing, the backup doesn't run.
- **Databases other than Postgres.** Doing one database well beats doing five adequately. For MySQL or SQLite, use a tool built for it.
- **Automatic restores.** Restoring over production should be a decision a person makes, with the database name typed out. pgbackup-r2 restores into a new database by default, and drills into a throwaway one.
