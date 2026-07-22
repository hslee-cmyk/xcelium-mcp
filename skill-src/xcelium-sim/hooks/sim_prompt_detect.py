#!/usr/bin/env python3
"""UserPromptSubmit hook: surface pending /sim work when trigger keywords appear.

Plan §6 Hook 자동화(Phase D).

**Why this doesn't duplicate native Skill auto-triggering**: Claude Code
already loads SKILL.md via description-keyword matching on its own — this
hook does NOT re-implement that. Its only job is to inject *live state*
(`.ai/sim-state.json`) that SKILL.md's static text can never contain, e.g.
"TOP015 is waiting at fix-plan approval" — so Claude doesn't have to
separately run `/sim status` to discover unfinished work from a prior
session. This is the actual gap Plan §6.1 wanted a hook for (§6.1 explicitly
rejected a SessionStart hook for the same reason this hook stays cheap: "토큰
낭비" — so this hook bails out immediately, no file I/O at all, unless a
trigger keyword is actually present).

**Language/naming note**: Python + underscore filename, same rationale as
sim_post_compound.py's module docstring (Phase D revisit of Plan §6.2's
Node.js/hyphen sketch).

**I/O contract**: stdin JSON has `user_input`, `cwd` (per the current Claude
Code hooks spec). Output: exit 0 + `{"hookSpecificOutput": {"hookEventName":
"UserPromptSubmit", "additionalContext": "..."}}`, or nothing at all when
there's no trigger match or no pending state. Never blocks (exit 2 is
available for that per spec, but this hook has no reason to reject a
prompt). Fails open on any parse problem.

**F-189 stdin encoding note**: `sys.stdin`'s default encoding follows the
platform locale, not UTF-8 -- on Windows this is often a legacy codepage
(e.g. cp949 for Korean Windows). `json.load(sys.stdin)` does NOT raise on
that mismatch; it silently decodes non-ASCII bytes into mojibake via
surrogateescape, so a Korean trigger keyword in `_TRIGGER_KEYWORDS` below
would then just never match -- no error, no output, indistinguishable from
"no trigger present". `_read_stdin_json()` reads the raw bytes and decodes
them as UTF-8 explicitly, sidestepping the platform locale entirely.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _read_stdin_json() -> dict:
    """Parse stdin as UTF-8 JSON regardless of the platform's stdin encoding.

    `sys.stdin.buffer` is the raw byte stream underneath the text wrapper --
    reading it directly and decoding as UTF-8 ourselves means the OS/locale
    default codec (e.g. cp949 on Windows) never gets a chance to mangle
    non-ASCII input. Falls back to `sys.stdin.read()` when there is no
    `.buffer` (e.g. tests that monkeypatch stdin with `io.StringIO`, which
    is already a decoded str -- nothing to re-decode there).
    """
    raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)

# Same keyword list as SKILL.md's frontmatter description trigger list —
# kept in sync manually (no shared source; SKILL.md is prose, this needs a
# literal list). If SKILL.md's trigger list changes, update this too.
_TRIGGER_KEYWORDS = (
    "xcelium", "simvision", "waveform", "fail 분석", "시뮬레이션", "simulation",
    "debugging", "디버깅", "csv", "checkpoint", "bisect", "regression",
    "dump_scopes", "dump_depth", "재기동", "supervisor",
    "연결 안 됨", "최신 코드 반영 안 됨", "mcp 응답 없음",
    "/sim ",
)

_NON_IDLE_PHASES = {"analyze", "debug", "fix-plan", "fix-design", "fix-implement", "fix-review"}


def _pending_summary(project_root: str) -> str:
    state_path = Path(project_root) / ".ai" / "sim-state.json"
    if not state_path.exists():
        return ""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    lines = [
        f"{test}: {entry.get('phase', 'idle')}"
        for test, entry in (state.get("tests") or {}).items()
        if entry.get("phase", "idle") in _NON_IDLE_PHASES
    ]
    if not lines:
        return ""
    return "미완료 /sim 작업 있음 -- " + ", ".join(lines) + " (`/sim status`로 상세 확인)"


def main() -> int:
    try:
        data = _read_stdin_json()
    except Exception:
        return 0

    user_input = (data.get("user_input") or "").lower()
    if not any(kw.lower() in user_input for kw in _TRIGGER_KEYWORDS):
        return 0

    summary = _pending_summary(data.get("cwd") or ".")
    if not summary:
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"[xcelium-sim] {summary}",
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
