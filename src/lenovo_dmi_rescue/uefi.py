"""Emit UEFI Shell scripts that actually run on real hardware.

Two traps cost real debugging time on the machine this tool was written for.
Both are handled here so callers cannot reintroduce them:

**Encoding.**  A ``.nsh`` file must be pure ASCII with CRLF line endings.
A UTF-8 comment gets mangled by a GBK console, and worse, a trailing UTF-8
byte can swallow the ``CR`` of a ``CRLF`` pair, gluing two lines into one and
producing a wall of ``'xxx' is not recognized`` errors.

**Leading slashes in ``echo``.**  The UEFI Shell parses a ``/token`` argument
to ``echo`` as an unknown flag and drops the *entire line* from the output::

    echo ..... DELETE /mfgmode .....     ->  echo: Unknown flag - '/mfgmode'
    echo ... TPM / fTPM option              ->  echo: Unknown flag - '/'

Nothing breaks, but the operator loses the hints exactly when they are needed.
:func:`sanitize_echo_line` removes those slashes and :func:`validate` refuses to
emit a file that still contains one.
"""

from __future__ import annotations

import re
from pathlib import Path

# The shell rejects any argument that *starts* with a slash, so a bare "/" is
# just as fatal as "/mfgmode" -- both produce "Unknown flag" and swallow the
# whole line.  Matching the slash itself (rather than slash-plus-letter) covers
# both cases.
_ECHO_SLASH_RE = re.compile(r"(?<=\s)/\s?")

NF = "NULL"
LF = "\n"
CRLF = "\r\n"


def sanitize_echo_line(line: str) -> str:
    """Drop slash-prefixed tokens from an ``echo`` line so the shell prints it."""
    if not line.lstrip().lower().startswith("echo"):
        return line
    return _ECHO_SLASH_RE.sub("", line)


def sanitize(text: str) -> str:
    """Apply :func:`sanitize_echo_line` to every line of ``text``."""
    return LF.join(sanitize_echo_line(line) for line in text.splitlines())


def validate(text: str) -> list[str]:
    """Return a list of human readable problems; empty means safe to write."""
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if any(ord(ch) > 126 for ch in line):
            problems.append(f"line {number}: non-ASCII character(s): {stripped!r}")
        if stripped.lower().startswith("echo") and _ECHO_SLASH_RE.search(line):
            problems.append(
                f"line {number}: echo argument would be read as a flag: {stripped!r}"
            )
    return problems


def write_nsh(path: str | Path, text: str, *, strict: bool = True) -> Path:
    """Write ``text`` as an ASCII/CRLF ``.nsh`` file.

    With ``strict=True`` (the default) the *original* text is validated first,
    so hand written scripts that would misbehave are rejected outright rather
    than silently repaired.  Generated scripts pass through :class:`NshBuilder`,
    which sanitises as it goes, and therefore validate cleanly here too.
    """
    path = Path(path)
    if strict:
        problems = validate(text)
        if problems:
            raise ValueError(
                "refusing to write a .nsh that would misbehave:\n  "
                + "\n  ".join(problems)
            )
    text = sanitize(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace(CRLF, LF).replace(LF, CRLF).encode("ascii"))
    return path


def write_ascii(path: str | Path, text: str) -> Path:
    """Write a plain ASCII/CRLF text file (used for README.txt on the USB stick)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace(CRLF, LF).replace(LF, CRLF).encode("ascii"))
    return path


class NshBuilder:
    """Small helper that keeps UEFI script output tidy and legal."""

    def __init__(self, *, title: str | None = None, echo_off: bool = True) -> None:
        self._lines: list[str] = []
        if echo_off:
            self._lines.append("echo -off")
        if title:
            self.rule()
            for chunk in title.splitlines():
                self.hint(chunk)
            self.rule()

    # ------------------------------------------------------------------ pieces

    def rule(self, char: str = ".") -> "NshBuilder":
        # Never use '=' -- the shell reads it as a flag on some firmware builds.
        self._lines.append("echo " + char * 60)
        return self

    def hint(self, text: str) -> "NshBuilder":
        self._lines.append(sanitize_echo_line(f"echo  {text}"))
        return self

    def blank(self) -> "NshBuilder":
        self._lines.append("echo.")
        return self

    def annotate(self, etype: int, lvar: str | None, text: str) -> "NshBuilder":
        """Comment line that never contains a slash-prefixed token."""
        tag = lvar.replace("/", "") if lvar else f"0x{etype:04X}"
        self._lines.append(sanitize_echo_line(f"echo . {tag} {text}"))
        return self

    def title(self, text: str) -> "NshBuilder":
        self.hint(".....")
        self.hint(f"..... {text} .....")
        self.hint(".....")
        return self

    def raw(self, command: str) -> "NshBuilder":
        """Append a verbatim command line (no sanitising: commands keep slashes)."""
        self._lines.append(command)
        return self

    def stall(self, microseconds: int = 1_000_000) -> "NshBuilder":
        self._lines.append(f"stall {microseconds}")
        return self

    def pause(self, message: str = "Press any key to continue") -> "NshBuilder":
        self.hint(message)
        self._lines.append("pause")
        return self

    # ------------------------------------------------------------------- build

    def text(self) -> str:
        return LF.join(self._lines) + LF

    def write(self, path: str | Path, *, strict: bool = True) -> Path:
        return write_nsh(path, self.text(), strict=strict)
