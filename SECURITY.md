# Security

## Reporting a vulnerability

Please report security problems privately, through GitHub's **[private vulnerability reporting](https://github.com/iotabyt/pgbackup-r2/security/advisories/new)** (the repo's **Security** tab → **Report a vulnerability**). Don't open a public issue.

We'll reply within a few working days, keep you posted while we fix it, and credit you in the release notes if you'd like.

## In scope

- **Anything that could expose data:**
  - the encryption being skipped or weakened
  - a plaintext dump reaching disk, the bucket or a log
  - a private key or password showing up in output, a process list or a container's command line
- **Anything that could destroy backups it shouldn't:** pruning outside the rules, or touching files it didn't make.
- **Restores going where they shouldn't**, such as over the live database without the confirmation.
- **The `pgb` scripts and the installer** (`scripts/`), and the Docker images on `ghcr.io/iotabyt/pgbackup-r2`.

## Out of scope

- **Vulnerabilities in dependencies.** Report those upstream: Postgres, age, boto3, the base image. Do tell us if pgbackup-r2 uses one in a way that makes it worse.
- **Problems that need someone who already has your `.env`,** your private key or full control of your machine.
- **Your storage provider's own security.**

## Your backups are only as safe as your private key

Backups are encrypted with [age](https://age-encryption.org). Anyone with the private key (`AGE-SECRET-KEY-1…`) can read every backup encrypted to it. Without it, no one can, including you.

- **Keep the private key off the server** that takes the backups. The server only needs the public key.
- **Keep a copy in a password manager,** and consider a second key stored offline: see [Keys and security](https://pgbackup-r2.iotabyt.com/concepts/keys/).
- **If a private key leaks,** make a new pair and switch `AGE_RECIPIENTS` to it. Backups already taken stay readable with the leaked key, so delete them once newer ones exist.
