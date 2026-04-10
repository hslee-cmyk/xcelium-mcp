#!/bin/bash
# Ralph Wiggum Stop Hook
# Intercepts exit, runs verification, and re-prompts if loop is active
#
# Default VERIFY_COMMAND is overridden by plans/prd.json `verifyCommand` if present.

set -euo pipefail

# ============================================
# CUSTOMIZATION
# ============================================

# Default verification command (overridden by prd.json verifyCommand if present)
VERIFY_COMMAND="python -m pytest"

# Context files location (relative to project root)
PROGRESS_FILE="plans/progress.md"
PRD_FILE="plans/prd.json"
GUARDRAILS_FILE="plans/guardrails.md"

# ============================================
# Core Logic
# ============================================

# State file location
RALPH_STATE_FILE=".claude/ralph-loop.local.md"

# Read hook input from stdin
HOOK_INPUT=$(cat)

# If no state file, allow normal exit
if [ ! -f "$RALPH_STATE_FILE" ]; then
  exit 0
fi

# Parse state file (YAML frontmatter)
ACTIVE=$(grep "^active:" "$RALPH_STATE_FILE" | cut -d' ' -f2 || echo "false")
ITERATION=$(grep "^iteration:" "$RALPH_STATE_FILE" | cut -d' ' -f2 || echo "0")
MAX_ITERATIONS=$(grep "^max_iterations:" "$RALPH_STATE_FILE" | cut -d' ' -f2 || echo "50")
COMPLETION_PROMISE=$(grep "^completion_promise:" "$RALPH_STATE_FILE" | cut -d' ' -f2- | tr -d '"' || echo "COMPLETE")

# Validate numeric fields
if ! [[ "$ITERATION" =~ ^[0-9]+$ ]]; then
  ITERATION=0
fi
if ! [[ "$MAX_ITERATIONS" =~ ^[0-9]+$ ]]; then
  MAX_ITERATIONS=50
fi

# If not active, allow exit
if [ "$ACTIVE" != "true" ]; then
  exit 0
fi

# Override verify command from prd.json if verifyCommand field exists
if [ -f "$PRD_FILE" ]; then
  PRD_VERIFY=$(jq -r '.verifyCommand // empty' "$PRD_FILE" 2>/dev/null || echo "")
  if [ -n "$PRD_VERIFY" ]; then
    VERIFY_COMMAND="$PRD_VERIFY"
  fi
fi

# Check if we've hit max iterations
NEXT_ITERATION=$((ITERATION + 1))
if [ "$NEXT_ITERATION" -gt "$MAX_ITERATIONS" ]; then
  echo "Warning: Max iterations ($MAX_ITERATIONS) reached. Stopping loop." >&2
  rm -f "$RALPH_STATE_FILE"
  exit 0
fi

# Check for completion promise
LAST_OUTPUT=$(echo "$HOOK_INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")

if [ -z "$LAST_OUTPUT" ]; then
  TRANSCRIPT_PATH=$(echo "$HOOK_INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
  if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
    LAST_LINE=$(grep '"role":"assistant"' "$TRANSCRIPT_PATH" | tail -1 || echo "")
    if [ -n "$LAST_LINE" ]; then
      LAST_OUTPUT=$(echo "$LAST_LINE" | jq -r '
        .message.content |
        map(select(.type == "text")) |
        map(.text) |
        join("\n")
      ' 2>/dev/null || echo "")
    fi
  fi
fi

if [ -n "$LAST_OUTPUT" ]; then
  PROMISE_TEXT=$(echo "$LAST_OUTPUT" | perl -0777 -pe 's/.*?<promise>(.*?)<\/promise>.*/\1/s; s/^\s+|\s+$//g; s/\s+/ /g' 2>/dev/null || echo "")

  if [ -n "$PROMISE_TEXT" ] && [ "$PROMISE_TEXT" = "$COMPLETION_PROMISE" ]; then
    echo "Completion promise detected: $PROMISE_TEXT" >&2
    rm -f "$RALPH_STATE_FILE"
    exit 0
  fi
fi

# Update iteration count
TEMP_FILE=$(mktemp)
sed "s/^iteration: .*/iteration: $NEXT_ITERATION/" "$RALPH_STATE_FILE" > "$TEMP_FILE"
mv "$TEMP_FILE" "$RALPH_STATE_FILE"

# Run verification
echo "" >&2
echo "=================================================================" >&2
echo "RALPH LOOP - Iteration $NEXT_ITERATION of $MAX_ITERATIONS" >&2
echo "=================================================================" >&2
echo "" >&2
echo "Running verification ($VERIFY_COMMAND)..." >&2
VERIFY_OUTPUT=$(eval "$VERIFY_COMMAND" 2>&1) || true
VERIFY_EXIT_CODE=$?

# Get task from state file
TASK=$(awk '/^## Task$/,0' "$RALPH_STATE_FILE" | tail -n +2)

# Read guardrails if they exist
GUARDRAILS_CONTEXT=""
if [ -f "$GUARDRAILS_FILE" ]; then
  GUARDRAILS_CONTEXT=$(cat "$GUARDRAILS_FILE" 2>/dev/null || echo "")
fi

# Build continuation prompt
if [ $VERIFY_EXIT_CODE -eq 0 ]; then
  echo "Verification passed!" >&2
  PROMPT="# Ralph Loop - Iteration $NEXT_ITERATION of $MAX_ITERATIONS

## Verification Status
**PASSED** - All tests and lint checks passed.

## Guardrails (Signs)
$GUARDRAILS_CONTEXT

## Your Task
$TASK

## Instructions
1. Review what was accomplished in the previous iteration
2. Check $PROGRESS_FILE for context
3. Follow the guardrails above
4. Continue working on the task
5. If genuinely complete, re-read prd.json to confirm ALL tasks pass, then output:
   \`<promise>$COMPLETION_PROMISE</promise>\`
6. Otherwise, make more progress and end normally

**Remember:** Only output the completion promise when ALL tasks in prd.json are complete."
else
  echo "Verification FAILED (exit code: $VERIFY_EXIT_CODE)" >&2
  PROMPT="# Ralph Loop - Iteration $NEXT_ITERATION of $MAX_ITERATIONS

## Verification Status
**FAILED** - Fix these issues before continuing:

\`\`\`
$VERIFY_OUTPUT
\`\`\`

## Guardrails (Signs)
$GUARDRAILS_CONTEXT

## Your Task
$TASK

## Instructions
1. Fix the verification errors above
2. Run \`$VERIFY_COMMAND\` to check your fixes
3. Follow the guardrails above
4. Once verification passes, continue with the task
5. Do NOT output the completion promise until verification passes AND all tasks in prd.json are complete

**Priority:** Fix verification errors first, then continue with the task."
fi

SYSTEM_MSG="Ralph loop iteration $NEXT_ITERATION/$MAX_ITERATIONS. Verification: $([ $VERIFY_EXIT_CODE -eq 0 ] && echo 'PASSED' || echo 'FAILED')"

jq -n \
  --arg prompt "$PROMPT" \
  --arg msg "$SYSTEM_MSG" \
  '{
    "decision": "block",
    "reason": $prompt,
    "systemMessage": $msg
  }'
