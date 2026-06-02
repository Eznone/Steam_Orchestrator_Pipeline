from collections.abc import Callable

from deadlock_clipper.recording.client_launcher import dismiss_enter_screen, launch_demo, prepare_replay, wait_for_launch


def launch_and_prepare(
    dem_path: str,
    start_tick: int,
    steam_exe: str,
    launch_wait: float,
    seek_settle: float,
    on_status: Callable[[str, str], None],
    enter_screen_settle: float = 5.0,
) -> None:
    """Launch Deadlock with the given replay and seek to start_tick.

    on_status(status, message) is called at each stage so the caller can
    update job state without this function knowing about the job system.
    """
    on_status("preparing", "Launching game...")
    launch_demo(dem_path, steam_exe)
    wait_for_launch(launch_wait)
    on_status("preparing", "Dismissing enter screen...")
    dismiss_enter_screen(enter_screen_settle)
    on_status("preparing", "Seeking to tick...")
    prepare_replay(start_tick, seek_settle)
