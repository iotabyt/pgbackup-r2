"""The `pgb` wrappers: scripts/pgb (bash) and scripts/pgb.ps1 (PowerShell).

Most tests run both scripts against a fake `docker` that records its arguments, so they check
that the two behave the same. The drill tests use real Docker and a stub image, to prove the
scratch database and network are always removed.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from pgbackup_r2 import __version__

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
PGB = SCRIPTS / "pgb"
PGB_PS1 = SCRIPTS / "pgb.ps1"
INSTALL = SCRIPTS / "install.sh"
LINUX = os.uname().sysname == "Linux"

SHELLS = [
    pytest.param(
        ["bash", str(PGB)],
        id="bash",
        marks=pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
    ),
    pytest.param(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(PGB_PS1)],
        id="pwsh",
        marks=pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs pwsh"),
    ),
]

# Records each call as its arguments, one per line, then the DATABASE_URL it was given and
# "--end--". `inspect` fails, as it does for a container that doesn't exist. `info` prints
# FAKE_DOCKER_OS and exits with FAKE_DOCKER_INFO_RC.
FAKE_DOCKER = """#!/bin/sh
{ for a in "$@"; do printf '%s\\n' "$a"; done; echo "KEYS:${PGB_ENV_KEYS-}"
  echo "ENV:${DATABASE_URL-}"; echo --end--; } \\
  >> "$FAKE_DOCKER_LOG"
case "$1" in
  inspect) exit 1 ;;
  info) echo "${FAKE_DOCKER_OS:-Ubuntu 24.04}"; exit "${FAKE_DOCKER_INFO_RC:-0}" ;;
esac
case "$1 $2" in "image inspect") exit 1 ;; esac
for last in "$@"; do :; done
if [ "$last" = "--env-lines" ]; then  # the image's `detect --env-lines`
  echo "Found folders daily/ (lifecycle: delete after 30 days) and monthly/." >&2
  printf '%s\\n' "${FAKE_SUGGEST-TIERS=\\"daily=daily/ monthly=monthly/\\"
