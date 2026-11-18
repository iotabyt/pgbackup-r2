"""All user-facing output lives here, in one of two modes.

Interactive: stdout is a terminal, NO_COLOR isn't set and --plain wasn't passed. Rich
spinners, ✓ lines with timings, tables, and a big PASS / FAIL.

Plain: everything else (containers without -t, cron, CI, pipes, `schedule`). Timestamped
log lines on stderr and simple text on stdout. No colour, spinners or box drawing, so
`docker logs` and grep stay readable.

Commands only call the functions below; they never print or style things themselves.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

log = logging.getLogger("pgbackup_r2")

ALWAYS_PLAIN = ("schedule",)
QUIET_LOGGERS = ("botocore", "boto3", "s3transfer", "urllib3")


@dataclass
class _State:
    interactive: bool = False
    quiet: bool = False
    console: object | None = None  # rich.console.Console when interactive


_state = _State()


def should_be_interactive(
    *, stdout_isatty: bool, env: Mapping[str, str], plain: bool, command: str | None
) -> bool:
    """Pure decision so it's easy to test."""
    if plain or command in ALWAYS_PLAIN:
        return False
    if "NO_COLOR" in env and env["NO_COLOR"] != "":
        return False
    if env.get("TERM", "").lower() == "dumb":
        return False
    return stdout_isatty


class _UTCFormatter(logging.Formatter):
    converter = time.gmtime

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")


class _RichWarnings(logging.Handler):
    """In interactive mode, warnings and errors from anywhere become one yellow/red line."""

    def emit(self, record: logging.LogRecord) -> None:
        style = "red" if record.levelno >= logging.ERROR else "yellow"
        _print(f"[{style}]! {_escape(record.getMessage())}[/]")


def configure(*, plain: bool = False, quiet: bool = False, command: str | None = None) -> None:
    """Pick the mode and set up logging. Call once per CLI invocation."""
    interactive = should_be_interactive(
        stdout_isatty=_isatty(sys.stdout), env=os.environ, plain=plain, command=command
    )
    _state.interactive = interactive
    _state.quiet = quiet
    _state.console = None

    if interactive:
        from rich.console import Console

        _state.console = Console(highlight=False)
        handler: logging.Handler = _RichWarnings(level=logging.WARNING)
        level = logging.WARNING
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_UTCFormatter())
        level = logging.WARNING if quiet else logging.INFO
    handler._pgbackup_r2 = True  # type: ignore[attr-defined]

    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_pgbackup_r2", False)]
    root.addHandler(handler)
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def is_interactive() -> bool:
    return _state.interactive


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _escape(text: str) -> str:
    from rich.markup import escape

    return escape(text)


def _print(*objects, **kwargs) -> None:
    console = _state.console
    if console is None:
        print(*objects)
    else:
        console.print(*objects, **kwargs)  # type: ignore[attr-defined]


def _secs(seconds: float) -> str:
    return f"{seconds:.1f}s"


# --- progress ---------------------------------------------------------------------------


class Step:
    """Handle yielded by step(): set .done to change the ✓ line, .detail to add to it."""

    def __init__(self, done: str):
        self.done = done
        self.detail: str | None = None
        self.seconds = 0.0


@contextlib.contextmanager
def step(doing: str, *, done: str | None = None, log_line: str | None = None) -> Iterator[Step]:
    """A unit of work.

    Interactive: a spinner reading "<doing>…", then "✓ <done>  1.2s".
    Plain: `log_line` (if given) is logged at INFO when the step starts.
    """
    handle = Step(done or doing)
    started = time.monotonic()
    if not _state.interactive:
        if log_line:
            log.info("%s", log_line)
        yield handle
        handle.seconds = time.monotonic() - started
        return

    console = _state.console
    try:
        with console.status(f"[bold]{_escape(doing)}…[/]", spinner="dots"):  # type: ignore[attr-defined]
            yield handle
    except BaseException:
        _print(f"[red]✗ {_escape(doing)}[/]")
        raise
    handle.seconds = time.monotonic() - started
    if not _state.quiet:
        detail = f" [dim]{_escape(handle.detail)}[/]" if handle.detail else ""
        _print(f"[green]✓[/] {_escape(handle.done)}{detail}  [dim]{_secs(handle.seconds)}[/]")


