"""shell_utils.py — Core shell utilities for xcelium-mcp.

Extracted from sim_runner.py (v4.4 code review refactoring).
Contains: shell_quote, ssh_run, build_redirect, login_shell_cmd,
validate_path, sanitize_signal_name, sanitize_tcl_string,
UserInputRequired.

All modules import these from shell_utils to avoid circular deps.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex
from typing import Any

logger = logging.getLogger(__name__)


# ===================================================================
# Shell quoting & redirect
# ===================================================================


def shell_quote(s: str) -> str:
    """Shell-quote a user-supplied string to prevent injection."""
    return shlex.quote(s)


# Backward compat alias — will be removed in v5
sq = shell_quote


def build_redirect(log_path: str) -> str:
    """Build shell redirect suffix safe for both bash and tcsh.

    NEVER use '2>&1' — tcsh interprets '&1' as filename, creating file '1'.
    Use '>& file' which works in both bash and tcsh.
    """
    return f">& {log_path}"


# ===================================================================
# UserInputRequired
# ===================================================================


class UserInputRequired(Exception):
    """Raised when user input is needed to continue."""
    def __init__(self, prompt: str):
        self.prompt = prompt
        super().__init__(prompt)


# ===================================================================
# SSH / subprocess execution
# ===================================================================


async def ssh_run(cmd: str, timeout: float = 60.0, log_file: str = "") -> str:
    """Run a shell command as a local subprocess.

    Since xcelium-mcp runs on cloud0, this is a local asyncio subprocess —
    not an SSH call. Combined stdout+stderr is returned as a single string.
    """
    if "2>&1" in cmd:
        raise ValueError(
            "Do not use '2>&1' — tcsh interprets '&1' as filename. "
            "Use log_file parameter or build_redirect() instead."
        )
    if log_file:
        cmd = f"{cmd} {build_redirect(log_file)}"

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise asyncio.TimeoutError(f"ssh_run timeout ({timeout}s): {cmd}")
    return (stdout + stderr).decode("utf-8", errors="replace").strip()


def login_shell_cmd(login_shell: str, cmd: str) -> str:
    """Build a command that runs in login shell environment.

    WARNING: Do not include '2>/dev/null' or '2>&1' in cmd.
    tcsh has no stderr-only redirect syntax — '2>' causes 'Ambiguous redirect'.
    If stderr suppression is needed, either:
      - Filter results in Python (e.g. check '/' in path for 'which' output)
      - Add ssh_run(stderr_mode="drop") parameter (implement when needed)
    """
    safe_cmd = cmd.replace("'", "'\\''")
    if "tcsh" in login_shell or "csh" in login_shell:
        return (
            f"{login_shell} -c '"
            f"if (-f ~/.tcshrc) source ~/.tcshrc >& /dev/null; "
            f"if (-f ~/.cshrc) source ~/.cshrc >& /dev/null; "
            f"set noglob; "
            f"{safe_cmd}'"
        )
    return f"{login_shell} -l -c '{safe_cmd}'"


# ===================================================================
# Path & input validation
# ===================================================================


def validate_path(path: str, label: str = "path") -> str | None:
    """Reject paths with traversal components. Returns error string or None if OK."""
    if ".." in path.split("/"):
        return f"ERROR: {label} must not contain '..' (path traversal rejected): {path}"
    return None


_SIGNAL_NAME_RE = re.compile(r'^[A-Za-z0-9_.\[\]:*\\\/ ]+$')


def sanitize_signal_name(name: str) -> str:
    """Sanitize a signal name for safe Tcl command interpolation.

    Allows: alphanumeric, underscore, dot, brackets, colon, star, backslash, slash, space.
    Rejects: Tcl injection chars like [, ], $, ;, {, } when used outside of
    legitimate signal path syntax.

    Raises ValueError if the name contains dangerous characters.
    """
    # Strip leading/trailing whitespace
    stripped = name.strip()
    if not stripped:
        raise ValueError("Signal name cannot be empty")
    # Check for Tcl command substitution: [exec ...] or [...]
    if re.search(r'\[(?![\d:]+\])', stripped):
        # Allow [N:M] bit-select but reject [exec ...] etc.
        # Bracket contents must be only digits and colons
        for match in re.finditer(r'\[([^\]]*)\]', stripped):
            content = match.group(1)
            if not re.fullmatch(r'[\d:]+', content):
                raise ValueError(
                    f"Signal name contains potential Tcl injection: {name!r}. "
                    f"Bracket content '{content}' is not a valid bit-select."
                )
    # Check for other dangerous Tcl metacharacters
    if '$' in stripped or ';' in stripped:
        raise ValueError(
            f"Signal name contains forbidden Tcl metachar: {name!r}. "
            "Only signal path characters allowed."
        )
    return stripped


def sanitize_tcl_string(s: str) -> bool:
    """Check if a string is safe to embed in a Tcl command.

    Returns True if safe, False if it contains embedded exec or dangerous patterns.
    Used by execute_tcl denylist enhancement.
    """
    lower = s.lower()
    # Check for embedded [exec ...] anywhere in the string
    if re.search(r'\[\s*exec\b', lower):
        return False
    # Check for embedded [open ...] anywhere
    if re.search(r'\[\s*open\b', lower):
        return False
    return True
