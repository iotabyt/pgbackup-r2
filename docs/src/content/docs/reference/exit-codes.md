---
title: Exit codes
description: What each exit code from pgbackup-r2 and pgb means.
---

Scripts, cron and CI can rely on these.

## pgbackup-r2 (the image)

| Code | Meaning |
|---|---|
| `0` | Success. For `drill`, the drill passed. For `doctor`, nothing failed (⚠ and `-` lines don't count). |
| `1` | Something failed. That includes: a drill that ran and found problems (FAIL), any ✗ from `doctor`, a configuration error, an S3 or Postgres error, and an unexpected crash. The error is printed with a hint, and `backup` and `drill` also POST it to `HEARTBEAT_FAIL_URL`. |
| `2` | Bad command-line usage: an unknown command or flag. |
| `130` | Interrupted (Ctrl+C, or SIGTERM when it's the container's main process). `schedule` exits `0` instead, because stopping is how it's meant to end. |

## pgb

`pgb` passes the image's exit code through, plus:

| Code | Meaning |
|---|---|
| `1` | `pgb` itself stopped before running anything. For example: no `.env` where one is needed, a bad `PG_MAJOR`, an unknown `pgb init` option, or `doctor` finding Docker not running. `pgb nightly status` also exits 1 when the container exists but isn't running. |
| `3` | `pgb nightly status`: there's no nightly container. |
| `130` | `pgb drill` was interrupted with Ctrl+C. The throwaway database and network are still removed. |
| `143` | `pgb drill` was stopped with SIGTERM. The throwaway database and network are still removed. |
| `125`–`127` | Docker's own errors: `125` means the daemon or `docker run` failed (for example, a `PGB_NETWORK` that doesn't exist), and `126` and `127` mean the command couldn't run in the container. |

In PowerShell, `pgb drill` interrupted with Ctrl+C still cleans up, but the exit code isn't set.
