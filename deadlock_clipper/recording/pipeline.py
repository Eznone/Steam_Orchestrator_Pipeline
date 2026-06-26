from collections.abc import Callable
from pathlib import Path

from deadlock_clipper.recording.client_launcher import (
    disconnect_demo,
    dismiss_enter_screen,
    launch_demo,
    load_demo_via_console,
    prepare_replay,
    wait_for_launch,
)


def prepare_only(
    dem_path: str,
    start_tick: int,
    seek_settle: float,
    on_status: Callable[[str, str], None],
    player_name: str = "",
    hud_visible: bool = True,
) -> None:
    """Seek an already-running game to start_tick without relaunching.

    Call this for every clip after the first when the game is already open
    with the correct demo loaded.
    """
    on_status("preparing", "Seeking to tick...")
    prepare_replay(start_tick, seek_settle, player_name=player_name, hud_visible=hud_visible)


def switch_and_prepare(
    dem_path: str,
    start_tick: int,
    seek_settle: float,
    on_status: Callable[[str, str], None],
    demo_load_wait: float = 8.0,
    disconnect_settle: float = 10.0,
    player_name: str = "",
    hud_visible: bool = True,
) -> None:
    """Disconnect from the current demo, load the next one via console, and seek.

    Call this when the game is already running but a different demo is needed.
    Avoids a full Steam relaunch between demos in a multi-demo queue.
    """
    on_status("preparing", "Disconnecting from current demo...")
    disconnect_demo(disconnect_settle)
    on_status("preparing", "Loading next demo via console...")
    load_demo_via_console(Path(dem_path).name, demo_load_wait)
    on_status("preparing", "Seeking to tick...")
    prepare_replay(start_tick, seek_settle, player_name=player_name, hud_visible=hud_visible)


def launch_and_prepare(
    dem_path: str,
    start_tick: int,
    steam_exe: str,
    launch_wait: float,
    seek_settle: float,
    on_status: Callable[[str, str], None],
    enter_screen_settle: float = 5.0,
    replays_dir: str | None = None,
    demo_load_wait: float = 8.0,
    player_name: str = "",
    hud_visible: bool = True,
) -> None:
    """Launch Deadlock with the given replay and seek to start_tick.

    on_status(status, message) is called at each stage so the caller can
    update job state without this function knowing about the job system.
    """
    on_status("preparing", "Launching game...")
    launch_demo(dem_path, steam_exe, replays_dir=replays_dir)
    wait_for_launch(launch_wait)
    on_status("preparing", "Dismissing enter screen...")
    dismiss_enter_screen(enter_screen_settle)
    on_status("preparing", "Loading demo via console...")
    load_demo_via_console(Path(dem_path).name, demo_load_wait)
    on_status("preparing", "Seeking to tick...")
    prepare_replay(start_tick, seek_settle, player_name=player_name, hud_visible=hud_visible)
