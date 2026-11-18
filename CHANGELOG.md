# Changelog

## 0.1.1 (unreleased)

- `restore`: the target can come from `RESTORE_DATABASE_URL`, so the password stays out of the process list. `--target` still works and overrides it. With neither set, `restore` fails with a clear message.
- `examples/drill`: `PG_MAJOR` in `.env` picks both images (`postgres:${PG_MAJOR}` and `pgbackup-r2:pg${PG_MAJOR}`, default 16). The README explains why it must match the server's major version.
- `examples/drill`: the same folder and `.env` can now take backups too (`docker compose run --rm drill backup`). `.env.example` has `DATABASE_URL`, which must be a direct, non-pooled connection, and `AGE_RECIPIENTS`.
- `pgb`: a short wrapper around `docker run` (`scripts/pgb` for bash, `scripts/pgb.ps1` for PowerShell). It uses `./.env`, takes the image tag from `PG_MAJOR`, mounts `./out` at `/out`, adds `-it` at a terminal, and runs as you on Linux. `PGB_DOCKER_ARGS` passes extra Docker options. The README now uses `pgb` in its examples, with one-line installs.
- CI: `actions/checkout@v5` and `astral-sh/setup-uv@v7`, with monthly Dependabot updates for GitHub Actions and uv.

## 0.1.0 (unreleased)

First release.

- `backup`: `pg_dump -Fc` piped into `age`, uploaded to R2/S3 with a SHA-256 in the object metadata, plus an encrypted manifest of every table's row count. Refuses to run without `AGE_RECIPIENTS`.
- `drill`: restores into a scratch database and compares row counts against the manifest. Reports missing tables, extra tables and drifted counts.
- `verify`: checksum, decrypt and table of contents, with no database.
- `decrypt`: writes a plain `pg_dump` file, from the bucket or from a local `.dump.age` (`--file`).
- `restore`: restores into `--target URL`. Refuses the live database unless `--to-production` is passed and the database name is typed to confirm.
- `list`, `prune` (keeps the newest `MIN_KEEP`), `keygen`, and `schedule`, which is the container default and needs no cron.
- Heartbeat pings on success and failure. A failed ping never fails a backup.
- Interactive terminal output with rich (spinners, tables, PASS/FAIL), and plain log lines everywhere else (`--plain`, `NO_COLOR`).
- Docker images for Postgres 16, 17 and 18 (`ghcr.io/iotabyt/pgbackup-r2:pg16` and so on), plus a Docker-only drill kit in `examples/drill`.
