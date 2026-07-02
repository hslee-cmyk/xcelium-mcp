"""Unit tests for tools.batch._validate_run_params (F-164).

F-164: sim_batch_run and sim_regression used to duplicate the
dump_depth/sdf_corner/dump_scopes validation logic verbatim (down to
suffixed variable names to avoid collisions). Extracted into a single
shared function so both tools stay in sync.
"""
from __future__ import annotations

from xcelium_mcp.tools.batch import _validate_run_params


class TestValidateRunParams:
    def test_valid_params_return_none(self) -> None:
        assert _validate_run_params("boundary", "", "max", None) is None
        assert _validate_run_params("", "", "max", None) is None
        assert _validate_run_params("all", "corner.sdf", "min", None) is None

    def test_invalid_dump_depth(self) -> None:
        err = _validate_run_params("bogus", "", "max", None)
        assert err is not None
        assert "dump_depth" in err

    def test_invalid_sdf_corner_only_checked_when_sdf_file_set(self) -> None:
        assert _validate_run_params("", "", "bogus", None) is None
        err = _validate_run_params("", "corner.sdf", "bogus", None)
        assert err is not None
        assert "sdf_corner" in err

    def test_valid_dump_scopes(self) -> None:
        assert _validate_run_params("", "", "max", {"tb.dut": "all", "tb.*": "skip"}) is None

    def test_invalid_dump_scopes_key(self) -> None:
        err = _validate_run_params("", "", "max", {"tb/dut": "all"})
        assert err is not None
        assert "dump_scopes key" in err

    def test_invalid_dump_scopes_value(self) -> None:
        err = _validate_run_params("", "", "max", {"tb.dut": "everything"})
        assert err is not None
        assert "dump_scopes value" in err
