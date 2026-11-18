<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/mark-reversed.svg">
  <img src=".github/assets/mark.svg" width="64" height="64" alt="pgbackup-r2 logo">
</picture>

# pgbackup-r2

Encrypted Postgres backups to Cloudflare R2 or any S3, with restore drills that prove they work.

Built and used in production by [Iotabyt](https://www.iotabyt.com).

```
PASS  shop/2026-11-18T020000Z.dump.age  (48.2 MB, restored in 21.4s)
  37 tables and 1,284,903 rows match the manifest
```

`pg_dump` goes straight into [age](https://age-encryption.org), and the result goes to your bucket with a checksum and an encrypted row-count manifest. A drill restores a backup into a throwaway Postgres and checks every table against that manifest. All you need is Docker.

**Documentation: [pgbackup-r2.iotabyt.com](https://pgbackup-r2.iotabyt.com)**

## Quick start

```bash
curl -fsSL https://pgbackup-r2.iotabyt.com/install.sh | sh
mkdir acme-backups && cd acme-backups && pgb init   # writes a commented .env
pgb keygen            # then fill in .env: bucket, token, DATABASE_URL and both keys
pgb doctor            # checks Docker, the database, the bucket and the keys
pgb backup && pgb drill
pgb nightly start     # back up every night at 02:00 UTC
```

The [Quick start](https://pgbackup-r2.iotabyt.com/start/quick-start/) explains each step. Other starting points:

- [Install `pgb`](https://pgbackup-r2.iotabyt.com/start/install/), for bash, zsh, fish and PowerShell
- [Set up R2](https://pgbackup-r2.iotabyt.com/guides/r2/), or [other storage](https://pgbackup-r2.iotabyt.com/guides/other-storage/)
- [An app on a VPS](https://pgbackup-r2.iotabyt.com/guides/vps/), or [a managed database with GitHub Actions](https://pgbackup-r2.iotabyt.com/guides/managed-databases/)
- [Use your existing folders](https://pgbackup-r2.iotabyt.com/guides/existing-folders/): `daily/` and `monthly/`, lifecycle rules and bucket locks
- [Restoring on a bad day](https://pgbackup-r2.iotabyt.com/guides/restoring/)
- [Reference](https://pgbackup-r2.iotabyt.com/reference/pgb/) and [Troubleshooting](https://pgbackup-r2.iotabyt.com/troubleshooting/)

## Development

Contributions are welcome: see [`CONTRIBUTING.md`](CONTRIBUTING.md). In short:

```bash
uv sync
uv run ruff check && uv run ruff format --check
uv run shellcheck scripts/pgb scripts/install.sh
uv run pytest
```

The `pgb` tests run both `scripts/pgb` and `scripts/pgb.ps1`. They need `pwsh` for the PowerShell half, and Docker for the drill-cleanup and bash 3.2 tests. Each part is skipped if its tool is missing.

The end-to-end tests need a real Postgres, plus `age` and Postgres client tools of the same major version, on `PATH`. In WSL or Ubuntu:

```bash
sudo apt install age postgresql-client-16
docker run -d --name pgbackup-test -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16
PATH=/usr/lib/postgresql/16/bin:$PATH \
PGBACKUP_TEST_URL=postgresql://postgres:pw@localhost:5432/postgres uv run pytest -v
```

S3 is mocked with moto. For a manual run against a fake S3, start `uv run moto_server -p 5055` and set `S3_ENDPOINT=http://localhost:5055`.

To build an image: `docker build --build-arg PG_MAJOR=16 -t pgbackup-r2:pg16 .`

The documentation site lives in [`docs/`](docs/): an Astro Starlight project with its own `package.json`. See [`docs/README.md`](docs/README.md) to run it locally.

## License

MIT. Copyright (c) 2026 Frank Owusu Boakye.
