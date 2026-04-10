---
description: Start an autonomous Ralph loop for iterative development (same-session mode)
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, TaskCreate, TaskUpdate, TaskList
argument-hint: "<task>" | --next [--branch NAME] [--max-iterations N] [--dry-run]
---

# Ralph Loop - Autonomous Development (Same-Session)

Start an autonomous iteration loop that continues until the task is complete or max iterations is reached. Uses Claude Code's Stop hook to block exit and re-prompt in the same session.

## Arguments

- `$ARGUMENTS` - The task description OR flags
- `--next` - Auto-pick the next failing task from plans/prd.json
- `--branch NAME` - Create/checkout branch before starting
- `--max-iterations N` - Maximum iterations (default: 50)
- `--completion-promise TEXT` - Completion signal (default: COMPLETE)
- `--dry-run` - Preview the prompt without starting the loop

## Usage Examples

```bash
# Single task
/ralph-loop "Fix all pytest failures" --max-iterations 20

# Next failing task from prd.json
/ralph-loop --next

# With branch
/ralph-loop --next --branch ralph/backlog

# Preview
/ralph-loop --next --dry-run
```

## Instructions

<instruction>
You are starting a Ralph loop - an autonomous development cycle in same-session mode.

**Step 1: Parse Arguments**

Extract from `$ARGUMENTS`:
- `--next` flag: If present, auto-pick task from prd.json
- `--branch NAME`: If present, extract the branch name
- `--dry-run` flag: If present, output prompt and stop
- `--max-iterations N`: Extract value or default to 50
- `--completion-promise TEXT`: Extract value or default to "COMPLETE"
- Task: Everything else (if not using --next)

**Step 2: Handle Branch**

Priority order:
1. `--branch` flag value
2. `branchName` from plans/prd.json (if present)
3. Stay on current branch

If a branch is determined:
1. Check: `git branch --list <branch-name>`
2. If exists: `git checkout <branch-name>`
3. If not: `git checkout -b <branch-name>`

If `--dry-run` also set, just report without switching.

**Step 3: Determine the Task**

If `--next` flag is present:
1. Read `plans/prd.json`
2. Find the first feature/task where `passes: false` and `skip` is not true, ordered by priority
3. Extract id, title, acceptance_criteria, plan_file, github_issue
4. If NO failing tasks remain, output: "All tasks in prd.json are complete! Nothing to do."

If task provided directly:
- Use the provided task description
- Check prd.json for matching task to get acceptance criteria

**Step 4: Build Rich Context**

Read and include:
1. Task details from prd.json
2. Plan file content if `plan_file` is specified
3. Last 20 lines of plans/progress.md
4. GitHub issue reference if present
5. plans/guardrails.md content

**Step 5: Handle --dry-run**

If `--dry-run` is present:
1. Output the full prompt that WOULD be used
2. Show which task would be picked
3. Show acceptance criteria
4. Show branch (if specified)
5. DO NOT create the state file
6. Stop here

**Step 6: Create State File**

Create `.claude/ralph-loop.local.md` with EXACT format:

```markdown
---
active: true
iteration: 0
max_iterations: [extracted or 50]
completion_promise: "[extracted or COMPLETE]"
mode: "[next or single]"
branch: "[branch name or empty]"
started_at: "[current ISO timestamp]"
---

## Current Task

**ID:** [task id]
**Title:** [task title]
**GitHub Issue:** #[number] (if present)

### Acceptance Criteria
- [ ] [criterion 1]
- [ ] [criterion 2]

### Plan File Content (if applicable)
[Content from plan_file if specified]

---

## Guardrails (Signs)

[Content from plans/guardrails.md]

---

## Context

### Recent Progress (last 20 lines of plans/progress.md)
[Include last 20 lines]

---

## Task

[Restate the task here for the stop hook to pick up]
```

**Step 7: Begin Working**

1. Read context files (progress.md, guardrails.md, prd.json, plan_file)
2. Start implementing the task
3. Run verification to check your work
4. Commit when tests pass (include `Fixes #N` if github_issue present)
5. When current task complete:
   - COMMIT changes
   - Update prd.json: `passes: true`, add `completed_at: "YYYY-MM-DD"`
   - Check remaining `passes: false` tasks
   - If more remain: end normally (stop hook continues)
   - If ALL pass: output `<promise>COMPLETE</promise>`

**IMPORTANT:** Only output the completion promise when ALL prd.json tasks pass.

### Verify Command

The stop hook runs verification automatically between iterations. Default:
```
python -m pytest && python -m ruff check src/
```
(overridden by `verifyCommand` in plans/prd.json)
</instruction>
