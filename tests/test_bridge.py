"""Unit tests for TclBridge using a mock Tcl socket server."""

from __future__ import annotations

import asyncio

import pytest

from xcelium_mcp.tcl_bridge import TclBridge, TclError


class MockTclServer:
    """Minimal asyncio TCP server that mimics mcp_bridge.tcl protocol."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None
        self._handlers: dict[str, str] = {}
        self._error_commands: set[str] = set()

    def set_response(self, command: str, response: str, is_error: bool = False):
        self._handlers[command] = response
        if is_error:
            self._error_commands.add(command)
        else:
            self._error_commands.discard(command)

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port,
        )
        # Retrieve the actual port (useful when port=0)
        addr = self._server.sockets[0].getsockname()
        self.port = addr[1]

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                cmd = line.decode("utf-8").strip()
                if not cmd:
                    continue

                # Meta commands
                if cmd == "__PING__":
                    self._send(writer, "OK", "pong")
                elif cmd == "__QUIT__":
                    self._send(writer, "OK", "bye")
                    break
                elif cmd in self._handlers:
                    status = "ERROR" if cmd in self._error_commands else "OK"
                    self._send(writer, status, self._handlers[cmd])
                else:
                    # Default: echo the command back
                    self._send(writer, "OK", cmd)

                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _send(writer: asyncio.StreamWriter, status: str, body: str):
        msg = f"{status} {len(body)}\n{body}\n<<<END>>>\n"
        writer.write(msg.encode("utf-8"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
async def mock_server():
    server = MockTclServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def bridge(mock_server: MockTclServer):
    b = TclBridge(host="127.0.0.1", port=mock_server.port, timeout=5.0)
    await b.connect()
    yield b
    await b.disconnect()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestConnection:
    async def test_connect_and_ping(self, mock_server: MockTclServer):
        b = TclBridge(host="127.0.0.1", port=mock_server.port, timeout=5.0)
        result = await b.connect()
        assert result == "pong"
        assert b.connected
        await b.disconnect()

    async def test_disconnect(self, bridge: TclBridge):
        assert bridge.connected
        await bridge.disconnect()
        assert not bridge.connected

    async def test_connect_refused(self):
        b = TclBridge(host="127.0.0.1", port=1, timeout=1.0)
        with pytest.raises((ConnectionRefusedError, OSError, TimeoutError)):
            await b.connect()


class TestCommandExecution:
    async def test_execute_returns_result(self, bridge: TclBridge,
                                          mock_server: MockTclServer):
        mock_server.set_response("where", "100ns : /tb/dut")
        result = await bridge.execute("where")
        assert result == "100ns : /tb/dut"

    async def test_execute_error_raises(self, bridge: TclBridge,
                                        mock_server: MockTclServer):
        mock_server.set_response("bad_cmd", "invalid command name", is_error=True)
        with pytest.raises(TclError, match="invalid command name"):
            await bridge.execute("bad_cmd")

    async def test_execute_safe_returns_response(self, bridge: TclBridge,
                                                  mock_server: MockTclServer):
        mock_server.set_response("bad_cmd", "error msg", is_error=True)
        resp = await bridge.execute_safe("bad_cmd")
        assert not resp.ok
        assert resp.body == "error msg"

    async def test_echo_default(self, bridge: TclBridge):
        """Commands not explicitly set echo back."""
        result = await bridge.execute("puts hello")
        assert result == "puts hello"


class TestTimeout:
    async def test_execute_not_connected(self):
        b = TclBridge(host="127.0.0.1", port=1, timeout=1.0)
        with pytest.raises(ConnectionError):
            await b.execute("where")


class TestConcurrency:
    async def test_serialized_commands(self, bridge: TclBridge,
                                       mock_server: MockTclServer):
        """Multiple concurrent calls should not interleave."""
        mock_server.set_response("cmd1", "result1")
        mock_server.set_response("cmd2", "result2")

        r1, r2 = await asyncio.gather(
            bridge.execute("cmd1"),
            bridge.execute("cmd2"),
        )
        assert {r1, r2} == {"result1", "result2"}
