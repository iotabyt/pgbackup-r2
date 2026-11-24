---
title: "Postgres backups with Kamal 2"
sidebar:
  label: "Kamal 2"
description: "Run nightly encrypted Postgres backups to Cloudflare R2 as a Kamal 2 accessory next to your database, with pg_dump, age and a heartbeat."
next:
  link: /guides/monthly-drills/
  label: Monthly drills
---

If you deploy with [Kamal 2](https://kamal-deploy.org), run the backup as an accessory next to your database. In `config/deploy.yml`:

```yaml
accessories:
  backup:
    image: ghcr.io/iotabyt/pgbackup-r2:pg16      # match your Postgres major version
    host: 203.0.113.10
    env:
      clear:
        S3_ENDPOINT: https://<account-id>.r2.cloudflarestorage.com
        S3_BUCKET: acme-db-backups
        S3_PREFIX: shop
        AGE_RECIPIENTS: age1...                  # the PUBLIC key only
        BACKUP_AT: "02:00"
      secret:
        - DATABASE_URL          # e.g. postgresql://app:pw@shop-db:5432/app
        - AWS_ACCESS_KEY_ID
        - AWS_SECRET_ACCESS_KEY
        - HEARTBEAT_URL
        - HEARTBEAT_FAIL_URL
```

Put the secrets in `.kamal/secrets`, then:

```bash
kamal accessory boot backup
kamal accessory exec backup --reuse "pgbackup-r2 backup"     # a first backup now
kamal accessory logs backup
```

The accessory runs `schedule`, the image's default command. It backs up at `BACKUP_AT` (UTC), prunes, and pings the heartbeat. `DATABASE_URL` uses the database accessory's container name as the host, which Kamal puts on the same Docker network.

Check the setup from inside the accessory:

```bash
kamal accessory exec backup --reuse "pgbackup-r2 doctor"
```

The private key doesn't belong on the server, so `doctor` shows "-" for it. Run drills from your laptop with `pgb drill`, in a folder whose `.env` has the private key: see [Monthly drills](/guides/monthly-drills/).

## Next

**[Monthly drills](/guides/monthly-drills/)**: prove a backup restores, every month.
