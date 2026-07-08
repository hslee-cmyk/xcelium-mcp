"""Tests for stdio_forward.py's stdin->socket relay (F-180).

stdin.read(n) (io.BufferedReader.read) blocks until n bytes accumulate OR
EOF — but a real MCP client keeps stdin open indefinitely and sends
messages far smaller than _CHUNK_SIZE (64KB), so with the old code the very
first message never got forwarded while the connection stayed open. These
tests specifically keep the write end of a pipe open (no EOF) while
asserting the chunk still arrives promptly — the exact case the T-8 manual
re-verification originally missed by only ever testing "send one message,
then EOF immediately" (see design.md §8 T-8 / prd.json F-180 notes).

Wiring: a real os.pipe() stands in for stdin (its read end has the same
io.BufferedReader.read1() semantics as sys.stdin.buffer), and
socket.socketpair() stands in for the unix socket to the supervisor —
sock_a is what _pump_stdin_to_sock() writes into, sock_b is the "worker"
side we assert on.
"""
from __future__ import annotations

import os
import socket
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from xcelium_mcp.stdio_forward import _pump_stdin_to_sock

_RECV_TIMEOUT = 2.0


def _open_stdin_pipe():
    """Returns (fake_stdin_buffer, write_fd). Caller writes bytes via
    os.write(write_fd, ...) and must os.close(write_fd) to signal EOF.

    Default buffering (not buffering=0) is required — that yields a real
    io.BufferedReader, matching sys.stdin.buffer's type and giving it the
    read1() method the fix under test relies on (io.FileIO, the buffering=0
    result, has no read1()).
    """
    read_fd, write_fd = os.pipe()
    return os.fdopen(read_fd, "rb"), write_fd


@pytest.fixture
def sock_pair():
    sock_a, sock_b = socket.socketpair()
    sock_b.settimeout(_RECV_TIMEOUT)
    yield sock_a, sock_b
    for s in (sock_a, sock_b):
        try:
            s.close()
        except OSError:
            pass


class TestPumpStdinToSock:
    def test_single_small_chunk_forwarded_without_eof(self, sock_pair) -> None:
        """The exact F-180 repro: a small chunk must arrive promptly even
        though stdin is left open (no EOF) — with the old stdin.read(n) this
        would block forever waiting for 64KB or EOF."""
        sock_a, sock_b = sock_pair
        stdin_buf, write_fd = _open_stdin_pipe()
        try:
            with patch("xcelium_mcp.stdio_forward.sys.stdin", SimpleNamespace(buffer=stdin_buf)):
                thread = threading.Thread(target=_pump_stdin_to_sock, args=(sock_a,), daemon=True)
                thread.start()

                os.write(write_fd, b'{"jsonrpc":"2.0","method":"initialize"}')

                received = sock_b.recv(4096)
                assert received == b'{"jsonrpc":"2.0","method":"initialize"}'
        finally:
            os.close(write_fd)
            thread.join(timeout=5)

    def test_multiple_small_chunks_forwarded_in_order_without_eof(self, sock_pair) -> None:
        sock_a, sock_b = sock_pair
        stdin_buf, write_fd = _open_stdin_pipe()
        try:
            with patch("xcelium_mcp.stdio_forward.sys.stdin", SimpleNamespace(buffer=stdin_buf)):
                thread = threading.Thread(target=_pump_stdin_to_sock, args=(sock_a,), daemon=True)
                thread.start()

                for i in range(5):
                    payload = f"msg-{i}".encode()
                    os.write(write_fd, payload)
                    assert sock_b.recv(4096) == payload
        finally:
            os.close(write_fd)
            thread.join(timeout=5)

    def test_large_payload_delivered_completely(self, sock_pair) -> None:
        """A payload well past _CHUNK_SIZE (64KB) must still arrive whole —
        read1() returning less than requested per call is fine because the
        outer while loop keeps reading until EOF."""
        sock_a, sock_b = sock_pair
        stdin_buf, write_fd = _open_stdin_pipe()
        payload = os.urandom(150_000)
        try:
            with patch("xcelium_mcp.stdio_forward.sys.stdin", SimpleNamespace(buffer=stdin_buf)):
                thread = threading.Thread(target=_pump_stdin_to_sock, args=(sock_a,), daemon=True)
                thread.start()

                def _writer():
                    os.write(write_fd, payload)
                    os.close(write_fd)

                writer_thread = threading.Thread(target=_writer, daemon=True)
                writer_thread.start()

                received = b""
                while len(received) < len(payload):
                    chunk = sock_b.recv(65536)
                    assert chunk, "socket closed before full payload received"
                    received += chunk
                assert received == payload
                writer_thread.join(timeout=5)
        finally:
            thread.join(timeout=5)

    def test_stdin_eof_propagates_shutdown_wr(self, sock_pair) -> None:
        """Closing stdin (EOF) must still shut down the socket's write side
        so the peer (worker) observes EOF too — unchanged existing behavior."""
        sock_a, sock_b = sock_pair
        stdin_buf, write_fd = _open_stdin_pipe()
        with patch("xcelium_mcp.stdio_forward.sys.stdin", SimpleNamespace(buffer=stdin_buf)):
            thread = threading.Thread(target=_pump_stdin_to_sock, args=(sock_a,), daemon=True)
            thread.start()
            os.close(write_fd)  # immediate EOF, nothing written
            thread.join(timeout=5)
            assert not thread.is_alive()

        # SHUT_WR on sock_a means sock_b now sees EOF (empty recv).
        assert sock_b.recv(1) == b""
