---
title: "Encryption keys and security (age)"
sidebar:
  label: "Keys and security"
description: "How pgbackup-r2 uses age keys: the public key on the server, the private key off it, key files for CI and Kamal, rotating keys, and losing one."
next:
  link: /guides/monthly-drills/
  label: Monthly drills
---

Backups are encrypted with [age](https://age-encryption.org), which uses a key pair:

- The **public key** (`age1…`) can only encrypt. It goes in `AGE_RECIPIENTS`.
- The **private key** (`AGE-SECRET-KEY-1…`) decrypts. It goes in `AGE_IDENTITY`, or in a file named by `AGE_IDENTITY_FILE`.

`pgb keygen` makes a pair. It prints both keys and writes nothing to disk.

## Where each key lives

**The server only ever holds the public key.** That includes the nightly container, a Compose service, a Kamal accessory and a GitHub Actions backup workflow. If someone steals the server, or the bucket leaks, all they get is encrypted blobs. They can't even read the manifests.

**The private key lives in two places:**

- in your password manager
- on whatever machine runs drills and restores: your laptop, or a GitHub Actions secret used only by a drill workflow

`pgb nightly start` warns if the `.env` it's given has the private key in it.

`backup` refuses to run without a public key, so an unencrypted backup isn't possible. It also refuses an `AGE_RECIPIENTS` that contains a private key, because that key doesn't belong on the server.

## Key in a file or in the environment

There are two ways to give pgbackup-r2 the private key:

- **`AGE_IDENTITY`** holds the key itself. It's the simplest choice on your own laptop, in the `.env` that only you can read.
- **`AGE_IDENTITY_FILE`** holds the path to a file containing the key. Prefer it anywhere secrets are mounted as files, or where other people or processes share the machine:
  - **Shared machines:** an environment variable is visible to anyone who can inspect the process or the container (`docker inspect` shows a container's environment). A file with mode 600 is only readable by its owner.
  - **CI:** write the secret to a file in the job's temporary folder, point `AGE_IDENTITY_FILE` at it, and it won't be printed if a step dumps the environment.
  - **Kamal and Docker secrets:** both can put the key in a file inside the container. Use a Kamal accessory's `files:` entry, or a Compose `secrets:` entry, which appears as `/run/secrets/<name>`. Set `AGE_IDENTITY_FILE` to that path, and the key never sits in the container's environment.
  - **With `pgb`:** `./out` is the folder the container can see. Put the key there with mode 600, and set `AGE_IDENTITY_FILE=/out/age-identity.txt`.

Set one or the other, never both: pgbackup-r2 refuses to guess which one you meant.

## Inside the container

With `AGE_IDENTITY_FILE`, pgbackup-r2 reads the key from that file and never copies it. `AGE_IDENTITY` arrives as an environment variable instead, so for each decrypt, pgbackup-r2 writes it to a file that only the container's user can read (mode 0600), in a fresh private folder (0700). It deletes the file straight afterwards, even on failure.

## Lose the private key, lose the backups

There's no recovery, no back door and no support ticket. Without the private key, a backup is noise.

**Use a second recipient.**

1. Run `pgb keygen` again for a second pair.
2. Store that private key offline: printed on paper, or on a USB stick in a safe.
3. List both public keys, separated by a comma:

   ```bash
   AGE_RECIPIENTS=age1first...,age1second...
   ```

Every backup is now encrypted to both keys, and either one opens it. Lose your everyday key, and the one in the safe still works.

## Check the keys match

If the private key in `AGE_IDENTITY` doesn't belong to any of the public keys in `AGE_RECIPIENTS`, nothing you back up can be restored with it. `pgb doctor` checks this. It encrypts a test string to `AGE_RECIPIENTS` and decrypts it with `AGE_IDENTITY`. If that fails, it tells you which public key belongs to your private key.

## Rotating keys

Old backups stay encrypted to the old key. To rotate:

1. Make a new pair with `pgb keygen`.
2. Put the new public key in `AGE_RECIPIENTS` on the server.
3. Keep the **old** private key until the last backup encrypted to it has expired.
4. Run a drill to check new backups open with the new private key.

## Tokens

The R2/S3 token can read, and usually delete, your backups, but it can't decrypt them. Limit each token to one bucket. A [bucket lock](/guides/existing-folders/#example-lifecycle-and-lock-rules-on-r2) stops a leaked token from deleting recent backups.

## Next

**[Monthly drills](/guides/monthly-drills/)**: prove a backup restores, every month.
