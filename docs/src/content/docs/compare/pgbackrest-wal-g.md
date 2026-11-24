---
title: "pgbackup-r2 vs pgBackRest and WAL-G"
description: "pgBackRest and WAL-G are the right choice for point-in-time recovery and big Postgres databases. pgbackup-r2 is for small teams who want proven dumps."
draft: true
sidebar:
  label: "vs pgBackRest and WAL-G"
---

Short version: if you need point-in-time recovery, or your database is too big to dump and restore in a reasonable time, use [pgBackRest](https://pgbackrest.org) or [WAL-G](https://github.com/wal-g/wal-g). They're excellent, and pgbackup-r2 isn't trying to replace them.

pgbackup-r2 is for a different situation: a small team, a database or two, and the wish to know for sure that last night's backup restores.

## How they differ

pgBackRest and WAL-G take **physical** backups: a copy of the database's data files, plus a continuous archive of Postgres's write-ahead log (WAL). Replaying the WAL lets you restore to any moment, say, the minute before someone ran the wrong `DELETE`. They also support incremental backups and run in parallel, which is what makes them practical for large databases.

pgbackup-r2 takes **logical** backups with `pg_dump`: a snapshot of each run. You can restore to the last backup, not to a minute of your choosing.

| | pgBackRest, WAL-G | pgbackup-r2 |
|---|---|---|
| Backup type | Physical, plus WAL archiving | Logical (`pg_dump`) |
| Restore to any point in time | Yes | No: to the last backup |
| Large databases | Built for them | Fine while a dump and restore take minutes, not hours |
| Needs access to the server's files and WAL | Yes | No: connects like any client |
| Managed databases (Neon, Supabase and so on) | Usually not, since you don't get the server's files or WAL | Yes |
| Restore into a different setup | Same major version and setup | Any Postgres of the same major version |
| Restore drills built in | No | Yes, with a row-count check |

## When pgbackup-r2 fits

- **Your database is on a managed service** such as Neon or Supabase, where you can't install anything next to the server or archive its WAL.
- **Your data is small enough** that a nightly dump is a fine recovery point, and a restore takes minutes.
- **You want proof, not hope:** every backup carries a row-count manifest, and drills restore it into a throwaway Postgres and check it.
- **You want it boring:** one container, one `.env`, encrypted with age, into R2 or any S3.

## When to use pgBackRest or WAL-G

- You need point-in-time recovery.
- Your database is big enough that nightly dumps are slow, or restores take too long.
- You run Postgres yourself and can set up WAL archiving.

You can also run both: physical backups for point-in-time recovery, and pgbackup-r2's logical dumps as an independent, portable copy that restores into any Postgres of the same major version.
