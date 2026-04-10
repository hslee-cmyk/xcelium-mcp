"""shell_utils.py — Core shell utilities for xcelium-mcp.

Extracted from sim_runner.py (v4.4 code review refactoring).
Contains: shell_quote, shell_run, build_redirect, login_shell_cmd,
validate_path, sanitize_signal_name, is_safe_tcl_string,
UserInputRequired.

All modules import these from shell_utils to avoid circular deps.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex

logger = logging.getLogger(__name__)


# ===================================================================
# Shell quoting & redirect
# ===================================================================


def shell_quote(s: str) -> str:
    """Shell-quote a user-supplied string to prevent injection."""
    return shlex.quote(s)




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


def get_ssh_cmd_timeout(runner: dict, default: float = 30.0) -> float:
    """Return SSH infrastructure command timeout from runner config or default.

    Reads 'ssh_command_timeout' from .mcp_sim_config.json runner section.
    Use this for infrastructure commands (file write, pid check, mkdir) —
    NOT for simulation polling (poll_batch_log uses sim timeout from resolve_sim_params).
    """
    return float(runner.get("ssh_command_timeout", default))


async def shell_run_with_retry(
    cmd: str,
    timeout: float = 30.0,
    max_retries: int = 2,
    backoff_base: float = 2.0,
) -> str:
    """shell_run with exponential backoff retry for transient SSH timeouts.

    Retry only on asyncio.TimeoutError (transient SSH delay).
    Non-timeout errors (command failure) propagate immediately without retry.

    Backoff: attempt 0 → fail → wait 1s → attempt 1 → fail → wait 2s → attempt 2 → raise
    """
    for attempt in range(max_retries + 1):
        try:
            return await shell_run(cmd, timeout=timeout)
        except asyncio.TimeoutError:
            if attempt == max_retries:
                raise
            wait = backoff_base ** attempt  # 1s, 2s
            await asyncio.sleep(wait)
    raise asyncio.TimeoutError(f"shell_run_with_retry exhausted after {max_retries} retries: {cmd}")


async def shell_run(cmd: str, timeout: float = 60.0, log_file: str = "") -> str:
    """Run a shell command as a local subprocess.

    Since xcelium-mcp runs on cloud0, this is a local subprocess —
    not an SSH call. Uses subprocess.run via asyncio.to_thread.
    """
    if "2>&1" in cmd:
        raise ValueError(
            "Do not use '2>&1' — tcsh interprets '&1' as filename. "
            "Use log_file parameter or build_redirect() instead."
        )
    if log_file:
        cmd = f"{cmd} {build_redirect(log_file)}"

    def _run() -> str:
        import subprocess
        try:
            r = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = r.stdout
            if r.stderr:
                out += "\n" + r.stderr
            return out.strip()
        except subprocess.TimeoutExpired:
            raise asyncio.TimeoutError(f"shell_run timeout ({timeout}s): {cmd}")

    return await asyncio.to_thread(_run)


async def shell_run_fire_and_forget(cmd: str, timeout: float = 10.0) -> None:
    """Launch a shell command and return immediately without waiting for output.

    Uses subprocess.Popen with stdin/stdout/stderr all set to DEVNULL.
    The launched process runs independently — survives xcelium-mcp exit.
    Use this for PID watchers and other background helpers that should
    not block the caller.
    """
    import subprocess

    def _fire() -> None:
        subprocess.Popen(
            ["bash", "-c", cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    await asyncio.to_thread(_fire)


def login_shell_cmd(login_shell: str, cmd: str) -> str:
    """Build a command that runs in login shell environment.

    WARNING: Do not include '2>/dev/null' or '2>&1' in cmd.
    tcsh has no stderr-only redirect syntax — '2>' causes 'Ambiguous redirect'.
    If stderr suppression is needed, either:
      - Filter results in Python (e.g. check '/' in path for 'which' output)
      - Add shell_run(stderr_mode="drop") parameter (implement when needed)
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


