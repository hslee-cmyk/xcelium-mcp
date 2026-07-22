#!/usr/bin/env python3
"""PostToolUse hook: after a compound tool call, suggest the next /sim step.

Plan §6 Hook 자동화(Phase D) — matches sim_run_and_check/sim_analyze_waveform/
sim_regression_summary (Phase 2 module-2 MCP tools, src/xcelium_mcp/tools/
compound.py) and reads the CompoundResult's `status:` line from
tool_output.text to look up a next-step suggestion (same mapping as
SKILL.md's "Next-Skill 자동 제안" table).

**Language note (Phase D revisit, per Plan's own flagged note)**: Plan §6.2
originally sketched this hook in Node.js, matching bkit's own internal hook
convention (bkit ships as a distributed plugin needing guaranteed cross-machine
portability, so it bundles its own Node.js runtime dependency). That reasoning
doesn't apply here — this is a single-user personal skill, and this user's own
actual hooks (~/.claude/hooks/guard-*.py) are Python. Implemented in Python
for consistency, per Plan §6.2's own "revisit at Phase D" flag.

**I/O contract** (verified against the current Claude Code hooks spec, not
assumed from Plan's illustrative snippet): stdin JSON has `tool_name`,
`tool_input`, `tool_output` (`{"type": "text", "text": "..."}` for MCP tools).
Output: exit 0 + `{"hookSpecificOutput": {"hookEventName": "PostToolUse",
"additionalContext": "..."}}` on stdout. PostToolUse cannot block (the tool
already ran) — this hook only ever adds informational context or does
nothing; it never errors loudly. Fails open (exit 0, no output) on any
parse problem.

**F-189 stdin encoding note**: see `sim_prompt_detect.py`'s matching
docstring/`_read_stdin_json()` -- same fix applied here for consistency,
even though this hook's own `_STATUS_RE` match target ("status: PASS" etc.)
is ASCII-only and not itself at risk; `tool_output.text` in general (e.g.
future log content) is not guaranteed to be.
"""
from __future__ import annotations

import json
import re
import sys


def _read_stdin_json() -> dict:
    """Parse stdin as UTF-8 JSON regardless of the platform's stdin encoding.
    See sim_prompt_detect.py's identical helper for the full rationale (F-189).
    """
    raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)

_TOOL_NEXT_STEP = {
    "sim_run_and_check": {
        "PASS": "/sim run --regression 제안(전체 regression)",
        "FAIL": "/sim analyze {test}로 진행",
        "ERROR": "실행 자체 실패 — EDA 환경변수/SSH 연결 확인 필요",
    },
    "sim_analyze_waveform": {
        "PASS": "원인 특정됐으면 /sim debug {test}, 아니면 --signals로 추가 분석",
        "ERROR": "CSV 추출 실패 — dump_path/simvisdbutil 확인 필요",
    },
    "sim_regression_summary": {
        "PASS": "regression 전체 통과 — 완료",
        "FAIL": "/sim debug {failing_test}로 개별 원인 추적",
        "PARTIAL": "/sim analyze {failing_test}로 실패 테스트부터 분석",
        "ERROR": "regression 실행 자체 실패 — 연결/환경 확인 필요",
    },
}

_TOOL_NAME_RE = re.compile(r"mcp__xcelium-mcp__(sim_\w+)$")
_STATUS_RE = re.compile(r"^status:\s*(\w+)", re.MULTILINE)


def main() -> int:
    try:
        data = _read_stdin_json()
    except Exception:
        return 0

    tool_name = data.get("tool_name", "")
    m = _TOOL_NAME_RE.search(tool_name)
    if not m or m.group(1) not in _TOOL_NEXT_STEP:
        return 0
    short_name = m.group(1)

    tool_output = data.get("tool_output")
    text = tool_output.get("text", "") if isinstance(tool_output, dict) else ""
    status_match = _STATUS_RE.search(text)
    if not status_match:
        return 0

    suggestion = _TOOL_NEXT_STEP[short_name].get(status_match.group(1))
    if not suggestion:
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"[xcelium-sim] {short_name} -> {status_match.group(1)}. "
                                  f"다음 단계 제안: {suggestion}",
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
