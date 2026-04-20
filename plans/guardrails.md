# Ralph Guardrails (Signs)

Learned constraints that prevent repeated failures. Each "sign" is a rule discovered through iteration failures. Add new signs as you encounter failure patterns.

> "Progress should persist. Failures should evaporate." — The Ralph philosophy

---

## Verification Signs

### SIGN-001: Verify Before Complete
**Trigger:** About to output completion promise
**Instruction:** ALWAYS run `python -m pytest && python -m ruff check src/` and confirm it passes before outputting `<promise>COMPLETE</promise>`
**Reason:** Models tend to declare victory without proper verification

### SIGN-002: Check All Tasks Before Complete
**Trigger:** Completing a task in multi-task mode
**Instruction:** Re-read plans/prd.json and count remaining `passes: false` tasks (ignore `skip: true`). Only output completion promise when ALL non-skipped tasks pass, not just the current one.
**Reason:** Premature completion exits loop with work remaining

---

## Progress Signs

### SIGN-003: Document Learnings
**Trigger:** Completing any task
**Instruction:** Update plans/progress.md with what was learned (patterns discovered, files modified, decisions made) before ending iteration
**Reason:** Future iterations need context to avoid re-discovering the same patterns

### SIGN-004: Small Focused Changes
**Trigger:** Making changes per iteration
**Instruction:** Keep changes small and focused. Commit incrementally when tests pass. Don't try to solve everything in one iteration.
**Reason:** Large changes are harder to debug when verification fails

---

## Task Management Signs

### SIGN-005: Use Skip for Manual Tasks
**Trigger:** Encountering a task that requires manual human intervention (EDA tool licenses, cloud0 server setup, physical hardware)
**Instruction:** Set `skip: true` and `skipReason` in plans/prd.json for tasks that cannot be automated. The Ralph loop will ignore skipped tasks and can complete without them.
**Reason:** Allows loop to complete automatable work without blocking on manual steps

### SIGN-006: Reference GitHub Issues in Commits
**Trigger:** Committing changes for a prd.json task
**Instruction:** Include `Fixes #N` or `Closes #N` in commit message body (where N is the `github_issue` from prd.json). Format: `fix: description\n\nFixes #61`
**Reason:** Auto-closes GitHub issues when merged to main, maintains traceability

---

## Project-Specific Signs (xcelium-mcp)

### SIGN-007: No Direct cloud0 Modifications
**Trigger:** About to modify files on remote cloud0 server via ssh_run
**Instruction:** cloud0 is a TEST environment. Final code lives in local xcelium-mcp repo. Do not edit remote files — pull locally, fix, commit, then sync to cloud0.
**Reason:** cloud0 modifications are lost on git pull; local repo is source of truth

### SIGN-008: Ruff Check Scope
**Trigger:** Running ruff as part of verification
**Instruction:** Use `python -m ruff check src/` (not `ruff .`) — only lint the source package, not tests/docs/tcl.
**Reason:** Tests and TCL scripts have different conventions; lint scope should match the deliverable

### SIGN-009: No Double Error Prefix in Except Clauses
**Trigger:** Writing `except ... as e: return f"ERROR: {prefix}: {e}"` where `e` may already carry the same prefix
**Instruction:** Always check `if not msg.startswith("ERROR:")` before prepending. Pattern: `msg = str(e); msg = f"ERROR: {msg}" if not msg.startswith("ERROR:") else msg`
**Reason:** F-129 — TclError from xmsim already contained "restore failed:", so the except clause produced "ERROR: restore failed: restore failed: xmsim: ..."
**Added after:** F-129 (2026-04-16)

### SIGN-010: Pre-Validate Before Forwarding to Tcl Bridge
**Trigger:** About to pass a name/path/key to the Tcl bridge (save, restore, probe, bisect)
**Instruction:** Read the manifest/registry first and return an early ERROR if the name doesn't exist. Never rely on xmsim error messages to surface bad inputs — xmsim may silently succeed with wrong results.
**Reason:** F-128 — restoring unknown checkpoint name returned current sim state instead of error
**Added after:** F-128 (2026-04-16)

### SIGN-011: Modify Lists BEFORE join(), Not After
**Trigger:** Building a shell command via `"; ".join(parts)` or similar, then wanting to inject an extra item
**Instruction:** Always append/insert into the parts list BEFORE calling join. Inserting into `inner_parts` after the joined string is already assigned is a no-op for the output.
**Reason:** F-116 — `inner_parts.append(f"setenv MCP_SHM_PATH ...")` was called after `inner_cmd = "; ".join(inner_parts)`, so the env var never appeared in the launched command
**Added after:** F-116 v1→v2 (2026-04-15)

