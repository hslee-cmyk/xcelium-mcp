# TODO

Known limitations that are safe (fail to "no match", never a wrong match) and
low-priority, deliberately left as-is for now.

## F-175 — TB source discovery (test_name → file) known gaps

Both found while reviewing F-175's `parse_test_discovery_output()` /
`resolve_tb_source_file()` (2026-07-06). Neither produces a *wrong* mapping —
they just fail to find a match, which `build_tb_provenance()` already
handles gracefully (returns `None`, tools skip the provenance section).

- **Multi-line class declarations**: if a UVM test's `class Name` and
  `extends uvm_test` are split across lines (e.g.
  ```systemverilog
  class VENEZIA_TOP015_i2c_8bit_offset_test
      extends uvm_test;
  ```
  ), the `grep -rn 'extends uvm_test'` line only contains `extends uvm_test;`
  with no class name on it, so the test is never captured — it won't even
  appear in `cached_tests`. This is a **pre-existing limitation of the
  original test_discovery mechanism** (predates F-175), not something F-175
  introduced.
- **Duplicate class names across files**: if the same test class name is
  (incorrectly) defined in two different files, `parse_test_discovery_output`
  keeps whichever file grep visits first (`dict.setdefault`) — silently, with
  no warning. Very unlikely in practice (would itself be a TB authoring bug),
  but worth knowing if provenance ever looks like it's pointing at the wrong
  file for a test.

Revisit if either of these actually bites in practice; low priority since
current behavior degrades safely rather than lying.

## F-175 — TB source provenance: dependency-scan scope (2026-07-06, hash-timing follow-up)

Found while discussing "hash 업데이트하는 부분에 추가로 고려할 사항" after the
sim_regression timing fix. `build_tb_provenance()` now covers the test's own
file plus its *direct* `` `include``/`import` references (one level deep) —
this closed the original single-file gap. One smaller thing remains:

- **Not recursive**: if a directly-`include`d file itself includes something
  else, that second-hop file is not scanned/hashed. A change two hops away
  from the test file (e.g. an interface file included by a sequence file the
  test includes) won't be detected. Same "safe absence, not a wrong match"
  character as the other items above.
- **Duplicate basenames for `include`d files**: `_find_file_by_basename` picks
  the first match `find` returns if two files share the same basename in
  different directories — same class of ambiguity as "duplicate class names"
  above, just for included files instead of test files.

**Resolved (2026-07-06, same day)** — dependency-scan caching: `find_dependency_files()`
(the actual `find`/`grep -rl` scan) now only runs at discovery/cache-miss
time, alongside `cached_test_files` — results are stored in a new
`test_discovery.cached_dependency_files: {test_name: {"scanned_primary_sha256", "deps"}}`
field. `build_tb_provenance()` never calls `find_dependency_files()`
unconditionally on the per-run hot path; it only reads
`cached_dependency_files` (via `resolve_cached_dependency_files()`) and
always re-hashes file *contents* fresh.

**Resolved (2026-07-06, same day, follow-up)** — self-healing staleness:
initially the cache had no way to notice when a test's OWN file was edited
to add/remove an `include`/import line — the cached dependency *locations*
would silently go stale. Fixed by recording the primary file's sha256 at
scan time (`scanned_primary_sha256`) and comparing it, on every
`build_tb_provenance()` call, against the CURRENT primary hash (already
computed for the "files" entry anyway — free comparison). A mismatch (or no
cache entry at all — same backward-compat path as before) triggers exactly
one live re-scan via `resolve_cached_dependency_files()`, which then persists
the refreshed entry. So the find/grep cost is paid only on the run right
after the test file actually changed, never on every run, and never stays
silently stale indefinitely.

Low priority remainder — the single-file gap, the caching cost, and the
staleness blind spot were the three significant issues and all three are now
fixed; the two bullets above (non-recursive scan, duplicate basenames) are
refinements on top.

## `parse_existing_job()` — silent full re-run if completion markers aren't found (2026-07-07)

Found while investigating `xcelium-mcp-session-state-reattach` (F-D/F-E) — specifically
the "SSH disconnects, batch simulation finishes on its own while disconnected, client
reconnects and re-issues `sim_batch_run`" scenario (`batch_runner.py:191-227`).

When the resumed job's PID is dead ("finished while disconnected"), the code greps the
saved log for completion markers:

```python
result = await shell_run(
    f"(grep -E 'PASS|FAIL|Errors:|\\$finish|COMPLETE' {saved_log} || true) | tail -30"
)
if result.strip():
    ...
    return f"(Completed while disconnected)\n{result}"
