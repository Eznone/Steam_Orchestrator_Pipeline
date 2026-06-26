import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deadlock_clipper.output.organizer import organize_clip
from deadlock_clipper.ports.capture import CapturePort
from deadlock_clipper.recording.client_launcher import teardown_game
from deadlock_clipper.recording.pipeline import launch_and_prepare, prepare_only, switch_and_prepare

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class RecordOptions:
    steam_exe: str
    launch_wait: float
    enter_screen_settle: float
    seek_settle: float
    replays_dir: str | None
    clips_dir: Path
    hud_visible: bool

    @classmethod
    def from_config(cls, config: dict, overrides: dict | None = None) -> "RecordOptions":
        rec = config.get("recording", {})
        watcher = config.get("watcher", {})
        ov = overrides or {}
        return cls(
            steam_exe=ov.get("steam_exe", rec.get("steam_exe", "")),
            launch_wait=float(ov.get("launch_wait", rec.get("launch_wait_seconds", 30))),
            enter_screen_settle=float(ov.get("enter_screen_settle", rec.get("enter_screen_settle_seconds", 5))),
            seek_settle=float(ov.get("seek_settle", rec.get("seek_settle_seconds", 2))),
            replays_dir=ov.get("replays_dir") or watcher.get("hotfolder") or None,
            clips_dir=Path(config.get("clips", {}).get("output_dir", "./data/clips")),
            hud_visible=bool(ov.get("hud_visible", rec.get("hud_visible", True))),
        )


class GameSessionService:
    """Orchestrates game client + capture for a replay recording session.

    Owns active_dem and game_running state. Chooses the correct
    preparation strategy (launch / switch / seek-only) based on game state.
    No Flask dependency — safe to use from a headless daemon.
    """

    def __init__(self, capture: CapturePort, recording_lock: threading.Lock) -> None:
        self._capture = capture
        self._lock = recording_lock
        self.active_dem: str | None = None
        self.game_running: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def record_clip(
        self,
        clip: dict,
        dem_path: str,
        tick_rate: int,
        opts: RecordOptions,
        on_status: StatusCallback,
    ) -> str | None:
        """Full pipeline: prepare game → record → return output_path."""
        start_tick = int(clip.get("start_tick", 0))
        end_tick = int(clip.get("end_tick", 0))
        player_name = clip.get("player_name", "")
        duration_s = max((end_tick - start_tick) / tick_rate, 1)

        with self._lock:
            game_prepared = False
            try:
                if self._capture is None:
                    raise RuntimeError("Capture backend not connected — click 'Connect' before recording")
                self._prepare_game(dem_path, start_tick, opts, on_status, player_name=player_name)
                game_prepared = True

                target_dir = (opts.clips_dir / "deadlock" / Path(dem_path).stem).resolve()
                target_dir.mkdir(parents=True, exist_ok=True)

                on_status("recording", "Recording...")
                try:
                    self._capture.set_record_directory(str(target_dir))
                except Exception as exc:
                    logger.warning(
                        "Could not set OBS record directory to '%s': %s "
                        "— recording will go to OBS default output folder.",
                        target_dir, exc,
                    )
                self._capture.start_recording()
                time.sleep(duration_s)
                output_path = self._capture.stop_recording()

                if output_path:
                    output_path = str(organize_clip(
                        output_path, player_name, dem_path, start_tick, tick_rate, opts.clips_dir,
                    ))
                return output_path
            except Exception:
                if not game_prepared:
                    self.active_dem = None
                    self.game_running = False
                raise

    def prepare(
        self,
        dem_path: str,
        start_tick: int,
        end_tick: int,
        tick_rate: int,
        opts: RecordOptions,
        on_status: StatusCallback,
        player_name: str = "",
    ) -> None:
        """Seek game to start_tick without recording."""
        duration_s = max((end_tick - start_tick) / tick_rate, 0) if end_tick > start_tick else 0
        with self._lock:
            try:
                self._prepare_game(dem_path, start_tick, opts, on_status, player_name=player_name)
                if duration_s > 0:
                    on_status("preparing", "Playing clip...")
                    time.sleep(duration_s)
                on_status("done", "Ready at tick")
            except Exception:
                self.active_dem = None
                raise

    def teardown(self, on_status: StatusCallback) -> None:
        """Exit the game client cleanly after all clips are done."""
        with self._lock:
            teardown_game()
            self.active_dem = None
            self.game_running = False
            on_status("done", "Game exited")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _choose_strategy(self, dem_path: str) -> str:
        if self.active_dem == dem_path:
            return "seek_only"
        if self.game_running:
            return "switch"
        return "launch"

    def _prepare_game(
        self,
        dem_path: str,
        start_tick: int,
        opts: RecordOptions,
        on_status: StatusCallback,
        player_name: str = "",
    ) -> None:
        strategy = self._choose_strategy(dem_path)
        if strategy == "seek_only":
            prepare_only(
                dem_path, start_tick, opts.seek_settle,
                on_status=on_status, player_name=player_name, hud_visible=opts.hud_visible,
            )
        elif strategy == "switch":
            switch_and_prepare(
                dem_path, start_tick, opts.seek_settle,
                on_status=on_status, player_name=player_name, hud_visible=opts.hud_visible,
            )
            self.active_dem = dem_path
        else:
            self.active_dem = None
            launch_and_prepare(
                dem_path, start_tick, opts.steam_exe, opts.launch_wait, opts.seek_settle,
                on_status=on_status,
                enter_screen_settle=opts.enter_screen_settle,
                replays_dir=opts.replays_dir,
                player_name=player_name,
                hud_visible=opts.hud_visible,
            )
            self.active_dem = dem_path
            self.game_running = True
