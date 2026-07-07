# xcelium-mcp deployment (안C+)

Implements `docs/02-design/features/xcelium-mcp-server-process-lifecycle.design.md`.
Replaces the old cold-spawn-per-connection model (`ssh cloud0 xcelium-mcp`) with a
resident prefork supervisor — no code in `server.py`/`tools/*.py`/`BridgeManager`
was changed; only the process wrapper around them.

Everything below runs **without root/sudo/admin** (verified against cloud0's
actual constraints — see design.md §1.3: `Linger=no`, no passwordless sudo,
no `socat`). This is the default and only supported path in this repo; the
`systemd-user-optional/` units are a later upgrade an admin can opt into once
they've run `loginctl enable-linger <user>` — do not use them before that.

## 1. No install step needed for the new modules (2026-07-07 실측)

`/opt/mcp-env/` on cloud0 is **root-owned and not writable** by a regular user
(confirmed: `touch /opt/mcp-env/bin/x` → Permission denied). This means
`pip install -e .` cannot generate the `xcelium-mcp-supervisor`/`xcelium-mcp-culler`
console-script wrappers declared in `pyproject.toml` — those files would need
to be created under `/opt/mcp-env/bin/`, which requires root.

This is not a blocker: the editable install (`/opt/xcelium-mcp` → already on
`sys.path` via the existing `pip install -e`) already makes the new modules
importable right now. `crontab.example` and the `systemd-user-optional/` units
were updated to invoke them via `python3 -m` instead of a console script:

```bash
/opt/mcp-env/bin/python3 -m xcelium_mcp.supervisor
/opt/mcp-env/bin/python3 -m xcelium_mcp.idle_culler
```

`git pull` on cloud0 is the only "install" step — no `pip install` re-run needed
unless `pyproject.toml`'s dependencies themselves changed.

## 2. Register the cron watchdog (cloud0, your own user — no sudo)

```bash
crontab -e
```

Merge in the lines from `crontab.example` (do **not** blindly `crontab crontab.example`
if you already have other cron entries — it would overwrite them).

This starts the supervisor now (first `* * * * *` tick) and re-starts it within
1 minute if it ever dies. The culler runs every 5 minutes and only ever touches
workers that are both bridge-disconnected and older than the idle threshold
(default 6h, override with `XCELIUM_MCP_IDLE_THRESHOLD_SEC` in the crontab line's
environment if needed).

## 3. Switch the client's `~/.claude.json`

Replace the `xcelium-mcp` entry with `claude-json-mcpServers-snippet.json`'s
content, filling in `<remote $HOME>` with the actual remote home directory —
**check it first with `ssh cloud0 'echo $HOME'`, don't assume `/home/<user>`**
(on this cloud0, hoseung.lee's home is `/users/hoseung.lee`, not
`/home/hoseung.lee` — design.md's original example guessed wrong). The socket
path is a literal ssh argv element — it cannot use a bare `$HOME`, which is a
*local* shell variable and never reaches the remote `ssh` argv.

Reconnect Claude Code (or restart the session) to pick up the new config.

## 4. Verify (smoke test, matches design.md §8 T-1/T-2/T-7/T-8)

```bash
# T-1: repeated connect/disconnect shouldn't accumulate workers
ps -ef | grep xcelium_mcp.supervisor   # before vs. after a few Claude Code reconnects

# T-7: kill the supervisor, confirm cron restarts it within ~1 minute
pkill -f xcelium_mcp.supervisor
sleep 70 && ps -ef | grep xcelium_mcp.supervisor

# T-8: the forwarder works standalone (no socat involved)
/opt/mcp-env/bin/python3 -m xcelium_mcp.stdio_forward $HOME/.xcelium_mcp/run/xcelium-mcp.sock
```

## Rollback

If anything goes wrong, revert `~/.claude.json`'s `xcelium-mcp` entry to the
old direct-spawn form (`"command": "ssh", "args": [..., "cloud0",
"/opt/mcp-env/bin/xcelium-mcp"]`) and remove the crontab lines — the old code
path (`server.py:main()`) is untouched and still works standalone.
