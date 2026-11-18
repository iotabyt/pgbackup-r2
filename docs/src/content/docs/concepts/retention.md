---
title: Retention
description: Tool-managed or bucket-managed retention, MIN_KEEP, and object locks.
---

Old backups have to go somewhere. pgbackup-r2 can delete them itself, or leave that to your bucket.

## Tool-managed (the default)

Backups go straight under `S3_PREFIX`. After each backup, `prune` deletes dumps and their manifests older than `RETENTION_DAYS` (default 30).

**`MIN_KEEP`** (default 7) is a floor: the newest `MIN_KEEP` backups are never pruned, however old they are. If backups stop for two months, the last seven are still there when you notice.

```bash
RETENTION_DAYS=30
MIN_KEEP=7
```

- `pgb prune --dry-run` shows what would go, without deleting anything.
- `pgb backup --no-prune` skips pruning once.
- `PRUNE=false` turns it off for good.

## Bucket-managed (TIERS)

Set `TIERS`, and backups spread over your own folders: `daily/`, `weekly/`, `monthly/` and so on.

- **Uploads and copies:** the most frequent tier gets every upload. The others get a server-side copy of the first backup of each week, month or year.
- **Deleting:** the bucket's lifecycle rules do it, and `PRUNE` defaults to `false`.

```bash
TIERS="daily=daily/ monthly=monthly/"
```

With lifecycle rules of 30 days on `daily/` and 365 on `monthly/`, you keep a month of nightly backups and a year of monthly ones. [Use your existing folders](/guides/existing-folders/) has the full syntax and example rules.

If you set `PRUNE=true` with `TIERS`, only the most frequent tier is pruned, by `RETENTION_DAYS` with the `MIN_KEEP` floor. The other tiers are never touched by the tool.

## Which to choose

| | Tool-managed | Bucket-managed |
|---|---|---|
| Setup | nothing | `TIERS` plus lifecycle rules |
| Layout | one folder | your folders |
| Long-term (monthly, yearly) copies | no | yes |
| Deletes happen | after each backup | whenever the bucket's rules run |
| Works with an existing layout | no | yes, `pgb init --detect` finds it |

## Object lock and bucket locks

A lock makes recent backups impossible to delete or overwrite for a while, even with the right token. It's your defence against a leaked token or ransomware that deletes backups first.

- **R2** has bucket lock rules per prefix: see the [example](/guides/existing-folders/#example-lifecycle-and-lock-rules-on-r2).
- **AWS S3** has Object Lock.

If `prune` (or anything else) tries to delete a locked backup, that backup is skipped with a warning: `locked, skipped`. The backup run itself doesn't fail. Keep locks shorter than the retention period for the same files, or old backups simply stay until their lock ends.

## Things retention never touches

- **Other files.** Anything that isn't a pgbackup-r2 dump or manifest, such as other tools' dumps or notes, is left alone and counted by `doctor`.
- **Drill records** in `<prefix>/.drills/`.