PRUNE=false}"
fi
"""


def clean_env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "PG_MAJOR",
            "PGB_IMAGE",
            "PGB_NETWORK",
            "NO_COLOR",
            "DATABASE_URL",
        )
    }
    env.update(extra)
    return env


def run_fake(shell: list[str], cwd: Path, *args: str, env: dict[str, str] | None = None):
    """Run pgb with the fake docker. Returns (result, [argv of each docker call])."""
    fake_bin = cwd / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    docker = fake_bin / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = cwd / "docker.log"
    log.unlink(missing_ok=True)
    full_env = clean_env(**(env or {}))
    full_env["PATH"] = f"{fake_bin}{os.pathsep}{full_env['PATH']}"
    full_env["FAKE_DOCKER_LOG"] = str(log)
    result = subprocess.run(
        [*shell, *args],
        cwd=cwd,
        env=full_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    calls: list[list[str]] = []
    if log.exists():
        current: list[str] = []
        for line in log.read_text().splitlines():
            if line == "--end--":
                calls.append(current)
                current = []
            else:
                current.append(line)
    return result, calls


def strip_env(argv: list[str]) -> list[str]:
    return [a for a in argv if not a.startswith(("ENV:", "KEYS:"))]


def run_calls(calls: list[list[str]]) -> list[list[str]]:
    """The `docker run` calls, without the recorded DATABASE_URL."""
    return [strip_env(c) for c in calls if c[:1] == ["run"]]


def run_env(calls: list[list[str]]) -> list[str]:
    """The DATABASE_URL each `docker run` call saw in its environment."""
    return [c[-1].removeprefix("ENV:") for c in calls if c[:1] == ["run"]]


def run_keys(calls: list[list[str]]) -> list[str]:
    """The PGB_ENV_KEYS each `docker run` call saw."""
    return [c[-2].removeprefix("KEYS:") for c in calls if c[:1] == ["run"]]


# --- passthrough ---------------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_passthrough_without_env_file(shell, tmp_path):
    result, calls = run_fake(shell, tmp_path, "keygen", "--plain")
    assert result.returncode == 0, result.stderr
    [argv] = run_calls(calls)
    assert argv[:2] == ["run", "--rm"]
    assert "--env-file" not in argv
    assert "-it" not in argv  # not a terminal
    assert argv[-3:] == ["ghcr.io/iotabyt/pgbackup-r2:pg16", "keygen", "--plain"]
    assert not (tmp_path / "out").exists()  # no stray folders for commands that don't need one


@pytest.mark.parametrize("shell", SHELLS)
def test_passthrough_env_file_pg_major_out_mount_and_user(shell, tmp_path):
    (tmp_path / ".env").write_text("S3_BUCKET=b\r\nPG_MAJOR=18\r\nAGE_IDENTITY=x\r\n")  # CRLF
    result, calls = run_fake(shell, tmp_path, "decrypt", "latest", "--out", "/out/backup.dump")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out").is_dir()  # created before docker would make it root-owned
    [argv] = run_calls(calls)
    assert argv[argv.index("--env-file") + 1] == ".env"
    assert argv.count("-v") == 1
    assert argv[argv.index("-v") + 1] == f"{tmp_path}/out:/out"
    if LINUX:
        assert argv[argv.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    image = argv.index("ghcr.io/iotabyt/pgbackup-r2:pg18")
    assert argv[image + 1 :] == ["decrypt", "latest", "--out", "/out/backup.dump"]


@pytest.mark.parametrize("shell", SHELLS)
def test_overrides(shell, tmp_path):
    (tmp_path / ".env").write_text("PG_MAJOR=17\n")
    env = {"PG_MAJOR": "18", "PGB_IMAGE": "local:dev"}
    _, calls = run_fake(shell, tmp_path, "list", env=env)
    [argv] = run_calls(calls)
    assert argv[-2:] == ["local:dev", "list"]
    # PGB_DOCKER_ARGS is gone: setting it changes nothing.
    _, calls = run_fake(shell, tmp_path, "list", env={"PGB_DOCKER_ARGS": "--network host"})
    assert "--network" not in run_calls(calls)[0]
    _, calls = run_fake(shell, tmp_path, "list", env={"PG_MAJOR": "18"})
    assert run_calls(calls)[0][-2] == "ghcr.io/iotabyt/pgbackup-r2:pg18"


@pytest.mark.parametrize("shell", SHELLS)
def test_bad_pg_major(shell, tmp_path):
    (tmp_path / ".env").write_text("PG_MAJOR=latest\n")
    result, calls = run_fake(shell, tmp_path, "list")
    assert result.returncode == 1
    assert "PG_MAJOR must be a number" in result.stderr
    assert calls == []


# --- help, version, init -------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("args", [[], ["help"], ["--help"]])
def test_help(shell, tmp_path, args):
    result, calls = run_fake(shell, tmp_path, *args)
    assert result.returncode == 0, result.stderr
    for command in ("init", "drill", "nightly start|stop|status|logs", "update", "version"):
        assert f"pgb {command}" in result.stdout
    assert run_calls(calls) == []  # help never starts a container


@pytest.mark.parametrize("shell", SHELLS)
def test_version_without_image(shell, tmp_path):
    result, _ = run_fake(shell, tmp_path, "version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"pgb {__version__}",
        "image ghcr.io/iotabyt/pgbackup-r2:pg16: not pulled yet (pgb update)",
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_init_writes_env_once(shell, tmp_path):
    result, calls = run_fake(shell, tmp_path, "init")
    assert result.returncode == 0, result.stderr
    assert calls == []
    env_file = tmp_path / ".env"
    text = env_file.read_bytes().decode()
    assert "\r" not in text and not text.startswith("﻿")  # docker --env-file keeps those
    # The settings a backup needs are filled in at the top; everything else is commented out.
    required = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert [ln.split("=")[0] for ln in required] == [
        "PG_MAJOR",
        "DATABASE_URL",
        "S3_ENDPOINT",
        "S3_BUCKET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AGE_RECIPIENTS",
    ]
    assert "\n# Optional." in text
    for optional in ("# AGE_IDENTITY=", "# RETENTION_DAYS=30", "# TIERS=", "# PGB_NETWORK="):
        assert f"\n{optional}\n" in text
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "out").is_dir()
    steps = result.stdout
    assert [w for w in ("keygen", ".env", "backup", "verify", "drill") if w in steps] == [
        "keygen",
        ".env",
        "backup",
        "verify",
        "drill",
    ]
    assert steps.index("pgb keygen") < steps.index("pgb backup") < steps.index("pgb drill")

    env_file.write_text("PG_MAJOR=17\n")
    again, _ = run_fake(shell, tmp_path, "init")
    assert again.returncode == 1
    assert "already exists" in again.stderr
    assert env_file.read_text() == "PG_MAJOR=17\n"


def test_init_template_is_the_same_in_both_scripts():
    bash = re.search(r"cat >\.env <<'ENV'\n(.*?)\nENV\n", PGB.read_text(), re.S)
    ps1 = re.search(r"\$template = @'\n(.*?)\n'@\n", PGB_PS1.read_text(), re.S)
    assert bash and ps1
    assert bash.group(1) == ps1.group(1)


def test_script_versions_match_the_package():
    assert f"PGB_VERSION={__version__}\n" in PGB.read_text()
    assert f"$PgbVersion = '{__version__}'\n" in PGB_PS1.read_text()


# --- nightly -------------------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_nightly_start_names_container_after_prefix(shell, tmp_path):
    (tmp_path / ".env").write_text("PG_MAJOR=17\nS3_PREFIX=apps/My App\n")
    result, calls = run_fake(shell, tmp_path, "nightly", "start")
    assert result.returncode == 0, result.stderr
    [argv] = run_calls(calls)
    name = argv[argv.index("--name") + 1]
    assert name == "pgbackup-apps-My-App"
    assert argv[argv.index("--restart") + 1] == "unless-stopped"
    assert "-d" in argv and "-it" not in argv
    assert argv[argv.index("--env-file") + 1] == ".env"
    assert argv[-2:] == ["ghcr.io/iotabyt/pgbackup-r2:pg17", "schedule"]
    assert "Started pgbackup-apps-My-App" in result.stdout


@pytest.mark.parametrize("shell", SHELLS)
def test_nightly_status_and_stop_when_not_running(shell, tmp_path):
    (tmp_path / ".env").write_text("S3_PREFIX=shop\n")
    result, _ = run_fake(shell, tmp_path, "nightly", "status")
    assert result.returncode == 3
    assert "pgbackup-shop: not running" in result.stdout
    result, calls = run_fake(shell, tmp_path, "nightly", "stop")
    assert result.returncode == 0
    assert "isn't running" in result.stdout
    assert run_calls(calls) == []
    result, _ = run_fake(shell, tmp_path, "nightly", "bogus")
    assert result.returncode == 1
    assert "usage" in result.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_nightly_start_needs_env(shell, tmp_path):
    result, calls = run_fake(shell, tmp_path, "nightly", "start")
    assert result.returncode == 1
    assert "pgb init" in result.stderr
    assert run_calls(calls) == []


# --- static checks and old bash ------------------------------------------------------------


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")
def test_shellcheck():
    subprocess.run(["shellcheck", str(PGB), str(INSTALL)], check=True)


def docker_works() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(not docker_works(), reason="needs a running Docker")


@needs_docker
def test_runs_on_bash_32(tmp_path):
    """macOS ships bash 3.2: no empty-array expansions under set -u, no mapfile, and so on."""
    script = r"""
