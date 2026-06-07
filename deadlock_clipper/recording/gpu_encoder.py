"""Detects whether a hardware (GPU) H.264 encoder actually works on this machine.

FFmpeg builds typically ship with `h264_nvenc`/`h264_qsv`/`h264_amf` compiled in
regardless of whether the matching GPU/driver is present — so just listing
supported encoders (`ffmpeg -encoders`) tells you nothing about viability. The
only reliable check is to actually run a tiny throwaway encode through each
candidate and see whether FFmpeg can initialise the hardware.
"""

import logging
import subprocess
import sys
from typing import Literal

logger = logging.getLogger(__name__)

# Ordered by how common each vendor is in gaming PCs.
_CANDIDATES = ("h264_nvenc", "h264_qsv", "h264_amf")

_PROBE_TIMEOUT_SECONDS = 5

_UNPROBED: Literal["unprobed"] = "unprobed"
_detected: "str | None | Literal['unprobed']" = _UNPROBED


def detect_gpu_encoder(ffmpeg_path: str) -> str | None:
    """Return the name of the first hardware encoder that actually works, or None.

    Probed once per process and cached — the result can't change while the
    process is running, and each probe spawns an FFmpeg subprocess that can
    take a couple of seconds on degraded driver setups.
    """
    global _detected
    if _detected == _UNPROBED:
        _detected = _probe(ffmpeg_path)
    return _detected


def _probe(ffmpeg_path: str) -> str | None:
    for codec in _CANDIDATES:
        if _can_encode(ffmpeg_path, codec):
            logger.info("Detected working GPU encoder: %s", codec)
            return codec
    logger.info("No working GPU encoder found among %s.", _CANDIDATES)
    return None


def _can_encode(ffmpeg_path: str, codec: str) -> bool:
    cmd = [
        ffmpeg_path, "-hide_banner", "-loglevel", "error",
        # 256x256 — comfortably above NVENC's minimum encode dimension (it
        # rejects anything below ~160x160 with "Frame Dimension less than the
        # minimum supported value", which would otherwise look like "no GPU").
        "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.5:r=5",
        "-frames:v", "2", "-c:v", codec, "-f", "null", "-",
    ]
    # On Windows, a missing hardware DLL can pop up a blocking native dialog
    # before FFmpeg ever writes to stderr — suppress any console window.
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=_PROBE_TIMEOUT_SECONDS, creationflags=creationflags,
        )
        return result.returncode == 0
    except Exception:
        logger.debug("GPU encoder probe failed for %s.", codec, exc_info=True)
        return False
