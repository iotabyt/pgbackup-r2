# syntax=docker/dockerfile:1

# --- build: make the wheel with uv -------------------------------------------------------
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md PYPI.md LICENSE ./
COPY src ./src
RUN uv build --wheel --out-dir /dist

# --- runtime: Python + age + the matching Postgres client ---------------------------------
FROM python:3.12-slim-bookworm

# The client must match your server's major version: build pg16, pg17, pg18 and pg19 images.
ARG PG_MAJOR=16

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PGBACKUP_R2_IMAGE=pg${PG_MAJOR} \
    PATH=/usr/lib/postgresql/${PG_MAJOR}/bin:$PATH

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg age; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}"; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*; \
    pg_dump --version; age --version

COPY --from=build /dist/*.whl /tmp/
RUN pip install /tmp/*.whl && rm /tmp/*.whl \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin pgbackup

USER 10001
WORKDIR /home/pgbackup

# image.source must stay exactly this: `pgb uninstall --all` finds our containers and images by it.
LABEL org.opencontainers.image.title="pgbackup-r2" \
      org.opencontainers.image.description="Encrypted Postgres backups to Cloudflare R2 or any S3, with restore drills that prove they work" \
      org.opencontainers.image.url="https://pgbackup-r2.iotabyt.com" \
      org.opencontainers.image.documentation="https://pgbackup-r2.iotabyt.com" \
      org.opencontainers.image.source="https://github.com/iotabyt/pgbackup-r2" \
      org.opencontainers.image.licenses="MIT"

ENTRYPOINT ["pgbackup-r2"]
CMD ["schedule"]
