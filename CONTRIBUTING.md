# Contributing

Thanks for helping. A few things first:

- **Open an issue before a big pull request.** pgbackup-r2 is deliberately small (see [Why pgbackup-r2](https://pgbackup-r2.iotabyt.com/start/why/)), so a quick chat first saves you building something that won't be merged. Typos, small fixes and docs improvements are welcome without asking.
- **Questions** go in [Discussions](https://github.com/iotabyt/pgbackup-r2/discussions), and **security problems** go through [private reporting](SECURITY.md).

## The tool (Python)

```bash
uv sync
uv run ruff check && uv run ruff format --check
uv run shellcheck scripts/pgb scripts/install.sh
uv run pytest
```

Most tests need nothing else. Some need more, and are skipped if it's missing:

- **End-to-end:** a real Postgres, plus `age` and Postgres client tools of the same major version on `PATH`. In WSL or Ubuntu:

  ```bash
  sudo apt install age postgresql-client-16
  docker run -d --name pgbackup-test -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16
  PATH=/usr/lib/postgresql/16/bin:$PATH \
  PGBACKUP_TEST_URL=postgresql://postgres:pw@localhost:5432/postgres uv run pytest
  ```

- **The `pgb` scripts:** Docker for the drill-cleanup and bash 3.2 tests, and `pwsh` for the PowerShell half.

S3 is mocked with moto, so no bucket is needed.

### Trying a local build

Build an image, then point `pgb` (or the drill kit's `compose.yml`) at it with `PGB_IMAGE`:

```bash
docker build --build-arg PG_MAJOR=16 -t pgbackup-r2:pg16 .
PGB_IMAGE=pgbackup-r2:pg16 pgb doctor
cd examples/drill && PGB_IMAGE=pgbackup-r2:pg16 docker compose run --rm drill
```

`PGB_IMAGE` is a developer setting: it isn't in the user docs, and it overrides the image `PG_MAJOR` would pick.

## The docs

```bash
cd docs
npm ci
npm run dev        # http://localhost:4321
```

[`docs/README.md`](docs/README.md) has the rest, including the checks to run before a pull request.

## Pull requests

- **Keep changes small and focused,** with a test for anything that changes behaviour.
- **Update the docs** in `docs/src/content/docs/` when a command, setting or default changes.
- **Update the changelog.** Add a line to `CHANGELOG.md` under the next version, with an "Upgrading" note if users have to do anything.
