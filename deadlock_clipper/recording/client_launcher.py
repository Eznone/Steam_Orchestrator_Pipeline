"""Stage 4 — Automation & Capture: launch the Deadlock client and drive the replay.

Runtime requirement: Windows Python. PyAutoGUI cannot control Windows applications
from WSL2. Run this module (and the full pipeline) with Windows-native Python.
"""

import logging
import os
import shutil
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
    replays_dir: str | Path | None = None,
) -> None:
    """Launch Deadlock with the given demo file via Steam.

    +playdemo only searches Deadlock's replays directory. If replays_dir is
    provided and the .dem is not already there, it is copied in before launch.

    Args:
        dem_path: Path to the .dem file.
        steam_exe: Path to steam.exe. Tried first; falls back to the Steam URI.
        replays_dir: Deadlock's replays directory (watcher.hotfolder). When
            provided, the .dem is copied here if it isn't already present.
    """
    dem_path = Path(dem_path)

    if replays_dir is not None:
        replays_dir = Path(replays_dir)
        target = replays_dir / dem_path.name
        if not target.exists():
            logger.info("Copying %s → %s for +playdemo", dem_path.name, replays_dir)
            shutil.copy2(dem_path, target)

    demo_arg = f"replays/{dem_path.name}"
    cmd = [steam_exe, "-applaunch", DEADLOCK_APP_ID, "-console", "+playdemo", demo_arg]

    try:
        logger.info("Running: %s", " ".join(cmd))
        subprocess.Popen(cmd)
        logger.info("Launched Deadlock via Steam.exe: +playdemo %s", demo_arg)
    except FileNotFoundError:
        # steam://rungameid/<id>//<args> — double-slash signals game launch args
        uri = f"steam://rungameid/{DEADLOCK_APP_ID}//+playdemo {demo_arg}"
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


def load_demo_via_console(filename: str, load_wait: float = 8.0) -> None:
    """Open the console and type 'playdemo replays/<filename>' to load the replay.

    More reliable than the +playdemo Steam launch argument, which Deadlock
    silently ignores if it launches to the main menu.

    Args:
        filename: Demo filename with extension (e.g. '83083467.dem').
        load_wait: Seconds to wait after the command for the demo to load.
    """
    demo_arg = f"replays/{filename}"
    send_console_command(f"playdemo {demo_arg}")
    logger.info("Sent 'playdemo %s' via console, waiting %.0fs to load...", demo_arg, load_wait)
    time.sleep(load_wait)


def hide_hud() -> None:
    """Send the console command to hide the replay timeline and HUD controls."""
    send_console_command("citadel_hide_replay_hud true")
    logger.info("HUD hidden.")


def goto_tick(
    tick: int,
    seek_settle_seconds: float = 2.0,
) -> None:
    """Jump the replay to a specific tick and wait for it to settle.

    Args:
        tick: The demo server tick to seek to.
        seek_settle_seconds: Extra wait after the command for the seek to settle.
    """
    send_console_command(f"demo_gototick {tick}")
    logger.info("demo_gototick %d sent, waiting %.1fs to settle...", tick, seek_settle_seconds)
    time.sleep(seek_settle_seconds)


def prepare_replay(start_tick: int, seek_settle_seconds: float = 2.0) -> None:
    """Hide the HUD and seek to the clip's start tick.

    Call this after wait_for_launch() and before starting OBS recording.
    """
    hide_hud()
    goto_tick(start_tick, seek_settle_seconds)