def info(message: str) -> None:
    """A status line: logged in plain mode, dimmed in interactive mode."""
    if _state.interactive:
        if not _state.quiet:
            _print(f"[dim]{_escape(message)}[/]")
    else:
        log.info("%s", message)


def warn(message: str) -> None:
    log.warning("%s", message)


def error(message: str, hint: str | None = None) -> None:
    if _state.interactive:
        _print(f"[bold red]✗ {_escape(message)}[/]")
        if hint:
            _print(f"  [dim]{_escape(hint)}[/]")
    else:
        log.error("%s", f"{message} ({hint})" if hint else message)


def exception(message: str) -> None:
    """An unexpected crash: always show the traceback, it's a bug."""
    log.exception("%s", message)


def out(text: str = "") -> None:
    """Plain text to stdout, in either mode (e.g. instructions to copy)."""
    if _state.interactive:
        _print(_escape(text), soft_wrap=True)
    else:
        print(text)


# --- tables and results -----------------------------------------------------------------


def table(
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    align: str = "",
    bad_rows: Sequence[int] = (),
    title: str | None = None,
    plain_lines: Sequence[str] | None = None,
) -> None:
    """A table in interactive mode; `plain_lines` (if any) printed as-is in plain mode.

    `align` is one letter per column, "l" or "r". Rows listed in `bad_rows` show in red.
    """
    if not _state.interactive:
        for line in plain_lines or ():
            print(line)
        return
    from rich import box
    from rich.table import Table

    t = Table(title=title, box=box.SIMPLE_HEAD, title_justify="left", title_style="bold")
    for i, name in enumerate(columns):
        justify = "right" if i < len(align) and align[i] == "r" else "left"
        t.add_column(name, justify=justify, header_style="bold")
    bad = set(bad_rows)
    for i, row in enumerate(rows):
        t.add_row(*(_escape(c) for c in row), style="red" if i in bad else None)
    _print(t)


def result(ok: bool, headline: str, lines: Sequence[str] = ()) -> None:
    """The verdict. Plain: `PASS  <headline>` then indented lines. Interactive: a big banner."""
    word = "PASS" if ok else "FAIL"
    if not _state.interactive:
        print(f"{word}  {headline}")
        for line in lines:
            print(f"  {line}")
        return
    from rich.panel import Panel
    from rich.text import Text

    colour = "green" if ok else "red"
    body = Text(justify="left")
    body.append(f" {word} ", style=f"bold white on {colour}")
    # Keep the key on the banner line and move "(size, timing)" below it, so 80 columns fit.
    key, sep, extra = headline.partition("  (")
    body.append(f"  {key}", style="bold")
    if sep:
        body.append(f"\n       ({extra}", style="dim")
    for line in lines:
        body.append("\n       ")
        body.append(line, style=None if ok else "red")
    _print(Panel(body, border_style=colour, expand=False, padding=(1, 2)))


def keys(public: str, secret: str, plain_template: str) -> None:
    """Show a fresh key pair. Plain mode prints the exact .env-ready template."""
    if not _state.interactive:
        print(plain_template)
        return
    from rich import box
    from rich.panel import Panel

    _print("[bold]Your new key pair.[/] Do this once.")
    _print(
        "  1. Save the [bold red]PRIVATE[/] key in your password manager now. "
        "Lose it and your backups can't be opened."
    )
    _print("  2. Put the [bold green]PUBLIC[/] key on your server.\n")
    # HORIZONTALS: no side borders, so copying a key line doesn't pick up box characters.
    _print(
        Panel(
            f"AGE_RECIPIENTS={public}",
            title="[bold green]PUBLIC key[/] · server .env · safe to share",
            title_align="left",
            border_style="green",
            box=box.HORIZONTALS,
            expand=False,
        ),
        soft_wrap=True,
    )
    _print(
        Panel(
            f"AGE_IDENTITY={secret}",
            title="[bold red]PRIVATE key[/] · your laptop's .env · keep this secret",
            title_align="left",
            border_style="red",
            box=box.HORIZONTALS,
            expand=False,
        ),
        soft_wrap=True,
    )
    _print("[dim]Nothing was written to disk. Use --plain for copy-paste friendly output.[/]")