```

If **none** of those keywords appear in the log (non-standard TB harness output format,
or an unusual abnormal exit that doesn't print any of them), `result.strip()` is empty,
the `if` is skipped, `job_file` is deleted anyway, and `parse_existing_job()` returns
`None` — `run_batch_single()` then falls through to **launching the test again from
scratch**, silently. Not a wrong-*result* class of bug (the re-run will itself complete
correctly and report accurately) but it does waste a full simulation's worth of compute
time without telling the caller why a re-run happened.

Low priority — this repo's actual TB harnesses do emit one of these markers in practice
(that's how the keyword list was derived), and `run_batch_single`/`run_batch_regression`
are mature, already-hardened modules (F-174's completion-keyword false-positive fix, P6-1/
P6-2/P6-5 polling refinements) that this project's Design work has deliberately chosen not
to touch for small, unrelated fixes (see `xcelium-mcp-session-state-reattach.design.md`
§2 Checkpoint 3 — Option C explicitly avoids `batch_runner.py` changes to keep its
regression risk low). Revisit only if a real TB harness is found whose log genuinely
lacks all five markers on completion.

## `run_batch_regression()` — no structured per-test PASS/FAIL, only aggregate summary text (2026-07-22)

Found while implementing `compound.py`'s `regression_summary()` (`xcelium-mcp-debug-workflow-v2`,
Do phase module-1/Phase A) — needed to pinpoint exactly which tests in a regression failed, in
order to extract CSV only for those tests (`csv_on_fail` param).

`run_batch_regression()` returns `(log_str, dump_stats, tb_provenance)`, where `log_str` comes
from `classify_regression_results()` — a human-readable aggregate string, e.g.:

```
1/2 verdict tests PASS (5 checks passed, 1 failed)

Log (/tmp/regression_....log):
=== T1 ===
...
=== T2 ===
...
```

The per-test classification (pass/fail/complete/error — computed internally in
`classify_regression_results()`'s loop over `test_list`) is discarded after being folded into
the aggregate counts; no `{test_name: "pass"|"fail"|...}` dict is exposed to the caller.
The embedded `Log (...): {details}` portion is also truncated to the first 4000 characters
(`raw[:4000]`), so even text-parsing the returned string to recover per-test verdicts would be
unreliable for larger regressions.

**Current workaround** (`compound.py::regression_summary`, documented in its own docstring):
when `csv_on_fail=True` and the overall regression status isn't `"PASS"`, CSV is extracted for
**every** test in `test_list` rather than only the failing ones — imprecise but safe (never
mis-attributes a CSV to the wrong test, just does more extraction work than strictly needed).

**Why not fixed now**: exposing structured per-test results means changing
`run_batch_regression()`/`classify_regression_results()` themselves, which
`xcelium-mcp-debug-workflow-v2.plan.md` §3.4 explicitly avoids — reworking an
already-verified execution path for one new consumer's convenience risks a new bug surface
disproportionate to the payoff (Plan RISK: "이미 검증된 경로와 별개로 새 버그 표면이 생김").

**Suggested fix** (for whoever revisits): have `classify_regression_results()` additionally
return (or have `run_batch_regression()` additionally return) a `dict[str, str]` of
`{test_name: "pass"|"fail"|"complete"|"error"}` alongside the existing formatted string —
purely additive, so existing callers and the current 3-tuple return shape are unaffected.
`compound.py::regression_summary` could then extract CSV precisely for the tests that actually
failed instead of the whole `test_list`.

Low priority — current behavior is safe (over-fetches rather than mis-attributes), and no
consumer other than the new `csv_on_fail` path needs per-test precision yet.
