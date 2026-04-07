"""Bridge state management — shared between server.py and sim_runner.py.

Replaces module-level globals _xmsim_bridge / _simvision_bridge.
Single instance created in server.py, passed to tools and sim_runner via DI.
"""
from __future__ import annotations

from xcelium_mcp.tcl_bridge import TclBridge


class BridgeManager:
    """Encapsulates xmsim/SimVision bridge state."""

    def __init__(self) -> None:
        self._xmsim: TclBridge | None = None
        self._simvision: TclBridge | None = None

    @property
    def xmsim(self) -> TclBridge:
        """Get connected xmsim bridge. Raises if not connected."""
        if self._xmsim is None or not self._xmsim.connected:
            raise ConnectionError(
                "Not connected to xmsim. Use connect_simulator or sim_bridge_run first."
            )
        return self._xmsim

    @property
    def simvision(self) -> TclBridge:
        """Get connected SimVision bridge. Raises if not connected."""
        if self._simvision is None or not self._simvision.connected:
            raise ConnectionError(
                "Not connected to SimVision. Use simvision_start first."
            )
        return self._simvision

    def get_bridge(self, target: str = "auto") -> TclBridge:
        """Get bridge by target. auto = xmsim first, then simvision."""
        if target == "xmsim":
            return self.xmsim
        elif target == "simvision":
            return self.simvision
        elif target == "auto":
            if self._xmsim and self._xmsim.connected:
                return self._xmsim
            if self._simvision and self._simvision.connected:
                return self._simvision
            raise ConnectionError("No simulator connected.")
        raise ValueError(f"Unknown target: {target}")

    def set_xmsim(self, bridge: TclBridge | None) -> None:
        self._xmsim = bridge

    def set_simvision(self, bridge: TclBridge | None) -> None:
        self._simvision = bridge

    @property
    def xmsim_raw(self) -> TclBridge | None:
        """Raw access without connection check (for disconnect/shutdown)."""
        return self._xmsim

    @property
    def simvision_raw(self) -> TclBridge | None:
        """Raw access without connection check (for disconnect/shutdown)."""
        return self._simvision
