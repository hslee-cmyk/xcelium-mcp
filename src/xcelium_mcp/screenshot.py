"""PostScript → PNG screenshot conversion for SimVision waveform captures.

Tool paths are resolved from mcp_sim_config.json ``external_tools`` section
(populated by sim_discover). Falls back to shutil.which() if config unavailable.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path


def _gs_from_config(config: dict | None) -> str | None:
    """Extract ghostscript path from config external_tools."""
    if not config:
        return None
    ext = config.get("external_tools", {})
    return ext.get("gs") or ext.get("gswin64c") or ext.get("gswin32c") or None


def _convert_from_config(config: dict | None) -> str | None:
    """Extract ImageMagick path from config external_tools."""
    if not config:
        return None
    ext = config.get("external_tools", {})
    return ext.get("convert") or ext.get("magick") or None


async def ps_to_png(ps_path: str, png_path: str | None = None,
                    resolution: int = 150,
                    config: dict | None = None) -> bytes:
    """Convert a PostScript file to PNG and return the PNG bytes.

    Tries ghostscript first, falls back to ImageMagick convert.
    If config is provided, tool paths are resolved from external_tools section.
    """
    if png_path is None:
        fd, png_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        cleanup = True
    else:
        cleanup = False

    try:
        await _convert_gs(ps_path, png_path, resolution, config)
    except (FileNotFoundError, RuntimeError):
        await _convert_imagemagick(ps_path, png_path, resolution, config)

    png_bytes = Path(png_path).read_bytes()
    if cleanup:
        os.unlink(png_path)
    return png_bytes


async def _convert_gs(ps_path: str, png_path: str, resolution: int,
                      config: dict | None = None):
    """Convert using ghostscript."""
    gs_cmd = _gs_from_config(config) or _find_gs()
    if not gs_cmd:
        raise FileNotFoundError("ghostscript not found")

    proc = await asyncio.create_subprocess_exec(
        gs_cmd,
        "-dNOPAUSE", "-dBATCH", "-dSAFER",
        f"-r{resolution}",
        "-sDEVICE=png16m",
        f"-sOutputFile={png_path}",
        ps_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ghostscript failed: {stderr.decode()}")


async def _convert_imagemagick(ps_path: str, png_path: str, resolution: int,
                               config: dict | None = None):
    """Convert using ImageMagick."""
    convert_cmd = _convert_from_config(config) or _find_convert()
    if not convert_cmd:
        raise FileNotFoundError(
            "Neither ghostscript nor ImageMagick found. "
            "Install one of them for screenshot support."
        )

    proc = await asyncio.create_subprocess_exec(
        convert_cmd,
        "-density", str(resolution),
        ps_path,
        png_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ImageMagick failed: {stderr.decode()}")


def _find_gs() -> str | None:
    """Find ghostscript via PATH fallback."""
    for name in ("gs", "gswin64c", "gswin32c"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _find_convert() -> str | None:
    """Find ImageMagick via PATH fallback."""
    return shutil.which("convert") or shutil.which("magick")
