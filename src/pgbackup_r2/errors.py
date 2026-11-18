"""Expected failures. The CLI shows these as one line (plus a hint) and exits 1, no traceback."""

from __future__ import annotations


class PgBackupError(Exception):
    """Base class for failures we expect and can explain.

    `hint` is an optional short "how to fix it", shown dimmed under the error in
    interactive mode and appended in brackets in plain mode.
    """

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class ConfigError(PgBackupError):
    """A setting is missing or invalid."""


class CommandError(PgBackupError):
    """A command could not do its job (tool failed, backup missing, refused, ...)."""
