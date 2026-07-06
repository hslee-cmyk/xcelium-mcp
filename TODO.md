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