### SIGN-012: Never Hardcode Project-Specific File Patterns
**Trigger:** Constructing a SHM/dump/log file path by name (e.g. `ci_top_{test_name}.shm`)
**Instruction:** Use `find_shm(sim_dir, test_name)` from `shell_utils`. It globs `*{test_name}*.shm` with newest-file selection and falls back to `*.shm` — no project prefix assumptions.
**Reason:** F-116 — `ci_top_{test_name}.shm` only worked for the ci_top project; broke for any other testbench prefix
**Added after:** F-116 (2026-04-15)

### SIGN-013: Per-Item Delete Loops Over Bulk Tcl Commands
**Trigger:** Clearing/deleting a set of Tcl simulator objects (breakpoints, watchpoints, stops)
**Instruction:** Iterate collected IDs and delete each with an explicit `stop -delete {id}` (or equivalent) rather than `stop -delete -all`. Bulk commands may silently no-op or delete unintended objects.
**Reason:** F-114 — `stop -delete -all` did not reliably remove stops in some SimVision versions; per-ID loop is deterministic
**Added after:** F-114 (2026-04-15)

### SIGN-014: Always Use login_shell_cmd for EDA Tool Invocations
**Trigger:** Launching xrun, SimVision, ncsim, or any EDA binary via shell_run/nohup
**Instruction:** Wrap the command with `login_shell_cmd(cmd)` from `shell_utils`. EDA tools require PATH/LD_LIBRARY_PATH from sourced shell profiles; a plain subprocess won't find them.
**Reason:** F-110 — compare_waveforms launched SimVision without login shell; binary not found on PATH
**Added after:** F-110 (2026-04-14)

### SIGN-015: Match Tcl Command Flags to the Operation Variant
**Trigger:** Calling a Tcl command with variant-specific flags (bisect, probe, stop, watch)
**Instruction:** Verify the exact flag name in SimVision's Tcl reference for the specific operation variant. `-condition` and `-object` are NOT interchangeable even if they look similar. Write a targeted test that exercises the exact flag path.
**Reason:** F-109 — bisect `change` op used `-condition` (edge trigger) instead of `-object` (value change), causing immediate false positives
**Added after:** F-109 (2026-04-14)

### SIGN-017: Lazy Import Inside Function Blocks Module-Level Name
**Trigger:** Adding a use of `name` earlier in a function where a `from module import name` already exists later in the same function body
**Instruction:** If `name` is already imported at module level, remove the lazy (inline) `from ... import name` from the function body. Otherwise move the lazy import to BEFORE the first use, or extract a helper. NEVER have both a module-level import and a function-level import of the same name.
**Reason:** F-130 — `scan_ready_files` was used at line 200 (P4 cleanup) but also had a lazy `from bridge_manager import scan_ready_files` at line 301; Python compiled it as a local variable and raised `UnboundLocalError` at line 200
**Added after:** F-130 (2026-04-20)

### SIGN-016: Parse Output Before Relying On It
**Trigger:** Using `stop -show` or similar Tcl listing commands to enumerate IDs for subsequent operations
**Instruction:** Parse the listing output line-by-line to extract IDs before building delete/modify commands. Do NOT assume a fixed output format — run the listing, capture output, then parse.
**Reason:** F-115 — watch clear-all used a stale `watch_ids` list instead of fresh `stop -show` parse, leaving ghost watchpoints
**Added after:** F-115 (2026-04-15)

### SIGN-018: No `...` Recursive Wildcard in Tcl Hierarchy Commands
**Trigger:** Building a Tcl `describe` or similar command that needs to match signals at any depth (e.g. `describe top.hw...*sda*`)
**Instruction:** Use Python-side recursion via `scope show {scope}` instead. Call `scope show` for each scope level, collect matching leaf names with `fnmatch`, and recurse into `u_*`-prefixed children. Never embed `...` in a Tcl hierarchy path.
**Reason:** F-131 — `describe top.hw...*sda*` was tried on both xmsim (error: "Invalid first character in HDL identifier: ..*sda*") and SimVision (error: "invalid command name 'describe'"). Neither tool recognises `...` as a recursive wildcard.
**Added after:** F-131 (2026-04-20)

### SIGN-019: Bridge-Specific Tcl Commands Must Be Routed to the Correct Bridge
**Trigger:** Sending a Tcl command that is only supported by one bridge type (xmsim vs SimVision) via `bridges.get_bridge(target)` which may return the wrong type
**Instruction:** Before executing bridge-specific commands, check `bridge is bridges.xmsim_raw` and route to the correct bridge. If `scope show` is needed → must use SimVision bridge. If `describe` / `value` / `drivers` → must use xmsim. Add an explicit error or auto-fallback rather than letting the helper silently return empty results.
**Reason:** F-132 — `scope show` is SimVision-only; when `target="auto"` returned xmsim, `_list_signals_recursive` caught `TclError` silently and returned `[]`, causing "No signals found" instead of a meaningful error.
**Added after:** F-132 (2026-04-20)
