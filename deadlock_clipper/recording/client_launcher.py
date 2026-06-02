"""Stage 4 — Automation & Capture: launch the Deadlock client and drive the replay.

Runtime requirement: Windows Python. PyAutoGUI cannot control Windows applications
from WSL2. Run this module (and the full pipeline) with Windows-native Python.
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEADLOCK_APP_ID = "1422450"
# Default Steam executable path; overridable via config.yaml
_DEFAULT_STEAM_EXE = r"C:\Program Files (x86)\Steam\steam.exe"

# pyautogui is always bound as a module-level name so tests can patch it.
# On WSL2 / headless Linux the import fails and it stays None; _GUI_AVAILABLE
# gates every call so None is never actually invoked at runtime.
pyautogui = None
try:
    import pyautogui

    pyautogui.FAILSAFE = True  # move mouse to top-left corner to abort
    _GUI_AVAILABLE = True
except Exception:
    _GUI_AVAILABLE = False


def _require_gui() -> None:
    if not _GUI_AVAILABLE:
        raise RuntimeError(
            "pyautogui is unavailable. client_launcher must run on Windows Python."
        )


# ── Launch ───────────────────────────────────────────────────────────────────


def launch_demo(
    dem_path: str | Path,
    steam_exe: str = _DEFAULT_STEAM_EXE,
) -> None:
    """Launch Deadlock with the given demo file via Steam.

    The .dem file must reside in Deadlock's replays directory. Only the stem
    (filename without extension) is passed to +playdemo; the game resolves the
    full path internally.

    Args:
        dem_path: Path to the .dem file (used to extract the stem).
        steam_exe: Path to steam.exe. Tried first; falls back to the Steam URI.
    """
    stem = Path(dem_path).stem
    cmd = [steam_exe, "-applaunch", DEADLOCK_APP_ID, "-console", f"+playdemo {stem}"]

    try:
        subprocess.Popen(cmd)
        logger.info("Launched Deadlock via Steam.exe: +playdemo %s", stem)
    except FileNotFoundError:
        # Steam not at the expected path — fall back to URI protocol
        uri = f"steam://run/{DEADLOCK_APP_ID}//-console +playdemo {stem}"
        logger.warning("steam.exe not found at %s, falling back to URI.", steam_exe)
        if sys.platform == "win32":
            os.startfile(uri)
        else:
            subprocess.Popen(["xdg-open", uri])
        logger.info("Launched via Steam URI: %s", uri)


def wait_for_launch(wait_seconds: float = 30.0) -> None:
    """Block until the game is expected to be loaded and showing the replay."""
    logger.info("Waiting %.0fs for Deadlock to load...", wait_seconds)
    time.sleep(wait_seconds)


def dismiss_enter_screen(settle_seconds: float = 5.0) -> None:
    """Press Enter to get past the Deadlock splash screen, then wait for the replay to load.

    Args:
        settle_seconds: How long to wait after pressing Enter for the replay to finish loading.
    """
    _require_gui()
    logger.info("Dismissing enter screen...")
    pyautogui.press("enter")
    logger.info("Waiting %.0fs for replay to load...", settle_seconds)
    time.sleep(settle_seconds)


# ── Console automation ───────────────────────────────────────────────────────


def send_console_command(
    command: str,
    open_delay: float = 0.3,
    type_interval: float = 0.03,
    post_delay: float = 0.6,
) -> None:
    """Open the Deadlock developer console, type a command, and close it.

    Key sequence: F7 (open) → type command → Enter → F7 (close).

    Args:
        command: Console command string to send (e.g. 'citadel_hide_replay_hud true').
        open_delay: Seconds to wait after pressing F7 before typing.
        type_interval: Seconds between each keypress while typing.
        post_delay: Seconds to wait after Enter before closing the console.
    """
    _require_gui()
    logger.debug("Sending console command: %s", command)
    pyautogui.press("f7")
    time.sleep(open_delay)
    pyautogui.typewrite(command, interval=type_interval)
    pyautogui.press("enter")
    time.sleep(post_delay)
    pyautogui.press("f7")


def hide_hud() -> None:
    """Send the console command to hide the replay timeline and HUD controls."""
    send_console_command("citadel_hide_replay_hud true")
    logger.info("HUD hidden.")


def goto_tick(tick: int, seek_settle_seconds: float = 2.0) -> None:
    """Jump the replay to a specific tick and wait for it to settle.

    Args:
        tick: The demo server tick to seek to.
        seek_settle_seconds: Extra wait after the seek command before recording.
    """
    send_console_command(f"demo_goto {tick}")
    logger.info(
        "Sought to tick %d, waiting %.1fs to settle...", tick, seek_settle_seconds
    )
    time.sleep(seek_settle_seconds)


def prepare_replay(start_tick: int, seek_settle_seconds: float = 2.0) -> None:
    """Hide the HUD and seek to the clip's start tick.

    Call this after wait_for_launch() and before starting OBS recording.
    """
    hide_hud()
    goto_tick(start_tick, seek_settle_seconds)
