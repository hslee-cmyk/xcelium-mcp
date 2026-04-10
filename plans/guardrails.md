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

<!-- Add project-specific signs below as failures teach them
### SIGN-XXX: [Descriptive Name]
**Trigger:** [When this sign applies]
**Instruction:** [What to do instead]
**Reason:** [Why this matters]
**Added after:** [Iteration N / date when learned]
-->
