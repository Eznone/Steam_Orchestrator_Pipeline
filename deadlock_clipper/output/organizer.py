import re
from pathlib import Path


def _safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '', name).strip() or "clip"


def _ticks_to_time_str(ticks: int, tick_rate: int) -> str:
    total_sec = int(ticks) // int(tick_rate)
    return f"{total_sec // 60}-{total_sec % 60:02d}"


def organize_clip(
    raw_path: str,
    player_name: str,
    dem_path: str,
    start_tick: int,
    tick_rate: int,
    clips_dir: Path,
) -> Path:
    """Rename and move a raw OBS output file to the canonical clip path.

    Returns the final Path where the file was placed.
    """
    p = Path(raw_path)
    safe_player = _safe_filename(player_name)
    time_str = _ticks_to_time_str(start_tick, tick_rate)
    match_code = Path(dem_path).stem
    target_dir = (clips_dir / "deadlock" / match_code).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    new_path = target_dir / f"{safe_player}_{time_str}.mp4"
    p.rename(new_path)
    return new_path
