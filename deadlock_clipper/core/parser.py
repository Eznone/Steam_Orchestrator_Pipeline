import json
import logging
from pathlib import Path

from boon import Demo, DemoHeaderError, InvalidDemoError, hero_names

from deadlock_clipper.core.models import Kill, ObjectiveEvent, Player

logger = logging.getLogger(__name__)


def parse_demo(dem_path: str | Path, output_path: str | Path | None = None) -> dict:
    """Parse a Deadlock .dem file and return structured event data.

    Args:
        dem_path: Path to the .dem replay file.
        output_path: If provided, write the result JSON to this path.

    Returns:
        Dict with match metadata, player list, and kill events.

    Raises:
        FileNotFoundError: If the .dem file does not exist.
        RuntimeError: If Boon fails to parse the file (corrupt or unsupported).
    """
    dem_path = Path(dem_path)

    if not dem_path.exists():
        raise FileNotFoundError(f"Demo file not found: {dem_path}")

    logger.info("Parsing %s", dem_path.name)

    try:
        demo = Demo(str(dem_path))
        demo.load("kills", "objectives")
    except (InvalidDemoError, DemoHeaderError) as exc:
        raise RuntimeError(f"Failed to parse {dem_path.name}: {exc}") from exc

    heroes = hero_names()
    kills_df = demo.kills
    players_df = demo.players
    objectives_df = demo.objectives

    kills = [Kill.from_row(row, heroes).to_dict() for row in kills_df.rows(named=True)]

    players = [Player.from_row(row, heroes).to_dict() for row in players_df.rows(named=True)]

    # Objective destructions: one row per destroyed structure (deduplicated by entity_id)
    dest_rows = objectives_df.filter(objectives_df["health"] == 0).sort("tick")
    seen_entities: set[int] = set()
    objectives_destroyed: list[dict] = []
    for row in dest_rows.rows(named=True):
        if row["entity_id"] not in seen_entities:
            seen_entities.add(row["entity_id"])
            objectives_destroyed.append(ObjectiveEvent.from_row(row).to_dict())

    payload = {
        "match_id": demo.match_id,
        "map_name": demo.map_name,
        "total_ticks": demo.total_ticks,
        "tick_rate": demo.tick_rate,
        "total_clock_time": demo.total_clock_time,
        "winning_team_num": demo.winning_team_num,
        "players": players,
        "kills": kills,
        "objectives_destroyed": objectives_destroyed,
    }

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(payload, f, indent=2)
        logger.info("Saved parsed data -> %s", output_path)

    logger.info(
        "Match %s | %s | %d kills",
        payload["match_id"],
        payload["total_clock_time"],
        len(kills),
    )
    return payload


