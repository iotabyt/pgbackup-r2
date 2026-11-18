# Changelog

## 0.1.0 (unreleased)

First release.

- `backup`: `pg_dump -Fc` piped into `age`, uploaded to R2/S3 with a SHA-256 in the object metadata, plus an encrypted manifest of every table's row count. Refuses to run without `AGE_RECIPIENTS`.
- `drill`: restores into a scratch database and compares row counts against the manifest. Reports missing tables, extra tables and drifted counts.
- `verify`: checksum, decrypt and table of contents, with no database.
- `decrypt`: writes a plain `pg_dump` file, from the bucket or from a local `.dump.age` (`--file`).
- `restore`: restores into `RESTORE_DATABASE_URL`, or `--target URL` (the env var keeps the password out of the process list). Refuses the live database unless `--to-production` is passed and the database name is typed to confirm.
- `list`, `prune` (keeps the newest `MIN_KEEP`), `keygen`, and `schedule`, which is the container default and needs no cron.
- Heartbeat pings on success and failure. A failed ping never fails a backup.
- Interactive terminal output with rich (spinners, tables, PASS/FAIL), and plain log lines everywhere else (`--plain`, `NO_COLOR`).
- Docker images for Postgres 16, 17 and 18 (`ghcr.io/iotabyt/pgbackup-r2:pg16` and so on), plus a Docker-only drill kit in `examples/drill`.
