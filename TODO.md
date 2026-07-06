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
