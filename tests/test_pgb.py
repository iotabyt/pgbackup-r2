"""The `pgb` bash wrapper, with a fake `docker` that prints the arguments it was given."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PGB = Path(__file__).resolve().parent.parent / "scripts" / "pgb"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run_pgb(cwd: Path, *args: str, env: dict[str, str] | None = None) -> list[str]:
    fake_bin = cwd / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    docker = fake_bin / "docker"
    docker.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    docker.chmod(0o755)
    full_env = {
        k: v for k, v in os.environ.items() if k not in ("PG_MAJOR", "PGB_IMAGE", "PGB_DOCKER_ARGS")
    }
    full_env["PATH"] = f"{fake_bin}{os.pathsep}{full_env['PATH']}"
    full_env.update(env or {})
    out = subprocess.run(
        ["bash", str(PGB), *args],
        cwd=cwd,
        env=full_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.splitlines()


def test_without_env_file_uses_pg16_and_no_env(tmp_path):
    argv = run_pgb(tmp_path, "keygen")
    assert argv[:2] == ["run", "--rm"]
    assert "--env-file" not in argv
    assert "-it" not in argv  # not a terminal
    assert argv[-2:] == ["ghcr.io/iotabyt/pgbackup-r2:pg16", "keygen"]
    assert not (tmp_path / "out").exists()  # no stray folders for commands that don't need one


def test_env_file_pg_major_out_mount_and_user(tmp_path):
    (tmp_path / ".env").write_text("S3_BUCKET=b\r\nPG_MAJOR=18\r\nAGE_IDENTITY=x\r\n")  # CRLF
    argv = run_pgb(tmp_path, "decrypt", "latest", "--out", "/out/backup.dump")
    assert (tmp_path / "out").is_dir()  # created before docker would make it root-owned
    i = argv.index("--env-file")
    assert argv[i + 1] == ".env"
    i = argv.index("-v")
    assert argv[i + 1] == f"{tmp_path}/out:/out"
    if os.uname().sysname == "Linux":
        i = argv.index("--user")
        assert argv[i + 1] == f"{os.getuid()}:{os.getgid()}"
    image = argv.index("ghcr.io/iotabyt/pgbackup-r2:pg18")
    assert argv[image + 1 :] == ["decrypt", "latest", "--out", "/out/backup.dump"]


def test_overrides(tmp_path):
    (tmp_path / ".env").write_text("PG_MAJOR=17\n")
    argv = run_pgb(
        tmp_path,
        "list",
        env={"PG_MAJOR": "18", "PGB_DOCKER_ARGS": "--network host", "PGB_IMAGE": "local:dev"},
    )
    assert "--network" in argv and "host" in argv
    assert argv[-2:] == ["local:dev", "list"]
    argv = run_pgb(tmp_path, "list", env={"PG_MAJOR": "18"})
    assert argv[-2] == "ghcr.io/iotabyt/pgbackup-r2:pg18"