def build_eda_command(runner: dict, inner_cmd: str) -> str:
    """Build a shell command that sources EDA environment before execution.

    Uses runner config to determine environment sourcing strategy:
    - source_separately=True: explicit source commands in env_shell
    - Otherwise: login_shell_cmd for login shell environment

    Args:
        runner: runner config dict with keys: env_files, env_shell,
                login_shell, source_separately.
        inner_cmd: The command to run after environment setup.

    Returns:
        Shell command string ready for shell_run().
    """
    env_files = runner.get("env_files", [])
    if runner.get("source_separately") and env_files:
        env_shell = runner.get("env_shell", runner.get("login_shell", "/bin/csh"))
        source_cmds = "; ".join(f"source {shell_quote(f)}" for f in env_files)
        return f"{env_shell} -c '{source_cmds}; {inner_cmd}'"
    login_shell = runner.get("login_shell", "/bin/sh")
    return login_shell_cmd(login_shell, inner_cmd)


# ===================================================================
# Path & input validation
# ===================================================================


def validate_path(path: str, label: str = "path") -> str | None:
    """Reject paths with traversal components or null bytes. Returns error string or None if OK."""
    if "\x00" in path:
        return f"ERROR: {label} must not contain null bytes: {path!r}"
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


def is_safe_tcl_string(s: str) -> bool:
    """Check if a string is safe to embed in a Tcl command.

    Returns True if safe, False if it contains embedded exec or dangerous patterns.
    Used by execute_tcl denylist enhancement.
    """
    lower = s.lower()
    # Check for dangerous bracket commands — consistent with execute_tcl denylist
    _BRACKET_DENYLIST = (
        r'\[\s*exec\b',
        r'\[\s*open\b',
        r'\[\s*socket\b',
        r'\[\s*file\s+delete\b',
        r'\[\s*file\s+rename\b',
        r'\[\s*interp\s+eval\b',
        r'\[\s*interp\s+create\b',
        r'\[\s*load\b',
    )
    for pattern in _BRACKET_DENYLIST:
        if re.search(pattern, lower):
            return False
    return True


# ===================================================================
# Utility functions (moved from sim_runner.py)
# ===================================================================


_USER_TMP: str = ""  # cached after first call
_USER_TMP_LOCK = asyncio.Lock()


async def get_user_tmp_dir() -> str:
    """Get per-user temp directory. Creates on first call.

    Returns /tmp/xcelium_mcp_{uid}/ — unique per Unix user.
    Python and Tcl must use the same path pattern for ready file sync.
    """
    global _USER_TMP
    if _USER_TMP:
        return _USER_TMP
    async with _USER_TMP_LOCK:
        # Double-check after acquiring lock
        if _USER_TMP:
            return _USER_TMP
        r = await shell_run("id -u", timeout=5)
        uid = r.strip()
        _USER_TMP = f"/tmp/xcelium_mcp_{uid}"
        await shell_run(f"mkdir -p {_USER_TMP}", timeout=5)
        return _USER_TMP


def _parse_shm_path(db_list_output: str) -> str:
    """Parse SHM path from xmsim 'database -list' output."""
    for line in db_list_output.strip().splitlines():
        line = line.strip().strip("'\"")
        if ".shm" in line:
            idx = line.index(".shm") + 4
            return line[:idx]
    return ""


def _parse_time_ns(where_output: str) -> int:
    """Parse simulation time from xmsim 'where' output into nanoseconds."""
    m = re.search(r'(\d+)\s+MS\s*\+\s*(\d+)', where_output, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 1_000_000 + int(m.group(2))
    m = re.search(r'(\d+)\s+US\s*\+\s*(\d+)', where_output, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 1000 + int(m.group(2))
    m = re.search(r'(\d+)\s+NS\s*\+\s*(\d+)', where_output, re.IGNORECASE)
    if m:
        return int(m.group(1)) + int(m.group(2))
    m = re.search(r'(\d+)', where_output)
    if m:
        return int(m.group(1))
    return 0
