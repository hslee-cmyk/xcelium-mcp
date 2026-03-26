"""Async TCP client for communicating with mcp_bridge.tcl inside SimVision."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class TclResponse:
    """Parsed response from the Tcl bridge."""
    ok: bool
    body: str

    def raise_on_error(self) -> str:
        if not self.ok:
            raise TclError(self.body)
        return self.body


class TclError(Exception):
    """Error returned by Tcl command evaluation."""


class TclBridge:
    """Asyncio TCP client for the SimVision Tcl bridge.

    Commands are serialized with an asyncio.Lock because the Tcl
    interpreter inside SimVision is single-threaded.
    """

    def __init__(self, host: str = "localhost", port: int = 9876,
                 timeout: float = 30.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> str:
        """Open TCP connection and send a PING to verify."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        resp = await self.execute("__PING__")
        return resp

    async def disconnect(self) -> None:
        """Send __QUIT__ and close the connection."""
        if self._writer and not self._writer.is_closing():
            try:
                await self.execute_safe("__QUIT__")
            except Exception:
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def execute(self, command: str, timeout: float | None = None) -> str:
        """Send a command and return the result body. Raises TclError on failure."""
        resp = await self.execute_safe(command, timeout=timeout)
        return resp.raise_on_error()

    async def execute_safe(self, command: str, timeout: float | None = None) -> TclResponse:
        """Send a command and return a TclResponse (does not raise on Tcl errors)."""
        if not self.connected:
            raise ConnectionError("Not connected to SimVision bridge")

        effective_timeout = timeout if timeout is not None else self.timeout
        async with self._lock:
            return await asyncio.wait_for(
                self._send_and_recv(command),
                timeout=effective_timeout,
            )

    async def _send_and_recv(self, command: str) -> TclResponse:
        """Low-level send/receive. Must be called under self._lock."""
        assert self._writer is not None
        assert self._reader is not None

        # Send command
        self._writer.write((command + "\n").encode("utf-8"))
        await self._writer.drain()

        # Read status line: "OK <len>" or "ERROR <len>"
        status_line = await self._reader.readline()
        if not status_line:
            raise ConnectionError("Connection closed by bridge")
        status_line = status_line.decode("utf-8").strip()

        parts = status_line.split(None, 1)
        if len(parts) < 2:
            raise ConnectionError(f"Malformed status line: {status_line!r}")

        status = parts[0]
        ok = status == "OK"

        # Read body lines until <<<END>>>
        body_lines: list[str] = []
        while True:
            line = await self._reader.readline()
            if not line:
                raise ConnectionError("Connection closed while reading body")
            decoded = line.decode("utf-8").rstrip("\n")
            if decoded == "<<<END>>>":
                break
            body_lines.append(decoded)

        body = "\n".join(body_lines)
        return TclResponse(ok=ok, body=body)

    async def screenshot(self, path: str = "") -> str:
        """Request a waveform screenshot. Returns the file path."""
        cmd = f"__SCREENSHOT__ {path}" if path else "__SCREENSHOT__"
        return await self.execute(cmd)
