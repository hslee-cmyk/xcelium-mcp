---
description: Cancel the active Ralph loop (same-session mode)
allowed-tools: Bash, Read
argument-hint: [--force]
---

# Cancel Ralph Loop

Stop the active Ralph loop by removing the state file. Same-session mode only.

## Arguments

- `$ARGUMENTS` - Optional flags
- `--force` or `-f` - Remove state file without prompting

## Instructions

<instruction>
Cancel/stop the active Ralph loop:

**Step 1: Check current state**

```bash
ls -la .claude/ralph-loop.local.md 2>/dev/null
```

If the file doesn't exist, report "No active Ralph loop found" and stop.

If it exists, show its contents:
```bash
cat .claude/ralph-loop.local.md
```

**Step 2: Remove state file**

If `--force` is in `$ARGUMENTS`, or after user confirms:

```bash
rm -f .claude/ralph-loop.local.md
```

**Step 3: Report status**

Confirm state file was removed. Note that any in-progress work remains in the working tree — only the loop state is cleared.

The Stop hook will now allow normal exit on next end-of-turn.
</instruction>