set -e
bash --version | head -n 1
mkdir -p /fake && printf '#!/bin/sh\necho docker "$@"\n' > /fake/docker && chmod +x /fake/docker
cd "$(mktemp -d)"
bash /s/pgb help | head -n 1
bash /s/pgb help drill | head -n 1
bash /s/pgb drill --help | head -n 1
bash /s/pgb version
PATH=/fake:$PATH bash /s/pgb keygen
bash /s/pgb init > /dev/null
PATH=/fake:$PATH bash /s/pgb decrypt latest --out /out/x
"""
    out = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{SCRIPTS}:/s:ro", "bash:3.2", "bash", "-c", script],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    lines = out.stdout.splitlines()
    assert lines[0].startswith("GNU bash, version 3.2")
    assert f"pgb {__version__}" in lines
    version = f"-e PGB_VERSION={__version__}"
    assert f"docker run --rm {version} --user 0:0 ghcr.io/iotabyt/pgbackup-r2:pg16 keygen" in lines
    assert lines.count("Restore a backup into a throwaway database and check every table.") == 2
    assert any(
        re.search(r"--env-file \.env -e PGB_VERSION=\S+ -v /tmp/[^ ]+/out:/out .* decrypt", x)
        for x in lines
    )


# --- drill, with real Docker ---------------------------------------------------------------

STUB = "pgb-test-drill-stub"
STUB_DOCKERFILE = """FROM postgres:16
# Stands in for pgbackup-r2: proves DRILL_DATABASE_URL reaches the scratch database, then
# sleeps for STUB_SLEEP seconds and exits with STUB_EXIT. Like the real image, it stops on
# SIGINT (sh only runs the trap during `wait`, not while a foreground command runs).
ENTRYPOINT ["sh", "-c", "psql \\"$DRILL_DATABASE_URL\\" -tAc 'select 6*7' || exit 1; trap 'exit 130' INT TERM; sleep ${STUB_SLEEP:-0} & wait $!; exit ${STUB_EXIT:-0}", "stub"]
"""


@pytest.fixture(scope="module")
def stub_image():
    subprocess.run(
        ["docker", "build", "-q", "-t", STUB, "-"],
        input=STUB_DOCKERFILE,
        text=True,
        check=True,
        capture_output=True,
    )
    yield STUB


def leftovers() -> list[str]:
    ps = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=pgb-drill-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    nets = subprocess.run(
        ["docker", "network", "ls", "--filter", "name=pgb-drill-", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return ps + nets


def drill_env(**extra: str) -> dict[str, str]:
    return clean_env(PG_MAJOR="16", PGB_IMAGE=STUB, **extra)


@needs_docker
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("code", [0, 3])
def test_drill_cleans_up_and_passes_exit_code(shell, tmp_path, stub_image, code):
    assert leftovers() == []
    (tmp_path / ".env").write_text(f"STUB_EXIT={code}\n")  # pgb passes .env to the drill
    result = subprocess.run(
        [*shell, "drill", "latest"],
        cwd=tmp_path,
        env=drill_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert "42" in result.stdout.split()  # the stub reached the scratch database
    assert leftovers() == []


@needs_docker
@pytest.mark.parametrize("shell", SHELLS)
def test_drill_cleans_up_after_ctrl_c(shell, tmp_path, stub_image):
    assert leftovers() == []
    (tmp_path / ".env").write_text("STUB_SLEEP=60\n")
    proc = subprocess.Popen(
        [*shell, "drill"],
        cwd=tmp_path,
        env=drill_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not any(n.endswith("-run") for n in leftovers()):
            assert time.monotonic() < deadline, "the drill container never started"
            assert proc.poll() is None, proc.stdout.read() if proc.stdout else ""
            time.sleep(0.5)
        time.sleep(1)
        os.killpg(proc.pid, signal.SIGINT)  # what Ctrl+C in a terminal does
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
    if shell[0] == "bash":
        assert proc.returncode == 130
    # PowerShell runs `finally` on Ctrl+C but nothing after it, so pwsh -File exits 0 here.
    # Forcing a code would need [Environment]::Exit, which closes an interactive window.
    assert leftovers() == []


# --- database networking -------------------------------------------------------------------

LOCAL_URL = "postgresql://app:s3cret@localhost:5432/app"


@pytest.mark.parametrize("shell", SHELLS)
def test_localhost_on_docker_desktop_becomes_host_docker_internal(shell, tmp_path):
    (tmp_path / ".env").write_text(f"DATABASE_URL={LOCAL_URL}\n")
    result, calls = run_fake(shell, tmp_path, "backup", env={"FAKE_DOCKER_OS": "Docker Desktop"})
    assert result.returncode == 0, result.stderr
    [argv] = run_calls(calls)
    # Passed by name, so the password isn't on the docker command line.
    assert ["-e", "DATABASE_URL"] in [argv[i : i + 2] for i in range(len(argv) - 1)]
    assert not any("s3cret" in a for a in argv)
    assert run_env(calls) == ["postgresql://app:s3cret@host.docker.internal:5432/app"]
    assert "--network" not in argv
    assert "host.docker.internal (Docker Desktop)" in result.stderr


@pytest.mark.skipif(not LINUX, reason="--network host is the Linux-engine path")
@pytest.mark.parametrize("shell", SHELLS)
def test_localhost_on_linux_engine_uses_host_network(shell, tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://app:pw@127.0.0.1/app\n")
    result, calls = run_fake(shell, tmp_path, "backup", env={"FAKE_DOCKER_OS": "Ubuntu 24.04"})
    assert result.returncode == 0, result.stderr
    [argv] = run_calls(calls)
    assert argv[argv.index("--network") + 1] == "host"
    assert run_env(calls) == [""]  # not rewritten


@pytest.mark.parametrize("shell", SHELLS)
def test_pgb_network_joins_a_compose_network(shell, tmp_path):
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://app:pw@db:5432/app\nPGB_NETWORK=shop_default\n"
    )
    _, calls = run_fake(shell, tmp_path, "nightly", "start")
    [argv] = run_calls(calls)
    assert argv[argv.index("--network") + 1] == "shop_default"
    assert argv[-2:] == ["ghcr.io/iotabyt/pgbackup-r2:pg16", "schedule"]


@pytest.mark.parametrize("shell", SHELLS)
def test_remote_database_and_keygen_get_no_network_options(shell, tmp_path):
    (tmp_path / ".env").write_text(
        f"DATABASE_URL={LOCAL_URL.replace('localhost', 'db.neon.tech')}\n"
    )
    _, calls = run_fake(shell, tmp_path, "backup")
    assert "--network" not in run_calls(calls)[0]
    (tmp_path / ".env").write_text(f"DATABASE_URL={LOCAL_URL}\n")
    _, calls = run_fake(shell, tmp_path, "keygen")
    assert [c for c in calls if c[:1] == ["info"]] == []  # no Docker Desktop probe either
    assert "--network" not in run_calls(calls)[0]


# --- doctor --------------------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_doctor_stops_when_docker_is_down(shell, tmp_path):
    (tmp_path / ".env").write_text("S3_BUCKET=b\n")
    result, calls = run_fake(shell, tmp_path, "doctor", env={"FAKE_DOCKER_INFO_RC": "1"})
    assert result.returncode == 1
    assert "✗ Docker isn't running" in result.stdout
    assert run_calls(calls) == []


@pytest.mark.parametrize("shell", SHELLS)
def test_doctor_needs_env(shell, tmp_path):
    result, calls = run_fake(shell, tmp_path, "doctor")
    assert result.returncode == 1
    assert "✓ Docker is running" in result.stdout
    assert "✗ No .env" in result.stdout and "pgb init" in result.stdout
    assert run_calls(calls) == []


@pytest.mark.parametrize("shell", SHELLS)
def test_doctor_runs_the_image_check_with_db_network(shell, tmp_path):
    (tmp_path / ".env").write_text("PGB_NETWORK=shop_default\n")
    result, calls = run_fake(shell, tmp_path, "doctor")
    assert result.returncode == 0, result.stderr
    assert "✓ .env found" in result.stdout
    [argv] = run_calls(calls)
    assert argv[argv.index("--network") + 1] == "shop_default"
    assert argv[-2:] == ["ghcr.io/iotabyt/pgbackup-r2:pg16", "doctor"]


# --- init --detect -------------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_init_detect_writes_suggestion_with_yes(shell, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("S3_BUCKET=b\nPRUNE=true\nTIERS=daily=old/\nPG_MAJOR=17\n")
    env_file.chmod(0o600)
    result, calls = run_fake(shell, tmp_path, "init", "--detect", "--yes")
    assert result.returncode == 0, result.stderr
    [argv] = run_calls(calls)
    assert "-it" not in argv and argv[-3:] == [
        "ghcr.io/iotabyt/pgbackup-r2:pg17",
        "detect",
        "--env-lines",
    ]
    assert "Found folders daily/" in result.stderr
    assert 'TIERS="daily=daily/ monthly=monthly/"' in result.stdout
    assert env_file.read_text().splitlines() == [
        "S3_BUCKET=b",
        "PG_MAJOR=17",
        'TIERS="daily=daily/ monthly=monthly/"',
        "PRUNE=false",
    ]
    assert env_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("shell", SHELLS)
def test_init_detect_asks_and_leaves_env_alone_without_a_terminal(shell, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("S3_BUCKET=b\n")
    result, _ = run_fake(shell, tmp_path, "init", "--detect")
    assert result.returncode == 0, result.stderr
    assert "unchanged" in result.stdout
    assert env_file.read_text() == "S3_BUCKET=b\n"


@pytest.mark.parametrize("shell", SHELLS)
def test_init_detect_with_nothing_to_suggest(shell, tmp_path):
    (tmp_path / ".env").write_text("S3_BUCKET=b\n")
    result, _ = run_fake(shell, tmp_path, "init", "--detect", "--yes", env={"FAKE_SUGGEST": ""})
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".env").read_text() == "S3_BUCKET=b\n"


@pytest.mark.parametrize("shell", SHELLS)
def test_init_detect_without_env_writes_the_template_first(shell, tmp_path):
    result, calls = run_fake(shell, tmp_path, "init", "--detect")
    assert result.returncode == 0, result.stderr
    assert run_calls(calls) == []
    assert (tmp_path / ".env").is_file()
    assert "then run pgb init --detect again" in result.stdout
    result, _ = run_fake(shell, tmp_path, "init", "--bogus")
    assert result.returncode == 1 and "unknown option --bogus" in result.stderr


# --- help ----------------------------------------------------------------------------------

OWN = ["init", "drill", "nightly", "update", "version", "help"]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", OWN)
def test_own_command_help(shell, tmp_path, command):
    result, calls = run_fake(shell, tmp_path, "help", command)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    # Kamal-style: a one-line summary, the usage, then examples.
    assert lines[0].endswith(".") and lines[1] == ""
    assert lines[2].startswith(f"Usage: pgb {command}")
    assert "Examples:" in lines
    assert lines[lines.index("Examples:") + 1].startswith(f"  pgb {command}")
    assert calls == []  # no Docker needed


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_flag_on_own_commands_does_nothing_else(shell, tmp_path, flag):
    result, calls = run_fake(shell, tmp_path, "drill", flag)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Restore a backup into a throwaway database")
    assert calls == []  # in particular: no network or scratch database started


def test_own_help_is_the_same_in_both_scripts(tmp_path):
    if shutil.which("pwsh") is None:
        pytest.skip("needs pwsh")
    for command in OWN:
        bash, _ = run_fake(SHELLS[0].values[0], tmp_path, "help", command)
        pwsh, _ = run_fake(SHELLS[1].values[0], tmp_path, "help", command)
        assert bash.stdout == pwsh.stdout, command


@pytest.mark.parametrize("shell", SHELLS)
def test_help_for_image_commands_asks_the_image(shell, tmp_path):
    result, calls = run_fake(shell, tmp_path, "help", "backup")
    [argv] = run_calls(calls)
    assert ["-e", f"PGB_VERSION={__version__}"] in [argv[i : i + 2] for i in range(len(argv) - 1)]
    assert argv[-3:] == ["ghcr.io/iotabyt/pgbackup-r2:pg16", "backup", "--help"]


@pytest.mark.parametrize("shell", SHELLS)
def test_every_container_knows_it_runs_under_pgb(shell, tmp_path):
    _, calls = run_fake(shell, tmp_path, "list")
    [argv] = run_calls(calls)
    assert f"PGB_VERSION={__version__}" in argv


@pytest.mark.parametrize("shell", SHELLS)
def test_doctor_gets_the_env_key_names_only(shell, tmp_path):
    (tmp_path / ".env").write_text("S3_BUKET=b\nDATABASE_URL=postgresql://a:secret@h/d\n# X=1\n")
    result, calls = run_fake(shell, tmp_path, "doctor")
    assert result.returncode == 0, result.stderr
    [argv] = run_calls(calls)
    assert argv[argv.index("PGB_ENV_KEYS") - 1] == "-e"  # passed by name
    [keys] = run_keys(calls)
    assert [k for k in keys.split(",") if k] == ["S3_BUKET", "DATABASE_URL"]
    assert "secret" not in keys
