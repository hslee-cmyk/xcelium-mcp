"""Unit tests for idle_culler.py's pure /proc-text parsing logic.

These test only the string-parsing helpers, which need no real /proc
filesystem and therefore run on any platform (Windows dev box included).
The pid-based lookups (find_supervisor_pid/find_worker_pids/has_established_tcp/
process_age_seconds) are thin /proc-reading wrappers around this logic and are
Linux-only — verified on cloud0 per design.md §8 Test Plan, not here.
"""
from __future__ import annotations

from xcelium_mcp.idle_culler import (
    parse_stat_starttime,
    parse_tcp_table_established_inodes,
    parse_uptime_seconds,
)


class TestParseStatStarttime:
    def test_simple_comm(self) -> None:
        stat = (
            "12345 (python3) S 1 12345 12345 0 -1 4194304 100 0 0 0 "
            "50 10 0 0 20 0 5 0 987654 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        assert parse_stat_starttime(stat) == 987654

    def test_comm_with_parens_and_spaces(self) -> None:
        """comm can itself contain '(' ')' and spaces (e.g. a renamed process) —
        must split on the *last* ')' in the line, not the first."""
        stat = (
            "12345 (my (weird) proc) S 1 12345 12345 0 -1 4194304 100 0 0 0 "
            "50 10 0 0 20 0 5 0 42 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
        )
        assert parse_stat_starttime(stat) == 42


class TestParseUptimeSeconds:
    def test_typical_line(self) -> None:
        assert parse_uptime_seconds("123456.78 987654.32\n") == 123456.78


class TestParseTcpTableEstablishedInodes:
    _HEADER = (
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
        "retrnsmt   uid  timeout inode\n"
    )

    def test_established_row_included(self) -> None:
        # state=01 (ESTABLISHED), inode=99887
        row = (
            "   0: 0100007F:270F 0100007F:9876 01 00000000:00000000 "
            "00:00000000 00000000  1000        0 99887 1 0000000000000000 20 0 0 10 -1\n"
        )
        inodes = parse_tcp_table_established_inodes(self._HEADER + row)
        assert inodes == {99887}

    def test_listen_row_excluded(self) -> None:
        # state=0A (LISTEN) — must not be counted as an active bridge connection
        row = (
            "   0: 00000000:2694 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000  1000        0 11111 1 0000000000000000 20 0 0 10 -1\n"
        )
        inodes = parse_tcp_table_established_inodes(self._HEADER + row)
        assert inodes == set()

    def test_no_rows(self) -> None:
        assert parse_tcp_table_established_inodes(self._HEADER) == set()

    def test_multiple_established_rows(self) -> None:
        rows = "".join(
            f"   {i}: 0100007F:270F 0100007F:9876 01 00000000:00000000 "
            f"00:00000000 00000000  1000        0 {inode} 1 0000000000000000 20 0 0 10 -1\n"
            for i, inode in enumerate((111, 222, 333))
        )
        assert parse_tcp_table_established_inodes(self._HEADER + rows) == {111, 222, 333}
