"""Everything that touches `age`.

The private key is never logged and never put in a process argument. When it comes from
AGE_IDENTITY, it is written to a 0600 file in a private temp directory just for the one
`age -d` call, and deleted as soon as that call returns, even if it fails.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator, Sequence

from . import hints
from .config import Settings
from .errors import CommandError, ConfigError
from .util import run

SECRET_PREFIX = "AGE-SECRET-KEY-1"
PUBLIC_PREFIX = "age1"
AGE_MAGIC = b"age-encryption.org/v1"


def check_recipients(recipients: Sequence[str]) -> tuple[str, ...]:
    """Refuse to continue without at least one public key: no unencrypted backups, ever."""
    if not recipients:
        raise ConfigError(
            "AGE_RECIPIENTS is empty, so the backup can't be encrypted and won't run",
            hint=f"Make a key pair with {hints.cmd('keygen')} and put the age1... public key "
            "in AGE_RECIPIENTS",
        )
    for r in recipients:
        if r.upper().startswith("AGE-SECRET-KEY-"):
            raise ConfigError(
                "AGE_RECIPIENTS contains a private key (AGE-SECRET-KEY-...). Only the age1... "
                "public key belongs on the server; keep the private key off it",
                hint="Put the age1... public key in AGE_RECIPIENTS, and the private key in "
                "AGE_IDENTITY on your laptop only",
            )
        if not (r.startswith(PUBLIC_PREFIX) or r.startswith("ssh-")):
            raise ConfigError(
                f"AGE_RECIPIENTS entry starting {r[:8]!r}... isn't an age public key "
                "(they start with age1)",
                hint="Copy the AGE_RECIPIENTS=age1... line from " + hints.cmd("keygen --plain"),
            )
    return tuple(recipients)


def recipient_args(recipients: Sequence[str]) -> list[str]:
    args: list[str] = []
    for r in check_recipients(recipients):
        args += ["-r", r]
    return args


def validate_identity(value: str) -> str:
    """Check AGE_IDENTITY looks like a private key. Returns the key text to write to a file."""
    lines = [ln.strip() for ln in value.strip().splitlines()]
    keys = [ln for ln in lines if ln and not ln.startswith("#")]
    if keys and all(k.startswith(SECRET_PREFIX) for k in keys):
        return "\n".join(keys) + "\n"
    first = keys[0] if keys else ""
    if first.startswith(PUBLIC_PREFIX):
        raise ConfigError(
            "AGE_IDENTITY looks like a public key (age1...), not a private key",
            hint="Use the private key, the line starting AGE-SECRET-KEY-1",
        )
    raise ConfigError(
        "AGE_IDENTITY doesn't start with AGE-SECRET-KEY-1; this looks like a typo or a "
        "truncated paste",
        hint="Copy the whole private key line into your .env, without quotes",
    )


def require_identity(settings: Settings) -> None:
    """Fail early, before downloading anything, if no usable private key is configured."""
    if settings.age_identity:
        validate_identity(settings.age_identity)
        return
    if settings.age_identity_file:
        _check_identity_file(settings.age_identity_file)
        return
    raise ConfigError(
        "no private key, so the backup can't be decrypted",
        hint="Set AGE_IDENTITY in your .env (the AGE-SECRET-KEY-1... line), "
        "or AGE_IDENTITY_FILE to a key file",
    )


def _check_identity_file(path: str) -> None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        raise ConfigError(
            f"can't read AGE_IDENTITY_FILE {path}: {exc.strerror}",
            hint="Point AGE_IDENTITY_FILE at a readable key file, or use AGE_IDENTITY instead",
        ) from None
    if SECRET_PREFIX not in text and "PRIVATE KEY" not in text:
        raise ConfigError(
            f"AGE_IDENTITY_FILE {path} doesn't contain an AGE-SECRET-KEY-1... private key",
            hint="Point AGE_IDENTITY_FILE at the file with the AGE-SECRET-KEY-1... line",
        )


@contextlib.contextmanager
def identity_path(settings: Settings) -> Iterator[str]:
    """Yield a path to the private key file, removing any temporary copy afterwards."""
    require_identity(settings)
    if settings.age_identity_file:
        yield settings.age_identity_file
        return
    assert settings.age_identity is not None
    content = validate_identity(settings.age_identity)
    directory = tempfile.mkdtemp(prefix="pgbackup-r2-key-")  # mode 0700
    path = os.path.join(directory, "identity.txt")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        yield path
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        with contextlib.suppress(OSError):
            os.rmdir(directory)


def _decrypt_error(exc: CommandError) -> CommandError:
    text = str(exc)
    if "no identity matched" in text:
        return CommandError(
            "the private key doesn't match this backup: it was encrypted for a different "
            "public key",
            hint="Check AGE_IDENTITY is the partner of the AGE_RECIPIENTS key on the server",
        )
    if "header" in text or "not an age file" in text or "introduction" in text:
        return CommandError(f"this file isn't an age-encrypted backup ({text})")
    return exc


def encrypt_bytes(data: bytes, recipients: Sequence[str]) -> bytes:
    return run(["age", *recipient_args(recipients)], input=data, what="age").stdout


def decrypt_bytes(data: bytes, settings: Settings) -> bytes:
    with identity_path(settings) as key:
        try:
            return run(["age", "-d", "-i", key], input=data, what="age").stdout
        except CommandError as exc:
            raise _decrypt_error(exc) from None


def decrypt_file(src: str, dst: str, settings: Settings) -> None:
    with identity_path(settings) as key:
        try:
            run(["age", "-d", "-i", key, "-o", dst, src], what="age")
        except CommandError as exc:
            with contextlib.suppress(FileNotFoundError):
                os.remove(dst)
            raise _decrypt_error(exc) from None


def keygen() -> tuple[str, str]:
    """A fresh (public, private) key pair, generated in memory. Nothing is written to disk."""
    out = run(["age-keygen"], what="age-keygen").stdout.decode()
    secret = next((ln.strip() for ln in out.splitlines() if ln.startswith(SECRET_PREFIX)), None)
    if not secret:
        raise CommandError(
            "age-keygen didn't print a private key",
            hint="This is a bug in the image: please report it at "
            "https://github.com/iotabyt/pgbackup-r2/issues",
        )
    public = next(
        (
            ln.split(":", 1)[1].strip()
            for ln in out.splitlines()
            if ln.lower().startswith("# public key:")
        ),
        None,
    )
    if not public:
        public = run(["age-keygen", "-y"], input=secret.encode() + b"\n").stdout.decode().strip()
    return public, secret
