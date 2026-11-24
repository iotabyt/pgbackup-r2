# Changelog

## Unreleased

## 0.4.0 (2026-11-24)

pgbackup-r2 is on PyPI now. Docker and `pgb` stay the main way to run it, and nothing about how it works has changed.

- **On PyPI:** `pip install pgbackup-r2`, or `uv tool install pgbackup-r2`, or try it with `uvx pgbackup-r2 --version`. You still need `pg_dump`, `pg_restore`, `psql` and `age` on your `PATH` for that route.
- **New guides:** [Neon](https://pgbackup-r2.iotabyt.com/guides/neon/), the setup pgbackup-r2 runs on in production (Postgres 18), and [Supabase](https://pgbackup-r2.iotabyt.com/guides/supabase/), which isn't tested yet and says so. Managed databases is now a short hub for both.
- **Docs, for people searching:**
  - Every page has a clearer title and description.
  - The homepage has its own title, a proper link preview and structured data.
  - There's a `robots.txt` that points at the sitemap.
  - Workers preview URLs are off, so only `pgbackup-r2.iotabyt.com` gets indexed.
- **Docs fixes:**
  - GitHub's 60-day pause for scheduled workflows only applies to public repos, and the docs now say so.
  - The Supabase connection advice is gentler: the direct host (IPv6, or IPv4 with the add-on) or the Session pooler, and the Transaction pooler isn't recommended.
- **Packaging:**
  - Full PyPI metadata: keywords, classifiers and project links.
  - The README uses absolute links, so it renders on PyPI.
  - The Docker images carry OCI labels for title, description, url and documentation.

### Upgrading from 0.3.2

Nothing to change. Re-run the install line, then `pgb update` in each backup folder.

## 0.3.2 (2026-11-19)

- **`pgb` works with an older image.** Before using a feature the image may not have, `pgb` asks the image for its version (once per run, remembered by image ID) and compares it with the version that feature needs.
  - **`pgb drill` with an image older than 0.3.1** skips the preflight with a note ("image 0.3.0 is older than pgb 0.3.2; run `pgb update`") and drills the old way, instead of failing with "unrecognized arguments".
  - **Images older than 0.2.0 have no `doctor` or `detect`.** `pgb doctor` and `pgb init --detect` say so and point to `pgb update`.
- **`pgb doctor` compares versions:** ✓ when `pgb` and the image match, ⚠ with "run `pgb update`" when the image is older, and ⚠ with the install line when `pgb` is.

### Upgrading from 0.3.1

Re-run the install line, then `pgb update` in each backup folder.

## 0.3.1 (2026-11-19)

Polish found while backing up Labpadi for real.

- **`pgb drill` checks before starting Postgres.** It first runs the image's new `drill --preflight`: a private key is set, the bucket answers, the backup exists. A missing key or an empty bucket now fails in a second, with the same message as before and no container or network started.
- **Shorter database URLs in output.**
  - A database shows as `host/dbname`, with `:port` if it isn't 5432: `ep-withered-cell-batk9adw.c-8.eu-central-1.aws.neon.tech/labpadi`.
  - The user, password and query string (`sslmode`, `channel_binding`) never appear, in `restore`, `drill`, `doctor`, `backup` or the nightly log.
  - `restore` names its target once, in the final `PASS` line.
- **`pgb nightly start` and `AGE_IDENTITY`:** the note now reads "note: .env has AGE_IDENTITY. That's fine on your own machine; on a server, leave it out (backups only need AGE_RECIPIENTS)."
- **Unknown commands stop before any container starts**, with the closest match: `pgb: unknown command "uninstal". Did you mean "uninstall"?` The exit code is 2.

### Upgrading from 0.3.0

Re-run the install line, then `pgb update` in each backup folder.

## 0.3.0 (2026-11-19)

- Short install URL: `curl -fsSL https://pgbackup-r2.iotabyt.com/install.sh | sh`. It redirects (302) to the installer on GitHub. The README and docs use it everywhere, and the Install page keeps the GitHub URLs for networks that block redirects.
- PowerShell has an installer script too: `irm https://pgbackup-r2.iotabyt.com/install.ps1 | iex`. It writes the `pgb` function into `$PROFILE` between marker comments, and running it again leaves `$PROFILE` alone.
- The installers fetch `pgb` from the latest release (via GitHub's API, falling back to the highest `vX.Y.Z` tag), never from `main`. `PGB_VERSION=v0.2.0` installs a specific release. If the API can't be reached, the installer stops with a clear message instead of falling back to `main`.
- `pgb uninstall` removes pgb, and in PowerShell its `~\.pgb` folder and only the installer's lines in `$PROFILE`.
  - **`--all`:** also removes the `pgb nightly` containers and the `ghcr.io/iotabyt/pgbackup-r2` images, but only those carrying pgbackup-r2's image label.
  - **Asks first:** it lists what it will remove before asking. `--yes` skips the question, and without a terminal and `--yes` it refuses.
  - **Never touched:** backups, `.env` files, `out/` folders or keys.
  - **Twice is safe:** a second run says `pgb is not installed`.

### Upgrading from 0.2.x

Re-run the install line to get `pgb uninstall`, then run `pgb update` in each backup folder.

- **bash, zsh, fish:** `curl -fsSL https://pgbackup-r2.iotabyt.com/install.sh | sh`
- **PowerShell:** `irm https://pgbackup-r2.iotabyt.com/install.ps1 | iex`. Your existing `function pgb` line in `$PROFILE` is kept, and `pgb uninstall` recognises it.

## 0.2.0 (2026-11-18)

`pgb` is now the main way to use pgbackup-r2. It's still a thin wrapper around Docker, with no Python on the host, and the same in bash (`scripts/pgb`) and PowerShell (`scripts/pgb.ps1`).

- `pgb init` writes a commented `.env` (never overwriting one), makes it readable only by you on Linux and macOS, creates `out/`, and prints the next steps.
- `pgb doctor`, and `doctor` in the image, check:
  - that Docker is running, and that `.env` exists with every setting a backup needs
  - that the database is reachable and its major version matches `PG_MAJOR`
  - that the bucket is writable (a test file is put, then deleted)
  - that `AGE_IDENTITY` opens what's encrypted to `AGE_RECIPIENTS`

  Each check prints ✓ or ✗ with a fix, and `doctor` exits 1 if anything fails.
- `pgb drill` runs a full drill with no compose file:
  1. It starts a throwaway `postgres:<PG_MAJOR>` on a private network, with a random password and in-memory storage.
  2. It waits for the database to be ready (60s at most), then runs the drill.
  3. It always removes the database and the network afterwards, after a failure or Ctrl+C too.

  The exit code is the drill's.
- `pgb nightly start|stop|status|logs` runs scheduled backups in a background container named `pgbackup-<S3_PREFIX>`, with `--restart unless-stopped`.
- `pgb update` pulls the image and shows the old and new versions. `pgb version` and `pgb help` are new too.
- Database networking:
  - A `localhost` database URL works. `pgb` uses `--network host` on Linux, or `host.docker.internal` on Docker Desktop.
  - `PGB_NETWORK` joins an existing Docker network, such as a Compose project's `myapp_default`.
  - When a host name can't be resolved, the error explains why and gives both fixes.
- `scripts/install.sh`: a one-line installer. It detects your shell and prints the `PATH` line for it, without editing any files. `pgb` runs on bash 3.2 (macOS), and CI checks this.
- `keygen`: both key panels have the same width. In a terminal too narrow for them, the keys print as whole lines instead of being cut short.
- README:
  - Quick start uses `pgb` throughout.
  - New sections:
    - "Without pgb", with the plain Docker commands
    - "Run it from GitHub Actions", with `examples/nightly-backup.yml`
    - Two walkthroughs: a VPS with Compose, and Neon or Supabase with GitHub Actions
    - Troubleshooting
  - Step-by-step R2 setup, and notes for AWS S3, Backblaze B2 and MinIO.
- Retention can now live in the bucket. Set `TIERS="daily=daily/ monthly=monthly/"` to use your own tier folders under the prefix.
  - **Tiers and copies:** the tiers are `hourly`, `daily`, `weekly`, `monthly` and `yearly`, in folders with any names. The most frequent tier gets every upload. Each other tier gets a server-side copy (metadata kept, nothing re-uploaded) of the first backup of its period.
  - **Coverage is read from the bucket,** never from local state.
  - **`PRUNE` defaults to false with TIERS,** so the bucket's lifecycle rules do the deleting. With `PRUNE=true`, only the most frequent tier is pruned.
  - **Bad TIERS values** are a config error, with an example.
  - **Without TIERS,** retention is tool-managed, as before.
- `pgb init --detect`, `detect` and `doctor` look at the bucket. They list the folders under the prefix and read the lifecycle rules (skipped quietly if not allowed), then suggest a `TIERS` line. `init --detect` writes it into `.env` only if you confirm (or pass `--yes`).
- Reading across tiers:
  - `list` shows a tier column.
  - `latest` is the newest backup in any tier.
  - `verify`, `drill`, `decrypt` and `restore` accept `<tier>/<stamp>`.
- Files that aren't pgbackup-r2 backups are never touched, and `list` ignores them. `doctor` counts them.
- A delete refused by a lock or retention rule is a warning ("locked, skipped"), not a failure.
- Docs: a "Use your existing folders" section, with example R2 lifecycle and bucket lock rules. The R2 EU-jurisdiction endpoint (`https://<account-id>.eu.r2.cloudflarestorage.com`) is in the README, `.env.example` and the `pgb init` template.
- Drill tracking:
  - Every drill, pass or fail, writes `<prefix>/.drills/<time>.json`: the backup tested, PASS or FAIL, tables, rows, duration and tool version. It holds counts only, so it isn't encrypted. `list`, `prune` and detection skip it.
  - `list` ends with `Last drill: PASS, 12 days ago`.
  - `doctor` shows ✓ for a successful drill within 35 days. It shows ⚠ when the last one is older, when none has passed, or when the most recent drill failed.
- Documentation moved to its own site, [pgbackup-r2.iotabyt.com](https://pgbackup-r2.iotabyt.com): guides, concepts, a full reference and troubleshooting. It's built from `docs/` with Astro Starlight. The README is now short and links into it. pgbackup-r2 also has its own logo (`.github/assets/`).
- `examples/drill/compose.yml` stays, for people who prefer Compose.
- Polish:
  - **`pgb init`'s `.env`:** the 7 settings a backup needs come first, and everything else is commented out under "Optional", at its default.
  - **Typos:** `doctor` warns about settings it doesn't know, with a "did you mean" (`S3_BUKET → S3_BUCKET?`).
  - **Help:** every command's `--help` shows a one-line summary, the usage and examples. `pgb help <command>` does the same.
  - **Errors:** every error ends with the next thing to run or the setting to fix.
  - **One set of defaults** in the code, and a test that the template, the docs and `--help` agree with it.
  - **Docs:** a "Why pgbackup-r2" page.
  - **Repo:** issue templates, `SECURITY.md` and `CONTRIBUTING.md`.

### Upgrading from 0.1.x

- **`pgb`:** run the install line again to get the new commands (`init`, `doctor`, `drill`, `nightly`, `update`, `help`).
- **Image:** `pgb update` pulls 0.2.0. The tags (`pg16`, `pg17`, `pg18`) are unchanged.
- **`.env`:** nothing to change. No setting was renamed or removed, and defaults are unchanged. Without `TIERS`, pruning works as before. The new settings (`TIERS`, `PRUNE`, `PGB_NETWORK`) are all optional.
- **Drill records:** drills now write a small record to `<prefix>/.drills/`, which needs write access. With a read-only token, drills still run, with a warning, but `list` and `doctor` won't know about them.
- **Scripts that read `list`:** its output now ends with a `Last drill: …` line. Skip lines that don't start with a stamp.
- **`examples/drill`:** nothing to do. `pgb drill` does the same job without the compose file.
- **`PGB_DOCKER_ARGS` is gone.** `pgb` now handles a database on `localhost` by itself. For a Compose network, use `PGB_NETWORK`.
- **`PGBACKUP_IMAGE` in `examples/drill/compose.yml`** is now `PGB_IMAGE`, the same name `pgb` uses. It's for testing local builds only.
- Tests: both `pgb` scripts are tested against a fake `docker`, and the drill cleanup against real Docker. The scripts are also run under bash 3.2 and checked with shellcheck.

## 0.1.1 (unreleased)

- `restore`: the target can come from `RESTORE_DATABASE_URL`, so the password stays out of the process list. `--target` still works and overrides it. With neither set, `restore` fails with a clear message.
- `examples/drill`: `PG_MAJOR` in `.env` picks both images (`postgres:${PG_MAJOR}` and `pgbackup-r2:pg${PG_MAJOR}`, default 16). The README explains why it must match the server's major version.
- `examples/drill`: the same folder and `.env` can now take backups too (`docker compose run --rm drill backup`). `.env.example` has `DATABASE_URL`, which must be a direct, non-pooled connection, and `AGE_RECIPIENTS`.
- `pgb`: a short wrapper around `docker run` (`scripts/pgb` for bash, `scripts/pgb.ps1` for PowerShell). It uses `./.env`, takes the image tag from `PG_MAJOR`, mounts `./out` at `/out`, adds `-it` at a terminal, and runs as you on Linux. `PGB_DOCKER_ARGS` passes extra Docker options. The README now uses `pgb` in its examples, with one-line installs.
- CI: `actions/checkout@v5` and `astral-sh/setup-uv@v7`, with monthly Dependabot updates for GitHub Actions and uv.

### Upgrading from 0.1.0

Nothing to do. `restore --target` still works. To keep the password out of `ps`, move the URL into `RESTORE_DATABASE_URL` in `.env`. In `examples/drill`, `PG_MAJOR` defaults to 16; set it if your server is newer.

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
